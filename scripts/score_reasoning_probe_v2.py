"""Automatic (provisional) scoring of runs/reasoning_probe_v2, development only. Read-only.

Quality columns (same for both models): decision; top hypothesis; lexical component / mechanism match of the top
hypothesis and of any hypothesis (eval.matching, as in eval_v1); citation validity (ids not in the packet);
contradicting ids listed (top hypothesis / all); uncited links.
Cost columns (reported separately): prompt and completion tokens, done_reason, wall, load, prompt-eval and
generation seconds, generation tokens/s, peak GPU memory and peak model-server RSS during the call.
Semantic verdicts come from the reviewer (_review.json) and human review (_human_review.md), not from here.

    python scripts/score_reasoning_probe_v2.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.matching import accepted_components, accepted_mechanisms, component_matches, mechanism_matches  # noqa: E402
from blackbox.schemas import AnswerKey  # noqa: E402

RUNS = ROOT / "runs" / "reasoning_probe_v2"
KEYS = ROOT / "evaluator_private" / "answer_keys"


def score(f: Path) -> dict:
    o = json.loads(f.read_text(encoding="utf-8"))
    ids = {r["evidence_id"] for r in json.loads((f.parent / "packet.json").read_text(encoding="utf-8"))}
    key = AnswerKey.model_validate_json((KEYS / f"{o['incident']}.json").read_text(encoding="utf-8"))
    ac, am = accepted_components(key), accepted_mechanisms(key)
    p = o.get("parsed") or {}
    hs = p.get("hypotheses") or []
    top = next((h for h in hs if h.get("id") == p.get("top_hypothesis")), None)
    if top is None and p.get("decision") == "diagnosis" and hs:
        top = hs[0]
    invalid, uncited = [], 0
    for h in hs:
        for v in (h.get("chain") or {}).values():
            e = v.get("evidence_ids") or []
            uncited += not e
            invalid += [i for i in e + (h.get("contradicting") or []) if i not in ids]
    gen_s = o.get("eval_seconds") or 0
    return {"run": f"{f.parent.name}/{f.stem}", "model": o["model"], "incident": o["incident"],
            "condition": o["condition"], "repeat": o["repeat"], "parsed": bool(p), "done_reason": o.get("done_reason"),
            "decision": p.get("decision"),
            "top": f"{top.get('component')} :: {top.get('mechanism')}" if top else "",
            "top_component_ok": bool(top) and component_matches(top.get("component", ""), ac),
            "top_mechanism_ok": bool(top) and mechanism_matches(top.get("mechanism", ""), am),
            "any_hypothesis_ok": any(component_matches(h.get("component", ""), ac)
                                     and mechanism_matches(h.get("mechanism", ""), am) for h in hs),
            "invalid_ids": invalid, "uncited_links": uncited,
            "top_contradicting": len((top or {}).get("contradicting") or []),
            "all_contradicting": sum(len(h.get("contradicting") or []) for h in hs),
            "prompt_tokens": o.get("prompt_tokens"), "completion_tokens": o.get("completion_tokens"),
            "wall_s": o.get("wall_seconds"), "load_s": o.get("load_seconds"),
            "prompt_eval_s": o.get("prompt_eval_seconds"), "gen_s": gen_s,
            "gen_tok_per_s": round((o.get("completion_tokens") or 0) / gen_s, 1) if gen_s else None,
            "peak_vram_mib": o.get("peak_vram_mib"), "peak_server_rss_mib": o.get("peak_server_rss_mib")}


def main():
    rows = [score(f) for f in sorted(RUNS.glob("INC-*/qwen3_*_r*.json"))]
    (RUNS / "_auto_scores.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    q = ["run", "decision", "top_component_ok", "top_mechanism_ok", "any_hypothesis_ok", "invalid_ids",
         "uncited_links", "top_contradicting", "all_contradicting", "top"]
    c = ["run", "done_reason", "prompt_tokens", "completion_tokens", "wall_s", "load_s", "prompt_eval_s", "gen_s",
         "gen_tok_per_s", "peak_vram_mib", "peak_server_rss_mib"]
    for cols in (q, c):
        print("| " + " | ".join(cols) + " |\n|" + "---|" * len(cols))
        for r in rows:
            print("| " + " | ".join(str(r[k])[:140] for k in cols) + " |")
        print()


if __name__ == "__main__":
    main()
