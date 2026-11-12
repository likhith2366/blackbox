"""Harness planning, phase guards, resume and failure recording on tmp dirs only.

Uses the labelled stub system/fixtures from eval.stub; nothing is written to runs/ or results/.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blackbox.schemas import RunConfig  # noqa: E402
from eval.common import load_manifest, read_json  # noqa: E402
from eval.harness import (PlanError, build_plan, load_matrix, run_batch,  # noqa: E402
                          validate_phase)
from eval.score import score_batch  # noqa: E402
from eval.stub import StubBackend, create_stub_fixtures, run_stub_system  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
MATRIX = load_matrix(REPO / "eval" / "matrix.yaml")


@pytest.fixture
def fixtures(tmp_path):
    mpath, keys = create_stub_fixtures(tmp_path / "fx")
    return mpath, keys


def test_eval_plan_is_47_and_b1_after_blackbox(fixtures):
    mpath, keys = fixtures
    plan = build_plan(MATRIX, "eval", load_manifest(mpath), keys)
    assert len(plan) == 47
    cells = {c: sum(p["cell"] == c for p in plan) for c in ("main", "repeats", "ablations")}
    assert cells == {"main": 27, "repeats": 8, "ablations": 12}
    pos = {p["run_id"]: i for i, p in enumerate(plan)}
    for p in plan:
        if p["system"] == "b1":
            assert pos[p["b1_source_run"]] < pos[p["run_id"]]
    # repeats/ablations only on diagnosable primary (scenarios 1-4)
    assert {p["scenario_number"] for p in plan if p["cell"] != "main"} == {1, 2, 3, 4}
    assert not validate_phase(plan, "eval", load_manifest(mpath), keys, True)


def test_pilot_plan_only_pilot(fixtures):
    mpath, keys = fixtures
    plan = build_plan(MATRIX, "pilot", load_manifest(mpath), keys)
    assert {p["scenario_number"] for p in plan} == {0}
    assert len(plan) == 6


def test_eval_refuses_unfrozen_and_superseded(fixtures, tmp_path):
    mpath, keys = fixtures
    m = read_json(mpath)
    m["incidents"][3]["frozen"] = False                    # scenario 3
    m["incidents"].append({"incident_id": "STUB-0099", "scenario_number": 3, "frozen": True})
    p2 = tmp_path / "m2.json"
    p2.write_text(json.dumps(m))
    with pytest.raises(PlanError):                          # two active scenario-3 incidents
        build_plan(MATRIX, "eval", load_manifest(p2), keys)
    m["incidents"][-1]["superseded"] = True
    p2.write_text(json.dumps(m))
    man = load_manifest(p2)
    plan = build_plan(MATRIX, "eval", man, keys)
    assert all(p["incident_id"] != "STUB-0099" for p in plan)
    probs = validate_phase(plan, "eval", man, keys, True)
    assert any("STUB-0003" in x and "not frozen" in x for x in probs)


def test_pilot_phase_refuses_eval_incidents(fixtures):
    mpath, keys = fixtures
    man = load_manifest(mpath)
    bad = {"phases": {"pilot": {"cells": [{"name": "x", "select": {"scenario_numbers": [1]},
                                           "systems": ["blackbox"]}]}}}
    plan = build_plan(bad, "pilot", man, keys)
    assert validate_phase(plan, "pilot", man, keys, False)


def _run(plan, bdir, system_fn=run_stub_system, max_runs=None):
    return run_batch(plan=plan, batch_dir=bdir, phase="eval", stub=True, cfg=RunConfig(),
                     system_fn=system_fn, backend_fn=lambda i, a: StubBackend(i, a),
                     base_meta={"model": "stub"}, max_runs=max_runs, log=lambda s: None)


def test_resume_and_failure_records(fixtures, tmp_path):
    mpath, keys = fixtures
    plan = build_plan(MATRIX, "eval", load_manifest(mpath), keys)
    bdir = tmp_path / "batch"
    bdir.mkdir()
    c1 = _run(plan, bdir, max_runs=5)
    assert c1["executed"] == 5
    # simulate an interrupted run left in "running"
    nxt = plan[5]["run_id"]
    (bdir / nxt).mkdir()
    (bdir / nxt / "meta.json").write_text(json.dumps({"status": "running"}))
    c2 = _run(plan, bdir)
    assert c2["skipped_done"] == 5 and c2["executed"] == 42
    assert any((bdir / "_interrupted").iterdir())
    metas = {p["run_id"]: read_json(bdir / p["run_id"] / "meta.json") for p in plan}
    assert all(m["stub"] is True for m in metas.values())
    assert metas["STUB-0008_b0_none_r0"]["status"] == "exception"
    assert "simulated" in metas["STUB-0008_b0_none_r0"]["exception"]
    assert (bdir / "STUB-0008_b0_none_r0" / "traceback.txt").exists()
    assert metas["STUB-0001_b1_none_r0"]["b1_evidence_count"] == 3
    out = read_json(bdir / "STUB-0001_blackbox_none_r0" / "output.json")
    assert out["stub"] is True


def test_b1_skipped_when_blackbox_has_no_output(fixtures, tmp_path):
    mpath, keys = fixtures
    plan = [p for p in build_plan(MATRIX, "eval", load_manifest(mpath), keys)
            if p["incident_id"] == "STUB-0001" and p["cell"] == "main"]

    def boom(system, backend, cfg, b1_evidence=None):
        if system == "blackbox":
            raise RuntimeError("stub blackbox crash")
        return run_stub_system(system, backend, cfg, b1_evidence)

    bdir = tmp_path / "b"
    bdir.mkdir()
    _run(plan, bdir, system_fn=boom)
    m = read_json(bdir / "STUB-0001_b1_none_r0" / "meta.json")
    assert m["status"] == "skipped_dependency"


def test_score_counts_not_run_and_excludes_stub_from_real_scoring(fixtures, tmp_path):
    mpath, keys = fixtures
    plan = build_plan(MATRIX, "eval", load_manifest(mpath), keys)
    bdir = tmp_path / "batch"
    bdir.mkdir()
    (bdir / "batch.json").write_text(json.dumps({"phase": "eval", "manifest_path": str(mpath),
                                                 "answer_keys_dir": str(keys)}))
    (bdir / "plan.json").write_text(json.dumps({"phase": "eval", "runs": plan}))
    _run(plan, bdir, max_runs=10)
    s = score_batch(bdir, tmp_path / "res_stub", stub_mode=True)
    assert s["records_on_disk"] == 10 and s["not_run"] == 37
    assert (tmp_path / "res_stub" / "worksheets" / "all_claims.csv").exists()
    real = score_batch(bdir, tmp_path / "res_real", stub_mode=False)
    assert real["records_on_disk"] == 0
    assert len(real["excluded_stub_records"]) == 10
    # reviewer columns are blank
    import csv
    with open(tmp_path / "res_stub" / "worksheets" / "all_claims.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows and all(r["reviewer_A_verdict"] == "" and r["reviewer_B_verdict"] == ""
                        for r in rows)


def test_phase_config_files_and_unique_ids(fixtures):
    from eval.harness import make_config
    mpath, keys = fixtures
    plan = build_plan(MATRIX, "eval", load_manifest(mpath), keys)
    assert len({p["run_id"] for p in plan}) == len(plan) == 47
    assert MATRIX["phases"]["pilot"]["run_config_file"] == "configs/dev.yaml"
    assert MATRIX["phases"]["eval"]["run_config_file"] == "configs/frozen_v1.yaml"
    cfg, src = make_config(MATRIX, "pilot", "rev")
    assert src["run_config_file"] == "configs/dev.yaml" and len(src["run_config_sha256"]) == 64
    assert cfg.code_revision == "rev"
    if not (REPO / "configs" / "frozen_v1.yaml").exists():
        with pytest.raises(PlanError):           # no silent fallback to defaults
            make_config(MATRIX, "eval", "rev")


def test_meta_saves_full_run_config(fixtures, tmp_path):
    mpath, keys = fixtures
    plan = build_plan(MATRIX, "eval", load_manifest(mpath), keys)[:1]
    bdir = tmp_path / "b"
    bdir.mkdir()
    _run(plan, bdir)
    meta = read_json(bdir / plan[0]["run_id"] / "meta.json")
    assert meta["run_config"] == RunConfig().model_dump()
    assert meta["decoding"]["seed"] == RunConfig().seed


def _run_real_eval(plan, bdir, system_fn=run_stub_system, backend_fn=None):
    return run_batch(plan=plan, batch_dir=bdir, phase="eval", stub=False, cfg=RunConfig(),
                     system_fn=system_fn, backend_fn=backend_fn or (lambda i, a: StubBackend(i, a)),
                     base_meta={"model": "test"}, log=lambda s: None)


def test_eval_refuses_non_snapshot_backend(fixtures, tmp_path):
    mpath, keys = fixtures
    plan = build_plan(MATRIX, "eval", load_manifest(mpath), keys)[:1]
    bdir = tmp_path / "b"
    bdir.mkdir()
    _run_real_eval(plan, bdir)
    m = read_json(bdir / plan[0]["run_id"] / "meta.json")
    assert m["status"] == "refused_live" and "not the snapshot backend" in m["exception"]
    assert not (bdir / plan[0]["run_id"] / "output.json").exists()


def test_eval_marks_live_evidence_invalid(fixtures, tmp_path, monkeypatch):
    import eval.harness as H
    monkeypatch.setattr(H, "live_backend_problem", lambda b: None)   # pretend it is a snapshot backend
    mpath, keys = fixtures
    plan = build_plan(MATRIX, "eval", load_manifest(mpath), keys)[:1]

    def live_system(system, backend, cfg, b1_evidence=None):
        out = run_stub_system(system, backend, cfg, b1_evidence)
        out.evidence_seen[0].attributes["live"] = True
        return out

    bdir = tmp_path / "b"
    bdir.mkdir()
    _run_real_eval(plan, bdir, system_fn=live_system)
    m = read_json(bdir / plan[0]["run_id"] / "meta.json")
    assert m["status"] == "invalid_live_data"
    assert (bdir / plan[0]["run_id"] / "output.json").exists()      # kept as a result


def test_live_evidence_ids_detects_gh_prefix():
    from blackbox.schemas import EvidenceRecord, SystemOutput
    from eval.harness import live_evidence_ids
    out = SystemOutput(system="blackbox", incident_id="INC-1", status="completed",
                       evidence_seen=[EvidenceRecord(evidence_id="gh:commit:o/r@7addfee", source="commits"),
                                      EvidenceRecord(evidence_id="log:1", source="logs")])
    assert live_evidence_ids(out) == ["gh:commit:o/r@7addfee"]


def test_score_refuses_changed_answer_key(fixtures, tmp_path):
    """A run made against one answer key must not be silently scored with a rebuilt key."""
    from eval.harness import run_provenance
    from eval.score import AnswerKeyMismatch
    mpath, keys = fixtures
    plan = build_plan(MATRIX, "eval", load_manifest(mpath), keys)[:1]
    bdir = tmp_path / "batch"
    bdir.mkdir()
    (bdir / "batch.json").write_text(json.dumps({"phase": "eval", "manifest_path": str(mpath),
                                                 "answer_keys_dir": str(keys)}))
    (bdir / "plan.json").write_text(json.dumps({"phase": "eval", "runs": plan}))
    run_batch(plan=plan, batch_dir=bdir, phase="eval", stub=True, cfg=RunConfig(),
              system_fn=run_stub_system, backend_fn=lambda i, a: StubBackend(i, a),
              base_meta={}, log=lambda s: None,
              provenance_fn=lambda iid: run_provenance(iid, keys, None))
    iid = plan[0]["incident_id"]
    assert read_json(bdir / plan[0]["run_id"] / "meta.json")["answer_key_sha256"]
    old = tmp_path / "old_keys"
    old.mkdir()
    (old / f"{iid}.json").write_bytes((keys / f"{iid}.json").read_bytes())
    k = read_json(keys / f"{iid}.json")
    k["notes"] = "rebuilt"
    (keys / f"{iid}.json").write_text(json.dumps(k))
    with pytest.raises(AnswerKeyMismatch):
        score_batch(bdir, tmp_path / "r1", stub_mode=True)
    s = score_batch(bdir, tmp_path / "r2", stub_mode=True, answer_keys_dir=old)
    assert s["records_on_disk"] == 1


def test_unfrozen_incidents_are_skipped_not_dropped(fixtures, tmp_path):
    from eval.harness import classify_incidents
    mpath, keys = fixtures
    m = read_json(mpath)
    m["incidents"][4]["frozen"] = False                      # STUB-0004
    p2 = tmp_path / "m.json"
    p2.write_text(json.dumps(m))
    man = load_manifest(p2)
    plan = build_plan(MATRIX, "eval", man, keys)
    blocking, skips = classify_incidents(plan, "eval", man, keys, True)
    assert not blocking and set(skips) == {"STUB-0004"}
    for p in plan:
        if p["incident_id"] in skips:
            p["skip_reason"] = skips[p["incident_id"]]
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "batch.json").write_text(json.dumps({"phase": "eval", "manifest_path": str(p2),
                                                 "answer_keys_dir": str(keys)}))
    (bdir / "plan.json").write_text(json.dumps({"phase": "eval", "runs": plan}))
    c = _run(plan, bdir)
    n_skip = sum(1 for p in plan if p.get("skip_reason"))
    assert c["skipped_with_reason"] == n_skip == 3 + 2 + 3    # main 3, repeats 2, ablations 3
    assert not (bdir / "STUB-0004_blackbox_none_r0").exists()
    s = score_batch(bdir, tmp_path / "r", stub_mode=True)
    assert s["planned_runs"] == 47 and s["not_run"] == n_skip


def test_cases_flag_and_order_via_cli(tmp_path, monkeypatch, capsys):
    from eval import harness as H
    rc = H.main(["plan", "--phase", "eval", "--stub", "--cases", "STUB-0002,STUB-0001"])
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.startswith(("main", "repeats", "ablations"))]
    runnable = [l.split()[1] for l in lines if "SKIP" not in l]
    assert runnable[:3] == ["STUB-0001_blackbox_none_r0", "STUB-0001_b1_none_r0", "STUB-0001_b0_none_r0"]
    assert runnable[3:6] == ["STUB-0002_blackbox_none_r0", "STUB-0002_b1_none_r0", "STUB-0002_b0_none_r0"]
    assert all(r.startswith(("STUB-0001", "STUB-0002")) for r in runnable)
    assert "runnable now: 16" in out      # rc is 1 until configs/frozen_v1.yaml exists


def test_gpu_lock_acquire_release_and_parent(tmp_path):
    import os
    from eval.harness import GpuLock
    lock = tmp_path / "_gpu.lock"
    with GpuLock("test", path=lock, wait_s=0.01) as g:
        assert g.mine and json.loads(lock.read_text())["pid"] == os.getpid()
    assert not lock.exists()
    import psutil
    parent = psutil.Process().parent().pid
    lock.write_text(json.dumps({"pid": parent, "owner": "wrapper", "started": "x"}))
    with GpuLock("test", path=lock, wait_s=0.01) as g:
        assert not g.mine and "parent" in g.via
    assert lock.exists()                     # the wrapper's lock is left alone
    lock.write_text(json.dumps({"pid": 999999999, "owner": "dead", "started": "x"}))
    with GpuLock("test", path=lock, wait_s=0.01) as g:   # stale lock is cleared
        assert g.mine


_V2 = pytest.mark.skipif(
    "b1_source_dir" not in __import__("inspect").signature(run_batch).parameters,
    reason="harness v2 patch pending: results/eval_harness_v2.patch is re-applied after eval_v1 "
           "(eval/ kept byte-identical to 4dd96c0 during the batch)")


@_V2
def test_supplementary_rerun_takes_b1_evidence_from_original(fixtures, tmp_path):
    mpath, keys = fixtures
    plan = [p for p in build_plan(MATRIX, "eval", load_manifest(mpath), keys)
            if p["incident_id"] == "STUB-0001" and p["cell"] == "main"]
    orig, sup = tmp_path / "eval_v1", tmp_path / "eval_v1_rerun"
    orig.mkdir(), sup.mkdir()
    _run(plan, orig)
    rerun = [dict(p) for p in plan]
    for p in rerun:
        if p["system"] != "b1":
            p["skip_reason"] = "not selected (--only-runs)"
    run_batch(plan=rerun, batch_dir=sup, phase="eval", stub=True, cfg=RunConfig(),
              system_fn=run_stub_system, backend_fn=lambda i, a: StubBackend(i, a),
              base_meta={}, log=lambda s: None, b1_source_dir=orig)
    m = read_json(sup / "STUB-0001_b1_none_r0" / "meta.json")
    assert m["status"] == "completed" and m["b1_evidence_count"] == 3
    assert m["b1_source_batch"] == str(orig)
    assert not (sup / "STUB-0001_blackbox_none_r0").exists()
    assert (orig / "STUB-0001_b1_none_r0" / "meta.json").exists()        # original untouched


@_V2
def test_only_runs_cli_and_scoped_git(capsys):
    from eval import harness as H
    H.main(["plan", "--phase", "eval", "--stub", "--only-runs", "STUB-0001_b1_none_r0,STUB-0001_b0_none_r0"])
    out = capsys.readouterr().out
    assert "runnable now: 2" in out
    sc = H.run_provenance("STUB-0001", Path("."), None)["git_scoped"]
    assert sc["scoped_paths"][:3] == ["blackbox", "eval", "configs"]
    assert "scoped_dirty" in sc
