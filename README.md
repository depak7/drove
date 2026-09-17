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

Status: M0 — pipeline spine, Claude adapter, plan stage.
