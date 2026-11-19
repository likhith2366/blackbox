# BlackBox: Milestone 3 Report

Likhith Vardhan Goruputi · Praneeth Yashovardhan Kadem · Bhavana Peruri, 20 November 2026

We keep four families of results apart throughout this report. **Pilot** runs were used for
development. The **development before/after** runs tested one prompt change. The **formal evaluation
(eval_v1)** is partial: it runs on frozen code, configuration and snapshots. The **live demo** runs
come from the user interface. Diagnosis and claim verdicts come from an automated, lexical scorer and
remain provisional until two of us review the worksheets. An abstention (insufficient_evidence) on a
case the answer key marks diagnosable counts as a diagnosis miss.

## 1. What is built

**Demo shop and investigated system.** checkout-svc calls payment-svc over HTTP; both use a shared HTTP
client and SQLite. Faults are real commits, config changes or operator actions, and each one is built and
deployed by our tooling. Logs, traces, metrics, deployment records and build records are captured from
the running services. Commits and configs come from the demo-shop git repository, whose history the team
authored. Its provenance is documented in `docs/PROVENANCE.md`: `main` (b7ae74f) and the pilot
capture tag (e7a0cc0) match GitHub. Every frozen fault commit and both demo commits exist only
locally, so snapshot mode is the only valid way to investigate those incidents. A live GitHub run on
them would read a different history and must be labelled that way. Tickets are team-authored
Jira-style fixtures.

**BlackBox and the baselines.** All three systems run `qwen3:4b` on Ollama on an RTX 3060 6 GB laptop GPU
(context 8,192), with the same decoding settings and the same report schema.
- **BlackBox** runs up to 6 rounds and 12 read-only tool calls (3 per round). It keeps a hypothesis
  ledger, audits every citation and caches repeated calls; a cached repeat still uses budget.
- **B0** is a single call with the alert and the logs that fit.
- **B1** is a single call with the evidence that BlackBox retrieved.

**Gate v1.** BlackBox may only stop with a diagnosis when its top hypothesis cites at least 2 retrieved
evidence ids, at least 1 of which comes from a source in
`RUNTIME_SOURCES = {"logs", "traces", "metrics", "configs", "deployments"}`
(`blackbox/investigator/common.py`). The gate enforces these evidence requirements. It does not check
whether the stated cause is true.

**Evaluation tooling.**
- A resumable harness that refuses unfrozen snapshots and live backends.
- Per-run provenance: config hash, code revision, model digest and answer-key hash.
- Leakage and cutoff tests on every snapshot, and a snapshot validator.
- **One known leak, decided before the run:** under no_provenance, an INC-2691 config id carries the
  fault deploy's commit sha. That cell runs as frozen but is excluded from no_provenance conclusions
  (`docs/KNOWN_DEFECTS.md` D15).
- Reports and reviewer worksheets.
- Two decisive-evidence scorers. **v1** is the frozen `eval/metrics.py`. **v2** is
  `scripts/score_v2.py`, which applies the same rule and also counts decisive evidence cited in the
  report. Under both, a decisive id counts only if the system retrieved that exact id or an id listed
  as its equivalent in the answer key; there is no substring or prefix matching. Re-scoring every run
  below with both versions changed no count.

**Integrations**

| Integration | Implemented | Verified end to end |
|---|---|---|
| Logs, traces, metrics (snapshot) | yes | yes: every run below |
| Deployments, builds, commits, configs, tickets (snapshot) | yes | backend tests only; **no investigation has queried commits or deployments** |
| Local application logs (live) | yes | yes: UI job 52d9fe read 15 live log records |
| GitHub commits/files pinned to a commit | yes | connector level only (real commit, diff and pinned file on blackbox-demo-shop @063397d); never chosen by the model |
| GitHub Actions | yes | 3 successful CI runs on blackbox-demo-shop read through the connector; failed-job and expired-log paths not verified on GitHub; never used in an investigation |
| Jira, Jenkins | no | not configured; fixtures are used |
| Shop UI and operator controls | yes | yes: screenshots below |
| BlackBox API and UI (jobs, live progress, history, cancel/retry) | yes | yes: INC-9001 and INC-9002 journey screenshots; cancel/retry verified without inference |
| Demo incident from a UI session | yes | yes: INC-9001, INC-9002 (development only) |

## 2. The demo journey (working interfaces)

All screenshots are of the running interfaces, not fixture mode. They are in
`demo/web/screenshots/verified/{pool-fastpath,retry-policy}/` (shop) and
`ui/blackbox-web/screenshots/verified/journey_INC-9001/` and `journey_INC-9002/` (BlackBox; `urls.txt`
in each lists the page URL of every screenshot). The full walkthrough with commands
is in `docs/DEMO_WALKTHROUGH.md`.
1. **Healthy checkout:** `01_reset_healthy_baseline`, `02_healthy_order`.
2. **Fault injected from the operator panel:** `03_fault_injected`.
3. **Failed checkout:** `04_order_during_fault`, `05_traffic_finished`.
4. **Incident created:** `06_demo_incident_created`; in BlackBox, `01_overview_incident_list` and
   `02_incident_timeline`.
5. **Investigation started:** `03_start_form_filled`, `04_job_submitted`.
6. **Live progress:** `05_job_running_rounds`.
7. **Evidence and source links:** `08_cited_record_and_source`.
8. **The actual conclusion, which here was an abstention:** `06_job_finished`, `07_final_report`.
9. **Operator reset and recovery:** `07_reset_again`, `08_healthy_after_reset`. This is the
   controller's action, not a BlackBox repair.
10. **Saved investigation reopened:** `09_results_history`, `10a_history_before_click`,
    `10b_reopened_after_click`.

## 3. Results

**Pilot (INC-0000, old snapshot, development).** The pilot snapshot was recaptured after the demo
history was re-authored. These runs are scored against the answer key in force when they ran.

| | BlackBox | B1 | B0 |
|---|---|---|---|
| Outcome | abstained (miss) | wrong diagnosis | wrong diagnosis |
| Decisive evidence retrieved | 0/4 | 0/4 | 0/4 |

B0's 5,792-token evidence budget held the newest 35 of 3,245 log records (about 165 tokens per record
on this capture). None of the 35 was decisive.

**Development before/after (INC-0000, 2 runs per arm, revision 4d2951e).** The two arms differ only in
a coverage note in the prompt.

| | Without the note | With the note |
|---|---|---|
| Tools used | metrics, traces | logs, metrics |
| Decisive retrieved / cited | 0/4 / 0/4 | 1/4 / 1/4 |
| Outcome | abstained, both runs | **wrong diagnosis** "payment-svc :: HTTP 500 errors", both runs, **accepted by gate v1** |

Each arm repeated its decisions across its two runs. That says nothing about determinism in general.
The note did not improve accuracy and produced a false diagnosis that passed the gate, so the frozen
evaluation configuration (`configs/frozen_v1.yaml`, code 4dd96c0) leaves it out.

**Formal evaluation eval_v1 (partial).**
- 3 of 47 planned outputs are complete: the s1 trio only. The scheduler is held by its health gate (GPU
  idle at 80–83 °C with software thermal slowdown active) and resumes automatically when the machine
  recovers.
- On s1 (INC-3172, diagnosable), BlackBox abstained with 0/4 decisive evidence. B1 and B0 both reached
  the 240 s model timeout and produced no report. All three count as misses: provisional top-1 is 0/1
  for each system.
- B0's prompt did contain 1 of the 4 decisive records.
- The eval code, config and the frozen snapshots are byte-identical to 4dd96c0 for every run.
- The timed-out cells will be rerun in a separate, labelled batch. The rerun is never merged into
  eval_v1's numbers.

**Live demo and development incidents (UI).**

| Run | Mode, incident | Rounds / calls | Outcome | Decisive retrieved / cited |
|---|---|---|---|---|
| 52d9fe | live, INC-0000 | 2 / 2 | "checkout-svc :: payment call timeout": a symptom label, accepted by gate v1 | 0/4 / 0/4 |
| a3febe | development snapshot, INC-9001 | 6 / 6 | abstained (miss) | 1/4 / 0/4 |
| b72b2e | development snapshot, INC-9002 | 6 / 6 | abstained (miss) | 1/4 / 1/4 |

The INC-9001 run was made with code a50ba25 + uncommitted changes, before the API's snapshot-root fix was
committed (d3a930c on live-integration).

**Tests.**
- `pytest -q`: 370 passed, 3 skipped (the opt-in live test and two tests waiting for a harness change),
  2 expected failures (open defects QA-F10 and QA-F11), 0 failed.
- A leakage test added afterwards for D15, run on its own: 11 passed, 1 expected failure (INC-2691).
- Integration QA (Agent 4): 120 passed, 2 expected failures. The UI builds with 12 lint warnings and
  has no UI unit tests.

## 4. What failed and why

**Change evidence was never retrieved.** Across the pilot, the before/after runs, eval s1 and INC-9001,
BlackBox never queried commits or deployments before concluding, and the same holds for INC-9002.
In INC-9001, 3 of the 4 decisive records were never requested, even though a call with no arguments to
`search_commits`, `list_deployments` or `get_metrics(service=payment-svc)` returns each of them first.
The fourth arrived as its listed equivalent (`log:3bf4f24e33`) in rounds 3, 5 and 6, but the model
never cited it. Nothing decisive was truncated.
- Under both scorers this is 1/4 decisive retrieved and 0/4 cited.
- In INC-9002 the model sent the same log search five times; four of those were cache repeats that
  used up budget.

In every case the failure was in where the model looked and what it cited, not in missing evidence.

**The coverage note did not help.** It moved the model toward logs, but the result was a wrong,
symptom-level diagnosis that gate v1 accepted, because both cited records were valid ids.

**Limitations of our measurements.**
- The diagnosis matcher is lexical. A valid citation is not proof that a claim is supported. No human
  review is recorded yet.
- On Windows, a refused connection can appear as a timeout. A "timeout" symptom therefore does not
  separate a slow dependency from an unreachable one.
- During eval s1 the GPU was thermally throttled: 96 °C, an SM clock of 307 of 2,100 MHz, and nearly
  full memory with swapping. We observed this alongside the two timeouts but do not claim it explains
  every slow or failed run. The scheduler now waits for GPU < 80 °C and free memory before each run.
- Open defects:
  - two in the local-log connector: QA-F10 (substring matching) and QA-F11 (unparseable timestamps);
  - a malformed timestamp in a tool call returns `error` rather than `invalid_args`;
  - log grouping can merge startup lines whose ports differ (D16).
  See `docs/KNOWN_DEFECTS.md`.

## 5. Changes made and their effect

Tested fixes, each backed by a test that now passes:
- citation ids padded or prefixed were recovered as real ids;
- a transport failure was reported as a diagnosis;
- malformed backend results and model JSON crashed runs;
- prompts could overflow the context;
- truncation flags were wrong;
- decisive coverage ignored listed equivalent ids;
- re-scoring after a snapshot rebuild silently used a new answer key.

We have not measured any effect on accuracy from the response schema, citation recovery, prompt v2 or
the call cache. The coverage note made things worse. Gate v2, which would add a causal-support check
(`docs/gate_v2_design.md`), is designed but not implemented.

## 6. Plan for the final two weeks

1. Finish all 47 eval_v1 outputs and the labelled rerun of the timed-out cells. Report every count with
   its denominator.
2. Two-reviewer scoring of the worksheets and the answer keys. Until that review is recorded, every
   verdict stays provisional.
3. As a separately labelled experiment: a coordinator rule that requires a deployment and a commit
   query before any diagnosis or abstention. This targets the failure seen in every run so far.
4. Refuse identical repeated calls instead of serving them from the cache, so the budget is not spent on
   repeats.
5. Implement and test gate v2 as its own experiment, never inside the frozen configuration.
6. Fix QA-F10, QA-F11 and the timestamp validation after eval_v1 is complete.
7. Run the live demo with GitHub evidence actually retrieved, or say plainly that it was not.
