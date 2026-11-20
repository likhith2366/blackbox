"""Read-only GitHub REST connector (CONTRACT.md s10c). Owner: QA.

    python -m blackbox.connectors.github_api smoke --repo owner/name [--sha SHA] [--file PATH] [--issue N]

Only HTTP GET is ever issued. Every method returns a BackendResult and never raises.

Status mapping
  ok / empty    answered (empty: nothing matched, e.g. empty repository, unknown sha)
  unavailable   401 / 403 / 404 (no credentials, no access, repo or object absent)
  error         network failure, timeout, HTTP 5xx, rate limit exhausted (reset time in the
                message), oversized response, invalid arguments, mutable ref refused

Credentials: env GITHUB_TOKEN, else `gh auth token` read into memory, else unauthenticated
(public repositories only, 60 requests/hour). The token is never printed, logged, stored in a
record or returned in an error: every error text passes through _scrub().

Evidence ids: gh:repo:<repo>, gh:commit:<repo>@<sha7>, gh:file:<repo>@<sha7>:<path>,
gh:issue:<repo>#<n>, gh:issue:<repo>#<n>#c<k> (k = 1-based comment position).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from blackbox.connectors.capture import ReplayMiss
from blackbox.schemas import BackendResult, EvidenceRecord

API = "https://api.github.com"
USER_AGENT = "blackbox-readonly-connector"
SEARCH_CHARS = 600
GET_CHARS = 4000
PATCH_CHARS = 1500
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TOKEN_PATTERNS = re.compile(r"(gh[opsur]_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,})")


def _now() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _z(ts: Optional[str]) -> Optional[str]:
    """GitHub timestamps ('2026-11-19T10:00:00Z' or with offset) -> ISO-8601 UTC with Z."""
    if not ts:
        return None
    s = ts.strip()
    try:
        dt = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _after(a: Optional[str], b: Optional[str]) -> bool:
    za, zb = _z(a), _z(b)
    return bool(za and zb and za > zb)


def _clip(text: str, n: int) -> tuple[str, bool]:
    if len(text) <= n:
        return text, False
    return text[: n - 15].rstrip() + " ...[truncated]", True


def _gh_cli_token() -> Optional[str]:
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    tok = (r.stdout or "").strip()
    return tok if r.returncode == 0 and tok else None


class _Fail(Exception):
    def __init__(self, status: str, message: str, http_status: Optional[int] = None):
        super().__init__(message)
        self.status, self.message, self.http_status = status, message, http_status


class GitHubConnector:
    """Read-only access to one GitHub repository."""

    def __init__(self, repo: str, timeout_s: float = 10, max_bytes: int = 200_000, max_pages: int = 2,
                 token: Optional[str] = None, use_gh_cli: bool = True, session: Optional[requests.Session] = None):
        self.repo = repo.strip()
        self.timeout_s, self.max_bytes, self.max_pages = timeout_s, max_bytes, max(1, max_pages)
        if token is not None:
            self._token, self.auth_mode = (token or None), ("token (explicit)" if token else "none")
        elif os.environ.get("GITHUB_TOKEN"):
            self._token, self.auth_mode = os.environ["GITHUB_TOKEN"].strip(), "token (env GITHUB_TOKEN)"
        else:
            tok = _gh_cli_token() if use_gh_cli else None
            self._token, self.auth_mode = tok, ("token (gh auth token)" if tok else "none (unauthenticated)")
        self._session = session or requests.Session()
        self.requests_made = 0

    def __repr__(self) -> str:   # never include the token
        return f"GitHubConnector(repo={self.repo!r}, auth={self.auth_mode!r})"

    # ------------------------------------------------------------------ plumbing
    def _scrub(self, text: str) -> str:
        text = str(text)
        if self._token:
            text = text.replace(self._token, "***")
        return _TOKEN_PATTERNS.sub("***", text)

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        h = {"Accept": accept, "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _get(self, path: str, params: Optional[dict] = None, accept: str = "application/vnd.github+json",
             raw: bool = False, tail_bytes: Optional[int] = None) -> tuple[Any, int, requests.structures.CaseInsensitiveDict]:
        """One bounded GET. Returns (parsed json or text, http status, headers) or raises _Fail.

        tail_bytes (logs only): instead of failing on a large body, read at most 10 x max_bytes and keep
        the last tail_bytes; self.last_body_cut says whether anything was dropped."""
        self.last_body_cut = False
        if not _REPO_RE.match(self.repo):
            raise _Fail("error", f"invalid repository name {self.repo!r} (expected owner/name)")
        url = path if path.startswith("http") else f"{API}{path}"
        if not url.startswith(API + "/"):
            raise _Fail("error", "refusing to follow a URL outside api.github.com")
        try:
            self.requests_made += 1
            resp = self._session.get(url, params=params, headers=self._headers(accept), timeout=self.timeout_s,
                                     stream=True, allow_redirects=True)
            body = b""
            read = 0
            for chunk in resp.iter_content(65536):
                body += chunk
                read += len(chunk)
                if tail_bytes is not None:
                    if len(body) > tail_bytes:
                        body, self.last_body_cut = body[-tail_bytes:], True
                    if read > 10 * self.max_bytes:
                        self.last_body_cut = True
                        resp.close()
                        break
                elif len(body) > self.max_bytes:
                    resp.close()
                    raise _Fail("error", f"response exceeded max_bytes={self.max_bytes}", resp.status_code)
        except _Fail:
            raise
        except ReplayMiss as exc:
            raise _Fail("error", self._scrub(str(exc))[:300])
        except requests.Timeout:
            raise _Fail("error", f"GitHub request timed out after {self.timeout_s:g}s")
        except requests.RequestException as exc:
            raise _Fail("error", self._scrub(f"network error: {type(exc).__name__}: {exc}")[:300])
        code = resp.status_code
        if code >= 400:
            msg = ""
            try:
                msg = (json.loads(body.decode("utf-8", "replace")) or {}).get("message", "")
            except Exception:
                msg = body.decode("utf-8", "replace")[:200]
            msg = self._scrub(msg)[:200]
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if code == 429 or (code == 403 and (remaining == "0" or "rate limit" in msg.lower())):
                reset = resp.headers.get("X-RateLimit-Reset")
                when = (datetime.fromtimestamp(int(reset), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        if reset and reset.isdigit() else "unknown")
                raise _Fail("error", f"GitHub rate limit exhausted (auth: {self.auth_mode}); resets at {when}", code)
            if code in (401, 403, 404, 410):
                raise _Fail("unavailable", f"HTTP {code}: {msg or 'no access'} (auth: {self.auth_mode})", code)
            if code in (409, 422):        # empty repository / no commit for sha: answered, nothing there
                raise _Fail("empty", f"HTTP {code}: {msg}", code)
            if code >= 500:
                raise _Fail("error", f"GitHub HTTP {code}: {msg}", code)
            raise _Fail("error", f"HTTP {code}: {msg}", code)
        text = body.decode("utf-8", "replace")
        if raw:
            return text, code, resp.headers
        try:
            return json.loads(text), code, resp.headers
        except ValueError:
            raise _Fail("error", "GitHub returned a non-JSON body", code)

    def _attrs(self, url: str, http_status: int, **extra: Any) -> dict[str, Any]:
        return {"live": True, "retrieved_at": _now(), "repo": self.repo, "url": url,
                "http_status": http_status, **extra}

    def _wrap(self, fn, *args, **kw) -> BackendResult:
        try:
            return fn(*args, **kw)
        except _Fail as f:
            return BackendResult(status=f.status, error=self._scrub(f.message))
        except Exception as exc:   # never raise to the caller
            return BackendResult(status="error", error=self._scrub(f"{type(exc).__name__}: {exc}")[:300])

    @staticmethod
    def _ok(records: list[EvidenceRecord], total: Optional[int] = None, truncated: bool = False) -> BackendResult:
        if not records:
            return BackendResult(status="empty", total_matches=total or 0)
        return BackendResult(status="ok", records=records, total_matches=total if total is not None else len(records),
                             truncated=truncated)

    # ------------------------------------------------------------------ repo
    def repo_metadata(self) -> BackendResult:
        return self._wrap(self._repo_metadata)

    def _repo_metadata(self) -> BackendResult:
        d, code, _ = self._get(f"/repos/{self.repo}")
        empty = not d.get("size") and not d.get("pushed_at")
        text = (f"repository {d.get('full_name')} ({'private' if d.get('private') else 'public'}) "
                f"default_branch={d.get('default_branch')} size_kb={d.get('size')} created={_z(d.get('created_at'))} "
                f"pushed={_z(d.get('pushed_at'))} archived={d.get('archived')}"
                + (" (repository has no pushed content)" if empty or d.get("size") == 0 else "")
                + (f"\n{d.get('description')}" if d.get("description") else ""))
        rec = EvidenceRecord(
            evidence_id=f"gh:repo:{self.repo}", source="commits", event_time=_z(d.get("created_at")),
            available_time=_z(d.get("pushed_at") or d.get("updated_at")), title=f"GitHub repository {self.repo}",
            content=text, content_ref=d.get("html_url"),
            attributes=self._attrs(d.get("url", ""), code, default_branch=d.get("default_branch"),
                                   size_kb=d.get("size"), private=d.get("private")))
        return self._ok([rec])

    # ------------------------------------------------------------------ commits
    def _commit_record(self, c: dict, code: int, full: bool) -> EvidenceRecord:
        sha = c.get("sha", "")
        info = c.get("commit") or {}
        author, committer = info.get("author") or {}, info.get("committer") or {}
        msg = info.get("message", "")
        files = c.get("files") or []
        head = (f"commit {sha[:7]} {_z(committer.get('date'))} {author.get('name', '')}\n{msg}"
                + (("\nfiles: " + ", ".join(f"{f.get('status')} {f.get('filename')}" for f in files)) if files else ""))
        cut = False
        if full and files:
            parts = []
            for f in files:
                patch = f.get("patch")
                if patch is None:
                    parts.append(f"--- {f.get('filename')} (no textual patch)")
                    continue
                p, c1 = _clip(patch, PATCH_CHARS)
                cut = cut or c1
                parts.append(f"--- {f.get('filename')}\n{p}")
            head += "\n\n" + "\n".join(parts)
        text, c2 = _clip(head, GET_CHARS if full else SEARCH_CHARS)
        return EvidenceRecord(
            evidence_id=f"gh:commit:{self.repo}@{sha[:7]}", source="commits",
            event_time=_z(author.get("date")), available_time=_z(committer.get("date")), version=sha[:7],
            title=(msg.splitlines() or [""])[0][:160], content=text, truncated=cut or c2,
            content_ref=c.get("html_url"),
            attributes=self._attrs(c.get("url", ""), code, sha=sha, author=author.get("name"),
                                   files=[f.get("filename") for f in files][:50],
                                   files_total=len(files) if files else None))

    def list_commits(self, since: Optional[str] = None, until: Optional[str] = None, path: Optional[str] = None,
                     sha: Optional[str] = None, limit: int = 10) -> BackendResult:
        return self._wrap(self._list_commits, since, until, path, sha, limit)

    def _list_commits(self, since, until, path, sha, limit) -> BackendResult:
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            raise _Fail("error", "limit must be an integer")
        params: dict[str, Any] = {"per_page": min(limit, 100)}
        for k, v in (("since", since), ("until", until), ("path", path), ("sha", sha)):
            if v:
                params[k] = v
        recs, url, pages, more, code = [], f"/repos/{self.repo}/commits", 0, False, 200
        while url and pages < self.max_pages and len(recs) < limit:
            data, code, headers = self._get(url, params if pages == 0 else None)
            pages += 1
            recs += [self._commit_record(c, code, full=False) for c in data]
            m = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", ""))
            url = m.group(1) if m else None
        more = bool(url) or len(recs) > limit
        return self._ok(recs[:limit], truncated=more)

    def get_commit(self, sha: str) -> BackendResult:
        return self._wrap(self._get_commit, sha)

    def _get_commit(self, sha) -> BackendResult:
        s = str(sha or "").split("@")[-1].split(":")[-1].strip()
        if not _SHA_RE.match(s):
            raise _Fail("error", f"not a commit sha: {str(sha)[:60]!r} (7-40 hex characters)")
        d, code, _ = self._get(f"/repos/{self.repo}/commits/{s}")
        return self._ok([self._commit_record(d, code, full=True)])

    # ------------------------------------------------------------------ files
    def get_file(self, path: str, ref: str, allow_mutable_ref: bool = False) -> BackendResult:
        return self._wrap(self._get_file, path, ref, allow_mutable_ref)

    def _get_file(self, path, ref, allow_mutable_ref) -> BackendResult:
        p = str(path or "").strip().lstrip("/")
        if not p or ".." in p.split("/") or "\\" in p:
            raise _Fail("error", f"invalid repository path {str(path)[:80]!r}")
        r = str(ref or "").strip()
        if not r:
            raise _Fail("error", "ref is required (a commit SHA)")
        if not _SHA_RE.match(r) and not allow_mutable_ref:
            raise _Fail("error", f"ref {r[:60]!r} is not a commit SHA; branch and tag names are mutable and are "
                                 "refused (the content could differ from what existed at the incident). "
                                 "Pass a commit SHA, or allow_mutable_ref=True explicitly.")
        # resolve to the full commit sha and its dates (small responses, no patches)
        full_sha, _, _ = self._get(f"/repos/{self.repo}/commits/{r}", accept="application/vnd.github.sha", raw=True)
        full_sha = full_sha.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", full_sha):
            raise _Fail("error", "could not resolve ref to a commit sha")
        gc, _, _ = self._get(f"/repos/{self.repo}/git/commits/{full_sha}")
        d, code, _ = self._get(f"/repos/{self.repo}/contents/{p}", params={"ref": full_sha})
        if isinstance(d, list):
            text = f"directory {p} @ {full_sha[:7]}:\n" + "\n".join(f"{e.get('type')} {e.get('path')}" for e in d)
            html = None
        else:
            if d.get("encoding") == "base64" and d.get("content") is not None:
                try:
                    body = base64.b64decode(d["content"]).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    body = f"(binary file, {d.get('size')} bytes, not shown)"
            else:
                body = f"(content not inlined by the API, {d.get('size')} bytes)"
            text = f"{p} @ {full_sha[:7]}\n{body}"
            html = d.get("html_url")
        text, cut = _clip(text, GET_CHARS)
        committed = _z((gc.get("committer") or {}).get("date"))
        rec = EvidenceRecord(
            evidence_id=f"gh:file:{self.repo}@{full_sha[:7]}:{p}",
            source="configs" if p.startswith("config/") else "commits",
            event_time=_z((gc.get("author") or {}).get("date")), available_time=committed, version=full_sha[:7],
            title=f"{p} @ {full_sha[:7]}", content=text, truncated=cut, content_ref=html,
            attributes=self._attrs(d.get("url", "") if isinstance(d, dict) else "", code, sha=full_sha, path=p,
                                   ref_requested=r, ref_was_mutable=not bool(_SHA_RE.match(r))))
        return self._ok([rec])

    # ------------------------------------------------------------------ issues / PRs
    def get_issue(self, number: int, cutoff: Optional[str] = None) -> BackendResult:
        return self._wrap(self._get_issue, number, cutoff)

    def _get_issue(self, number, cutoff) -> BackendResult:
        try:
            n = int(str(number).lstrip("#"))
        except ValueError:
            raise _Fail("error", f"issue number must be an integer, got {str(number)[:40]!r}")
        if n <= 0:
            raise _Fail("error", "issue number must be positive")
        d, code, _ = self._get(f"/repos/{self.repo}/issues/{n}")
        if cutoff and _after(d.get("created_at"), cutoff):
            return BackendResult(status="empty", total_matches=0,
                                 error=f"issue #{n} was created after the cutoff {cutoff}")
        kind = "pull request" if d.get("pull_request") else "issue"
        hist = "unverified" if cutoff and _after(d.get("updated_at"), cutoff) else ("verified" if cutoff else None)
        labels = [lb.get("name") for lb in d.get("labels") or [] if isinstance(lb, dict)]
        text = (f"{kind} #{n}: {d.get('title')}\nstate: {d.get('state')} author: {(d.get('user') or {}).get('login')} "
                f"created: {_z(d.get('created_at'))} comments: {d.get('comments')}"
                + (f" labels: {', '.join(labels)}" if labels else "") + f"\n{d.get('body') or ''}")
        text, cut = _clip(text, GET_CHARS)
        extra = {"kind": kind, "state": d.get("state"), "updated_at": _z(d.get("updated_at"))}
        if hist:
            extra["historical_availability"] = hist
        recs = [EvidenceRecord(
            evidence_id=f"gh:issue:{self.repo}#{n}", source="tickets", event_time=_z(d.get("created_at")),
            available_time=_z(d.get("created_at")), title=f"{kind} #{n}: {d.get('title')}"[:160], content=text,
            truncated=cut, content_ref=d.get("html_url"), attributes=self._attrs(d.get("url", ""), code, **extra))]
        dropped, comments, url, pages = 0, [], f"/repos/{self.repo}/issues/{n}/comments", 0
        more = False
        if d.get("comments"):
            while url and pages < self.max_pages:
                data, ccode, headers = self._get(url, {"per_page": 30} if pages == 0 else None)
                pages += 1
                comments += [(c, ccode) for c in data]
                m = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", ""))
                url = m.group(1) if m else None
            more = bool(url)
        for k, (c, ccode) in enumerate(comments, start=1):
            if cutoff and _after(c.get("created_at"), cutoff):
                dropped += 1
                continue
            body, ccut = _clip(f"#{n} comment {k} by {(c.get('user') or {}).get('login')} at "
                               f"{_z(c.get('created_at'))}:\n{c.get('body') or ''}", GET_CHARS)
            cx = {"comment_id": c.get("id"), "author": (c.get("user") or {}).get("login"),
                  "updated_at": _z(c.get("updated_at"))}
            if cutoff:
                cx["historical_availability"] = "unverified" if _after(c.get("updated_at"), cutoff) else "verified"
            recs.append(EvidenceRecord(
                evidence_id=f"gh:issue:{self.repo}#{n}#c{k}", source="tickets", event_time=_z(c.get("created_at")),
                available_time=_z(c.get("created_at")), title=f"#{n} comment {k}", content=body, truncated=ccut,
                content_ref=c.get("html_url"), attributes=self._attrs(c.get("url", ""), ccode, **cx)))
        if dropped:
            recs[0].attributes["comments_after_cutoff_dropped"] = dropped
        return self._ok(recs, truncated=more)


    # ------------------------------------------------------------------ GitHub Actions (read-only)
    # Never triggers, re-runs, cancels or deletes anything, and never downloads artifacts.
    def _run_record(self, r: dict, code: int) -> EvidenceRecord:
        sha = r.get("head_sha") or ""
        text = (f"workflow run {r.get('id')} '{r.get('name')}' #{r.get('run_number')} attempt {r.get('run_attempt')} "
                f"event={r.get('event')} branch={r.get('head_branch')} status={r.get('status')} "
                f"conclusion={r.get('conclusion')}\nhead commit {sha[:7]}: "
                f"{((r.get('head_commit') or {}).get('message') or '').splitlines()[0] if r.get('head_commit') else ''}\n"
                f"created={_z(r.get('created_at'))} started={_z(r.get('run_started_at'))} updated={_z(r.get('updated_at'))} "
                f"workflow={r.get('path')}")
        text, cut = _clip(text, SEARCH_CHARS)
        return EvidenceRecord(
            evidence_id=f"gh:run:{self.repo}#{r.get('id')}", source="builds",
            event_time=_z(r.get("run_started_at") or r.get("created_at")), available_time=_z(r.get("updated_at")),
            version=sha[:7] or None, title=f"CI run {r.get('name')} #{r.get('run_number')}: {r.get('status')}/"
                                           f"{r.get('conclusion')} @ {sha[:7]}",
            content=text, truncated=cut, content_ref=r.get("html_url"),
            attributes=self._attrs(r.get("url", ""), code, run_id=r.get("id"), head_sha=sha,
                                   commit_evidence_id=f"gh:commit:{self.repo}@{sha[:7]}" if sha else None,
                                   workflow_id=r.get("workflow_id"), workflow=r.get("name"), path=r.get("path"),
                                   event=r.get("event"), status=r.get("status"), conclusion=r.get("conclusion"),
                                   head_branch=r.get("head_branch"), run_number=r.get("run_number"),
                                   run_attempt=r.get("run_attempt")))

    def _job_record(self, j: dict, code: int, log: Optional[str] = None, log_cut: bool = False) -> EvidenceRecord:
        steps = j.get("steps") or []
        lines = [f"job {j.get('id')} '{j.get('name')}' status={j.get('status')} conclusion={j.get('conclusion')} "
                 f"runner={j.get('runner_name') or '-'} started={_z(j.get('started_at'))} "
                 f"completed={_z(j.get('completed_at'))}"]
        lines += [f"  {s.get('number')}. {s.get('name')}: {s.get('status')}/{s.get('conclusion')}" for s in steps]
        cut = False
        if log is not None:
            # keep the END of the log (failures are reported last) within the content limit
            room = max(500, GET_CHARS - len("\n".join(lines)) - 40)
            tail = log[-room:] if len(log) > room else log
            cut = log_cut or len(tail) < len(log)
            lines.append(("log (last part):\n" if cut else "log:\n") + tail)
        text, c2 = _clip("\n".join(lines), GET_CHARS)
        sha = j.get("head_sha") or ""
        return EvidenceRecord(
            evidence_id=f"gh:job:{self.repo}#{j.get('id')}", source="builds",
            event_time=_z(j.get("started_at") or j.get("created_at")),
            available_time=_z(j.get("completed_at") or j.get("started_at")), version=sha[:7] or None,
            title=f"CI job {j.get('name')}: {j.get('status')}/{j.get('conclusion')}", content=text,
            truncated=cut or c2, content_ref=j.get("html_url"),
            attributes=self._attrs(j.get("url", ""), code, job_id=j.get("id"), run_id=j.get("run_id"), head_sha=sha,
                                   commit_evidence_id=f"gh:commit:{self.repo}@{sha[:7]}" if sha else None,
                                   status=j.get("status"), conclusion=j.get("conclusion"),
                                   steps=[{"number": s.get("number"), "name": s.get("name"),
                                           "conclusion": s.get("conclusion")} for s in steps],
                                   log_included=log is not None))

    @staticmethod
    def _int_id(x: Any, what: str) -> int:
        try:
            v = int(str(x).split("#")[-1].strip())
        except ValueError:
            raise _Fail("error", f"{what} must be an integer id, got {str(x)[:40]!r}")
        if v <= 0:
            raise _Fail("error", f"{what} must be positive")
        return v

    def list_workflow_runs(self, limit: int = 10, branch: Optional[str] = None,
                           status: Optional[str] = None) -> BackendResult:
        return self._wrap(self._list_workflow_runs, limit, branch, status)

    def _list_workflow_runs(self, limit, branch, status) -> BackendResult:
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            raise _Fail("error", "limit must be an integer")
        params: dict[str, Any] = {"per_page": limit}
        if branch:
            params["branch"] = branch
        if status:
            params["status"] = status
        d, code, _ = self._get(f"/repos/{self.repo}/actions/runs", params)
        runs = d.get("workflow_runs") or []
        return self._ok([self._run_record(r, code) for r in runs[:limit]], total=d.get("total_count"),
                        truncated=(d.get("total_count") or 0) > len(runs[:limit]))

    def get_run(self, run_id: int) -> BackendResult:
        return self._wrap(self._get_run, run_id)

    def _get_run(self, run_id) -> BackendResult:
        rid = self._int_id(run_id, "run_id")
        r, code, _ = self._get(f"/repos/{self.repo}/actions/runs/{rid}")
        jobs, jcode, _ = self._get(f"/repos/{self.repo}/actions/runs/{rid}/jobs", {"per_page": 50})
        recs = [self._run_record(r, code)] + [self._job_record(j, jcode) for j in jobs.get("jobs") or []]
        return self._ok(recs, truncated=(jobs.get("total_count") or 0) > len(jobs.get("jobs") or []))

    def get_job_log(self, job_id: int, failed_only: bool = True, tail_bytes: int = 200_000) -> BackendResult:
        return self._wrap(self._get_job_log, job_id, failed_only, tail_bytes)

    def _get_job_log(self, job_id, failed_only, tail_bytes) -> BackendResult:
        jid = self._int_id(job_id, "job_id")
        j, code, _ = self._get(f"/repos/{self.repo}/actions/jobs/{jid}")
        if failed_only and j.get("conclusion") not in ("failure", "timed_out", "cancelled", "startup_failure"):
            return BackendResult(status="empty", total_matches=0,
                                 error=f"job {jid} conclusion is {j.get('conclusion')!r}; logs are returned for failed "
                                       "jobs only (pass failed_only=False to fetch it anyway)")
        # 302 to a short-lived signed URL; requests drops Authorization on the cross-host redirect, and only
        # the original api.github.com URL is ever recorded by RecordingSession
        text, lcode, _ = self._get(f"/repos/{self.repo}/actions/jobs/{jid}/logs", accept="application/vnd.github+json",
                                   raw=True, tail_bytes=max(1000, int(tail_bytes)))
        return self._ok([self._job_record(j, lcode, log=self._scrub(text), log_cut=self.last_body_cut)])

    def list_run_artifacts(self, run_id: int) -> BackendResult:
        return self._wrap(self._list_run_artifacts, run_id)

    def _list_run_artifacts(self, run_id) -> BackendResult:
        rid = self._int_id(run_id, "run_id")
        d, code, _ = self._get(f"/repos/{self.repo}/actions/runs/{rid}/artifacts", {"per_page": 50})
        recs = []
        for a in d.get("artifacts") or []:
            run = a.get("workflow_run") or {}
            sha = run.get("head_sha") or ""
            text = (f"artifact {a.get('id')} '{a.get('name')}' size={a.get('size_in_bytes')} bytes "
                    f"expired={a.get('expired')} digest={a.get('digest') or '-'} created={_z(a.get('created_at'))} "
                    f"expires={_z(a.get('expires_at'))} run={rid} head={sha[:7]} (metadata only; not downloaded)")
            recs.append(EvidenceRecord(
                evidence_id=f"gh:artifact:{self.repo}#{a.get('id')}", source="builds",
                event_time=_z(a.get("created_at")), available_time=_z(a.get("created_at")), version=sha[:7] or None,
                title=f"CI artifact {a.get('name')} ({a.get('size_in_bytes')} bytes)", content=text,
                content_ref=f"https://github.com/{self.repo}/actions/runs/{rid}",
                attributes=self._attrs(a.get("url", ""), code, artifact_id=a.get("id"), run_id=rid, head_sha=sha,
                                       name=a.get("name"), size_in_bytes=a.get("size_in_bytes"),
                                       expired=a.get("expired"), digest=a.get("digest"),
                                       commit_evidence_id=f"gh:commit:{self.repo}@{sha[:7]}" if sha else None)))
        return self._ok(recs, total=d.get("total_count"))


# ---------------------------------------------------------------------- smoke CLI
def _line(name: str, res: BackendResult) -> str:
    out = [f"{name}: status={res.status} records={len(res.records)} truncated={res.truncated}"
           + (f" error={res.error}" if res.error else "")]
    for r in res.records:
        extra = {k: r.attributes[k] for k in ("historical_availability", "http_status") if k in r.attributes}
        out.append(f"    {r.evidence_id} source={r.source} chars={len(r.content)} truncated={r.truncated} "
                   f"available_time={r.available_time} {extra}")
    return "\n".join(out)


def smoke(repo: str, sha: Optional[str] = None, path: Optional[str] = None, issue: Optional[int] = None,
          cutoff: Optional[str] = None) -> int:
    gh = GitHubConnector(repo)
    print(f"CONNECTOR SMOKE TEST (not project evidence) repo={repo} auth={gh.auth_mode}")
    meta = gh.repo_metadata()
    print(_line("repo_metadata", meta))
    commits = gh.list_commits(limit=3)
    print(_line("list_commits(limit=3)", commits))
    if not sha and commits.records:
        sha = commits.records[0].attributes.get("sha")
    if sha:
        c = gh.get_commit(sha)
        print(_line(f"get_commit({sha[:7]})", c))
        if not path and c.records:
            files = c.records[0].attributes.get("files") or []
            path = files[0] if files else None
        if path:
            print(_line(f"get_file({path!r}, {sha[:7]})", gh.get_file(path, sha)))
        print(_line("get_file(branch ref, refused)", gh.get_file(path or "README", "master")))
    print(_line("get_commit('not-a-sha')", gh.get_commit("not-a-sha")))
    if issue is None:
        try:
            data, _, _ = gh._get(f"/repos/{repo}/issues", {"state": "all", "per_page": 1})
            issue = data[0]["number"] if data else None
        except _Fail as f:
            print(f"issue lookup: {f.status} {gh._scrub(f.message)}")
    if issue is not None:
        print(_line(f"get_issue({issue})", gh.get_issue(issue)))
        print(_line(f"get_issue({issue}, cutoff={cutoff or '2000-01-01T00:00:00Z'})",
                    gh.get_issue(issue, cutoff=cutoff or "2000-01-01T00:00:00Z")))
    print(f"http requests made: {gh.requests_made}")
    return 0


def actions_smoke(repo: str, run_id: Optional[int] = None, fetch_success_log: bool = True) -> int:
    gh = GitHubConnector(repo)
    print(f"GITHUB ACTIONS CONNECTOR SMOKE TEST (read-only; not project evidence) repo={repo} auth={gh.auth_mode}")
    runs = gh.list_workflow_runs(limit=5)
    print(_line("list_workflow_runs(limit=5)", runs))
    for r in runs.records:
        a = r.attributes
        print(f"      run {a['run_id']} {a['workflow']} event={a['event']} {a['status']}/{a['conclusion']} "
              f"head={a['head_sha'][:7]} -> {a['commit_evidence_id']}")
    if run_id is None and runs.records:
        run_id = runs.records[0].attributes["run_id"]
    if run_id is None:
        print("no workflow runs to inspect")
        return 0
    run = gh.get_run(run_id)
    print(_line(f"get_run({run_id})", run))
    for rec in run.records[1:]:
        a = rec.attributes
        print(f"      job {a['job_id']} {rec.title}")
        for st in a["steps"]:
            print(f"         {st['number']}. {st['name']}: {st['conclusion']}")
    print(_line(f"list_run_artifacts({run_id})", gh.list_run_artifacts(run_id)))
    for rec in run.records[1:]:
        jid = rec.attributes["job_id"]
        print(_line(f"get_job_log({jid}, failed_only=True)", gh.get_job_log(jid)))
        if fetch_success_log:
            print(_line(f"get_job_log({jid}, failed_only=False)", gh.get_job_log(jid, failed_only=False)))
    print(_line("get_run('not-an-id')", gh.get_run("not-an-id")))
    print(f"http requests made: {gh.requests_made}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m blackbox.connectors.github_api")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("smoke", help="exercise every method against a repository (prints ids/statuses, never the token)")
    s.add_argument("--repo", required=True)
    s.add_argument("--sha")
    s.add_argument("--file", dest="path")
    s.add_argument("--issue", type=int)
    s.add_argument("--cutoff")
    ac = sub.add_parser("actions", help="read-only GitHub Actions smoke test: runs, jobs/steps, artifacts metadata, logs")
    ac.add_argument("--repo", required=True)
    ac.add_argument("--run", type=int)
    ac.add_argument("--no-success-log", action="store_true", help="do not fetch the log of a successful job")
    a = ap.parse_args(argv)
    if a.cmd == "actions":
        return actions_smoke(a.repo, a.run, not a.no_success_log)
    return smoke(a.repo, a.sha, a.path, a.issue, a.cutoff)


if __name__ == "__main__":
    sys.exit(main())
