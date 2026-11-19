"""Scoring version 2: decisive-evidence matching through EXPLICIT equivalents only.

Lives outside eval/ on purpose: eval/ stays byte-identical to the frozen revision 4dd96c0 while
eval_v1 runs. v1 result files are never modified; v2 writes results/<name>/scores_v2.{json,md}.

Rule (v2). A retrieved or cited evidence id counts for a decisive id D only if
  (a) it equals D, or
  (b) it is listed explicitly in that answer key's injected_fault["decisive_equivalents"][D].
No substring, prefix or template-similarity matching of any kind.

Measures per run
- retrieved: decisive ids whose id or a listed equivalent was in the evidence the system had
  (BlackBox: evidence_seen; B0/B1: evidence_budget.included_ids), as in v1.
- cited (new in v2): decisive ids whose id or a listed equivalent is cited in the final report
  (claims and ranked causes, as stored after the coordinator's citation resolution).
v1 numbers are recomputed side by side with eval.metrics.score_run (eval/ at 4dd96c0, unchanged).

Usage
  python scripts/score_v2.py --name pilot_v1 --runs runs/pilot_v1 --family pilot \
      --answer-keys runs/pilot_v1/_answer_keys_at_run
  python scripts/score_v2.py --name exp_coverage_v1 --runs runs/exp_coverage_v1 --family development
  python scripts/score_v2.py --name eval_v1 --runs runs/eval_v1 --family formal
  python scripts/score_v2.py --name ui_demo_dev --runs runs/ui_jobs --family development \
      --only 20261120T034855-a3febe,20261120T035254-b72b2e
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from blackbox.schemas import AnswerKey, SystemOutput  # noqa: E402

SCORING_VERSION = "v2"
DEFAULT_KEYS = REPO / "evaluator_private" / "answer_keys"


# --------------------------------------------------------------------------- rule
def equivalents_of(key: AnswerKey) -> dict[str, list[str]]:
    raw = (key.injected_fault or {}).get("decisive_equivalents") or {}
    if not isinstance(raw, dict):
        return {}
    return {d: [e for e in (v or []) if isinstance(e, str)] for d, v in raw.items()}


def match_decisive(decisive: list[str], equivalents: dict[str, list[str]],
                   ids: set[str]) -> dict[str, Optional[str]]:
    """For each decisive id: 'exact', 'equivalent:<id>' or None. Exact set membership only."""
    out: dict[str, Optional[str]] = {}
    for d in decisive:
        if d in ids:
            out[d] = "exact"
            continue
        hit = next((e for e in equivalents.get(d, []) if e in ids), None)
        out[d] = f"equivalent:{hit}" if hit else None
    return out


def supplied_ids(out: SystemOutput) -> list[str]:
    if out.system in ("b0", "b1") and out.evidence_budget is not None:
        return list(out.evidence_budget.included_ids)
    return [r.evidence_id for r in out.evidence_seen]


def cited_ids(out: SystemOutput) -> list[str]:
    if out.report is None:
        return []
    ids: list[str] = []
    for c in out.report.claims:
        ids += c.evidence_ids
    for rc in out.report.ranked_causes:
        ids += rc.evidence_ids
    return list(dict.fromkeys(ids))


def score_v2(out: Optional[SystemOutput], key: Optional[AnswerKey]) -> dict[str, Any]:
    decisive = list(dict.fromkeys(key.decisive_evidence_ids)) if (key and key.diagnosable) else []
    if not decisive:
        return {"decisive_total": 0, "retrieved_v2": 0, "cited_v2": 0,
                "retrieved_detail": {}, "cited_detail": {}}
    eq = equivalents_of(key)
    sup = set(supplied_ids(out)) if out is not None else set()
    cit = set(cited_ids(out)) if out is not None else set()
    r = match_decisive(decisive, eq, sup)
    c = match_decisive(decisive, eq, cit)
    return {"decisive_total": len(decisive),
            "retrieved_v2": sum(v is not None for v in r.values()),
            "cited_v2": sum(v is not None for v in c.values()),
            "retrieved_detail": r, "cited_detail": c}


# --------------------------------------------------------------------------- runs
def find_runs(root: Path, only: Optional[list[str]] = None) -> list[Path]:
    dirs = [d for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith("_")
            and (d / "output.json").exists()]
    if only:
        dirs = [d for d in dirs if d.name in only]
    return dirs


def sha256(path: Path) -> Optional[str]:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def score_dir(root: Path, keys_dir: Path, family: str, only: Optional[list[str]] = None) -> dict:
    from eval.metrics import score_run   # v1, eval/ unchanged
    rows, notes = [], []
    for d in find_runs(root, only):
        out = SystemOutput.model_validate_json((d / "output.json").read_text(encoding="utf-8"))
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
        kp = keys_dir / f"{out.incident_id}.json"
        key = AnswerKey.model_validate_json(kp.read_text(encoding="utf-8")) if kp.exists() else None
        key_sha = sha256(kp)
        recorded = meta.get("answer_key_sha256")
        if recorded and key_sha and recorded != key_sha:
            notes.append(f"{d.name}: answer key differs from the one recorded at run time; skipped")
            continue
        status = meta.get("status") or out.status
        v1 = score_run(out, key, run_status=status, system=out.system)
        v2 = score_v2(out, key)
        rows.append({
            "run": d.name, "incident_id": out.incident_id, "system": out.system,
            "ablation": out.ablation, "run_status": status,
            "report_status": out.report.status if out.report else None,
            "answer_key_sha256": key_sha, "answer_key_recorded_at_run": recorded,
            "decisive_total": v2["decisive_total"],
            "retrieved_v1": v1.get("decisive_retrieved"), "retrieved_v2": v2["retrieved_v2"],
            "cited_v2": v2["cited_v2"],
            "changed": v1.get("decisive_retrieved") != v2["retrieved_v2"],
            "top1_auto_v1": v1.get("top1_auto"),
            "retrieved_detail": v2["retrieved_detail"], "cited_detail": v2["cited_detail"],
        })
    return {"family": family, "rows": rows, "notes": notes}


def code_versions() -> dict:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:
        head = None
    return {"scoring_v1": {"file": "eval/metrics.py", "sha256": sha256(REPO / "eval" / "metrics.py")},
            "scoring_v2": {"file": "scripts/score_v2.py", "sha256": sha256(Path(__file__).resolve())},
            "git_head": head}


def render_md(name: str, res: dict) -> str:
    fam = res["family"]
    L = [f"# Decisive-evidence scores v1 vs v2: `{name}` ({fam})", "",
         f"Generated {res['generated']}. v1 = `eval/metrics.py` sha256 "
         f"`{res['code']['scoring_v1']['sha256'][:12]}`; v2 = `scripts/score_v2.py` sha256 "
         f"`{res['code']['scoring_v2']['sha256'][:12]}`.",
         "Rule (both): a decisive id counts only via the same id or an id listed explicitly in "
         "`decisive_equivalents`; no substring, prefix or similarity matching. v2 adds "
         "decisive evidence cited in the final report.", ""]
    if fam != "formal":
        L += [f"**{fam.upper()} results: not part of the formal evaluation.**", ""]
    L += ["| run | system | status | report | decisive | retrieved v1 | retrieved v2 | cited v2 | changed |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        L.append(f"| {r['run']} | {r['system']} | {r['run_status']} | {r['report_status'] or '-'} | "
                 f"{r['decisive_total']} | {r['retrieved_v1']} | {r['retrieved_v2']} | {r['cited_v2']} | "
                 f"{'yes' if r['changed'] else 'no'} |")
    n_changed = sum(r["changed"] for r in res["rows"])
    L += ["", ("Nothing changed: v1 and v2 give the same retrieved-decisive counts for every run."
               if n_changed == 0 else f"{n_changed} run(s) changed between v1 and v2."), ""]
    for note in res["notes"]:
        L.append(f"- NOTE: {note}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python scripts/score_v2.py")
    ap.add_argument("--name", required=True, help="results/<name>/ output directory")
    ap.add_argument("--runs", required=True, help="directory holding run dirs with output.json")
    ap.add_argument("--family", required=True, choices=["pilot", "development", "formal", "live-demo"])
    ap.add_argument("--answer-keys", default=None)
    ap.add_argument("--only", default=None, help="comma-separated run dir names")
    a = ap.parse_args(argv)
    keys = Path(a.answer_keys) if a.answer_keys else DEFAULT_KEYS
    only = [x.strip() for x in a.only.split(",")] if a.only else None
    res = score_dir(REPO / a.runs if not Path(a.runs).is_absolute() else Path(a.runs), keys, a.family, only)
    res.update(name=a.name, scoring_version=SCORING_VERSION, answer_keys_dir=str(keys),
               generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), code=code_versions())
    out = REPO / "results" / a.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "scores_v2.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    (out / "scores_v2.md").write_text(render_md(a.name, res), encoding="utf-8")
    print(render_md(a.name, res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
