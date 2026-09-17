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
cd ~/my-repo && vorflux init && vorflux doctor && vorflux serve
```

Status: M0 — pipeline spine, Claude adapter, plan stage.
