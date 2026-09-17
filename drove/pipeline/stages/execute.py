"""EXECUTE stage: an approved plan becomes commits on the feature branch in each repo."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from drove.config import runs_dir
from drove.events import HarnessEvent, Result
from drove.harness import registry
from drove.harness.base import InvokeSpec
from drove.pipeline.schemas import PlanDoc
from drove.vcs import git
from drove.vcs import tree as trees_mod
from drove.vcs.tree import FeatureTrees
from drove.workspace import Workspace

# Above this share of the context window, resuming risks a lossy auto-compaction mid-task, so the
# next round starts a fresh session seeded with a written handoff instead.
CONTEXT_HANDOFF_THRESHOLD = 0.60
DEFAULT_CONTEXT_WINDOW = 200_000


class StageError(RuntimeError):
    pass


@dataclass
class ExecuteOutcome:
    session_id: str
    head_shas: dict[str, str] = field(default_factory=dict)
    committed: bool = False
    files_changed: list[str] = field(default_factory=list)
    final_text: str = ""
    cost_usd: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    raw_log: Path | None = None

    @property
    def head_sha(self) -> str | None:
        """The primary repo's commit — what a single-repo feature means by "the commit"."""
        return next(iter(self.head_shas.values()), None)


def render_prompt(plan: PlanDoc, trees: FeatureTrees) -> str:
    template = (
        resources.files("drove.pipeline.prompts")
        .joinpath("execute.md")
        .read_text(encoding="utf-8")
    )
    repos = "\n".join(f"  {t.repo.name}/   (base branch {t.base})" for t in trees)
    return template.format(
        plan=plan.model_dump_json(indent=2),
        branch=trees.branch,
        root=trees.root,
        repos=repos,
    )


def write_plan_file(root: Path, plan: PlanDoc) -> Path:
    """Drop the approved plan into the feature root.

    Gitignored, and outside every repo's worktree, so it can never reach a commit. It means the
    agent can re-read its own instructions at any point — including after a compaction has dropped
    them from the conversation.
    """
    target = root / ".drove" / "plan.md"
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
    trees: FeatureTrees,
    workspace: Workspace,
    run_id: str,
    title: str,
    resume_session: str | None = None,
    on_event: Callable[[HarnessEvent], None] | None = None,
    prompt_override: str | None = None,
) -> ExecuteOutcome:
    """Implement the plan, or — with `prompt_override` — a fix round in the same session.

    A fix round is the same stage, not a new one: same harness, same worktrees, same conversation.
    Only the instruction differs, so the agent does not re-derive the code it just wrote.
    """
    session_id = resume_session or str(uuid.uuid4())
    suffix = "execute" if prompt_override is None else "fix"
    raw_log = runs_dir(run_id) / f"{suffix}.jsonl"

    write_plan_file(trees.root, plan)

    harness = registry.get(workspace.harness["execute"])
    spec = InvokeSpec(
        prompt=prompt_override or render_prompt(plan, trees),
        # The agent works from the feature root so every repo is a sibling directory, and each
        # worktree is granted explicitly so writes are allowed where they belong and nowhere else.
        cwd=trees.root,
        mode="write",
        session_id=session_id,
        resume=bool(resume_session),
        extra_dirs=[t.path for t in trees],
        raw_log=raw_log,
    )

    result: Result | None = None
    async for event in harness.invoke(spec):
        if on_event:
            on_event(event)
        if isinstance(event, Result):
            result = event

    if result is None:
        raise StageError("executor produced no result event")
    if not result.ok:
        raise StageError(f"executor failed: {result.error or 'unknown error'}")

    head_shas = commit(trees, title)

    return ExecuteOutcome(
        session_id=result.session_id or session_id,
        head_shas=head_shas,
        committed=bool(head_shas),
        files_changed=trees_mod.changed_files(trees),
        final_text=result.final_text,
        cost_usd=result.cost_usd,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        raw_log=raw_log,
    )


def commit(trees: FeatureTrees, title: str) -> dict[str, str]:
    """Commit in every repo the agent touched. Drove is the sole committer.

    Agents are told not to commit so that each run lands as exactly one reviewable unit per repo,
    rather than whatever cadence the model happened to choose. It also sidesteps the `index.lock`
    contention that bites when several agents share a repository.

    The same subject in each repo is deliberate: it is how a reviewer recognises the branches as
    one change spread across repositories.
    """
    # The user's own words, not plan.summary — a summary is explanatory prose whose first line
    # makes a terrible commit subject.
    message = " ".join(title.split())[:72] or "drove run"

    shas: dict[str, str] = {}
    for t in trees:
        if not git.is_dirty(t.path):
            continue
        git.git(t.path, "add", "-A")
        git.git(t.path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
        shas[t.repo.name] = git.head_sha(t.path)
    return shas
