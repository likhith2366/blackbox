"""Contract tests for live connectors (CONTRACT.md 10c).

Targets: blackbox/connectors/github_api.py (QA) and blackbox/connectors/local_logs.py
(Evidence). Each module is tested once it exists; until then the tests skip with a reason.

Discovery (so the tests do not fix method names): a module-level `make_connector(**kw)`
if present, else the first class defined in the module whose name ends in Connector,
Backend or Client. Every public method is called with arguments chosen by parameter name
(sha/ref -> a 40-hex sha, path -> a file path, query -> "retry", number -> 1, ...);
methods with required parameters the factory cannot fill are reported, not called.

HTTP: FIXTURES. Unit tests never touch the network: requests' HTTPAdapter.send is
replaced by recorded/mocked GitHub-shaped responses (labelled fixtures, written by
Evaluation), raw sockets are blocked, and `gh auth token` is blocked so a real token on this
machine can never be read. One opt-in test marked `live` hits the real API and skips
unless BLACKBOX_LIVE_TESTS=1 and the network is reachable.

Checked: BackendResult/EvidenceRecord validity; stable, origin-prefixed evidence ids;
event_time, available_time, attributes.retrieved_at, attributes.live == True,
fixture == False, content_ref; commit pinning (a file fetched by branch name is refused
or labelled with the resolved sha, never id'd by the branch); explicit statuses
(unavailable for no credentials/no access, error for 5xx and rate limit, empty for an
answered query with no match, never an exception); secret exclusion (a fake token injected
via GITHUB_TOKEN appears in no record, error text, exception, log line or stdout).
"""
from __future__ import annotations

import base64
import importlib
import inspect
import io
import json
import logging
import os
import re
import socket
import subprocess

import pytest
import requests
from requests.models import Response

from blackbox.schemas import BackendResult, EvidenceRecord

FAKE_TOKEN = "ghp_TESTSECRET123"
SHA = "7addfee820edbbd240229333f6141050e9589a35"
REPO = "owner/demo-shop"
GH_ID = re.compile(r"^gh:(repo|commit|file|issue|pr|pull|tree|blob|ref|run|job|artifact)[:@#A-Za-z0-9_.\-/]*")
HEXSHA = re.compile(r"^[0-9a-f]{7,40}$")

ARG_VALUES = {
    "sha": SHA, "commit": SHA, "commit_sha": SHA, "ref": SHA, "rev": SHA, "revision": SHA,
    "path": "libs/httpclient/policy.py", "file": "libs/httpclient/policy.py",
    "file_path": "libs/httpclient/policy.py", "filename": "libs/httpclient/policy.py",
    "query": "retry", "q": "retry", "text": "retry", "pattern": "retry", "search": "retry",
    "run_id": 101, "job_id": 202, "number": 1, "issue": 1, "issue_number": 1, "n": 1, "pr": 1, "pull_number": 1, "key": "1",
    "repo": REPO, "repository": REPO, "limit": 5, "max_results": 5, "per_page": 5,
    "service": "checkout-svc", "level": None, "start": None, "end": None, "since": None,
    "until": None, "author": None, "state": None,
}


# --------------------------------------------------------------------------- discovery
def _module(name):
    try:
        return importlib.import_module(f"blackbox.connectors.{name}")
    except ModuleNotFoundError as e:
        if e.name and e.name.startswith("blackbox.connectors"):
            pytest.skip(f"blackbox/connectors/{name}.py not written yet")
        raise


def _ctor_kwargs(fn, extra):
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    kw = {}
    for p in params.values():
        if p.name in extra:
            kw[p.name] = extra[p.name]
        elif p.name in ("repo", "repository"):
            kw[p.name] = REPO
    return kw


def make(mod, extra=None):
    extra = extra or {}
    if hasattr(mod, "make_connector"):
        return mod.make_connector(**_ctor_kwargs(mod.make_connector, extra))
    own = [cls for _, cls in inspect.getmembers(mod, inspect.isclass)
           if cls.__module__ == mod.__name__ and not cls.__name__.startswith("_")
           and not issubclass(cls, BaseException)]
    for cls in own:
        if cls.__name__.endswith(("Connector", "Backend", "Client")):
            return cls(**_ctor_kwargs(cls, extra))
    if len(own) == 1:                       # e.g. local_logs.LocalLogs
        return own[0](**_ctor_kwargs(own[0], extra))
    pytest.skip(f"{mod.__name__}: no make_connector() and no *Connector/*Backend/*Client class "
                "to discover (see coordination/requests.md)")


def public_methods(obj):
    out, skipped = [], []
    for name, fn in inspect.getmembers(obj, callable):
        if name.startswith("_") or inspect.isclass(fn) or name in ("close", "make_connector"):
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        kw, ok = {}, True
        for p in sig.parameters.values():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            if p.name in ARG_VALUES:
                if ARG_VALUES[p.name] is not None or p.default is p.empty:
                    kw[p.name] = ARG_VALUES[p.name]
            elif p.default is p.empty:
                ok = False
        (out if ok else skipped).append((name, fn, kw))
    return out, skipped


def call_all(obj):
    """[(method, kwargs, result | None, exception_text | None)]"""
    methods, _ = public_methods(obj)
    res = []
    for name, fn, kw in methods:
        try:
            res.append((name, kw, fn(**kw), None))
        except Exception as e:
            res.append((name, kw, None, f"{type(e).__name__}: {e}"))
    return res


def _results(calls, include_lists: bool = False):
    """BackendResults; with include_lists, plain record lists (e.g. export()) are wrapped so
    their records are schema-checked too. Lists carry no status, so status checks skip them."""
    out = []
    for n, _, r, _ in calls:
        if isinstance(r, BackendResult):
            out.append((n, r))
        elif include_lists and isinstance(r, list) and all(isinstance(x, EvidenceRecord) for x in r):
            out.append((n, BackendResult(status="ok" if r else "empty", records=r)))
    return out


# --------------------------------------------------------------------------- HTTP fixtures
def _commit_json(sha=SHA):
    return {"sha": sha, "html_url": f"https://github.com/{REPO}/commit/{sha}",
            "commit": {"message": "httpclient: tune retry policy", "author": {
                "name": "dev", "date": "2026-11-20T02:03:59Z"},
                "committer": {"name": "dev", "date": "2026-11-20T02:03:59Z"}},
            "files": [{"filename": "libs/httpclient/policy.py", "status": "modified",
                       "additions": 2, "deletions": 2, "patch": "@@ -1 +1 @@\n-MAX=1\n+MAX=4"}],
            "parents": [{"sha": "2950840e6b3e0a99d6e26770dc418452aab460fc"}]}


def _issue_json(n=1):
    return {"number": n, "title": "Checkout latency", "body": "seen after deploy",
            "state": "open", "html_url": f"https://github.com/{REPO}/issues/{n}",
            "created_at": "2026-11-20T01:00:00Z", "updated_at": "2026-11-20T01:30:00Z",
            "user": {"login": "dev"}, "comments": 0}


def _run_json():
    return {"id": 101, "name": "ci", "run_number": 2, "run_attempt": 1, "event": "push",
            "head_branch": "main", "head_sha": SHA, "status": "completed", "conclusion": "success",
            "workflow_id": 9, "path": ".github/workflows/ci.yml",
            "created_at": "2026-11-20T02:00:00Z", "run_started_at": "2026-11-20T02:00:05Z",
            "updated_at": "2026-11-20T02:02:00Z", "head_commit": {"message": "ci"},
            "html_url": f"https://github.com/{REPO}/actions/runs/101",
            "url": f"https://api.github.com/repos/{REPO}/actions/runs/101"}


def _job_json():
    return {"id": 202, "run_id": 101, "name": "test", "status": "completed", "conclusion": "success",
            "head_sha": SHA, "runner_name": "gh", "started_at": "2026-11-20T02:00:10Z",
            "completed_at": "2026-11-20T02:01:50Z", "created_at": "2026-11-20T02:00:06Z",
            "steps": [{"name": "pytest", "status": "completed", "conclusion": "success", "number": 1,
                       "started_at": "2026-11-20T02:00:20Z", "completed_at": "2026-11-20T02:01:40Z"}],
            "html_url": f"https://github.com/{REPO}/actions/runs/101/job/202",
            "url": f"https://api.github.com/repos/{REPO}/actions/jobs/202"}


def fixture_body(url: str, mode: str, accept: str = ""):
    """(status_code, json_body | raw str, headers) for a GitHub API URL under a scenario mode."""
    path = url.split("?")[0]
    if mode == "500":
        return 502, {"message": "Server Error"}, {}
    if mode == "rate":
        return 403, {"message": "API rate limit exceeded"}, {"X-RateLimit-Remaining": "0"}
    if mode == "401":
        return 401, {"message": "Bad credentials"}, {}
    if mode == "404":
        return 404, {"message": "Not Found"}, {}
    empty = mode == "empty"
    if re.search(r"/search/(issues|commits)", path):
        items = [] if empty else ([_commit_json()] if "commits" in path else [_issue_json()])
        return 200, {"total_count": len(items), "items": items}, {}
    if path.endswith("/actions/runs"):
        runs = [] if empty else [_run_json()]
        return 200, {"total_count": len(runs), "workflow_runs": runs}, {}
    if re.search(r"/actions/runs/\d+/jobs$", path):
        jobs = [] if empty else [_job_json()]
        return 200, {"total_count": len(jobs), "jobs": jobs}, {}
    if re.search(r"/actions/runs/\d+/artifacts$", path):
        return 200, {"total_count": 0, "artifacts": []}, {}
    if re.search(r"/actions/runs/\d+$", path):
        return (404, {"message": "Not Found"}, {}) if empty else (200, _run_json(), {})
    if re.search(r"/actions/jobs/\d+/logs$", path):
        return (404, {"message": "Not Found"}, {}) if empty else (
            200, "2026-11-20T02:00:20Z pytest\n2026-11-20T02:01:40Z 12 passed\n",
            {"Content-Type": "text/plain"})
    if re.search(r"/actions/jobs/\d+$", path):
        return (404, {"message": "Not Found"}, {}) if empty else (200, _job_json(), {})
    if re.search(r"/git/commits/[0-9a-f]{40}$", path):
        return 200, {"sha": SHA, "author": {"date": "2026-11-20T02:03:00Z"},
                     "committer": {"date": "2026-11-20T02:03:59Z"}, "message": "m"}, {}
    if re.search(r"/commits/[^/]+$", path):
        if empty:
            return 404, {"message": "No commit found"}, {}
        if "vnd.github.sha" in accept:              # ref -> sha resolution returns raw text
            return 200, SHA, {"Content-Type": "text/plain"}
        return 200, _commit_json(), {}
    if path.endswith("/commits"):
        return 200, ([] if empty else [_commit_json()]), {}
    if "/contents/" in path:
        if empty:
            return 404, {"message": "Not Found"}, {}
        return 200, {"type": "file", "encoding": "base64", "path": "libs/httpclient/policy.py",
                     "sha": "b" * 40, "size": 9, "content": base64.b64encode(b"MAX = 4\n").decode(),
                     "html_url": f"https://github.com/{REPO}/blob/{SHA}/libs/httpclient/policy.py"}, {}
    if re.search(r"/issues/\d+$", path) or re.search(r"/pulls/\d+$", path):
        return (404, {"message": "Not Found"}, {}) if empty else (200, _issue_json(), {})
    if path.endswith("/issues") or path.endswith("/pulls"):
        return 200, ([] if empty else [_issue_json()]), {}
    if re.search(r"/(git/ref|git/refs|branches)/", path) or path.endswith("/branches/main"):
        return 200, {"object": {"sha": SHA, "type": "commit"}, "commit": {"sha": SHA},
                     "name": "main"}, {}
    if re.search(r"/repos/[^/]+/[^/]+$", path):
        return 200, {"full_name": REPO, "default_branch": "main", "private": False, "size": 120,
                     "created_at": "2026-01-01T00:00:00Z", "pushed_at": "2026-11-20T02:04:00Z",
                     "updated_at": "2026-11-20T02:04:00Z", "html_url": f"https://github.com/{REPO}",
                     "url": f"https://api.github.com/repos/{REPO}"}, {}
    return 404, {"message": "Not Found"}, {}


@pytest.fixture
def http(monkeypatch):
    """Mocked HTTP + blocked network/gh. Returns a dict to set the mode and read requests."""
    state = {"mode": "ok", "requests": []}

    def send(self, request, **kw):
        state["requests"].append(request)
        if state["mode"] == "conn_headers":
            # adversarial: an exception whose text carries the request headers
            raise requests.ConnectionError(f"connection failed for {request.url} "
                                           f"headers={dict(request.headers)}")
        code, body, headers = fixture_body(request.url, state["mode"], request.headers.get("Accept", ""))
        r = Response()
        r.status_code, r.url, r.request = code, request.url, request
        r._content = (body if isinstance(body, str) else json.dumps(body)).encode()
        r._content_consumed = True              # iter_content(stream=True) replays _content
        r.raw = io.BytesIO(r._content)
        r.headers.update({"Content-Type": "application/json", **headers})
        r.encoding = "utf-8"
        return r

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)

    def no_net(*a, **k):
        raise OSError("network blocked in unit tests")
    monkeypatch.setattr(socket.socket, "connect", no_net)

    def no_gh(*a, **k):
        raise FileNotFoundError("gh blocked in unit tests")
    for fn in ("run", "check_output", "Popen", "call", "check_call"):
        monkeypatch.setattr(subprocess, fn, no_gh)
    monkeypatch.setenv("GITHUB_TOKEN", FAKE_TOKEN)
    return state


# --------------------------------------------------------------------------- shared checks
def check_records(results, id_check):
    problems = []
    for name, r in results:
        try:
            BackendResult.model_validate(r.model_dump())
        except Exception as e:
            problems.append(f"{name}: BackendResult invalid: {e}")
        for rec in r.records:
            try:
                EvidenceRecord.model_validate(rec.model_dump())
            except Exception as e:
                problems.append(f"{name}: EvidenceRecord invalid: {e}")
                continue
            a = rec.attributes
            if not id_check(rec.evidence_id):
                problems.append(f"{name}: evidence id {rec.evidence_id!r} not origin-prefixed")
            for f in ("event_time", "available_time"):
                if not getattr(rec, f):
                    problems.append(f"{name}: {rec.evidence_id} missing {f}")
            if not a.get("retrieved_at"):
                problems.append(f"{name}: {rec.evidence_id} missing attributes.retrieved_at")
            if a.get("live") is not True:
                problems.append(f"{name}: {rec.evidence_id} attributes.live is not True")
            if rec.fixture:
                problems.append(f"{name}: {rec.evidence_id} fixture=True on a live connector")
            if not rec.content_ref:
                problems.append(f"{name}: {rec.evidence_id} content_ref empty")
    return problems


def assert_no_secret(calls, capsys, caplog, where):
    leaks = []
    for name, kw, r, exc in calls:
        if r is not None and FAKE_TOKEN in (r.model_dump_json() if hasattr(r, "model_dump_json") else repr(r)):
            leaks.append(f"{where}: {name} result contains the token")
        if exc and FAKE_TOKEN in exc:
            leaks.append(f"{where}: {name} exception text contains the token")
    out = capsys.readouterr()
    if FAKE_TOKEN in out.out + out.err:
        leaks.append(f"{where}: token printed to stdout/stderr")
    if FAKE_TOKEN in caplog.text:
        leaks.append(f"{where}: token in log output")
    assert not leaks, leaks


# =========================================================================== GitHub
@pytest.fixture
def gh(http):
    return make(_module("github_api"))


def test_github_records_valid_and_ids_stable(gh, http):
    calls = call_all(gh)
    assert calls, "no public methods could be exercised"
    raised = [(n, e) for n, _, _, e in calls if e]
    assert not raised, f"methods raised instead of returning BackendResult: {raised}"
    res = _results(calls)
    errs = [(n, r.status, r.error) for n, r in res if r.status not in ("ok", "empty")]
    assert not errs, f"methods failed against the recorded OK fixtures: {errs}"
    assert any(r.records for _, r in res), "no method returned records from the recorded fixtures"
    problems = check_records(res, lambda i: bool(GH_ID.match(i)))
    assert not problems, problems
    again = _results(call_all(gh))
    ids1 = {n: [x.evidence_id for x in r.records] for n, r in res}
    ids2 = {n: [x.evidence_id for x in r.records] for n, r in again}
    assert ids1 == ids2, "evidence ids are not stable across identical calls"


def test_github_no_writes(gh, http):
    call_all(gh)
    methods = {r.method for r in http["requests"]}
    assert methods <= {"GET", "HEAD"}, f"non-read HTTP methods used: {methods}"


@pytest.mark.parametrize("mode,expected", [
    ("401", {"unavailable"}), ("500", {"error"}), ("rate", {"error"}),
])
def test_github_status_semantics(gh, http, mode, expected):
    http["mode"] = mode
    calls = call_all(gh)
    raised = [(n, e) for n, _, _, e in calls if e]
    assert not raised, f"[{mode}] methods raised: {raised}"
    bad = [(n, r.status) for n, r in _results(calls) if r.status not in expected]
    assert not bad, f"[{mode}] expected status in {expected}, got {bad}"
    assert all(not r.records for _, r in _results(calls))


def test_github_empty_is_empty(gh, http):
    http["mode"] = "empty"
    calls = call_all(gh)
    assert not [(n, e) for n, _, _, e in calls if e]
    bad = [(n, r.status) for n, r in _results(calls)
           if ("repo" not in n and "metadata" not in n)    # repository metadata exists regardless
           and (r.status not in ("empty", "unavailable") or r.records)]
    # a lookup of a missing object may be "empty"; "error" would misreport an answered query
    assert not bad, f"answered-but-no-match must be 'empty': {bad}"


def test_github_commit_pinning(gh, http):
    methods, _ = public_methods(gh)
    file_methods = [(n, fn, kw) for n, fn, kw in methods if "file" in n or "content" in n]
    if not file_methods:
        pytest.skip("no file/content method found on the GitHub connector")
    for name, fn, kw in file_methods:
        refs = [p for p in ("ref", "sha", "commit", "rev", "revision") if p in kw]
        if not refs:
            continue
        r = fn(**{**kw, refs[0]: "main"})
        for rec in r.records:
            sha = str(rec.attributes.get("sha", ""))
            assert HEXSHA.match(sha), f"{name}(ref='main') record not labelled with a resolved sha: {rec.attributes}"
            assert "@main" not in rec.evidence_id, f"{name}: evidence id pinned to a branch: {rec.evidence_id}"


@pytest.mark.parametrize("mode", ["ok", "empty", "401", "500", "rate", "conn_headers"])
def test_github_secret_never_leaks(gh, http, mode, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    http["mode"] = mode
    calls = call_all(gh)
    assert_no_secret(calls, capsys, caplog, f"github[{mode}]")
    # the token may only travel in the Authorization header of outgoing requests
    for req in http["requests"]:
        assert FAKE_TOKEN not in req.url, "token sent in a URL"
        body = req.body.decode("utf-8", "replace") if isinstance(req.body, bytes) else str(req.body or "")
        assert FAKE_TOKEN not in body, "token sent in a request body"


def test_github_without_credentials(monkeypatch, http):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    gh = make(_module("github_api"))
    calls = call_all(gh)
    assert not [(n, e) for n, _, _, e in calls if e], "methods raised without credentials"
    bad = [(n, r.status) for n, r in _results(calls) if r.status not in ("ok", "empty", "unavailable")]
    assert not bad, f"missing credentials must be 'unavailable' (or anonymous ok/empty): {bad}"


@pytest.mark.live
def test_github_live_opt_in(capsys, caplog):
    if os.environ.get("BLACKBOX_LIVE_TESTS") != "1":
        pytest.skip("live test: set BLACKBOX_LIVE_TESTS=1 to run against the real GitHub API")
    try:
        socket.create_connection(("api.github.com", 443), timeout=5).close()
    except OSError:
        pytest.skip("no network")
    gh = make(_module("github_api"), {"repo": os.environ.get("BLACKBOX_LIVE_REPO", REPO)})
    calls = call_all(gh)
    assert not [(n, e) for n, _, _, e in calls if e]
    token = os.environ.get("GITHUB_TOKEN")
    for _, _, r, _ in calls:
        if token and r is not None:
            assert token not in r.model_dump_json()


# =========================================================================== local logs
LOG_LINES = [
    {"ts": "2026-11-20T02:02:59.071Z", "level": "INFO", "service": "checkout-svc",
     "msg": "checkout-svc starting"},
    {"ts": "2026-11-20T02:04:10.500Z", "level": "ERROR", "service": "checkout-svc",
     "msg": "payment call failed: retry 3/4 timeout after 250ms"},
]


@pytest.fixture
def log_dir(tmp_path):
    """FIXTURE: two log lines in the demo telemetry JSONL format (written by Evaluation)."""
    d = tmp_path / "logs"
    d.mkdir()
    (d / "checkout-svc.jsonl").write_text("\n".join(json.dumps(x) for x in LOG_LINES) + "\n",
                                          encoding="utf-8")
    return d


def _local(log_dir):
    mod = _module("local_logs")
    extra = {k: log_dir for k in ("root", "path", "log_dir", "logs_dir", "directory", "dir", "base")}
    extra["telemetry_dir"] = log_dir.parent          # LocalLogs reads <telemetry_dir>/logs/*.jsonl
    return make(mod, extra)


def test_local_logs_records_valid_and_stable(http, log_dir):
    c = _local(log_dir)
    calls = call_all(c)
    assert calls, "no public methods could be exercised"
    assert not [(n, e) for n, _, _, e in calls if e], "methods raised"
    res = _results(calls, include_lists=True)
    assert any(r.records for _, r in res), "no method returned records from the fixture log file"
    problems = check_records(res, lambda i: i.startswith("log:"))
    assert not problems, problems
    again = _results(call_all(c), include_lists=True)
    assert {n: [x.evidence_id for x in r.records] for n, r in res} == \
           {n: [x.evidence_id for x in r.records] for n, r in again}


def test_local_logs_missing_dir_is_unavailable(http, tmp_path):
    c = _local(tmp_path / "does-not-exist" / "logs")
    calls = call_all(c)
    assert not [(n, e) for n, _, _, e in calls if e], "methods raised on a missing directory"
    bad = [(n, r.status) for n, r in _results(calls) if r.status not in ("unavailable",)]
    assert not bad, f"missing log directory must be 'unavailable': {bad}"


def test_local_logs_secret_never_leaks(http, log_dir, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    assert_no_secret(call_all(_local(log_dir)), capsys, caplog, "local_logs")
