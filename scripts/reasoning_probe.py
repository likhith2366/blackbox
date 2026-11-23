"""STEP 4 reasoning probe: can qwen3:4b reason to a cited causal chain when the evidence is already in front of it?

No tools and no coordinator: one model call per run on a fixed evidence packet. Development incidents only
(INC-0000, INC-9001, INC-9002); never the frozen evaluation cases. BlackBox behaviour is not changed.

Packet assembly rule (the same for every incident; it never reads the answer key):
  1. every deployment in the incident window (list_deployments), and for each: its build (get_build) and its
     deployed commit with the full diff (get_commit);
  2. the configuration of each incident service at its running deployment (get_config);
  3. every metric series of each incident service over the window (get_metrics);
  4. the 3 most frequent ERROR log groups of each incident service (search_logs level=ERROR);
  5. one error trace of the alerting service (search_traces status=error, limit 1).
Records are shown with render_record at full length (no 600-character cut). Ids and timestamps are unchanged.
To fit num_ctx 8192, every metric series is thinned to at most 9 points (evenly spaced, always keeping the
first, the last and the maximum point; the min/max/last summary line is unchanged), and the sha256
artifact digests and artifact byte counts are removed from deployment and build text (ids, versions, commits
and timestamps stay). latency_p50 series are dropped (latency_p95 is kept) for the same reason.
Deployments of other services and the unrelated deployed commit(s) in step 1 are the misleading records.

Condition "full": the packet above.
Condition "no_mechanism": the same packet with the mechanism-bearing material removed by a generic rule:
  commit records lose their diff (message and file list stay), configs are dropped, and every metric whose name
  is not a symptom series (error / latency / duration / timeout / request rate; causal_gate.SYMPTOM_METRIC) is
  dropped. Logs and traces stay, so the packets are otherwise comparable.

    python scripts/reasoning_probe.py build            # write packets + prompts (no model)
    python scripts/with_gpu_lock.py probe -- python scripts/reasoning_probe.py run [--repeats 2]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackbox.evidence.backend import load_backend                      # noqa: E402
from blackbox.investigator.causal_gate import SYMPTOM_METRIC            # noqa: E402
from blackbox.investigator.common import count_tokens, render_incident, render_record  # noqa: E402
from blackbox.model.adapter import OllamaAdapter                         # noqa: E402
from blackbox.schemas import RunConfig                                   # noqa: E402

import yaml  # noqa: E402

INCIDENTS = ["INC-0000", "INC-9001", "INC-9002"]
CONDITIONS = ["full", "no_mechanism"]
EXP = ROOT / "experiments" / "reasoning_probe"
OUT = ROOT / "runs" / "reasoning_probe"
PROMPT = (EXP / "prompt_v1.txt").read_text(encoding="utf-8")
LINK = {"type": "object", "properties": {"statement": {"type": "string"},
                                         "evidence_ids": {"type": "array", "items": {"type": "string"}}},
        "required": ["statement", "evidence_ids"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "hypotheses": {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "component": {"type": "string"}, "mechanism": {"type": "string"},
                           "chain": {"type": "object", "properties": {"cause": LINK, "runtime_effect": LINK,
                                                                      "symptom": LINK},
                                     "required": ["cause", "runtime_effect", "symptom"]},
                           "contradicting": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "component", "mechanism", "chain", "contradicting"]}},
        "decision": {"type": "string", "enum": ["diagnosis", "insufficient_evidence"]},
        "top_hypothesis": {"type": ["string", "null"]},
        "rationale": {"type": "string"},
    },
    "required": ["hypotheses", "decision", "top_hypothesis", "rationale"],
}


def packet(inc: str) -> tuple[str, list]:
    b = load_backend(inc)
    i = b.incident()
    recs, seen = [], set()

    def add(res, role):
        for r in res.records:
            if r.evidence_id not in seen:
                seen.add(r.evidence_id)
                recs.append((role, r))

    deps = b.list_deployments()
    add(deps, "deployment")
    for d in deps.records:
        add(b.get_build(d.attributes["build_id"]), "build")
        add(b.get_commit(d.attributes["commit"]), "commit")
    for s in i.services:
        add(b.get_config(s), "config")
    for s in i.services:
        add(b.get_metrics(service=s), "metric")
    for s in i.services:
        res = b.search_logs(service=s, level="ERROR", limit=3)
        add(res, "log")
    add(b.search_traces(service=i.alert.service, status="error", limit=1), "trace")
    return render_incident(i, b.source_status()), recs


MAX_POINTS = 9


def thin(r):
    head, _, pts = r.content.partition("\n")
    pts = [p for p in pts.split("; ") if p.strip()]
    if len(pts) <= MAX_POINTS:
        return r
    vals = [float(p.split(" ")[-1]) for p in pts]
    step = (len(pts) - 1) / (MAX_POINTS - 2)
    idx = {round(k * step) for k in range(MAX_POINTS - 1)} | {len(pts) - 1, vals.index(max(vals))}
    return r.model_copy(update={"content": head + "\n" + "; ".join(pts[i] for i in sorted(idx))})


DIGEST = re.compile(r" ?artifact_digest=sha256:[0-9a-f]+| bytes=\d+")


def keep(role, r, cond):
    if role == "metric" and str(r.attributes.get("metric", "")).startswith("latency_p50"):
        return None                       # p95 is kept; p50 is dropped only to fit num_ctx
    if role == "metric":
        r = thin(r)
    if role in ("deployment", "build"):
        r = r.model_copy(update={"content": DIGEST.sub("", r.content)})
    if cond == "full":
        return r
    if role == "config":
        return None
    if role == "metric" and not SYMPTOM_METRIC.search(str(r.attributes.get("metric", ""))):
        return None
    if role == "commit":
        head = r.content.split("\n\ndiff --git", 1)[0]
        return r.model_copy(update={"content": head})
    return r


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {}
    for inc in INCIDENTS:
        inc_text, recs = packet(inc)
        for cond in CONDITIONS:
            kept = [(role, keep(role, r, cond)) for role, r in recs]
            kept = [(role, r) for role, r in kept if r is not None]
            ev = "\n\n".join(render_record(r, full=True, max_chars=100000) for _, r in kept)
            user = f"INCIDENT\n{inc_text}\n\nEVIDENCE ({len(kept)} records)\n{ev}\n\nReply with the JSON object."
            d = OUT / f"{inc}_{cond}"
            d.mkdir(exist_ok=True)
            (d / "prompt_system.txt").write_text(PROMPT, encoding="utf-8")
            (d / "prompt_user.txt").write_text(user, encoding="utf-8")
            (d / "packet.json").write_text(json.dumps([{"role": role, **r.model_dump(mode="json")} for role, r in kept],
                                                      indent=1), encoding="utf-8")
            summary[f"{inc}_{cond}"] = {"records": len(kept), "ids": [r.evidence_id for _, r in kept],
                                        "prompt_tokens_est": count_tokens(PROMPT + user)}
    (OUT / "_packets.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    for k, v in summary.items():
        print(k, v["records"], "records,", v["prompt_tokens_est"], "tokens (est.)")


def run(repeats: int):
    cfg = RunConfig(**yaml.safe_load((ROOT / "configs" / "dev_v2.yaml").read_text(encoding="utf-8")))
    ad = OllamaAdapter(cfg)
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    for rep in range(1, repeats + 1):
        for inc in INCIDENTS:
            for cond in CONDITIONS:
                d = OUT / f"{inc}_{cond}"
                f = d / f"r{rep}.json"
                if f.exists():
                    continue
                system = (d / "prompt_system.txt").read_text(encoding="utf-8")
                user = (d / "prompt_user.txt").read_text(encoding="utf-8")
                t0 = time.time()
                reply = ad.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                schema=SCHEMA, num_predict=cfg.num_predict)
                f.write_text(json.dumps({"incident": inc, "condition": cond, "repeat": rep, "code_revision": rev,
                                         "settings": {k: getattr(cfg, k) for k in ("model", "temperature", "top_p", "seed",
                                                                                   "num_ctx", "num_predict", "think")},
                                         "raw_output": reply.text, "parsed": reply.parsed,
                                         "parse_error": reply.parse_error, "prompt_tokens": reply.prompt_tokens,
                                         "completion_tokens": reply.completion_tokens,
                                         "seconds": round(time.time() - t0, 1)}, indent=1), encoding="utf-8")
                print(f"{inc} {cond} r{rep}: {reply.prompt_tokens} prompt tok, {reply.completion_tokens} out, "
                      f"{time.time() - t0:.0f}s, parsed={reply.parsed is not None}", flush=True)
                if "--one" in sys.argv:
                    return


if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        build()
    else:
        run(int(sys.argv[sys.argv.index("--repeats") + 1]) if "--repeats" in sys.argv else 2)
