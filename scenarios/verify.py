"""Verify built snapshots and answer keys.

    python -m scenarios.verify [INC-XXXX ...]

Checks per incident:
  schema     incident.json validates as Incident, answer key as AnswerKey, opaque id
  files      every file listed in manifest.json matches its sha256 (frozen integrity)
  leakage    no private change id / fault family / controller vocabulary in any snapshot
             file name or file content
  decisive   every decisive/distractor evidence id exists in the snapshot and is visible
             at the cutoff
  cutoff     the snapshot holds records after the cutoff, and the evidence backend
             (public API only) never returns any of them
  ablation   each ablation removes its source from source_status and from every method,
             including indirect mentions (ticket keys, provenance fields)
Exit code 1 on any failure.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from blackbox.schemas import AnswerKey, Incident
from demo.controller_faults import CHANGES
from demo.timeutil import parse

REPO = Path(__file__).resolve().parents[1]
SNAPSHOTS = REPO / "scenarios" / "snapshots"
KEYS = REPO / "evaluator_private" / "answer_keys"

# Controller vocabulary: checked everywhere, including attacker-controlled text.
CONTROLLER_WORDS = [r"\binject", r"controller", r"answer[_ ]key", r"evaluator", r"connector[_ ]failure",
                    r"prompt[_ ]injection", r"ground[_ ]truth"]
# Generic scenario vocabulary: checked everywhere EXCEPT attacker-controlled text that a scenario plants
# on purpose (order notes / imported ticket comments listed in the plan). Such text may say e.g.
# "no fault" without revealing the hidden cause.
GENERIC_WORDS = [r"\bfault", r"\bscenario", r"\bpilot\b", r"misleading", r"distractor", r"\bdecoy",
                 r"\bharmless", r"insufficient",
                 # retired fictional personas must not appear in any new snapshot
                 r"Maya Chen", r"Priya Nair", r"Lena Hoffmann", r"Tom Becker", r"Rafael Ortiz"]
FORBIDDEN_WORDS = CONTROLLER_WORDS + GENERIC_WORDS


def controller_patterns():
    pats = [re.compile(w, re.I) for w in CONTROLLER_WORDS]
    for cid, ch in CHANGES.items():
        pats.append(re.compile(re.escape(cid), re.I))
        if ch["family"] not in ("harmless",):
            pats.append(re.compile(r"\b" + re.escape(ch["family"]) + r"\b", re.I))
    return pats


def generic_patterns():
    return [re.compile(w, re.I) for w in GENERIC_WORDS]


def forbidden_patterns():
    return controller_patterns() + generic_patterns()


def planted_texts(inc_id: str) -> list[str]:
    """Attacker-controlled strings a scenario plants on purpose (from the private plan)."""
    from demo.controller_plans import PLANS
    out = []
    for plan in PLANS.values():
        if plan["incident_id"] != inc_id:
            continue
        cap = plan.get("capture")
        if cap is not None:
            out += [o["note"] for o in cap.special_orders.values() if o.get("note")]
        if plan.get("tickets"):
            from scenarios.fixtures_tickets import SETS
            for t in SETS[plan["tickets"]]():
                out += [c["body"] for c in t["comments"] if "(integration)" in c["author"]]
    return out


def _strip_planted(text: str, planted: list[str]) -> str:
    for p in planted:
        text = text.replace(p, " ").replace(json.dumps(p, ensure_ascii=False)[1:-1], " ")
        text = text.replace(p[:390], " ")  # checkout logs notes truncated to 400 chars
    return text


def _jsonl(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def snapshot_ids(snap: Path) -> dict[str, float]:
    """evidence id -> available time (epoch) for everything stored in the snapshot."""
    ids = {}
    for r in _jsonl(snap / "logs.jsonl"):
        ids[r["evidence_id"]] = parse(r["ts"])
    for s in _jsonl(snap / "spans.jsonl"):
        ids[s["evidence_id"]] = parse(s["end"])
        t = ids.get(f"trace:{s['trace_id']}")
        ids[f"trace:{s['trace_id']}"] = parse(s["end"]) if t is None else min(t, parse(s["end"]))
    for m in _jsonl(snap / "metrics.jsonl"):
        ids[m["evidence_id"]] = parse(m["points"][0][0])
    for d in _jsonl(snap / "deployments.jsonl"):
        ids[d["evidence_id"]] = parse(d["finished_at"])
    for b in _jsonl(snap / "builds.jsonl"):
        ids[b["evidence_id"]] = parse(b["finished_at"])
    for c in _jsonl(snap / "commits.jsonl"):
        ids[c["evidence_id"]] = parse(c["committer_date"])
    for c in _jsonl(snap / "configs.jsonl"):
        ids[c["evidence_id"]] = parse(c["committed_at"])
    if (snap / "tickets.json").exists():
        for t in json.loads((snap / "tickets.json").read_text(encoding="utf-8")):
            ids[f"jira:{t['key']}"] = parse(t["created"])
            for c in t["comments"]:
                ids[f"jira:{t['key']}#{c['id']}"] = parse(c["at"])
    return ids


def post_cutoff_ids(snap: Path, cutoff: float) -> set[str]:
    return {i for i, t in snapshot_ids(snap).items() if t > cutoff}


def _sweep(be, inc: Incident) -> list:
    """Call every backend method broadly; return all records seen."""
    recs = []

    def take(res):
        if res is not None and getattr(res, "records", None):
            recs.extend(res.records)

    for svc in [None] + inc.services:
        take(be.search_logs(service=svc, limit=50))
        take(be.search_logs(service=svc, level="ERROR", limit=50))
        take(be.search_traces(service=svc, limit=50))
        take(be.search_traces(service=svc, status="error", limit=50))
        take(be.search_commits(service=svc, limit=50))
        take(be.search_tickets(service=svc, limit=50))
        take(be.list_deployments(service=svc))
    for svc in inc.services:
        take(be.get_metrics(svc))
        take(be.get_config(svc))
    for r in list(recs):
        if r.evidence_id.startswith("trace:"):
            take(be.get_trace(r.evidence_id))
        if r.evidence_id.startswith("commit:"):
            take(be.get_commit(r.evidence_id))
        if r.evidence_id.startswith("jira:") and "#" not in r.evidence_id:
            take(be.get_ticket(r.evidence_id))
        if r.evidence_id.startswith("deploy:") and r.attributes.get("build_id"):
            take(be.get_build(r.attributes["build_id"]))
    recs.extend(be.export_logs())
    return recs


def verify_incident(inc_id: str, use_backend: bool = True) -> list[str]:
    errs = []
    snap = SNAPSHOTS / inc_id
    if not re.fullmatch(r"INC-\d{4}", inc_id):
        errs.append("incident id not opaque INC-NNNN")
    try:
        inc = Incident.model_validate_json((snap / "incident.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return [f"incident.json invalid: {e}"]
    key = None
    kp = KEYS / f"{inc_id}.json"
    try:
        key = AnswerKey.model_validate_json(kp.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        errs.append(f"answer key invalid/missing: {e}")
    man = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in man["files"].items():
        p = snap / name
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            errs.append(f"file hash mismatch: {name}")
    # leakage (names + content)
    pats = forbidden_patterns()
    cpats, gpats = controller_patterns(), generic_patterns()
    planted = planted_texts(inc_id)
    for p in sorted(snap.rglob("*")):
        if not p.is_file():
            continue
        for pat in pats:
            if pat.search(p.name):
                errs.append(f"forbidden token {pat.pattern!r} in file name {p.name}")
        full = p.read_text(encoding="utf-8", errors="replace")
        stripped = _strip_planted(full, planted)
        for pat, text in [(c, full) for c in cpats] + [(g, stripped) for g in gpats]:
            m = pat.search(text)
            if m:
                ctx = text[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
                errs.append(f"forbidden token {pat.pattern!r} in {p.name}: ...{ctx}...")
    cutoff = parse(inc.investigation_cutoff)
    ids = snapshot_ids(snap)
    if key:
        for i in key.decisive_evidence_ids + key.distractor_evidence_ids:
            if i not in ids:
                errs.append(f"evidence id {i} not in snapshot")
            elif ids[i] > cutoff:
                errs.append(f"evidence id {i} only available after cutoff")
    late = post_cutoff_ids(snap, cutoff)
    if not late:
        errs.append("no post-cutoff records in snapshot (cutoff not exercised)")
    if not use_backend:
        return errs
    try:
        from blackbox.evidence.backend import load_backend
    except Exception as e:  # noqa: BLE001
        errs.append(f"backend import failed: {e}")
        return errs
    try:
        be = load_backend(inc_id, "none")
        seen = _sweep(be, inc)
        leaked = sorted({r.evidence_id for r in seen} & late)
        if leaked:
            errs.append(f"backend returned post-cutoff records: {leaked[:5]}")
        for r in seen:
            if r.available_time and parse(r.available_time) > cutoff + 0.001:
                errs.append(f"backend record {r.evidence_id} available_time after cutoff")
                break
        for r in seen:
            body = r.content + "\n" + r.title
            for pat, text in [(c, body) for c in cpats] + [(g, _strip_planted(body, planted)) for g in gpats]:
                if pat.search(text):
                    errs.append(f"forbidden token {pat.pattern!r} in backend record {r.evidence_id}")
        # ablations
        ticket_re = re.compile(r"\b(?:DEMO|OPS)-\d+\b")
        for abl, src in (("no_jira", "tickets"), ("no_provenance", "builds"), ("no_traces", "traces")):
            b = load_backend(inc_id, abl)
            st = {s.source: s for s in b.source_status()}
            if st[src].available:
                errs.append(f"{abl}: source_status still reports {src} available")
            recs = _sweep(b, inc)
            if any(r.source == src for r in recs):
                errs.append(f"{abl}: records from {src} still returned")
            blob = "\n".join(r.content + " " + r.title + " " + json.dumps(r.attributes, default=str) for r in recs)
            if abl == "no_jira" and ticket_re.search(blob):
                errs.append(f"no_jira: ticket key still visible: {ticket_re.search(blob).group(0)}")
            r_t = b.search_tickets()
            if abl == "no_jira" and (r_t.status in ("ok", "empty") or r_t.records):
                errs.append(f"no_jira: search_tickets returned status={r_t.status}")
            if abl == "no_provenance":
                for r in recs:
                    if r.source == "deployments" and (r.version or "build_id" in r.attributes
                                                      or "commit" in r.attributes or "b-1" in r.content):
                        errs.append(f"no_provenance: deployment {r.evidence_id} still carries provenance")
                        break
                if re.search(r'"(?:commit|build_id|artifact_digest)":"(?!\[redacted\])', blob):
                    errs.append("no_provenance: commit/build/digest still printed in a log line")
            r_tr = b.search_traces()
            if abl == "no_traces" and (r_tr.status in ("ok", "empty") or r_tr.records):
                errs.append(f"no_traces: search_traces returned status={r_tr.status}")
    except Exception as e:  # noqa: BLE001
        errs.append(f"backend check crashed: {type(e).__name__}: {e}")
    return errs


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    no_backend = "--no-backend" in argv
    ids = [a for a in argv if not a.startswith("--")] or sorted(
        p.name for p in SNAPSHOTS.iterdir() if (p / "incident.json").exists())
    bad = 0
    for i in ids:
        errs = verify_incident(i, use_backend=not no_backend)
        print(f"{i}: {'OK' if not errs else 'FAIL'}")
        for e in errs[:30]:
            print("   -", e)
        bad += bool(errs)
    print(f"{len(ids) - bad}/{len(ids)} incidents passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
