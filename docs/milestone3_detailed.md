# BlackBox: Milestone 3 report (DRAFT v3, 2026-11-20)

Factual status only (draft owner: Agent 3). **PENDING** = no measured result yet. Every number
comes from a run record on disk. Automated diagnosis and claim judgements are **provisional**
until two human reviewers confirm them.

## 1. System implemented so far
**Demo application and data.** checkout-svc calls payment-svc over HTTP; both use a shared HTTP
client library and SQLite. Faults are real commits, config changes or operational actions, built and
deployed by the demo tooling. The data comes from three kinds of source:
- **captured** from the running services: logs, spans, metrics, deployments and builds;
- **team-authored** history in a real git repository: commits and configs;
- **team-authored Jira-style fixtures**: tickets.

Jira and Jenkins are not configured.

**Snapshots and isolation.** Each capture becomes a frozen, model-visible snapshot plus a private
answer key. The evidence backend enforces the investigation cutoff, three source ablations
(no_jira, no_provenance, no_traces) and simulated connector failure in code. Tests enforce that
`blackbox/` never imports the evaluator, the controller or the scenario builder.

**Systems.** All three use the same model, decoding settings and report schema. The model is
`qwen3:4b` (Q4_K_M) on Ollama with an RTX 3060 6 GB and `num_ctx` 8192. At 16384 the model spilled to
CPU and generation fell from about 15 to about 2 tokens/s (measured).
- **BlackBox:** an iterative coordinator with 6 rounds, 12 tool calls and at most 3 calls per round.
  It has a read-only tool allowlist enforced in code, a hypothesis ledger and a citation audit;
  ambiguous citations are refused. Repeated calls are served from a per-run cache but still spend
  budget. Two orchestration rules apply: a first-tool rule and a diagnosis gate.
- **B0:** a single call with the alert and incident-window logs.
- **B1:** a single call with the evidence the initial BlackBox run retrieved.

**Evaluation tooling.**
- A sequential, resumable harness. Pilot and eval phases are separate. The eval phase refuses
  unfrozen snapshots and live backends.
- Each run records config, code revision, model digest and answer-key sha256.
- Metrics follow Milestone 2 section 5.
- Two-reviewer worksheets, with reviewer columns left blank.
- Reports: per-case table, repeats, ablations, B1 same-evidence comparison, hypothesis traces and a
  failure catalogue.
- A snapshot validator, plus ablation and cutoff leakage tests.

**Live integrations (development only; never in the 47-output matrix).**
- GitHub connector: real commit, diff and pinned-file retrieval verified on
  likhith2366/blackbox-demo-shop @063397d.
- Local-log connector: implemented and smoke-tested.
- Live backend (branch live-integration, 963c2ee): reads commits and configs from GitHub, pinned to the
  deployed commit e7a0cc0, and logs from the app's JSONL files. End-to-end investigation **PENDING**.
- GitHub Actions: 1 workflow, 2 successful runs; connector **PENDING**.

## 2. Baseline results: development pilot (not an evaluation result)
`pilot_v1` ran on pilot incident INC-0000, **old snapshot**, git 45e1314, `configs/dev.yaml`. The pilot
was later **recaptured**, because the demo repository history was re-authored (new fault commit
e7a0cc0). These results remain a development finding on the old snapshot. They are scored against
the answer key that was in force at run time.

| | BlackBox | B1 | B0 |
|---|---|---|---|
| Report status | insufficient_evidence | diagnosis | diagnosis |
| Top-1 / top-3 (provisional) | miss / miss | miss / miss | miss / miss |
| Decisive evidence retrieved | 0/4 | 0/4 | 0/4 |
| Asserted claims with no valid citation | 0/2 | 0/3 | 0/3 |
| Tool calls (backend + cache hits) | 6 (3 + 3) | - | - |
| Tokens / wall time | 25,379 / 292 s | 10,588 / 311 s | 6,944 / 111 s |

- **B1** was matched: all 11 BlackBox records fitted.
- **B0's** 5,792-token evidence budget held the newest 35 of 3,245 log records. They used 5,777 tokens,
  about 165 per record on this capture (a measurement, not a general limit). The whole prompt was
  6,240 tokens. None of the 35 records was decisive.
- **A valid citation is not proof of support.** All 8 asserted claims cite retrieved ids, and all await
  semantic review. QA found one BlackBox claim citing a trace of an order that succeeded after retries.

## 3. Current results
**Before/after experiment** (development pilot).
- Setup: `exp_before` vs `exp_after` differ only in `coverage_hint`. 2 runs each, interleaved, at
  frozen revision 4d2951e on a frozen snapshot copy.
- A first launch failed on an import-path error before any run started. It was relaunched with no
  code change.

| | before r1, r2 | after r1, r2 |
|---|---|---|
| Behaviour | identical in both runs | identical in both runs |
| Tools used | metrics 1, traces 5 (6 calls, 2 cached repeats) | logs 1, metrics 5 (6 calls, 2 cached repeats) |
| Decisive retrieved | 0/4 | 1/4 (log:aea2b46ce0) |
| Outcome | insufficient_evidence (stop_rounds) | **wrong** diagnosis: payment-svc :: "HTTP 500 errors", accepted by gate v1 |
| Wall time | 286 s / 389 s | 435 s / 861 s |

- The wall-time differences may come from machine load (scenario captures were running at the same
  time); this is unproven.
- Both "after" runs got status error (ValueError) when the model passed a malformed timestamp to
  get_metrics ('2026-11-20T020:07.510'). It should be invalid_args. This backend argument-validation
  gap is left as is in the frozen evaluation code.
- **Decision:** the hint changed which sources were queried but gave no accuracy benefit, so it was not
  adopted. Evaluation config `configs/frozen_v1.yaml` equals exp_before, at code 4dd96c0. With n = 2 per
  arm on one case, no accuracy or reliability claim is made.

**Evaluation batch eval_v1** (frozen_v1, s1–s4, main cells first, started 03:21Z).
- `batch.json` records `git_dirty = true`: the runs use code 4dd96c0 plus uncommitted working-tree
  changes.
- Planned 47; s5–s9 are not frozen, so their runs are recorded as not_run.
- Per-case results: **PENDING** (filled in as each case's three main runs complete).

**Scenarios and snapshots.**
- Frozen: s1 INC-3172, s2 INC-5408, s3 INC-2691, s4 INC-7735 (being run in eval_v1).
- Built but not frozen: s5 INC-1846, s6 INC-6053.
- Not yet frozen: s7 INC-4417, s8 INC-8290, s9 INC-2264.

**Snapshot validation and leakage tests** (two separate checks).
- `eval.validate_snapshot` (schema, cutoff, answer leakage): passed for INC-0000, INC-3172, INC-5408 and
  INC-6053. INC-2691 and INC-7735 were checked before their answer keys existed and are due for a rerun.
- Ablation and cutoff leakage tests: all passed on the 6 snapshots present. The deployed-sha check
  exempts `configs` (repository versions; approved). Shas, build ids and digests are still asserted
  absent from deployments, builds, logs, spans, metrics and the summaries.

**Tests.** `pytest -q` on the working tree: **294 passed, 1 skipped, 8 xfailed, 0 failed.**
- The skip is the opt-in live GitHub test.
- The 8 xfails are open QA defects kept as strict expected failures: QA-F09 (CLI, 6 cases), QA-F10 and
  QA-F11 (local-log connector). Open defects remain, so this is not a claim that all guardrails pass.

## 4. Experiments and comparisons completed
- Pilot B0 / B1 / BlackBox: a single development case, so no comparative claim.
- Before/after coverage hint: 4 of 4 runs.
- Leakage verification on every built snapshot.
- Evaluation main cells: in progress. Repeats and ablations have not started.

## 5. What worked
- **Isolation:** independent QA found no fault id, change id or evaluator term in snapshot files or in
  retrieved records. All records were within the cutoff.
- **Guardrails (pilot):** 0 unauthorized actions, 0 budget violations, invalid citations caught.
- **Context accounting:** on all 6 pilot BlackBox rounds the prompt-token estimate was Ollama's count + 5.
- **Determinism:** each experiment configuration repeated identically.
- **Failure recording:** failed and incomplete runs are kept with their status.

## 6. What failed, and likely causes
**Pilot misses (provisional).**
- BlackBox never queried logs, deployments or commits.
- The gate refused its diagnosis requests in rounds 2–6, because the top hypothesis cited one id.
- Rounds 4–6 repeated round 3's call (3 cache hits), and duplicate-call feedback did not change this.
- B0 saw only the newest 35 log records. B1 reasoned over 11 non-decisive records.
- B0 rank 2 ("checkout-svc :: retrying payment service with HTTP 500"): the component matches and the
  mechanism does not, under the lexical matcher. Pending human review.
- B1's "ConnectionAbortedError" is a downstream symptom: textually supported, not causal (QA).

**Gate v1 accepted a wrong diagnosis.** It checks citation format (at least 2 retrieved ids, at least 1
runtime source), not causal support. Both cited records were checkout-svc symptom lines.

**Scoring risk, fixed.** After the pilot recapture, re-scoring old outputs silently used the new
answer key. Scoring now refuses a key-hash mismatch unless pointed at the key used at run time.

**Matcher.** "retrying" does not stem to "retry". This is unchanged, and every match goes to human
review anyway.

## 7. Changes attempted and their effects
**Tested bug fixes** (each with a test that now passes):
- QA-F01/F01b: padded or prefix citation ids were recovered as real ones.
- F02: a transport error was reported as a completed diagnosis.
- F03: a malformed backend result crashed the run.
- F04: wrongly typed model JSON.
- F05: the prompt could exceed the context window.
- F07: wrong truncation flag.
- F08: decisive coverage ignored the recorded equivalent ids.

**Diagnosis changes with no measured accuracy effect:**
- state-dependent response schema, citation recovery, prompt v2 and the call cache;
- the coverage hint: sources changed and decisive coverage went from 0/4 to 1/4, but the diagnosis was
  wrong in both "after" runs;
- gate v2 (causal-support check): design only (`docs/gate_v2_design.md`), not implemented or tested.

## 8. Remaining gaps vs success criteria
- Evaluation outputs: s1–s4 main cells in progress; s5–s9 not frozen.
- Two-reviewer scoring has not started.
- Teammate review of answer keys is not yet recorded.
- The gate does not check causal support.
- Open QA items: F06, F09–F11, and N4 (a pilot ticket comment that nearly states the answer).
- Live end-to-end investigation and the Actions connector are PENDING.

## 9. Plan for the final two weeks (prioritized)
1. Freeze and validate s5–s9; record a teammate review of every answer key.
2. Complete all 47 outputs (main 27, repeats 8, ablations 12).
3. Two-reviewer scoring. Then report the confirmed accuracy, unsupported-claim rate, abstention,
   coverage and trajectories.
4. Analyse every miss in the failure catalogue.
5. Gate v2 only as a separately labelled experiment, never in the frozen configuration.
6. Demo, with the live backend labelled as development.
