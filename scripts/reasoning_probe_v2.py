"""STEP 5 (development only): audited packets and a two-model comparison. BlackBox itself is not changed.

Packets = the STEP 4 packets (scripts/reasoning_probe.py, same rule, same ids) plus SOURCE records from the deployed
build artifacts, added by one neutral rule:

  For every deployment in the incident window, take its build's artifact from the build store
  (demo/.runtime/artifacts/<artifact>), and use it only if its sha256 equals the artifact_digest in the snapshot's
  build record (so the code is exactly what was deployed, and it existed before the cutoff: the build finished
  before the deployment). For each .py file changed by the deployed commit:
    S1  a hunk inside a function or class: add the enclosing top-level definition (a class if it is at most
        120 lines, otherwise the enclosing method);
    S2  a hunk at module level: for every module-level name it assigns, add the top-level definition (same size
        rule) of every reference to that name in the artifact's .py files (tests excluded).
  Each source record is the verbatim code with line numbers. Its id is source:<build>:<path>#<qualname>; its time is
  the build's finish time. No comments, labels or explanations are added.

Conditions:
  full          STEP 4 full packet + source records.
  no_mechanism  identical to the STEP 4 no_mechanism packet (diffs, configs, non-symptom metrics removed; no
                source records).

Models (identical prompt, schema, packets and settings): qwen3:4b and qwen3:8b, temperature 0, seed 7,
num_ctx 12288, num_predict 4000 (raised from 900 so no output is truncated), think off. Two runs per packet.

    python scripts/reasoning_probe_v2.py build
    python scripts/reasoning_probe_v2.py run --model qwen3:8b --one
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import reasoning_probe as v1                                             # noqa: E402
from blackbox.investigator.common import render_record                   # noqa: E402
from blackbox.schemas import EvidenceRecord                              # noqa: E402

OUT = ROOT / "runs" / "reasoning_probe_v2"
ARTIFACTS = ROOT / "demo" / ".runtime" / "artifacts"
SNAP = ROOT / "scenarios" / "snapshots"
MODELS = ["qwen3:4b", "qwen3:8b"]
SETTINGS = {"temperature": 0.0, "top_p": 1.0, "seed": 7, "num_ctx": 12288, "num_predict": 4000}
MAX_DEF_LINES = 120
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _jsonl(p: Path):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def added_lines(diff: str) -> dict[str, set[int]]:
    """New-file line numbers added or changed, per file."""
    out, path, n = {}, None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            continue
        m = HUNK.match(line)
        if m:
            n = int(m.group(1))
            continue
        if path is None or line.startswith("---"):
            continue
        if line.startswith("+"):
            out.setdefault(path, set()).add(n)
            n += 1
        elif line.startswith("-"):
            out.setdefault(path, set()).add(n)    # deletion point
        else:
            n += 1
    return out


def top_def(tree: ast.Module, line: int):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.lineno <= line <= node.end_lineno:
            if isinstance(node, ast.ClassDef) and node.end_lineno - node.lineno + 1 > MAX_DEF_LINES:
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.lineno <= line <= sub.end_lineno:
                        return f"{node.name}.{sub.name}", sub
                return None
            return node.name, node
    return None


def source_records(inc: str) -> tuple[list[EvidenceRecord], list[str]]:
    builds = {b["build_id"]: b for b in _jsonl(SNAP / inc / "builds.jsonl")}
    commits = {c["sha"]: c for c in _jsonl(SNAP / inc / "commits.jsonl")}
    b = v1.load_backend(inc)
    log, recs, seen = [], [], set()
    for d in b.list_deployments().records:
        bd = builds.get(d.attributes["build_id"])
        art = ARTIFACTS / bd["artifact"] if bd else None
        if not art or not art.exists():
            log.append(f"{d.evidence_id}: artifact not found")
            continue
        data = art.read_bytes()
        if "sha256:" + hashlib.sha256(data).hexdigest() != bd["artifact_digest"]:
            log.append(f"{d.evidence_id}: artifact digest mismatch, skipped")
            continue
        files = {}
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            for m in tf.getmembers():
                if m.isfile() and m.name.endswith(".py") and "/tests/" not in "/" + m.name:
                    files[m.name] = tf.extractfile(m).read().decode("utf-8")
        c = commits[bd["commit"]]
        wanted = []                                   # (path, line)
        for path, lines in added_lines(c["diff"]).items():
            if path not in files:
                continue
            tree = ast.parse(files[path])
            for ln in sorted(lines):
                hit = top_def(tree, ln)
                if hit:
                    wanted.append((path, ln))
                    continue
                names = set()
                for node in tree.body:                # S2: module-level assignment changed at this line
                    if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.lineno <= ln <= node.end_lineno:
                        for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                            if isinstance(t, ast.Name):
                                names.add(t.id)
                for p2, src in files.items():
                    if p2 == path:
                        continue
                    t2 = ast.parse(src)
                    for node in ast.walk(t2):
                        if (isinstance(node, ast.Attribute) and node.attr in names) or \
                           (isinstance(node, ast.Name) and node.id in names):
                            wanted.append((p2, node.lineno))
        for path, ln in wanted:
            tree = ast.parse(files[path])
            hit = top_def(tree, ln)
            if not hit:
                continue
            qual, node = hit
            rid = f"source:{bd['build_id']}:{path}#{qual}"
            if rid in seen:
                continue
            seen.add(rid)
            src = files[path].splitlines()[node.lineno - 1: node.end_lineno]
            text = (f"{path} lines {node.lineno}-{node.end_lineno} in build {bd['build_id']} artifact {bd['artifact']} "
                    f"(commit {bd['commit'][:7]})\n" + "\n".join(f"{node.lineno + i:4d} {s}" for i, s in enumerate(src)))
            recs.append(EvidenceRecord(evidence_id=rid, source="builds", service=bd["service"],
                                       event_time=bd["finished_at"].replace("+00:00", "Z"),
                                       version=bd["version"], title=f"{path} {qual} @ {bd['build_id']}", content=text))
            log.append(f"{rid}: {node.end_lineno - node.lineno + 1} lines")
    return recs, log


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {}
    for inc in v1.INCIDENTS:
        inc_text, recs = v1.packet(inc)
        src, log = source_records(inc)
        for cond in v1.CONDITIONS:
            kept = [(role, v1.keep(role, r, cond)) for role, r in recs]
            kept = [(role, r) for role, r in kept if r is not None]
            if cond == "full":
                # source records go right after the commit records
                last = max(i for i, (role, _) in enumerate(kept) if role in ("commit", "build"))
                kept = kept[:last + 1] + [("source", r) for r in src] + kept[last + 1:]
            ev = "\n\n".join(render_record(r, full=True, max_chars=100000) for _, r in kept)
            user = f"INCIDENT\n{inc_text}\n\nEVIDENCE ({len(kept)} records)\n{ev}\n\nReply with the JSON object."
            d = OUT / f"{inc}_{cond}"
            d.mkdir(exist_ok=True)
            (d / "prompt_system.txt").write_text(v1.PROMPT, encoding="utf-8")
            (d / "prompt_user.txt").write_text(user, encoding="utf-8")
            (d / "packet.json").write_text(json.dumps([{"role": role, **r.model_dump(mode="json")} for role, r in kept],
                                                      indent=1), encoding="utf-8")
            summary[f"{inc}_{cond}"] = {"records": len(kept), "ids": [r.evidence_id for _, r in kept],
                                        "source_log": log if cond == "full" else []}
    (OUT / "_packets.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    for k, v in summary.items():
        print(k, v["records"], "records", v["source_log"])


def _gpu_mem_sampler(stop, peak):
    while not stop.is_set():
        try:
            o = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
            peak["vram_mib"] = max(peak.get("vram_mib", 0), int(o))
            import psutil
            rss = sum(p.memory_info().rss for p in psutil.process_iter() if "llama" in (p.name() or "").lower()
                      or "ollama" in (p.name() or "").lower())
            peak["server_rss_mib"] = max(peak.get("server_rss_mib", 0), rss // 2**20)
        except Exception:
            pass
        stop.wait(1.0)


def run(model: str, repeats: int = 2, one: bool = False):
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    tag = model.replace(":", "_")
    for rep in range(1, repeats + 1):
        for inc in v1.INCIDENTS:
            for cond in v1.CONDITIONS:
                d = OUT / f"{inc}_{cond}"
                f = d / f"{tag}_r{rep}.json"
                if f.exists():
                    continue
                msgs = [{"role": "system", "content": (d / "prompt_system.txt").read_text(encoding="utf-8")},
                        {"role": "user", "content": (d / "prompt_user.txt").read_text(encoding="utf-8")}]
                body = {"model": model, "messages": msgs, "stream": False, "think": False, "format": v1.SCHEMA,
                        "options": SETTINGS, "keep_alive": "2m"}
                stop, peak = threading.Event(), {}
                th = threading.Thread(target=_gpu_mem_sampler, args=(stop, peak), daemon=True)
                th.start()
                t0 = time.time()
                req = urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps(body).encode(),
                                             {"Content-Type": "application/json"})
                r = json.load(urllib.request.urlopen(req, timeout=1800))
                wall = time.time() - t0
                stop.set()
                th.join()
                text = r["message"]["content"]
                try:
                    parsed, err = json.loads(text), None
                except ValueError as e:
                    parsed, err = None, str(e)
                f.write_text(json.dumps({
                    "incident": inc, "condition": cond, "repeat": rep, "model": model, "code_revision": rev,
                    "settings": {**SETTINGS, "think": False}, "raw_output": text, "parsed": parsed, "parse_error": err,
                    "done_reason": r.get("done_reason"), "prompt_tokens": r.get("prompt_eval_count"),
                    "completion_tokens": r.get("eval_count"), "wall_seconds": round(wall, 1),
                    "load_seconds": round((r.get("load_duration") or 0) / 1e9, 1),
                    "prompt_eval_seconds": round((r.get("prompt_eval_duration") or 0) / 1e9, 1),
                    "eval_seconds": round((r.get("eval_duration") or 0) / 1e9, 1),
                    "peak_vram_mib": peak.get("vram_mib"), "peak_server_rss_mib": peak.get("server_rss_mib")},
                    indent=1), encoding="utf-8")
                print(f"{model} {inc} {cond} r{rep}: {r.get('prompt_eval_count')} in, {r.get('eval_count')} out, "
                      f"{r.get('done_reason')}, {wall:.0f}s, vram {peak.get('vram_mib')} MiB", flush=True)
                if one:
                    return


if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        build()
    else:
        run(sys.argv[sys.argv.index("--model") + 1], one="--one" in sys.argv)
