"""Build scenario snapshots from real captures.

    python -m scenarios.build pilot            # one scenario (slugs: pilot, s1..s9)
    python -m scenarios.build --all            # pilot + nine evaluation scenarios
    python -m scenarios.build s3 --freeze      # verify, then freeze (answer key + manifest frozen_at)
    python -m scenarios.build s1 --from-capture  # re-normalise an existing raw capture

Pipeline per scenario: run the capture (demo.capture: real services, real load, real
changes) -> evaluate the alert rule over the captured metrics -> normalise into a
model-visible snapshot under scenarios/snapshots/<incident_id>/ -> write the PRIVATE
answer key and manifest entry under evaluator_private/.
A frozen snapshot is never rewritten; rebuilding requires a new incident id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from blackbox.schemas import SCHEMA_VERSION, Alert, AnswerKey, Incident
from demo.alerts import evaluate
from demo.capture import run_capture
from demo.controller_plans import ORDER, PLANS
from demo.paths import CAPTURES, REPO
from demo.timeutil import iso, iso_now, parse
from scenarios.fixtures_tickets import build_tickets

SNAPSHOTS = REPO / "scenarios" / "snapshots"
PRIVATE = REPO / "evaluator_private"
KEYS = PRIVATE / "answer_keys"
MANIFEST = PRIVATE / "scenario_manifest.json"
CUTOFF_AFTER_ALERT_S = 60
ALL_SOURCES = ["logs", "traces", "metrics", "deployments", "builds", "commits", "configs", "tickets"]


def h(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def _jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _write_jsonl(path: Path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def services_for_paths(paths) -> list[str]:
    out = set()
    for p in paths:
        if p.startswith("services/checkout") or p == "config/checkout-svc.yaml":
            out.add("checkout-svc")
        elif p.startswith("services/payment") or p.startswith("db/") or p == "config/payment-svc.yaml":
            out.add("payment-svc")
        elif p.startswith("libs/"):
            out.update(["checkout-svc", "payment-svc"])
    return sorted(out)


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"incidents": {}}


def save_manifest(m: dict):
    PRIVATE.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2), encoding="utf-8")


def _materialise_time(ref, anchors) -> str:
    kind, val = ref
    if kind == "abs":
        return iso(parse(val))
    return iso(anchors[kind] + float(val))


def materialise_tickets(set_name: str, anchors: dict) -> list[dict]:
    out = []
    for t in build_tickets(set_name):
        t = json.loads(json.dumps(t))  # deep copy (tuples -> lists)
        conv = lambda r: _materialise_time(tuple(r), anchors)  # noqa: E731
        t["created"] = conv(t["created"])
        for d in t["description_versions"]:
            d["at"] = conv(d["at"])
        for s in t["status_history"]:
            s["at"] = conv(s["at"])
        for c in t["comments"]:
            c["at"] = conv(c["at"])
        t["fixture"] = True
        out.append(t)
    return out


# --------------------------------------------------------------------------- snapshot
def write_snapshot(inc_id: str, cdir: Path, meta: dict, alert: dict, cutoff: float,
                   withhold: dict | None, tickets_set: str, runtime_cfg: dict | None,
                   notes: str = "Alert fired in demo-local. Investigate using the available evidence sources.") -> list:
    """Normalise one raw capture directory into scenarios/snapshots/<inc_id>/. Returns available sources."""
    snap = SNAPSHOTS / inc_id
    tdir = cdir / "telemetry"
    fired = parse(alert["fired_at"])
    window_start = parse(meta["t_start"])
    events = meta["events"]
    first_change = parse(events[0]["at"]) if events else parse(meta["load_start"]) + 60
    anchors = {"t0": window_start, "change": first_change, "alert": fired, "cutoff": cutoff}

    wh = withhold or {}
    drop_sources = set(wh.get("sources", []))
    drop_log_services = set(wh.get("log_services", []))
    drop_metrics = wh.get("metrics", {})

    if snap.exists():
        shutil.rmtree(snap)
    snap.mkdir(parents=True)

    services = ["checkout-svc", "payment-svc"]
    # logs
    logs = []
    if "logs" not in drop_sources:
        for svc in services:
            if svc in drop_log_services:
                continue
            for i, rec in enumerate(_jsonl(tdir / "logs" / f"{svc}.jsonl")):
                logs.append({"evidence_id": f"log:{h(inc_id, 'log', svc, i)}", "service": svc,
                             "line": i, "ts": rec["ts"], "raw": rec})
        logs.sort(key=lambda r: (r["ts"], r["service"], r["line"]))
    _write_jsonl(snap / "logs.jsonl", logs)
    # spans
    spans = []
    if "traces" not in drop_sources:
        for svc in services:
            for rec in _jsonl(tdir / "spans" / f"{svc}.jsonl"):
                spans.append({"evidence_id": f"span:{rec['span_id']}", **rec})
        spans.sort(key=lambda r: (r["start"], r["span_id"]))
    _write_jsonl(snap / "spans.jsonl", spans)
    # metrics (one record per series)
    series = {}
    for svc in services:
        dm = drop_metrics.get(svc, [])
        for rec in _jsonl(tdir / "metrics" / f"{svc}.jsonl"):
            if dm == "*" or rec["metric"] in dm:
                continue
            key = (svc, rec["metric"])
            s = series.setdefault(key, {"evidence_id": f"metric:{h(inc_id, 'metric', svc, rec['metric'])}",
                                        "service": svc, "metric": rec["metric"],
                                        "interval_s": rec.get("interval_s", 5), "points": []})
            s["points"].append([rec["ts"], rec["value"]])
    mrows = [series[k] for k in sorted(series)]
    for s in mrows:
        s["points"].sort()
    _write_jsonl(snap / "metrics.jsonl", mrows)
    # deployments and builds (produced by the build/deploy tooling during the capture)
    deps = [{"evidence_id": f"deploy:{d['deploy_id']}", **d} for d in _jsonl(cdir / "deployments.jsonl")]
    _write_jsonl(snap / "deployments.jsonl", deps)
    builds = []
    for p in sorted((cdir / "builds").glob("*.json")):
        b = json.loads(p.read_text(encoding="utf-8"))
        builds.append({"evidence_id": f"build:{b['build_id']}", **b})
    _write_jsonl(snap / "builds.jsonl", builds)
    # commits and configs (exported from the company repo at capture end)
    export = json.loads((cdir / "repo_export.json").read_text(encoding="utf-8"))
    commits = []
    for c in export["commits"]:
        commits.append({"evidence_id": f"commit:{c['sha'][:7]}", **c,
                        "services": services_for_paths([f["path"] for f in c["files"]]),
                        "fixture": True})
    _write_jsonl(snap / "commits.jsonl", commits)
    configs = []
    if "configs" not in drop_sources:
        for c in export["configs"]:
            configs.append({"evidence_id": f"config:{c['service']}@{c['commit'][:7]}", **c})
    _write_jsonl(snap / "configs.jsonl", configs)
    # tickets (team-authored fixtures, timestamps aligned to this capture)
    tickets = materialise_tickets(tickets_set, anchors)
    (snap / "tickets.json").write_text(json.dumps(tickets, indent=1, ensure_ascii=False), encoding="utf-8")
    # runtime settings for the evidence backend (connector behaviour, coverage notes)
    coverage = {}
    if drop_log_services:
        coverage["logs"] = "logs available for: " + ", ".join(s for s in services if s not in drop_log_services)
    for svc, dm in drop_metrics.items():
        if dm == "*":
            coverage["metrics"] = "metrics available for: " + ", ".join(s for s in services if s != svc)
    runtime = {"failures": (runtime_cfg or {}).get("failures", []), "coverage": coverage}
    (snap / "runtime.json").write_text(json.dumps(runtime, indent=1), encoding="utf-8")

    available = [s for s in ALL_SOURCES if s not in drop_sources]
    incident = Incident(
        incident_id=inc_id, alert=Alert(**alert), window_start=iso(window_start),
        window_end=iso(cutoff), investigation_cutoff=iso(cutoff), environment="demo-local",
        services=services, available_sources=available,
        notes=notes)
    (snap / "incident.json").write_text(incident.model_dump_json(indent=2), encoding="utf-8")
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(snap.iterdir())}
    (snap / "manifest.json").write_text(json.dumps(
        {"incident_id": inc_id, "schema_version": SCHEMA_VERSION, "built_at": iso_now(),
         "files": files, "frozen_at": None}, indent=2), encoding="utf-8")
    return available


def build_snapshot(slug: str, log=print, from_capture: bool = False) -> dict:
    plan = PLANS[slug]
    inc_id = plan["incident_id"]
    snap = SNAPSHOTS / inc_id
    man = load_manifest()
    entry = man["incidents"].get(inc_id, {})
    if entry.get("frozen"):
        raise SystemExit(f"{inc_id} is frozen; rebuilds need a new incident id")

    if plan.get("reuse_capture_of") or from_capture:
        src_id = PLANS[plan["reuse_capture_of"]]["incident_id"] if plan.get("reuse_capture_of") else inc_id
        cdir = CAPTURES / src_id
        if not cdir.exists():
            raise SystemExit(f"capture of {src_id} not found; build {plan['reuse_capture_of']} first")
        meta = json.loads((cdir / "capture_meta.json").read_text(encoding="utf-8"))
    else:
        meta = run_capture(plan["capture"], inc_id, log=log)
        from demo import workspace
        workspace.tag(f"capture/{inc_id}")
        cdir = CAPTURES / inc_id
    tdir = cdir / "telemetry"

    alert = evaluate(plan["alert"], tdir / "metrics", not_before=iso(parse(meta["load_start"]) + 20))
    if alert is None:
        raise SystemExit(f"{slug}: alert rule did not fire in the capture; not building a snapshot")
    fired = parse(alert["fired_at"])
    cutoff = fired + CUTOFF_AFTER_ALERT_S
    if cutoff > parse(meta["t_end"]) - 5:
        raise SystemExit(f"{slug}: capture too short for cutoff")
    available = write_snapshot(inc_id, cdir, meta, alert, cutoff, plan.get("withhold"), plan["tickets"],
                               plan.get("runtime"))

    # ------------------------------------------------------------- private answer key
    key = make_answer_key(slug, plan, snap, meta, cutoff, available)
    KEYS.mkdir(parents=True, exist_ok=True)
    (KEYS / f"{inc_id}.json").write_text(key.model_dump_json(indent=2), encoding="utf-8")
    (PRIVATE / "captures").mkdir(parents=True, exist_ok=True)
    (PRIVATE / "captures" / f"{inc_id}_capture_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    man = load_manifest()  # reload: other entries may have changed (e.g. frozen) during the capture
    man["incidents"][inc_id] = {
        "slug": slug, "scenario_number": plan["number"], "scenario_type": plan["type"],
        "variant_of": PLANS[plan["variant_of"]]["incident_id"] if plan.get("variant_of") else None,
        "frozen": False, "frozen_at": None, "superseded_by": None,
        "build_command": f"python -m scenarios.build {slug}",
        "capture_id": meta["capture_id"], "capture_start": meta["t_start"], "capture_end": meta["t_end"],
        "alert_fired_at": alert["fired_at"], "investigation_cutoff": iso(cutoff),
        "replay_of_capture": PLANS[plan["reuse_capture_of"]]["incident_id"] if plan.get("reuse_capture_of") else None,
        "status": "built",
    }
    save_manifest(man)
    log(f"[{slug}] snapshot {snap} alert fired {alert['fired_at']} cutoff {iso(cutoff)}")
    return man["incidents"][inc_id]


def make_answer_key(slug, plan, snap: Path, meta: dict, cutoff: float, available) -> AnswerKey:
    from scenarios.resolve import resolve_selectors
    decisive, equivalents = resolve_selectors(plan["decisive"], snap, meta, cutoff)
    distract, _ = resolve_selectors(plan.get("distractors", []), snap, meta, cutoff)
    a = plan["answer"]
    from demo.controller_faults import CHANGES
    applied = [{"change_id": e["change_id"], "family": e["family"], "kind": e["kind"], "at": e["at"],
                "commit": e.get("commit"), "builds": e.get("builds"), "deployments": e.get("deployments"),
                "env": e.get("env")} for e in meta["events"]]
    faults = [x for x in applied if CHANGES[x["change_id"]]["family"] not in ("harmless",)
              and not x["change_id"].startswith(("h-", "d-"))]
    return AnswerKey(
        incident_id=plan["incident_id"], scenario_number=plan["number"], scenario_type=plan["type"],
        is_primary=a["is_primary"], diagnosable=a["diagnosable"],
        variant_of=PLANS[plan["variant_of"]]["incident_id"] if plan.get("variant_of") else None,
        expected_status=a["expected_status"], component=a.get("component"), mechanism=a.get("mechanism"),
        accepted_components=a.get("accepted_components", []),
        accepted_mechanisms=a.get("accepted_mechanisms", []),
        decisive_evidence_ids=decisive, distractor_evidence_ids=distract,
        injected_fault={"faults": faults, "all_changes": applied,
                        "decisive_equivalents": equivalents,
                        "withheld": plan.get("withhold", {}),
                        "connector_runtime": plan.get("runtime", {})},
        available_sources=available,
        remaining_evidence_sufficient=a.get("remaining_evidence_sufficient"),
        notes=plan.get("notes", ""), frozen_at=None)


def sync_frozen() -> list[str]:
    """Re-assert frozen flags from the answer keys (the keys are the record of a freeze)."""
    man = load_manifest()
    fixed = []
    for p in sorted(KEYS.glob("INC-*.json")):
        key = AnswerKey.model_validate_json(p.read_text(encoding="utf-8"))
        ent = man["incidents"].get(key.incident_id)
        if key.frozen_at and ent and not ent.get("frozen"):
            ent["frozen"], ent["frozen_at"], ent["status"] = True, key.frozen_at, "frozen"
            fixed.append(key.incident_id)
    save_manifest(man)
    return fixed


def freeze(slugs) -> int:
    from scenarios.verify import verify_incident
    bad = 0
    for s in slugs:
        inc_id = PLANS[s]["incident_id"]
        man = load_manifest()
        ent = man["incidents"].get(inc_id)
        if not ent or ent.get("status") != "built":
            print(f"[{s}] not built; cannot freeze")
            bad += 1
            continue
        if ent.get("frozen"):
            print(f"[{s}] already frozen at {ent['frozen_at']}")
            continue
        errs = verify_incident(inc_id)
        if errs:
            print(f"[{s}] verify failed; not freezing:", *errs[:10], sep="\n  ")
            bad += 1
            continue
        now = iso_now()
        kp = KEYS / f"{inc_id}.json"
        key = AnswerKey.model_validate_json(kp.read_text(encoding="utf-8"))
        key.frozen_at = now
        kp.write_text(key.model_dump_json(indent=2), encoding="utf-8")
        sm = SNAPSHOTS / inc_id / "manifest.json"
        m = json.loads(sm.read_text(encoding="utf-8"))
        m["frozen_at"] = now
        sm.write_text(json.dumps(m, indent=2), encoding="utf-8")
        ent["frozen"], ent["frozen_at"], ent["status"] = True, now, "frozen"
        save_manifest(man)
        print(f"[{s}] {inc_id} frozen at {now}")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--freeze", action="store_true", help="verify, then freeze the given scenarios")
    ap.add_argument("--sync-frozen", action="store_true", help="restore manifest frozen flags from answer keys")
    ap.add_argument("--from-capture", action="store_true",
                    help="re-normalise the existing raw capture instead of running a new one")
    args = ap.parse_args(argv)
    if args.sync_frozen:
        print("re-frozen in manifest:", sync_frozen())
        return
    slugs = ORDER if args.all else args.slugs
    if not slugs:
        ap.error("give slugs or --all")
    if args.freeze:
        sys.exit(freeze(slugs))
    failed = 0
    for s in slugs:
        if load_manifest()["incidents"].get(PLANS[s]["incident_id"], {}).get("frozen"):
            print(f"[{s}] frozen, skipping")
            continue
        try:
            build_snapshot(s, from_capture=args.from_capture)
        except (SystemExit, Exception) as e:  # record the blocker, continue with the rest
            failed += 1
            msg = f"{type(e).__name__}: {e}"
            print(f"[{s}] FAILED: {msg}")
            man = load_manifest()
            ent = man["incidents"].setdefault(PLANS[s]["incident_id"], {
                "slug": s, "scenario_number": PLANS[s]["number"], "scenario_type": PLANS[s]["type"],
                "frozen": False, "build_command": f"python -m scenarios.build {s}"})
            ent["status"] = "pending"
            ent["blocker"] = msg[:500]
            save_manifest(man)
        sys.stdout.flush()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
