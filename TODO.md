# drove — handoff

TODO: a daemon cannot cancel a run owned by `drove execute` in another terminal; cancellation
state is currently process-local.

Written for an agent picking this up cold. Read this file, then `README.md`, then
`drove/harness/base.py` and `drove/pipeline/engine.py` — those two carry the load.

## What it is

A local autonomous engineering pipeline. You describe a feature; it plans, implements, has a
**different model review it**, runs the project's own tests, and leaves you a branch plus an
evidence pack. Everything runs on the coding CLIs already installed on the machine
(`claude`, `codex`, `opencode`), driven **headlessly** — no PTY, no hook shims.

The one idea everything follows from:

> **A coding CLI is a function `(prompt, cwd, schema) → typed object`, plus a stream of events.**

## State

Working end to end, verified on real repos:

| | |
|---|---|
| Harness adapters | claude, codex, opencode — one normalized event vocabulary |
| Pipeline | plan → ⏸ approve → execute → review → fix (≤2) → verify → deliver |
| Isolation | a git worktree per repo per feature, teardown that refuses to lose work |
| Workspaces | 1..N repos per workspace; a feature may change several at once |
| Daemon + UI | FastAPI + SSE + React on localhost; workspaces and repos added from the app |
| Tests | 186 collected, 3 marked `live` (spend tokens, deselected by default) |

Proven live: a cross-repo rename in one workspace (`apilib` + `webapp`), reviewed by codex against
the *combined* diff, delivered on the first round.

```bash
uv tool install --force --reinstall .   # --reinstall matters; --force alone reuses a cached wheel
uv run --extra dev pytest               # offline, no tokens
uv run --extra dev pytest -m live       # hits the real CLIs, spends money
uv run --extra dev ruff check .
cd web && npm install && npm run build  # rebuilds the UI into drove/web/ (committed)
```

---

## Orientation

```
drove/
  cli.py            typer commands: workspace, plan, execute, pivot, features, doctor, serve
  config.py         ~/.drove paths, per-repo .drove.toml
  db.py             sqlite; versioned migrations, some with a Python step
  events.py         ★ the normalized harness event vocabulary
  workspace.py      ★ Workspace / Repo model
  evidence.py       the delivery pack (markdown + json)
  harness/
    base.py         ★ Harness protocol + the JSONL subprocess streamer
    registry.py     preset table — harness differences are DATA, not branching code
    claude_code.py  codex.py  opencode.py
  pipeline/
    engine.py       ★ the run state machine
    schemas.py      PlanDoc / ReviewVerdict + strict-mode JSON Schema
    stages/         plan, execute, review, verify
    prompts/        the actual English sent to the models
  vcs/
    git.py          every git call goes through here
    tree.py         ★ a feature's worktrees, one per repo
  api/
    server.py       REST + SSE
    jobs.py         background plan/cycle jobs
    bus.py          in-process pub/sub for SSE
web/                React + Vite source; built assets are committed into drove/web/
```

---

## Rules that matter

Break these and things fail in ways that are hard to diagnose.

1. **`stdin=DEVNULL` for every harness.** `codex exec` hangs outright without it; `claude -p`
   stalls 3s then warns.
2. **The prompt is the last argv element, and nothing option-shaped may sit next to it.**
   claude's `--add-dir` and `--disallowed-tools` are variadic and will eat it — hence the `--`
   terminator. There is a conformance test for this; it exists because the naive version
   (assert `argv[-1] == prompt`) passed while the code was broken.
3. **JSON Schemas are strict mode** (`additionalProperties: false`, every property in `required`).
   Codex rejects anything else. `json_schema()` handles it; don't bypass it.
4. **The reviewer is stateless — a fresh session every round.** Not a tunable. A resumed reviewer
   is judging its own prior verdict and tends to ratify the fix. The implementer, by contrast,
   keeps its session across fix rounds.
5. **Never `git worktree remove --force`, never `git branch -D`.** git refuses to delete a branch
   holding unmerged commits; that refusal is a safety net, not an obstacle.
6. **git is the authority on what changed**, not harness events — codex emits `FileChanged`,
   claude does not.
7. **Usage events are deltas**, and opencode's `tokens.total` is a per-step sum including cache
   reads, *not* a running total. Summing it invents tens of thousands of phantom tokens.
8. **Every stage is a loop over repos.** One repo is N=1. Do not add a single-repo fast path;
   it will drift.

---

## TODO

Ordered. Each item says why it matters, where to work, and what done looks like.

### 0. The arbiter stage — designed, never built

`config.py` defaults `arbiter = "opencode"` and the design says a third model breaks a deadlock.
Nothing reads it. Today the engine just stops at `needs_human` after two rounds.

- New `pipeline/stages/arbitrate.py`. Fresh session. Input: the plan, the combined diff, and
  **both** verdicts. Output a typed schema (add `Arbitration` to `schemas.py`) —
  `{siding: "implementer" | "reviewer", reasoning, blocking: [...]}`.
- Call it in `engine.py` where `status = "needs_human"` is set. If it sides with the implementer,
  deliver with the disagreement recorded in the evidence pack. If with the reviewer, stay
  `needs_human` but attach a sharpened issue list.
- **Watch out:** opencode has no native structured output — the adapter asks for JSON in the
  prompt and it complies maybe 4 times in 5. Handle a failed arbitration by falling back to
  `needs_human`, never by guessing.
- **Done when:** engine tests cover both sidings with stubs, and the evidence pack shows the
  three-way disagreement.

### 1. Rate-limit back-pressure

`RateLimit` events are parsed and *displayed* (`ui.py`, `jobs.py`) and otherwise ignored. During
development this hit 88% of the 5-hour window with nothing reacting.

- In `api/jobs.py`, keep the most recent utilization. Above ~0.85, refuse to *start* new runs —
  queue them — while letting in-flight ones finish. Three runs completing beats eight dying
  mid-execute.
- Surface it: the UI should say "holding new runs, 5h window 91% used", not silently stall.
- **Done when:** a test drives the gate with synthetic RateLimit events and asserts queueing.

### 2. Parallel runs, actually tested

`MAX_CONCURRENT_RUNS = 2` in `api/jobs.py` and the semaphore has never run under load.

- Test two features executing at once: separate worktrees, no interleaved commits, SSE events
  correctly attributed per feature.
- Check the SQLite write path under concurrency. WAL is on, but `busy_timeout` is the default 5s —
  confirm that is enough while two runs record sessions.
- **Done when:** a test starts two cycles with stubbed stages and asserts isolation.

### 3. Cost ledger

`Result` carries exact token counts and, for claude, real cost. `sessions` stores per-stage
figures. There is no per-feature or per-day view.

- `GET /api/costs` and a UI panel: cost per feature, per workspace, per day.
- **Be honest about unknowns:** codex reports tokens only, and opencode reports `cost: 0` under
  subscription auth — where the marginal cost genuinely *is* zero. Show `—`, never a guessed
  figure. (A `pricing.py` was deliberately deleted; see commit `ecbe221` for why.)

### 4. `cursor-agent` adapter

Installed on this machine, listed in `registry.PLANNED`, honestly reported as unimplemented.

- Follow `codex.py`. Record a real session as a fixture under `tests/fixtures/` and make it pass
  `tests/test_harness_conformance.py`, which is parametrized over every registered harness.

### 5. Browser verification

Deferred from v1. Drove's own pitch includes browser-based user-flow testing.

- New stage after `verify`: drive Playwright over flows named in `PlanDoc.test_plan`, capture
  screenshots into the evidence pack.
- Needs a new config block (`[verify.browser]`) and a way to say what "the app running" means.

### 6. Delivery to GitHub

Everything stops at a local branch. `gh` is the obvious route.

- Open a draft PR per repo with the plan as the body and the evidence pack as a comment.
- **For a cross-repo feature, the PRs must reference each other** and say they have to land
  together. This is presentation, not enforcement — see the caveat below.

### 7. Packaging

Currently `uv tool install git+…`, with UI assets committed because the wheel is built from the
git tree.

- PyPI + Trusted Publishing (GitHub Actions OIDC, no stored token), then a Homebrew tap with
  `brew services` for a launchd daemon.
- **Rename before publishing.** "Drove" is a funded startup's product name. Fine as a local
  working title, not on a public registry.

---

## Known gotchas

Each cost real debugging time.

- `codex exec resume` takes a **different, smaller option set** than `codex exec`: no `-s`,
  no `-C`, no `--add-dir`. Flags must precede the `SESSION_ID` positional.
- `codex review` exists and is purpose-built, but has **no `--json` and no `--output-schema`**, so
  it cannot return a machine-readable verdict. The review stage uses `codex exec` with a schema.
- `codex` refuses to run outside a git repo and exits **before emitting any event** — a silent,
  baffling failure. The adapter passes `--skip-git-repo-check` when `cwd` has no `.git`. Note a
  worktree's `.git` is a **file**, not a directory.
- `codex` writes login status to **stderr**.
- `claude --max-turns 1` breaks structured output: it exits `error_max_turns` before emitting the
  object. The adapter floors it at 2 whenever a schema is set.
- `claude --json-schema` takes **inline JSON**; `codex --output-schema` takes a **file path**.
- FastAPI runs `def` routes in a threadpool with no running event loop. Routes here are
  deliberately sync (blocking sqlite and git would stall the SSE loop), so jobs are scheduled onto
  the loop with `run_coroutine_threadsafe` and the bus hops publishes across.
- `uv tool install --force .` reuses a cached wheel for local paths. Use `--reinstall`.
- Landed detection cannot recognize a feature branch deleted after merge. A missing branch is
  indistinguishable from one force-deleted with unmerged work, so Drove leaves it `delivered`.

## The caveat that cannot be engineered away

**Cross-repo branches do not merge atomically.** A feature that changes an interface in one repo
and its callers in another produces branches that must land together; merging one without the
other breaks production. The evidence pack and the CLI both say so, and the branches share a name.
That is presentation, not a guarantee. Real enforcement means a merge queue or stacked PRs, and
should be treated as its own project rather than bolted onto delivery.
