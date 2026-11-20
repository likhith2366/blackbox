# Feature status

Owner: Evaluation. Last updated 2026-11-20.

- **Verified**: shown working by a passing test or a real run record. The evidence is named.
- **Implemented, unverified**: the code exists, but nothing on record shows it working end to end.
- **Incomplete**: missing, or partly working.

Results are kept separate: pilot (development), formal evaluation (eval_v1) and live demonstration.
"Verified" means the feature works as built. It never means a diagnosis was correct.

## Investigation and baselines
| Feature | Status | Evidence |
|---|---|---|
| BlackBox iterative investigation (6 rounds, 12 calls, max 3 per round) | Verified | runs `pilot_v1`, `exp_coverage_v1`, `eval_v1/INC-3172_blackbox_none_r0`; QA budget tests (`tests/integration/test_qa_investigator.py`) |
| Read-only tool allowlist; unauthorized calls refused, never executed | Verified (test doubles) | QA budget/adversarial tests. Pilot runs: 0 attempts. The planted-injection scenario (s9) is not yet run |
| Citation audit (exact/embedded/suffix; ambiguous refused) | Verified | QA-F01/F01b regression tests; `citation_audit` in run outputs |
| Diagnosis gate v1 (≥2 retrieved citations, ≥1 runtime) | Verified as built, but **insufficient** | Gate refusals in `pilot_v1`. It accepted symptom-only diagnoses (KNOWN_DEFECTS D1) |
| Gate v2 (causal-support check) | Incomplete (design only) | `docs/gate_v2_design.md` |
| Per-run call cache (repeats spend budget) | Verified | `cache_hits` in `pilot_v1` (3) and `eval_v1` s1 (1) |
| B0 single call (alert + logs that fit) | Verified | `pilot_v1/INC-0000_b0_none_r0` (completed); eval_v1 s1 B0 timed out (environment-affected, D9) |
| B1 single call on the BlackBox evidence (matched/unmatched) | Verified | `pilot_v1/INC-0000_b1_none_r0` (matched, completed); eval_v1 s1 B1 timed out (D9) |
| Context accounting (estimate vs Ollama count) | Verified | QA pilot review: estimate = actual + 5 on all 6 rounds |

## Evidence, snapshots and isolation
| Feature | Status | Evidence |
|---|---|---|
| Captured logs/spans/metrics/deployments/builds from running services | Verified | Evidence capture logs; `scenarios/snapshots/*` |
| Frozen snapshots + private answer keys | Verified | manifest: all nine eval snapshots frozen; `scenarios.verify` |
| Investigation cutoff | Verified | `tests/test_leakage_ablation.py::test_no_post_cutoff_records`; `eval.validate_snapshot` |
| Ablations no_jira / no_provenance / no_traces | Verified (leakage) | `tests/test_leakage_ablation.py`, passing on all snapshots present. Limitation D11 |
| Snapshot answer-leakage check | Verified | `python -m eval.validate_snapshot <id>`; `results/snapshot_validation/*.json` |
| Simulated connector failure (s8) | Implemented, unverified in a run | backend code and QA robustness tests; s8 not yet run in eval_v1 |
| `blackbox/` cannot import evaluator/controller/scenario code | Verified | `tests/test_leakage_imports.py` |

## Evaluation tooling
| Feature | Status | Evidence |
|---|---|---|
| Harness (sequential, resumable, pilot/eval separation, frozen-only, live refused) | Verified | `tests/test_eval_harness.py`; real batches `pilot_v1`, `eval_v1` |
| Per-run provenance (config sha, git, model digest, answer-key sha) | Verified | `runs/eval_v1/*/meta.json` |
| Answer-key mismatch refusal | Verified | `tests/test_eval_harness.py::test_score_refuses_changed_answer_key`; pilot re-scored with the run-time key |
| Metrics (Milestone 2 s5) and provisional matching | Verified (arithmetic) | `tests/test_eval_metrics.py`, `test_eval_matching.py`. Semantic verdicts need human review (D12, D13) |
| Two-reviewer worksheets | Implemented; review not started | `results/*/worksheets/` (reviewer columns blank) |
| Reports (per case, repeats, ablations, B1 same-evidence, traces, failure catalogue) | Verified on pilot and partial eval | `results/pilot_v1/`, `results/eval_v1/` |
| Supplementary rerun / path-scoped git metadata ("harness v2") | Implemented, not applied | `results/eval_harness_v2.patch`; applied after eval_v1 |
| GPU lock shared by harness, API and scheduler | Verified | `tests/test_eval_harness.py::test_gpu_lock_*`; `runs/_gpu.lock` used by the UI job |

## Live integrations (development and demo only; never in evaluation)
| Feature | Status | Evidence |
|---|---|---|
| GitHub connector: commits, diffs, files pinned to a commit | Verified (connector level) | QA smoke test on likhith2366/blackbox-demo-shop @063397d; `tests/test_connectors_contract.py` (recorded fixtures) |
| GitHub connector used in an investigation | **Incomplete** | live UI job 20261120T032323-52d9fe chose no GitHub tool; 0 `gh:` records |
| GitHub Actions connector | Implemented, unverified in an investigation | contract tests pass; the repository has 1 workflow with 2 runs |
| Local-log connector | Verified | live UI job: `search_logs` returned 15 live records; contract tests. Defects D3, D4 |
| Live capture / replay of GitHub responses | Implemented | `blackbox/connectors/capture.py`; format verifier `eval/live_capture.py` (tests pass) |
| Live incident from an injected fault (demo incident snapshot) | Verified (development only) | INC-9001, INC-9002; `06_demo_incident_created.png` (pool-fastpath, retry-policy) |
| Jira, Jenkins | Not configured | ticket and build fixtures are used instead |

## Interfaces
| Feature | Status | Evidence |
|---|---|---|
| BlackBox API (FastAPI, :8700) incl. jobs, SSE progress | Verified (one live job) | `runs/ui_jobs/20261120T032323-52d9fe` |
| BlackBox web UI (:5173) | Verified | `ui/blackbox-web/screenshots/verified/journey_INC-9001/` (10-step journey) |
| Demo shop UI (:5174) and control panel (control API :8710) | Verified | `demo/web/screenshots/verified/{pool-fastpath,retry-policy}/` 01-08 |
| Operator reset (control panel) | Verified | `07_reset_again.png`, `08_healthy_after_reset.png`. It is not a BlackBox repair |
