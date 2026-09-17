"""The run state machine: execute → review → fix → verify → deliver.

The fix loop is bounded. Two rounds, then the run stops as `needs_human` rather than burning
tokens on a disagreement neither side is going to concede. A cross-model deadlock is information —
it usually means the intent was ambiguous, which is a thing for a person to settle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from vorflux.config import RepoConfig
from vorflux.events import HarnessEvent
from vorflux.pipeline.schemas import PlanDoc, ReviewVerdict
from vorflux.pipeline.stages import review as review_stage
from vorflux.pipeline.stages import verify as verify_stage
from vorflux.pipeline.stages.execute import ExecuteOutcome, run_execute
from vorflux.vcs import git
from vorflux.vcs.worktree import Worktree

MAX_FIX_ROUNDS = 2


@dataclass
class RunOutcome:
    status: str
    execute: ExecuteOutcome | None = None
    reviews: list[ReviewVerdict] = field(default_factory=list)
    verify: verify_stage.VerifyOutcome | None = None
    sessions: dict[str, str] = field(default_factory=dict)
    files_changed: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    note: str = ""


Reporter = Callable[[str, str], None]


def _noop(stage: str, message: str) -> None:  # pragma: no cover - default reporter
    pass


async def run_cycle(
    plan: PlanDoc,
    wt: Worktree,
    cfg: RepoConfig,
    run_id: str,
    intent: str,
    resume_session: str | None = None,
    on_event: Callable[[HarnessEvent], None] | None = None,
    report: Reporter = _noop,
) -> RunOutcome:
    outcome = RunOutcome(status="running")
    costs: list[float] = []

    def account(cost: float | None, tin: int, tout: int) -> None:
        if cost is not None:
            costs.append(cost)
        outcome.tokens_in += tin
        outcome.tokens_out += tout

    # --- EXECUTE ---------------------------------------------------------------------------
    report("execute", "implementing the approved plan")
    executed = await run_execute(
        plan, wt, cfg, run_id, intent, resume_session=resume_session, on_event=on_event
    )
    outcome.execute = executed
    outcome.files_changed = list(executed.files_changed)
    outcome.sessions["execute"] = executed.session_id
    account(executed.cost_usd, executed.tokens_in, executed.tokens_out)

    if not executed.committed:
        outcome.status = "no_changes"
        outcome.note = "the executor made no changes"
        return _finish(outcome, costs, wt)

    # --- REVIEW / FIX ----------------------------------------------------------------------
    for attempt in range(1, MAX_FIX_ROUNDS + 1):
        report("review", f"independent review by {cfg.harness['review']} (round {attempt})")
        reviewed = await review_stage.run_review(
            plan, wt, cfg, run_id, attempt=attempt, on_event=on_event
        )
        outcome.reviews.append(reviewed.verdict)
        # Each round is a distinct session: the reviewer is never resumed.
        outcome.sessions[f"review-{attempt}"] = reviewed.session_id
        account(reviewed.cost_usd, reviewed.tokens_in, reviewed.tokens_out)

        if reviewed.passed:
            break

        if attempt == MAX_FIX_ROUNDS:
            outcome.status = "needs_human"
            outcome.note = (
                f"still blocked after {MAX_FIX_ROUNDS} review rounds: "
                f"{len(reviewed.verdict.blocking)} issue(s) outstanding"
            )
            return _finish(outcome, costs, wt)

        report("fix", f"addressing {len(reviewed.verdict.blocking)} blocking issue(s)")
        fixed = await run_execute(
            plan,
            wt,
            cfg,
            run_id,
            f"fix: {intent}",
            resume_session=executed.session_id,  # the implementer keeps its memory
            on_event=on_event,
            prompt_override=review_stage.render_fix_prompt(reviewed.verdict, wt.branch),
        )
        outcome.files_changed = sorted(set(outcome.files_changed) | set(fixed.files_changed))
        account(fixed.cost_usd, fixed.tokens_in, fixed.tokens_out)
        executed = fixed

    outcome.execute = executed

    # --- VERIFY ----------------------------------------------------------------------------
    if cfg.verify:
        report("verify", f"running {len(cfg.verify)} project command(s)")
        verified = verify_stage.run_verify(Path(wt.path), cfg.verify)
        outcome.verify = verified
        if not verified.passed:
            outcome.status = "verify_failed"
            names = ", ".join(c.name for c in verified.failures)
            outcome.note = f"project checks failed: {names}"
            return _finish(outcome, costs, wt)

    outcome.status = "delivered"
    return _finish(outcome, costs, wt)


def _finish(outcome: RunOutcome, costs: list[float], wt: Worktree | None = None) -> RunOutcome:
    outcome.cost_usd = sum(costs) if costs else None
    if wt is not None:
        changed = git.changed_files(wt.path, wt.base)
        if changed:
            outcome.files_changed = changed
    return outcome
