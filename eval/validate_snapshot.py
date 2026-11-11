"""Validate one snapshot before it is used (pilot or evaluation).

    python -m eval.validate_snapshot INC-0000 [--snapshot-root P] [--no-ablations] [--json OUT]

Sections (reported separately):
 (a) SCHEMA        incident.json -> Incident; answer key -> AnswerKey; every value the backend
                   returns validates (Incident, SourceStatus, BackendResult, EvidenceRecord);
                   required fields (available_time, id prefix matching source); snapshot
                   files parse; backend methods return errors instead of raising.
 (b) COMPLETENESS  informational only: source availability per source_status/incident/answer
                   key, and whether each decisive evidence id is retrievable, and by which method.
 (c) CUTOFF        no record with available_time after investigation_cutoff from any method
                   (export_logs included), for "none" and every ablation.
 (d) LEAKAGE       file/dir names, incident.json, raw snapshot files and every backend response
                   contain none of: injected fault change ids / families, scenario type words,
                   controller / evaluator names, answer-key mechanism text verbatim.
Intentionally unavailable sources are NOT failures. Exit code 1 only for (a), (c), (d)
failures; 0 otherwise. The JSON result goes to results/snapshot_validation/<id>.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from blackbox.schemas import (AnswerKey, BackendResult, EvidenceRecord, Incident,
                              SourceStatus)

from eval.common import (DEFAULT_ANSWER_KEYS, DEFAULT_MANIFEST, REPO_ROOT, RESULTS_ROOT,
                         load_manifest, write_json)
from eval.probe import collect, learn_identifiers

ABLATIONS = ("none", "no_jira", "no_provenance", "no_traces")
DEFAULT_SNAPSHOTS = REPO_ROOT / "scenarios" / "snapshots"
PREFIX = {"logs": ("log:",), "traces": ("span:", "trace:"), "metrics": ("metric:",),
          "deployments": ("deploy:",), "builds": ("build:",), "commits": ("commit:",),
          "configs": ("config:",), "tickets": ("jira:",)}
SCENARIO_WORDS_STRONG = ["misleading_symptom", "misleading symptom", "unrelated_change",
                         "unrelated change", "insufficient_evidence", "connector_failure",
                         "connector failure", "prompt_injection", "prompt injection",
                         "scenario_type", "scenario_number"]
SCENARIO_WORDS_NAMES_ONLY = ["healthy", "pilot", "fault", "inject", "scenario", "answer",
                             "decisive", "distractor"]
CONTROLLER_WORDS = ["controller_faults", "controller_plans", "controller_state",
                    "demo.controller", "demo/controller", "demo\\controller", "fault controller",
                    "evaluator_private", "answer_key", "answer key", "injected_fault",
                    "decisive_evidence", "distractor_evidence", "change_id"]


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


class Section:
    def __init__(self, name: str):
        self.name, self.failures, self.warnings, self.info = name, [], [], []

    def as_dict(self):
        return {"failures": self.failures, "warnings": self.warnings, "info": self.info}


# --------------------------------------------------------------------------- needles
def build_needles(key: Optional[AnswerKey], manifest_entry: Optional[dict]) -> dict[str, list[str]]:
    strong: dict[str, str] = {}     # needle -> why
    weak: dict[str, str] = {}

    def add(d, v, why):
        """Literal label plus its -/_ variant. A space-separated variant is NOT added: ids
        such as "retry-policy" turn into ordinary code wording ("retry policy") that occurs
        legitimately in baseline code/config (false positive seen on INC-0000). Mechanism
        wording is covered separately by the verbatim mechanism needles."""
        if not isinstance(v, str):
            return
        v = v.strip()
        if len(v) < 5 or re.fullmatch(r"[a-z]-\d+", v):
            return
        for form in {v, v.replace("-", "_"), v.replace("_", "-")}:
            d.setdefault(_norm(form), why)

    for w in SCENARIO_WORDS_STRONG:          # list already holds explicit spaced forms
        add(strong, w, "scenario type word")
    for w in CONTROLLER_WORDS:
        add(strong, w, "controller/evaluator name")
    if key is not None and ("_" in key.scenario_type):
        # single-word types ("healthy", "pilot") are normal vocabulary (SourceStatus.healthy);
        # they are checked in file names and incident.json only
        add(strong, key.scenario_type, "scenario type")
        inj = key.injected_fault or {}
        for f in inj.get("faults", []) or []:
            add(strong, f.get("change_id"), "injected fault change id")
            fam = f.get("family")
            add(weak if fam == "harmless" else strong, fam, "injected fault family")
        for f in inj.get("all_changes", []) or []:
            add(strong, f.get("change_id"), "controller change id")
            if f.get("family") and f.get("family") != "harmless":
                add(strong, f["family"], "controller change family")
        for m in [key.mechanism] + list(key.accepted_mechanisms):
            if m:
                strong.setdefault(_norm(m), "answer-key mechanism (verbatim)")
    if manifest_entry:
        add(strong, manifest_entry.get("slug"), "scenario slug")
    return {"strong": sorted(strong.items()), "weak": sorted(weak.items())}


def scan_text(text: str, needles: list[tuple[str, str]]) -> list[tuple[str, str]]:
    t = _norm(text)
    return [(n, why) for n, why in needles if n in t]


# --------------------------------------------------------------------------- validation
def validate(iid: str, snapshot_root: Optional[Path] = None, keys_dir: Path = DEFAULT_ANSWER_KEYS,
             manifest_path: Path = DEFAULT_MANIFEST, ablations=ABLATIONS) -> dict[str, Any]:
    root = Path(snapshot_root) if snapshot_root else DEFAULT_SNAPSHOTS
    snap = root / iid
    A, B, C, D = (Section(n) for n in ("schema", "completeness", "cutoff", "leakage"))
    res: dict[str, Any] = {"incident_id": iid, "snapshot_dir": str(snap),
                           "backend_checked": False, "ablations_checked": []}

    # ---------------- answer key + manifest
    key = None
    kp = Path(keys_dir) / f"{iid}.json"
    if not kp.exists():
        A.failures.append(f"answer key missing: {kp}")
    else:
        try:
            key = AnswerKey.model_validate_json(kp.read_text(encoding="utf-8"))
            if key.incident_id != iid:
                A.failures.append(f"answer key incident_id {key.incident_id} != {iid}")
        except Exception as e:
            A.failures.append(f"answer key does not validate as AnswerKey: {e}")
    mentry = None
    if Path(manifest_path).exists():
        try:
            raw = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            inc = raw.get("incidents", raw) if isinstance(raw, dict) else {}
            mentry = inc.get(iid) if isinstance(inc, dict) else None
            m = load_manifest(manifest_path).get(iid)
            B.info.append(f"manifest: {'entry present' if m else 'NO ENTRY'}"
                          + (f", scenario {m.scenario_number} ({m.scenario_type}), frozen={m.frozen},"
                             f" superseded={m.superseded}" if m else ""))
        except Exception as e:
            A.warnings.append(f"manifest unreadable: {e}")
    else:
        B.info.append(f"manifest not found: {manifest_path}")
    needles = build_needles(key, mentry)

    # ---------------- snapshot files
    incident = None
    if not snap.is_dir():
        A.failures.append(f"snapshot directory missing: {snap}")
    else:
        if not re.fullmatch(r"INC-\d{4}", iid):
            A.failures.append(f"incident id {iid!r} is not INC-<4 digits>")
        for p in sorted(snap.rglob("*")):
            rel = p.relative_to(snap).as_posix()
            name_needles = needles["strong"] + needles["weak"] + \
                [(w, "hinting word in a file name") for w in SCENARIO_WORDS_NAMES_ONLY]
            for n, why in scan_text(rel.replace("/", " ").replace(".", " "), name_needles):
                D.failures.append(f"file/dir name '{rel}' contains '{n}' ({why})")
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if p.suffix == ".jsonl":
                for i, line in enumerate(text.splitlines(), 1):
                    if line.strip():
                        try:
                            json.loads(line)
                        except Exception as e:
                            A.failures.append(f"{rel}:{i} is not valid JSON: {e}")
                            break
            elif p.suffix == ".json":
                try:
                    json.loads(text)
                except Exception as e:
                    A.failures.append(f"{rel} is not valid JSON: {e}")
            for n, why in scan_text(text, needles["strong"]):
                D.failures.append(f"snapshot file '{rel}' contains '{n}' ({why})")
            for n, why in scan_text(text, needles["weak"]):
                D.warnings.append(f"snapshot file '{rel}' contains '{n}' ({why})")
        ip = snap / "incident.json"
        if not ip.exists():
            A.failures.append("incident.json missing")
        else:
            try:
                incident = Incident.model_validate_json(ip.read_text(encoding="utf-8"))
                if incident.incident_id != iid:
                    A.failures.append(f"incident.json incident_id {incident.incident_id} != {iid}")
                _ts(incident.investigation_cutoff)
                if _ts(incident.window_start) > _ts(incident.investigation_cutoff):
                    A.failures.append("window_start is after investigation_cutoff")
                itext = ip.read_text(encoding="utf-8")
                for n, why in scan_text(itext, [(w, "hinting word") for w in ("healthy", "pilot")]):
                    D.failures.append(f"incident.json contains '{n}' ({why})")
            except Exception as e:
                A.failures.append(f"incident.json does not validate as Incident: {e}")

    # ---------------- backend
    try:
        from blackbox.evidence.backend import list_incidents, load_backend
    except Exception as e:
        res["backend_note"] = f"NOT RUN: evidence backend not importable ({type(e).__name__}: {e})"
        load_backend = None
    if load_backend is not None and snap.is_dir():
        res["backend_checked"] = True
        try:
            listed = list_incidents(snapshot_root) if snapshot_root else list_incidents()
            if iid not in listed:
                A.failures.append(f"list_incidents() does not include {iid}")
        except Exception as e:
            A.failures.append(f"list_incidents() raised {type(e).__name__}: {e}")
        base_col, ids = None, {}
        for abl in ablations:
            try:
                be = load_backend(iid, ablation=abl, snapshot_root=snapshot_root) if snapshot_root \
                    else load_backend(iid, ablation=abl)
            except Exception as e:
                A.failures.append(f"[{abl}] load_backend raised {type(e).__name__}: {e}")
                continue
            lookups = None
            if base_col is not None:
                lookups = {"trace_ids": ids["trace_ids"], "build_ids": ids["build_ids"],
                           "commit_shas": ids["commit_shas"][:20], "ticket_keys": ids["ticket_keys"]}
            col = collect(be, lookups)
            if abl == "none":
                base_col, ids = col, learn_identifiers(col)
                # second pass with lookups so direct-lookup methods are exercised too
                col = collect(be, {"trace_ids": ids["trace_ids"][:20], "build_ids": ids["build_ids"],
                                   "commit_shas": ids["commit_shas"][:20],
                                   "ticket_keys": ids["ticket_keys"]})
                base_col = col
            res["ablations_checked"].append(abl)
            _check_backend(abl, col, incident, needles, A, C, D)
        if base_col is not None:
            _completeness(base_col, key, incident, B, load_backend, iid, snapshot_root)
    elif load_backend is None:
        for s in (A, B, C, D):
            s.info.append(res["backend_note"])

    sections = {"schema": A, "completeness": B, "cutoff": C, "leakage": D}
    res.update({k: v.as_dict() for k, v in sections.items()})
    res["needles_checked"] = {"strong": len(needles["strong"]), "weak": len(needles["weak"])}
    res["ok"] = not (A.failures or C.failures or D.failures)
    return res


def _check_backend(abl, col, incident, needles, A, C, D):
    cutoff = _ts(incident.investigation_cutoff) if incident else None
    for lab, msg in col.exceptions:
        A.failures.append(f"[{abl}] {lab} raised instead of returning BackendResult(status='error'): {msg[:200]}")
    for lab, val in col.raw:
        try:
            if lab == "incident":
                Incident.model_validate(val.model_dump() if hasattr(val, "model_dump") else val)
            elif lab == "source_status":
                for s in val:
                    SourceStatus.model_validate(s.model_dump() if hasattr(s, "model_dump") else s)
            elif lab.startswith("export_logs"):
                if not isinstance(val, list):
                    raise TypeError(f"expected list, got {type(val).__name__}")
            else:
                BackendResult.model_validate(val.model_dump() if hasattr(val, "model_dump") else val)
        except Exception as e:
            A.failures.append(f"[{abl}] {lab} return value does not validate: {str(e)[:200]}")
    seen_bad = set()
    for lab, r in col.records:
        try:
            rec = EvidenceRecord.model_validate(r.model_dump() if hasattr(r, "model_dump") else r)
        except Exception as e:
            A.failures.append(f"[{abl}] {lab}: record does not validate as EvidenceRecord: {str(e)[:200]}")
            continue
        eid = rec.evidence_id
        if eid in seen_bad:
            continue
        if not rec.available_time:
            A.failures.append(f"[{abl}] {lab}: {eid} has no available_time (required, CONTRACT s7)")
            seen_bad.add(eid)
        elif cutoff is not None and _ts(rec.available_time) > cutoff:
            C.failures.append(f"[{abl}] {lab}: {eid} available_time {rec.available_time} > cutoff "
                              f"{incident.investigation_cutoff}")
            seen_bad.add(eid)
        if not eid.startswith(PREFIX.get(rec.source, ("?",))):
            A.failures.append(f"[{abl}] {lab}: {eid} prefix does not match source {rec.source}")
            seen_bad.add(eid)
    reported = set()
    for lab, txt in col.calls:
        for n, why in scan_text(txt, needles["strong"]):
            if (n, lab.split("/")[0]) not in reported:
                reported.add((n, lab.split("/")[0]))
                D.failures.append(f"[{abl}] backend response {lab} contains '{n}' ({why})")
        for n, why in scan_text(txt, needles["weak"]):
            D.warnings.append(f"[{abl}] backend response {lab} contains '{n}' ({why})")


def _completeness(col, key, incident, B, load_backend, iid, snapshot_root):
    for lab, val in col.raw:
        if lab == "source_status":
            for s in val:
                B.info.append(f"source {s.source}: available={s.available} healthy={s.healthy}"
                              + (f" ({s.detail})" if s.detail else ""))
    if incident:
        B.info.append(f"incident.available_sources: {incident.available_sources}")
    if key:
        B.info.append(f"answer key available_sources: {key.available_sources}; expected status "
                      f"{key.expected_status}; diagnosable={key.diagnosable}")
        if incident and sorted(key.available_sources) != sorted(incident.available_sources):
            B.info.append("NOTE: answer-key and incident available_sources differ")
        where: dict[str, list[str]] = {}
        for lab, r in col.records:
            where.setdefault(r.evidence_id, []).append(lab)
        be = load_backend(iid, ablation="none", snapshot_root=snapshot_root) if snapshot_root \
            else load_backend(iid, ablation="none")
        for d in key.decisive_evidence_ids:
            if d in where:
                labs = sorted({l.split("/")[0] for l in where[d]})
                B.info.append(f"decisive {d}: retrievable via {', '.join(labs)}")
                continue
            via = _direct_lookup(be, d)
            B.info.append(f"decisive {d}: " + (f"retrievable via {via}" if via else
                                               "NOT retrievable through any backend method"))
        if not key.decisive_evidence_ids:
            B.info.append("answer key lists no decisive evidence ids")


def _direct_lookup(be, eid: str) -> Optional[str]:
    pre, _, rest = eid.partition(":")
    tries = []
    if pre == "commit":
        tries.append(("get_commit", lambda: be.get_commit(rest)))
    elif pre == "build":
        tries.append(("get_build", lambda: be.get_build(rest)))
    elif pre == "jira":
        tries.append(("get_ticket", lambda: be.get_ticket(rest.split("#")[0])))
    elif pre == "config" and "@" in rest:
        svc, ver = rest.split("@", 1)
        tries.append(("get_config(version)", lambda: be.get_config(svc, version=ver)))
    elif pre in ("trace", "span"):
        tries.append(("get_trace", lambda: be.get_trace(rest)))
    for name, fn in tries:
        try:
            r = fn()
            if any(x.evidence_id == eid for x in r.records):
                return name
        except Exception:
            pass
    return None


# --------------------------------------------------------------------------- CLI
def render(res: dict) -> str:
    L = [f"Snapshot validation: {res['incident_id']}  ({res['snapshot_dir']})",
         f"backend checked: {res['backend_checked']}  ablations: {res['ablations_checked'] or '-'}"]
    if res.get("backend_note"):
        L.append(res["backend_note"])
    for name, title in (("schema", "(a) SCHEMA"), ("completeness", "(b) EVIDENCE COMPLETENESS (informational)"),
                        ("cutoff", "(c) CUTOFF"), ("leakage", "(d) ANSWER LEAKAGE")):
        s = res[name]
        verdict = "info" if name == "completeness" else ("FAIL" if s["failures"] else "PASS")
        L.append(f"\n{title}: {verdict}  ({len(s['failures'])} failures, {len(s['warnings'])} warnings)")
        for f in s["failures"][:40]:
            L.append(f"  FAIL  {f}")
        if len(s["failures"]) > 40:
            L.append(f"  ... {len(s['failures']) - 40} more (see JSON)")
        for w in s["warnings"][:20]:
            L.append(f"  warn  {w}")
        for i in s["info"]:
            L.append(f"  info  {i}")
    L.append(f"\nOVERALL: {'PASS' if res['ok'] else 'FAIL'}"
             + ("" if res["backend_checked"] else "  (file-level checks only; backend checks NOT RUN)"))
    return "\n".join(L)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.validate_snapshot")
    ap.add_argument("incident_id")
    ap.add_argument("--snapshot-root", default=None)
    ap.add_argument("--answer-keys", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--no-ablations", action="store_true", help="check only ablation 'none'")
    ap.add_argument("--json", default=None, help="output path (default results/snapshot_validation/<id>.json)")
    a = ap.parse_args(argv)
    res = validate(a.incident_id, Path(a.snapshot_root) if a.snapshot_root else None,
                   Path(a.answer_keys) if a.answer_keys else DEFAULT_ANSWER_KEYS,
                   Path(a.manifest) if a.manifest else DEFAULT_MANIFEST,
                   ("none",) if a.no_ablations else ABLATIONS)
    out = Path(a.json) if a.json else RESULTS_ROOT / "snapshot_validation" / f"{a.incident_id}.json"
    write_json(out, res)
    print(render(res))
    print(f"\nJSON: {out}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
