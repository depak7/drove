"""A shell inside a feature's worktree, over a WebSocket.

The pipeline is headless by design — no PTY anywhere near a harness. This is the opposite case: a
person who wants to run the tests themselves, look at `git log`, or fix one line by hand without
leaving the app and hunting for the worktree path.

Implemented in Python rather than in the Electron process on purpose. `drove serve` in a browser
and the packaged Mac app are two clients of the same daemon, and a terminal built on a native Node
module would exist in only one of them.

It binds to loopback like the rest of the daemon, and it is a real shell with the permissions of
whoever started Drove — the same reach as the coding agents it already runs, and the same reach as
the terminal the daemon was launched from.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import signal
import struct
import termios
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

# One read is one frame to the browser. Big enough that `cat` of a source file does not arrive in
# a hundred pieces, small enough that output starts appearing immediately.
CHUNK = 64 * 1024


def _shell() -> list[str]:
    """The user's own shell, interactive so their prompt and aliases are there."""
    return [os.environ.get("SHELL", "/bin/bash"), "-i"]


def _resize(fd: int, rows: int, cols: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass  # the pty went away between the resize and now; the read loop will notice


async def serve(socket: WebSocket, cwd: Path) -> None:
    """Run a shell in `cwd` until either side hangs up."""
    await socket.accept()
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - the child never returns
        try:
            os.chdir(cwd)
            os.environ["TERM"] = "xterm-256color"
            os.execvp(_shell()[0], _shell())
        except Exception:
            os._exit(1)

    loop = asyncio.get_running_loop()
    reader = asyncio.Queue()

    def on_readable() -> None:
        try:
            data = os.read(fd, CHUNK)
        except OSError:
            data = b""
        reader.put_nowait(data)
        if not data:
            loop.remove_reader(fd)

    loop.add_reader(fd, on_readable)

    async def pump_out() -> None:
        while True:
            data = await reader.get()
            if not data:
                return
            await socket.send_bytes(data)

    async def pump_in() -> None:
        while True:
            message = await socket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            if (raw := message.get("bytes")) is not None:
                os.write(fd, raw)
            elif (text := message.get("text")) is not None:
                # Text frames carry control, not keystrokes: only the window size so far.
                try:
                    payload = json.loads(text)
                except ValueError:
                    continue
                if payload.get("resize"):
                    _resize(fd, int(payload.get("rows", 24)), int(payload.get("cols", 80)))

    out = asyncio.ensure_future(pump_out())
    inp = asyncio.ensure_future(pump_in())
    try:
        await asyncio.wait({out, inp}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in (out, inp):
            task.cancel()
        loop.remove_reader(fd)
        # The shell has its own process group; take the group so anything it started goes too,
        # rather than leaving a `npm run dev` running with nothing attached to it.
        try:
            os.killpg(os.getpgid(pid), signal.SIGHUP)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass
