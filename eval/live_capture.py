"""Versioned live-response captures: format spec, verifier and replay index (CONTRACT 10c).

The WRITER belongs in blackbox/connectors/ (blackbox/ may not import eval/). This module
defines the format the writer must produce, verifies a capture, and loads it for replay.

    python -m eval.live_capture verify scenarios/live_captures/<capture_id>
    python -m eval.live_capture verify --all

FORMAT (format_version "1")
scenarios/live_captures/<capture_id>/          capture_id = LC-YYYYMMDDTHHMMSSZ-<6 hex>
  capture.json
    capture_id        same as the directory name
    format_version    "1"
    created_at        UTC "...Z", first exchange started
    finalized_at      UTC "...Z", files closed; the capture is immutable afterwards
    connector         "github_api" | "local_logs" | ...
    target            e.g. {"repo": "owner/name"} or {"log_dir": "<path>"}
    auth_mode         descriptive only, e.g. "token (env GITHUB_TOKEN)"; NEVER the token
    purpose           "pilot" | "development"   (never "eval": live data never feeds the matrix)
    incident_id       optional, the pilot incident the capture was taken for
    code_revision     git HEAD (+ "-dirty") of the code that made the capture
    n_exchanges       number of lines in exchanges.jsonl
    files             {"exchanges.jsonl": "<sha256 of the file bytes>"}
  exchanges.jsonl     one JSON object per HTTP exchange (or file read), in request order
    seq               1..n
    retrieved_at      UTC "...Z" when the response was received
    method            "GET" (connectors are read-only; anything else is invalid)
    url               absolute URL without credentials (no token in query string)
    params            query parameters as sent (dict)
    accept            the Accept header value only (other request headers are NOT stored)
    status_code       HTTP status (null when the request failed before a response)
    response_headers  allowlist only: content-type, etag, last-modified, link, date,
                      x-ratelimit-remaining, x-ratelimit-reset, x-github-request-id
    body_encoding     "utf-8" or "base64"
    body              response body VERBATIM (as received, before parsing)
    body_bytes        length of the raw body
    body_sha256       sha256 of the raw body bytes
    truncated         true when max_bytes stopped the read (body is the prefix that was read)
    elapsed_ms        float
    error             scrubbed error text when no response was received, else null
Token-free: no GitHub token patterns (ghp_, gho_, ghu_, ghs_, ghr_, github_pat_) and not the
runtime token value anywhere in the capture; no Authorization/Bearer/access_token text in
capture.json or in request-side fields (url, params, accept, response_headers, error).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from eval.common import REPO_ROOT

CAPTURE_ROOT = REPO_ROOT / "scenarios" / "live_captures"
CAPTURE_ID = re.compile(r"^LC-\d{8}T\d{6}Z-[0-9a-f]{6}$")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
# token-shaped strings: checked in every byte of the capture (bodies included)
TOKEN_PATTERNS = re.compile(r"(gh[pousr]_[A-Za-z0-9]{6,}|github_pat_[A-Za-z0-9_]{10,})")
# auth-header-shaped text: checked only in request-side fields and capture.json, because a
# verbatim body (e.g. a source file fetched with get_file) may legitimately contain it
AUTH_PATTERNS = re.compile(r"(Bearer\s+\S+|authorization|x-github-token|access_token=)", re.IGNORECASE)
HEADER_ALLOW = {"content-type", "etag", "last-modified", "link", "date", "x-ratelimit-remaining",
                "x-ratelimit-reset", "x-github-request-id"}
CAPTURE_FIELDS = ("capture_id", "format_version", "created_at", "finalized_at", "connector",
                  "target", "auth_mode", "purpose", "code_revision", "n_exchanges", "files")
EXCHANGE_FIELDS = ("seq", "retrieved_at", "method", "url", "params", "accept", "status_code",
                   "response_headers", "body_encoding", "body", "body_bytes", "body_sha256",
                   "truncated", "elapsed_ms", "error")


def _raw_body(ex: dict) -> bytes:
    if ex.get("body_encoding") == "base64":
        return base64.b64decode(ex.get("body") or "")
    return (ex.get("body") or "").encode("utf-8")


def verify_capture(cdir: Path, token: Optional[str] = None) -> list[str]:
    """Problems found (empty list = valid). `token` defaults to env GITHUB_TOKEN."""
    cdir = Path(cdir)
    token = token if token is not None else os.environ.get("GITHUB_TOKEN")
    P: list[str] = []
    cj, ej = cdir / "capture.json", cdir / "exchanges.jsonl"
    if not CAPTURE_ID.match(cdir.name):
        P.append(f"directory name {cdir.name!r} is not a capture id LC-YYYYMMDDTHHMMSSZ-xxxxxx")
    for f in (cj, ej):
        if not f.exists():
            P.append(f"missing {f.name}")
    if P and not (cj.exists() and ej.exists()):
        return P
    # token-freedom over every file in the capture, raw bytes
    for f in sorted(cdir.rglob("*")):
        if f.is_file():
            text = f.read_bytes().decode("utf-8", "replace")
            for m in TOKEN_PATTERNS.finditer(text):
                P.append(f"{f.name}: credential-like text {m.group(0)[:12]!r}...")
            if token and token in text:
                P.append(f"{f.name}: contains the runtime GITHUB_TOKEN value")
    try:
        meta = json.loads(cj.read_text(encoding="utf-8"))
    except Exception as e:
        return P + [f"capture.json invalid JSON: {e}"]
    for k in CAPTURE_FIELDS:
        if k not in meta:
            P.append(f"capture.json missing {k}")
    for m in AUTH_PATTERNS.finditer(json.dumps(meta)):
        P.append(f"capture.json: auth-header-like text {m.group(0)[:16]!r}")
    if meta.get("capture_id") != cdir.name:
        P.append("capture.json capture_id does not match the directory name")
    if meta.get("format_version") != "1":
        P.append(f"unsupported format_version {meta.get('format_version')!r}")
    if meta.get("purpose") not in ("pilot", "development"):
        P.append(f"purpose must be pilot|development, got {meta.get('purpose')!r}")
    for k in ("created_at", "finalized_at"):
        if k in meta and not TS.match(str(meta[k] or "")):
            P.append(f"capture.json {k} is not a UTC ...Z timestamp")
    raw = ej.read_bytes()
    want = (meta.get("files") or {}).get("exchanges.jsonl")
    if want != hashlib.sha256(raw).hexdigest():
        P.append("exchanges.jsonl sha256 does not match capture.json files (modified after finalize?)")
    lines = [l for l in raw.decode("utf-8").splitlines() if l.strip()]
    if meta.get("n_exchanges") != len(lines):
        P.append(f"n_exchanges {meta.get('n_exchanges')} != {len(lines)} lines")
    for i, line in enumerate(lines, 1):
        try:
            ex = json.loads(line)
        except Exception as e:
            P.append(f"exchanges.jsonl:{i} invalid JSON: {e}")
            continue
        miss = [k for k in EXCHANGE_FIELDS if k not in ex]
        if miss:
            P.append(f"exchange {i}: missing {miss}")
            continue
        req_side = json.dumps([ex["url"], ex["params"], ex["accept"], ex["response_headers"], ex["error"]])
        for m in AUTH_PATTERNS.finditer(req_side):
            P.append(f"exchange {i}: auth-header-like text in request-side fields {m.group(0)[:16]!r}")
        if ex["seq"] != i:
            P.append(f"exchange {i}: seq {ex['seq']} out of order")
        if not TS.match(str(ex["retrieved_at"] or "")):
            P.append(f"exchange {i}: retrieved_at is not a UTC ...Z timestamp")
        if ex["method"] not in ("GET", "HEAD", "READ"):
            P.append(f"exchange {i}: non-read method {ex['method']!r}")
        extra = {h.lower() for h in (ex["response_headers"] or {})} - HEADER_ALLOW
        if extra:
            P.append(f"exchange {i}: response headers outside the allowlist: {sorted(extra)}")
        if ex["status_code"] is not None:
            try:
                b = _raw_body(ex)
            except Exception as e:
                P.append(f"exchange {i}: body not decodable as {ex['body_encoding']}: {e}")
                continue
            if hashlib.sha256(b).hexdigest() != ex["body_sha256"]:
                P.append(f"exchange {i}: body_sha256 mismatch (body not verbatim)")
            if len(b) != ex["body_bytes"]:
                P.append(f"exchange {i}: body_bytes {ex['body_bytes']} != {len(b)}")
        elif not ex.get("error"):
            P.append(f"exchange {i}: no status_code and no error")
    return P


def _key(method: str, url: str, params: Optional[dict], accept: str) -> str:
    return json.dumps([method.upper(), url.split("?")[0], sorted((params or {}).items()),
                       accept or ""], default=str)


class Replay:
    """Responses of a verified capture, keyed by (method, url, params, accept). Repeated
    identical requests are served in recorded order, then the last one again."""

    def __init__(self, cdir: Path, verify: bool = True):
        cdir = Path(cdir)
        if verify:
            probs = verify_capture(cdir)
            if probs:
                raise ValueError(f"capture {cdir.name} invalid: {probs[:5]}")
        self.meta = json.loads((cdir / "capture.json").read_text(encoding="utf-8"))
        self._by: dict[str, list[dict]] = {}
        self._pos: dict[str, int] = {}
        for line in (cdir / "exchanges.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                ex = json.loads(line)
                self._by.setdefault(_key(ex["method"], ex["url"], ex["params"], ex["accept"]), []).append(ex)

    def lookup(self, method: str, url: str, params: Optional[dict] = None,
               accept: str = "") -> Optional[dict]:
        k = _key(method, url, params, accept)
        lst = self._by.get(k)
        if not lst:
            return None
        i = self._pos.get(k, 0)
        self._pos[k] = min(i + 1, len(lst) - 1)
        return lst[i]

    @staticmethod
    def body_bytes(ex: dict) -> bytes:
        return _raw_body(ex)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.live_capture")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("capture_dir", nargs="?")
    v.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    dirs = sorted(p for p in CAPTURE_ROOT.iterdir() if p.is_dir()) if a.all else [Path(a.capture_dir)]
    bad = 0
    for d in dirs:
        probs = verify_capture(d)
        print(f"{d.name}: {'OK' if not probs else 'INVALID'}")
        for p in probs:
            print(f"  - {p}")
        bad += bool(probs)
    if not dirs:
        print("no captures")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
