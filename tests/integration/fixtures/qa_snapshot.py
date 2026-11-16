"""TEST FIXTURE (Agent 4, QA): a tiny synthetic snapshot in the on-disk format written by
scenarios/build.py, used to exercise blackbox/evidence/backend.py before (and independently
of) the real pilot. Nothing here is captured telemetry; every text says "QA FIXTURE".

Timeline (UTC, 2026-11-19): window 10:00:00, change 10:01:00, alert 10:02:00, cutoff 10:03:00.
Deliberately includes: one log line, one span, one metric point, one ticket comment and one
ticket description update AFTER the cutoff; ticket keys in commit messages and log lines;
provenance fields in a startup log line; a log template repeated across the change.
"""
from __future__ import annotations

import json
from pathlib import Path

from blackbox.schemas import Alert, Incident

D = "2026-11-19T"
CUTOFF = D + "10:03:00.000Z"
SHA_OLD = "a" * 40
SHA_NEW = "b" * 40


def _jl(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _log(i, ts, svc, level, msg, **extra):
    raw = {"ts": ts, "level": level, "service": svc, "msg": msg, **extra}
    return {"evidence_id": f"log:qa{i:04d}", "service": svc, "line": i, "ts": ts, "raw": raw}


def write_mini_snapshot(snap: Path, runtime: dict | None = None) -> Path:
    snap.mkdir(parents=True, exist_ok=True)
    inc = Incident(incident_id=snap.name,
                   alert=Alert(alert_id="QA-ALR", title="checkout-svc error rate high (QA fixture)",
                               service="checkout-svc", signal="error_rate", condition="error_rate > 0.1 for 15s",
                               observed_value=0.5, fired_at=D + "10:02:00.000Z"),
                   window_start=D + "10:00:00.000Z", window_end=CUTOFF, investigation_cutoff=CUTOFF,
                   services=["checkout-svc", "payment-svc"],
                   available_sources=["logs", "traces", "metrics", "deployments", "builds", "commits",
                                      "configs", "tickets"],
                   notes="QA fixture incident")
    (snap / "incident.json").write_text(inc.model_dump_json(indent=2), encoding="utf-8")
    logs = [
        _log(1, D + "10:00:01.000Z", "checkout-svc", "INFO", "checkout-svc starting", version="1.0.0",
             commit=SHA_OLD, build_id="b-1", artifact_digest="sha256:" + "1" * 64),
        _log(2, D + "10:00:30.000Z", "checkout-svc", "ERROR", "payment call timed out", order_id="o1"),
        _log(3, D + "10:01:30.000Z", "checkout-svc", "ERROR", "payment call timed out", order_id="o2"),
        _log(4, D + "10:02:30.000Z", "checkout-svc", "ERROR", "payment call timed out", order_id="o3"),
        _log(5, D + "10:02:40.000Z", "checkout-svc", "INFO", "order received", note="see DEMO-9 QA FIXTURE"),
        _log(6, D + "10:05:00.000Z", "checkout-svc", "ERROR", "QA FIXTURE POST-CUTOFF LINE"),
    ]
    _jl(snap / "logs.jsonl", logs)
    span = lambda sid, tid, s, e, st: {"evidence_id": f"span:{sid}", "trace_id": tid, "span_id": sid,  # noqa: E731
                                       "parent_span_id": None, "service": "checkout-svc", "name": "POST /orders",
                                       "kind": "server", "start": s, "end": e, "duration_ms": 500.0,
                                       "status": st, "status_message": "", "attributes": {},
                                       "resource": {"service.version": "1.0.0"}}
    _jl(snap / "spans.jsonl", [span("s1", "t1", D + "10:01:40.000Z", D + "10:01:40.500Z", "error"),
                               span("s2", "t2", D + "10:04:00.000Z", D + "10:04:00.500Z", "error")])
    _jl(snap / "metrics.jsonl", [{"evidence_id": "metric:qa-err", "service": "checkout-svc", "metric": "error_rate",
                                  "interval_s": 5, "points": [[D + "10:01:00.000Z", 0.0], [D + "10:02:00.000Z", 0.5],
                                                              [D + "10:04:00.000Z", 0.9]]}])
    _jl(snap / "deployments.jsonl", [{"evidence_id": "deploy:d-1", "deploy_id": "d-1", "service": "checkout-svc",
                                      "version": "1.1.0", "build_id": "b-2", "artifact_digest": "sha256:" + "2" * 64,
                                      "commit": SHA_NEW, "environment": "demo-local",
                                      "started_at": D + "10:01:00.000Z", "finished_at": D + "10:01:01.000Z",
                                      "actor": "deploy-bot", "trigger": "pipeline", "status": "succeeded"}])
    _jl(snap / "builds.jsonl", [{"evidence_id": "build:b-2", "build_id": "b-2", "service": "checkout-svc",
                                 "version": "1.1.0", "commit": SHA_NEW, "artifact": "x.tar",
                                 "artifact_digest": "sha256:" + "2" * 64, "artifact_bytes": 10,
                                 "source_paths": ["services/checkout"], "started_at": D + "10:00:50.000Z",
                                 "finished_at": D + "10:00:55.000Z", "status": "success",
                                 "pipeline": "local-build", "triggered_by": "ci"}])
    commit = lambda sha, when, msg: {"evidence_id": f"commit:{sha[:7]}", "sha": sha, "parents": [],  # noqa: E731
                                     "author": "QA Fixture", "author_email": "qa@example", "author_date": when,
                                     "committer_date": when, "message": msg,
                                     "files": [{"status": "M", "path": "config/checkout-svc.yaml"}],
                                     "diff": "QA FIXTURE diff", "services": ["checkout-svc"], "fixture": True}
    _jl(snap / "commits.jsonl", [commit(SHA_OLD, "2026-11-01T10:00:00+00:00", "DEMO-3: QA fixture old change"),
                                 commit(SHA_NEW, "2026-11-19T10:00:40+00:00", "DEMO-9: QA fixture retry change")])
    _jl(snap / "configs.jsonl", [{"service": "checkout-svc", "path": "config/checkout-svc.yaml", "commit": SHA_OLD,
                                  "committed_at": "2026-11-01T10:00:00+00:00", "content": "retries: 1\n",
                                  "previous": None},
                                 {"service": "checkout-svc", "path": "config/checkout-svc.yaml", "commit": SHA_NEW,
                                  "committed_at": "2026-11-19T10:00:40+00:00", "content": "retries: 4\n",
                                  "previous": "retries: 1\n"}])
    tickets = [{"key": "DEMO-9", "summary": "QA fixture ticket", "type": "Story", "components": ["checkout-svc"],
                "labels": [], "reporter": "QA", "assignee": None, "created": D + "09:00:00.000Z",
                "description_versions": [{"at": D + "09:00:00.000Z", "text": "QA FIXTURE original description"},
                                         {"at": D + "11:00:00.000Z", "text": "QA FIXTURE POST-CUTOFF DESCRIPTION"}],
                "status_history": [{"at": D + "09:00:00.000Z", "status": "Open"},
                                   {"at": D + "11:00:00.000Z", "status": "Resolved"}],
                "comments": [{"id": "c1", "author": "QA", "at": D + "09:30:00.000Z", "body": "QA FIXTURE comment"},
                             {"id": "c2", "author": "QA", "at": D + "11:00:00.000Z",
                              "body": "QA FIXTURE POST-CUTOFF COMMENT"}],
                "links": [], "fixture": True}]
    (snap / "tickets.json").write_text(json.dumps(tickets), encoding="utf-8")
    (snap / "runtime.json").write_text(json.dumps(runtime or {"failures": [], "coverage": {}}), encoding="utf-8")
    return snap
