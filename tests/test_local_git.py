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


def test_not_a_repository_is_unavailable(tmp_path):
    assert LocalGitConnector(str(tmp_path)).list_commits().status == "unavailable"
