"""Per-run metrics for runs/dev_v2c_experiment (dev_v2 vs dev_v2c, causal-evidence gate). Read-only.

For each run (from its saved output.json):
- decisive retrieved / cited: answer-key decisive ids (or a listed equivalent) that were retrieved, and that
  were cited by the final report (claims or causes) or by any hypothesis.
- refusals: diagnose requests refused, grouped by reason class (change check, v1 citation count, and the
  causal gate's component / mechanism-not-stated / change / mechanism-evidence / effect / timing).
- after refusal: the tools the model requested in the round right after each gate refusal.
- outcome: eval/matching.match_rank on the final report (correct = top-ranked cause matches component AND
  mechanism; wrong = diagnosis not matching; abstained = insufficient_evidence / no_fault).

The answer key is read here for scoring only; the gate never sees it.

    python scripts/analyze_gate_experiment.py [--runs DIR] [--md out.md]
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.matching import match_rank                 # noqa: E402
from blackbox.schemas import AnswerKey, FinalReport  # noqa: E402

RUNS = ROOT / "runs" / "dev_v2c_experiment"
KEYS = ROOT / "evaluator_private" / "answer_keys"
CLASSES = [("change check", r"refused by change check"), ("component", r"component missing"),
           ("mechanism not stated", r"mechanism not stated"), ("change evidence", r"change evidence missing"),
           ("mechanism evidence", r"mechanism evidence missing"), ("effect", r"runtime effect missing"),
           ("timing", r"timing inconsistent"), ("v1 citations", r"cites \d+ retrieved|no supporting runtime")]


def reason_class(text: str) -> str:
    return next((name for name, pat in CLASSES if re.search(pat, text)), "other")


def analyze(d: Path) -> dict:
    f = d / "output.json"
    if not f.exists():
        return {"run": d.name, "outcome": "no output"}
    o = json.loads(f.read_text(encoding="utf-8"))
    key = AnswerKey.model_validate_json((KEYS / f"{o['incident_id']}.json").read_text(encoding="utf-8"))
    eq = key.injected_fault.get("decisive_equivalents", {}) if isinstance(key.injected_fault, dict) else {}
    groups = {i: {i, *eq.get(i, [])} for i in key.decisive_evidence_ids}
    seen = {r["evidence_id"] for r in o.get("evidence_seen", [])}
    steps = o["steps"]
    rep = o.get("report") or {}
    cited = {i for s in steps for h in s["hypotheses_after"] for i in h["supporting"]}
    for c in rep.get("claims", []) or []:
        cited |= set(c.get("evidence_ids", []) or [])
    for c in rep.get("ranked_causes", []) or []:
        cited |= set(c.get("supporting_evidence_ids", []) or c.get("evidence_ids", []) or [])
    retrieved = [i for i, g in groups.items() if g & seen]
    cited_dec = [i for i, g in groups.items() if g & cited]
    refusals, after = Counter(), []
    for n, s in enumerate(steps):
        r = s.get("stop_reason") or ""
        if "refused" in r and ("diagnosis" in r or "change check" in r):
            cls = reason_class(r)
            refusals[cls] += 1
            if cls not in ("change check",) and n + 1 < len(steps):
                nxt = steps[n + 1]
                after.append(f"R{nxt['round']}:" + ("+".join(t["tool"] for t in nxt["tool_requests"]) or
                                                    f"({nxt.get('model_decision')})"))
    outcome, top = "failed", ""
    if rep:
        fr = FinalReport.model_validate(rep)
        if fr.status == "diagnosis":
            outcome = "correct" if match_rank(fr, key) == 1 else "wrong"
            if fr.ranked_causes:
                top = f"{fr.ranked_causes[0].component} :: {fr.ranked_causes[0].mechanism}"
        else:
            outcome = f"abstained ({fr.status})"
    tools = [t["tool"] for s in steps for t in s["tool_requests"]]
    rank = match_rank(FinalReport.model_validate(rep), key) if rep else None
    return {"run": d.name, "outcome": outcome, "top3": bool(rank and rank <= 3),
            "decisive_retrieved": f"{len(retrieved)}/{len(groups)} {sorted(retrieved)}",
            "decisive_cited": f"{len(cited_dec)}/{len(groups)} {sorted(cited_dec)}",
            "refusals": dict(refusals), "after_gate_refusal": after,
            "get_commit_calls": tools.count("get_commit"), "get_config_calls": tools.count("get_config"),
            "tool_calls": o.get("tool_calls_used"), "rounds": len(steps), "wall_s": round(o.get("wall_seconds") or 0),
            "top_cause": top, "stop": steps[-1].get("stop_reason") if steps else ""}


def main():
    runs = Path(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else RUNS
    rows = [analyze(d) for d in sorted(p for p in runs.iterdir() if p.is_dir())]
    cols = ["run", "outcome", "decisive_retrieved", "decisive_cited", "refusals", "after_gate_refusal",
            "get_commit_calls", "tool_calls", "wall_s", "top_cause"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    text = "\n".join(lines)
    print(text)
    (runs / "_analysis.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    if "--md" in sys.argv:
        Path(sys.argv[sys.argv.index("--md") + 1]).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
