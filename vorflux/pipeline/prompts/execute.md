Implement the approved plan below. It has been reviewed by a human and is the agreed scope — do
not redesign it. If something in it turns out to be wrong or impossible, implement everything else
and say clearly in your final message what you could not do and why.

The full plan is also written to `.vorflux/plan.md` in this working directory; re-read it whenever
you need to.

Repository conventions apply: match the surrounding code's style, reuse what already exists rather
than adding new abstractions, and do not add dependencies that are not already present.

PLAN
{plan}

Rules:
- Work only inside this directory. It is an isolated git worktree on branch `{branch}`.
- Do NOT run `git commit`, `git push`, `git checkout` or `git merge`. Vorflux commits your work.
- Do not modify `.vorflux/`.
- When you are done, state briefly what you changed and anything a reviewer should look at first.
