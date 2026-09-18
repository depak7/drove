"""The run state machine: execute → review → fix → verify → deliver.

The fix loop is bounded. Two rounds, then the run stops as `needs_human` rather than burning
tokens on a disagreement neither side is going to concede. A cross-model deadlock is information —
it usually means the intent was ambiguous, which is a thing for a person to settle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from drove.config import runs_dir
from drove.events import HarnessEvent
from drove.pipeline.schemas import PlanDoc, ReviewVerdict
from drove.pipeline.stages import review as review_stage
from drove.pipeline.stages import browser as browser_stage
from drove.pipeline.stages import verify as verify_stage
from drove.pipeline.stages.execute import ExecuteOutcome, run_execute
from drove.vcs import tree as trees_mod
from drove.vcs.tree import FeatureTrees
from drove.workspace import Workspace

MAX_FIX_ROUNDS = 2


@dataclass
class RunOutcome:
    status: str
    execute: ExecuteOutcome | None = None
    reviews: list[ReviewVerdict] = field(default_factory=list)
    verify: verify_stage.VerifyOutcome | None = None
    browser: browser_stage.BrowserOutcome | None = None
    sessions: dict[str, str] = field(default_factory=dict)
    files_changed: list[str] = field(default_factory=list)
    repos_touched: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    note: str = ""


Reporter = Callable[[str, str], None]


def _noop(stage: str, message: str) -> None:  # pragma: no cover - default reporter
    pass


async def run_cycle(
    plan: PlanDoc,
    trees: FeatureTrees,
    workspace: Workspace,
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
        plan, trees, workspace, run_id, intent, resume_session=resume_session, on_event=on_event
    )
    outcome.execute = executed
    outcome.files_changed = list(executed.files_changed)
    outcome.sessions["execute"] = executed.session_id
    account(executed.cost_usd, executed.tokens_in, executed.tokens_out)

    # "This invocation committed nothing" is not the same as "the branch holds nothing". A retry
    # after a crashed run finds the work already done and rightly adds nothing — and skipping
    # review there would strand finished, unreviewed code on the branch and report it as if the
    # agent had done nothing at all.
    standing = trees_mod.touched(trees)
    if not executed.committed and not standing:
        outcome.status = "no_changes"
        outcome.note = "the executor made no changes, and the branch has none"
        return _finish(outcome, costs, trees)

    if not executed.committed:
        report("review", "nothing new to build; reviewing what is already on the branch")

    # --- REVIEW / FIX ----------------------------------------------------------------------
    for attempt in range(1, MAX_FIX_ROUNDS + 1):
        report("review", f"independent review by {workspace.harness['review']} (round {attempt})")
        reviewed = await review_stage.run_review(
            plan, trees, workspace, run_id, attempt=attempt, on_event=on_event
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
            return _finish(outcome, costs, trees)

        report("fix", f"addressing {len(reviewed.verdict.blocking)} blocking issue(s)")
        fixed = await run_execute(
            plan,
            trees,
            workspace,
            run_id,
            f"fix: {intent}",
            resume_session=executed.session_id,  # the implementer keeps its memory
            on_event=on_event,
            prompt_override=review_stage.render_fix_prompt(reviewed.verdict, trees.branch),
        )
        outcome.files_changed = sorted(set(outcome.files_changed) | set(fixed.files_changed))
        account(fixed.cost_usd, fixed.tokens_in, fixed.tokens_out)
        executed = fixed

    outcome.execute = executed

    # --- VERIFY ----------------------------------------------------------------------------
    configured = _configured_checks(trees)
    if configured:
        report("verify", f"running {configured} project command(s)")
        verified = verify_stage.run_all(trees, workspace)
        outcome.verify = verified
        if not verified.passed:
            outcome.status = "verify_failed"
            names = ", ".join(
                f"{c.repo}/{c.name}" if c.repo else c.name for c in verified.failures
            )
            outcome.note = f"project checks failed: {names}"
            return _finish(outcome, costs, trees)

    # --- BROWSER --------------------------------------------------------------------------
    # Advisory. Browser checks are the flakiest thing in any pipeline, and a stage that blocks
    # delivery before it has earned trust is a stage people switch off. Recorded either way.
    #
    # Reached only when verify passed, because verify failing already stopped the run for a human,
    # and booting a dev server to photograph an app whose tests are red adds a minute for
    # information nobody asked for yet.
    for tree in trees_mod.touched(trees):
        config = tree.repo.config.browser
        if not config:
            continue
        report("browser", f"opening {config.get('url', 'the app')}")
        outcome.browser = await browser_stage.run_browser(
            tree.path, config, runs_dir(run_id) / "screens"
        )
        if outcome.browser.skipped:
            report("browser", f"skipped: {outcome.browser.skipped}")
        elif not outcome.browser.passed:
            names = ", ".join(c.path for c in outcome.browser.failures)
            outcome.note = f"browser checks flagged {names}"
        break  # one app per feature; the first configured repo owns it

    outcome.status = "delivered"
    return _finish(outcome, costs, trees)


def _configured_checks(trees: FeatureTrees) -> int:
    """How many verify commands the touched repos declare between them."""
    return sum(len(t.repo.config.verify) for t in trees_mod.touched(trees))


def _finish(
    outcome: RunOutcome, costs: list[float], trees: FeatureTrees | None = None
) -> RunOutcome:
    outcome.cost_usd = sum(costs) if costs else None
    if trees is not None:
        # git is the authority on what changed, not the harness event stream: codex emits
        # FileChanged events, claude does not.
        changed = trees_mod.changed_files(trees)
        if changed:
            outcome.files_changed = changed
        outcome.repos_touched = [t.repo.name for t in trees_mod.touched(trees)]
    return outcome
