"""The evidence pack: what was promised, what was delivered, and what actually ran.

Written next to the run's raw JSONL under ~/.vorflux/runs/<run-id>/. Both a human-readable
markdown file and a machine-readable JSON one, because the first is what you read before merging
and the second is what the UI and any later tooling consume.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vorflux.config import runs_dir
from vorflux.pipeline.schemas import PlanDoc, ReviewVerdict
from vorflux.pipeline.stages.verify import VerifyOutcome


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
    cost_usd: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    sessions: dict[str, str] = field(default_factory=dict)
    status: str = "delivered"

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
            "reviews": [r.model_dump() for r in self.reviews],
            "verify": [asdict(c) for c in (self.verify.checks if self.verify else [])],
            "cost_usd": self.cost_usd,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "sessions": self.sessions,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    def to_markdown(self) -> str:
        lines = [
            f"# {self.intent}",
            "",
            f"`{self.branch}` → `{self.base}` · run {self.run_id} · iteration {self.iteration}"
            f" · **{self.status}**",
            "",
            "## Plan",
            "",
            self.plan.summary,
            "",
        ]
        if self.plan.acceptance_criteria:
            lines.append("**Acceptance criteria**")
            lines += [f"- {c}" for c in self.plan.acceptance_criteria]
            lines.append("")

        lines += ["## Delivered", ""]
        if len(self.head_shas) > 1:
            # Spelled out deliberately: these branches are one change and have to be merged
            # together. Landing one without the others is a broken deploy.
            lines.append(
                f"**{len(self.head_shas)} repositories — merge `{self.branch}` in all of them:**"
            )
            for repo, sha in self.head_shas.items():
                lines.append(f"- `{repo}` → commit `{sha[:12]}`")
        else:
            lines.append(f"- commit `{(self.head_sha or '—')[:12]}`")
        lines.append("")
        for path in self.files_changed:
            lines.append(f"- `{path}`")
        lines.append("")

        lines += ["## Review", ""]
        if not self.reviews:
            lines.append("_not reviewed_")
        for i, review in enumerate(self.reviews, 1):
            mark = "PASS" if review.verdict == "pass" else "CHANGES REQUESTED"
            lines.append(f"**Round {i} — {mark}** ({review.summary})")
            for issue in review.blocking:
                where = issue.file + (f":{issue.line}" if issue.line else "")
                lines.append(f"- `{where}` [{issue.severity}] {issue.why}")
            lines.append("")

        lines += ["## Verification", ""]
        if self.verify is None or not self.verify.ran:
            lines.append("_no verify commands configured in `.vorflux.toml`_")
        else:
            for check in self.verify.checks:
                mark = "pass" if check.ok else f"FAIL (exit {check.exit_code})"
                label = f"{check.repo}/{check.name}" if check.repo else check.name
                lines.append(f"**{label}** — `{check.command}` — {mark} ({check.duration_s}s)")
                lines.append("")
                lines.append("```")
                lines.append(check.output.strip()[-2000:] or "(no output)")
                lines.append("```")
                lines.append("")

        lines += ["## Cost", ""]
        dollars = f"${self.cost_usd:.4f}" if self.cost_usd is not None else "—"
        lines.append(f"{self.tokens_in:,} in / {self.tokens_out:,} out · {dollars}")
        lines.append("")

        if self.sessions:
            lines += ["## Sessions", ""]
            lines.append("Reopen any of these to see exactly what the agent did:")
            lines.append("")
            for stage, sid in self.sessions.items():
                lines.append(f"- **{stage}** `{sid}`")
            lines.append("")

        return "\n".join(lines)


def write(evidence: Evidence) -> Path:
    directory = runs_dir(evidence.run_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "evidence.json").write_text(json.dumps(evidence.to_json(), indent=2))
    path = directory / "evidence.md"
    path.write_text(evidence.to_markdown())
    return path
