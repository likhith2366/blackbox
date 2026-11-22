"""Per-run metrics for runs/dev_v2b_experiment (dev_v2 vs dev_v2b). Read-only.

Definitions (computed from each run's saved output.json):
- change round: first round whose tool results returned a deploy: or commit: record.
- hypothesis changed: a round after the change round whose ledger (component, mechanism, status,
  supporting, contradicting) differs from the ledger it started with.
- relevant change ids: the answer key's decisive deploy:/commit: ids (with listed equivalents);
  other deploy:/commit: ids cited by a hypothesis are counted as irrelevant (latest-deploy bias check).
- rationale/ledger agreement (heuristic): in a round after the change round, the rationale names a change
  (a retrieved deploy:/commit: id, its short sha, "deploy" or "commit") exactly when a hypothesis cites a
  deploy:/commit: id. Reported as agree / rationale-only / ledger-only.
- gate pass: the run stopped with stop_diagnosis.
- outcome: eval/matching.match_rank on the final report: correct = top-ranked cause matches component AND
  mechanism; wrong = diagnosis not matching; abstained = insufficient_evidence / no_fault; failed = no report.

    python scripts/analyze_rationale_experiment.py [--runs DIR] [--md out.md]
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.matching import match_rank                 # noqa: E402
from blackbox.schemas import AnswerKey, FinalReport  # noqa: E402

RUNS = ROOT / "runs" / "dev_v2b_experiment"
KEYS = ROOT / "evaluator_private" / "answer_keys"
CHANGE = re.compile(r"^(deploy|commit):")


def ledger(hs):
    return sorted((h["component"], h["mechanism"], h["status"], tuple(sorted(h["supporting"])), tuple(sorted(h["contradicting"])))
                  for h in hs)


def analyze(d: Path) -> dict:
    run = json.loads((d / "runner.json").read_text(encoding="utf-8")) if (d / "runner.json").exists() else {}
    f = d / "output.json"
    if not f.exists():
        return {"run": d.name, "status": "no output", "rc": run.get("rc")}
    o = json.loads(f.read_text(encoding="utf-8"))
    key = AnswerKey.model_validate_json((KEYS / f"{o['incident_id']}.json").read_text(encoding="utf-8"))
    eq = key.injected_fault.get("decisive_equivalents", {}) if isinstance(key.injected_fault, dict) else {}
    relevant = set()
    for i in key.decisive_evidence_ids:
        if CHANGE.match(i):
            relevant |= {i, *eq.get(i, [])}
    steps = o["steps"]
    change_round = next((s["round"] for s in steps for t in s["tool_results"]
                         if t["status"] == "ok" and any(CHANGE.match(r["evidence_id"]) for r in t.get("records", []))), None)
    retrieved_change = {r["evidence_id"] for r in o.get("evidence_seen", []) if CHANGE.match(r["evidence_id"])}
    shas = {i.split(":", 1)[1][:7] for i in retrieved_change if i.startswith("commit:")} | \
           {i.split(":", 1)[1] for i in retrieved_change if i.startswith("deploy:")}
    after = [s for s in steps if change_round is not None and s["round"] > change_round]
    changed = sum(1 for s in after if ledger(s["hypotheses_after"]) != ledger(s["hypotheses_before"]))
    cited_rel, cited_irr = set(), set()
    agree = rat_only = led_only = 0
    for s in steps:
        ids = {i for h in s["hypotheses_after"] for i in h["supporting"]}
        cited_rel |= ids & relevant
        cited_irr |= {i for i in ids if CHANGE.match(i)} - relevant
    for s in after:
        try:
            rat = (json.loads(s["raw_output"]).get("rationale") or "").lower()
        except (ValueError, AttributeError):
            rat = ""
        r_change = any(x.lower() in rat for x in shas) or "deploy" in rat or "commit" in rat
        l_change = any(CHANGE.match(i) for h in s["hypotheses_after"] for i in h["supporting"])
        if r_change == l_change:
            agree += 1
        elif r_change:
            rat_only += 1
        else:
            led_only += 1
    rep = o.get("report")
    outcome, top = "failed", ""
    if rep:
        fr = FinalReport.model_validate(rep)
        if fr.status == "diagnosis":
            outcome = "correct" if match_rank(fr, key) == 1 else "wrong"
            if fr.ranked_causes:
                top = f"{fr.ranked_causes[0].component} :: {fr.ranked_causes[0].mechanism}"
        else:
            outcome = f"abstained ({fr.status})"
    final_hyps = [f"{h['component']} :: {h['mechanism']} {h['supporting']}" for h in (steps[-1]["hypotheses_after"] if steps else [])]
    return {"run": d.name, "status": o["status"], "outcome": outcome, "top_cause": top,
            "gate_pass": any(s.get("stop_decision") == "stop_diagnosis" for s in steps),
            "gate_refusals": sum(1 for s in steps if "diagnosis refused" in (s.get("stop_reason") or "")),
            "change_round": change_round, "rounds_after_change": len(after), "hyp_changed_after": changed,
            "relevant_change_cited": sorted(cited_rel), "irrelevant_change_cited": sorted(cited_irr),
            "agree": agree, "rationale_only": rat_only, "ledger_only": led_only,
            "parse_failures": sum(1 for s in steps if not s.get("parse_ok", True)),
            "tool_calls": o.get("tool_calls_used"), "rounds": len(steps), "wall_s": round(o.get("wall_seconds") or 0),
            "final_hypotheses": final_hyps}


def main():
    runs = Path(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else RUNS
    rows = [analyze(d) for d in sorted(p for p in runs.iterdir() if p.is_dir())]
    cols = ["run", "outcome", "gate_pass", "gate_refusals", "change_round", "rounds_after_change", "hyp_changed_after",
            "relevant_change_cited", "irrelevant_change_cited", "agree", "rationale_only", "ledger_only",
            "parse_failures", "tool_calls", "wall_s", "top_cause"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    text = "\n".join(lines)
    print(text)
    print()
    for r in rows:
        print(r["run"], "final hypotheses:", r.get("final_hypotheses"))
    (runs / "_analysis.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    if "--md" in sys.argv:
        Path(sys.argv[sys.argv.index("--md") + 1]).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
