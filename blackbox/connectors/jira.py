"""Read-only Jira Cloud connector (issues and comments) for BlackBox live mode.

Configuration (environment variables, or the git-ignored file <repo>/.env.local):
    JIRA_BASE_URL   e.g. https://your-site.atlassian.net
    JIRA_EMAIL      the Atlassian account e-mail
    JIRA_API_TOKEN  an API token (https://id.atlassian.com/manage-profile/security/api-tokens)
    JIRA_PROJECT    optional project key to restrict searches
Only GET requests are made (/rest/api/3/myself, /rest/api/3/search/jql, /rest/api/3/issue/<key>).
The token is never logged or returned; error text is redacted before it leaves this module.
Ticket text is untrusted data: it is returned as evidence content, never followed as instructions.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from blackbox.schemas import BackendResult, EvidenceRecord

REPO = Path(__file__).resolve().parents[2]
TIMEOUT_S = 10.0
CONTENT_CHARS = 1500
_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def load_env_local(path: Path = REPO / ".env.local") -> dict:
    out = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def setting(name: str) -> str:
    return (os.environ.get(name) or load_env_local().get(name) or "").strip()


def _iso(s: str) -> str:
    """Jira timestamps ('2026-11-24T03:11:22.811+0000') -> UTC ISO with Z."""
    try:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    except (ValueError, TypeError):
        return s


def _adf_text(node) -> str:
    """Plain text from Atlassian Document Format."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if node.get("type") == "text":
        return node.get("text", "")
    parts = [_adf_text(c) for c in node.get("content", []) or []]
    sep = "\n" if node.get("type") in ("doc", "paragraph", "listItem") else ""
    return sep.join(p for p in parts if p)


class JiraConnector:
    backend = "jira"

    def __init__(self, base_url: Optional[str] = None, email: Optional[str] = None, token: Optional[str] = None,
                 project: Optional[str] = None, timeout: float = TIMEOUT_S):
        self.base = (base_url or setting("JIRA_BASE_URL")).rstrip("/")
        self._email = email or setting("JIRA_EMAIL")
        self._token = token or setting("JIRA_API_TOKEN")
        self.project = project or setting("JIRA_PROJECT") or None
        self.timeout = timeout
        self.detail = f"live Jira Cloud at {self.base}" if self.configured else "Jira not configured"

    @property
    def configured(self) -> bool:
        return bool(self.base and self._email and self._token)

    def _redact(self, text: str) -> str:
        if self._token:
            text = text.replace(self._token, "***")
        return re.sub(r"(?i)(authorization: *basic) +\S+", r"\1 ***", text)

    def _get(self, path: str, params: dict | None = None) -> dict:
        auth = base64.b64encode(f"{self._email}:{self._token}".encode()).decode()
        url = f"{self.base}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _fail(self, exc) -> BackendResult:
        if isinstance(exc, urllib.error.HTTPError):
            st = "unavailable" if exc.code in (401, 403) else "error"
            return BackendResult(status=st, error=self._redact(f"Jira answered HTTP {exc.code}"))
        return BackendResult(status="unavailable", error=self._redact(f"Jira not reachable: {type(exc).__name__}"))

    def ready(self) -> tuple[bool, str]:
        if not self.configured:
            return False, "not configured (set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN)"
        try:
            me = self._get("/rest/api/3/myself")
            return True, f"authenticated as {me.get('displayName', '?')}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return False, self._fail(exc).error

    def _issue_record(self, issue: dict, cutoff: Optional[str]) -> list[EvidenceRecord]:
        f = issue.get("fields", {})
        key = issue["key"]
        link = f"{self.base}/browse/{key}"
        created = _iso(f.get("created", ""))
        if cutoff and created and created > cutoff:
            return []
        desc = _adf_text(f.get("description"))
        body = (f"{key} [{(f.get('status') or {}).get('name', '')}] {f.get('summary', '')}\n"
                f"created {created} by {(f.get('reporter') or {}).get('displayName', '?')}\n{desc}")
        base_attrs = {"backend": self.backend, "mode": "live", "source_url": link, "untrusted_text": True}
        recs = [EvidenceRecord(evidence_id=f"jira:{key}", source="tickets", event_time=created, available_time=created,
                               title=f"{key}: {f.get('summary', '')}"[:160], content=body[:CONTENT_CHARS],
                               truncated=len(body) > CONTENT_CHARS, content_ref=link, fixture=False,
                               attributes={**base_attrs, "query": f"GET issue {key}"})]
        for c in ((f.get("comment") or {}).get("comments") or []):
            ts = _iso(c.get("created", ""))
            if cutoff and ts > cutoff:
                continue
            txt = _adf_text(c.get("body"))
            recs.append(EvidenceRecord(
                evidence_id=f"jira:{key}#c{c['id']}", source="tickets", event_time=ts, available_time=ts,
                title=f"comment on {key} by {(c.get('author') or {}).get('displayName', '?')}",
                content=txt[:CONTENT_CHARS], truncated=len(txt) > CONTENT_CHARS,
                content_ref=f"{link}?focusedCommentId={c['id']}", fixture=False,
                attributes={**base_attrs, "query": f"GET issue {key} comments"}))
        return recs

    def search_tickets(self, query=None, service=None, limit=5, cutoff: Optional[str] = None) -> BackendResult:
        if not self.configured:
            return BackendResult(status="unavailable", error="Jira is not configured")
        words = [w for w in re.split(r"[^A-Za-z0-9_-]+", f"{query or ''} {service or ''}") if len(w) > 2][:6]
        clauses = []
        if self.project:
            clauses.append(f'project = "{self.project}"')
        if words:
            clauses.append("(" + " OR ".join(f'text ~ "{w}"' for w in words) + ")")
        if cutoff:
            clauses.append(f'created <= "{cutoff[:16].replace("T", " ")}"')
        jql = " AND ".join(clauses) + " ORDER BY created DESC" if clauses else "ORDER BY created DESC"
        try:
            limit = max(1, min(int(limit), 10))
            data = self._get("/rest/api/3/search/jql", {"jql": jql, "maxResults": limit,
                                                         "fields": "summary,status,created,reporter,description,comment"})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return self._fail(exc)
        recs = []
        for issue in data.get("issues", []):
            recs += self._issue_record(issue, cutoff)[:1]
        for r in recs:
            r.attributes["query"] = f"JQL {jql}"
        if not recs:
            return BackendResult(status="empty", total_matches=0)
        return BackendResult(status="ok", records=recs, total_matches=len(recs))

    def get_ticket(self, key: str, cutoff: Optional[str] = None) -> BackendResult:
        if not self.configured:
            return BackendResult(status="unavailable", error="Jira is not configured")
        k = str(key).split(":", 1)[-1].split("#")[0].strip().upper()
        if not _KEY.match(k):
            return BackendResult(status="error", error=f"bad argument: issue key {key!r}")
        try:
            issue = self._get(f"/rest/api/3/issue/{k}", {"fields": "summary,status,created,reporter,description,comment"})
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return BackendResult(status="empty", total_matches=0, error=f"issue {k} not found")
            return self._fail(exc)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return self._fail(exc)
        recs = self._issue_record(issue, cutoff)
        if not recs:
            return BackendResult(status="empty", total_matches=0, error=f"issue {k} was created after the cutoff")
        return BackendResult(status="ok", records=recs, total_matches=len(recs))
