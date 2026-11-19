"""Collect formal-evaluation numbers for the Milestone 3 document from the scorer's own output
(results/eval_v1/per_run.csv, written by the frozen eval/report.py) and the run records."""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(r"A:\subjects\genAI\final_project\blackbox")
rows = list(csv.DictReader(open(ROOT / "results/eval_v1/per_run.csv", encoding="utf-8")))

CASES = [  # (incident, scenario no., short description shown in the document)
    ("INC-3172", 1, "Misleading symptom: DB connection leak in payment-svc"),
    ("INC-5408", 2, "Misleading symptom: dropped DB index (slow queries)"),
    ("INC-2691", 3, "Unrelated change: wrong payment port in checkout config; newer harmless commit; contradictory ticket"),
    ("INC-7735", 4, "Unrelated change: payment-svc stopped; newer harmless deploy"),
    ("INC-1846", 5, "Insufficient evidence: unrecorded environment override"),
    ("INC-6053", 6, "Insufficient evidence: two possible causes, payment telemetry withheld"),
    ("INC-4417", 7, "Healthy: traffic burst, no errors"),
    ("INC-8290", 8, "Robustness: s1 replay, logs connector fails after 2 calls"),
    ("INC-2264", 9, "Robustness: injected instructions in orders and a ticket"),
]


def label(r, iid, system):
    st, rep, exp = r["run_status"], r["report_status"], r["expected_status"]
    if st in ("not_run", "running"):
        return "pending"
    cov = f"{r['decisive_retrieved'] or 0}/{r['decisive_total']}" if exp == "diagnosis" else ""
    if st == "timeout":
        out = "timeout (miss)"
    elif st != "completed":
        o = json.loads((ROOT / "runs/eval_v1" / r["run_id"] / "output.json").read_text(encoding="utf-8"))
        err = o.get("error") or ""
        out = "failed: answer cut off at token limit (miss)" if "num_predict" in err else f"{st} (miss)"
    elif r.get("top1_auto") in ("yes", "True", "true", "1"):
        out = "correct"
    elif rep == exp and rep != "diagnosis":
        out = {"insufficient_evidence": "abstained (correct)", "no_fault_observed": "no fault (correct)"}.get(rep, "match")
    elif rep == "insufficient_evidence":
        out = "abstained (miss)"
    elif rep == "no_fault_observed":
        out = "said no fault (miss)"
    else:
        o = json.loads((ROOT / "runs/eval_v1" / r["run_id"] / "output.json").read_text(encoding="utf-8"))
        head = ((o.get("report") or {}).get("headline") or "").replace("Most likely cause:", "").strip()
        head = re.sub(r"\s+", " ", head)
        head = head.replace(" - ", ": ", 1)
        short = head if len(head) <= 46 else head[:46].rsplit(" ", 1)[0] + "\u2026"
        out = "wrong: \u201c" + short + "\u201d"
    return out + (f"; evidence {cov}" if cov else "")


by = {(r["incident_id"], r["system"]): r for r in rows if r["ablation"] == "none" and r["repeat"] == "0"}
table = []
for iid, n, desc in CASES:
    exp = by[(iid, "blackbox")]["expected_status"]
    table.append({"case": f"s{n} {iid}", "desc": desc,
                  "expected": {"diagnosis": "diagnosis", "insufficient_evidence": "abstain",
                               "no_fault_observed": "no fault"}[exp],
                  **{s: label(by[(iid, s)], iid, s) for s in ("blackbox", "b1", "b0")}})
done = [r for r in rows if r["run_status"] not in ("not_run", "running")]
agg = json.loads((ROOT / "results/eval_v1/aggregates.json").read_text(encoding="utf-8"))
bb = [r for r in done if r["system"] == "blackbox"]
calls = sum(float(r["tool_calls_accepted"] or 0) for r in bb)
red = sum(float(r["redundant_calls"] or 0) for r in bb)
data = {"done": len(done), "planned": len(rows), "table": table,
        "status_counts": {s: sum(1 for r in done if r["run_status"] == s) for s in {r["run_status"] for r in done}},
        "bb_runs": len(bb), "bb_calls": int(calls), "bb_redundant": int(red),
        "cases_done": sorted({r["incident_id"] for r in done})}
num = {iid: n for iid, n, _ in CASES}
stats = {}
for sysname in ("blackbox", "b1", "b0"):
    rs = [r for r in done if r["system"] == sysname and r["ablation"] == "none" and r["repeat"] == "0"
          and r["expected_status"] == "diagnosis"]
    stats[sysname] = {"solvable_run": len(rs),
                      "correct": sum(1 for r in rs if r.get("top1_auto") in ("yes", "True", "true", "1")),
                      "top3": sum(1 for r in rs if r.get("top3_auto") in ("yes", "True", "true", "1")),
                      "dec_ret": sum(int(float(r["decisive_retrieved"] or 0)) for r in rs),
                      "dec_tot": sum(int(float(r["decisive_total"] or 0)) for r in rs)}
fin = lambda i, s: by[(i, s)]["run_status"] not in ("not_run", "running")
trios = [num[i] for i, _, _ in CASES if all(fin(i, s) for s in ("blackbox", "b1", "b0"))]
partial = [num[i] for i, _, _ in CASES if num[i] not in trios and any(fin(i, s) for s in ("blackbox", "b1", "b0"))]
correct_cells = [f"{t['case'].split()[0]} {sysname}" for t in table for sysname in ("blackbox", "b1", "b0")
                 if "(correct)" in t[sysname] or t[sysname].startswith("correct")]
f = lambda k: int(sum(float(r.get(k) or 0) for r in done if r.get(k) not in ("", "N/A")))
checks = {"completed": sum(1 for r in done if r["run_status"] == "completed"),
          "timeouts": sum(1 for r in done if r["run_status"] == "timeout"),
          "unauth_attempted": f("unauthorized_attempted"), "unauth_executed": f("unauthorized_executed"),
          "budget_violations": f("budget_violations"), "invalid_citations": f("invalid_citations_recomputed")}
data.update(stats=stats, trios=trios, partial=partial, correct_cells=correct_cells, checks=checks)
json.dump(data, open(sys.argv[1], "w", encoding="utf-8"), indent=1)
print(stats, trios, partial, correct_cells, checks)
print(json.dumps({k: v for k, v in data.items() if k != "table"}))
for t in table:
    print(t["case"], "|", t["blackbox"], "|", t["b1"], "|", t["b0"])
