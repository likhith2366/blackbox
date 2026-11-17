"""Turn the current UI demo session into a DEMO incident snapshot (development only).

Used by demo/control_api.py (POST /api/incidents/snapshot). A demo session starts with
POST /api/reset (healthy baseline, fresh telemetry directory) and contains whatever really
happened afterwards: shop orders, generated traffic, injected faults. This module copies the
session's real telemetry and the tooling's provenance into a capture directory, evaluates the
standard alert rules over the captured metrics, and writes a normal snapshot with an opaque id
INC-9xxx. Demo incidents are labelled development in the manifest and are never frozen; the
fault identity stays in evaluator_private/ (capture meta + answer key).
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from blackbox.schemas import AnswerKey
from demo import workspace
from demo.alerts import AlertRule, evaluate
from demo.capture import write_provenance
from demo.controller import PRIVATE_STATE
from demo.controller_plans import PLANS
from demo.paths import CAPTURES, load_state
from demo.timeutil import iso, parse
from scenarios import fixtures_tickets as ft
from scenarios.build import (KEYS, PRIVATE, SNAPSHOTS, load_manifest, write_snapshot)

# demo incidents live in their own manifest: the frozen eval harness requires a scenario number for
# every entry of evaluator_private/scenario_manifest.json, and demo incidents have none
DEMO_MANIFEST = PRIVATE / "demo_manifest.json"


def load_demo_manifest() -> dict:
    return json.loads(DEMO_MANIFEST.read_text(encoding="utf-8")) if DEMO_MANIFEST.exists() else {"incidents": {}}
from scenarios.resolve import resolve_selectors

CANDIDATE_RULES = [
    AlertRule("ALR-9101", "checkout-svc error rate high", "checkout-svc", "error_rate", ">", 0.10, 15, "error_rate"),
    AlertRule("ALR-9102", "checkout-svc p95 latency high", "checkout-svc", "latency_p95_ms", ">", 1500, 15,
              "p95_latency_ms"),
]
# fault -> (plan whose answer/decisive selectors describe the same fault, change-ticket factory)
FAULT_PLAN = {"retry-policy": ("pilot", ft.demo9), "pool-fastpath": ("s1", ft.demo41),
              "index-migration": ("s2", lambda: ft.demo52()), "endpoint-port": ("s3", None),
              "payment-stop": ("s4", None), "pool-override": ("s5", None)}


class DemoIncidentError(RuntimeError):
    pass


def _next_id() -> str:
    used = set(load_manifest()["incidents"]) | set(load_demo_manifest()["incidents"]) | {p.name for p in SNAPSHOTS.iterdir()} if SNAPSHOTS.exists() else set()
    for n in range(9001, 10000):
        if f"INC-{n}" not in used:
            return f"INC-{n}"
    raise DemoIncidentError("no free INC-9xxx id")


def _copy_complete_lines(src: Path, dst: Path) -> None:
    """Copy a JSONL file that may still be appended to; drop a trailing partial line."""
    data = src.read_bytes()
    cut = data.rfind(b"\n")
    dst.write_bytes(data[:cut + 1] if cut >= 0 else b"")


def _session_events(since: float) -> list[dict]:
    hist = json.loads(PRIVATE_STATE.read_text(encoding="utf-8")) if PRIVATE_STATE.exists() else []
    out = []
    for ev in hist:
        if ev.get("action") == "reset":
            out = []
        elif ev.get("action") == "apply" and parse(ev["at"]) >= since - 1:
            out.append({k: v for k, v in ev.items() if k != "action"})
    return out


def create_demo_incident(log=print) -> dict:
    st = load_state()
    sess = st.get("session")
    if not sess:
        raise DemoIncidentError("no demo session: POST /api/reset first")
    t_start, t_end = parse(sess["started"]), time.time()
    src = Path(sess["telemetry_dir"])
    if not (src / "metrics").is_dir():
        raise DemoIncidentError("session has no metrics yet")
    inc_id = _next_id()
    cdir = CAPTURES / inc_id
    if cdir.exists():
        shutil.rmtree(cdir)
    for kind in ("logs", "spans", "metrics"):
        (cdir / "telemetry" / kind).mkdir(parents=True)
        for f in (src / kind).glob("*.jsonl"):
            _copy_complete_lines(f, cdir / "telemetry" / kind / f.name)
    write_provenance(cdir, t_start, t_end)
    events = _session_events(t_start)
    traffic = st.get("ui_traffic_runs", [])
    load_start = min([parse(r["started"]) for r in traffic if parse(r["started"]) >= t_start] or [t_start])
    meta = {"capture_id": inc_id, "kind": "ui-demo-session", "t_start": iso(t_start), "load_start": iso(load_start),
            "t_end": iso(t_end), "events": events, "telemetry_source": str(src)}
    (cdir / "capture_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    fired = [a for a in (evaluate(r, cdir / "telemetry" / "metrics", not_before=iso(t_start + 20))
                         for r in CANDIDATE_RULES) if a]
    if not fired:
        shutil.rmtree(cdir)
        raise DemoIncidentError("no alert condition observed in this session (error_rate > 0.10 or "
                                "p95 > 1500 ms for 15 s); generate more traffic after injecting a fault")
    alert = min(fired, key=lambda a: a["fired_at"])
    cutoff = min(parse(alert["fired_at"]) + 60, t_end - 1)

    # tickets: background + change tickets of applied faults + an alert ticket (all fixtures)
    set_name = f"demo-{inc_id}"
    extra = [FAULT_PLAN[e["change_id"]][1] for e in events
             if e["change_id"] in FAULT_PLAN and FAULT_PLAN[e["change_id"]][1]]
    ft.SETS[set_name] = lambda: [f() for f in extra] + [ft.alert_ticket("OPS-90", alert["title"], "checkout-svc")]
    try:
        available = write_snapshot(inc_id, cdir, meta, alert, cutoff, None, set_name, None)
    finally:
        ft.SETS.pop(set_name, None)
    snap = SNAPSHOTS / inc_id

    # private answer key (development): reuse the matching plan's description and selectors
    faults = [e for e in events if e["change_id"] in FAULT_PLAN]
    decisive, equivalents, comp, mech, acc_c, acc_m = [], {}, None, None, [], []
    for e in faults:
        plan = PLANS[FAULT_PLAN[e["change_id"]][0]]
        a = plan["answer"]
        comp, mech = comp or a.get("component"), mech or a.get("mechanism")
        acc_c += a.get("accepted_components", [])
        acc_m += a.get("accepted_mechanisms", [])
        for sel in plan["decisive"]:
            try:
                ids, eq = resolve_selectors([sel], snap, meta, cutoff)
                decisive += [i for i in ids if i not in decisive]
                equivalents.update(eq)
            except (KeyError, ValueError):
                pass  # e.g. a selector for evidence this session did not produce
    key = AnswerKey(
        incident_id=inc_id, scenario_number=0, scenario_type="pilot", is_primary=False,
        diagnosable=bool(faults), expected_status="diagnosis" if faults else "no_fault_observed",
        component=comp, mechanism=mech, accepted_components=acc_c, accepted_mechanisms=acc_m,
        decisive_evidence_ids=decisive,
        injected_fault={"faults": faults, "all_changes": events, "decisive_equivalents": equivalents,
                        "source": "ui demo session"},
        available_sources=available,
        notes="DEMO incident created from a UI demo session (development only, never part of evaluation). "
              "Mechanism text is copied from the scenario plan of the same fault; verify against the "
              "session's evidence before relying on it.")
    KEYS.mkdir(parents=True, exist_ok=True)
    (KEYS / f"{inc_id}.json").write_text(key.model_dump_json(indent=2), encoding="utf-8")
    (PRIVATE / "captures").mkdir(parents=True, exist_ok=True)
    (PRIVATE / "captures" / f"{inc_id}_capture_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    workspace.tag(f"capture/{inc_id}")
    man = load_demo_manifest()
    man["incidents"][inc_id] = {
        "slug": None, "scenario_number": None, "scenario_type": "demo", "development": True,
        "variant_of": None, "frozen": False, "frozen_at": None, "superseded_by": None,
        "build_command": "POST http://127.0.0.1:8710/api/incidents/snapshot",
        "capture_id": inc_id, "capture_start": meta["t_start"], "capture_end": meta["t_end"],
        "alert_fired_at": alert["fired_at"], "investigation_cutoff": iso(cutoff),
        "replay_of_capture": None, "status": "built",
    }
    DEMO_MANIFEST.write_text(json.dumps(man, indent=2), encoding="utf-8")
    log(f"[demo] {inc_id} alert {alert['alert_id']} fired {alert['fired_at']} cutoff {iso(cutoff)}")
    return {"incident_id": inc_id, "alert": alert, "investigation_cutoff": iso(cutoff),
            "window_start": iso(t_start), "snapshot": str(snap)}
