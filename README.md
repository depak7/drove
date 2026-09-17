# vorflux-local

Local autonomous engineering pipeline. File a task against a repo, approve one plan, and come back
to a reviewed branch and an evidence pack — driven entirely by the coding CLIs you already have
installed and logged in (`claude`, `codex`, `opencode`).

```
PLAN    →  ⏸ you approve  →  EXECUTE  →  REVIEW  →  VERIFY  →  DELIVER
claude                        claude      codex     repo cmds  branch + evidence
```

The point is **cross-model review**: the harness that wrote the code never grades its own homework.

## Install

```bash
uv tool install git+https://github.com/<you>/vorflux-local
cd ~/my-repo && vorflux init && vorflux doctor
```

## Use

```bash
vorflux plan "add rate limiting to the API"   # plan, then approve / decline / type feedback
vorflux execute <feature>                     # implement it on an isolated worktree branch
vorflux features                              # what exists and where it got to
vorflux pivot <feature> "use a token bucket"  # change direction; keeps the branch and the agent's memory
```

Each feature gets its own git worktree under `~/.vorflux/worktrees/`, so runs never collide and
your own checkout is never touched. A worktree is only ever removed once its commits are in the
base branch — unintegrated work is preserved and the recovery command printed.

Status: M0 — pipeline spine, Claude adapter, plan stage.
