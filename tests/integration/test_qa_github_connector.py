"""Agent 4: offline tests for blackbox/connectors/github_api.py (no network).

A fake requests session (TEST FIXTURE) answers by URL. The token used here is a fake
string; the tests check it never appears in any record, error or repr.
"""
from __future__ import annotations

import base64
import json

import pytest
import requests

from blackbox.connectors.github_api import GitHubConnector

FAKE_TOKEN = "ghp_QAFIXTUREFAKETOKEN0123456789abcdef"
SHA = "a" * 40


class FakeResp:
    def __init__(self, status=200, body=None, headers=None, raw_text=None):
        self.status_code = status
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self._data = (raw_text if raw_text is not None else json.dumps(body)).encode()

    def iter_content(self, n):
        for i in range(0, len(self._data), n):
            yield self._data[i:i + n]

    def close(self):
        pass


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, **kw):
        self.calls.append((url, params, headers))
        for key, resp in self.routes.items():
            if url.endswith(key):
                if isinstance(resp, BaseException):
                    raise resp
                return resp
        return FakeResp(404, {"message": "Not Found"})


def conn(routes, **kw):
    s = FakeSession(routes)
    return GitHubConnector("qa/fixture-repo", token=FAKE_TOKEN, session=s, **kw), s


def everything(res) -> str:
    return json.dumps(res.model_dump())


COMMIT = {"sha": SHA, "html_url": "https://github.com/qa/fixture-repo/commit/" + SHA, "url": "u",
          "commit": {"message": "QA FIXTURE: change retry policy\n\nbody",
                     "author": {"name": "QA", "date": "2026-11-19T10:00:00Z"},
                     "committer": {"name": "QA", "date": "2026-11-19T10:05:00Z"}},
          "files": [{"filename": "config/a.yaml", "status": "modified", "patch": "+x\n" * 2000}]}


def test_commit_record_format_and_truncation():
    gh, s = conn({f"/commits/{SHA}": FakeResp(200, COMMIT)})
    res = gh.get_commit(SHA)
    r = res.records[0]
    assert res.status == "ok" and r.evidence_id == "gh:commit:qa/fixture-repo@aaaaaaa"
    assert r.event_time == "2026-11-19T10:00:00.000Z" and r.available_time == "2026-11-19T10:05:00.000Z"
    assert r.fixture is False and r.attributes["live"] is True and r.attributes["sha"] == SHA
    assert {"retrieved_at", "repo", "url", "http_status"} <= set(r.attributes)
    assert r.truncated is True and len(r.content) <= 4000
    assert r.content_ref.startswith("https://github.com/")
    assert s.calls[0][2]["Authorization"] == f"Bearer {FAKE_TOKEN}"     # sent, never stored
    assert FAKE_TOKEN not in everything(res) and FAKE_TOKEN not in repr(gh)


@pytest.mark.parametrize("status,headers,want", [
    (401, {}, "unavailable"), (403, {}, "unavailable"), (404, {}, "unavailable"),
    (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000000"}, "error"),
    (429, {}, "error"), (500, {}, "error"), (503, {}, "error"), (409, {}, "empty"), (422, {}, "empty"),
])
def test_http_status_mapping(status, headers, want):
    gh, _ = conn({"/repos/qa/fixture-repo": FakeResp(status, {"message": f"QA {status}"}, headers)})
    res = gh.repo_metadata()
    assert res.status == want and res.records == []
    if "X-RateLimit-Reset" in headers:
        assert "rate limit" in res.error and "2027-01-15T08:00:00Z" in res.error


def test_network_failures_never_raise_and_never_leak_token():
    for exc in (requests.ConnectionError(f"boom Authorization: Bearer {FAKE_TOKEN}"), requests.Timeout("slow"),
                ValueError(f"weird {FAKE_TOKEN}")):
        gh, _ = conn({"/repos/qa/fixture-repo": exc})
        res = gh.repo_metadata()
        assert res.status == "error" and FAKE_TOKEN not in (res.error or "")


def test_error_body_echoing_token_is_scrubbed():
    gh, _ = conn({"/repos/qa/fixture-repo": FakeResp(500, {"message": f"bad credentials {FAKE_TOKEN}"})})
    assert FAKE_TOKEN not in gh.repo_metadata().error


def test_garbage_json_and_oversize_are_errors():
    gh, _ = conn({"/repos/qa/fixture-repo": FakeResp(200, raw_text="<html>")})
    assert gh.repo_metadata().status == "error"
    gh, _ = conn({"/repos/qa/fixture-repo": FakeResp(200, {"x": "y" * 5000})}, max_bytes=1000)
    assert "max_bytes" in gh.repo_metadata().error


def test_mutable_ref_and_bad_paths_are_refused_without_a_request():
    gh, s = conn({})
    for path, ref in (("config/a.yaml", "main"), ("../etc/passwd", SHA), ("", SHA), ("a.yaml", "")):
        res = gh.get_file(path, ref)
        assert res.status == "error"
    assert s.calls == []
    assert "mutable" in gh.get_file("config/a.yaml", "main").error


def test_get_file_resolves_sha_and_labels_config_source():
    content = base64.b64encode(b"retries: 4\n").decode()
    gh, _ = conn({f"/commits/{SHA[:7]}": FakeResp(200, raw_text=SHA),
                  f"/git/commits/{SHA}": FakeResp(200, COMMIT["commit"]),
                  "/contents/config/a.yaml": FakeResp(200, {"encoding": "base64", "content": content,
                                                            "html_url": "https://github.com/x", "url": "u"})})
    res = gh.get_file("config/a.yaml", SHA[:7])
    r = res.records[0]
    assert r.evidence_id == "gh:file:qa/fixture-repo@aaaaaaa:config/a.yaml" and r.source == "configs"
    assert "retries: 4" in r.content and r.available_time == "2026-11-19T10:05:00.000Z"


def test_issue_cutoff_drops_later_comments_and_flags_edits():
    issue = {"number": 5, "title": "QA", "state": "open", "user": {"login": "qa"}, "comments": 2, "body": "QA body",
             "created_at": "2026-11-19T09:00:00Z", "updated_at": "2026-11-19T12:00:00Z", "html_url": "h", "url": "u"}
    comments = [{"id": 1, "user": {"login": "a"}, "created_at": "2026-11-19T09:30:00Z",
                 "updated_at": "2026-11-19T09:30:00Z", "body": "before", "html_url": "h1", "url": "u1"},
                {"id": 2, "user": {"login": "b"}, "created_at": "2026-11-19T11:00:00Z",
                 "updated_at": "2026-11-19T11:00:00Z", "body": "AFTER CUTOFF", "html_url": "h2", "url": "u2"}]
    gh, _ = conn({"/issues/5": FakeResp(200, issue), "/issues/5/comments": FakeResp(200, comments)})
    res = gh.get_issue(5, cutoff="2026-11-19T10:00:00Z")
    ids = [r.evidence_id for r in res.records]
    assert ids == ["gh:issue:qa/fixture-repo#5", "gh:issue:qa/fixture-repo#5#c1"]
    assert "AFTER CUTOFF" not in everything(res)
    assert res.records[0].attributes["historical_availability"] == "unverified"
    assert res.records[1].attributes["historical_availability"] == "verified"
    assert res.records[0].attributes["comments_after_cutoff_dropped"] == 1
    assert all(r.source == "tickets" for r in res.records)
    gh2, _ = conn({"/issues/5": FakeResp(200, issue)})
    assert gh2.get_issue(5, cutoff="2026-11-19T08:00:00Z").status == "empty"


def test_only_get_requests_are_possible():
    import inspect

    import blackbox.connectors.github_api as m
    src = inspect.getsource(m)
    for verb in (".post(", ".put(", ".patch(", ".delete(", "method=\"POST"):
        assert verb not in src, verb


def test_list_commits_pagination_is_bounded():
    page = [dict(COMMIT, sha=f"{i:040x}") for i in range(2)]
    nxt = {"Link": '<https://api.github.com/repos/qa/fixture-repo/commits?page=2>; rel="next"'}
    gh, s = conn({"/commits": FakeResp(200, page, nxt), "/commits?page=2": FakeResp(200, page, nxt)}, max_pages=2)
    res = gh.list_commits(limit=10)
    assert len(s.calls) == 2 and res.truncated is True and len(res.records) == 4
