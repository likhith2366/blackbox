"""Resolve answer-key evidence selectors (demo.controller_plans) to snapshot evidence ids."""
from __future__ import annotations

import json
from pathlib import Path

from demo.timeutil import parse


def _jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _event(meta, change_id):
    for e in meta["events"]:
        if e["change_id"] == change_id:
            return e
    raise KeyError(f"change {change_id} not applied in capture {meta['capture_id']}")


def resolve_selectors(selectors, snap: Path, meta: dict, cutoff: float):
    ids, equivalents = [], {}
    logs = spans = None
    for sel in selectors:
        kind = sel[0]
        if kind == "commit":
            ids.append(f"commit:{_event(meta, sel[1])['commit'][:7]}")
        elif kind == "deploy":
            ids.extend(f"deploy:{d}" for d in _event(meta, sel[1])["deployments"])
        elif kind == "build":
            ids.extend(f"build:{b}" for b in _event(meta, sel[1])["builds"])
        elif kind == "config":
            ids.append(f"config:{sel[1]}@{_event(meta, sel[2])['commit'][:7]}")
        elif kind == "metric":
            for m in _jsonl(snap / "metrics.jsonl"):
                if m["service"] == sel[1] and m["metric"] == sel[2]:
                    ids.append(m["evidence_id"])
                    break
            else:
                raise KeyError(f"metric {sel[1]}/{sel[2]} not in snapshot")
        elif kind == "log":
            logs = logs if logs is not None else _jsonl(snap / "logs.jsonl")
            _, svc, needle, after = sel
            t_after = parse(_event(meta, after)["at"]) if after else 0
            hits = [r for r in logs if r["service"] == svc and t_after <= parse(r["ts"]) <= cutoff
                    and needle.lower() in json.dumps(r["raw"], ensure_ascii=False).lower()]
            if not hits:
                raise KeyError(f"no log line {svc!r} {needle!r} before cutoff")
            first = hits[0]
            ids.append(first["evidence_id"])
            same = [r["evidence_id"] for r in hits[1:] if r["raw"]["msg"] == first["raw"]["msg"]]
            if same:
                equivalents[first["evidence_id"]] = same[:500]
        elif kind == "trace_error":
            spans = spans if spans is not None else _jsonl(snap / "spans.jsonl")
            _, svc, after = sel
            t_after = parse(_event(meta, after)["at"])
            errs = [s for s in spans if s["service"] == svc and s["status"] == "error"
                    and t_after <= parse(s["end"]) <= cutoff]
            if not errs:
                raise KeyError(f"no error span for {svc}")
            tids = []
            for s in errs:
                if s["trace_id"] not in tids:
                    tids.append(s["trace_id"])
            ids.append(f"trace:{tids[0]}")
            if len(tids) > 1:
                equivalents[f"trace:{tids[0]}"] = [f"trace:{t}" for t in tids[1:500]]
        elif kind == "ticket":
            ids.append(f"jira:{sel[1]}")
        elif kind == "comment":
            ids.append(f"jira:{sel[1]}#{sel[2]}")
        else:
            raise ValueError(kind)
    out = []
    for i in ids:
        if i not in out:
            out.append(i)
    return out, equivalents
