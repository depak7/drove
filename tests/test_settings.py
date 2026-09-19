"""Per-stage harness and model selection, end to end from config to argv."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from drove import db, workspace
from drove.api import jobs, server
from drove.harness import registry
from drove.harness.base import InvokeSpec

from .conftest import make_repo


@pytest.fixture
def client(state, monkeypatch):
    monkeypatch.setattr(jobs, "start_plan", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "start_cycle", lambda *a, **k: None)
    with TestClient(server.build_app()) as c:
        c.projects = state / "projects"
        yield c


def a_workspace(client):
    path = str(make_repo(client.projects, "api"))
    return client.post("/api/workspaces", json={"name": "product", "repos": [path]}).json()


# --- config round trip ------------------------------------------------------------------

def test_stage_config_is_saved_and_returned(client):
    ws = a_workspace(client)
    body = client.put(
        f"/api/workspaces/{ws['id']}/settings",
        json={"harness": {"review": "opencode"}, "models": {"plan": "opus"}},
    ).json()

    assert body["harness"]["review"] == "opencode"
    assert body["models"]["plan"] == "opus"
    # Unspecified stages keep their previous value rather than resetting to the default.
    assert body["harness"]["execute"] == "claude"


def test_clearing_a_model_means_let_the_cli_decide(client):
    """An empty string from a <select> is a choice: 'no pin', not 'never configured'."""
    ws = a_workspace(client)
    client.put(f"/api/workspaces/{ws['id']}/settings", json={"models": {"plan": "opus"}})
    body = client.put(f"/api/workspaces/{ws['id']}/settings", json={"models": {"plan": ""}}).json()

    assert body["models"]["plan"] is None


def test_a_harness_with_no_adapter_is_rejected(client):
    ws = a_workspace(client)
    response = client.put(
        f"/api/workspaces/{ws['id']}/settings", json={"harness": {"review": "gemini"}}
    )
    assert response.status_code == 400
    assert "gemini" in response.json()["detail"]


def test_review_sharing_the_execute_harness_is_flagged_not_blocked(client):
    """Someone with one CLI installed must still be able to run; they just get told."""
    ws = a_workspace(client)
    body = client.put(
        f"/api/workspaces/{ws['id']}/settings", json={"harness": {"review": "claude"}}
    ).json()

    assert body["harness"]["review"] == "claude"
    assert body["independent_review"] is False


# --- the part that actually matters: does the choice reach the CLI? ---------------------

def test_the_chosen_model_reaches_the_command_line(state):
    """Config that never becomes an argument is config that does nothing."""
    with db.connect() as conn:
        ws = workspace.create(conn, "product", [make_repo(state / "projects", "api")])
        db.set_workspace_config(
            conn, ws.id, dict(ws.harness), {"plan": "opus", "review": "gpt-5.6-terra"}
        )
        ws = workspace.get(conn, ws.id)

    assert ws.model_for("plan") == "opus"
    assert ws.model_for("execute") is None  # unset means the CLI decides

    claude = registry.get("claude")
    argv = claude.build_argv(InvokeSpec(prompt="x", cwd=Path("/repo"), model=ws.model_for("plan")))
    assert argv[argv.index("--model") + 1] == "opus"

    codex = registry.get("codex")
    argv = codex.build_argv(InvokeSpec(prompt="x", cwd=Path("/repo"), model=ws.model_for("review")))
    assert argv[argv.index("-m") + 1] == "gpt-5.6-terra"


def test_no_model_flag_is_sent_when_none_is_chosen(state):
    """Pinning a model by default is how a workspace breaks when a provider retires an id."""
    with db.connect() as conn:
        ws = workspace.create(conn, "product", [make_repo(state / "projects", "api")])

    for name, flag in (("claude", "--model"), ("codex", "-m"), ("opencode", "-m")):
        argv = registry.get(name).build_argv(
            InvokeSpec(prompt="x", cwd=Path("/repo"), model=ws.model_for("execute"))
        )
        assert flag not in argv, f"{name} should not pin a model by default"


# --- what the picker can honestly offer -------------------------------------------------

# Both CLIs cache the catalogue they were served. These tests supply that cache rather than
# reading the developer's own, because a machine with no coding CLIs installed — CI, or a
# contributor's first clone — would otherwise fail on facts about somebody else's laptop.
CLAUDE_CACHE = {
    "catalog": {"config": {"models": [
        {"id": "claude-fixture-opus", "short_name": "Fixture Opus", "description": "for tests"},
        {"id": "claude-fixture-haiku", "name": "Fixture Haiku"},
        {"id": "", "short_name": "no id at all"},
    ]}}
}
CODEX_CACHE = {"models": [
    {"slug": "gpt-fixture", "display_name": "Fixture GPT", "description": "for tests"},
    {"slug": "codex-auto-review", "visibility": "hide"},
    {"slug": "gpt-reserve", "visibility": "hide"},
]}


@pytest.fixture
def catalogues(tmp_path, monkeypatch):
    """A home directory holding exactly the caches these CLIs write."""
    home = tmp_path / "home"
    folder = home / ".claude" / "cache" / "model-catalog"
    folder.mkdir(parents=True)
    (folder / "catalog.json").write_text(json.dumps(CLAUDE_CACHE))
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "models_cache.json").write_text(json.dumps(CODEX_CACHE))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_model_lists_come_from_the_cli_catalogue_not_a_hardcoded_table(catalogues):
    """The proof is that ids only present in the cache come back out of it."""
    claude = {m["id"] for m in registry.list_models("claude")}
    codex = {m["id"] for m in registry.list_models("codex")}

    assert "claude-fixture-opus" in claude
    assert "gpt-fixture" in codex
    for name in ("claude", "codex"):
        for m in registry.list_models(name):
            assert m["id"] and m["label"]
            assert isinstance(m["note"], str)

    assert registry.list_models("nope") == []


def test_a_cli_that_never_cached_a_catalogue_still_offers_something(tmp_path, monkeypatch):
    """A fresh install has no cache, and an empty model picker looks like a broken screen."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty"))

    assert [m["id"] for m in registry.list_models("claude")] == ["opus", "sonnet", "haiku"]


def test_enumerating_models_never_launches_anything(catalogues, monkeypatch):
    """`lms ls` starts LM Studio, so opening the settings screen booted an application.

    Those models were unusable anyway: driving one through codex needs --oss --local-provider,
    which the adapter does not pass, so choosing one failed at spawn.
    """
    def explode(*args, **kwargs):
        raise AssertionError(f"model discovery must not spawn a process: {args}")

    monkeypatch.setattr(registry.subprocess, "run", explode)
    assert registry.list_models("claude")
    assert registry.list_models("codex")
    assert not hasattr(registry, "local_models"), "the lms probe should be gone entirely"


def test_codex_hides_its_internal_models(catalogues):
    """The cache marks an auto-review model and a reserve pool as `hide`; the CLI omits them."""
    ids = {m["id"] for m in registry.list_models("codex")}

    assert "gpt-fixture" in ids, "the visible one must survive, or this proves nothing"
    assert "codex-auto-review" not in ids
    assert "gpt-reserve" not in ids


def test_harness_endpoint_reports_what_each_one_can_do(client):
    rows = {h["name"]: h for h in client.get("/api/harnesses").json()}

    assert set(rows) == set(registry.PRESETS)
    assert rows["opencode"]["supports_schema"] is False, "schema rides in the prompt there"
    assert rows["claude"]["supports_schema"] is True


# --- discovery must not depend on a shell PATH -------------------------------------------

def test_clis_are_found_without_a_shell_path(tmp_path, monkeypatch):
    """A GUI-launched daemon gets /usr/local/bin:/bin:/usr/bin and none of these are on it.

    Relying on PATH alone made the app report every harness as missing and hand the settings
    screen an empty model list, while the binaries sat in ~/.local/bin.

    The directories are planted rather than read from this machine: the bug is that PATH is not
    consulted, and proving that needs a binary somewhere off PATH — not a laptop that happens to
    have one.
    """
    bindir = tmp_path / "somewhere" / "bin"
    bindir.mkdir(parents=True)
    installed = bindir / "claude"
    installed.write_text("#!/bin/sh\nexit 0\n")
    installed.chmod(0o755)

    monkeypatch.setattr(registry.shutil, "which", lambda _: None)  # nothing on PATH at all
    monkeypatch.setattr(registry, "EXTRA_BIN_DIRS", (bindir,))

    found = {name: registry.which(name) for name in registry.PRESETS}

    assert found["claude"] == str(installed)
    assert Path(found["claude"]).is_absolute()
    assert found["codex"] is None, "only what is really there may be reported as found"


def test_a_directory_entry_that_is_not_executable_is_not_a_cli(tmp_path, monkeypatch):
    """A same-named data file or directory must not be reported as an installed harness."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text("notes, not a binary")   # present, not executable
    (bindir / "codex").mkdir()                              # a directory wearing the name

    monkeypatch.setattr(registry.shutil, "which", lambda _: None)
    monkeypatch.setattr(registry, "EXTRA_BIN_DIRS", (bindir,))

    assert registry.which("claude") is None
    assert registry.which("codex") is None


def test_get_returns_a_harness_bound_to_an_absolute_binary():
    """Spawning by bare name fails with ENOENT deep inside a run rather than at discovery."""
    harness = registry.get("claude")
    if registry.which("claude"):
        assert Path(harness.binary).is_absolute()
