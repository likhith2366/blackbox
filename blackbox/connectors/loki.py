"""Read-only Grafana Loki connector: BlackBox reads application logs from a Loki server.

    python -m blackbox.connectors.loki smoke [--url http://127.0.0.1:3100] [--incident INC-9001]

Loki is a free, open-source log store (Grafana Labs). Logs reach it from a shipper
(scripts/push_logs_to_loki.py, Grafana Alloy or Promtail) with these labels:
    job="blackbox-demo", service="<service>", level="<LEVEL>"   (+ incident="<id>" for replayed snapshots)
This module only queries Loki's HTTP API (GET /loki/api/v1/query_range, /ready); it never writes.

It has the same interface as blackbox/connectors/local_logs.LocalLogs (search, export), so the
live backend can serve the model's `search_logs` tool from Loki instead of local files. Query words
are matched as whole words on the client, exactly like the snapshot and local-file backends; Loki
narrows the candidates first with a case-insensitive line filter.

URL resolution: constructor argument, then env BLACKBOX_LOKI_URL, then http://127.0.0.1:3100.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from blackbox.schemas import BackendResult, EvidenceRecord

DEFAULT_URL = "http://127.0.0.1:3100"
JOB = "blackbox-demo"
CONTENT_CHARS = 600
MAX_LIMIT = 100
FETCH_LIMIT = 5000          # lines fetched from Loki per search before client-side whole-word ranking
TIMEOUT_S = 10.0
STOPWORDS = {"the", "a", "an", "of", "in", "and", "or", "to", "for", "on", "at", "is", "with", "by", "from"}
_LABEL_OK = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _ts(s) -> Optional[float]:
    if s is None or s == "":
        return None
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _iso_ns(ns: int) -> str:
    dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _now() -> str:
    return _iso_ns(int(datetime.now(timezone.utc).timestamp() * 1e9))


def _tokens(q: Optional[str]) -> list[str]:
    if not q:
        return []
    out = []
    for t in re.split(r"[\s,;:()\"'=]+", q.lower()):
        if t and t not in STOPWORDS and t not in out:
            out.append(t)
    return out


def resolve_url(explicit: Optional[str] = None) -> str:
    return (explicit or os.environ.get("BLACKBOX_LOKI_URL") or DEFAULT_URL).rstrip("/")


class LokiLogs:
    def __init__(self, url: Optional[str] = None, incident: Optional[str] = None, timeout: float = TIMEOUT_S):
        self.url = resolve_url(url)
        self.incident = incident        # set when the logs were replayed from a snapshot with an incident label
        self.timeout = timeout
        self._last_query = ""
        self.detail = f"live Grafana Loki at {self.url}" + (f" (incident={incident})" if incident else "")

    # ------------------------------------------------------------------ HTTP
    def _get(self, path: str, params: dict) -> dict:
        q = urllib.parse.urlencode(params)
        with urllib.request.urlopen(f"{self.url}{path}?{q}", timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def ready(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(f"{self.url}/ready", timeout=self.timeout) as r:
                body = r.read().decode("utf-8", "replace").strip()
                return r.status == 200 and body.startswith("ready"), body[:200]
        except (urllib.error.URLError, OSError) as exc:
            return False, f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"

    # ------------------------------------------------------------------ query
    def logql(self, service=None, query=None, level=None) -> str:
        labels = [f'job="{JOB}"']
        for k, v in (("incident", self.incident), ("service", service)):
            if v:
                if not _LABEL_OK.match(str(v)):
                    raise ValueError(f"invalid {k} {v!r}")
                labels.append(f'{k}="{v}"')
        lv = (level or "").strip().upper()
        lv = "WARNING" if lv == "WARN" else lv
        if lv:
            if not _LABEL_OK.match(lv):
                raise ValueError(f"invalid level {level!r}")
            labels.append(f'level="{lv}"')
        expr = "{" + ", ".join(labels) + "}"
        toks = _tokens(query)
        if toks:
            # backtick string: LogQL takes it raw, so only backticks themselves must be kept out
            alt = "|".join(re.escape(t) for t in toks).replace("`", "")
            expr += f" |~ `(?i)({alt})`"
        return expr

    def _fetch(self, service, start, end, query, level):
        s, e = _ts(start), _ts(end)
        params = {"query": self.logql(service, query, level), "limit": FETCH_LIMIT, "direction": "forward"}
        self._last_query = params["query"]
        # Loki needs a window; without one, search the hour before the end (or now).
        end_ns = int((e if e is not None else datetime.now(timezone.utc).timestamp()) * 1e9) + 1
        start_ns = int(s * 1e9) if s is not None else end_ns - 3600 * 10**9
        params.update(start=start_ns, end=end_ns)
        data = self._get("/loki/api/v1/query_range", params)
        if data.get("status") != "success":
            raise RuntimeError(f"Loki answered status={data.get('status')}")
        rows = []
        for stream in data.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for ts_ns, line in stream.get("values", []):
                rows.append((int(ts_ns), labels, line))
        return rows, len(rows) >= FETCH_LIMIT

    def _record(self, ts_ns: int, labels: dict, line: str, retrieved_at: str) -> EvidenceRecord:
        try:
            rec = json.loads(line)
        except ValueError:
            rec = {}
        service = labels.get("service") or rec.get("service")
        # a snapshot replayed into Loki carries its original evidence id as structured metadata
        eid = labels.get("evidence_id") or "log:" + hashlib.sha1(f"loki|{service}|{ts_ns}|{line}".encode()).hexdigest()[:10]
        ts = rec.get("ts") or _iso_ns(ts_ns)
        return EvidenceRecord(
            evidence_id=eid, source="logs", service=service, event_time=ts, available_time=ts,
            title=f"{rec.get('level', labels.get('level', ''))} {service}: {rec.get('msg', line[:80])}"[:160],
            content=line if len(line) <= CONTENT_CHARS else line[:CONTENT_CHARS], truncated=len(line) > CONTENT_CHARS,
            content_ref=f"loki:{self.url}#{service}@{ts_ns}", fixture=False,
            attributes={"live": True, "retrieved_at": retrieved_at, "backend": "loki", "mode": "live",
                        "query": self._last_query, "source_url": f"{self.url}/loki/api/v1/query_range", "labels": labels,
                        "level": rec.get("level", labels.get("level")), "trace_id": rec.get("trace_id"),
                        **({"full_text": line[:20000]} if len(line) > CONTENT_CHARS else {})})

    # ------------------------------------------------------------------ API (same as LocalLogs)
    def search(self, service=None, start=None, end=None, query=None, level=None, limit=20) -> BackendResult:
        try:
            limit = max(1, min(int(limit), MAX_LIMIT))
        except (TypeError, ValueError):
            limit = 20
        retrieved_at = _now()
        try:
            rows, capped = self._fetch(service, start, end, query, level)
        except ValueError as exc:
            return BackendResult(status="error", error=f"bad argument: {exc}")
        except (urllib.error.URLError, OSError) as exc:
            return BackendResult(status="unavailable", error=f"Loki not reachable at {self.url}: {type(exc).__name__}")
        except (RuntimeError, KeyError, TypeError) as exc:
            return BackendResult(status="error", error=f"Loki query failed: {exc}")
        toks = _tokens(query)
        scored = []
        for ts_ns, labels, line in rows:
            low = line.lower()
            score = sum(1 for t in toks if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", low)) if toks else 0
            if toks and score == 0:
                continue        # Loki's regex matched inside a word; keep whole-word semantics
            scored.append((score, ts_ns, labels, line))
        if not scored:
            return BackendResult(status="empty", total_matches=0)
        scored.sort(key=lambda h: (-h[0], h[1], str(h[2].get("service")), h[3]))
        recs = [self._record(ts, lab, line, retrieved_at) for _, ts, lab, line in scored[:limit]]
        return BackendResult(status="ok", records=recs, total_matches=len(scored),
                             truncated=len(scored) > limit or capped)

    def export(self, start=None, end=None) -> list[EvidenceRecord]:
        try:
            rows, _ = self._fetch(None, start, end, None, None)
        except (ValueError, urllib.error.URLError, OSError, RuntimeError, KeyError, TypeError):
            return []
        retrieved_at = _now()
        rows.sort(key=lambda r: (r[0], str(r[1].get("service")), r[2]))
        return [self._record(ts, lab, line, retrieved_at) for ts, lab, line in rows]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["smoke"])
    ap.add_argument("--url", default=None)
    ap.add_argument("--incident", default=None)
    args = ap.parse_args(argv)
    lk = LokiLogs(args.url, incident=args.incident)
    ok, body = lk.ready()
    print(f"Loki {lk.url}: ready={ok} ({body})")
    if not ok:
        return 1
    print("LogQL:", lk.logql(level="ERROR"))
    r = lk.search(level="ERROR", limit=3)
    print(f"search(level=ERROR, limit=3): status={r.status} total={r.total_matches} {r.error or ''}")
    for rec in r.records:
        print("   ", rec.evidence_id, rec.service, rec.content[:140])
    return 0


if __name__ == "__main__":
    sys.exit(main())
