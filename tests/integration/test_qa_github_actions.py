"""QA: offline tests for the read-only GitHub Actions methods (no network).

The fake session (TEST FIXTURE, from test_qa_github_connector.py) answers by URL suffix. The failed-job
path is exercised here only. Live verification so far covered a successful run, so failed-job log
retrieval against GitHub is UNVERIFIED.
"""
from __future__ import annotations

import json

from blackbox.connectors.capture import RecordingSession, ReplaySession, capture_dir_for
from blackbox.connectors.github_api import GitHubConnector

from test_qa_github_connector import FAKE_TOKEN, FakeResp, FakeSession

SHA = "b7ae74f" + "0" * 33
RUN = {"id": 11, "name": "ci", "run_number": 3, "run_attempt": 1, "event": "push", "head_branch": "main",
       "status": "completed", "conclusion": "failure", "head_sha": SHA, "workflow_id": 9, "path": ".github/workflows/ci.yml",
       "created_at": "2026-11-20T02:30:53Z", "run_started_at": "2026-11-20T02:30:53Z", "updated_at": "2026-11-20T02:31:09Z",
       "html_url": "https://github.com/qa/fixture-repo/actions/runs/11", "url": "u",
       "head_commit": {"message": "QA FIXTURE change\n\nbody"}}
JOB_FAIL = {"id": 21, "run_id": 11, "name": "test", "status": "completed", "conclusion": "failure", "head_sha": SHA,
            "started_at": "2026-11-20T02:30:55Z", "completed_at": "2026-11-20T02:31:08Z", "html_url": "h", "url": "u",
            "steps": [{"number": 1, "name": "Set up job", "status": "completed", "conclusion": "success"},
                      {"number": 2, "name": "Run pytest -q tests", "status": "completed", "conclusion": "failure"}]}
JOB_OK = dict(JOB_FAIL, id=22, conclusion="success")


def conn(routes, **kw):
    s = FakeSession(routes)
    return GitHubConnector("qa/fixture-repo", token=FAKE_TOKEN, session=s, **kw), s


def test_runs_link_to_commit_evidence():
    gh, _ = conn({"/actions/runs": FakeResp(200, {"total_count": 1, "workflow_runs": [RUN]})})
    res = gh.list_workflow_runs(limit=5)
    r = res.records[0]
    assert r.evidence_id == "gh:run:qa/fixture-repo#11" and r.source == "builds"
    assert r.attributes["commit_evidence_id"] == "gh:commit:qa/fixture-repo@b7ae74f"
    assert r.attributes["head_sha"] == SHA and r.attributes["live"] is True and r.fixture is False
    assert r.available_time == "2026-11-20T02:31:09.000Z" and r.attributes["conclusion"] == "failure"


def test_get_run_returns_jobs_and_steps():
    gh, _ = conn({"/actions/runs/11": FakeResp(200, RUN),
                  "/actions/runs/11/jobs": FakeResp(200, {"total_count": 1, "jobs": [JOB_FAIL]})})
    res = gh.get_run(11)
    assert [r.evidence_id for r in res.records] == ["gh:run:qa/fixture-repo#11", "gh:job:qa/fixture-repo#21"]
    job = res.records[1]
    assert "2. Run pytest -q tests: completed/failure" in job.content
    assert job.attributes["steps"][1]["conclusion"] == "failure"


def test_failed_job_log_keeps_the_tail_and_scrubs_tokens():
    log = "\n".join(f"2026-11-20T02:31:0{i % 10}Z line {i}" for i in range(5000)) + f"\nFAILED test_x token={FAKE_TOKEN}\n"
    gh, s = conn({"/actions/jobs/21": FakeResp(200, JOB_FAIL), "/actions/jobs/21/logs": FakeResp(200, raw_text=log)})
    res = gh.get_job_log(21)
    rec = res.records[0]
    assert res.status == "ok" and rec.attributes["log_included"] is True
    assert "FAILED test_x" in rec.content and rec.truncated is True and len(rec.content) <= 4000
    assert FAKE_TOKEN not in json.dumps(res.model_dump())


def test_successful_job_log_is_empty_unless_asked():
    gh, s = conn({"/actions/jobs/22": FakeResp(200, JOB_OK), "/actions/jobs/22/logs": FakeResp(200, raw_text="ok log")})
    res = gh.get_job_log(22)
    assert res.status == "empty" and "failed jobs only" in res.error
    assert not any(c[0].endswith("/logs") for c in s.calls)          # log endpoint not even requested
    assert gh.get_job_log(22, failed_only=False).status == "ok"


def test_expired_or_missing_logs_are_unavailable():
    for code in (404, 410):
        gh, _ = conn({"/actions/jobs/21": FakeResp(200, JOB_FAIL),
                      "/actions/jobs/21/logs": FakeResp(code, {"message": "logs expired"})})
        assert gh.get_job_log(21).status == "unavailable"


def test_oversized_log_is_bounded():
    huge = "x" * 3_000_000
    gh, _ = conn({"/actions/jobs/21": FakeResp(200, JOB_FAIL), "/actions/jobs/21/logs": FakeResp(200, raw_text=huge)},
                 max_bytes=100_000)
    res = gh.get_job_log(21, tail_bytes=50_000)
    assert res.status == "ok" and res.records[0].truncated is True


def test_artifacts_are_metadata_only():
    arts = {"total_count": 1, "artifacts": [{"id": 31, "name": "dist", "size_in_bytes": 1234, "expired": False,
                                             "digest": "sha256:" + "a" * 64, "created_at": "2026-11-20T02:31:00Z",
                                             "expires_at": "2027-02-18T02:31:00Z", "url": "u",
                                             "archive_download_url": "https://api.github.com/x/zip",
                                             "workflow_run": {"head_sha": SHA}}]}
    gh, s = conn({"/actions/runs/11/artifacts": FakeResp(200, arts)})
    res = gh.list_run_artifacts(11)
    r = res.records[0]
    assert r.evidence_id == "gh:artifact:qa/fixture-repo#31" and r.attributes["size_in_bytes"] == 1234
    assert r.attributes["digest"].startswith("sha256:") and "not downloaded" in r.content
    assert "archive_download_url" not in json.dumps(r.model_dump()) and len(s.calls) == 1
    gh2, _ = conn({"/actions/runs/11/artifacts": FakeResp(200, {"total_count": 0, "artifacts": []})})
    assert gh2.list_run_artifacts(11).status == "empty"


def test_bad_ids_refused_without_request():
    gh, s = conn({})
    for res in (gh.get_run("abc"), gh.get_job_log(-1), gh.list_run_artifacts("x")):
        assert res.status == "error"
    assert s.calls == []


def test_actions_record_and_replay(tmp_path):
    routes = {"/actions/runs/11": FakeResp(200, RUN), "/actions/runs/11/jobs": FakeResp(200, {"jobs": [JOB_FAIL]}),
              "/actions/jobs/21": FakeResp(200, JOB_FAIL), "/actions/jobs/21/logs": FakeResp(200, raw_text="QA FAIL LOG")}
    cap = capture_dir_for("LC-QA-ACT", root=tmp_path)
    live_gh = GitHubConnector("qa/fixture-repo", token=FAKE_TOKEN, session=RecordingSession(cap, inner=FakeSession(routes)))
    live = [live_gh.get_run(11), live_gh.get_job_log(21)]
    rep_gh = GitHubConnector("qa/fixture-repo", token="", session=ReplaySession(cap))
    again = [rep_gh.get_run(11), rep_gh.get_job_log(21)]
    assert [[r.content for r in x.records] for x in live] == [[r.content for r in x.records] for x in again]
    assert FAKE_TOKEN not in "".join(p.read_text(encoding="utf-8") for p in cap.iterdir())


def test_no_write_endpoints_exist():
    import inspect

    import blackbox.connectors.github_api as m
    src = inspect.getsource(m)
    for bad in ("/rerun", "/cancel", "/dispatches", "archive_download_url\"]", ".post(", ".delete("):
        assert bad not in src, bad
