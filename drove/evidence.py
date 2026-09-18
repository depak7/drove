"""The evidence pack: what was asked, what was done, and who checked it.

This is the artifact you read before merging, and the one you hand to someone who asks why an
agent's change should be trusted. It is written to ~/.drove/runs/<run-id>/ as markdown to read and
JSON for tooling.

Its job is to make the change reviewable without re-deriving anything: the intent, the diff, the
independent verdict, and the real output of the project's own checks — in that order, because that
is the order a reviewer needs them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from drove.config import runs_dir
from drove.pipeline.schemas import PlanDoc, ReviewVerdict
from drove.pipeline.stages.verify import VerifyOutcome

# A diff long enough to scroll for an hour helps nobody; the branch is the source of truth.
MAX_DIFF = 60_000
# Failing output is the whole point of recording it, so it gets far more room than passing output.
MAX_FAIL_OUTPUT = 6_000
MAX_PASS_OUTPUT = 800


@dataclass
class Evidence:
    run_id: str
    feature_id: str
    iteration: int
    intent: str
    branch: str
    base: str
    plan: PlanDoc
    workspace: str = ""
    repos: list[str] = field(default_factory=list)
    head_shas: dict[str, str] = field(default_factory=dict)
    head_sha: str | None = None
    files_changed: list[str] = field(default_factory=list)
    reviews: list[ReviewVerdict] = field(default_factory=list)
    verify: VerifyOutcome | None = None
    browser: object | None = None
    diff: str = ""
    diff_stat: str = ""
    # stage -> {harness, model, lab}
    stages: dict[str, dict[str, str]] = field(default_factory=dict)
    cost_usd: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    sessions: dict[str, str] = field(default_factory=dict)
    status: str = "delivered"

    # --- helpers -------------------------------------------------------------------------

    @property
    def passed_review(self) -> bool:
        return bool(self.reviews) and self.reviews[-1].verdict == "pass"

    @property
    def independent(self) -> bool:
        """Did a different lab's model review the change?"""
        builder = self.stages.get("execute", {})
        reviewer = self.stages.get("review", {})
        if not builder or not reviewer:
            return False
        return builder.get("harness") != reviewer.get("harness")

    def _who(self, stage: str) -> str:
        info = self.stages.get(stage)
        if not info:
            return "an agent"
        bits = [info.get("harness", "?")]
        if lab := info.get("lab"):
            bits.append(f"({lab}{', ' + info['model'] if info.get('model') else ''})")
        elif info.get("model"):
            bits.append(f"({info['model']})")
        return " ".join(bits)

    # --- output --------------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "feature_id": self.feature_id,
            "iteration": self.iteration,
            "intent": self.intent,
            "branch": self.branch,
            "base": self.base,
            "workspace": self.workspace,
            "repos": self.repos,
            "status": self.status,
            "head_sha": self.head_sha,
            "head_shas": self.head_shas,
            "files_changed": self.files_changed,
            "plan": self.plan.model_dump(),
            "stages": self.stages,
            "independent_review": self.independent,
            "reviews": [r.model_dump() for r in self.reviews],
            "verify": [asdict(c) for c in (self.verify.checks if self.verify else [])],
            "browser": [asdict(c) for c in getattr(self.browser, "checks", [])],
            "diff_stat": self.diff_stat,
            "cost_usd": self.cost_usd,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "sessions": self.sessions,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    def to_markdown(self) -> str:
        out: list[str] = []
        add = out.append

        add(f"# {self.plan.title or self.intent}")
        add("")
        add(
            f"`{self.branch}` → `{self.base}` · {self.workspace or 'workspace'} · "
            f"run {self.iteration} · **{self.status.replace('_', ' ')}**"
        )
        add("")

        self._verdict(add)
        self._review(add)
        self._asked(add)
        self._changed(add)
        self._tests(add)
        self._browser(add)
        self._cost(add)
        self._sessions(add)
        return "\n".join(out)

    # Each section below answers one question a reviewer has, in the order they have them.

    def _verdict(self, add) -> None:
        """The single paragraph someone reads if they read nothing else."""
        built = self._who("execute")
        reviewed = self._who("review")

        parts = [f"{built} implemented this on `{self.branch}`."]
        if self.reviews:
            outcome = "passed it" if self.passed_review else "still blocked it"
            rounds = f" after {len(self.reviews)} rounds" if len(self.reviews) > 1 else ""
            parts.append(f"{reviewed} reviewed the diff and {outcome}{rounds}.")
        else:
            parts.append("It was not reviewed.")

        if self.verify and self.verify.ran:
            failed = self.verify.failures
            parts.append(
                f"The project's own checks failed ({', '.join(c.name for c in failed)})."
                if failed
                else f"All {len(self.verify.checks)} project checks passed."
            )
        else:
            parts.append("No project checks are configured, so nothing was run.")

        add("## In short")
        add("")
        add(" ".join(parts))
        add("")

    def _review(self, add) -> None:
        add("## Independent review")
        add("")
        if not self.reviews:
            add("_This change was not reviewed._")
            add("")
            return

        if self.independent:
            add(
                f"{self._who('execute')} wrote the change. {self._who('review')} judged it — a "
                "different tool from a different lab, given only the approved intent and the "
                "diff, never the implementer's reasoning, in a fresh session each round."
            )
        else:
            add(
                f"⚠️ The same harness (`{self.stages.get('execute', {}).get('harness', '?')}`) "
                "both wrote and reviewed this change. A model that has just argued for an "
                "approach tends to accept it, so treat this verdict as weaker than an "
                "independent one."
            )
        add("")

        for i, review in enumerate(self.reviews, 1):
            mark = "**passed**" if review.verdict == "pass" else "**changes requested**"
            add(f"**Round {i}** — {mark}")
            add("")
            if review.summary:
                add(f"> {review.summary}")
                add("")
            for issue in review.blocking:
                where = issue.file + (f":{issue.line}" if issue.line else "")
                add(f"- `{where}` **{issue.severity}** — {issue.why}")
            if review.blocking:
                add("")

        if len(self.reviews) > 1 and self.passed_review:
            add(
                f"_The issues raised in round {len(self.reviews) - 1} were addressed and a fresh "
                "reviewer passed the result._"
            )
            add("")

    def _asked(self, add) -> None:
        add("## What was asked")
        add("")
        add(f"> {self.intent}")
        add("")
        if self.plan.summary:
            add(self.plan.summary)
            add("")
        if self.plan.acceptance_criteria:
            add("**Done when**")
            add("")
            for c in self.plan.acceptance_criteria:
                add(f"- {c}")
            add("")
        if self.plan.risks:
            add("**Risks and assumptions the planner flagged**")
            add("")
            for r in self.plan.risks:
                add(f"- {r}")
            add("")

    def _changed(self, add) -> None:
        add("## What changed")
        add("")
        if len(self.head_shas) > 1:
            add(
                f"**{len(self.head_shas)} repositories — `{self.branch}` must be merged in all of "
                "them.** Landing one without the others breaks the build."
            )
            add("")
            for repo, sha in self.head_shas.items():
                add(f"- `{repo}` → `{sha[:12]}`")
        elif self.head_sha:
            add(f"Commit `{self.head_sha[:12]}`")
        add("")

        if self.diff_stat:
            add("```")
            add(self.diff_stat.strip())
            add("```")
            add("")
        elif self.files_changed:
            for path in self.files_changed:
                add(f"- `{path}`")
            add("")

        if self.diff:
            body = self.diff
            truncated = len(body) > MAX_DIFF
            if truncated:
                body = body[:MAX_DIFF]
            add("<details><summary>Full diff</summary>")
            add("")
            add("```diff")
            add(body.rstrip())
            if truncated:
                add(f"… truncated. Read it all with: git diff {self.base}...{self.branch}")
            add("```")
            add("")
            add("</details>")
            add("")

    def _tests(self, add) -> None:
        add("## Tests and checks")
        add("")
        if self.verify is None or not self.verify.ran:
            add("_No verify commands are configured in `.drove.toml`, so nothing was run._")
            add("")
            add(
                "Without them a run is delivered on a reviewer's opinion alone. Add a `[verify]` "
                "section naming the commands you would run yourself."
            )
            add("")
            return

        for check in self.verify.checks:
            label = f"{check.repo}/{check.name}" if check.repo else check.name
            mark = "passed" if check.ok else f"**FAILED** (exit {check.exit_code})"
            add(f"**{label}** — `{check.command}` — {mark} in {check.duration_s}s")
            add("")
            limit = MAX_PASS_OUTPUT if check.ok else MAX_FAIL_OUTPUT
            body = (check.output or "").strip()
            add("```")
            add(body[-limit:] if body else "(no output)")
            add("```")
            add("")

    def _browser(self, add) -> None:
        if self.browser is None:
            return
        add("## In a browser")
        add("")
        if skipped := getattr(self.browser, "skipped", ""):
            add(f"_Skipped: {skipped}_")
            add("")
            return
        for check in getattr(self.browser, "checks", []):
            mark = "rendered cleanly" if check.ok else "**has problems**"
            add(f"**{check.path}** — {mark} — {check.title or 'no title'}")
            for err in check.console_errors:
                add(f"- console error: `{err}`")
            for req in check.failed_requests:
                add(f"- failed request: `{req}`")
            if check.error:
                add(f"- {check.error}")
            if check.screenshot:
                add("")
                add(f"![{check.path}](screens/{check.screenshot})")
            add("")

    def _cost(self, add) -> None:
        add("## Cost")
        add("")
        dollars = f"${self.cost_usd:.4f}" if self.cost_usd is not None else "—"
        add(f"{self.tokens_in:,} tokens in · {self.tokens_out:,} out · {dollars}")
        if self.cost_usd is None:
            add("")
            add(
                "_No dollar figure: the harnesses used report tokens only, or run on a "
                "subscription where the marginal cost of a turn is genuinely zero._"
            )
        add("")

    def _sessions(self, add) -> None:
        if not self.sessions:
            return
        add("## Reopen any of this")
        add("")
        add("Each of these is a real conversation you can continue:")
        add("")
        for stage, sid in self.sessions.items():
            add(f"- **{stage}** `{sid}`")
        add("")


def stage_attribution(workspace) -> dict[str, dict[str, str]]:
    """Who ran each stage, on what model, from which lab."""
    from drove.harness import registry

    out: dict[str, dict[str, str]] = {}
    for stage, harness in workspace.harness.items():
        out[stage] = {
            "harness": harness,
            "model": workspace.model_for(stage) or "",
            "lab": registry.LAB.get(harness, ""),
        }
    return out


def write(evidence: Evidence) -> Path:
    directory = runs_dir(evidence.run_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "evidence.json").write_text(json.dumps(evidence.to_json(), indent=2))
    path = directory / "evidence.md"
    path.write_text(evidence.to_markdown())
    return path
