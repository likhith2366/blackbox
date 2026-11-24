"""Read-only Prometheus connector: BlackBox's `get_metrics` tool served from a Prometheus server.

    python -m blackbox.connectors.prometheus smoke [--url http://127.0.0.1:9090] [--service payment-svc]

The demo services' metric points reach Prometheus through the Alloy collector (OTLP -> remote write), so the
Prometheus series is `<metric>{job="<service>"}` with the services' own timestamps. Only the HTTP query API is
used (/-/ready, /api/v1/label/__name__/values, /api/v1/query_range). Output matches the snapshot backend's
metric records: one record per series with min/max/last and the points in the window.

URL: constructor argument, env BLACKBOX_PROMETHEUS_URL, default http://127.0.0.1:9090.
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

DEFAULT_URL = "http://127.0.0.1:9090"
TIMEOUT_S = 10.0
STEP_S = 5
MAX_POINTS = 240                 # per series (20 min at 5 s); longer windows are refused, not silently thinned
CONTENT_CHARS = 4000
_NAME = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:]*$")
_LABEL = re.compile(r"^[A-Za-z0-9_.:-]+$")
SKIP = re.compile(r"^(go_|process_|prometheus_|promhttp_|net_|scrape_|up$|target_info$)")


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


def _iso(t: float) -> str:
    dt = datetime.fromtimestamp(t, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class PrometheusMetrics:
    backend = "prometheus"

    def __init__(self, url: Optional[str] = None, timeout: float = TIMEOUT_S):
        self.url = (url or os.environ.get("BLACKBOX_PROMETHEUS_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self.detail = f"live Prometheus at {self.url}"

    def _get(self, path: str, params) -> dict:
        q = urllib.parse.urlencode(params, doseq=True)
        with urllib.request.urlopen(f"{self.url}{path}?{q}", timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("status") != "success":
            raise RuntimeError(data.get("error") or "query failed")
        return data["data"]

    def ready(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(f"{self.url}/-/ready", timeout=self.timeout) as r:
                body = r.read().decode("utf-8", "replace").strip()
                return r.status == 200, body[:200]
        except (urllib.error.URLError, OSError) as exc:
            return False, f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"

    def names(self, service: str) -> list[str]:
        data = self._get("/api/v1/label/__name__/values", {"match[]": f'{{job="{service}"}}'})
        return sorted(n for n in data if not SKIP.match(n))

    def get_metrics(self, service: str, metric=None, start=None, end=None) -> BackendResult:
        if not service or not _LABEL.match(str(service)):
            return BackendResult(status="error", error=f"bad argument: service {service!r}")
        s, e = _ts(start), _ts(end)
        if s is None or e is None or e <= s:
            return BackendResult(status="error", error="bad argument: start and end are required (end > start)")
        if (e - s) / STEP_S > MAX_POINTS:
            return BackendResult(status="error", error=f"window too long: at most {MAX_POINTS * STEP_S}s per query")
        try:
            names = self.names(service)
        except (urllib.error.URLError, OSError) as exc:
            return BackendResult(status="unavailable", error=f"Prometheus not reachable at {self.url}: {type(exc).__name__}")
        except (RuntimeError, ValueError) as exc:
            return BackendResult(status="error", error=f"Prometheus query failed: {exc}")
        want = (metric or "").lower().strip()
        chosen = [n for n in names if not want or want in n.lower()]
        if not chosen:
            return BackendResult(status="empty", total_matches=0,
                                 error=("no matching series; metrics for this service: " + ", ".join(names)) if names
                                 else f"no metrics for {service} in Prometheus")
        recs = []
        for name in chosen:
            if not _NAME.match(name):
                continue
            q = f'{name}{{job="{service}"}}'
            try:
                data = self._get("/api/v1/query_range", {"query": q, "start": s, "end": e, "step": STEP_S})
            except (urllib.error.URLError, OSError) as exc:
                return BackendResult(status="unavailable", error=f"Prometheus not reachable: {type(exc).__name__}")
            except (RuntimeError, ValueError) as exc:
                return BackendResult(status="error", error=f"Prometheus query failed: {exc}")
            pts = [(float(t), float(v)) for series in data.get("result", []) for t, v in series.get("values", [])]
            if not pts:
                continue
            pts.sort()
            vals = [v for _, v in pts]
            head = (f"{service} {name} ({STEP_S}s steps, {_iso(pts[0][0])} .. {_iso(pts[-1][0])}): "
                    f"min={min(vals):g} max={max(vals):g} last={vals[-1]:g}\n")
            body = head + "; ".join(f"{_iso(t)[11:19]} {v:g}" for t, v in pts)
            cut = len(body) > CONTENT_CHARS
            eid = "metric:" + hashlib.sha1(f"prometheus|{service}|{name}|{s}|{e}".encode()).hexdigest()[:10]
            link = f"{self.url}/graph?" + urllib.parse.urlencode({"g0.expr": q, "g0.tab": "0"})
            recs.append(EvidenceRecord(
                evidence_id=eid, source="metrics", service=service, event_time=_iso(pts[0][0]),
                available_time=_iso(pts[-1][0]), title=f"{service} {name}", content=body[:CONTENT_CHARS],
                truncated=cut, content_ref=link, fixture=False,
                attributes={"metric": name, "points": len(pts), "min": min(vals), "max": max(vals), "last": vals[-1],
                            "backend": "prometheus", "query": f"query_range {q} step={STEP_S}s",
                            "mode": "live", "source_url": link}))
        if not recs:
            return BackendResult(status="empty", total_matches=0, error="no points in the window")
        return BackendResult(status="ok", records=recs, total_matches=len(recs))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["smoke"])
    ap.add_argument("--url", default=None)
    ap.add_argument("--service", default="payment-svc")
    a = ap.parse_args(argv)
    pm = PrometheusMetrics(a.url)
    ok, body = pm.ready()
    print(f"Prometheus {pm.url}: ready={ok} ({body})")
    if not ok:
        return 1
    now = datetime.now(timezone.utc).timestamp()
    r = pm.get_metrics(a.service, start=_iso(now - 300), end=_iso(now))
    print(f"get_metrics({a.service}, last 5 min): status={r.status} {r.error or ''}")
    for rec in r.records:
        print("   ", rec.evidence_id, rec.content.splitlines()[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
