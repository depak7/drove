"""BROWSER stage: start the app, open it, and record what a person would see.

Your tests prove the code is correct. They cannot tell you the page is blank because one component
throws on render — the build passes, the linter passes, and the app is broken. This stage is the
shallowest possible check that the thing actually runs: load some pages, capture console errors,
failed requests and a screenshot of each.

No model is involved and nothing is clicked. It is deliberately the boring half; an agent that
drives the browser against acceptance criteria can be layered on top of exactly this plumbing.

Opt-in per repo, because most projects have no web UI to open:

    [browser]
    start = "npm run dev"
    dir   = "web"              # relative to the repo, optional
    url   = "http://localhost:5173"
    paths = ["/", "/settings"]
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

READY_TIMEOUT = 90.0
PAGE_TIMEOUT_MS = 20_000
SETTLE_MS = 700


@dataclass
class PageCheck:
    path: str
    url: str
    status: int | None = None
    title: str = ""
    console_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    screenshot: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and (self.status is None or self.status < 400)
            and not self.console_errors
            and not self.failed_requests
        )


@dataclass
class BrowserOutcome:
    ran: bool = False
    skipped: str = ""
    checks: list[PageCheck] = field(default_factory=list)
    startup_log: str = ""

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def failures(self) -> list[PageCheck]:
        return [c for c in self.checks if not c.ok]


def _responds(url: str, timeout: float = 2.0) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return None


async def _wait_until_ready(url: str, proc: subprocess.Popen | None, timeout: float) -> str | None:
    """Poll until the app answers, or explain why it never did."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return f"the start command exited with code {proc.returncode} before serving anything"
        if _responds(url) is not None:
            return None
        await asyncio.sleep(0.4)
    return f"nothing answered on {url} within {timeout:.0f}s"


def _terminate(proc: subprocess.Popen) -> None:
    """Kill the dev server and everything it spawned.

    Dev servers fork children — vite spawns esbuild, next spawns workers — and killing only the
    parent leaves orphans holding the port, so the next run tests a stale server. The process gets
    its own group precisely so the whole tree can be signalled.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


async def run_browser(cwd: Path, config: dict, shots_dir: Path) -> BrowserOutcome:
    """Start the app, open each path, and record what rendered."""
    url = str(config.get("url") or "").rstrip("/")
    if not url:
        return BrowserOutcome(skipped="no [browser] url configured")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return BrowserOutcome(
            skipped="playwright is not installed — add it with: uv tool install 'drove[browser]'"
        )

    # Someone else's server on this port would be silently tested instead of the branch's code,
    # and would report a pass for work that was never run.
    if config.get("start") and _responds(url) is not None:
        return BrowserOutcome(
            skipped=f"something is already serving {url}; stop it so the branch's own app is tested"
        )

    proc: subprocess.Popen | None = None
    outcome = BrowserOutcome()

    try:
        if start := config.get("start"):
            work = cwd / str(config.get("dir", "")) if config.get("dir") else cwd
            proc = subprocess.Popen(
                start,
                shell=True,
                cwd=str(work),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # own process group, so the whole tree can be killed
                text=True,
            )

        timeout = float(config.get("timeout", READY_TIMEOUT))
        if reason := await _wait_until_ready(url, proc, timeout):
            if proc is not None and proc.stdout is not None:
                with contextlib.suppress(Exception):
                    outcome.startup_log = proc.stdout.read()[-3000:]
            return BrowserOutcome(skipped=reason, startup_log=outcome.startup_log)

        shots_dir.mkdir(parents=True, exist_ok=True)
        paths = [str(p) for p in (config.get("paths") or ["/"])]

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                try:
                    for index, path in enumerate(paths):
                        outcome.checks.append(
                            await _check_page(browser, url, path, shots_dir, index)
                        )
                finally:
                    await browser.close()
        except Exception as exc:
            # The browser itself failing to start is a skip, never a failed run. This stage is
            # advisory, and the common cause is simply that no browser is installed — the
            # playwright package can be present while its browsers are not, which is exactly the
            # state a packaged app ships in.
            first = str(exc).split("\n")[0][:200]
            return BrowserOutcome(
                skipped=f"could not start a browser: {first}. Run `playwright install chromium`.",
                startup_log=outcome.startup_log,
            )

        outcome.ran = True
        return outcome
    finally:
        if proc is not None:
            _terminate(proc)


async def _check_page(browser, base: str, path: str, shots_dir: Path, index: int) -> PageCheck:
    target = f"{base}{path if path.startswith('/') else '/' + path}"
    check = PageCheck(path=path, url=target)

    context = await browser.new_context(viewport={"width": 1440, "height": 900})
    page = await context.new_page()

    page.on(
        "console",
        lambda m: check.console_errors.append(m.text[:300]) if m.type == "error" else None,
    )
    page.on("pageerror", lambda e: check.console_errors.append(str(e)[:300]))
    page.on(
        "response",
        lambda r: check.failed_requests.append(f"{r.status} {r.url[:160]}")
        if r.status >= 400
        else None,
    )

    try:
        response = await page.goto(target, wait_until="load", timeout=PAGE_TIMEOUT_MS)
        check.status = response.status if response else None
        # Client-rendered apps paint after load; without a settle the screenshot is a blank shell
        # and every console error arrives too late to be seen.
        await page.wait_for_timeout(SETTLE_MS)
        check.title = (await page.title())[:120]

        shot = shots_dir / f"{index:02d}-{(path.strip('/') or 'root').replace('/', '-')}.png"
        await page.screenshot(path=str(shot), full_page=False)
        check.screenshot = shot.name
    except Exception as exc:  # a page that will not load is the finding, not a crash
        check.error = f"{type(exc).__name__}: {exc}".split("\n")[0][:300]
    finally:
        await context.close()

    return check
