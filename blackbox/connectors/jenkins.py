"""Read-only Jenkins connector (builds and console logs) for BlackBox live mode.

Configuration (environment variables, or the git-ignored file <repo>/.env.local):
    JENKINS_URL         e.g. http://127.0.0.1:8080
    JENKINS_USER        a Jenkins user
    JENKINS_API_TOKEN   that user's API token
    JENKINS_JOB         optional default job name
Only GET requests are made (/api/json, /job/<job>/api/json, /job/<job>/<n>/api/json, /consoleText).
The token is never logged or returned. Console text is untrusted data and is bounded.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from blackbox.connectors.jira import setting
from blackbox.schemas import BackendResult, EvidenceRecord

TIMEOUT_S = 10.0
CONSOLE_TAIL = 6000
_JOB = re.compile(r"^[A-Za-z0-9_.-]+$")


def _iso_ms(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class JenkinsConnector:
    backend = "jenkins"

    def __init__(self, url: Optional[str] = None, user: Optional[str] = None, token: Optional[str] = None,
                 job: Optional[str] = None, timeout: float = TIMEOUT_S):
        self.url = (url or setting("JENKINS_URL")).rstrip("/")
        self._user = user or setting("JENKINS_USER")
        self._token = token or setting("JENKINS_API_TOKEN")
        self.job = job or setting("JENKINS_JOB") or None
        self.timeout = timeout
        self.detail = f"live Jenkins at {self.url}" if self.configured else "Jenkins not configured"

    @property
    def configured(self) -> bool:
        return bool(self.url and self._user and self._token)

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    def _open(self, path: str):
        auth = base64.b64encode(f"{self._user}:{self._token}".encode()).decode()
        req = urllib.request.Request(f"{self.url}{path}", headers={"Authorization": f"Basic {auth}"})
        return urllib.request.urlopen(req, timeout=self.timeout)

    def _json(self, path: str) -> dict:
        with self._open(path) as r:
            return json.loads(r.read().decode("utf-8"))

    def _fail(self, exc) -> BackendResult:
        if isinstance(exc, urllib.error.HTTPError):
            if exc.code == 404:
                return BackendResult(status="empty", total_matches=0, error="not found in Jenkins")
            return BackendResult(status="unavailable" if exc.code in (401, 403) else "error",
                                 error=self._redact(f"Jenkins answered HTTP {exc.code}"))
        return BackendResult(status="unavailable", error=self._redact(f"Jenkins not reachable: {type(exc).__name__}"))

    def ready(self) -> tuple[bool, str]:
        if not self.configured:
            return False, "not configured (set JENKINS_URL, JENKINS_USER, JENKINS_API_TOKEN)"
        try:
            d = self._json("/api/json?tree=jobs[name]")
            return True, f"{len(d.get('jobs', []))} jobs"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return False, self._fail(exc).error

    def _build_record(self, job: str, b: dict, query: str) -> EvidenceRecord:
        link = b.get("url") or f"{self.url}/job/{job}/{b['number']}/"
        ts = _iso_ms(b["timestamp"])
        end = _iso_ms(b["timestamp"] + (b.get("duration") or 0))
        params = {}
        for a in b.get("actions") or []:
            for p in (a or {}).get("parameters", []) or []:
                params[p.get("name")] = p.get("value")
        content = (f"jenkins build {job} #{b['number']} result={b.get('result') or 'RUNNING'} started={ts} "
                   f"duration={(b.get('duration') or 0) / 1000:.1f}s" + (f" parameters={json.dumps(params)}" if params else ""))
        return EvidenceRecord(
            evidence_id=f"jenkins:{job}#{b['number']}", source="builds", event_time=ts, available_time=end,
            title=f"Jenkins {job} #{b['number']} {b.get('result') or 'RUNNING'}", content=content, content_ref=link,
            fixture=False, attributes={"backend": self.backend, "mode": "live", "query": query, "source_url": link,
                                       "result": b.get("result"), "parameters": params})

    def list_builds(self, job: Optional[str] = None, limit=5, cutoff: Optional[str] = None) -> BackendResult:
        if not self.configured:
            return BackendResult(status="unavailable", error="Jenkins is not configured")
        job = job or self.job
        if not job or not _JOB.match(job):
            return BackendResult(status="error", error=f"bad argument: job {job!r}")
        q = f"/job/{urllib.parse.quote(job)}/api/json?tree=builds[number,result,timestamp,duration,url,actions[parameters[name,value]]]{{0,20}}"
        try:
            d = self._json(q)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return self._fail(exc)
        recs = [self._build_record(job, b, f"GET {q}") for b in d.get("builds", [])]
        if cutoff:
            recs = [r for r in recs if (r.available_time or "") <= cutoff]
        recs = recs[: max(1, min(int(limit), 10))]
        if not recs:
            return BackendResult(status="empty", total_matches=0)
        return BackendResult(status="ok", records=recs, total_matches=len(recs))

    def get_console(self, job: str, number: int, cutoff: Optional[str] = None) -> BackendResult:
        if not self.configured:
            return BackendResult(status="unavailable", error="Jenkins is not configured")
        if not job or not _JOB.match(job):
            return BackendResult(status="error", error=f"bad argument: job {job!r}")
        try:
            n = int(str(number).split("#")[-1])
            b = self._json(f"/job/{urllib.parse.quote(job)}/{n}/api/json")
            if cutoff and _iso_ms(b["timestamp"] + (b.get("duration") or 0)) > cutoff:
                return BackendResult(status="empty", total_matches=0, error="build finished after the cutoff")
            with self._open(f"/job/{urllib.parse.quote(job)}/{n}/consoleText") as r:
                text = r.read().decode("utf-8", "replace")
        except (ValueError, TypeError) as exc:
            return BackendResult(status="error", error=f"bad argument: {exc}")
        except (urllib.error.URLError, OSError) as exc:
            return self._fail(exc)
        tail = text[-CONSOLE_TAIL:]
        link = f"{self.url}/job/{job}/{n}/consoleText"
        rec = self._build_record(job, b, f"GET /job/{job}/{n}/consoleText")
        rec.evidence_id = f"jenkins:{job}#{n}/console"
        rec.content = self._redact(tail)
        rec.truncated = len(text) > CONSOLE_TAIL
        rec.content_ref = link
        rec.attributes.update({"source_url": link, "console_chars": len(text), "untrusted_text": True})
        return BackendResult(status="ok", records=[rec], total_matches=1)
