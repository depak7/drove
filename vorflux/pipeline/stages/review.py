"""REVIEW stage: an independent harness judges the diff against the approved intent.

Two rules make this worth more than asking one model to check its own work:

* a **different** harness reviews than the one that implemented, and
* the reviewer is **stateless** — a fresh session every round.

The second matters as much as the first. A reviewer resumed from the round that blocked the change
is evaluating its own prior judgment, and models are agreeable: it tends to accept the fix because
accepting confirms it was right. It also must not see the implementer's reasoning, only the
artifacts, since a confident justification for a shortcut is exactly what talks a reviewer into
approving one. The same issue found twice by two fresh reviewers is real signal.

Note on tooling: `codex review` exists and is purpose-built, but has no `--json` or
`--output-schema`, so it cannot return a machine-readable verdict. We use `codex exec` with a
schema instead.
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
from vorflux.pipeline.schemas import PlanDoc, ReviewVerdict, json_schema
from vorflux.vcs import git
from vorflux.vcs.worktree import Worktree

# Enough to review a normal feature inline; beyond it the reviewer runs git diff itself rather
# than us silently truncating the thing being judged.
MAX_INLINE_DIFF = 60_000


class StageError(RuntimeError):
    pass


@dataclass
class ReviewOutcome:
    verdict: ReviewVerdict
    session_id: str
    harness: str
    cost_usd: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    raw_log: Path | None = None

    @property
    def passed(self) -> bool:
        return self.verdict.verdict == "pass"


def diff_for(wt: Worktree) -> str:
    """Changes this branch introduces relative to where it diverged from base.

    Three dots, not two: `base...HEAD` is the change *since the merge base*, so commits that
    landed on base after this feature branched are not reported as if the feature undid them.
    """
    return git.git(wt.path, "diff", f"{wt.base}...HEAD", check=False)


def render_prompt(plan: PlanDoc, wt: Worktree, diff: str) -> str:
    template = (
        resources.files("vorflux.pipeline.prompts")
        .joinpath("review.md")
        .read_text(encoding="utf-8")
    )
    if len(diff) > MAX_INLINE_DIFF:
        diff = (
            diff[:MAX_INLINE_DIFF]
            + f"\n\n[... truncated at {MAX_INLINE_DIFF} chars — run the git diff command above "
            "for the rest; do not judge on this excerpt alone ...]"
        )
    return template.format(
        plan=plan.model_dump_json(indent=2),
        base=wt.base,
        branch=wt.branch,
        cwd=wt.path,
        diff=diff or "(no changes)",
    )


async def run_review(
    plan: PlanDoc,
    wt: Worktree,
    cfg: RepoConfig,
    run_id: str,
    attempt: int = 1,
    on_event: Callable[[HarnessEvent], None] | None = None,
) -> ReviewOutcome:
    diff = diff_for(wt)
    if not diff.strip():
        raise StageError("nothing to review: the branch has no changes against its base")

    name = cfg.harness["review"]
    harness = registry.get(name)
    # Fresh session, every round. Never resumed.
    session_id = str(uuid.uuid4())
    raw_log = runs_dir(run_id) / f"review-{attempt}.jsonl"

    spec = InvokeSpec(
        prompt=render_prompt(plan, wt, diff),
        cwd=wt.path,
        mode="readonly",
        output_schema=json_schema(ReviewVerdict),
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
        raise StageError("reviewer produced no result event")
    if not result.ok:
        raise StageError(f"reviewer failed: {result.error or 'unknown error'}")
    if result.structured is None:
        raise StageError(f"reviewer returned no verdict object; got {result.final_text[:300]!r}")

    try:
        verdict = ReviewVerdict.model_validate(result.structured)
    except ValidationError as exc:
        raise StageError(f"verdict did not match schema: {exc}") from exc

    return ReviewOutcome(
        verdict=verdict,
        session_id=result.session_id or session_id,
        harness=name,
        cost_usd=result.cost_usd,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        raw_log=raw_log,
    )


def render_fix_prompt(verdict: ReviewVerdict, branch: str) -> str:
    template = (
        resources.files("vorflux.pipeline.prompts").joinpath("fix.md").read_text(encoding="utf-8")
    )
    lines = []
    for i, issue in enumerate(verdict.blocking, 1):
        where = issue.file + (f":{issue.line}" if issue.line else "")
        lines.append(f"{i}. [{issue.severity}] {where}\n   {issue.why}")
    return template.format(issues="\n".join(lines) or verdict.summary, branch=branch)
