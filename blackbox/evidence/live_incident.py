"""Open a live incident from a Prometheus alert evaluation (no snapshot involved).

An alert rule `<metric> <op> <threshold> for <for_s>s` on one service is evaluated over Prometheus samples:
the alert fires at the sample that completes `for_s` seconds of consecutive breaching samples (the most recent
such episode is used), exactly
like demo/alerts.py does over captured points. The incident window starts `lookback_s` before the firing time;
the investigation cutoff is the moment the investigation starts (nothing later is visible).
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Optional

from blackbox.connectors.prometheus import PrometheusMetrics, _iso, _ts
from blackbox.schemas import Alert, Incident

OPS = {">": lambda a, b: a > b, "<": lambda a, b: a < b}
LIVE_SOURCES = ["logs", "traces", "metrics", "deployments", "builds", "commits", "configs", "tickets"]


def find_firing(prom: PrometheusMetrics, service: str, metric: str, op: str, threshold: float, for_s: int,
                since: str, until: str) -> Optional[tuple[str, float]]:
    data = prom._get("/api/v1/query_range", {"query": f'{metric}{{job="{service}"}}', "start": _ts(since),
                                              "end": _ts(until), "step": 5})
    pts = sorted((float(t), float(v)) for s in data.get("result", []) for t, v in s.get("values", []))
    need, streak, last = max(1, math.ceil(for_s / 5)), 0, None
    for t, v in pts:
        if OPS[op](v, threshold):
            streak += 1
            if streak == need:          # an alert episode fires here; keep the most recent episode
                last = (_iso(t), v)
        else:
            streak = 0
    return last


def open_incident(service: str, metric: str, op: str, threshold: float, for_s: int = 15, since: Optional[str] = None,
                  lookback_s: int = 300, services: Optional[list[str]] = None,
                  prom: Optional[PrometheusMetrics] = None, title: Optional[str] = None) -> Incident:
    prom = prom or PrometheusMetrics()
    now = datetime.now(timezone.utc).timestamp()
    cutoff = _iso(now)
    since = since or _iso(now - 1800)
    hit = find_firing(prom, service, metric, op, threshold, for_s, since, cutoff)
    if hit is None:
        raise LookupError(f"alert {service} {metric} {op} {threshold:g} for {for_s}s has not fired since {since}")
    fired_at, value = hit
    start = _iso(_ts(fired_at) - lookback_s)
    iid = "LIVE-" + hashlib.sha1(f"{service}|{metric}|{fired_at}".encode()).hexdigest()[:6].upper()
    return Incident(
        incident_id=iid,
        alert=Alert(alert_id=f"ALR-{iid[5:]}", title=title or f"{service} {metric.replace('_', ' ')} high",
                    service=service, signal=metric, condition=f"{metric} {op} {threshold:g} for {for_s}s",
                    observed_value=value, fired_at=fired_at),
        window_start=start, window_end=cutoff, investigation_cutoff=cutoff, environment="demo-local (live)",
        services=services or ["checkout-svc", "payment-svc"], available_sources=LIVE_SOURCES,
        notes="Live incident opened from a Prometheus alert evaluation. Investigate using the available evidence sources.")
