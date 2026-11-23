"""get_config compares a deployment's config with the PREVIOUS DEPLOYMENT of the same service.

Each test copies the INC-9001 development snapshot to a temporary directory and edits only the records the case
needs. Nothing under scenarios/ is modified.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from blackbox.evidence.backend import SnapshotBackend

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scenarios" / "snapshots" / "INC-9001"


def _rows(p: Path):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _write(p: Path, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def snap(tmp_path):
    d = tmp_path / "INC-9001"
    shutil.copytree(SRC, d)
    return d


def _config(d: Path, service="payment-svc", ablation="none"):
    r = SnapshotBackend("INC-9001", ablation, d).get_config(service)
    assert r.status == "ok", r.error
    return r.records[0]


def test_unchanged_config_names_both_deployments(snap):
    rec = _config(snap)
    assert "config of the running deployment d-576" in rec.content
    assert "unchanged from previous deployment d-574" in rec.content
    assert "2026-11-20T03:43:49.742Z" in rec.content          # previous deployment's time is shown
    assert "acquire_timeout_ms: 1000" in rec.content           # the current value stays visible
    assert "3000" not in rec.content                           # the old repository change is not presented
    assert rec.evidence_id == "config:payment-svc@b1e5a60"     # id unchanged


def test_genuinely_changed_config_shows_the_deployment_diff(snap):
    cfgs = _rows(snap / "configs.jsonl")
    old = next(c for c in cfgs if c["service"] == "payment-svc" and c["commit"].startswith("b1e5a60"))
    new = dict(old, commit=next(c["sha"] for c in _rows(snap / "commits.jsonl") if c["sha"].startswith("d142601")),
               committed_at="2026-11-20T03:44:03+00:00",
               content=old["content"].replace("pool_size: 4", "pool_size: 2"))
    _write(snap / "configs.jsonl", cfgs + [new])
    rec = _config(snap)
    assert "changed from previous deployment d-574" in rec.content
    assert "-  pool_size: 4" in rec.content and "+  pool_size: 2" in rec.content
    assert "unchanged" not in rec.content


def test_missing_previous_deployment_is_not_called_unchanged(snap):
    rec = _config(snap, "checkout-svc")
    assert "no earlier deployment of checkout-svc" in rec.content
    assert "unchanged" not in rec.content


def test_build_and_deployment_disagree_is_a_conflict(snap):
    builds = _rows(snap / "builds.jsonl")
    for b in builds:
        if b["build_id"] == "b-1214":                      # build of the previous deployment d-574
            b["commit"] = "0" * 40
    _write(snap / "builds.jsonl", builds)
    rec = _config(snap)
    assert "provenance conflict" in rec.content and "b-1214" in rec.content
    assert "unchanged" not in rec.content


def test_two_previous_deployments_at_the_same_time_is_a_conflict(snap):
    deps = _rows(snap / "deployments.jsonl")
    d574 = next(d for d in deps if d["deploy_id"] == "d-574")
    _write(snap / "deployments.jsonl", deps + [dict(d574, deploy_id="d-574b", evidence_id="deploy:d-574b")])
    rec = _config(snap)
    assert "started at the same time" in rec.content and "unchanged" not in rec.content


def _repoint(snap, deploy_id, sha):
    deps = _rows(snap / "deployments.jsonl")
    build = next(d["build_id"] for d in deps if d["deploy_id"] == deploy_id)
    _write(snap / "deployments.jsonl", [dict(d, commit=sha) if d["deploy_id"] == deploy_id else d for d in deps])
    _write(snap / "builds.jsonl", [dict(b, commit=sha) if b["build_id"] == build else b
                                   for b in _rows(snap / "builds.jsonl")])


def test_previous_deployment_commit_not_in_history_is_unavailable_not_unchanged(snap):
    _repoint(snap, "d-574", "f" * 40)
    rec = _config(snap)
    assert "comparison with the previous deployment d-574" in rec.content
    assert "its config could not be established" in rec.content and "ffffff" in rec.content
    assert "unchanged" not in rec.content


def test_running_deployment_commit_not_in_history_is_unavailable_not_unchanged(snap):
    _repoint(snap, "d-576", "e" * 40)
    rec = _config(snap)
    assert "config of deployment d-576" in rec.content and "could not be established" in rec.content
    assert "not possible (config of this deployment unknown)" in rec.content
    assert "unchanged" not in rec.content


def test_unavailable_provenance_states_the_deployed_version_is_unknown(snap):
    rec = _config(snap, ablation="no_provenance")
    assert "deployed version unknown" in rec.content and "not possible" in rec.content
    assert "unchanged" not in rec.content and "d-576" not in rec.content


def test_config_is_found_by_ancestry_not_by_commit_time(snap):
    # Give the 1000 ms config a commit time AFTER the deployments: time ordering would now pick the older config,
    # ancestry still finds b1e5a60 because it is an ancestor of the deployed commits.
    cfgs = _rows(snap / "configs.jsonl")
    for c in cfgs:
        if c["commit"].startswith("b1e5a60"):
            c["committed_at"] = "2026-11-06T11:15:00+00:00"
    _write(snap / "configs.jsonl", cfgs)
    commits = _rows(snap / "commits.jsonl")
    for c in commits:
        if c["sha"].startswith("b7ae74f"):
            c["committer_date"] = "2026-11-01T00:00:00+00:00"   # commit time earlier than the config commit
    _write(snap / "commits.jsonl", commits)
    rec = _config(snap)
    assert rec.evidence_id == "config:payment-svc@b1e5a60"
