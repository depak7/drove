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

from vorflux.config import RepoConfig, runs_dir
from vorflux.events import HarnessEvent, Result
from vorflux.harness import registry
from vorflux.harness.base import InvokeSpec
from vorflux.pipeline.schemas import PlanDoc, json_schema


class StageError(RuntimeError):
    pass


@dataclass
class PlanOutcome:
    plan: PlanDoc
    session_id: str
    cost_usd: float | None
    raw_log: Path


def render_prompt(task: str, cfg: RepoConfig) -> str:
    template = (
        resources.files("vorflux.pipeline.prompts").joinpath("plan.md").read_text(encoding="utf-8")
    )
    return template.format(repo=cfg.root, base_branch=cfg.base_branch, task=task)


async def run_plan(
    task: str,
    cfg: RepoConfig,
    run_id: str | None = None,
    on_event: Callable[[HarnessEvent], None] | None = None,
) -> PlanOutcome:
    run_id = run_id or uuid.uuid4().hex[:12]
    session_id = str(uuid.uuid4())
    raw_log = runs_dir(run_id) / "plan.jsonl"

    harness = registry.get(cfg.harness["plan"])
    spec = InvokeSpec(
        prompt=render_prompt(task, cfg),
        cwd=cfg.root,
        mode="readonly",
        output_schema=json_schema(PlanDoc),
        session_id=session_id,
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
    )
