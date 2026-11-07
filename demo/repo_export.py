"""Export commit history and config versions from the company repo.

`LocalGitRepo` is the only place that talks to git. A GitHub-backed implementation
(REST: /repos/{o}/{r}/commits, /commits/{sha}, /contents/{path}?ref=) can provide the
same three methods, and the snapshot builder will not notice the difference.
"""
from __future__ import annotations

from typing import Protocol

from demo.paths import WORKSPACE
from demo.workspace import git

CONFIG_PATHS = {"checkout-svc": "config/checkout-svc.yaml", "payment-svc": "config/payment-svc.yaml"}
SEP = "\x1e"


class CommitSource(Protocol):
    def list_commits(self) -> list[dict]: ...
    def diff(self, sha: str) -> str: ...
    def file_at(self, sha: str, path: str) -> str | None: ...


class LocalGitRepo:
    def __init__(self, path=WORKSPACE):
        self.path = path

    def list_commits(self) -> list[dict]:
        fmt = SEP.join(["%H", "%P", "%an", "%ae", "%aI", "%cI", "%B"]) + "\x1f"
        out = git("log", "--reverse", f"--format={fmt}", cwd=self.path)
        commits = []
        for chunk in out.split("\x1f"):
            chunk = chunk.strip("\n")
            if not chunk:
                continue
            sha, parents, an, ae, ad, cd, body = chunk.split(SEP)
            files = []
            ns = git("show", "--format=", "--name-status", sha, cwd=self.path)
            for line in ns.splitlines():
                if line.strip():
                    st, *paths = line.split("\t")
                    files.append({"status": st, "path": paths[-1]})
            commits.append({"sha": sha, "parents": parents.split(), "author": an, "author_email": ae,
                            "author_date": ad, "committer_date": cd, "message": body.strip(),
                            "files": files})
        return commits

    def diff(self, sha: str) -> str:
        return git("show", "--format=", "--patch", "--no-color", sha, cwd=self.path)

    def file_at(self, sha: str, path: str) -> str | None:
        try:
            return git("show", f"{sha}:{path}", cwd=self.path)
        except RuntimeError:
            return None


def export(repo: CommitSource | None = None) -> dict:
    repo = repo or LocalGitRepo()
    commits = repo.list_commits()
    for c in commits:
        c["diff"] = repo.diff(c["sha"])
    configs = []
    for svc, path in CONFIG_PATHS.items():
        prev = None
        for c in commits:
            if not any(f["path"] == path for f in c["files"]):
                continue
            text = repo.file_at(c["sha"], path)
            if text is None or text == prev:
                continue
            configs.append({"service": svc, "path": path, "commit": c["sha"],
                            "committed_at": c["committer_date"], "content": text, "previous": prev})
            prev = text
    return {"commits": commits, "configs": configs}
