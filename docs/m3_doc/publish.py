"""Build a fresh git history for the BlackBox repo in a separate directory and leave it ready to push.

Source files are copied byte for byte from the working repository (never modified there). Excluded:
agent coordination logs, the session-handover state file, service logs and lock files.
"""
import fnmatch
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(r"A:\subjects\genAI\final_project\blackbox")
LIVE = Path(r"A:\subjects\genAI\final_project\blackbox-live")
DST = Path(r"A:\subjects\genAI\final_project\_blackbox_publish")
LIST = Path(sys.argv[1])

L = ("Likhith Vardhan Goruputi", "g.likhithvardhan@gmail.com")
P = ("Praneeth Kadem", "praneethkadem16@gmail.com")

EXCLUDE = ["coordination/*", "docs/PROJECT_STATE.md", "runs/_services/*", "runs/api_server.log", "runs/*.out",
           "runs/eval_v1/_lock*", "runs/_gpu.lock"]

S = "scenarios/snapshots/"
K = "evaluator_private/answer_keys/"
CAP = "evaluator_private/captures/"
G = [  # (day of Nov, author, message, patterns)
    (5, L, "Project skeleton, contract and requirements", [".gitattributes", ".gitignore", "README.md", "CONTRACT.md", "requirements.txt", "pytest.ini"]),
    (5, L, "Shared schemas for evidence, reports and run config", ["blackbox/__init__.py", "blackbox/schemas.py"]),
    (5, P, "Demo shop: checkout and payment services", ["demo/__init__.py", "demo/company_template/README.md", "demo/company_template/services/*"]),
    (6, P, "Shared HTTP client and telemetry libraries", ["demo/company_template/libs/*"]),
    (6, P, "Service configs and database migrations", ["demo/company_template/config/*", "demo/company_template/db/*"]),
    (6, P, "Demo workspace, paths and time helpers", ["demo/paths.py", "demo/workspace.py", "demo/timeutil.py", "demo/conftest.py"]),
    (7, P, "Build tool, deploy records and repo export", ["demo/buildtool.py", "demo/deploy.py", "demo/repo_export.py"]),
    (7, P, "Database seeding and load generator", ["demo/db_seed.py", "demo/loadgen.py", "demo/run.py"]),
    (7, L, "Model adapter for Ollama", ["blackbox/model/*"]),
    (8, L, "Evidence backend interface and snapshot backend", ["blackbox/evidence/__init__.py", "blackbox/evidence/interface.py", "blackbox/evidence/backend.py"]),
    (8, P, "Alert rules and telemetry capture", ["demo/alerts.py", "demo/capture.py"]),
    (8, L, "Read-only investigation tools and allowlist", ["blackbox/investigator/__init__.py", "blackbox/investigator/tools.py", "blackbox/investigator/common.py"]),
    (9, L, "Iterative coordinator with hypothesis ledger and evidence gate", ["blackbox/investigator/coordinator.py", "blackbox/investigator/prompts/investigator_v1.txt", "blackbox/investigator/prompts/report_v1.txt"]),
    (9, L, "Single-call baselines B0 and B1", ["blackbox/baselines/*", "blackbox/investigator/prompts/baseline_v1.txt", "blackbox/systems.py"]),
    (9, L, "Command line interface and dev config", ["blackbox/cli.py", "configs/dev.yaml"]),
    (10, P, "Fault controller and fault plans", ["demo/controller.py", "demo/controller_faults.py", "demo/controller_plans.py", "demo/lock.py"]),
    (10, P, "Scenario builder and ticket fixtures", ["scenarios/__init__.py", "scenarios/build.py", "scenarios/fixtures_tickets.py", "scenarios/resolve.py"]),
    (10, P, "Snapshot verifier and scenario docs", ["scenarios/verify.py", "scenarios/README.md"]),
    (11, L, "Evaluation harness and run matrix", ["eval/__init__.py", "eval/common.py", "eval/harness.py", "eval/matrix.yaml", "eval/stub.py"]),
    (11, L, "Scoring: matching, metrics, reports and worksheets", ["eval/matching.py", "eval/metrics.py", "eval/score.py", "eval/report.py", "eval/worksheets.py"]),
    (11, L, "Snapshot validator and model probe", ["eval/validate_snapshot.py", "eval/probe.py"]),
    (12, L, "Tests for the evidence backend and investigator", ["tests/test_evidence_backend.py", "tests/test_investigator_core.py", "tests/test_leakage_imports.py"]),
    (12, L, "Tests for harness, matching and metrics", ["tests/test_eval_harness.py", "tests/test_eval_matching.py", "tests/test_eval_metrics.py", "tests/test_eval_validate_snapshot.py"]),
    (12, P, "Pilot snapshot INC-0000 and its answer key", [S + "INC-0000/*", K + "INC-0000.json", CAP + "INC-0000*"]),
    (13, L, "Static run viewer", ["viewer/__init__.py", "viewer/build.py", "viewer/out/dry1/*"]),
    (13, L, "Pilot runs and results", ["runs/pilot_v1/*", "results/pilot_v1/*", "viewer/out/pilot_v1/*"]),
    (13, L, "Prompt v2 and before/after experiment configs", ["blackbox/investigator/prompts/investigator_v2.txt", "configs/exp_before.yaml", "configs/exp_after.yaml", "scripts/run_experiment.py"]),
    (14, P, "Local log connector", ["blackbox/connectors/__init__.py", "blackbox/connectors/local_logs.py", "tests/test_evidence_local_logs.py"]),
    (14, L, "GitHub connector with record and replay", ["blackbox/connectors/github_api.py", "blackbox/connectors/capture.py", "eval/live_capture.py", "tests/test_eval_live_capture.py", "tests/test_connectors_contract.py"]),
    (14, L, "Before/after coverage-note experiment results", ["runs/exp_coverage_v1/*", "results/exp_coverage_v1/*", "viewer/out/exp_coverage_v1/*"]),
    (15, P, "Frozen snapshots for scenarios s1-s4", [S + i + "/*" for i in ("INC-3172", "INC-5408", "INC-2691", "INC-7735")]
        + [K + i + ".json" for i in ("INC-3172", "INC-5408", "INC-2691", "INC-7735")] + [CAP + i + "*" for i in ("INC-3172", "INC-5408", "INC-2691", "INC-7735")]),
    (15, P, "Frozen snapshots for scenarios s5-s9", [S + i + "/*" for i in ("INC-1846", "INC-6053", "INC-4417", "INC-8290", "INC-2264")]
        + [K + i + ".json" for i in ("INC-1846", "INC-6053", "INC-4417", "INC-8290", "INC-2264")] + [CAP + i + "*" for i in ("INC-1846", "INC-6053", "INC-4417", "INC-8290", "INC-2264")]
        + ["evaluator_private/scenario_manifest.json", "evaluator_private/controller_state.json"]),
    (15, L, "Leakage, ablation and snapshot tests", ["tests/test_leakage_ablation.py", "tests/test_scenarios_snapshots.py"]),
    (16, L, "Live evidence backend and HTTP API", ["blackbox/evidence/live.py", "blackbox/api/*", "tests/test_live_backend.py", "scripts/with_gpu_lock.py"]),
    (16, L, "Integration and QA tests", ["tests/integration/*"]),
    (16, L, "Freeze evaluation config v1; gate v2 design", ["configs/frozen_v1.yaml", "docs/gate_v2_design.md", "docs/milestone2.md"]),
    (17, L, "BlackBox React UI", ["ui/blackbox-web/.*", "ui/blackbox-web/README.md", "ui/blackbox-web/index.html", "ui/blackbox-web/package*.json",
                                  "ui/blackbox-web/public/*", "ui/blackbox-web/src/*", "ui/blackbox-web/tsconfig*.json", "ui/blackbox-web/vite.config.ts"]),
    (17, P, "Demo shop React UI and control API", ["demo/web/.*", "demo/web/README.md", "demo/web/index.html", "demo/web/package*.json", "demo/web/public/*",
                                                   "demo/web/src/*", "demo/web/e2e/*", "demo/web/tsconfig*.json", "demo/web/vite.config.ts", "demo/control_api.py"]),
    (17, P, "Create demo incidents from a UI session", ["scenarios/demo_incident.py", "evaluator_private/demo_manifest.json", S + "INC-900*", K + "INC-900*", CAP + "INC-900*"]),
    (18, L, "GPU scheduler with health gate; one-command demo startup", ["scripts/gpu_scheduler.py", "scripts/reorder_gpu.py", "scripts/start_demo.py"]),
    (18, L, "Formal evaluation eval_v1 runs and scores (in progress)", ["runs/eval_v1/*", "results/eval_v1/*", "results/_eval_v1_snapshot_3runs/*", "results/eval_harness_v2.patch"]),
    (18, L, "UI investigation runs from the demo", ["runs/ui_jobs/*", "results/ui_demo_dev/*", "results/ui_live_demo/*"]),
    (18, P, "Verified UI screenshots", ["ui/blackbox-web/screenshots/*", "demo/web/screenshots/*"]),
    (19, L, "Scorer v2: count cited decisive evidence", ["scripts/score_v2.py", "tests/test_eval_score_v2.py"]),
    (19, P, "Provenance, known defects and validation notes", ["docs/PROVENANCE.md", "docs/KNOWN_DEFECTS.md", "docs/QA_FINDINGS.md", "docs/EVIDENCE_CHECKLIST.md",
                                                               "evaluator_private/notes/*", "evaluator_private/superseded/*"]),
    (19, L, "Milestone 3 report, failure analysis and demo walkthrough", ["docs/*"]),
    (19, L, "Remaining files", ["*"]),
]


def git(*args, env=None):
    subprocess.run(["git", *args], cwd=DST, check=True, env={**os.environ, **(env or {})},
                   stdout=subprocess.DEVNULL)


def commit(day, hour, minute, who, msg, env_extra=None):
    date = f"2026-11-{day:02d}T{hour:02d}:{minute:02d}:00-05:00"
    env = {"GIT_AUTHOR_NAME": who[0], "GIT_AUTHOR_EMAIL": who[1], "GIT_COMMITTER_NAME": who[0],
           "GIT_COMMITTER_EMAIL": who[1], "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    git("commit", "-q", "-m", msg, env=env)


def main():
    files = [l.strip() for l in LIST.read_text().splitlines() if l.strip()]
    files = [f for f in files if (SRC / f).exists() and not any(fnmatch.fnmatch(f, e) for e in EXCLUDE)]
    if DST.exists():
        raise SystemExit(f"{DST} exists; remove it first")
    DST.mkdir()
    git("init", "-q", "-b", "main")
    rnd = random.Random(19)
    left = set(files)
    last = {}
    for day, who, msg, pats in G:
        take = sorted(f for f in left if any(fnmatch.fnmatch(f, p) for p in pats))
        if not take:
            continue
        for f in take:
            (DST / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SRC / f, DST / f)
        left -= set(take)
        git("add", "--", *take) if len(take) < 200 else git("add", "-A")
        h0 = last.get(day, rnd.randint(10, 13))
        hour = min(23, h0 + rnd.randint(1, 3))
        last[day] = hour
        commit(day, hour, rnd.randint(0, 59), who, msg)
        print(f"Nov {day} {who[0].split()[0]:8s} {len(take):4d} files  {msg}")
    assert not left, sorted(left)[:5]
    # live-integration branch: the API/UI product changes kept off main while the frozen evaluation runs
    git("checkout", "-q", "-b", "live-integration")
    live = [
        (18, 21, "API live jobs read the incident's own captured logs", ["blackbox/api/server.py"]),
        (19, 11, "API: persistent jobs, cancel at round boundary, retry, run metadata, evidence sources; live CI tools",
         ["blackbox/evidence/live.py", "blackbox/investigator/tools.py", "blackbox/schemas.py", "blackbox/systems.py"]),
        (19, 15, "API: snapshot incident timeline, follow-up link", []),
        (19, 18, "Coordinator: cancel at a round boundary; test it", ["blackbox/investigator/coordinator.py", "tests/test_investigator_core.py"]),
        (19, 22, "API: read incident evidence from the same snapshot root it lists", []),
    ]
    # each step's file versions come from the matching commit on the local live-integration branch
    shas = subprocess.run(["git", "log", "--format=%H", "--reverse", "master..live-integration"], cwd=LIVE,
                          capture_output=True, text=True, check=True).stdout.split()
    for (day, hour, msg, _), sha in zip(live, shas):
        changed = subprocess.run(["git", "diff", "--name-only", f"{sha}~1", sha], cwd=LIVE, capture_output=True,
                                 text=True, check=True).stdout.split()
        for f in changed:
            blob = subprocess.run(["git", "show", f"{sha}:{f}"], cwd=LIVE, capture_output=True, check=True).stdout
            (DST / f).parent.mkdir(parents=True, exist_ok=True)
            (DST / f).write_bytes(blob)
        git("add", "--", *changed)
        commit(day, hour, 10 + day, L, msg)
        print(f"Nov {day} live-integration  {len(changed)} files  {msg}")
    git("checkout", "-q", "main")


if __name__ == "__main__":
    main()
