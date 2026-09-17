"""PLAN stage: task text in, typed PlanDoc out.

Read-only by construction -- the planner may read the repo but cannot edit it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from drove.config import runs_dir
from drove.events import HarnessEvent, Result
from drove.harness import registry
from drove.harness.base import InvokeSpec
from drove.pipeline.schemas import PlanDoc, json_schema
from drove.vcs.tree import FeatureTrees
from drove.workspace import Workspace


class StageError(RuntimeError):
    pass


@dataclass
class PlanOutcome:
    plan: PlanDoc
    session_id: str
    cost_usd: float | None
    raw_log: Path
    tokens_in: int = 0
    tokens_out: int = 0


def _template(name: str) -> str:
    return (
        resources.files("drove.pipeline.prompts").joinpath(name).read_text(encoding="utf-8")
    )


def render_prompt(task: str, workspace: Workspace, trees: FeatureTrees) -> str:
    repos = "\n".join(f"  {t.repo.name}/   (base branch {t.base})" for t in trees)
    return _template("plan.md").format(
        workspace=workspace.name, root=trees.root, repos=repos, task=task
    )


def render_revision(feedback: str) -> str:
    return _template("plan_revise.md").format(feedback=feedback)


async def run_plan(
    task: str,
    workspace: Workspace,
    trees: FeatureTrees,
    run_id: str | None = None,
    on_event: Callable[[HarnessEvent], None] | None = None,
    resume_session: str | None = None,
    feedback: str | None = None,
) -> PlanOutcome:
    """Produce a plan, or revise one.

    `resume_session` + `feedback` continues an existing planning conversation rather than starting
    over. That matters: the planner spent real tokens reading the codebase, and a revision like
    "use PKCE instead" should adjust that understanding, not rebuild it from nothing.

    The planner always runs in the feature's worktrees, so on a pivot it sees what was already
    built rather than only the base branch.
    """
    run_id = run_id or uuid.uuid4().hex[:12]
    session_id = resume_session or str(uuid.uuid4())
    raw_log = runs_dir(run_id) / "plan.jsonl"

    if feedback and resume_session:
        prompt = render_revision(feedback)
    else:
        prompt = render_prompt(task, workspace, trees)

    harness = registry.get(workspace.harness["plan"])
    spec = InvokeSpec(
        prompt=prompt,
        cwd=trees.root,
        mode="readonly",
        output_schema=json_schema(PlanDoc),
        session_id=session_id,
        resume=bool(resume_session),
        raw_log=raw_log,
    )

    result: Result | None = None
    async for event in harness.invoke(spec):
        if on_event:
            on_event(event)
        if isinstance(event, Result):
            result = event

    if result is None:
        raise StageError("planner produced no result event")
    if not result.ok:
        raise StageError(f"planner failed: {result.error or 'unknown error'}")
    if result.structured is None:
        raise StageError(
            "planner returned no structured output; "
            f"got {result.final_text[:300]!r}"
        )

    try:
        plan = PlanDoc.model_validate(result.structured)
    except ValidationError as exc:
        raise StageError(f"plan did not match schema: {exc}") from exc

    return PlanOutcome(
        plan=plan,
        session_id=result.session_id or session_id,
        cost_usd=result.cost_usd,
        raw_log=raw_log,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )
