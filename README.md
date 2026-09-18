# Drove

Local autonomous engineering pipeline. Describe a feature, approve one plan, and come back to a
reviewed branch and an evidence pack — driven entirely by the coding CLIs you already have
installed and logged in (`claude`, `codex`, `opencode`).

A **workspace** is a named set of repositories worked on together. A feature gets a worktree in
every repo in its workspace, so one change can alter an interface in one repo and its callers in
another. One repo is simply a workspace of one.

```
PLAN  →  ⏸ you approve  →  EXECUTE  →  REVIEW  →  VERIFY  →  DELIVER
claude                     claude   ⇄  codex     repo cmds  branch + evidence
                                    ↑_____↓
                                   ≤2 fix rounds
```

The point is **cross-model review**: the harness that wrote the code never grades its own homework.

## Install

```bash
uv tool install git+https://github.com/<you>/drove
cd ~/my-repo && drove init && drove doctor
```

## Use

```bash
drove serve                                  # daemon + web UI on http://localhost:8787
                                               # create workspaces and add repos from the app

drove workspace new product ~/code/api ~/code/web
drove plan "rename greeting() and update its callers"
drove execute <feature>                      # implement → review → fix → verify → evidence
drove features                               # what exists and where it got to
drove pivot <feature> "use a token bucket"   # change direction; keeps branches and agent memory
```

The web UI and the CLI are two clients of the same engine and the same SQLite database — start a
feature in one and finish it in the other. The one thing the daemon adds is that the approval gate
becomes a state rather than a blocking prompt, so several features can sit waiting on you at once
while others run.

Each feature gets its own worktrees under `~/.drove/worktrees/<workspace>/<feature>/`, one per
repo, side by side — so runs never collide and your own checkouts are never touched. A worktree is
removed only once its commits are in that repo's base branch; unintegrated work is preserved and
the recovery command printed, per repo independently.

**Cross-repo changes do not merge atomically.** When a feature spans repos, its branches share a
name and have to be merged together — the evidence pack and the UI say so, but nothing can enforce
it.

## Browser checks (optional)

Your tests prove the code is correct. They cannot tell you the page is blank because one component
throws on render — the build passes, the linter passes, and the app is broken.

Add a `[browser]` section to a repo's `.drove.toml` and Drove starts the app after a run, opens the
pages you name, and records console errors, failed requests and a screenshot of each:

```toml
[browser]
start = "npm run dev"
dir   = "web"                      # relative to the repo, optional
url   = "http://localhost:5173"
paths = ["/", "/settings"]
```

Needs the extra: `uv tool install "drove[browser]"`, then `playwright install chromium`.

It is **advisory** — findings appear in the evidence pack and in the app, but never fail a run on
their own. It refuses to start if something is already serving that URL, rather than silently
testing someone else's server and reporting a pass for work that never ran.

## Developing

```bash
uv tool install --force --reinstall .   # --reinstall matters: --force alone reuses uv's cached wheel
uv run --extra dev pytest               # offline
uv run --extra dev pytest -m live       # spends tokens

cd web && npm install && npm run build  # rebuild the UI into drove/web/
uv tool install --force --reinstall .   # required after a UI rebuild: `drove serve` serves the
                                        # assets baked into the install, not the source tree
```

## macOS desktop release

The desktop app embeds the Drove daemon as a PyInstaller sidecar. A downloaded app therefore does
not require Python, uv, or a checkout of this repository. Build a native-architecture DMG with:

```bash
cd web
npm install
npm run desktop:package
```

The output is in `web/release/`. `desktop:package` creates `Resources/drove-sidecar/` inside the
app and Electron starts that executable on localhost; it never depends on a globally installed
`drove` command. The command deliberately builds only the host architecture: build separately on
Apple Silicon and Intel Macs for their respective targets, because each DMG must contain a
matching native Python sidecar.

Browser checks are not available inside the packaged app: the sidecar deliberately excludes
playwright, whose browsers live in a user cache rather than the bundle, so shipping it would add
129MB that could not run anything. Use `drove serve` from a checkout for those.

For a public release, run on a machine with an Apple Developer signing certificate and set
`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, and `APPLE_TEAM_ID`. The build signs with the available
Developer ID certificate, then the included hook notarizes it. Without those credentials the same
command produces an unsigned local-test DMG only.

Built UI assets are committed. The install path is `uv tool install git+…`, which builds the wheel
from the git tree, so anything not committed is not installed — and building at install time
instead would put Node on the critical path for every user.

Status: workspaces, cross-model review, the desktop shell, and optional browser checks.
Pending work is listed in `TODO.md`.
