"""Live, read-only connector over the JSONL logs the demo services are writing right now.

    python -m blackbox.connectors.local_logs smoke [--dir PATH]

The services (libs/telemetry in the demo company repo) append one JSON object per line to
<telemetry_dir>/logs/<service>.jsonl. This module only reads those files; it never writes,
and it never generates log lines. Each returned record carries the original line verbatim in
`content` (clipped only if it exceeds the content limit, flagged with truncated=True).

Telemetry directory resolution (first hit wins):
  1. constructor argument
  2. env BLACKBOX_TELEMETRY_DIR
  3. "telemetry_dir" in demo/.runtime/state.json (the directory the running services write to)
  4. demo/.runtime/telemetry/live
Only state.json is read to locate the directory; no other demo or evaluator file is touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from blackbox.schemas import BackendResult, EvidenceRecord

REPO = Path(__file__).resolve().parents[2]
STATE = REPO / "demo" / ".runtime" / "state.json"
FALLBACK = REPO / "demo" / ".runtime" / "telemetry" / "live"
CONTENT_CHARS = 600
MAX_LIMIT = 100
MAX_BYTES_PER_FILE = 64 * 1024 * 1024
STOPWORDS = {"the", "a", "an", "of", "in", "and", "or", "to", "for", "on", "at", "is", "with", "by", "from"}


def _ts(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _now() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _tokens(q: Optional[str]) -> list[str]:
    if not q:
        return []
    out = []
    for t in re.split(r"[\s,;:()\"'=]+", q.lower()):
        if t and t not in STOPWORDS and t not in out:
            out.append(t)
    return out


def resolve_telemetry_dir(explicit: Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("BLACKBOX_TELEMETRY_DIR")
    if env:
        return Path(env)
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
        if st.get("telemetry_dir"):
            return Path(st["telemetry_dir"])
    except (OSError, ValueError):
        pass
    return FALLBACK


class LocalLogs:
    def __init__(self, telemetry_dir: Path | None = None):
        self.telemetry_dir = resolve_telemetry_dir(telemetry_dir)
        self.logs_dir = self.telemetry_dir / "logs"

    # ------------------------------------------------------------------ reading
    def _files(self, service: Optional[str]) -> tuple[Optional[list[Path]], Optional[BackendResult]]:
        if not self.logs_dir.is_dir():
            return None, BackendResult(status="unavailable", error=f"no log directory at {self.logs_dir}")
        if service:
            p = self.logs_dir / f"{service}.jsonl"
            if not p.is_file():
                return None, BackendResult(status="unavailable", error=f"no log file for service {service!r}")
            return [p], None
        files = sorted(self.logs_dir.glob("*.jsonl"))
        if not files:
            return None, BackendResult(status="unavailable", error=f"no log files in {self.logs_dir}")
        return files, None

    def _read(self, files: list[Path], start, end, level, toks, retrieved_at: str):
        s, e = _ts(start), _ts(end)
        lv = (level or "").strip().upper()
        lv = "WARNING" if lv == "WARN" else lv
        out = []
        for p in files:
            with open(p, "rb") as fh:
                data = fh.read(MAX_BYTES_PER_FILE)
            for n, raw in enumerate(data.split(b"\n")):
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # a line still being written
                t = _ts(rec.get("ts"))
                if t is None or (s is not None and t < s) or (e is not None and t > e):
                    continue
                if lv and str(rec.get("level", "")).upper() != lv:
                    continue
                score = sum(1 for tok in toks if tok in line.lower()) if toks else 0
                if toks and score == 0:
                    continue
                out.append((score, t, n, p, line, rec))
        return out

    def _record(self, p: Path, n: int, line: str, rec: dict, retrieved_at: str) -> EvidenceRecord:
        content = line if len(line) <= CONTENT_CHARS else line[:CONTENT_CHARS]
        rel = str(p.resolve())
        eid = "log:" + hashlib.sha1(f"{rel}|{n}".encode()).hexdigest()[:10]
        return EvidenceRecord(
            evidence_id=eid, source="logs", service=rec.get("service") or p.stem,
            event_time=rec.get("ts"), available_time=rec.get("ts"),
            title=f"{rec.get('level', '')} {rec.get('service', p.stem)}: {rec.get('msg', '')}"[:160],
            content=content, truncated=len(line) > CONTENT_CHARS, content_ref=f"{rel}#{n}", fixture=False,
            attributes={"live": True, "retrieved_at": retrieved_at, "path": rel, "line": n,
                        "level": rec.get("level"), "trace_id": rec.get("trace_id")})

    # ------------------------------------------------------------------ API
    def search(self, service=None, start=None, end=None, query=None, level=None, limit=20) -> BackendResult:
        files, err = self._files(service)
        if err:
            return err
        try:
            limit = max(1, min(int(limit), MAX_LIMIT))
        except (TypeError, ValueError):
            limit = 20
        retrieved_at = _now()
        try:
            hits = self._read(files, start, end, level, _tokens(query), retrieved_at)
        except OSError as exc:
            return BackendResult(status="error", error=f"log read failed: {type(exc).__name__}")
        except ValueError as exc:
            return BackendResult(status="error", error=f"bad argument: {exc}")
        if not hits:
            return BackendResult(status="empty", total_matches=0)
        hits.sort(key=lambda h: (-h[0], h[1], str(h[3]), h[2]))
        recs = [self._record(p, n, line, rec, retrieved_at) for _, _, n, p, line, rec in hits[:limit]]
        return BackendResult(status="ok", records=recs, total_matches=len(hits), truncated=len(hits) > limit)

    def export(self, start=None, end=None) -> list[EvidenceRecord]:
        files, err = self._files(None)
        if err:
            return []
        retrieved_at = _now()
        hits = self._read(files, start, end, None, [], retrieved_at)
        hits.sort(key=lambda h: (h[1], str(h[3]), h[2]))
        return [self._record(p, n, line, rec, retrieved_at) for _, _, n, p, line, rec in hits]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["smoke"])
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args(argv)
    ll = LocalLogs(args.dir)
    print(f"telemetry dir: {ll.telemetry_dir}")
    files, err = ll._files(None)
    if err:
        print(f"status={err.status} error={err.error}")
        return 0
    for p in files:
        print(f"  {p.name}: {sum(1 for _ in open(p, 'rb'))} lines")
    allr = ll.search(limit=5)
    errs = ll.search(level="ERROR", limit=3)
    print(f"search(limit=5): status={allr.status} total_matches={allr.total_matches} truncated={allr.truncated}")
    print(f"search(level=ERROR, limit=3): status={errs.status} total_matches={errs.total_matches}")
    for r in errs.records:
        print("   ", r.evidence_id, r.content[:160])
    print(f"export(): {len(ll.export())} records")
    print(f"search(service='no-such-svc'): status={ll.search(service='no-such-svc').status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
