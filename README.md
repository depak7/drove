# Drove

A local autonomous engineering pipeline. Describe a feature, approve one plan, and come back to a
reviewed branch, a pushed compare link and an evidence pack you could hand to a colleague.

It runs entirely on the coding CLIs you already have installed and logged in — `claude`, `codex`,
`opencode`. No API keys, no cloud service, no second subscription. Nothing about your code leaves
your machine except what those CLIs already send on your behalf.

```
PLAN  →  ⏸ you approve  →  EXECUTE  →  REVIEW  →  VERIFY  →  DELIVER
claude                     claude   ⇄  codex     your cmds  branch + link + evidence
                                    ↑_____↓
                                   ≤2 fix rounds
```

The point is **cross-model review**: the harness that wrote the code never grades its own
homework. A reviewer that has read the implementer's reasoning tends to ratify it, so the reviewer
runs in a fresh session every round and sees only what a human reviewer sees — the diff and the
stated intent.

**Contents** · [Requirements](#requirements) · [Install](#install) · [Quickstart](#quickstart) ·
[How it works](#how-it-works) · [The app](#the-app) · [Feature states](#feature-states) ·
[Verifying](#verifying-your-own-checks) · [Publishing](#publishing) ·
[Stopping and recovery](#stopping-and-recovery) · [Evidence](#the-evidence-pack) ·
[Configuration](#configuration) · [CLI](#cli-reference) · [Where state lives](#where-state-lives) ·
[Desktop app](#the-macos-app) · [Developing](#developing) · [Limits](#known-limits)

---

## Requirements

- **macOS or Linux**, and **git**.
- **At least one coding CLI installed and authenticated.** `drove doctor` reports what it found:

  | CLI | Roles it can fill | Structured output |
  |---|---|---|
  | `claude` | plan, execute, review | native (`--json-schema`) |
  | `codex` | plan, execute, review | native (`--output-schema`) |
  | `opencode` | plan, execute, review | asked for in the prompt |

  Cross-model review needs **two** of them. One works, but then the same model reviews its own
  work — and the evidence pack says so rather than implying an independence that was not there.
- **Python 3.12+** — `uv` fetches one if you have none.

## Install

```bash
uv tool install git+https://github.com/depak7/drove
drove doctor        # which CLIs are present, authenticated and adapter-backed
```

With optional browser checks:

```bash
uv tool install "drove[browser] @ git+https://github.com/depak7/drove"
playwright install chromium
```

## Quickstart

```bash
drove serve         # http://localhost:8787
```

That is the whole setup. **Run it from anywhere** — your home directory, a scratch folder, a
launchd job. Drove is one machine-wide daemon, not a per-project tool: it keeps its state in
`~/.drove`, and repositories are pointed at rather than run from. You never `cd` into a project
to start it, and you never run a second copy for a second repo.

Add a workspace and a repo from the app, type what you want in the middle of the screen, and
approve the plan when it appears. Everything after that runs unattended until it needs you again.

The same thing from a terminal, also from anywhere:

```bash
drove workspace new product ~/code/api ~/code/web
drove plan "rename greeting() and update its callers"
drove execute <feature>
drove features
```

The web UI and the CLI are two clients of the same engine and the same SQLite database — start a
feature in one and finish it in the other. What the daemon adds is that the approval gate becomes
a *state* rather than a blocking prompt, so several features can sit waiting on you at once while
others run.

### Optional: teach a repo about itself

Nothing above requires configuring a project. A repo with no `.drove.toml` works — the base branch
falls back to whatever it is currently on, and nothing is verified because you have not said what
verifying means.

To have Drove run your build and tests before calling a change delivered, do this once, inside the
repository:

```bash
cd ~/code/api && drove init     # writes .drove.toml; the only command that needs a repo
```

Then fill in `[verify]`. See [Configuration](#configuration).

---

## How it works

### Workspaces

A **workspace** is a named set of repositories worked on together. A feature gets a worktree in
every repo in its workspace, so one change can alter an interface in one repo and its callers in
another. One repo is simply a workspace of one.

### Isolation

Each feature gets its own worktrees under `~/.drove/worktrees/<workspace>/<feature>/`, one per
repo, side by side — runs never collide and your own checkouts are never touched.

A worktree is removed only once its commits are in that repo's base branch. Unintegrated work is
preserved and the recovery command printed, per repo independently. Nothing auto-deletes work you
have not merged.

### Branch names

Branches are named from your request: `feat/add-cancel-for-an-in-flight-run`, `fix/login-bug`,
`docs/publishing-flow`. The kind comes from the opening clause, so a feature that happens to
mention tests is still a feature. The name is minted once, at creation, and never changes under
you — by then it may exist in several worktrees and on a remote.

### Sessions

Two different things, kept straight by one rule:

> Artifacts flow **between** stages. Conversations live **within** a stage.

| Stage | Session |
|---|---|
| plan | fresh, discarded — one shot |
| execute | **persistent** across fix rounds and pivots; it must remember what it tried and abandoned |
| review | **fresh every round** — see above |

Every session stays addressable afterwards. The app shows the exact command to drop into the real
conversation, for example `cd <worktree> && claude --resume <session-id>`.

### Pivots

Delivered is a resting state, not a terminal one. `drove pivot <feature> "use a token bucket"`
re-plans on the same branch and resumes the executor, which still knows why it built things the
way it did and what it already rejected.

---

## The app

Five screens in the sidebar — **Overview**, **Board**, **Agents**, **Runs**, and a pinned
**Needs you** section holding everything waiting on a decision — plus **Stages** and
**Workspace** under Configure.

A feature opens with tabs that appear only when there is something behind them:

| Tab | What it shows |
|---|---|
| **plan** | the typed plan, and the approve / revise / decline gate |
| **live** | the agent's output as it happens, and the recorded log afterwards |
| **diff** | every changed file with `+`/`−` counts; one file at a time, line numbers down both sides, with a toggle to the whole file |
| **code** | the entire worktree, so you can read any file rather than only the changed lines |
| **terminal** | a real shell in the worktree |
| **source** | where the branch was pushed, and the link to open it |
| **review** | the reviewer's verdict each round, and who wrote versus who judged |
| **browser** | screenshots and console errors from the optional smoke checks |
| **evidence** | the full pack, rendered, with a copy button |

The **terminal** deserves a word. Agents work in a checkout you did not make, and sometimes the
fastest thing is to run the tests yourself or fix one line by hand — having to leave the app to do
that is what stops people trusting it. The pty lives in the daemon rather than in Electron, so it
works the same in a browser and in the packaged app. It is your shell with your permissions,
reachable only from loopback.

## Feature states

| State | Meaning |
|---|---|
| `planning` | reading your code, writing a plan |
| `awaiting_approval` | **waiting on you** |
| `executing` / `fixing` | implementing, or addressing review findings |
| `reviewing` / `verifying` | a second model is judging the diff; then your own commands run |
| `delivered` | reviewed, verified, pushed — ready to merge |
| `landed` | its branch is in the base branch; the worktree can be reclaimed |
| `needs_human` | the reviewer still blocked it after two fix rounds — usually an ambiguous request |
| `verify_failed` | your own checks failed on the branch |
| `no_changes` | the agent changed nothing; the request may already be satisfied |
| `interrupted` | Drove stopped mid-run — resume it |
| `cancelled` | you stopped it — resume, re-plan, or discard |
| `failed` | the run errored; the reason is recorded on the run |

---

## Verifying: your own checks

After review passes, Drove runs the commands you name — in the worktree, never your checkout:

```toml
[verify]
build = "npm run build"
test  = "npm test"
lint  = "npm run lint"
```

These gate delivery. A failure stops the run at `verify_failed` with the output kept.

### Browser checks (optional)

Your tests prove the code is correct. They cannot tell you the page is blank because one component
throws on render — the build passes, the linter passes, and the app is broken.

```toml
[browser]
start = "npm run dev"
dir   = "web"                      # relative to the repo, optional
url   = "http://localhost:5173"
paths = ["/", "/settings"]
```

Drove starts the app, opens those pages, and records console errors, failed requests and a
screenshot of each. It is **advisory** — findings appear in the evidence pack and in the app but
never fail a run on their own. It refuses to start if something is already serving that URL,
rather than silently testing someone else's server and reporting a pass for work that never ran.

## Publishing

A branch that exists only on this laptop is not delivered in any useful sense — you cannot open
it, send it to anyone, or let CI near it. So once review and your own checks pass, Drove pushes the
feature branch and records a compare link, which appears in the evidence pack and the **source**
tab. Each repo in a workspace is pushed independently.

It pushes with `--force-with-lease`: rewriting our own fix rounds is fine, silently overwriting
someone who has pushed to the branch is not. A push that fails — no remote, no credentials, a
protected branch — is recorded and never fails the run, because none of those say anything about
the quality of the work. Push again from the source tab whenever the cause is fixed.

```toml
[deliver]
push   = true        # false keeps this repo's branches local
remote = "origin"
```

> **Cross-repo changes do not merge atomically.** When a feature spans repos, its branches share a
> name and have to be merged together. The evidence pack and the UI say so; nothing can enforce it.

## Stopping and recovery

**Stop** ends a run wherever it is running — this app, or a `drove execute` in another terminal.
Commits already made stay on the branch, uncommitted edits stay in the worktree, and **Resume**
picks the work back up rather than starting over.

A run in another process is not signalled. A CLI streaming a harness ignores `SIGINT`, sent to its
pid and to its process group alike, and runs to completion regardless — so the request is written
to the run, and the owning process stops itself at its next check, within a couple of seconds.

Runs are long and laptops close. Drove holds a power assertion while agents are working, so the
Mac will not idle-sleep mid-run and kill the harnesses' connections — but a lid close sleeps
regardless, and quitting or crashing is always possible. So on every start Drove looks for work
that was in flight when it last stopped: job state lives in memory, so a run whose owning process
is gone is provably abandoned. Those features are marked **interrupted**, with the stage they died
in recorded, and offered a Resume. A run owned by a live `drove execute` is left alone.

## The evidence pack

Every finished run writes one — JSON for the app, markdown for everyone else. It answers, in the
order a reviewer asks:

**In short** · **Independent review** · **What was asked** · **What changed** ·
**Where to find it** · **Tests and checks** · **In a browser** · **Cost** · **Reopen any of this**

"Independent review" is the section that matters most: if the same harness both wrote and reviewed
the change, the pack says so plainly. "Reopen any of this" lists the resume command for every
agent session in the run.

---

## Configuration

### Per repository — `.drove.toml`

Written by `drove init`. Every section is optional.

```toml
base_branch = "main"

[verify]
# Run in the worktree after review passes. These gate delivery.
build = "npm run build"
test  = "npm test"
lint  = "npm run lint"

[browser]
# Opt-in smoke checks. Advisory — never fails a run on its own.
start = "npm run dev"
dir   = "web"
url   = "http://localhost:5173"
paths = ["/"]

[deliver]
push   = true
remote = "origin"

[harness]
# Cross-harness by stage: whoever writes the code must not be the one who reviews it.
plan    = "claude"
execute = "claude"
review  = "codex"
```

### Per workspace — in the app

**Configure → Stages** sets the harness and, optionally, the model for each stage. Leaving the
model unset is both the default and the recommendation: pinning a model id is how a workspace
silently breaks when a provider retires one.

### Environment

| Variable | Effect |
|---|---|
| `DROVE_HOME` | where state lives (default `~/.drove`) |

## CLI reference

| Command | What it does |
|---|---|
| `drove serve [--port 8787] [--no-open]` | start the daemon and web UI |
| `drove doctor` | which CLIs are installed, authenticated and adapter-backed |
| `drove init` | write `.drove.toml` into a repository |
| `drove workspace new <name> [repos…]` | create a workspace |
| `drove workspace add <name> <repo>` | add a repository to one |
| `drove workspace list` | workspaces and their repositories |
| `drove plan "<what you want>"` | plan a feature, iterate, approve |
| `drove execute <feature>` | implement → review → fix → verify → deliver |
| `drove pivot <feature> "<new direction>"` | change direction; keeps the branch and the agent's memory |
| `drove features` | what exists and where it got to |
| `drove reclaim` | free worktrees whose branches have landed |
| `drove version` | print the version |

A `<feature>` is an id, an id prefix, or a branch name — whichever you have to hand.

Every command except `drove init` runs from anywhere; they talk to the one daemon and the one
database in `~/.drove`, not to the directory you happen to be standing in.

## Where state lives

```
~/.drove/
  drove.db                  features, runs, sessions, workspaces (SQLite, WAL)
  runs/<run-id>/            raw harness JSONL, evidence.json, evidence.md, screenshots
  worktrees/<ws>/<feature>/ one git worktree per repo
```

The raw JSONL *is* the event log, the replay source and the test fixture — nothing is summarised
away. Cost per feature is the sum of its runs, recorded per session.

### What leaves your machine

Your prompts and the code the agents read go to whichever provider each CLI is logged into —
exactly as they would if you ran `claude` or `codex` yourself. Drove adds no destination of its
own: no telemetry, no account, no server. The daemon binds `127.0.0.1` by default.

---

## The macOS app

The desktop app embeds the daemon as a PyInstaller sidecar, so a built app needs no Python, no uv
and no checkout.

```bash
cd web
npm install
npm run desktop:package     # output in web/release/
```

It builds only the host architecture: build separately on Apple Silicon and Intel, because each
DMG must contain a matching native Python sidecar.

Browser checks are unavailable inside the packaged app — the sidecar excludes playwright, whose
browsers live in a user cache rather than the bundle, so shipping it would add 129 MB that could
not run anything. Use `drove serve` from a checkout for those.

**The DMG is currently unsigned**, which is why releases ship as `uv tool install` rather than a
download: macOS refuses an unsigned, unnotarized app that arrives with a quarantine attribute, and
telling people to strip quarantine from an app that drives coding agents is bad advice. The build
config is ready — set `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD` and `APPLE_TEAM_ID` on a machine
with a Developer ID certificate and the same command produces a signed, notarized DMG.

## Developing

```bash
uv sync --extra dev
uv run pytest                    # offline; spends no tokens
uv run pytest -m live            # opt-in; spends real tokens against real CLIs
uv run ruff check drove tests

cd web && npm install && npm run build      # rebuild the UI into drove/web/
uv tool install --force --reinstall .       # --reinstall matters: --force alone reuses uv's cache
```

Built UI assets are committed, because the install path builds the wheel from the git tree —
anything uncommitted is not installed, and building at install time would put Node on every user's
critical path. So after a UI change, rebuild **and** commit `drove/web/`.

Adapters are verified against recorded fixtures in `tests/fixtures/*.jsonl`, replayed offline. CI
runs lint and the offline suite on every push and pull request.

`TODO.md` is written for someone picking this up cold and lists what is deliberately unbuilt.

## Known limits

- **Cross-repo merges are not atomic.** Branches sharing a name must be merged together.
- **The arbiter stage is designed, not built.** After two failed review rounds a run stops at
  `needs_human` rather than asking a third model to break the tie. Its configuration was removed
  rather than left advertising a stage that does nothing.
- **Rate limits are reported, not acted on.** Claude's remaining window is shown; nothing yet
  defers a run because of it.
- **`cursor-agent` is detected but has no adapter.**
- **The DMG is unsigned**, as above.

## Licence

MIT — see [LICENSE](LICENSE).
