"""Provenance of a live run: which BlackBox code, which settings file, which model.

The code revision is taken ONCE, when this module is first imported. A long-running process (a sweep or the API
server) keeps running the code it imported even if the checkout moves on later, so reading `git rev-parse HEAD`
at run time can name code that was never loaded. Every live run records the revision at process start, whether
tracked files were modified at that moment, and the sha256 of its settings file.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
_CODE_PATHS = ("blackbox", "scripts", "configs", "demo")


def _git(*args: str, cwd: Path = REPO) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _code_state() -> dict:
    dirty = [l[3:] for l in _git("status", "--porcelain", "--untracked-files=no", "--", *_CODE_PATHS).splitlines()]
    return {"blackbox_commit": _git("rev-parse", "HEAD") or "unknown",
            "blackbox_dirty": bool(dirty), "blackbox_dirty_files": dirty[:50],
            "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


AT_IMPORT = _code_state()


def short_revision() -> str:
    c = AT_IMPORT["blackbox_commit"]
    return (c[:7] if c != "unknown" else c) + ("+dirty" if AT_IMPORT["blackbox_dirty"] else "")


def file_sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def ollama_model_digest(model: str, url: str = "http://127.0.0.1:11434") -> Optional[str]:
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as r:
            for m in json.load(r).get("models", []):
                if m.get("name") == model or m.get("model") == model:
                    return m.get("digest")
    except Exception:
        return None
    return None


def record(config_file: Path, config, **extra) -> dict:
    """Provenance for one live run. `config` is the RunConfig actually used."""
    eff = config.model_dump(mode="json", exclude={"code_revision"})
    code_repo = extra.pop("code_repo", None)
    out = {**AT_IMPORT, "code_revision_note": "revision at process start; the code this process imported",
           "config_file": str(Path(config_file).resolve().relative_to(REPO)).replace("\\", "/")
           if Path(config_file).resolve().is_relative_to(REPO) else str(config_file),
           "config_file_sha256": file_sha256(config_file),
           "config_effective_sha256": hashlib.sha256(json.dumps(eff, sort_keys=True).encode()).hexdigest(),
           "model": config.model, "model_digest": ollama_model_digest(config.model)}
    if code_repo:
        out["code_source"] = {"repo": str(code_repo), "head": _git("rev-parse", "HEAD", cwd=Path(code_repo)) or None}
    out.update(extra)
    return out
