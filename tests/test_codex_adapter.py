"""Replay a recorded `codex exec --json` run through the adapter."""

from __future__ import annotations

import json
from pathlib import Path

from drove.events import AssistantText, FileChanged, SessionStarted, ToolCall, ToolResult, Usage
from drove.harness.base import InvokeSpec
from drove.harness.codex import CodexHarness

FIXTURE = Path(__file__).parent / "fixtures" / "codex_edit.jsonl"


def replay() -> list:
    h = CodexHarness()
    out = []
    for line in FIXTURE.read_text().splitlines():
        out.extend(h.parse(json.loads(line)))
    return out


def spec(**kw) -> InvokeSpec:
    return InvokeSpec(**{"prompt": "do it", "cwd": Path("/repo"), **kw})


def test_thread_started_becomes_session():
    first = replay()[0]
    assert isinstance(first, SessionStarted)
    assert first.session_id


def test_command_execution_yields_call_then_result():
    events = replay()
    calls = [e for e in events if isinstance(e, ToolCall) and e.name == "bash"]
    results = [e for e in events if isinstance(e, ToolResult)]
    assert calls and results
    assert calls[0].input["command"]
    # started -> ToolCall, completed -> ToolResult, so the ids must pair up.
    assert {c.tool_id for c in calls} & {r.tool_id for r in results}


def test_file_change_is_surfaced_with_normalised_kind():
    changes = [e for e in replay() if isinstance(e, FileChanged)]
    assert changes
    assert changes[0].path.endswith("calc.py")
    assert changes[0].change in ("add", "modify", "delete")


def test_usage_is_a_delta_not_a_running_total():
    usages = [e for e in replay() if isinstance(e, Usage)]
    assert usages
    assert usages[0].input_tokens > 0
    assert usages[0].cache_read_tokens > 0


def test_agent_messages_are_captured():
    assert any(isinstance(e, AssistantText) and e.text for e in replay())


# --- argv construction: the resume/exec option-set split -----------------------------------

def test_exec_passes_sandbox_and_cwd():
    argv = CodexHarness().build_argv(spec(mode="write"))
    assert argv[:3] == ["codex", "exec", "--json"]
    assert "-s" in argv and "workspace-write" in argv
    assert "-C" in argv


def test_readonly_uses_read_only_sandbox():
    assert "read-only" in CodexHarness().build_argv(spec(mode="readonly"))


def test_resume_omits_flags_exec_only_accepts():
    """`codex exec resume` rejects -s/-C/--add-dir: 'error: unexpected argument -s found'."""
    argv = CodexHarness().build_argv(
        spec(session_id="01a0", resume=True, mode="write", extra_dirs=[Path("/x")])
    )
    assert argv[:4] == ["codex", "exec", "resume", "--json"]
    for forbidden in ("-s", "-C", "--add-dir"):
        assert forbidden not in argv


def test_resume_puts_flags_before_positionals():
    argv = CodexHarness().build_argv(spec(session_id="01a0", resume=True), schema_file=Path("/s"))
    assert argv.index("--output-schema") < argv.index("01a0") < argv.index("do it")
    assert argv[-1] == "do it"


def test_schema_is_passed_as_a_file_path():
    """Opposite of claude, which wants the schema inline."""
    argv = CodexHarness().build_argv(spec(), schema_file=Path("/tmp/s.json"))
    assert argv[argv.index("--output-schema") + 1] == "/tmp/s.json"


def test_skip_git_repo_check_only_outside_a_repo(tmp_path):
    """Codex aborts in a non-repo before emitting any event, which is a baffling failure mode."""
    assert "--skip-git-repo-check" in CodexHarness().build_argv(spec(cwd=tmp_path))

    # A worktree's .git is a FILE, not a directory — presence is what matters.
    (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/task-1\n")
    assert "--skip-git-repo-check" not in CodexHarness().build_argv(spec(cwd=tmp_path))
