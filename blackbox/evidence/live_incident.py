"""Open a live incident from a Prometheus alert evaluation (no snapshot involved).

An alert rule `<metric> <op> <threshold> for <for_s>s` on one service is evaluated over Prometheus samples (5 s
steps, like demo/alerts.py does over captured points). Every run of consecutive breaching samples that lasts
`for_s` seconds is an alert **episode**: it has an onset (first breaching sample), a firing time (the sample that
completes `for_s`) and an end (last breaching sample).

Which episode opens the incident
--------------------------------
An alert often fires, recovers briefly, and fires again while the same fault is active. The incident is the
latest episode **together with every earlier episode that follows the previous one within `lookback_s`** (the
chain stops at the first quiet gap longer than the lookback). The alert's firing time is the firing time of the
first episode in that chain, and the window starts `lookback_s` before it. Consequences:

- a delayed or re-opened investigation keeps the original onset: later re-firings of the same fault extend the
  chain, they do not move the window past the change that started it;
- an earlier, unrelated episode separated by a quiet period longer than the lookback is not pulled in, and the
  window never reaches further back than `lookback_s` before the first chained firing (nor before `since`);
- the cutoff is the moment the incident is opened ("created"); nothing later is visible, however late the
  investigation starts. Callers that queue an investigation keep the incident they opened, they do not re-open.

`open_live_incident` returns the incident and a timing record (episodes, chosen onset, window, cutoff, creation
time) that callers persist with the run.
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
STEP_S = 5


def episodes(points: list[tuple[float, float]], op: str, threshold: float, for_s: int) -> list[dict]:
    """Alert episodes over (unix time, value) points: onset, fired, end (unix seconds) and the value at firing."""
    need, out, run = max(1, math.ceil(for_s / STEP_S)), [], []
    for t, v in sorted(points) + [(math.inf, None)]:
        if v is not None and OPS[op](v, threshold):
            run.append((t, v))
            continue
        if len(run) >= need:
            out.append({"onset": run[0][0], "fired": run[need - 1][0], "end": run[-1][0], "value": run[need - 1][1]})
        run = []
    return out


def chain(eps: list[dict], lookback_s: int) -> list[dict]:
    """The latest episode plus each earlier one that ended within lookback_s of the next one's onset."""
    if not eps:
        return []
    out = [eps[-1]]
    for e in reversed(eps[:-1]):
        if out[0]["onset"] - e["end"] <= lookback_s:
            out.insert(0, e)
        else:
            break
    return out


def _points(prom: PrometheusMetrics, service: str, metric: str, since: str, until: str) -> list[tuple[float, float]]:
    data = prom._get("/api/v1/query_range", {"query": f'{metric}{{job="{service}"}}', "start": _ts(since),
                                              "end": _ts(until), "step": STEP_S})
    return [(float(t), float(v)) for s in data.get("result", []) for t, v in s.get("values", [])]


def find_firing(prom: PrometheusMetrics, service: str, metric: str, op: str, threshold: float, for_s: int,
                since: str, until: str) -> Optional[tuple[str, float]]:
    """Firing time and value of the latest episode (kept for callers that only need to know whether it fired)."""
    eps = episodes(_points(prom, service, metric, since, until), op, threshold, for_s)
    return (_iso(eps[-1]["fired"]), eps[-1]["value"]) if eps else None


def open_live_incident(service: str, metric: str, op: str, threshold: float, for_s: int = 15,
                       since: Optional[str] = None, lookback_s: int = 300, services: Optional[list[str]] = None,
                       prom: Optional[PrometheusMetrics] = None, title: Optional[str] = None,
                       now: Optional[float] = None) -> tuple[Incident, dict]:
    prom = prom or PrometheusMetrics()
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    cutoff = _iso(now)
    since = since or _iso(now - 1800)
    eps = [e for e in episodes(_points(prom, service, metric, since, cutoff), op, threshold, for_s)
           if e["fired"] <= now]
    if not eps:
        raise LookupError(f"alert {service} {metric} {op} {threshold:g} for {for_s}s has not fired since {since}")
    ch = chain(eps, lookback_s)
    first = ch[0]
    fired_at = _iso(first["fired"])
    start = _iso(max(first["fired"] - lookback_s, _ts(since)))
    iid = "LIVE-" + hashlib.sha1(f"{service}|{metric}|{fired_at}".encode()).hexdigest()[:6].upper()
    inc = Incident(
        incident_id=iid,
        alert=Alert(alert_id=f"ALR-{iid[5:]}", title=title or f"{service} {metric.replace('_', ' ')} high",
                    service=service, signal=metric, condition=f"{metric} {op} {threshold:g} for {for_s}s",
                    observed_value=first["value"], fired_at=fired_at),
        window_start=start, window_end=cutoff, investigation_cutoff=cutoff, environment="demo-local (live)",
        services=services or ["checkout-svc", "payment-svc"], available_sources=LIVE_SOURCES,
        notes="Live incident opened from a Prometheus alert evaluation. Investigate using the available evidence sources.")
    timing = {
        "rule": f"{service}:{metric}{op}{threshold:g}:{for_s}",
        "created_at": cutoff, "searched_since": since, "lookback_s": lookback_s,
        "episodes_found": len(eps), "episodes_in_incident": len(ch),
        "first_onset_at": _iso(first["onset"]), "first_fired_at": fired_at,
        "latest_onset_at": _iso(ch[-1]["onset"]), "latest_fired_at": _iso(ch[-1]["fired"]),
        "window_start": start, "window_end": cutoff, "investigation_cutoff": cutoff,
        "episodes": [{k: (_iso(v) if k != "value" else v) for k, v in e.items()} for e in eps],
        "rule_note": "window starts lookback_s before the first episode of the latest chain of episodes; "
                     "cutoff fixed when the incident was opened",
    }
    return inc, timing


def open_incident(service: str, metric: str, op: str, threshold: float, for_s: int = 15, since: Optional[str] = None,
                  lookback_s: int = 300, services: Optional[list[str]] = None,
                  prom: Optional[PrometheusMetrics] = None, title: Optional[str] = None) -> Incident:
    return open_live_incident(service, metric, op, threshold, for_s, since, lookback_s, services, prom, title)[0]
