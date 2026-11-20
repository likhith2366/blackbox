"""QA: offline tests for blackbox/connectors/capture.py (record/replay). No network.

Uses the fake HTTP session from test_qa_github_connector.py (TEST FIXTURE) and a fake token.
"""
from __future__ import annotations

import json

import pytest
import requests

from blackbox.connectors.capture import (CaptureIntegrityError, RecordingSession, ReplaySession,
                                         capture_dir_for)
from blackbox.connectors.github_api import GitHubConnector

from test_qa_github_connector import COMMIT, FAKE_TOKEN, SHA, FakeResp, FakeSession

ISSUE = {"number": 5, "title": "QA", "state": "open", "user": {"login": "qa"}, "comments": 0, "body": "QA body",
         "created_at": "2026-11-19T09:00:00Z", "updated_at": "2026-11-19T09:00:00Z", "html_url": "h", "url": "u"}


def routes():
    return {f"/commits/{SHA}": FakeResp(200, COMMIT, {"X-RateLimit-Remaining": "4999", "Set-Cookie": "sess=SECRET"}),
            "/issues/5": FakeResp(200, ISSUE),
            "/repos/qa/fixture-repo": FakeResp(503, {"message": "QA upstream down"})}


def record(tmp_path):
    cap = capture_dir_for("LC-QA-0001", root=tmp_path)
    rec = RecordingSession(cap, repo="qa/fixture-repo", inner=FakeSession(routes()))
    gh = GitHubConnector("qa/fixture-repo", token=FAKE_TOKEN, session=rec)
    live = [gh.get_commit(SHA), gh.get_issue(5), gh.repo_metadata()]
    return cap, live


def strip_volatile(res):
    d = res.model_dump()
    for r in d["records"]:
        r["attributes"].pop("retrieved_at", None)
    return d


def test_capture_never_contains_token_auth_or_cookies(tmp_path):
    cap, _ = record(tmp_path)
    blob = "".join(p.read_text(encoding="utf-8") for p in cap.iterdir())
    assert FAKE_TOKEN not in blob
    assert "Authorization" not in blob and "Bearer" not in blob and "SECRET" not in blob
    files = sorted(p.name for p in cap.glob("0*.json"))
    assert len(files) == 3
    m = json.loads((cap / "manifest.json").read_text(encoding="utf-8"))
    assert m["request_count"] == 3 and m["repo"] == "qa/fixture-repo" and set(m["files"]) == set(files)
    first = json.loads((cap / files[0]).read_text(encoding="utf-8"))
    assert first["request"]["url"].endswith(f"/commits/{SHA}")
    assert first["response"]["headers"]["X-RateLimit-Remaining"] == "4999"
    assert json.loads(first["response"]["body_text"]) == COMMIT      # verbatim body


def test_replay_reproduces_live_results_offline(tmp_path):
    cap, live = record(tmp_path)
    gh = GitHubConnector("qa/fixture-repo", token="", session=ReplaySession(cap))
    again = [gh.get_commit(SHA), gh.get_issue(5), gh.repo_metadata()]
    assert [strip_volatile(a) for a in again] == [strip_volatile(b) for b in live]
    assert again[2].status == "error"                     # the recorded 503 replays as an error


def test_replay_refuses_unrecorded_requests(tmp_path):
    cap, _ = record(tmp_path)
    rep = ReplaySession(cap)
    gh = GitHubConnector("qa/fixture-repo", token="", session=rep)
    res = gh.get_issue(6)
    assert res.status == "error" and "not in capture" in res.error
    assert rep.misses and "/issues/6" in rep.misses[0]


def test_replay_detects_tampering(tmp_path):
    cap, _ = record(tmp_path)
    f = sorted(cap.glob("0*.json"))[0]
    f.write_text(f.read_text(encoding="utf-8").replace("QA FIXTURE", "EDITED"), encoding="utf-8")
    with pytest.raises(CaptureIntegrityError):
        ReplaySession(cap)


def test_network_exceptions_are_recorded_and_replayed(tmp_path):
    cap = capture_dir_for("LC-QA-0002", root=tmp_path)
    rec = RecordingSession(cap, repo="qa/fixture-repo",
                           inner=FakeSession({"/repos/qa/fixture-repo": requests.Timeout("QA slow")}))
    live = GitHubConnector("qa/fixture-repo", token=FAKE_TOKEN, session=rec).repo_metadata()
    rep = GitHubConnector("qa/fixture-repo", token="", session=ReplaySession(cap)).repo_metadata()
    assert live.status == rep.status == "error" and "timed out" in rep.error


def test_token_echoed_in_body_is_scrubbed_and_flagged(tmp_path):
    cap = capture_dir_for("LC-QA-0003", root=tmp_path)
    rec = RecordingSession(cap, repo="qa/fixture-repo", inner=FakeSession(
        {"/repos/qa/fixture-repo": FakeResp(500, {"message": f"oops {FAKE_TOKEN}"})}))
    GitHubConnector("qa/fixture-repo", token=FAKE_TOKEN, session=rec).repo_metadata()
    blob = "".join(p.read_text(encoding="utf-8") for p in cap.iterdir())
    assert FAKE_TOKEN not in blob and '"scrubbed": true' in blob


def test_capture_id_is_validated(tmp_path):
    with pytest.raises(ValueError):
        capture_dir_for("../escape", root=tmp_path)
