"""Read-only connector over a local git repository (a self-hosted code source), same interface as GitHubConnector
for list_commits / get_commit / get_file.

Used in live mode ONLY when configured explicitly (env BLACKBOX_GIT_REPO_DIR); it is not a fallback for GitHub.
Every record says `backend: local-git` and names the repository path. Only read commands are run
(`git log`, `git show`, `git cat-file`, `git rev-parse`), with a timeout and bounded output.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from blackbox.schemas import BackendResult, EvidenceRecord

GET_CHARS = 6000
LIST_CHARS = 600
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_SEP = "\x1f"


def _attributes_file() -> str:
    import tempfile
    p = Path(tempfile.gettempdir()) / "blackbox_git_attributes"
    if not p.exists():
        p.write_text("*.py diff=python" + chr(10), encoding="utf-8")
    return str(p)


def _z(s: str) -> str:
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(s.strip()).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class LocalGitConnector:
    backend = "local-git"

    def __init__(self, repo_dir: Optional[str] = None, timeout: float = 10.0):
        self.dir = Path(repo_dir or os.environ.get("BLACKBOX_GIT_REPO_DIR", ""))
        self.repo = f"local:{self.dir.name}" if str(self.dir) else "local:?"
        self.timeout = timeout
        self.detail = f"local git repository {self.dir}"

    def ready(self) -> tuple[bool, str]:
        if not (self.dir / ".git").exists():
            return False, f"not a git repository: {self.dir}"
        return True, "ok"

    def _git(self, *args: str) -> str:
        r = subprocess.run(["git", "-C", str(self.dir), *args], capture_output=True, text=True, timeout=self.timeout,
                           encoding="utf-8", errors="replace")
        if r.returncode:
            raise LookupError(r.stderr.strip()[:300] or f"git {args[0]} failed")
        return r.stdout

    def _services(self, sha: str) -> str:
        """Services a commit touches, from its changed paths: services/<name>/ -> <name>-svc, config/<svc>.yaml -> <svc>,
        libs/ (shared code) -> no service (the deployment records say which service received it). Empty when nothing maps."""
        try:
            paths = self._git("show", "--format=", "--name-only", sha).split()
        except (LookupError, subprocess.TimeoutExpired, OSError):
            return ""
        out: list[str] = []
        for p in paths:
            parts = p.split("/")
            if parts[0] == "services" and len(parts) > 2:
                out.append(f"{parts[1]}-svc")
            elif parts[0] == "config" and parts[-1].endswith(".yaml"):
                out.append(parts[-1][:-5])
            # libs/ (shared code) names no service: a shared change reaches a service only through a deployment,
            # and the deployment record says which service that was
        return ",".join(dict.fromkeys(out))

    def _attrs(self, sha: str, query: str, **kw) -> dict:
        return {"backend": self.backend, "mode": "live", "query": query, "source_url": f"{self.dir}@{sha[:7]}",
                "sha": sha, **kw}

    def list_commits(self, since=None, until=None, path=None, sha=None, limit=10) -> BackendResult:
        ok, why = self.ready()
        if not ok:
            return BackendResult(status="unavailable", error=why)
        limit = max(1, min(int(limit or 10), 100))
        args = ["log", f"-n{limit}", f"--format=%H{_SEP}%aI{_SEP}%cI{_SEP}%an{_SEP}%s"]
        if since:
            args.append(f"--since={since}")
        if until:
            args.append(f"--until={until}")
        args.append(sha or "HEAD")
        if path:
            args += ["--", path]
        try:
            out = self._git(*args)
        except (LookupError, subprocess.TimeoutExpired) as exc:
            return BackendResult(status="empty" if "unknown revision" in str(exc) else "error", error=str(exc)[:200])
        recs = []
        for line in out.splitlines():
            h, ad, cd, an, subj = line.split(_SEP, 4)
            recs.append(EvidenceRecord(
                evidence_id=f"commit:{h[:7]}", source="commits", service=self._services(h) or None,
                event_time=_z(ad), available_time=_z(cd), version=h[:7],
                title=subj[:160], content=f"commit {h[:7]} {_z(cd)} {an}\n{subj}"[:LIST_CHARS], fixture=False,
                content_ref=f"{self.dir}@{h[:7]}", attributes=self._attrs(h, " ".join(args), author=an)))
        if not recs:
            return BackendResult(status="empty", total_matches=0)
        return BackendResult(status="ok", records=recs, total_matches=len(recs))

    def get_commit(self, sha: str) -> BackendResult:
        s = str(sha or "").split("@")[-1].split(":")[-1].strip()
        if not _SHA.match(s):
            return BackendResult(status="error", error=f"not a commit sha: {s[:60]!r}")
        try:
            meta = self._git("show", "-s", f"--format=%H{_SEP}%aI{_SEP}%cI{_SEP}%an{_SEP}%B", s)
            files = self._git("show", "--format=", "--name-status", s).strip()
            # whole enclosing functions around each change (git's built-in Python function detection), so a reader
            # sees e.g. the release calls that a new early return skips; plain -U3 hunks would hide them
            diff = self._git("-c", f"core.attributesFile={_attributes_file()}", "show", "--format=", "--patch",
                             "--function-context", s)
        except (LookupError, subprocess.TimeoutExpired) as exc:
            return BackendResult(status="empty", total_matches=0, error=f"commit {s} not in {self.dir}: {str(exc)[:120]}")
        h, ad, cd, an, body = meta.split(_SEP, 4)
        text = f"commit {h[:7]} {_z(cd)} {an}\n{body.strip()}\nfiles: {files.replace(chr(9), ' ')}\n\n{diff}"
        return BackendResult(status="ok", total_matches=1, records=[EvidenceRecord(
            evidence_id=f"commit:{h[:7]}", source="commits", service=self._services(h) or None,
            event_time=_z(ad), available_time=_z(cd), version=h[:7],
            title=(body.strip().splitlines() or [""])[0][:160], content=text[:GET_CHARS], truncated=len(text) > GET_CHARS,
            content_ref=f"{self.dir}@{h[:7]}", fixture=False,
            attributes=self._attrs(h, f"git show {h[:7]}", author=an,
                                   **({"full_text": text[:40000]} if len(text) > GET_CHARS else {})))])

    def get_file(self, path: str, ref: str) -> BackendResult:
        p = str(path or "").strip().lstrip("/")
        if not p or ".." in p.split("/") or "\\" in p:
            return BackendResult(status="error", error=f"invalid repository path {p[:80]!r}")
        r = str(ref or "").strip()
        if not _SHA.match(r):
            return BackendResult(status="error", error="ref must be a commit SHA")
        try:
            full = self._git("rev-parse", f"{r}^{{commit}}").strip()
            body = self._git("show", f"{full}:{p}")
            cd = self._git("show", "-s", "--format=%cI", full).strip()
        except (LookupError, subprocess.TimeoutExpired) as exc:
            return BackendResult(status="empty", total_matches=0, error=f"{p} @ {r} not in {self.dir}: {str(exc)[:120]}")
        text = f"{p} @ {full[:7]}\n{body}"
        return BackendResult(status="ok", total_matches=1, records=[EvidenceRecord(
            evidence_id=f"file:{full[:7]}:{p}", source="configs" if p.startswith("config/") else "commits",
            event_time=_z(cd), available_time=_z(cd), version=full[:7], title=f"{p} @ {full[:7]}",
            content=text[:GET_CHARS], truncated=len(text) > GET_CHARS, content_ref=f"{self.dir}@{full[:7]}:{p}",
            fixture=False, attributes=self._attrs(full, f"git show {full[:7]}:{p}", path=p))])
