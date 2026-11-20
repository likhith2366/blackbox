# Reproducing BlackBox end to end

Run every command from the repository root (`A:\subjects\genAI\final_project\blackbox`) with
Python 3.11. Commands are listed in dependency order. Steps marked **GPU** call the model
(Ollama, `qwen3:4b`) and must run one at a time, never in parallel with the demo load generator.

## 0. Environment
```
pip install -r requirements.txt          # pydantic 2, requests, fastapi, uvicorn, pyyaml, jsonschema, pytest, pandas, transformers
ollama pull qwen3:4b                     # model used for all results (Q4_K_M)
ollama serve                             # http://127.0.0.1:11434
```
The demo company's source repository is a separate git repo, by default `../blackbox-demo-shop`
(override with env `BLACKBOX_COMPANY_REPO`). `demo.controller reset` recreates its baseline
history with identical commit SHAs.

## 1. Tests (no model, no network)
```
pytest -q                                # all unit, contract and QA tests
pytest -q tests/integration              # QA QA suite only (xfail = known open defect, see docs/QA_FINDINGS.md)
```

## 2. Demo application and scenarios (no model)
```
python -m demo.run --healthy             # start checkout-svc + payment-svc, healthy seeded load, print health
python -m demo.run --status
python -m demo.controller list           # private fault ids (evaluator side)
python -m demo.controller inject <fault_id>
python -m demo.controller reset          # back to the healthy baseline
python -m demo.run --stop

python -m scenarios.build pilot          # real capture -> scenarios/snapshots/INC-0000 + private answer key
python -m scenarios.build --all          # pilot + nine evaluation scenarios
python -m scenarios.build <slug> --from-capture   # re-normalise an existing raw capture (no new run)
python -m scenarios.verify               # schema, hashes, leakage, decisive ids, cutoff, ablations
python -m eval.validate_snapshot INC-0000
python -m scenarios.build <slug> --freeze         # freeze after verify passes (frozen snapshots are never rewritten)
```

## 3. Single runs (GPU)
```
python -m blackbox.cli investigate --incident INC-0000 --system blackbox --config configs/dev.yaml --out runs/_manual/bb
python -m blackbox.cli investigate --incident INC-0000 --system b1 --b1-from runs/_manual/bb/output.json --config configs/dev.yaml --out runs/_manual/b1
python -m blackbox.cli investigate --incident INC-0000 --system b0 --config configs/dev.yaml --out runs/_manual/b0
python -m blackbox.cli investigate --incident INC-0000 --system blackbox --ablation no_jira --out runs/_manual/bb_nojira
python -m blackbox.cli show runs/_manual/bb/output.json          # no model call
```
B1 must use the `output.json` of the ablation-`none` BlackBox run of the same incident.

## 4. Batches (GPU; harness runs strictly sequentially)
Real runs are refused until `coordination/agent1_log.md` contains `PIPELINE READY`.
```
python -m eval.harness plan --phase pilot --matrix eval/matrix.yaml
python -m eval.harness run  --phase pilot --matrix eval/matrix.yaml --batch pilot_v1
python -m eval.harness plan --phase eval  --matrix eval/matrix.yaml          # expects 47 runs, frozen snapshots only
python -m eval.harness run  --phase eval  --matrix eval/matrix.yaml --batch eval_v1
```
An interrupted batch resumes with the same command, and finished runs are skipped. The
matrix's `run_config_file` points at the frozen config (`configs/frozen_v1.yaml` once frozen).

## 5. Scoring and reports (no model)
```
python -m eval.score  --batch eval_v1    # objective metrics + review worksheets -> results/eval_v1/
python -m eval.report --batch eval_v1    # tables, failure catalogue, summary
```
Semantic verdicts in the worksheets are provisional until a human fills the review fields.

## 6. Inspect saved runs (no model)
```
python -m viewer.build --runs runs/pilot_v1 --out viewer/out/pilot_v1
python -m viewer.build --runs runs/_dev_stub/dry1 --out viewer/out/dry1     # stub pages are bannered STUB
```
Open `viewer/out/<batch>/index.html` in a browser. The viewer only reads `output.json`,
`meta.json` and `input.json`.

## 7. Live GitHub connector (network, read-only; never used for the 47-run matrix)
```
python -m blackbox.connectors.github_api smoke --repo octocat/Hello-World       # connector smoke test, not project evidence
python -m blackbox.connectors.github_api smoke --repo likhith2366/blackbox-demo-shop
```
Credentials come from env `GITHUB_TOKEN` or `gh auth token` and are kept in memory only.
To make a live run replayable, wrap the session:
`GitHubConnector(repo, session=RecordingSession(capture_dir_for("<id>")))`, and later replay
it with `ReplaySession(capture_dir_for("<id>"))` (module `blackbox.connectors.capture`).

## Development-only stub (no model)
```
python -m eval.harness run --phase eval --matrix eval/matrix.yaml --batch dry1 --stub   # writes runs/_dev_stub/dry1 only
python -m eval.score --batch dry1 --stub
```
