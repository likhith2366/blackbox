"""Broad, read-only probing of an EvidenceBackend (used by the snapshot validator and the
leakage tests). Calls every interface method: default window, an extremely wide window,
per service, a few ticket queries, export_logs, and direct lookups of given ids.
Every response (records, errors, exception text, incident, source status) is kept as
text so callers can scan it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

WIDE = {"start": "1970-01-01T00:00:00Z", "end": "2100-01-01T00:00:00Z"}
BIG = 1000
# no_provenance: sources whose records may legitimately contain a deployed commit sha.
# commits stay searchable (CONTRACT s8); configs are repository versions keyed by commit
# (config:<svc>@<sha7>; scenarios 3/6 deploy a config commit) - approved by Integration
# 2026-11-20; tickets are team discussion (reported as a warning, not a failure).
DEPLOYED_SHA_ALLOWED_SOURCES = ("commits", "configs", "tickets")
# where a deployed sha / build id / digest must never survive under no_provenance
PROVENANCE_STRICT_SOURCES = ("deployments", "builds", "logs", "traces", "metrics")
TICKET_ID = re.compile(r"^jira:([A-Za-z][A-Za-z0-9]*-\d+)")
HEX40 = re.compile(r"\b[0-9a-f]{40}\b")
DIGEST = re.compile(r"sha256:[0-9a-f]{16,}")


def dump(x) -> str:
    if hasattr(x, "model_dump_json"):
        return x.model_dump_json()
    if isinstance(x, list):
        return "[" + ",".join(dump(i) for i in x) + "]"
    return json.dumps(x, default=str)


class Collected:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []      # (label, response text)
        self.records: list[tuple[str, Any]] = []    # (label, EvidenceRecord-like)
        self.raw: list[tuple[str, Any]] = []        # (label, raw return value)
        self.exceptions: list[tuple[str, str]] = []

    def add(self, label, result):
        self.raw.append((label, result))
        self.calls.append((label, dump(result)))
        recs = getattr(result, "records", None)
        if recs is None and isinstance(result, list):
            recs = [r for r in result if hasattr(r, "evidence_id")]
        for r in recs or []:
            self.records.append((label, r))


def call(col: Collected, label, fn, *a, echo: Optional[str] = None, **kw):
    """`echo`: the id a direct lookup asked for. An error that merely repeats the caller's
    own argument is not a leak, so it is masked in the call text; returned records are
    still collected."""
    n = len(col.calls)
    try:
        col.add(label, fn(*a, **kw))
    except Exception as e:
        msg = f"EXCEPTION {type(e).__name__}: {e}"
        col.calls.append((label, msg))
        col.exceptions.append((label, msg))
    if echo:
        col.calls[n:] = [(lab, txt.replace(echo, "<ARG>")) for lab, txt in col.calls[n:]]


def collect(be, lookups: Optional[dict] = None) -> Collected:
    col = Collected()
    call(col, "incident", be.incident)
    try:
        inc = be.incident()
        services = list(dict.fromkeys(list(inc.services) + [inc.alert.service]))
    except Exception:
        services = []
    call(col, "source_status", be.source_status)
    for wl, win in (("default", {}), ("wide", WIDE)):
        call(col, f"search_logs/{wl}", be.search_logs, limit=BIG, **win)
        call(col, f"search_traces/{wl}", be.search_traces, limit=BIG, **win)
        call(col, f"list_deployments/{wl}", be.list_deployments, **win)
        call(col, f"search_commits/{wl}", be.search_commits, limit=BIG,
             **({"since": win["start"], "until": win["end"]} if win else {}))
        call(col, f"export_logs/{wl}", be.export_logs, **win)
        for s in services:
            call(col, f"search_logs/{s}/{wl}", be.search_logs, service=s, limit=BIG, **win)
            call(col, f"search_traces/{s}/{wl}", be.search_traces, service=s, limit=BIG, **win)
            call(col, f"get_metrics/{s}/{wl}", be.get_metrics, service=s, **win)
            call(col, f"list_deployments/{s}/{wl}", be.list_deployments, service=s, **win)
    for s in services:
        call(col, f"search_commits/{s}", be.search_commits, service=s, limit=BIG)
        call(col, f"get_config/{s}", be.get_config, service=s)
        call(col, f"search_tickets/{s}", be.search_tickets, service=s, limit=BIG)
    call(col, "search_tickets", be.search_tickets, limit=BIG)
    for q in ("error", "timeout", "deploy", "incident", "pool", "latency"):
        call(col, f"search_tickets/q={q}", be.search_tickets, query=q, limit=BIG)
    lk = lookups or {}
    for t in lk.get("trace_ids", []):
        call(col, f"get_trace/{t}", be.get_trace, t, echo=t)
    for b in lk.get("build_ids", []):
        call(col, f"get_build/{b}", be.get_build, b, echo=b)
    for c in lk.get("commit_shas", []):
        call(col, f"get_commit/{c}", be.get_commit, c, echo=c)
    for k in lk.get("ticket_keys", []):
        call(col, f"get_ticket/{k}", be.get_ticket, k, echo=k)
    for svc, ver in lk.get("configs", []):
        call(col, f"get_config/{svc}@{ver}", be.get_config, service=svc, version=ver, echo=ver)
    return col


def _strings(d, prefix=""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from _strings(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(d, list):
        for v in d:
            yield from _strings(v, prefix)
    elif isinstance(d, str):
        yield prefix, d


def learn_identifiers(col: Collected) -> dict[str, list]:
    """Identifiers of ablatable information, learned from an UN-ablated probe."""
    L: dict[str, set] = {"ticket_keys": set(), "trace_ids": set(), "build_ids": set(),
                         "digests": set(), "deployed_shas": set(), "commit_shas": set(),
                         "trace_record_ids": set()}
    for _, r in col.records:
        eid = r.evidence_id
        m = TICKET_ID.match(eid)
        if r.source == "tickets" and m:
            L["ticket_keys"].add(m.group(1))
        if r.source == "traces":
            L["trace_record_ids"].add(eid)
            tid = r.attributes.get("trace_id")
            if isinstance(tid, str) and tid:
                L["trace_ids"].add(tid)
            if eid.startswith("trace:"):
                L["trace_ids"].add(eid.split(":", 1)[1])
        if r.source == "builds" or eid.startswith("build:"):
            L["build_ids"].add(eid.split(":", 1)[1])
        if r.source == "commits" and eid.startswith("commit:"):
            L["commit_shas"].add(eid.split(":", 1)[1])
        if r.source in ("deployments", "builds"):
            blob = r.model_dump_json()
            L["digests"].update(DIGEST.findall(blob))
            for k, v in _strings(r.attributes):
                kl = k.lower()
                if "build" in kl and v:
                    L["build_ids"].add(v)
                if ("digest" in kl or "artifact" in kl) and v:
                    L["digests"].add(v)
                if any(x in kl for x in ("commit", "sha", "git", "revision")) and v:
                    L["deployed_shas"].add(v)
            if r.source == "deployments" and r.version:
                L["deployed_shas"].add(r.version)
            L["deployed_shas"].update(HEX40.findall(blob))
    for k in ("build_ids", "digests", "deployed_shas"):
        L[k] = {v for v in L[k] if len(v) >= 6}
    forms = set()
    for s in L["deployed_shas"]:
        forms.add(s)
        if re.fullmatch(r"[0-9a-f]{12,40}", s):
            forms.add(s[:7])
    out = {k: sorted(v) for k, v in L.items()}
    out["sha_forms"] = sorted(forms)
    return out
