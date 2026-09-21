Implement the approved plan below. A human reviewed and approved it — it is the agreed scope, so
do not redesign it. If part of it turns out to be wrong or impossible, implement everything else
and say clearly in your final message what you could not do and why.

The plan is also written to `.drove/plan.md` here; re-read it whenever you need to.

WORKING DIRECTORY
You are at {root}. It contains one checked-out repository per directory:

{repos}

Each is an isolated git worktree on branch `{branch}`. Change whichever repos the plan requires —
a change that alters an interface in one repo and its callers in another belongs in one run.

Repository conventions apply: match the surrounding code's style, reuse what already exists rather
than adding new abstractions, and do not add dependencies that are not already present.

Start with the files named in the approved plan. Read direct callers or dependencies only when they
are needed to make the requested change correct; do not inventory or refactor unrelated parts of
the repository. Run the plan's targeted checks before expanding the investigation.

PLAN
{plan}

Rules:
- Work only inside {root}. Do not touch anything outside it.
- Do NOT run `git commit`, `git push`, `git checkout` or `git merge`. Drove commits your work.
- Do not modify `.drove/`.
- When you are done, state briefly what you changed in each repo and what a reviewer should look
  at first.
