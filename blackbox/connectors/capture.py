"""Record / replay of live connector HTTP traffic (CONTRACT.md s10c). Owner: Agent 4.

    rec = RecordingSession(capture_dir_for("LC-0001"), repo="owner/name")
    GitHubConnector("owner/name", session=rec)          # live pilot: responses saved verbatim
    rep = ReplaySession(capture_dir_for("LC-0001"))
    GitHubConnector("owner/name", session=rep, token="")  # re-run offline, byte-identical answers

Layout (one directory per capture and source):
    scenarios/live_captures/<capture_id>/github/
        manifest.json                  format, capture_id, source, repo, created_at, updated_at,
                                       request_count, files {name: sha256}
        0001_<key8>.json ...           one exchange per file, in request order

Exchange file (format_version 1):
    {"format_version": 1, "seq": 1, "retrieved_at": "...Z",
     "request":  {"method": "GET", "url": "<full url incl. sorted query>", "accept": "<Accept header>"},
     "response": {"status": 200, "headers": {<allow-listed headers>}, "body_text": "<verbatim>"}
                 | {"status": 200, ..., "body_b64": "..."}   (non-UTF-8 bodies)
     "exception": null | {"type": "Timeout" | "ConnectionError" | ..., "message": "..."},
     "scrubbed": false}

Secrets: request headers are NOT stored except Accept. Authorization, cookies and every other
request header are dropped; response headers are allow-listed. If a token-shaped string
(or the session's known token) ever appears in a URL, header or body it is replaced by "***"
and the exchange is marked "scrubbed": true (the only case in which the body is not verbatim).

Replay matches on (method, url, accept). Repeated identical requests are served in recorded
order (the last answer repeats). A request that was never recorded raises ReplayMiss, which
GitHubConnector reports as status "error" with "not in capture". Files are checked against the
manifest sha256 when a ReplaySession is created; a mismatch raises CaptureIntegrityError.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

FORMAT_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_ROOT = REPO_ROOT / "scenarios" / "live_captures"
KEEP_RESPONSE_HEADERS = ("Content-Type", "ETag", "Last-Modified", "Link", "Date", "X-GitHub-Request-Id",
                         "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "X-RateLimit-Used",
                         "X-RateLimit-Resource")
MAX_RECORD_BYTES = 5_000_000
_TOKEN_RE = re.compile(r"(gh[opsur]_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,})")
_CAPTURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ReplayMiss(requests.RequestException):
    """A replayed run asked for something the capture does not contain."""


class CaptureIntegrityError(ValueError):
    pass


def _now() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def capture_dir_for(capture_id: str, source: str = "github", root: Optional[Path] = None) -> Path:
    if not _CAPTURE_ID_RE.match(capture_id or ""):
        raise ValueError(f"invalid capture id {capture_id!r}")
    return Path(root or CAPTURE_ROOT) / capture_id / source


def canonical_url(url: str, params: Optional[dict] = None) -> str:
    return requests.Request("GET", url, params=params).prepare().url


def request_key(method: str, url: str, accept: str) -> str:
    return f"{method.upper()} {url} accept={accept}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class CapturedResponse:
    """Minimal stand-in for requests.Response as used by GitHubConnector."""

    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status_code = status
        self.headers = requests.structures.CaseInsensitiveDict(headers)
        self._body = body

    def iter_content(self, chunk_size: int = 65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------------- record
class RecordingSession:
    def __init__(self, capture_dir: Path, repo: str = "", inner: Optional[requests.Session] = None,
                 capture_id: Optional[str] = None, known_secrets: tuple[str, ...] = ()):
        self.dir = Path(capture_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.inner = inner or requests.Session()
        self._secrets = tuple(s for s in known_secrets if s)
        mpath = self.dir / "manifest.json"
        if mpath.exists():
            self.manifest = json.loads(mpath.read_text(encoding="utf-8"))
            if repo and self.manifest.get("repo") and self.manifest["repo"] != repo:
                raise ValueError(f"capture {self.dir} belongs to {self.manifest['repo']}, not {repo}")
        else:
            self.manifest = {"format_version": FORMAT_VERSION, "capture_id": capture_id or self.dir.parent.name,
                             "source": self.dir.name, "repo": repo, "live": True, "created_at": _now(),
                             "updated_at": None, "request_count": 0, "files": {},
                             "note": "Verbatim live HTTP responses (read-only GETs). Replay with ReplaySession."}
        self.seq = int(self.manifest.get("request_count", 0))

    def _scrub(self, text: str, header_tokens: tuple[str, ...]) -> tuple[str, bool]:
        out = text
        for s in self._secrets + header_tokens:
            out = out.replace(s, "***")
        out = _TOKEN_RE.sub("***", out)
        return out, out != text

    def get(self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None, **kw: Any):
        headers = dict(headers or {})
        auth = headers.get("Authorization", "")
        header_tokens = tuple(t for t in (auth.split(" ", 1)[-1] if auth else "",) if len(t) >= 8)
        full = canonical_url(url, params)
        accept = headers.get("Accept", "")
        retrieved = _now()
        exc_info, status, keep, body = None, None, {}, b""
        try:
            resp = self.inner.get(url, params=params, headers=headers, **kw)
            status = resp.status_code
            for chunk in resp.iter_content(65536):
                body += chunk
                if len(body) > MAX_RECORD_BYTES:
                    break
            try:
                resp.close()
            except Exception:
                pass
            keep = {k: resp.headers[k] for k in KEEP_RESPONSE_HEADERS if k in resp.headers}
        except requests.RequestException as exc:
            exc_info = exc
        scrubbed = False
        url_s, s1 = self._scrub(full, header_tokens)
        keep_s = {}
        for k, v in keep.items():
            keep_s[k], s2 = self._scrub(str(v), header_tokens)
            scrubbed = scrubbed or s2
        entry: dict[str, Any] = {"format_version": FORMAT_VERSION, "seq": self.seq + 1, "retrieved_at": retrieved,
                                 "request": {"method": "GET", "url": url_s, "accept": accept},
                                 "response": None, "exception": None, "scrubbed": scrubbed or s1}
        if exc_info is not None:
            msg, s3 = self._scrub(str(exc_info), header_tokens)
            entry["exception"] = {"type": type(exc_info).__name__, "message": msg[:500]}
            entry["scrubbed"] = entry["scrubbed"] or s3
        else:
            try:
                text = body.decode("utf-8")
                text_s, s4 = self._scrub(text, header_tokens)
                entry["response"] = {"status": status, "headers": keep_s, "body_text": text_s}
                entry["scrubbed"] = entry["scrubbed"] or s4
                if s4:
                    body = text_s.encode("utf-8")
            except UnicodeDecodeError:
                entry["response"] = {"status": status, "headers": keep_s,
                                     "body_b64": base64.b64encode(body).decode("ascii")}
            entry["response"]["body_truncated_at"] = MAX_RECORD_BYTES if len(body) > MAX_RECORD_BYTES else None
        self.seq += 1
        key8 = hashlib.sha256(request_key("GET", url_s, accept).encode()).hexdigest()[:8]
        name = f"{self.seq:04d}_{key8}.json"
        path = self.dir / name
        _write_atomic(path, json.dumps(entry, indent=1, ensure_ascii=False))
        self.manifest["files"][name] = _sha256(path)
        self.manifest["request_count"] = self.seq
        self.manifest["updated_at"] = _now()
        _write_atomic(self.dir / "manifest.json", json.dumps(self.manifest, indent=2))
        if exc_info is not None:
            raise exc_info
        return CapturedResponse(status, dict(resp.headers), body)


# ---------------------------------------------------------------------------------- replay
_EXC_TYPES = {"Timeout": requests.Timeout, "ReadTimeout": requests.ReadTimeout,
              "ConnectTimeout": requests.ConnectTimeout, "ConnectionError": requests.ConnectionError}


class ReplaySession:
    def __init__(self, capture_dir: Path, verify: bool = True):
        self.dir = Path(capture_dir)
        mpath = self.dir / "manifest.json"
        if not mpath.exists():
            raise FileNotFoundError(f"no capture manifest at {mpath}")
        self.manifest = json.loads(mpath.read_text(encoding="utf-8"))
        if self.manifest.get("format_version") != FORMAT_VERSION:
            raise CaptureIntegrityError(f"unsupported capture format {self.manifest.get('format_version')!r}")
        self._index: dict[str, list[dict]] = {}
        self._served: dict[str, int] = {}
        self.misses: list[str] = []
        for name, digest in sorted(self.manifest.get("files", {}).items()):
            path = self.dir / name
            if not path.exists():
                raise CaptureIntegrityError(f"capture file missing: {name}")
            if verify and _sha256(path) != digest:
                raise CaptureIntegrityError(f"capture file modified after recording: {name}")
            e = json.loads(path.read_text(encoding="utf-8"))
            r = e["request"]
            self._index.setdefault(request_key(r["method"], r["url"], r.get("accept", "")), []).append(e)

    def get(self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None, **kw: Any):
        full = canonical_url(url, params)
        accept = (headers or {}).get("Accept", "")
        key = request_key("GET", full, accept)
        entries = self._index.get(key)
        if not entries:
            self.misses.append(key)
            raise ReplayMiss(f"not in capture: GET {full} (Accept: {accept})")
        i = self._served.get(key, 0)
        self._served[key] = i + 1
        e = entries[min(i, len(entries) - 1)]
        if e.get("exception"):
            raise _EXC_TYPES.get(e["exception"]["type"], requests.RequestException)(e["exception"]["message"])
        resp = e["response"]
        body = (resp["body_text"].encode("utf-8") if "body_text" in resp
                else base64.b64decode(resp.get("body_b64", "")))
        return CapturedResponse(resp["status"], resp.get("headers", {}), body)
