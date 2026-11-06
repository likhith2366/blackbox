"""The demo company's source repository: a separate git repo (default ../blackbox-demo-shop).

Its history is published at https://github.com/likhith2366/blackbox-demo-shop (branch main).
That history is TEAM-AUTHORED (labelled as fixture history in snapshots) and authored only by
the two team members below. The healthy baseline is the commit tagged `baseline`
(origin/main at the time of the last sync). Captures reset `main` to that tag and add
real commits at the real time they happen; each capture's commits stay reachable via the
tag `capture/<incident_id>`. Nothing here pushes.

`LocalGitRepo` (demo/repo_export.py) is the only reader used by the snapshot builder; a
GitHub-API implementation could replace it.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from demo.paths import WORKSPACE

# The only identities allowed on commits made by demo tooling.
PEOPLE = {
    "likhith": ("Likhith Vardhan Goruputi", "g.likhithvardhan@gmail.com"),
    "praneeth": ("Praneeth Kadem", "praneethkadem16@gmail.com"),
}
BASELINE_TAG = "baseline"


def git(*args: str, cwd: Path | None = None, env: dict | None = None, check: bool = True) -> str:
    full_env = dict(os.environ)
    full_env.update(env or {})
    r = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
                       cwd=str(cwd or WORKSPACE), env=full_env, capture_output=True,
                       text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def _identity_env(who: str, date: str | None) -> dict:
    name, email = PEOPLE[who]
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    return env


def baseline_sha() -> str:
    return git("rev-parse", f"{BASELINE_TAG}^{{commit}}").strip()


def reset_workspace() -> str:
    """Move `main` back to the published healthy baseline and clean the tree. Returns HEAD sha."""
    if not (WORKSPACE / ".git").exists():
        raise RuntimeError(f"company repo not found at {WORKSPACE}; clone "
                           "https://github.com/likhith2366/blackbox-demo-shop there and tag the baseline")
    base = baseline_sha()
    git("checkout", "-q", "-f", "-B", "main", base)
    git("clean", "-q", "-fdx")
    return head()


def tag(name: str) -> None:
    """Point a tag at the current HEAD (keeps each capture's commits reachable)."""
    git("tag", "-f", name)


def head() -> str:
    return git("rev-parse", "HEAD").strip()


def commit_change(who: str, message: str, edits: list[tuple[str, str, str]],
                  new_files: dict[str, str] | None = None, date: str | None = None) -> str:
    """Apply text edits (path, old, new) and commit at the current time. Returns sha."""
    for path, old, new in edits:
        p = WORKSPACE / path
        text = p.read_text(encoding="utf-8")
        if old not in text:
            raise ValueError(f"edit anchor not found in {path}: {old[:60]!r}")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace(old, new, 1))
    for path, text in (new_files or {}).items():
        p = WORKSPACE / path
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    git("add", "-A")
    git("commit", "-q", "-m", message, env=_identity_env(who, date))
    return head()


def show(commit: str, path: str) -> str:
    return git("show", f"{commit}:{path}")
