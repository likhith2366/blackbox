"""Snapshot-backed EvidenceBackend (written by Agent 2, owned by Agent 1 since the handoff). Implements blackbox/evidence/interface.py.

Reads only scenarios/snapshots/<incident_id>/ (or `snapshot_root`). Never reads
evaluator_private/ or demo/ controller files. Every rule is enforced here in code:

- cutoff: records whose available_time is after Incident.investigation_cutoff are dropped
  at load time (ticket comments/status/description versions individually; metric points
  individually; spans individually).
- ablations (CONTRACT.md s8): no_jira, no_provenance, no_traces, incl. indirect mentions.
- sources absent from the snapshot (withheld) behave like ablated ones.
- connector failure (snapshot runtime.json, not visible to the model): after N successful
  calls to a source, its methods return BackendResult(status="error").
- result limits (`truncated`, `total_matches`), content limits (600 chars in search
  results, 4000 in get_* results), deterministic ordering, read-only.

Adapters are snapshot replays of captured data and team-authored fixtures; none of them
is a live integration (SourceStatus.detail says which is which).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from blackbox.schemas import (Ablation, BackendResult, EvidenceRecord, Incident, SourceName,
                              SourceStatus)

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "scenarios" / "snapshots"
SEARCH_CHARS = 600
GET_CHARS = 4000
MAX_LIMIT = 50
ALL_SOURCES: list[SourceName] = ["logs", "traces", "metrics", "deployments", "builds", "commits",
                                 "configs", "tickets"]
ABLATED: dict[str, set] = {"none": set(), "no_jira": {"tickets"}, "no_provenance": {"builds"},
                           "no_traces": {"traces"}}
PROVENANCE_FIELDS = ("version", "commit", "build_id", "artifact_digest", "client_version")
REDACTED = "[redacted]"
TICKET_REDACTED = "[ticket ref removed]"
STOPWORDS = {"the", "a", "an", "of", "in", "and", "or", "to", "for", "on", "at", "is", "with", "by", "from"}

_CACHE: dict[tuple, "_Store"] = {}
_CACHE_LOCK = threading.Lock()


# --------------------------------------------------------------------------- helpers
def _ts(s: Optional[str]) -> Optional[float]:
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


def _norm_iso(s: str) -> str:
    return _iso(_ts(s))


def _clip(text: str, n: int) -> tuple[str, bool]:
    if len(text) <= n:
        return text, False
    return text[: n - 15].rstrip() + " ...[truncated]", True


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _tokens(q: Optional[str]) -> list[str]:
    if not q:
        return []
    toks = [t for t in re.split(r"[\s,;:()\"'=]+", q.lower()) if t and t not in STOPWORDS]
    out = []
    for t in toks:
        if t not in out:
            out.append(t)
    return out


def _score(text: str, toks: list[str]) -> int:
    """Whole-word matches only: the token "out" must not match inside "checkout-svc"."""
    low = text.lower()
    return sum(1 for t in toks if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low))


def _limit(n, default) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, MAX_LIMIT))


# --------------------------------------------------------------------------- store
class _Store:
    """Prepared, immutable view of one snapshot under one ablation (cutoff applied)."""

    def __init__(self, snap: Path, ablation: str):
        self.snap = snap
        self.ablation = ablation
        self.incident = Incident.model_validate_json((snap / "incident.json").read_text(encoding="utf-8"))
        self.cutoff = _ts(self.incident.investigation_cutoff)
        self.w_start = _ts(self.incident.window_start)
        self.w_end = min(_ts(self.incident.window_end), self.cutoff)
        rt = snap / "runtime.json"
        self.runtime = json.loads(rt.read_text(encoding="utf-8")) if rt.exists() else {}
        self.present = set(self.incident.available_sources)
        self.unavailable = (set(ALL_SOURCES) - self.present) | ABLATED[ablation]
        raw_tickets = json.loads((snap / "tickets.json").read_text(encoding="utf-8")) \
            if (snap / "tickets.json").exists() else []
        keys = sorted({t["key"].split("-")[0] for t in raw_tickets} | {"DEMO", "OPS"})
        self.ticket_re = re.compile(r"\b(?:" + "|".join(map(re.escape, keys)) + r")-\d+\b")
        self._load_logs()
        self._load_spans()
        self._load_metrics()
        self._load_provenance()
        self._load_code()
        self._load_tickets(raw_tickets)

    # --- text hygiene for ablations
    def scrub(self, text: str) -> str:
        if self.ablation == "no_jira":
            text = self.ticket_re.sub(TICKET_REDACTED, text)
        return text

    def visible(self, t: Optional[float]) -> bool:
        return t is not None and t <= self.cutoff

    # --- logs
    def _load_logs(self):
        self.logs: list[EvidenceRecord] = []
        if "logs" in self.unavailable:
            return
        for r in _jsonl(self.snap / "logs.jsonl"):
            t = _ts(r["ts"])
            if not self.visible(t):
                continue
            raw = dict(r["raw"])
            if self.ablation == "no_provenance":
                for f in PROVENANCE_FIELDS:
                    if f in raw:
                        raw[f] = REDACTED
                if isinstance(raw.get("payment_client"), dict):
                    pc = dict(raw["payment_client"])
                    if "client_version" in pc:
                        pc["client_version"] = REDACTED
                    raw["payment_client"] = pc
            extras = {k: v for k, v in raw.items() if k not in ("ts", "level", "service", "msg")}
            body = f"{raw['ts']} {raw['level']} {raw['service']} {raw['msg']}"
            if extras:
                body += " " + json.dumps(extras, ensure_ascii=False, separators=(",", ":"))
            body = self.scrub(body)
            self.logs.append(EvidenceRecord(
                evidence_id=r["evidence_id"], source="logs", service=r["service"],
                event_time=_norm_iso(r["ts"]), available_time=_norm_iso(r["ts"]),
                title=self.scrub(f"{raw['level']} {raw['service']}: {raw['msg']}")[:160],
                content=body, content_ref=f"logs.jsonl#{r['line']}",
                attributes={"level": raw["level"], "msg": self.scrub(raw["msg"]),
                            "group": f"{raw['service']}|{raw['level']}|{raw['msg']}|"
                                     + ",".join(sorted(extras))}))
        self.logs.sort(key=lambda e: (e.event_time, e.evidence_id))

    # --- traces
    def _load_spans(self):
        self.spans: dict[str, dict] = {}
        self.traces: dict[str, list[dict]] = {}
        if "traces" in self.unavailable:
            return
        for s in _jsonl(self.snap / "spans.jsonl"):
            if not self.visible(_ts(s["end"])):
                continue
            s = dict(s)
            if self.ablation == "no_provenance":
                s["resource"] = {k: (REDACTED if "version" in k else v) for k, v in s.get("resource", {}).items()}
            self.spans[s["span_id"]] = s
            self.traces.setdefault(s["trace_id"], []).append(s)
        for tid in self.traces:
            self.traces[tid].sort(key=lambda x: (x["start"], x["span_id"]))
        self.trace_summ = {tid: self._summarise(tid, spans) for tid, spans in self.traces.items()}

    def _summarise(self, tid, spans):
        ids = {s["span_id"] for s in spans}
        roots = [s for s in spans if not s.get("parent_span_id") or s["parent_span_id"] not in ids]
        root = roots[0]
        errs = [s for s in spans if s["status"] == "error"]
        return {"trace_id": tid, "root": root, "start": _ts(root["start"]),
                "duration_ms": root["duration_ms"], "status": "error" if errs else "ok",
                "services": sorted({s["service"] for s in spans}), "n": len(spans), "errors": errs,
                "available": max(_ts(s["end"]) for s in spans)}

    def span_line(self, s: dict) -> str:
        line = f"{s['service']} {s['name']} {s['duration_ms']}ms {s['status']}"
        if s.get("status_message"):
            line += f" ({s['status_message']})"
        attrs = {k: v for k, v in s.get("attributes", {}).items()}
        if attrs:
            line += " " + json.dumps(attrs, separators=(",", ":"))
        return line

    def trace_record(self, tid: str) -> EvidenceRecord:
        sm = self.trace_summ[tid]
        root = sm["root"]
        lines = [f"trace {tid} root={root['service']} {root['name']} start={_norm_iso(root['start'])} "
                 f"duration={root['duration_ms']}ms status={sm['status']} spans={sm['n']} "
                 f"services={','.join(sm['services'])}"]
        for s in self.traces[tid]:
            lines.append(" - " + self.span_line(s))
        content, cut = _clip("\n".join(lines), SEARCH_CHARS)
        return EvidenceRecord(
            evidence_id=f"trace:{tid}", source="traces", service=root["service"],
            event_time=_norm_iso(root["start"]), available_time=_iso(sm["available"]),
            title=f"{root['service']} {root['name']} {root['duration_ms']}ms {sm['status']}",
            content=content, truncated=cut, content_ref=f"spans.jsonl?trace_id={tid}",
            attributes={"trace_id": tid, "duration_ms": root["duration_ms"], "status": sm["status"],
                        "span_count": sm["n"], "services": sm["services"]})

    # --- metrics
    def _load_metrics(self):
        self.metrics: list[dict] = []
        if "metrics" in self.unavailable:
            return
        for m in _jsonl(self.snap / "metrics.jsonl"):
            pts = [(_ts(t), float(v)) for t, v in m["points"] if self.visible(_ts(t))]
            if pts:
                self.metrics.append({**m, "pts": pts})
        self.metrics.sort(key=lambda m: (m["service"], m["metric"]))

    # --- deployments / builds
    def _load_provenance(self):
        self.deployments: list[dict] = []
        self.builds: dict[str, dict] = {}
        if "deployments" not in self.unavailable:
            for d in _jsonl(self.snap / "deployments.jsonl"):
                if self.visible(_ts(d["finished_at"])):
                    self.deployments.append(d)
            self.deployments.sort(key=lambda d: (d["started_at"], d["deploy_id"]))
        if "builds" not in self.unavailable:
            for b in _jsonl(self.snap / "builds.jsonl"):
                if self.visible(_ts(b["finished_at"])):
                    self.builds[b["build_id"]] = b

    def deployment_record(self, d: dict, full: bool) -> EvidenceRecord:
        if self.ablation == "no_provenance":
            content = (f"{_norm_iso(d['started_at'])} deployment of {d['service']} to {d['environment']} "
                       f"by {d['actor']} ({d['trigger']}) status={d['status']}")
            attrs = {"deploy_id": d["deploy_id"], "trigger": d["trigger"], "actor": d["actor"]}
            version = None
        else:
            content = (f"{_norm_iso(d['started_at'])} deployment {d['deploy_id']} of {d['service']} version "
                       f"{d['version']} to {d['environment']} build={d['build_id']} commit={d['commit'][:7]} "
                       f"artifact_digest={d['artifact_digest']} by {d['actor']} ({d['trigger']}) "
                       f"status={d['status']}")
            attrs = {"deploy_id": d["deploy_id"], "build_id": d["build_id"], "commit": d["commit"][:7],
                     "artifact_digest": d["artifact_digest"], "trigger": d["trigger"], "actor": d["actor"]}
            version = d["version"]
        content, cut = _clip(content, GET_CHARS if full else SEARCH_CHARS)
        return EvidenceRecord(
            evidence_id=f"deploy:{d['deploy_id']}", source="deployments", service=d["service"],
            event_time=_norm_iso(d["started_at"]), available_time=_norm_iso(d["finished_at"]),
            version=version, title=f"deployment of {d['service']}" + (f" {version}" if version else ""),
            content=content, truncated=cut, content_ref=f"deployments.jsonl#{d['deploy_id']}",
            attributes=attrs)

    # --- commits / configs
    def _load_code(self):
        self.commits: list[dict] = []
        self.configs: list[dict] = []
        if "commits" not in self.unavailable:
            for c in _jsonl(self.snap / "commits.jsonl"):
                if self.visible(_ts(c["committer_date"])):
                    self.commits.append(c)
            self.commits.sort(key=lambda c: (_ts(c["committer_date"]), c["sha"]), reverse=True)
        if "configs" not in self.unavailable:
            for c in _jsonl(self.snap / "configs.jsonl"):
                if self.visible(_ts(c["committed_at"])):
                    self.configs.append(c)
            self.configs.sort(key=lambda c: (_ts(c["committed_at"]), c["commit"]))

    def commit_record(self, c: dict, full: bool) -> EvidenceRecord:
        head = (f"commit {c['sha'][:7]} {_norm_iso(c['committer_date'])} {c['author']}\n"
                f"{c['message']}\nfiles: " + ", ".join(f"{f['status']} {f['path']}" for f in c["files"]))
        text = head + ("\n\n" + c["diff"] if full else "")
        text, cut = _clip(self.scrub(text), GET_CHARS if full else SEARCH_CHARS)
        return EvidenceRecord(
            evidence_id=f"commit:{c['sha'][:7]}", source="commits",
            service=",".join(c["services"]) or None,
            event_time=_norm_iso(c["author_date"]), available_time=_norm_iso(c["committer_date"]),
            version=c["sha"][:7], title=self.scrub(c["message"].splitlines()[0])[:160], content=text,
            truncated=cut, content_ref=f"commits.jsonl#{c['sha']}", fixture=True,
            attributes={"sha": c["sha"], "author": c["author"],
                        "files": [f["path"] for f in c["files"]]})

    # --- tickets
    def _load_tickets(self, raw):
        self.tickets: list[dict] = []
        if "tickets" in self.unavailable:
            return
        for t in raw:
            if not self.visible(_ts(t["created"])):
                continue
            descs = [d for d in t["description_versions"] if self.visible(_ts(d["at"]))]
            stats = [s for s in t["status_history"] if self.visible(_ts(s["at"]))]
            comments = [c for c in t["comments"] if self.visible(_ts(c["at"]))]
            self.tickets.append({
                "key": t["key"], "summary": t["summary"], "type": t["type"],
                "components": t["components"], "labels": t["labels"], "reporter": t["reporter"],
                "assignee": t["assignee"], "created": t["created"], "links": t.get("links", []),
                "description": descs[-1]["text"] if descs else "",
                "description_at": descs[-1]["at"] if descs else t["created"],
                "status": stats[-1]["status"] if stats else "Open",
                "status_at": stats[-1]["at"] if stats else t["created"],
                "comments": comments})
        self.tickets.sort(key=lambda t: (t["key"].split("-")[0], int(t["key"].split("-")[1])))

    def ticket_record(self, t: dict, full: bool) -> EvidenceRecord:
        text = (f"{t['key']} [{t['type']}] {t['summary']}\nstatus: {t['status']} (as of {_norm_iso(t['status_at'])}) "
                f"components: {', '.join(t['components'])} reporter: {t['reporter']} "
                f"assignee: {t['assignee'] or '-'} created: {_norm_iso(t['created'])} "
                f"comments: {len(t['comments'])}"
                + (f" links: {', '.join(t['links'])}" if t["links"] else "")
                + f"\n{t['description']}")
        text, cut = _clip(text, GET_CHARS if full else SEARCH_CHARS)
        last = max([_ts(t["created"]), _ts(t["description_at"]), _ts(t["status_at"])]
                   + [_ts(c["at"]) for c in t["comments"]])
        return EvidenceRecord(
            evidence_id=f"jira:{t['key']}", source="tickets", service=(t["components"] or [None])[0],
            event_time=_norm_iso(t["created"]), available_time=_iso(last),
            title=f"{t['key']}: {t['summary']}"[:160], content=text, truncated=cut,
            content_ref=f"tickets.json#{t['key']}", fixture=True,
            attributes={"status": t["status"], "components": t["components"], "comment_count": len(t["comments"])})

    def comment_record(self, t: dict, c: dict) -> EvidenceRecord:
        text, cut = _clip(f"{t['key']} comment {c['id']} by {c['author']} at {_norm_iso(c['at'])}:\n{c['body']}",
                          GET_CHARS)
        return EvidenceRecord(
            evidence_id=f"jira:{t['key']}#{c['id']}", source="tickets", service=(t["components"] or [None])[0],
            event_time=_norm_iso(c["at"]), available_time=_norm_iso(c["at"]),
            title=f"{t['key']} comment by {c['author']}", content=text, truncated=cut,
            content_ref=f"tickets.json#{t['key']}/{c['id']}", fixture=True,
            attributes={"author": c["author"], "ticket": t["key"]})


def _get_store(snap: Path, ablation: str) -> _Store:
    snap = snap.resolve()
    stamp = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(snap.iterdir()) if p.is_file())
    key = (str(snap), ablation, stamp)  # keyed by snapshot AND ablation (and file versions)
    with _CACHE_LOCK:
        st = _CACHE.get(key)
        if st is None:
            st = _Store(snap, ablation)
            _CACHE[key] = st
        return st


# --------------------------------------------------------------------------- backend
SOURCE_DETAIL = {
    "logs": "snapshot replay of JSON logs captured from the demo services",
    "traces": "snapshot replay of spans captured from the demo services",
    "metrics": "snapshot replay of metric points captured from the demo services",
    "deployments": "snapshot replay of records written by the demo deploy tooling",
    "builds": "snapshot replay of manifests written by the demo build tooling",
    "commits": "snapshot of the demo company git repo (team-authored fixture history)",
    "configs": "config file versions from the demo company git repo (team-authored fixture)",
    "tickets": "team-authored Jira-style ticket fixtures (not a live Jira integration)",
}
FIXTURE_SOURCES = {"commits", "configs", "tickets"}
METHOD_SOURCE = {"search_logs": "logs", "search_traces": "traces", "get_trace": "traces",
                 "get_metrics": "metrics", "list_deployments": "deployments", "get_build": "builds",
                 "search_commits": "commits", "get_commit": "commits", "get_config": "configs",
                 "search_tickets": "tickets", "get_ticket": "tickets"}


class SnapshotBackend:
    def __init__(self, incident_id: str, ablation: Ablation, snap_dir: Path):
        if ablation not in ABLATED:
            raise ValueError(f"unknown ablation {ablation!r}")
        self.incident_id = incident_id
        self.ablation = ablation
        self._st = _get_store(snap_dir, ablation)
        self._ok_calls: dict[str, int] = {}
        self._failing: set[str] = set()
        self._lock = threading.Lock()
        self._failures = {f["source"]: f for f in self._st.runtime.get("failures", [])}

    # ----------------------------------------------------------- plumbing
    def _gate(self, method: str) -> Optional[BackendResult]:
        src = METHOD_SOURCE[method]
        if src in self._st.unavailable:   # withheld or ablated: absent, not failing
            return BackendResult(status="unavailable", error=f"source '{src}' is not available for this incident")
        with self._lock:
            f = self._failures.get(src)
            if f and (src in self._failing or self._ok_calls.get(src, 0) >= int(f["after_successful_calls"])):
                self._failing.add(src)
                return BackendResult(status="error", error=f.get("error", f"{src} connector error"))
        return None

    def _done(self, method: str, res: BackendResult) -> BackendResult:
        if res.status in ("ok", "empty"):
            with self._lock:
                src = METHOD_SOURCE[method]
                self._ok_calls[src] = self._ok_calls.get(src, 0) + 1
        return res

    @staticmethod
    def _result(records: list[EvidenceRecord], total: Optional[int] = None, limit: Optional[int] = None,
                error: Optional[str] = None) -> BackendResult:
        total = len(records) if total is None else total
        if limit is not None and len(records) > limit:
            records = records[:limit]
        if not records:
            return BackendResult(status="empty", records=[], total_matches=total, truncated=False, error=error)
        return BackendResult(status="ok", records=[r.model_copy(deep=True) for r in records],
                             total_matches=total, truncated=total > len(records), error=error)

    def _window(self, start, end):
        st = self._st
        s = _ts(start) if start else st.w_start
        e = _ts(end) if end else st.w_end
        return s, min(e, st.cutoff)

    # ----------------------------------------------------------- interface
    def incident(self) -> Incident:
        inc = self._st.incident.model_copy(deep=True)
        inc.available_sources = [s for s in inc.available_sources if s not in self._st.unavailable]
        return inc

    def source_status(self) -> list[SourceStatus]:
        out = []
        cov = self._st.runtime.get("coverage", {})
        for s in ALL_SOURCES:
            if s in ABLATED[self.ablation]:
                out.append(SourceStatus(source=s, available=False, healthy=False,
                                        detail=f"removed by ablation {self.ablation}", fixture=s in FIXTURE_SOURCES))
                continue
            if s not in self._st.present:
                out.append(SourceStatus(source=s, available=False, healthy=False,
                                        detail="not captured for this incident", fixture=s in FIXTURE_SOURCES))
                continue
            detail = SOURCE_DETAIL[s] + (f"; {cov[s]}" if s in cov else "")
            if self.ablation == "no_provenance" and s == "deployments":
                detail += "; version/build/commit/digest links removed"
            out.append(SourceStatus(source=s, available=True, healthy=s not in self._failing,
                                    detail=detail, fixture=s in FIXTURE_SOURCES))
        return out

    def _uncovered(self, source: str, service: Optional[str]) -> Optional[BackendResult]:
        """A named service that has no captured records for this source at all is unavailable."""
        if not service or service not in self._st.incident.services:
            return None
        have = ({r.service for r in self._st.logs} if source == "logs"
                else {m["service"] for m in self._st.metrics})
        if service in have:
            return None
        return BackendResult(status="unavailable",
                             error=f"no {source} were captured for service '{service}' in this incident")

    def search_logs(self, service=None, start=None, end=None, query=None, level=None, limit=20) -> BackendResult:
        g = self._gate("search_logs") or self._uncovered("logs", service)
        if g:
            return g
        limit = _limit(limit, 20)
        s, e = self._window(start, end)
        toks = _tokens(query)
        lv = (level or "").strip().upper()
        if lv in ("WARN",):
            lv = "WARNING"
        matched = []
        for r in self._st.logs:
            t = _ts(r.event_time)
            if t < s or t > e or (service and r.service != service):
                continue
            if lv and r.attributes["level"] != lv:
                continue
            sc = _score(r.content, toks) if toks else 0
            if toks and sc == 0:
                continue
            matched.append((sc, r))
        # collapse repeated lines of the same template; keep first occurrence
        groups: dict[str, dict] = {}
        for sc, r in matched:
            gk = r.attributes["group"]
            grp = groups.get(gk)
            if grp is None:
                groups[gk] = {"rec": r, "score": sc, "count": 1, "first": r.event_time, "last": r.event_time}
            else:
                grp["count"] += 1
                grp["last"] = r.event_time
                if sc > grp["score"]:
                    grp["rec"], grp["score"] = r, sc
        ordered = sorted(groups.values(), key=lambda g: (-g["score"], g["rec"].event_time, g["rec"].evidence_id))
        recs = []
        for grp in ordered:
            r = grp["rec"].model_copy(deep=True)
            content, cut = _clip(r.content, SEARCH_CHARS)
            r.content, r.truncated = content, cut
            attrs = {k: v for k, v in r.attributes.items() if k != "group"}
            if grp["count"] > 1:
                attrs.update({"similar_lines": grp["count"], "first_seen": grp["first"], "last_seen": grp["last"]})
                r.title = f"{r.title} (x{grp['count']} similar lines {grp['first']} .. {grp['last']})"[:200]
            r.attributes = attrs
            recs.append(r)
        # truncation is about groups returned vs groups found; line counts live in similar_lines (QA-F07)
        res = self._result(recs, total=len(recs), limit=limit)
        return self._done("search_logs", res)

    def search_traces(self, service=None, start=None, end=None, status=None, min_duration_ms=None,
                      limit=10) -> BackendResult:
        g = self._gate("search_traces")
        if g:
            return g
        limit = _limit(limit, 10)
        s, e = self._window(start, end)
        want = (status or "").lower() or None
        if want in ("failed", "failure", "errors"):
            want = "error"
        out = []
        for tid, sm in self._st.trace_summ.items():
            if sm["start"] < s or sm["start"] > e:
                continue
            if service and service not in sm["services"]:
                continue
            if want and sm["status"] != want:
                continue
            if min_duration_ms is not None and sm["duration_ms"] < float(min_duration_ms):
                continue
            out.append((sm["start"], tid))
        out.sort()
        return self._done("search_traces",
                          self._result([self._st.trace_record(t) for _, t in out[:limit]], total=len(out)))

    def get_trace(self, trace_id: str) -> BackendResult:
        g = self._gate("get_trace")
        if g:
            return g
        tid = str(trace_id).split(":", 1)[-1].strip()
        spans = self._st.traces.get(tid)
        if not spans:
            return self._done("get_trace", BackendResult(status="empty", total_matches=0,
                                                         error=f"trace {tid} not found"))
        recs = []
        for s in spans:
            body = (f"span {s['span_id']} parent={s.get('parent_span_id') or '-'} trace={tid} "
                    f"start={_norm_iso(s['start'])} " + self._st.span_line(s)
                    + " resource=" + json.dumps(s.get("resource", {}), separators=(",", ":")))
            body, cut = _clip(body, GET_CHARS)
            recs.append(EvidenceRecord(
                evidence_id=f"span:{s['span_id']}", source="traces", service=s["service"],
                event_time=_norm_iso(s["start"]), available_time=_norm_iso(s["end"]),
                title=f"{s['service']} {s['name']} {s['duration_ms']}ms {s['status']}", content=body,
                truncated=cut, content_ref=f"spans.jsonl#{s['span_id']}",
                attributes={"trace_id": tid, "parent_span_id": s.get("parent_span_id"), "kind": s["kind"],
                            "duration_ms": s["duration_ms"], "status": s["status"]}))
        return self._done("get_trace", self._result(recs))

    def get_metrics(self, service: str, metric=None, start=None, end=None) -> BackendResult:
        g = self._gate("get_metrics") or self._uncovered("metrics", service)
        if g:
            return g
        s, e = self._window(start, end)
        want = (metric or "").lower().strip()
        recs = []
        for m in self._st.metrics:
            if service and m["service"] != service:
                continue
            if want and want not in m["metric"].lower():
                continue
            pts = [(t, v) for t, v in m["pts"] if s <= t <= e]
            if not pts:
                continue
            vals = [v for _, v in pts]
            head = (f"{m['service']} {m['metric']} ({m['interval_s']}s points, {_iso(pts[0][0])} .. "
                    f"{_iso(pts[-1][0])}): min={min(vals):g} max={max(vals):g} last={vals[-1]:g}\n")
            series = "; ".join(f"{_iso(t)[11:19]} {v:g}" for t, v in pts)
            content, cut = _clip(head + series, GET_CHARS)
            recs.append(EvidenceRecord(
                evidence_id=m["evidence_id"], source="metrics", service=m["service"],
                event_time=_iso(pts[0][0]), available_time=_iso(pts[-1][0]),
                title=f"{m['service']} {m['metric']}", content=content, truncated=cut,
                content_ref=f"metrics.jsonl#{m['service']}/{m['metric']}",
                attributes={"metric": m["metric"], "points": len(pts), "min": min(vals), "max": max(vals),
                            "last": vals[-1]}))
        res = self._result(recs)
        if res.status == "empty":
            names = sorted({m["metric"] for m in self._st.metrics if not service or m["service"] == service})
            res.error = "no matching series; metrics for this service: " + ", ".join(names) if names else \
                "no metrics for this service"
        return self._done("get_metrics", res)

    def list_deployments(self, service=None, start=None, end=None) -> BackendResult:
        g = self._gate("list_deployments")
        if g:
            return g
        s, e = self._window(start, end)
        recs = [self._st.deployment_record(d, full=False) for d in self._st.deployments
                if (not service or d["service"] == service) and s <= _ts(d["started_at"]) <= e]
        return self._done("list_deployments", self._result(recs))

    def get_build(self, build_id: str) -> BackendResult:
        g = self._gate("get_build")
        if g:
            return g
        bid = str(build_id).split(":", 1)[-1].strip()
        b = self._st.builds.get(bid)
        if not b:
            return self._done("get_build", BackendResult(status="empty", total_matches=0,
                                                         error=f"build {bid} not found"))
        text = (f"build {b['build_id']} service={b['service']} version={b['version']} commit={b['commit']} "
                f"artifact={b['artifact']} artifact_digest={b['artifact_digest']} bytes={b['artifact_bytes']} "
                f"status={b['status']} pipeline={b['pipeline']} started={_norm_iso(b['started_at'])} "
                f"finished={_norm_iso(b['finished_at'])} source_paths={','.join(b['source_paths'])}")
        rec = EvidenceRecord(
            evidence_id=f"build:{b['build_id']}", source="builds", service=b["service"],
            event_time=_norm_iso(b["started_at"]), available_time=_norm_iso(b["finished_at"]),
            version=b["version"], title=f"build {b['build_id']} of {b['service']} {b['version']}",
            content=self._st.scrub(text), content_ref=f"builds.jsonl#{b['build_id']}",
            attributes={"commit": b["commit"][:7], "artifact_digest": b["artifact_digest"]})
        return self._done("get_build", self._result([rec]))

    def search_commits(self, service=None, path=None, query=None, since=None, until=None,
                       limit=10) -> BackendResult:
        g = self._gate("search_commits")
        if g:
            return g
        limit = _limit(limit, 10)
        s = _ts(since) if since else None
        u = min(_ts(until), self._st.cutoff) if until else self._st.cutoff
        toks = _tokens(query)
        hits = []
        for c in self._st.commits:
            t = _ts(c["committer_date"])
            if (s is not None and t < s) or t > u:
                continue
            if service and service not in c["services"]:
                continue
            if path and not any(path.strip("/") in f["path"] for f in c["files"]):
                continue
            sc = 0
            if toks:
                text = self._st.scrub(c["message"] + " " + " ".join(f["path"] for f in c["files"]) + " "
                                      + c["author"] + " " + c["diff"][:2000])
                sc = _score(text, toks)
                if sc == 0:
                    continue
            hits.append((-sc, -t, c["sha"], c))
        hits.sort(key=lambda x: x[:3])
        recs = [self._st.commit_record(c, full=False) for *_, c in hits[:limit]]
        return self._done("search_commits", self._result(recs, total=len(hits)))

    def get_commit(self, sha: str) -> BackendResult:
        g = self._gate("get_commit")
        if g:
            return g
        want = str(sha).split(":", 1)[-1].strip().lower()
        if len(want) < 4:
            return self._done("get_commit", BackendResult(status="empty", total_matches=0,
                                                          error="sha prefix too short"))
        hits = [c for c in self._st.commits if c["sha"].startswith(want)]
        if not hits:
            return self._done("get_commit", BackendResult(status="empty", total_matches=0,
                                                          error=f"commit {want} not found"))
        return self._done("get_commit", self._result([self._st.commit_record(hits[0], full=True)]))

    def get_config(self, service: str, version=None) -> BackendResult:
        g = self._gate("get_config")
        if g:
            return g
        st = self._st
        vers = [c for c in st.configs if c["service"] == service]
        if not vers:
            return self._done("get_config", BackendResult(status="empty", total_matches=0,
                                                          error=f"no config versions for {service}"))
        chosen, note = None, ""
        if version:
            v = str(version).split(":", 1)[-1].split("@")[-1].strip().lower()
            for c in vers:
                if len(v) >= 4 and c["commit"].startswith(v):
                    chosen, note = c, f"config at commit {c['commit'][:7]}"
            if chosen is None and self.ablation != "no_provenance":
                for d in st.deployments:
                    if d["service"] == service and d["version"].lower() == v:
                        chosen = self._config_for_commit(vers, d["commit"])
                        note = f"config shipped with {service} {d['version']} ({d['deploy_id']})"
            if chosen is None and len(v) >= 4:
                c = next((c for c in st.commits if c["sha"].startswith(v)), None)
                if c:
                    chosen = self._config_for_commit(vers, c["sha"])
                    note = f"config in effect at commit {c['sha'][:7]}"
            if chosen is None:
                return self._done("get_config", BackendResult(status="empty", total_matches=0,
                                                              error=f"no config for {service} at {version}"))
        elif self.ablation == "no_provenance" or "deployments" in st.unavailable:
            chosen, note = vers[-1], "latest repository version at the cutoff (deployed version unknown)"
        else:
            deps = [d for d in st.deployments if d["service"] == service]
            if deps:
                chosen = self._config_for_commit(vers, deps[-1]["commit"])
                note = f"config of the running deployment {deps[-1]['deploy_id']} (commit {deps[-1]['commit'][:7]})"
            else:
                chosen, note = vers[-1], "latest repository version at the cutoff"
        diff = ""
        if chosen.get("previous"):
            diff = "".join(difflib.unified_diff(chosen["previous"].splitlines(True), chosen["content"].splitlines(True),
                                                "previous", chosen["commit"][:7], n=1))
        text = (f"{chosen['path']} @ {chosen['commit'][:7]} (committed {_norm_iso(chosen['committed_at'])}); {note}\n"
                f"{chosen['content']}" + (f"\nchange vs previous version:\n{diff}" if diff else ""))
        text, cut = _clip(st.scrub(text), GET_CHARS)
        rec = EvidenceRecord(
            evidence_id=f"config:{service}@{chosen['commit'][:7]}", source="configs", service=service,
            event_time=_norm_iso(chosen["committed_at"]), available_time=_norm_iso(chosen["committed_at"]),
            version=chosen["commit"][:7], title=f"{chosen['path']} @ {chosen['commit'][:7]}", content=text,
            truncated=cut, content_ref=f"configs.jsonl#{service}@{chosen['commit']}", fixture=True,
            attributes={"versions": [c["commit"][:7] for c in vers], "path": chosen["path"]})
        return self._done("get_config", self._result([rec]))

    def _config_for_commit(self, vers, sha):
        cm = next((c for c in self._st.commits if c["sha"] == sha), None)
        if cm is None:
            return vers[-1]
        t = _ts(cm["committer_date"])
        older = [c for c in vers if _ts(c["committed_at"]) <= t]
        return older[-1] if older else vers[0]

    def search_tickets(self, query=None, service=None, limit=5) -> BackendResult:
        g = self._gate("search_tickets")
        if g:
            return g
        limit = _limit(limit, 5)
        toks = _tokens(query)
        hits = []
        for t in self._st.tickets:
            if service and service not in t["components"]:
                continue
            sc = 0
            if toks:
                text = " ".join([t["key"], t["summary"], t["description"], " ".join(t["components"])]
                                + [c["body"] for c in t["comments"]])
                sc = _score(text, toks)
                if sc == 0:
                    continue
            hits.append((-sc, -_ts(t["created"]), t["key"], t))
        hits.sort(key=lambda x: x[:3])
        recs = [self._st.ticket_record(t, full=False) for *_, t in hits[:limit]]
        return self._done("search_tickets", self._result(recs, total=len(hits)))

    def get_ticket(self, key: str) -> BackendResult:
        g = self._gate("get_ticket")
        if g:
            return g
        k = str(key).split(":", 1)[-1].split("#")[0].strip().upper()
        t = next((t for t in self._st.tickets if t["key"] == k), None)
        if not t:
            return self._done("get_ticket", BackendResult(status="empty", total_matches=0,
                                                          error=f"ticket {k} not found"))
        recs = [self._st.ticket_record(t, full=True)] + [self._st.comment_record(t, c) for c in t["comments"]]
        return self._done("get_ticket", self._result(recs))

    def export_logs(self, start=None, end=None) -> list[EvidenceRecord]:
        """Full log window for baseline B0 (oldest first). Not a model tool; not subject to the
        simulated connector failure, which models a failure *during* an iterative investigation."""
        if "logs" in self._st.unavailable:
            return []
        s, e = self._window(start, end)
        out = []
        for r in self._st.logs:
            if s <= _ts(r.event_time) <= e:
                c = r.model_copy(deep=True)
                c.content, c.truncated = _clip(c.content, SEARCH_CHARS)
                c.attributes = {k: v for k, v in c.attributes.items() if k != "group"}
                out.append(c)
        return out


# --------------------------------------------------------------------------- entry points
def _root(snapshot_root: Optional[Path]) -> Path:
    if snapshot_root is not None:
        return Path(snapshot_root)
    env = os.environ.get("BLACKBOX_SNAPSHOT_ROOT")
    return Path(env) if env else DEFAULT_ROOT


def list_incidents(snapshot_root: Optional[Path] = None) -> list[str]:
    root = _root(snapshot_root)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "incident.json").exists())


def load_backend(incident_id: str, ablation: Ablation = "none",
                 snapshot_root: Optional[Path] = None) -> SnapshotBackend:
    if not re.fullmatch(r"INC-\d{4}", incident_id or ""):
        raise ValueError(f"invalid incident id {incident_id!r}")
    snap = _root(snapshot_root) / incident_id
    if not (snap / "incident.json").exists():
        raise FileNotFoundError(f"no snapshot for {incident_id} under {snap.parent}")
    return SnapshotBackend(incident_id, ablation, snap)
