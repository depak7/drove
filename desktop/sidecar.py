"""Entry point bundled into the macOS application.

The Electron process owns the UI; this executable owns the local Drove daemon.  Keeping the
command deliberately small means the packaged application uses exactly the same CLI/server path
as terminal users, without requiring Python or uv to be installed on the user's Mac.
"""

from __future__ import annotations

import sys

from drove.cli import main


if __name__ == "__main__":
    # Electron supplies the port.  The sidecar must never launch a separate browser window.
    sys.argv = [sys.argv[0], "serve", "--no-open", *sys.argv[1:]]
    main()
