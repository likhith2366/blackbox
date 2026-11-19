# INC-9001 failure analysis (live UI demo, development only)

Run `ui_jobs~20261120T034855-a3febe`, job `20261120T034855-a3febe`, submitted from the BlackBox UI on
2026-11-20 03:48:55Z. Snapshot mode, config `configs/dev.yaml`, code revision `a50ba25+dirty` (the
worktree with the snapshot-root fix that was committed afterwards). INC-9001 is a development incident
made through the shop UI (fault `pool-fastpath`, a connection leak, commit d142601, deploy d-576). It is
not part of the frozen evaluation.

## Outcome

- Final status: `insufficient_evidence`, headline "No reliable root cause identified." No ranked cause.
- 6 rounds, 6 tool calls (one of them a cached repeat that still counted against the budget), 141 s.
- The answer key marks the case diagnosable (`expected_status: diagnosis`). The abstention is
  therefore scored as a **diagnosis miss**, not as a correct abstention.
- BlackBox changed nothing in the application. Recovery was the operator's reset control.

## The gate that refused the diagnosis (exact rule)

Gate v1 (`blackbox/investigator/coordinator.py::_supports_diagnosis`, identical in the frozen revision
4dd96c0 and in the live worktree): the top hypothesis must cite **at least 2 retrieved evidence ids**,
and **at least 1** of them must come from a runtime source, where
`RUNTIME_SOURCES = {"logs", "traces", "metrics", "configs", "deployments"}`
(`blackbox/investigator/common.py`). It is not "logs or traces only". The gate checks evidence
requirements. It does not check whether the cause is true; the before/after experiment shows a wrong
diagnosis passing it.

In rounds 2–6 the model asked to diagnose with hypothesis h1, which cited one id (`metric:4d19b12fad`,
checkout-svc error_rate). The gate refused all five requests ("h1 cites 1 retrieved evidence id(s); at
least 2 are required"), and the run stopped at the round limit.

## Where each decisive record was (answer key `decisive_evidence_ids`)

| Decisive id | What it is | Reachable through an exposed tool? | Returned by a valid query? | What the run did |
|---|---|---|---|---|
| `commit:d142601` | the fault commit | yes, `search_commits` | yes: `search_commits()` with no arguments returns it first of 23 | **never requested** (0 commit calls) |
| `deploy:d-576` | deploy of the faulty build | yes, `list_deployments` | yes: `list_deployments()` returns 3 records including it | **never requested** (0 deployment calls) |
| `metric:03d4f590b8` | payment-svc `db_pool_in_use` rising 0 → 4 after the deploy | yes, `get_metrics` | yes: `get_metrics(service="payment-svc")` over the window returns it first | **never requested**: the only metrics call was checkout-svc error_rate |
| `log:4f9f142c0b` | payment-svc "db pool acquire timed out after 1000ms" | yes, `search_logs` | yes | documented equivalent `log:3bf4f24e33` (the grouped form of the same line, x308) **returned in rounds 3, 5 and 6, seen, and not cited** |

Nothing decisive was truncated. The log searches returned total_matches 1 with truncated false. The one
truncated result, round 4 `search_traces` (308 matches, 8 returned), contained no decisive id. Its first
trace (84bc7633…) is the trace of the decisive log line, but traces are not in the key.

## Coverage

- An ad hoc exact-id check (no equivalents), used in my first status message, gave 0/4. That figure was wrong.
- Scoring v1 (`eval/metrics.py`, which already reads `decisive_equivalents` since fix F08) and scoring v2
  (`scripts/score_v2.py`) agree: decisive evidence retrieved **1/4** (`log:3bf4f24e33`, a listed
  equivalent of `log:4f9f142c0b`), cited in the final report **0/4**. See `results/ui_demo_dev/scores_v2.md`.

## Classification

The failure was **not** caused by missing or hidden evidence, an unavailable connector, or truncation.
Two things went wrong:

1. **Search scope.** All three missing decisive records were one obvious, valid call away, through tools
   in the model's catalog. The model spent its budget on the symptom service's errors and payment-svc
   error logs (three near-identical log calls, one an exact repeat) and never looked at what changed
   (deployments, commits) or at payment-svc resource metrics. Pilot, the development before/after runs
   and eval s1 (INC-3172) show the same thing: no run has yet queried commits or deployments before
   concluding.
2. **Citation.** The pool-timeout log that points at the mechanism was retrieved and visible to the
   model, but it never added it to h1's supporting ids. With that one citation, h1 would have passed
   gate v1 as a symptom-level diagnosis ("payment-svc timeouts"). That would still have missed the
   cause, which is the commit that leaks connections.

The model's own "next check" ("payment-svc database pool configuration and connection metrics") named the
right area, but it was output after the budget ran out and had no evidence behind it.

## Same pattern on INC-9002 (second demo incident, development only)

Run `ui_jobs~20261120T035254-b72b2e`, job `20261120T035254-b72b2e` (fault `retry-policy`, commit
0d6ba14, deploy d-581). Final status `insufficient_evidence`, no ranked cause; scored as a diagnosis miss.

- Round 1 was `get_metrics` on checkout-svc error_rate. Rounds 2–6 sent the **identical**
  `search_logs(service=checkout-svc, level=ERROR, limit=15)` call five times. Four of those were cache
  repeats, and each still used up one tool call and one round of the budget.
- The gate refused four diagnose requests, each because h1 cited only 1 retrieved id.
- Decisive coverage: 1/4. The log `log:7db2506518` was retrieved and cited. The commit, the deploy and the
  checkout `upstream_attempts_per_s` metric were never requested.

So both UI demo incidents failed the same way as the pilot, the before/after runs and eval s1: BlackBox
never queried commits or deployments, and the change that caused the incident stayed out of view.
