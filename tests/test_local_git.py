"""Local git code source: read-only reads of a throwaway repository."""
from __future__ import annotations

import subprocess

import pytest

from blackbox.connectors.local_git import LocalGitConnector


@pytest.fixture
def repo(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True,
                       env={"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "T",
                            "GIT_COMMITTER_EMAIL": "t@x", "GIT_AUTHOR_DATE": "2026-11-24T03:00:00Z",
                            "GIT_COMMITTER_DATE": "2026-11-24T03:00:00Z", "PATH": __import__("os").environ["PATH"]})
    git("init", "-q")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "a.yaml").write_text("pool_size: 4\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    (tmp_path / "config" / "a.yaml").write_text("pool_size: 1\n")
    git("commit", "-q", "-am", "shrink pool")
    return tmp_path


def test_reads_commits_diffs_and_files_at_a_sha(repo):
    g = LocalGitConnector(str(repo))
    assert g.ready()[0]
    lst = g.list_commits(limit=5)
    assert lst.status == "ok" and [r.title for r in lst.records] == ["shrink pool", "base"]
    c = g.get_commit(lst.records[0].evidence_id)
    assert c.status == "ok" and "-pool_size: 4" in c.records[0].content and "+pool_size: 1" in c.records[0].content
    assert c.records[0].attributes["backend"] == "local-git" and c.records[0].attributes["mode"] == "live"
    f = g.get_file("config/a.yaml", lst.records[1].version)
    assert f.status == "ok" and "pool_size: 4" in f.records[0].content
    assert g.get_file("config/a.yaml", "main").status == "error"          # mutable refs refused
    assert g.get_file("../etc/passwd", lst.records[0].version).status == "error"
    assert g.get_commit("deadbee").status == "empty"
    assert c.records[0].service == "a"                  # config/a.yaml -> service "a"


def test_not_a_repository_is_unavailable(tmp_path):
    assert LocalGitConnector(str(tmp_path)).list_commits().status == "unavailable"


@pytest.fixture
def shop(tmp_path):
    """A repository shaped like the demo shop: two services and a shared HTTP client library."""
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True,
                       env={"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "T",
                            "GIT_COMMITTER_EMAIL": "t@x", "GIT_AUTHOR_DATE": "2026-11-24T03:00:00Z",
                            "GIT_COMMITTER_DATE": "2026-11-24T03:00:00Z", "PATH": __import__("os").environ["PATH"]})
    for d in ("services/checkout", "services/payment", "libs/httpclient"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "services/checkout/VERSION").write_text("1.8.2\n")
    (tmp_path / "services/payment/VERSION").write_text("2.4.1\n")
    (tmp_path / "libs/httpclient/policy.py").write_text("DEFAULT_TIMEOUT_MS = 2000\nMAX_RETRIES = 1\n")
    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    # the retry fault: shared client policy plus the checkout version bump
    (tmp_path / "libs/httpclient/policy.py").write_text("DEFAULT_TIMEOUT_MS = 250\nMAX_RETRIES = 4\n")
    (tmp_path / "services/checkout/VERSION").write_text("1.9.0\n")
    git("commit", "-q", "-am", "retry transient payment failures in shared client")
    # a shared-library-only change
    (tmp_path / "libs/httpclient/policy.py").write_text("DEFAULT_TIMEOUT_MS = 250\nMAX_RETRIES = 3\n")
    git("commit", "-q", "-am", "shared client: one retry fewer")
    return tmp_path


def test_shared_library_plus_checkout_version_is_attributed_to_checkout_only(shop):
    g = LocalGitConnector(str(shop))
    lst = g.list_commits(limit=5)
    by_title = {r.title: r for r in lst.records}
    listed = by_title["retry transient payment failures in shared client"]
    assert listed.service == "checkout-svc"
    fetched = g.get_commit(listed.evidence_id).records[0]
    assert fetched.service == "checkout-svc"
    assert "payment-svc" not in (fetched.service or "")


def test_shared_library_only_commit_names_no_service(shop):
    g = LocalGitConnector(str(shop))
    rec = {r.title: r for r in g.list_commits(limit=5).records}["shared client: one retry fewer"]
    assert rec.service is None                         # unknown: the deployment records say who received it
    assert g.get_commit(rec.evidence_id).records[0].service is None
