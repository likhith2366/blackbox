"""Read-only Grafana Tempo connector: BlackBox's `search_traces` and `get_trace` tools served from Tempo.

    python -m blackbox.connectors.tempo smoke [--url http://127.0.0.1:3200] [--service checkout-svc]

The demo services' spans reach Tempo through the Alloy collector (OTLP). Only the HTTP query API is used:
/ready, /api/search (TraceQL) and /api/traces/<id>. Output matches the snapshot backend: search returns one
`trace:<id>` record per trace; get_trace returns one `span:<id>` record per span.

URL: constructor argument, env BLACKBOX_TEMPO_URL, default http://127.0.0.1:3200.
"""
from __future__ import annotations

import argparse
import base64
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

DEFAULT_URL = "http://127.0.0.1:3200"
TIMEOUT_S = 10.0
MAX_LIMIT = 20
CONTENT_CHARS = 1200
_LABEL = re.compile(r"^[A-Za-z0-9_.:-]+$")
_HEX = re.compile(r"^[0-9a-f]{1,32}$")
KIND = {"SPAN_KIND_SERVER": "server", "SPAN_KIND_CLIENT": "client", "SPAN_KIND_INTERNAL": "internal",
        2: "server", 3: "client", 1: "internal"}


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


def _hex(v: str) -> str:
    """Tempo returns ids as hex (search) or base64 (trace JSON); normalise to lower-case hex."""
    if not v:
        return ""
    if re.fullmatch(r"[0-9a-fA-F]+", v) and len(v) in (16, 32):
        return v.lower()
    try:
        return base64.b64decode(v).hex()
    except ValueError:
        return v


def _attrs(lst) -> dict:
    out = {}
    for a in lst or []:
        v = a.get("value", {})
        out[a.get("key")] = next(iter(v.values()), None) if v else None
    return out


class TempoTraces:
    backend = "tempo"

    def __init__(self, url: Optional[str] = None, timeout: float = TIMEOUT_S):
        self.url = (url or os.environ.get("BLACKBOX_TEMPO_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self.detail = f"live Grafana Tempo at {self.url}"

    def _get(self, path: str, params: dict | None = None) -> dict:
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        with urllib.request.urlopen(f"{self.url}{path}{q}", timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def ready(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(f"{self.url}/ready", timeout=self.timeout) as r:
                body = r.read().decode("utf-8", "replace").strip()
                return r.status == 200 and body.startswith("ready"), body[:200]
        except (urllib.error.URLError, OSError) as exc:
            return False, f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"

    # ------------------------------------------------------------------ search
    def traceql(self, service=None, status=None, min_duration_ms=None) -> str:
        conds = []
        if service:
            if not _LABEL.match(str(service)):
                raise ValueError(f"invalid service {service!r}")
            conds.append(f'resource.service.name = "{service}"')
        st = (status or "").lower()
        if st in ("error", "errors", "failed", "failure"):
            conds.append("status = error")
        elif st in ("ok", "success"):
            conds.append("status != error")
        if min_duration_ms is not None:
            conds.append(f"duration >= {float(min_duration_ms):g}ms")
        return "{ " + " && ".join(conds) + " }" if conds else "{ }"

    def search_traces(self, service=None, start=None, end=None, status=None, min_duration_ms=None,
                      limit=10) -> BackendResult:
        try:
            limit = max(1, min(int(limit), MAX_LIMIT))
            q = self.traceql(service, status, min_duration_ms)
        except (TypeError, ValueError) as exc:
            return BackendResult(status="error", error=f"bad argument: {exc}")
        s, e = _ts(start), _ts(end)
        if s is None or e is None or e <= s:
            return BackendResult(status="error", error="bad argument: start and end are required (end > start)")
        params = {"q": q, "start": int(s), "end": int(e) + 1, "limit": 200, "spss": 50}
        try:
            data = self._get("/api/search", params)
        except (urllib.error.URLError, OSError) as exc:
            return BackendResult(status="unavailable", error=f"Tempo not reachable at {self.url}: {type(exc).__name__}")
        except ValueError as exc:
            return BackendResult(status="error", error=f"Tempo search failed: {exc}")
        hits = []
        for t in data.get("traces", []):
            st_ns = int(t.get("startTimeUnixNano", 0))
            if not (s <= st_ns / 1e9 <= e):     # the cutoff is the end of the window; never later
                continue
            hits.append((st_ns, t))
        if not hits:
            return BackendResult(status="empty", total_matches=0)
        hits.sort(key=lambda h: h[0])
        recs = []
        for st_ns, t in hits[:limit]:
            tid = _hex(t["traceID"])
            dur = t.get("durationMs", 0)
            spans = [sp for ss in t.get("spanSets", []) or [t.get("spanSet", {})] for sp in (ss or {}).get("spans", [])]
            n = sum(len((ss or {}).get("spans", [])) for ss in (t.get("spanSets") or [t.get("spanSet") or {}]))
            link = f"{self.url}/api/traces/{tid}"
            content = (f"trace {tid} root={t.get('rootServiceName')} {t.get('rootTraceName')} start={_iso_ns(st_ns)} "
                       f"duration={dur}ms matched_spans={n}")
            recs.append(EvidenceRecord(
                evidence_id=f"trace:{tid}", source="traces", service=t.get("rootServiceName"),
                event_time=_iso_ns(st_ns), available_time=_iso_ns(st_ns), title=f"{t.get('rootServiceName')} "
                f"{t.get('rootTraceName')} {dur}ms", content=content, content_ref=link, fixture=False,
                attributes={"trace_id": tid, "duration_ms": dur, "matched_spans": n, "backend": "tempo",
                            "query": f"search {q}", "mode": "live", "source_url": link,
                            "matched_span_ids": [_hex(sp.get("spanID", "")) for sp in spans][:10]}))
        return BackendResult(status="ok", records=recs, total_matches=len(hits), truncated=len(hits) > limit)

    # ------------------------------------------------------------------ one trace
    def get_trace(self, trace_id: str, cutoff: Optional[str] = None) -> BackendResult:
        tid = str(trace_id).split(":", 1)[-1].strip().lower()
        if not _HEX.match(tid):
            return BackendResult(status="error", error=f"bad argument: trace id {trace_id!r}")
        link = f"{self.url}/api/traces/{tid}"
        try:
            data = self._get(f"/api/traces/{tid}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return BackendResult(status="empty", total_matches=0, error=f"trace {tid} not found in Tempo")
            return BackendResult(status="error", error=f"Tempo answered HTTP {exc.code}")
        except (urllib.error.URLError, OSError) as exc:
            return BackendResult(status="unavailable", error=f"Tempo not reachable at {self.url}: {type(exc).__name__}")
        except ValueError as exc:
            return BackendResult(status="error", error=f"Tempo returned invalid JSON: {exc}")
        cut_t = _ts(cutoff)
        recs, hidden = [], 0
        for b in data.get("batches", data.get("resourceSpans", [])):
            res = _attrs(b.get("resource", {}).get("attributes"))
            svc = res.get("service.name")
            for ss in b.get("scopeSpans", b.get("instrumentationLibrarySpans", [])):
                for sp in ss.get("spans", []):
                    start_ns, end_ns = int(sp["startTimeUnixNano"]), int(sp["endTimeUnixNano"])
                    if cut_t is not None and end_ns / 1e9 > cut_t:
                        hidden += 1     # finished after the investigation cutoff: not visible yet
                        continue
                    sid, parent = _hex(sp.get("spanId", "")), _hex(sp.get("parentSpanId", ""))
                    status = sp.get("status", {})
                    err = status.get("code") in (2, "STATUS_CODE_ERROR")
                    dur = round((end_ns - start_ns) / 1e6, 2)
                    attrs = _attrs(sp.get("attributes"))
                    body = (f"span {sid} parent={parent or '-'} trace={tid} start={_iso_ns(start_ns)} {svc} "
                            f"{sp.get('name')} {dur}ms {'error' if err else 'ok'}"
                            + (f" ({status.get('message')})" if status.get("message") else "")
                            + " " + json.dumps(attrs, separators=(",", ":")) + " resource=" + json.dumps(res, separators=(",", ":")))
                    recs.append(EvidenceRecord(
                        evidence_id=f"span:{sid}", source="traces", service=svc, event_time=_iso_ns(start_ns),
                        available_time=_iso_ns(end_ns), title=f"{svc} {sp.get('name')} {dur}ms {'error' if err else 'ok'}",
                        content=body[:CONTENT_CHARS], truncated=len(body) > CONTENT_CHARS, content_ref=link, fixture=False,
                        attributes={"trace_id": tid, "parent_span_id": parent or None,
                                    "kind": KIND.get(sp.get("kind"), str(sp.get("kind"))), "duration_ms": dur,
                                    "status": "error" if err else "ok", "backend": "tempo",
                                    "query": f"GET /api/traces/{tid}", "mode": "live", "source_url": link}))
        recs.sort(key=lambda r: r.event_time or "")
        if not recs:
            return BackendResult(status="empty", total_matches=0,
                                 error=f"trace {tid} has no spans finished before the cutoff" if hidden else f"trace {tid} empty")
        return BackendResult(status="ok", records=recs, total_matches=len(recs))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["smoke"])
    ap.add_argument("--url", default=None)
    ap.add_argument("--service", default="checkout-svc")
    a = ap.parse_args(argv)
    tp = TempoTraces(a.url)
    ok, body = tp.ready()
    print(f"Tempo {tp.url}: ready={ok} ({body})")
    if not ok:
        return 1
    now = datetime.now(timezone.utc).timestamp()
    r = tp.search_traces(a.service, start=_iso_ns(int((now - 600) * 1e9)), end=_iso_ns(int(now * 1e9)), limit=3)
    print(f"search_traces({a.service}, last 10 min): status={r.status} total={r.total_matches} {r.error or ''}")
    for rec in r.records:
        print("   ", rec.content)
    if r.records:
        g = tp.get_trace(r.records[0].evidence_id)
        print(f"get_trace: status={g.status} spans={len(g.records)}")
        for rec in g.records[:4]:
            print("   ", rec.content[:160])
    return 0


if __name__ == "__main__":
    sys.exit(main())
