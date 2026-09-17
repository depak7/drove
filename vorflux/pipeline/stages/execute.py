"""EXECUTE stage: an approved plan becomes commits on the feature branch."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from vorflux.config import RepoConfig, runs_dir
from vorflux.events import FileChanged, HarnessEvent, Result
from vorflux.harness import registry
from vorflux.harness.base import InvokeSpec
from vorflux.pipeline.schemas import PlanDoc
from vorflux.vcs import git
from vorflux.vcs.worktree import Worktree

# Above this share of the context window, resuming risks a lossy auto-compaction mid-task, so a
# pivot starts a fresh session seeded with a written handoff instead.
CONTEXT_HANDOFF_THRESHOLD = 0.60
DEFAULT_CONTEXT_WINDOW = 200_000


class StageError(RuntimeError):
    pass


@dataclass
class ExecuteOutcome:
    session_id: str
    head_sha: str | None
    committed: bool
    files_changed: list[str] = field(default_factory=list)
    final_text: str = ""
    cost_usd: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    raw_log: Path | None = None


def render_prompt(plan: PlanDoc, branch: str) -> str:
    template = (
        resources.files("vorflux.pipeline.prompts")
        .joinpath("execute.md")
        .read_text(encoding="utf-8")
    )
    return template.format(plan=plan.model_dump_json(indent=2), branch=branch)


def write_plan_file(wt: Worktree, plan: PlanDoc) -> Path:
    """Drop the approved plan into the worktree.

    Gitignored, so it never reaches a commit. It means the agent can re-read its own instructions
    at any point — including after a compaction has dropped them from the conversation.
    """
    target = wt.path / ".vorflux" / "plan.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    (target.parent / ".gitignore").write_text("*\n")
    target.write_text(_as_markdown(plan))
    return target


def _as_markdown(plan: PlanDoc) -> str:
    lines = [f"# Plan\n\n{plan.summary}\n"]
    if plan.steps:
        lines.append("## Steps\n")
        for i, step in enumerate(plan.steps, 1):
            lines.append(f"{i}. **{step.title}**")
            if step.files:
                lines.append(f"   - files: {', '.join(step.files)}")
            if step.detail:
                lines.append(f"   - {step.detail}")
        lines.append("")
    if plan.acceptance_criteria:
        lines.append("## Done when\n")
        lines += [f"- {c}" for c in plan.acceptance_criteria]
        lines.append("")
    if plan.risks:
        lines.append("## Risks\n")
        lines += [f"- {r}" for r in plan.risks]
        lines.append("")
    if plan.test_plan:
        lines.append(f"## Verify\n\n{plan.test_plan}\n")
    return "\n".join(lines)


def should_resume(tokens_in: int, cache_read: int, window: int = DEFAULT_CONTEXT_WINDOW) -> bool:
    """Resume while the prior session still has room; hand off to a fresh one above the line."""
    return (tokens_in + cache_read) < window * CONTEXT_HANDOFF_THRESHOLD


async def run_execute(
    plan: PlanDoc,
    wt: Worktree,
    cfg: RepoConfig,
    run_id: str,
    title: str,
    resume_session: str | None = None,
    on_event: Callable[[HarnessEvent], None] | None = None,
    prompt_override: str | None = None,
) -> ExecuteOutcome:
    """Implement the plan, or — with `prompt_override` — a fix round against the same session.

    A fix round is the same stage, not a new one: same harness, same worktree, same conversation.
    Only the instruction differs, so the agent does not re-derive the code it just wrote.
    """
    session_id = resume_session or str(uuid.uuid4())
    suffix = "execute" if prompt_override is None else "fix"
    raw_log = runs_dir(run_id) / f"{suffix}.jsonl"

    write_plan_file(wt, plan)

    harness = registry.get(cfg.harness["execute"])
    spec = InvokeSpec(
        prompt=prompt_override or render_prompt(plan, wt.branch),
        cwd=wt.path,
        mode="write",
        session_id=session_id,
        resume=bool(resume_session),
        extra_dirs=[wt.path],
        raw_log=raw_log,
    )

    touched: list[str] = []
    result: Result | None = None
    async for event in harness.invoke(spec):
        if on_event:
            on_event(event)
        if isinstance(event, FileChanged) and event.path not in touched:
            touched.append(event.path)
        elif isinstance(event, Result):
            result = event

    if result is None:
        raise StageError("executor produced no result event")
    if not result.ok:
        raise StageError(f"executor failed: {result.error or 'unknown error'}")

    head_sha, committed = commit(wt, title)

    return ExecuteOutcome(
        session_id=result.session_id or session_id,
        head_sha=head_sha,
        committed=committed,
        files_changed=touched,
        final_text=result.final_text,
        cost_usd=result.cost_usd,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        raw_log=raw_log,
    )


def commit(wt: Worktree, title: str) -> tuple[str | None, bool]:
    """Vorflux is the sole committer.

    Agents are told not to commit, so that what lands on the branch is exactly one reviewable unit
    per run rather than whatever cadence the model happened to choose. It also sidesteps the
    `index.lock` contention that bites when several agents share a repo.
    """
    if not git.is_dirty(wt.path):
        return (git.head_sha(wt.path), False)

    git.git(wt.path, "add", "-A")
    # The user's own words, not plan.summary — a summary is an explanatory paragraph whose first
    # line makes a terrible subject ("`test_calc.py` does `from calc import add, subtract`, but…").
    message = " ".join(title.split())[:72] or "vorflux run"
    git.git(
        wt.path,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        message,
    )
    return (git.head_sha(wt.path), True)
