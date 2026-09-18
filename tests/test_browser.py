"""Browser smoke checks against a real server.

These use python -m http.server rather than a mocked page, because the parts that break are the
ones a mock hides: starting a process, waiting for a port, and killing the whole tree afterwards.
"""

from __future__ import annotations

import socket

import pytest

from drove.pipeline.stages import browser

pytest.importorskip("playwright", reason="browser checks are an opt-in extra")


def _chromium_installed() -> bool:
    """The package alone is not enough; `playwright install chromium` fetches the browser."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:
        return False


# Skipped rather than failed: this is an opt-in extra, and someone who never asked for browser
# checks should not have a red suite because they have no browser installed.
pytestmark = pytest.mark.skipif(
    not _chromium_installed(), reason="run `playwright install chromium` to exercise these"
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text("<title>Home</title><h1>it works</h1>")
    (tmp_path / "broken.html").write_text(
        "<title>Broken</title><script>throw new Error('kaboom')</script>"
    )
    (tmp_path / "missing-asset.html").write_text(
        "<title>Missing</title><img src='/nope.png'>"
    )
    return tmp_path


def config_for(port: int, paths: list[str]) -> dict:
    return {
        "start": f"python3 -m http.server {port}",
        "url": f"http://127.0.0.1:{port}",
        "paths": paths,
        "timeout": 30,
    }


async def test_a_healthy_page_passes_and_is_screenshotted(site, tmp_path):
    port = free_port()
    shots = tmp_path / "screens"

    outcome = await browser.run_browser(site, config_for(port, ["/index.html"]), shots)

    assert outcome.ran, outcome.skipped
    assert outcome.passed
    check = outcome.checks[0]
    assert check.title == "Home"
    assert check.status == 200
    assert (shots / check.screenshot).stat().st_size > 1000, "a real image, not an empty file"


async def test_a_javascript_error_is_caught_even_though_the_page_loads(site, tmp_path):
    """This is the whole point: the build passes, the page 200s, and it is still broken."""
    port = free_port()

    outcome = await browser.run_browser(site, config_for(port, ["/broken.html"]), tmp_path / "s")

    assert outcome.ran, outcome.skipped
    assert not outcome.passed
    check = outcome.checks[0]
    assert check.status == 200, "the page served fine; the failure is only visible in the browser"
    assert any("kaboom" in e for e in check.console_errors)


async def test_a_failed_request_is_caught(site, tmp_path):
    port = free_port()

    outcome = await browser.run_browser(
        site, config_for(port, ["/missing-asset.html"]), tmp_path / "s"
    )

    assert not outcome.passed
    assert any("404" in r for r in outcome.checks[0].failed_requests)


async def test_the_server_is_stopped_afterwards(site, tmp_path):
    """A dev server left holding the port means the next run tests a stale app."""
    port = free_port()
    await browser.run_browser(site, config_for(port, ["/index.html"]), tmp_path / "s")

    assert browser._responds(f"http://127.0.0.1:{port}") is None, "the port must be free again"


async def test_a_start_command_that_dies_is_reported_not_waited_on(tmp_path):
    port = free_port()
    config = {"start": "exit 3", "url": f"http://127.0.0.1:{port}", "timeout": 20}

    outcome = await browser.run_browser(tmp_path, config, tmp_path / "s")

    assert not outcome.ran
    assert "exited with code 3" in outcome.skipped, "do not sit out the full timeout"


async def test_a_port_already_in_use_is_refused_rather_than_silently_tested(site, tmp_path):
    """Testing someone else's server would report a pass for work that never ran."""
    import subprocess
    import time

    port = free_port()
    other = subprocess.Popen(
        ["python3", "-m", "http.server", str(port)],
        cwd=site, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        for _ in range(50):
            if browser._responds(f"http://127.0.0.1:{port}") is not None:
                break
            time.sleep(0.1)

        outcome = await browser.run_browser(site, config_for(port, ["/index.html"]), tmp_path / "s")
        assert not outcome.ran
        assert "already serving" in outcome.skipped
    finally:
        import os
        import signal
        os.killpg(os.getpgid(other.pid), signal.SIGKILL)


async def test_no_configuration_means_the_stage_does_nothing(tmp_path):
    outcome = await browser.run_browser(tmp_path, {}, tmp_path / "s")
    assert not outcome.ran
    assert "no [browser] url" in outcome.skipped


async def test_a_browser_that_cannot_start_is_a_skip_not_a_failed_run(site, tmp_path, monkeypatch):
    """The stage is advisory, so nothing in it may take a run down.

    The playwright package can be installed while its browsers are not — which is precisely the
    state a packaged app ships in, since the browsers live in a user cache rather than the bundle.
    """
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty"))
    port = free_port()

    outcome = await browser.run_browser(site, config_for(port, ["/index.html"]), tmp_path / "s")

    assert not outcome.ran
    assert "could not start a browser" in outcome.skipped
    assert "playwright install" in outcome.skipped
