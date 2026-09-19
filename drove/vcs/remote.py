"""Publishing a feature branch, and pointing at it on the web.

A branch that only exists on this laptop is not delivered in any useful sense — you cannot open it
on your phone, send it to anyone, or let CI see it. Pushing is therefore part of delivery, not a
separate manual step.

It is deliberately forgiving. A push can fail for reasons that say nothing about the quality of the
work — no remote, no network, no credentials, a protected branch — and none of them should turn a
reviewed, verified run into a failure. Every function here reports what happened instead of
raising, and the caller records it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from drove.vcs import git as git_mod
from drove.vcs.git import git
from drove.vcs.tree import Tree

# Hosts whose compare URLs we know how to build. Anything else still pushes; it just gets the
# remote address rather than a link, which is honest about what we can and cannot construct.
PROVIDERS = {
    "github.com": "{base_url}/compare/{base}...{branch}?expand=1",
    "gitlab.com": "{base_url}/-/compare/{base}...{branch}",
    "bitbucket.org": "{base_url}/branch/{branch}",
}


@dataclass(frozen=True)
class Push:
    """What became of one repo's branch."""

    repo: str
    branch: str
    pushed: bool
    remote: str | None = None      # the configured URL, e.g. git@github.com:me/x.git
    url: str | None = None         # a web link a person can open, when we can build one
    skipped: str | None = None     # why nothing was attempted
    error: str | None = None       # why an attempt failed

    @property
    def note(self) -> str:
        if self.pushed:
            return self.url or f"pushed to {self.remote}"
        return self.skipped or self.error or "not pushed"


def _ssh_host(alias: str) -> str:
    """Resolve an SSH config alias to the real hostname.

    People with more than one account routinely use aliases — `git@github-personal:me/x.git` is a
    different key, not a different site. Taking the alias literally would either build a URL to a
    host that does not exist or refuse to build one at all, so read what ssh would read.
    """
    config = Path.home() / ".ssh" / "config"
    if not config.exists():
        return alias
    try:
        text = config.read_text()
    except OSError:
        return alias
    current: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        key = key.lower().rstrip("=").strip()
        value = value.strip().lstrip("=").strip()
        if key == "host":
            current = value.split()
        elif key == "hostname" and alias in current:
            return value
    return alias


def _parse(remote: str) -> tuple[str, str] | None:
    """(host, owner/repo) from a git remote, or None when it is not a shape we recognise."""
    remote = remote.strip()
    # Scheme first. A scp-style pattern happily reads `https://github.com/…` as host `https`,
    # because a colon is all it looks for — so decide which shape this is before matching.
    if "://" in remote:
        url = re.match(
            r"^(?:https?|git|ssh)://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+?)(?:\.git)?/?$", remote
        )
        if url is None:
            return None
        host, path = url.group(1), url.group(2)
    elif scp := re.match(r"^(?:[^@/]+@)?([^/:]+):(.+?)(?:\.git)?/?$", remote):
        host, path = scp.group(1), scp.group(2)
    else:
        return None
    if not path or path.count("/") < 1:
        return None
    return _ssh_host(host), path


def web_url(remote: str, base: str, branch: str) -> str | None:
    """A page a person can open to see the branch against its base."""
    parsed = _parse(remote)
    if parsed is None:
        return None
    host, path = parsed
    template = PROVIDERS.get(host)
    if template is None:
        return None
    return template.format(base_url=f"https://{host}/{path}", base=base, branch=branch)


def remote_url(repo: Path, name: str = "origin") -> str | None:
    url = git(repo, "remote", "get-url", name, check=False)
    return url or None


# Words that mark git's actual complaint, as opposed to the advice that follows it.
_CAUSE = ("denied", "fatal", "error", "rejected", "not found", "timed out", "could not")


def _why(proc) -> str:
    """The line worth showing out of a failed push.

    git reports a short cause and then several lines of generic advice — "Please make sure you
    have the correct access rights and the repository exists." Taking the last line gets the end
    of that advice and tells you nothing; the cause comes first.
    """
    text = (proc.stderr or proc.stdout or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if any(word in line.lower() for word in _CAUSE):
            return line[:300]
    return lines[0][:300] if lines else f"git push exited {proc.returncode}"


def push(tree: Tree, remote: str = "origin") -> Push:
    """Publish one repo's feature branch, and say where it landed.

    `--force-with-lease` rather than `--force`: a feature branch is ours to rewrite across fix
    rounds and pivots, but not if someone else has pushed to it in the meantime — that is a person
    working on our branch, and overwriting them silently is the one outcome worth refusing.
    """
    url = remote_url(tree.path, remote)
    if url is None:
        return Push(
            repo=tree.repo.name, branch=tree.branch, pushed=False,
            skipped=f"no '{remote}' remote configured",
        )

    proc = git_mod.run(
        tree.path, "push", "--force-with-lease", "--set-upstream", remote, tree.branch
    )
    if proc.returncode != 0:
        return Push(
            repo=tree.repo.name, branch=tree.branch, pushed=False, remote=url,
            error=_why(proc),
        )

    return Push(
        repo=tree.repo.name, branch=tree.branch, pushed=True, remote=url,
        url=web_url(url, tree.base, tree.branch),
    )
