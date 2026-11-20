# QA findings (QA)

Independent integration QA. Every finding has a test in `tests/integration/`. Open defects are
`xfail(strict=True)`: when the owner fixes one, the test XPASSes, the suite fails, and QA
removes the marker. Nothing here comes from model inference. All checks used test doubles,
labelled fixtures, code reading, or the GitHub connector smoke test.

Update 2026-11-20: Integration fixed F01b, F02, F03, F04, F05 and F07 (commit 9335b82). Their xfail markers were removed, so they are now regression tests. Still open: F08 (Evaluation) and F09 (Integration); F06, N1 and N2 await decisions.

Status legend: OPEN (xfail test present), FIXED (now a regression test), NOTE (no defect; needs
a decision or human review).

| id | sev | owner | status | test |
|---|---|---|---|---|
| QA-F01 | high | Integration | FIXED 2026-11-20 | `test_qa_investigator.py::test_padded_or_extended_id_is_not_recovered` |
| QA-F01b | medium | Integration | FIXED 9335b82 | `test_qa_investigator.py::test_two_ids_where_one_prefixes_the_other_are_ambiguous` |
| QA-F02 | medium | Integration | FIXED 9335b82 | `test_qa_investigator.py::test_transport_error_mid_run_is_not_a_completed_diagnosis` |
| QA-F08 | medium | Evaluation | FIXED (eval/metrics.py) | `test_qa_eval_contract.py::test_decisive_coverage_uses_recorded_equivalents` |
| QA-F03 | low-med | Integration | FIXED 9335b82 | `test_qa_investigator.py::test_malformed_backend_result_is_an_error_observation` (3 cases) |
| QA-F04 | low | Integration | FIXED 9335b82 | `test_qa_investigator.py::test_wrongly_typed_model_json_does_not_crash` (3), `::test_non_object_args_are_invalid_not_rewritten` |
| QA-F05 | low | Integration | FIXED 9335b82 | `test_qa_investigator.py::test_prompt_never_exceeds_window_even_with_large_fixed_part` |
| QA-F07 | low | Integration (backend, transferred from Evidence) | FIXED 9335b82 | `test_qa_backend_contract.py::test_search_logs_truncated_only_when_limit_cut_results` |
| QA-F09 | low | Integration | OPEN | `test_qa_cli.py::test_b1_with_ablation_is_refused`, `::test_config_typo_is_rejected`, `::test_user_errors_give_clean_exit` (4) |
| QA-F06 | note | Integration | NOTE | `test_qa_investigator.py::test_run_timeout_is_checked_between_rounds_only` |
| QA-N1..N5 | note | various | NOTE | see below |

---

## QA-F01 (high, FIXED): padded or fabricated citation ids were recovered as real ones
`resolve_citation` "embedded" rule used a plain substring test, so `log:12` resolved to `log:1`
and `log:9f2c41ff` to `log:9f2c41`. A fabricated id became a valid citation, which is exactly
the padding behaviour CONTRACT s1 reports for qwen3:4b. Integration fixed it with an id-boundary
regex. The test is now a regression test.

## QA-F01b (medium, FIXED at 9335b82): two ids where one is a prefix of the other resolve to the longer one
`resolve_citation("log:a1, log:a12", {"log:a1","log:a12"})` returns `("log:a12","embedded")`.
The text names two distinct retrieved ids, so it must be refused as ambiguous. The fixed
boundary check finds both ids, but the `distinct` filter still drops `log:a1` because it is a
substring of `log:a12`. Proposed patch (keeps `jira:DEMO-12#c3` resolving, guarded by
`test_comment_id_embedded_in_text_still_resolves`):
```python
def _standalone(i, text):
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(i) + r"(?![A-Za-z0-9_])", text) is not None
inside = [i for i in seen if _standalone(i, c)]
# drop i only when every standalone occurrence of i lies inside an occurrence of a longer id j
distinct = [i for i in inside
            if not any(i != j and i in j and not _standalone(i, c.replace(j, " ")) for j in inside)]
```

## QA-F02 (medium, FIXED at 9335b82): model server dies mid-run, run reported as a completed diagnosis
Script: two good rounds whose ledger cites `log:qa1` and `metric:qa1`, then `ModelError` for
the round and for the report call. `_final_status` sees that the top hypothesis passes the
diagnosis gate and returns `diagnosis`. `_write_report` falls back to `_ledger_report`. Because
`self.steps` is non-empty, `out_status` is `completed` (the `not self.steps` branch is never
true; a failed step is always appended before the break). eval's `score_run` then counts it as
a usable report and can score a top-1 hit for a diagnosis the model never made.
Proposed patch: in `run()`, remember that the stop came from a transport `ModelError`. For that
stop, use `status="insufficient_evidence"` for the report (or keep the ledger report but set
`SystemOutput.status="failed"`, with `error` saying it was derived from the ledger). Whichever
you choose, record it in CONTRACT 10b. The test accepts either.

## QA-F03 (low-medium, FIXED at 9335b82): non-BackendResult from a backend crashes the run
In `ToolRunner.run`, `result.status` and the `ToolResult(...)` construction sit outside the
`try`. A backend returning `None`, a dict, or a BackendResult with invalid records (built with
`model_construct`) raises out of the coordinator, and the harness records `exception`.
Patch: inside the `try` (or a second `try`), check `isinstance(result, BackendResult)` and wrap
`done(...)`. On failure return `done("error", error="backend returned an invalid result: ...")`.

## QA-F04 (low, FIXED at 9335b82): wrongly typed model JSON crashes the coordinator or rewrites the call
The grammar normally prevents these. If it is bypassed (server version, retry path, other
model), the following still parse:
- `hypotheses: ["text"]` raises AttributeError in `_normalize_ledger` (`h.get`)
- `tool_calls: "search_logs"` or `["search_logs"]` raises AttributeError (`(tc or {}).get`)
- `{"tool": "search_logs", "args": "payment-svc"}` executes with `{}`, a call the model never asked for

Patch: skip non-dict hypotheses and record them in `parse_error`. Give a non-dict tool-call
entry status `invalid_args`, counted like other invalid calls. Give non-dict `args` status
`invalid_args` instead of replacing it with `{}`. Do the same type checks in `build_report`
for `ranked_causes` and `claims`.

## QA-F05 (low, FIXED at 9335b82): prompt can exceed `num_ctx - num_predict`
`_round_message` and `_write_report` use `self._notebook(max(budget, 400))`. When the fixed
part (incident notes, ledger, coordinator notes) leaves less than 400 tokens, the notebook
still gets 400 and the prompt overflows. Nothing records this before the call (only
`prompt_truncated_by_server` afterwards, from the server's count). Test: 22k chars of incident
notes with `num_ctx=4096`. Not reachable with current snapshots (short notes), so low.
Patch: if `budget < 400`, render ids-only one-liners or omit the notebook, add an
orchestration rule `context: fixed part X tokens leaves Y for evidence`, and never send a
prompt whose estimate exceeds the window.
Related: the retry prompt (`YOUR PREVIOUS OUTPUT WAS REJECTED ...`) adds about 30 tokens that
`prompt_tokens_est` does not include.

## QA-F06 (note): `run_timeout_s` is soft
It is checked only at the start of each round. The round in flight (up to 2 x
`model_timeout_s` with the retry) and the report call (up to 2 x) still run, so with defaults
a run can last about 1200 + 960 s. The harness records `exceeded_run_timeout`. Decide whether
this is acceptable and document it, or cap each model call's timeout at the remaining run time.

## QA-F07 (low, FIXED at 9335b82; Integration also made query tokens match whole words): `search_logs` says truncated when lines were only grouped
`total_matches` counts matched lines while `records` holds one representative per template.
As a result `truncated = total > len(records)` is true whenever two lines share a template,
even if every template fits under `limit`. The coordinator then tells the model
"N records of M (truncated)", which invites extra calls. Patch: `total_matches=len(groups)`,
and report line counts in attributes (`similar_lines` already exists).

## QA-F08 (medium, FIXED by Evaluation): decisive coverage ignores the recorded equivalents
`scenarios/build.py` stores `injected_fault.decisive_equivalents` (for example, the other
identical log lines and error traces of the same decisive event). The backend returns one
representative per log template, and that representative depends on the query window and
score (`test_log_grouping_representative_depends_on_query_window`). `search_traces` returns
the first N traces. `eval/metrics.py::score_run` and `calls_to_first_decisive` match exact ids
only, so retrieving an equivalent line counts as a miss. Decisive coverage is then biased
downward, and differently for each system (B0 sees raw lines, BlackBox sees grouped ones).
Patch:
```python
eq = (key.injected_fault or {}).get("decisive_equivalents", {}) if key else {}
def _hit(d, got): return d in got or any(e in got for e in eq.get(d, []))
hit = [d for d in decisive if _hit(d, got)]
# and in calls_to_first_decisive: any(_hit(d, set(c.evidence_ids)) for d in decisive)
```

## QA-F09 (low, OPEN): CLI usability
- `--system b1 --ablation no_jira` is accepted. B1 then gets the ablation-none evidence
  (tickets) inside an ablated run. Refuse unless the ablation is `none`.
- Unknown YAML keys are ignored (`RunConfig` allows extras): `num_ctxx: 4096` silently runs
  with 8192. Patch: in `load_config`, reject keys not in `RunConfig.model_fields`.
- A missing incident, bad YAML, a missing `--b1-from` file or a missing `show` file each end in
  a traceback. Patch: catch `FileNotFoundError`, `ValueError` and `yaml.YAMLError` around those
  steps and call `p.error(...)`.

## QA-F10 (low, OPEN, Evidence): local_logs query tokens match substrings
`blackbox/connectors/local_logs.py` scores `tok in line.lower()`, so the query "out" matches every
`checkout-svc` line. Integration changed the snapshot backend to whole-word matching. Patch: use the same
`re.search(r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z0-9])", low)` rule.
Test: `test_qa_local_logs.py::test_query_matches_whole_words_like_the_snapshot_backend`.

## QA-F11 (low, OPEN, Evidence): one bad timestamp fails the whole local log search
`_read` calls `_ts(rec.get("ts"))` outside any per-line guard. A single line with an unparseable `ts`
raises ValueError, and `search` returns `status=error` for the whole file. Patch: wrap the per-line
`_ts` call in try/except and skip that line.
Test: `test_qa_local_logs.py::test_one_bad_timestamp_does_not_fail_the_search`.

## Notes (no defect proven)
- **QA-N7 (Evidence / Integration).** local_logs evidence ids hash the absolute file path plus the raw line
  index (blank lines included), so the same log read from another directory gets different ids, and
  they never equal the snapshot ids (`h(incident, "log", service, parsed_index)`). CONTRACT 10c says
  local `log:` ids "keep the snapshot hashing scheme". Decide which is intended.
  Characterisation test: `test_qa_local_logs.py::test_ids_depend_on_absolute_path`.
- **QA-N8 (Evidence / Evaluation).** `tests/test_connectors_contract.py` skips the 3 local_logs contract tests
  because discovery needs `make_connector()` or a class ending in Connector/Backend/Client, and the class is
  `LocalLogs`. One-line fix in Evidence's file: `make_connector = LocalLogs` (or a `LocalLogsConnector`
  alias). Until then, QA's `tests/integration/test_qa_local_logs.py` covers it.
- **QA-N1 (Evaluation).** `FAILED_TOOL_STATUSES` and `REFUSED_TOOL_STATUSES` do not include the new
  `unavailable` ToolStatus, so a call to a withheld or ablated source counts as executed but
  neither failed nor refused. Decide whether it counts as failed. Repeated `unavailable` calls
  are not cached by the coordinator, so they also count as redundant once identical.
- **QA-N2 (Evaluation).** The harness passes B1 the `evidence_seen` of the BlackBox r0 run
  whatever its status. A failed BlackBox run with zero evidence gives a B1 run with
  "(no records)" and `matched=True`. Consider recording `b1_source_status` in meta and
  excluding such pairs from same-evidence claims.
- **QA-N3 (Evidence, resolved).** The pilot capture was read while still running. Evidence
  confirmed it is complete and that `run_capture` now resets `telemetry_dir` to the live
  directory.
- **QA-N4 (human review).** Pilot INC-0000: the DEMO-9 ticket comment ("Retrying timeouts
  against a slow dependency multiplies its load") nearly states the answer. On the payment side
  the dominant error is `OperationalError: database is locked`. Evidence has since added the
  lock/UNIQUE errors to the answer key mechanism and accepted wordings. Whether the key is now
  fair is a human-review item, not decided here.
- **QA-N5 (Integration).** The tool catalog always lists ticket and build tools, even when they
  are ablated. The incident's `Sources:` line marks them unavailable and calls return
  `unavailable`. That is consistent with CONTRACT s8 (no content leak), but expect such calls
  under ablation.

- **QA-N6 (Evaluation, likely a false positive).** `tests/test_leakage_ablation.py::test_no_provenance_leak`
  fails on INC-2691 and INC-6053 because `get_config` returns `config:checkout-svc@<sha7>`, where `<sha7>` is
  also the deployed commit. I inspected the no_provenance response. It names the latest repository commit
  that touched the config file and says "latest repository version at the cutoff (deployed version unknown)".
  The same sha is already returned by `search_commits(path="config/checkout-svc.yaml")` (`commit:1ac1cc2`),
  and CONTRACT s8 keeps commits searchable. So the record exposes repository history, not a
  deployment->commit mapping, and it will recur on any scenario whose fault is a config commit, rebuilt or
  not. Suggest the test allow a sha in a `configs` record when the same sha is visible as a commit record and
  the record does not claim to be the deployed version. This is a judgement on the ablation's intent:
  Integration (CONTRACT owner) should confirm.

## Pilot review: runs/pilot_v1 (INC-0000), independent of Integration
Read-only inspection of the three saved outputs, the snapshot and the answer key. Viewer:
`python -m viewer.build --runs runs/pilot_v1 --out viewer/out/pilot_v1`.

Automated checks (done):
- Leakage: no fault id, change id, family label or evaluator term in any snapshot file or any
  record the three systems saw (only `ALR-3008`, the alert id, appears). Every evidence record
  has `available_time` at or before the cutoff `2026-11-20T02:05:42.727Z`.
- Every cited id in all three reports exists in that system's supplied evidence (BlackBox
  `evidence_seen`, B0/B1 `included_ids`). The BlackBox report removed one invalid citation,
  `h1`: the model cited a hypothesis id as evidence.
- Context: on all 6 BlackBox rounds `prompt_tokens_est` was exactly Ollama's
  `prompt_eval_count` + 5. The fixed 20-token chat-template allowance overestimates by 5.
- Decisive coverage is 0 of 4 for all three systems, including with the recorded equivalents.
  BlackBox never queried logs, deployments or commits. B0 kept the newest 35 of 3245 log
  lines (3210 omitted), which contain no decisive line. B1 matched (11 of 11 records).

Behaviour observations (for Integration, not code defects):
- BlackBox asked to diagnose in rounds 2 to 6 and was refused each time by the gate ("h1
  cites 1 retrieved evidence id"). It never added the traces it had retrieved to the ledger,
  although its report cites them. Rounds 4, 5 and 6 repeated round 3's call exactly, and all
  three were cache hits, so half the tool budget went to repeats. The duplicate-call feedback
  did not change its behaviour. Final status: `insufficient_evidence` after `stop_rounds`.
- Backend semantics (Integration, backend owner): `search_traces(status="error")` returns traces
  whose root request succeeded, because trace status is "error" when any span errored. For
  example `trace:a1bc8a0d...` has root `POST /orders` with HTTP 201 after a retried 250 ms
  timeout. A model reading "status=error" may count it as a failed order. Consider adding the
  root status (`root_status=ok`, `http.status_code`) to the trace summary line.

Human review still required (not decided here):
- BlackBox claim c2 "POST /orders requests are failing with timeouts" cites 4 traces. At least
  one of them (`trace:a1bc8a0d...`) is an order that succeeded after retries, so check support
  per trace.
- B1 cause 1 says the cause is payment-svc with "ConnectionAbortedError [WinError 10053]". The
  cited spans contain that string verbatim, but it is the server-side effect of checkout
  abandoning the call at 250 ms. The text supports the literal claim, not the causal one.
  B1 cause 2, "HTTP 504 Gateway Timeout", is literally present in the cited traces.
- B0 cause 1 "payment-svc database lock contention" and cause 2 "checkout-svc retrying payment
  service with HTTP 500" both cite real log lines. Whether either matches the answer key
  (retry amplification in the shared client), given the key's newly accepted wording "retries
  overload payment-svc database causing lock contention", is a scoring decision for the human
  reviewer. Cause 3 duplicates cause 1.
- The DEMO-9 ticket comment that nearly states the answer (QA-N4).

## Verified (automated, passing)
- Backend (`blackbox/evidence/backend.py`) matches `interface.py`: every method name,
  parameter and default, and `load_backend`/`list_incidents` per CONTRACT s4. Every tool
  argument is a backend parameter. `isinstance(backend, EvidenceBackend)` holds.
- Cutoff: no post-cutoff log, span, metric point, ticket comment, description or status
  appears in any method or in `export_logs`. Every record has `available_time <= cutoff`.
- Ablations on the fixture snapshot: `no_jira` removes tickets and ticket keys from commits
  and logs. `no_provenance` removes build/commit/digest/version from deployments and startup
  logs, and builds become unavailable while commits stay searchable. `no_traces` removes
  spans and keeps logs.
- Backend store cache is keyed by snapshot AND ablation (and file stamps). Returned records
  are copies. Connector-failure counters live on each backend instance, so each run starts
  healthy.
- Coordinator cache is per run. Failed calls are not cached.
- Budgets: cached repeats consume budget; denied calls never execute, take a round slot and
  consume no budget; invalid-argument calls consume budget; malformed rounds neither extend
  nor reset limits; an adversarial script never exceeds 12 calls, 3 per round, 6 rounds.
- Robustness: slow connector gives `timeout`; a raising connector, a missing method and a
  connector failing mid-run give `error`; a first-round transport error gives a failed run.
- Citations: raw text kept in `citation_audit`; ambiguous and invalid citations never resolve;
  every non-exact recovery is in `normalized_citations`; same-round pre-citation is refused.
- Context: `prompt_tokens_est` equals the tokenizer count of the exact system and user
  messages plus 20, and is within 25 tokens of the Qwen3 chat template applied to the exact
  messages (`enable_thinking=False`). Estimate + `num_predict` stays within `num_ctx` with
  large evidence, and the notebook degrades to one-line and omitted records at `num_ctx=4096`.
- CLI: `configs/dev.yaml` loads as the contract limits; a missing or wrong `--b1-from`
  (system, incident or ablation) gives exit code 2.
