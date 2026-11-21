# Step 1 diagnosis: why v2 retrieves the decisive change but still does not diagnose

**Scope.** This step reads the code, configurations, connectors and the saved v2 traces in
`runs/dev_v2_experiment/` (development incidents only). It changes no behaviour and does not touch
the frozen eval_v1 artifacts. Repository state: master `6423c5d`.

**Method.**
- `scripts/replay_prompts.py` rebuilds the exact prompt of every round. It replays each run's recorded
  model outputs through the same coordinator, config and snapshot, without calling the model.
- For all three v2 runs the replayed tool results match the saved ones exactly.
- Statistics over all 43 saved BlackBox runs (eval_v1, development and UI) were computed from their
  output.json files.

## Evidence path, per development incident (v2 run)

| | INC-0000 (retry amplification) | INC-9001 (connection leak) | INC-9002 (retry amplification) |
|---|---|---|---|
| Faulty change in the source data | deploy d-535 checkout-svc 1.9.0 at 02:38:26, commit e7a0cc0 at 02:38:24 | deploy d-576 payment-svc 2.5.0 at 03:44:06, commit d142601 at 03:44:03 | deploy d-581 checkout-svc 1.9.0 at 03:46:24, commit 0d6ba14 at 03:46:22 |
| Backend linkage (deploy → build → commit; service; time) | correct | correct | correct |
| Change tools called (round) | list_deployments, search_commits (R3) | same (R3) | same (R3) |
| In the prompt from round 4 | full deploy and commit records, nothing omitted | full records, 2,772 of 5,902 notebook tokens used, 0 omitted | full records, nothing omitted |
| Decisive records retrieved / cited in the report | 3/4 / 3 | 2/4 / 2 | 3/4 / 1 |
| Decisive records never retrieved | log line | `metric:03d4f590b8` (db_pool_in_use): the model asked for payment-svc metrics by guessed names (error_rate, latency_p50, requests_per_s), never the full list | log line |
| Rationale names the change? | R4: "latest deployment of checkout-svc (v1.9.0) includes a c[ommit] …" | R4/R6: "payment-svc deployment (v=2.5.0) introduced a fast path … which may have caused …" | R5/R6: "deployment of checkout-svc (v=1.9.0) includes a commit (0d6ba14) that adds retry logic … could be causing more a[ttempts]" |
| Hypotheses after the change was retrieved | unchanged R3–R6: checkout-svc / "payment-svc timeout", one citation | unchanged R3–R6: checkout-svc / "rate limiting", "payment-svc timeout", "payment-svc overload", one citation each | unchanged R3–R6: checkout-svc / "payment-svc timeout / overload / connection issues", all citing metric:858412a4f1 |
| Diagnose requests refused | 1 by the change check (R2), then 3 by gate v1 (1 citation) | 1 by the change check, then 2 by gate v1 | 1 by the change check, then 3 by gate v1 |
| Final report | insufficient_evidence; claims cite d-535 and e7a0cc0 as facts, with no causal link | insufficient_evidence; claim "payment-svc received a commit … fast path" | insufficient_evidence; claims cite only the metric |

## Sources: working vs actually used (development incidents)

| Source | Connector works? (checked directly) | Called by the model in v2 runs |
|---|---|---|
| metrics, logs, traces | yes | yes |
| deployments, commits | yes, linked by service, time and SHA | yes, only because the change check forced it |
| builds (`get_build`) | yes: b-1229 → full SHA d142601…, b-1230 → 0d6ba14… | never |
| commit diff (`get_commit`) | yes, returns message and diff | never |
| configs (`get_config`) | yes: the config at the deployed commit, labelled "config of the running deployment d-576" | never |
| tickets | yes (DEMO-41, DEMO-9 match the changes) | never |

No connector or data-contract bug was found that explains these failures, so this step makes no behaviour change.

## Top three causes, ranked by evidence

1. **The hypothesis list is frozen: the model reasons in the rationale and never updates the ledger.**
   - 117 of 163 round-to-round transitions across all 43 runs leave the hypotheses identical.
   - In 10 rounds the rationale discusses a deploy or commit; in 0 of them does any hypothesis mention a change.
   - Gate v1 reads the ledger, not the rationale, so every diagnose request fails with "cites 1 retrieved evidence id".
   - Likely contributors:
     - The schema puts `hypotheses` first and `rationale` last, so the ledger is written before the reasoning.
     - The prompt shows "HYPOTHESES SO FAR".
     - Rule 7 says "keep every hypothesis in the list each round".
   - The field-order effect is a hypothesis and has not been tested.
2. **Symptom anchoring on the alerting service.**
   - 34 of 36 distinct hypotheses use the alerting service as the component, including on INC-9001, where the cause is in payment-svc.
   - Mechanisms describe symptoms ("payment-svc timeout", "rate limiting"), not changes.
   - The prompt's rule 4 ("the most recent change is not automatically the cause") is the only guidance about changes, and it discourages them.
3. **The mechanism text needs the diff or a resource metric, and neither reached the model.**
   - INC-9001's commit message describes a performance fast path, not a connection leak. The leak is visible only in the diff (`get_commit`) or in `db_pool_in_use`.
   - The model never called `get_commit` and asked for payment metrics by guessed names.
   - A latent presentation limit also exists: `render_record` cuts content at 600 characters, so if `get_commit` were called, the decisive code hunk would be cut in all three incidents. The diff starts at character 485–558 and the visible text ends at the file header with "...[cut]".
   - This limit has not been exercised in any run yet.

**Ruled out for these runs:**
- Notebook truncation: 0 records omitted.
- Server-side prompt truncation: false in every round.
- Wrong deploy/commit linkage.
- Missing timestamps.
- Unavailable connectors.

One tool-argument error was seen: INC-0000 asked for `upstream_attempts_per_s` on payment-svc, a metric that exists only on checkout-svc, and got an empty result.

## Recommended next intervention (one, smallest)

**Generate the rationale before the hypotheses.** This targets cause 1 directly.

**What changes.** A config flag `rationale_first` (off by default, so the frozen behaviour is unchanged). When on, the round schema lists `rationale` before `hypotheses`, and the prompt's closing line asks the model to state, as part of its reasoning, which retrieved change, if any, matches the symptom onset.

**Files.**
- `blackbox/schemas.py`: the flag.
- `blackbox/investigator/common.py`: schema variant with the field order changed.
- `blackbox/investigator/coordinator.py`: choose the schema and closing line from the flag.
- `tests/test_investigator_core.py`: schema order and default-off tests.
- `configs/dev_v2b.yaml`: dev_v2 plus the flag.

**Measurement.**
- Runs: v2 vs v2b on the same three development incidents, 2 runs each (the model is not fully deterministic). No frozen cases.
- Primary metric: the share of rounds after change retrieval in which the hypotheses change and cite a `deploy:` or `commit:` id.
- Secondary metrics: gate passes, and agreement between the rationale and the ledger.
- Correct diagnosis: reported, but not expected to move with n = 6.

## Uncertainty

- There are three development incidents with one v2 run each; two of them share a fault family.
- The causes are ranked from observed traces, not from controlled interventions.
- The replay reconstructs prompts exactly only because the tool results match. The system prompt and schema were taken from the current code, which equals the code that ran (commit 8c5b6d5).

**Tests:** full `pytest -q`: 389 passed, 3 skipped, 1 xfailed, 0 failed.
