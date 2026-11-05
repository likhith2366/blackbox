# BlackBox (Milestone 3 build)

Evidence-grounded incident investigation on a local demo system. Design: `docs/milestone2.md`.
Contract, ownership and commands: `CONTRACT.md`.

## Layout
- `demo/` local services, build/deploy tooling, fault controller (Agent 2)
- `scenarios/` frozen evidence snapshots and how to rebuild them (Agent 2)
- `evaluator_private/` answer keys and manifest; never read by `blackbox/`
- `blackbox/` investigator (BlackBox), baselines B0/B1, model adapter, evidence backend
- `eval/` harness, metrics, worksheets, reports (Agent 3); outputs in `runs/` and `results/`

## Quick start
```
pytest -q
python -m blackbox.cli investigate --incident INC-0000 --system blackbox --config configs/dev.yaml --out runs/_manual/pilot_bb
python -m blackbox.cli investigate --incident INC-0000 --system b1 --b1-from runs/_manual/pilot_bb/output.json --config configs/dev.yaml --out runs/_manual/pilot_b1
python -m blackbox.cli show runs/_manual/pilot_bb/output.json
python -m eval.harness run --phase pilot --batch pilot_v1 && python -m eval.report --batch pilot_v1
```
Model: `qwen3:4b` (Q4_K_M) through Ollama on an RTX 3060 6 GB, `num_ctx` 8192, temperature 0, seed 7.

## Data honesty
Logs, traces and metrics come from executing the demo services. Jira tickets, build history and
commit history are team-authored fixtures, labelled `fixture: true`. Evaluation and ablations read
only frozen snapshots; the harness refuses live backends. A separate live demo mode reads GitHub
(commits, configs at a pinned commit SHA, Actions runs/logs) through a read-only REST connector and the
services' own log files; no investigation has yet used GitHub evidence. Jira is snapshot-only.

## Reproducing the evaluation (section maintained by Agent 3)
Run from the repo root. Commands that need the GPU take `runs/_gpu.lock`; nothing here calls the
model except step 4.
1. `pytest -q`: expect 0 failed. Strict xfails are open defects listed in `docs/KNOWN_DEFECTS.md`.
2. `python -m eval.validate_snapshot <INC-id>`: checks schema, cutoff and answer leakage. Exit code 1
   means a failure.
3. `python -m eval.harness plan --phase eval`: shows the 47 planned runs, any skips with their
   reasons, and the frozen config file with its sha256.
4. `python -m eval.harness run --phase eval --batch <name>`: needs frozen snapshots and
   `configs/frozen_v1.yaml`; it is resumable.
5. `python -m eval.report --batch <name>`: writes `results/<name>/` (`report.md`, `per_case.csv`,
   `worksheets/`, `hypothesis_traces/`, `failure_catalogue.md`). To score outputs made before a
   snapshot rebuild, add `--answer-keys <dir>` pointing at the keys used at run time.
6. `python scripts/score_v2.py --name <name> --runs runs/<batch> --family formal`: decisive-evidence
   scoring v2 (exact id, or an id listed explicitly as an equivalent; also counts decisive evidence
   cited in the report). Writes `results/<name>/scores_v2.{json,md}`, with v1 side by side.

Results families, always kept separate:
- pilot: `runs/pilot_v1`;
- development experiment: `runs/exp_coverage_v1`;
- formal evaluation: `runs/eval_v1`;
- supplementary reruns: `runs/eval_v1_rerun`;
- live and development demo jobs: `runs/ui_jobs`.

Automated diagnosis and claim verdicts are provisional until two reviewers fill
`results/<batch>/worksheets/`. See also `docs/FEATURE_STATUS.md`, `docs/KNOWN_DEFECTS.md` and
`docs/DEMO_WALKTHROUGH.md`.
