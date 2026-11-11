"""Score a batch: objective metrics, provisional semantic metrics, review worksheets.

    python -m eval.score --batch <name>            # runs/<name>  -> results/<name>/
    python -m eval.score --batch <name> --stub     # dev only: runs/_dev_stub/<name> -> runs/_dev_stub/<name>/_results/

Stub records are never scored into results/: in a real batch they are excluded and
listed; a stub batch is only scored with --stub and its output stays under runs/_dev_stub/.
Planned runs with no record on disk are scored as "not_run" (misses), never dropped.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from eval.common import (REPO_ROOT, DEFAULT_ANSWER_KEYS, DEFAULT_MANIFEST, PRIMARY_DIAGNOSABLE_SCENARIOS,
                         SCENARIO_TYPE_BY_NUMBER, ManifestEntry, RunRecord, load_answer_key,
                         load_batch_runs, load_manifest, read_json, resolve_batch_dir,
                         results_dir_for, write_json)
from eval.metrics import aggregate_system, score_run
from eval.worksheets import (CLAIM_COLS, DIAG_COLS, claim_rows, diagnosis_rows, run_markdown,
                             write_csv)


def case_info(incident_id: str, manifest: dict[str, ManifestEntry], key) -> dict[str, Any]:
    e = manifest.get(incident_id)
    num = key.scenario_number if key else (e.scenario_number if e else None)
    stype = key.scenario_type if key else (e.scenario_type if e else
                                           SCENARIO_TYPE_BY_NUMBER.get(num, "unknown"))
    is_primary = key.is_primary if key else (num is not None and 1 <= num <= 6)
    diagnosable = key.diagnosable if key else (num in PRIMARY_DIAGNOSABLE_SCENARIOS)
    return {"scenario_number": num, "scenario_type": stype, "is_primary": bool(is_primary),
            "diagnosable": bool(diagnosable),
            "variant_of": (key.variant_of if key else (e.variant_of if e else None)),
            "frozen": e.frozen if e else None}


class AnswerKeyMismatch(Exception):
    pass


def score_batch(batch_dir: Path, out_dir: Path, *, stub_mode: bool,
                answer_keys_dir: Optional[Path] = None) -> dict[str, Any]:
    """`answer_keys_dir` overrides the batch's key directory, e.g. a directory holding the
    keys as they were when the runs were made (after a snapshot rebuild)."""
    batch_meta = read_json(batch_dir / "batch.json") if (batch_dir / "batch.json").exists() else {}
    plan = read_json(batch_dir / "plan.json") if (batch_dir / "plan.json").exists() else {"runs": []}
    manifest_path = Path(batch_meta.get("manifest_path") or DEFAULT_MANIFEST)
    keys_dir = Path(answer_keys_dir or batch_meta.get("answer_keys_dir") or DEFAULT_ANSWER_KEYS)
    manifest = load_manifest(manifest_path) if manifest_path.exists() else {}

    records = load_batch_runs(batch_dir)
    excluded_stub = [r.run_id for r in records if r.stub and not stub_mode]
    excluded_real = [r.run_id for r in records if (not r.stub) and stub_mode]
    records = [r for r in records if r.stub == stub_mode]
    by_id: dict[str, RunRecord] = {r.run_id: r for r in records}

    keys: dict[str, Any] = {}

    def key_for(iid: str):
        if iid not in keys:
            keys[iid] = load_answer_key(iid, keys_dir)
        return keys[iid]

    planned = {p["run_id"]: p for p in plan.get("runs", [])}
    all_ids = list(planned) + [i for i in by_id if i not in planned]

    rows, claims_all, diag_all, mismatched = [], [], [], []
    ws_dir = out_dir / "worksheets"
    for run_id in all_ids:
        rec = by_id.get(run_id)
        p = planned.get(run_id, {})
        info = {
            "run_id": run_id,
            "incident_id": rec.incident_id if rec else p["incident_id"],
            "system": rec.system if rec else p["system"],
            "ablation": rec.ablation if rec else p.get("ablation", "none"),
            "repeat": rec.repeat if rec else int(p.get("repeat_index", 0)),
            "cell": (rec.input.get("cell") if rec else p.get("cell")),
            "phase": (rec.input.get("phase") if rec else plan.get("phase")),
            "planned": run_id in planned,
        }
        key = key_for(info["incident_id"])
        info.update(case_info(info["incident_id"], manifest, key))
        status = rec.run_status if rec else "not_run"
        out = rec.output if rec else None
        row = score_run(out, key, run_status=status, system=info["system"])
        row = {**info, **row}
        row["answer_key_found"] = key is not None
        kp = keys_dir / f"{info['incident_id']}.json"
        row["answer_key_sha256"] = hashlib.sha256(kp.read_bytes()).hexdigest() if kp.exists() else None
        row["answer_key_sha256_at_run"] = rec.meta.get("answer_key_sha256") if rec else None
        if row["answer_key_sha256_at_run"] and row["answer_key_sha256_at_run"] != row["answer_key_sha256"]:
            mismatched.append(run_id)
        row["harness_wall_seconds"] = rec.meta.get("wall_seconds") if rec else None
        row["b1_source_status"] = rec.meta.get("b1_source_status") if rec else None
        row["exception"] = (rec.meta.get("exception") or rec.output_error) if rec else None
        row["stub"] = stub_mode
        try:
            row["run_dir"] = (batch_dir / run_id).resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            row["run_dir"] = str(batch_dir / run_id)
        rows.append(row)
        info["exception"] = row["exception"]
        cr = claim_rows(run_id, info, out)
        dr = diagnosis_rows(run_id, info, out, key, row)
        claims_all += cr
        diag_all += dr
        if rec is not None:
            write_csv(ws_dir / f"{run_id}_claims.csv", cr, CLAIM_COLS)
            (ws_dir / f"{run_id}.md").write_text(
                run_markdown(run_id, info, out, key, row, cr, stub_mode), encoding="utf-8")

    if mismatched:
        raise AnswerKeyMismatch(
            f"{len(mismatched)} run(s) were made against a different answer key than {keys_dir} "
            f"holds (snapshot rebuilt?): {mismatched[:5]}. Score them with --answer-keys <dir> "
            "containing the key version used at run time.")
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(ws_dir / "all_claims.csv", claims_all, CLAIM_COLS)
    write_csv(ws_dir / "diagnosis_review.csv", diag_all, DIAG_COLS)
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "per_run.csv", index=False)
    write_json(out_dir / "per_run.json", rows)

    # main pass aggregates: ablation none, repeat 0, per system
    n_diag_planned = len({r["incident_id"] for r in rows
                          if r["is_primary"] and r["diagnosable"] and r["planned"]})
    main = [r for r in rows if r["ablation"] == "none" and r["repeat"] == 0]
    agg = {s: aggregate_system([r for r in main if r["system"] == s],
                               n_diag_primary_planned=n_diag_planned)
           for s in sorted({r["system"] for r in main})}
    summary = {
        "batch": batch_dir.name, "phase": batch_meta.get("phase"), "stub": stub_mode,
        "answer_keys_dir": str(keys_dir),
        "answer_key_sha256": {r["incident_id"]: r["answer_key_sha256"] for r in rows},
        "planned_runs": len(planned), "records_on_disk": len(records),
        "not_run": sum(r["run_status"] == "not_run" for r in rows),
        "status_counts": df["run_status"].value_counts().to_dict() if len(df) else {},
        "excluded_stub_records": excluded_stub, "excluded_non_stub_records": excluded_real,
        "diagnosable_primary_cases_planned": n_diag_planned,
        "main_pass_by_system": agg,
        "provisional_fields": ["top1_auto", "top3_auto", "outcome_correct_auto",
                               "top1_accuracy_auto", "top3_accuracy_auto",
                               "unsupported_rate_auto (automated lower bound)",
                               "correct/false abstention (status-based)"],
    }
    write_json(out_dir / "aggregates.json", summary)
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.score")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--answer-keys", default=None,
                    help="directory with the answer keys as they were when the runs were made")
    ap.add_argument("--stub", action="store_true",
                    help="development only: score a stub batch under runs/_dev_stub/")
    a = ap.parse_args(argv)
    batch_dir = resolve_batch_dir(a.batch, a.stub)
    if not batch_dir.exists():
        print(f"batch directory not found: {batch_dir}", file=sys.stderr)
        return 2
    if not a.stub and batch_dir.resolve().is_relative_to(resolve_batch_dir("", True).resolve()):
        print("refusing: stub batches are scored only with --stub", file=sys.stderr)
        return 2
    out_dir = results_dir_for(a.batch, a.stub)
    try:
        s = score_batch(batch_dir, out_dir, stub_mode=a.stub,
                        answer_keys_dir=Path(a.answer_keys) if a.answer_keys else None)
    except AnswerKeyMismatch as e:
        print(f"refusing: {e}", file=sys.stderr)
        return 2
    print(f"scored {s['records_on_disk']} records ({s['not_run']} planned runs not on disk) -> {out_dir}")
    if s["excluded_stub_records"]:
        print(f"excluded {len(s['excluded_stub_records'])} stub records from scoring")
    if a.stub:
        print("STUB batch: development output only, not a result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
