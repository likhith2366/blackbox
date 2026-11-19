# Milestone evidence checklist

Which artefact proves which claim. Each claim is marked as checked by an **automated** test or
script, or as needing **human review** (a person must look; the automated layer cannot
decide). Do not tick a human-review item from automated output alone.

| # | Claim | Artefact that proves it | How checked |
|---|---|---|---|
| 1 | Snapshots come from real captures of the running demo, not synthesised telemetry | `scenarios/build.py`, `demo/capture.py`, `evaluator_private/captures/<id>_capture_meta.json`, company repo tag `capture/<id>` | automated: `python -m scenarios.verify` (hashes); human: spot-check one capture's logs and metrics against its timeline |
| 2 | Team-authored data is labelled as fixtures | `tickets.json` (`fixture: true`), backend `SourceStatus.fixture`, `EvidenceRecord.fixture` | automated: `tests/integration/test_qa_backend_contract.py`, `scenarios.verify` |
| 3 | Nothing after the investigation cutoff is visible | backend cutoff filtering; `test_qa_backend_contract.py::test_nothing_after_cutoff_is_returned`; each run's `evidence_seen[].available_time` | automated |
| 4 | No answer leakage into model-visible data | `scenarios.verify`, `tests/test_leakage_*.py`, `tests/test_leakage_imports.py` (blackbox/ never imports demo/eval/evaluator_private) | automated; human: read the tickets of each scenario for answer-revealing text (see QA-N4) |
| 5 | Ablations remove exactly their source, including indirect mentions | `tests/test_leakage_ablation.py`, `test_qa_backend_contract.py::test_no_*` | automated |
| 6 | Tool allowlist enforced; unauthorized requests never executed | `SystemOutput.unauthorized_attempts`, `unauthorized_executed == 0`; `tests/test_investigator_core.py`, `test_qa_investigator.py::test_denied_calls_*` | automated; scenario 9 runs show the planted tool names refused |
| 7 | Budgets (6 rounds, 12 calls, 3 per round) cannot be exceeded | `test_qa_investigator.py` budget tests; `eval.metrics.limit_exceedances` per run | automated |
| 8 | Cited evidence ids were actually retrieved | `FinalReport.citation_audit` / `invalid_citations`; `eval` `invalid_citations_recomputed` | automated |
| 9 | Cited evidence supports each claim | `results/<batch>/` claim worksheet | **human review** (automated layer only marks "cited, pending review") |
| 10 | Diagnosis correctness (top-1 / top-3) | `eval/matching.py` provisional verdicts + diagnosis worksheet | **human review** confirms every provisional match |
| 11 | B1 received exactly BlackBox's evidence (same-evidence comparison) | B1 `evidence_budget.matched`, `included_ids` vs BlackBox `evidence_seen` | automated; unmatched B1 runs excluded from same-evidence claims |
| 12 | Run records are complete and reproducible | `runs/<batch>/<run>/{input,meta,output}.json`, `batch.json` (git head, model digest, matrix sha) | automated (harness); human: rerun one cell with `docs/REPRODUCE.md` |
| 13 | Context window never silently overflowed | `InvestigationStep.context` (`prompt_tokens_est` vs `prompt_tokens_actual`, `prompt_truncated_by_server`) | automated: `test_qa_investigator.py` context tests; pilot estimate was within 5 tokens of Ollama's count on every round |
| 14 | Failures are results, not dropped | harness `meta.json` status for every planned run (`exception`, `failed`, `skipped_dependency`) | automated |
| 15 | Live connector is read-only, bounded, and leaks no secrets | `blackbox/connectors/github_api.py`, `capture.py`; `test_qa_github_connector.py`, `test_qa_connector_capture.py` | automated; smoke-test output in `coordination/agent4_log.md` |
| 16 | Stub output never appears as a result | `runs/_dev_stub/` only, `"stub": true`; viewer banner | automated (eval score/report refuse stub records) |
| 17 | Known defects are disclosed | `docs/QA_FINDINGS.md`, xfail tests in `tests/integration/` | automated |
