"""Threshold alert rules evaluated over captured metric points (like a Prometheus rule).

A rule fires at the timestamp of the sample that completes `for_s` seconds of
consecutive breaching samples; that timestamp becomes Alert.fired_at.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from demo.timeutil import parse

OPS = {">": lambda a, b: a > b, "<": lambda a, b: a < b}


@dataclass
class AlertRule:
    alert_id: str
    title: str
    service: str
    metric: str
    op: str
    threshold: float
    for_s: int = 15
    signal: str = ""

    def condition(self) -> str:
        return f"{self.metric} {self.op} {self.threshold:g} for {self.for_s}s"


def load_points(metrics_dir: Path, service: str, metric: str) -> list[dict]:
    p = metrics_dir / f"{service}.jsonl"
    pts = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec["metric"] == metric:
                    pts.append(rec)
    pts.sort(key=lambda r: r["ts"])
    return pts


def evaluate(rule: AlertRule, metrics_dir: Path, not_before: str | None = None,
             interval_s: float = 5.0) -> dict | None:
    need = max(1, math.ceil(rule.for_s / interval_s))
    streak = 0
    for pt in load_points(metrics_dir, rule.service, rule.metric):
        if not_before and parse(pt["ts"]) < parse(not_before):
            continue
        if OPS[rule.op](float(pt["value"]), rule.threshold):
            streak += 1
            if streak >= need:
                return {"alert_id": rule.alert_id, "title": rule.title, "service": rule.service,
                        "signal": rule.signal or rule.metric, "condition": rule.condition(),
                        "observed_value": float(pt["value"]), "fired_at": pt["ts"]}
        else:
            streak = 0
    return None
