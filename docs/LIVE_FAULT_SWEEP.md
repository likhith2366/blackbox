# Live fault sweeps (development only)

Each demo fault was injected into the running demo. The incident was opened from a live Prometheus alert, and
BlackBox investigated it with live sources only. All outputs are kept unedited; the ground truth for each fault is
in `truth.json`, which the model never sees.
- **Script:** `scripts/live_fault_sweep.py`
- **Scorer:** `scripts/score_live_sweep.py`. Its keyword scoring is provisional; read the reports.

| Fault (family) | v1 settings (`runs/live_faults_v1`) | v2 settings (`runs/live_faults_v2`) | v2 + local git code (`runs/live_faults_v2_localgit`) |
|---|---|---|---|
| retry-policy (retry amplification, code) | insufficient evidence | insufficient evidence | insufficient evidence; fault commit listed, never opened |
| index-migration (dropped index, code) | **wrong**: "checkout-svc: error rate due to payment-svc timeout" | insufficient evidence | no alert within 4 min |
| endpoint-port (wrong port, config) | insufficient evidence | **wrong**: "payment-svc timeout" | **wrong**: "payment-svc dependency failure"; fault commit listed, never opened |
| payment-stop (stopped dependency, operator) | insufficient evidence | insufficient evidence | insufficient evidence |
| pool-override (pool size override, operator) | no alert within 4 min | no alert | no alert |
| pool-fastpath (connection leak, code) | insufficient evidence | **wrong**: "payment-svc dependency failure" | **wrong**: same |

**Totals.** Correct component and mechanism: **0 of 13 investigations**. 4 were wrong diagnoses and 9 abstained.
Five runs (pool-override in all three sweeps, index-migration once) had no alert and no investigation.

**What changed between settings:**
- **v1** used `get_metrics` once, then repeated the same `search_traces` call about 5 times. It never looked at
  logs, deployments or code.
- **v2** adds a change check and refuses repeated calls. It queried deployments and commits every time and
  retrieved the faulty deployment in 4 of 5 runs, but cited it 0 times.
- **v2 + local git** reads the code from the company repository (explicit `BLACKBOX_GIT_REPO_DIR`, read-only),
  because the fault commits exist only locally and GitHub answers 404 for them.
  - The fault commit appeared in the commit list for 2 of 4 runs.
  - The model never called `get_commit`, so it never saw a diff.
  - Its hypotheses stayed symptom labels ("payment-svc timeout") from round 2 onward.

**Why it fails (consistent with Steps 1–6):**
- The model does not plan the step from "a change was deployed" to "read that change".
- It does not revise its hypothesis list once it has written one.
- When the evidence is placed in front of it (Steps 4–6), it names the right change but still not the mechanism.

Infrastructure is not the bottleneck: every source answered live, and the decisive records were reachable.

## dev_v3: change expansion + from-scratch synthesis (`configs/dev_v3.yaml`, local git, `runs/live_faults_v3_localgit`)

These are two development options in `blackbox/investigator/synthesis.py` and the coordinator, off by default.
- **Change expansion:** the coordinator follows each deployment in the incident window to its build, commit diff
  and service metrics.
- **Synthesis:** at the end, the model rewrites its hypotheses from scratch over all the evidence. The result must
  still pass the evidence gate.

| Fault | Result | Top cause (shortened) | Faulty change cited | Provisional verdict |
|---|---|---|---|---|
| retry-policy | diagnosis | checkout-svc 1.9.0 "causes payment failures to be retried too aggressively" | commit cc699b8 (the injected commit) + build | **likely correct**: right component and change, retry mechanism; it does not mention the 250 ms timeout or call multiplication |
| endpoint-port | diagnosis | checkout-svc "bug in the payment routing logic" | commit e8d58bd ("route checkout … through the new sidecar port"); also a wrong deploy (d-655) | **partially correct**: right change and area, no "wrong port / nothing listening" |
| pool-fastpath | diagnosis | "new payment-svc deployment introduced a bug in the fast path for micro-charges" (component field: checkout-svc) | commit d24f5c0 (the injected commit) | **partially correct**: right change, no connection leak, component field names the caller |
| payment-stop | diagnosis | "timeout in the payment service's database pool" | (no change exists for an operator stop) | **wrong** |
| index-migration | no alert within 4 min | – | – | not investigated |

**Compared with v1, v2 and v2 + local git (0 of 13 found):**
- In 3 of 4 investigated faults, the investigation now reaches and cites the injected change, and one diagnosis
  states the mechanism in substance.
- The remaining failure modes:
  - an operator fault with no code change (payment-stop) gets a confident wrong answer that the gate does not stop;
  - mechanisms stay shallow ("routing bug", "fast path bug") rather than "nothing listens on that port" or
    "connections not released";
  - deployments left over from earlier faults can be cited (d-655).
- **Status:** provisional, scored by me. A second reviewer should confirm before any of this is reported as
  accuracy. This is n = 4 development faults, not an evaluation.

## dev_v3c: dev_v3 + the causal-evidence gate (`configs/dev_v3c.yaml`, local git, `runs/live_faults_v3c_localgit`)

Fixes found and made while getting this run clean (earlier attempts are kept in `runs/live_faults_v3c_superseded/`):
- **D18** (first written as D15): a commit fetched with its diff could not replace the diff-less copy from the commit list (coordinator).
- **Local git:** commits now carry the services they touch, taken from their paths.
- **Causal gate onset:** onset is now the start of the latest error episode.
- **Sweep:** the demo settles longer than the 5-minute incident lookback, so the previous fault is outside the window.

| Fault | Result | Top cause | Cited the injected change | Provisional verdict |
|---|---|---|---|---|
| retry-policy | diagnosis | "checkout-svc's retry policy for payment failures was changed to retry more aggressively, causing cascading retries and timeouts" | commit 3ae7e95 + deploy d-689 | **correct** (component + mechanism) |
| endpoint-port | diagnosis | "checkout-svc misconfigured to use a non-existent payment-svc port, causing payment requests to fail" | commit b40d5ee + deploy d-692 | **correct** |
| payment-stop | insufficient evidence | – | (no change exists) | abstained; the earlier wrong diagnosis is now blocked |
| pool-fastpath | insufficient evidence | (gate refused: top hypothesis named checkout-svc but cited a payment-svc commit) | listed, not cited by the accepted cause | abstained; the model's second hypothesis was "payment-svc's new fast path … causes database pool exhaustion" |

**Totals:** 2 correct, 0 wrong, 2 abstained, out of 4 investigated faults. Before these options, the totals were
0 correct out of 13. This is still development-only (n = 4, one run each, scored by me) and needs a second reviewer.

## dev_v3d + in-window change rule (`configs/dev_v3d.yaml`, `runs/live_faults_v3e_localgit`)

dev_v3d adds gate-checked ranking: the first synthesized hypothesis that the gate accepts is reported.

The earlier dev_v3d sweep (`runs/live_faults_v3d_localgit`) exposed a gap. The gate accepted "rate-limiting bug in
checkout-svc" on the strength of checkout deployments from an hour earlier, left over from previous faults. The
causal gate now requires a change-based claim to cite a change inside the incident window (`007eda1`). The sweep
also checks traffic before injecting: in the earlier run a stalled load generator left retry-policy without an
alert.

| Fault | Result | Top cause | Provisional verdict |
|---|---|---|---|
| retry-policy | diagnosis | "checkout-svc 1.9.0 … bug in the HTTP client retry policy that causes excessive retries on payment-svc" | **correct** |
| endpoint-port | diagnosis | "checkout-svc misconfigured payment base_url causing payment-svc to fail" | **correct** (names the field, not the port) |
| pool-fastpath | diagnosis (rank 2 accepted) | "payment-svc v2.5.0 has a bug in the database connection pool that causes high latency for small charges" (cites commit 3cc944e + build) | **partially correct**: right component and change, mechanism stops short of "connections not released" |
| payment-stop | insufficient evidence | – | abstained (no wrong answer) |

**Across the clean runs with the full options (dev_v3c and v3e), 8 investigations:**

| | Count |
|---|---|
| Correct | 4 (retry-policy ×2, endpoint-port ×2) |
| Partially correct | 1 (pool-fastpath) |
| Wrong | 0 |
| Abstained | 3 (payment-stop ×2, pool-fastpath ×1) |

For comparison, the v1 and v2 settings found 0 of 13. This is still development-only and scored by me; a second
reviewer is needed. Not yet tested by these options: faults without an alert (index-migration, pool-override) and
the 47-run evaluation set.

## The same settings on the saved development incidents (`runs/dev_v3d_snapshot/`)

Snapshot mode, dev_v3d settings, one run each. The 47 formal runs are not involved.

| Incident | Top cause | Decisive evidence cited | Lexical matcher | My verdict |
|---|---|---|---|---|
| INC-0000 (retry amplification) | "checkout-svc 1.9.0 … HTTP client retry policy that causes excessive retries on payment-svc" | commit e7a0cc0 and attempts metric `17f535fce8` (2 of 4) | no match | correct in substance |
| INC-9001 (connection leak) | "fast path … bypasses risk scoring … leading to database pool exhaustion" | deploy d-576, commit d142601, pool metric `03d4f590b8` (3 of 4) | no match | partially correct (pool exhaustion through load, not the unreleased connection) |
| INC-9002 (retry amplification) | "checkout-svc 1.9.0 … HTTP client retry policy that causes excessive retries on payment-svc" | commit 0d6ba14, deploy d-581, attempts metric `858412a4f1` (3 of 4) | no match | correct in substance |

**Comparison with earlier runs:** under v1/v2 every development run either abstained or named a symptom, and the
decisive change was never cited. These runs cite the faulty commit and the decisive runtime metric.

**The lexical matcher still says "no match".** It needs the key's wording ("retry amplification", "connection
leak"). A semantic reviewer, human or blinded, should score them.

## dev_v3e: + error and lifecycle logs before synthesis (`configs/dev_v3e.yaml`, `runs/live_faults_v3f_localgit`)

Before synthesis, the coordinator fetches each incident service's ERROR logs and lifecycle events (start, stop,
exit) with ordinary read-only `search_logs` calls. This is generic operational context.

| Fault | Top cause | Provisional verdict |
|---|---|---|
| payment-stop | "payment-svc was shut down by operator, causing checkout-svc to time out", citing the "shutdown requested (operator request)" and "payment-svc stopped" log lines | **correct** (first time in any sweep) |
| pool-fastpath | "payment-svc 2.5.0 introduced a db pool timeout due to new fast path for micro-charges" | **partially correct** (no unreleased connection) |
| retry-policy | "checkout-svc's payment client configuration change caused payment calls to time out due to reduced timeout and increased retries" | **correct** |
| endpoint-port | "checkout-svc's payment client configuration changed to use port 18112, but payment-svc is not listening on that port" | **correct** (exact) |

**This sweep:** 3 correct, 1 partially correct, 0 wrong, 0 abstained.

**Progress across sweeps of the same live faults (provisional, my scoring):**

| Settings | Correct | Partial | Wrong | Abstained |
|---|---|---|---|---|
| v1 (evaluated) | 0 | 0 | 1 | 4 |
| v2 | 0 | 0 | 2 | 2 |
| v2 + local git | 0 | 0 | 2 | 2 |
| dev_v3 (expansion + synthesis) | 1 | 2 | 1 | 0 |
| dev_v3c (+ causal gate) | 2 | 0 | 0 | 2 |
| dev_v3d (+ gate ranking, in-window rule) | 2 | 1 | 0 | 1 |
| **dev_v3e (+ error/lifecycle logs)** | **3** | **1** | **0** | **0** |

**Still open:**
- The connection leak's mechanism (an early return skips `POOL.release`) is not stated.
- Faults that raise no alert (index-migration, pool-override) were not investigated.
- Every number here comes from one run per fault, scored by me. These are development results; a second reviewer
  is needed, and the 47 formal runs are untouched.

## Function-context diffs (`runs/live_faults_v3g_localgit`, dev_v3e settings)

Local git now returns each changed function in full (`git show --function-context`), and synthesis shows code
changes up to 5000 characters.

- **pool-fastpath:** the full `_charge` function (acquire, three `POOL.release` calls, the new `return 201`)
  reached synthesis. The diagnosis was still "a bug in the micro-charge handling that causes database pool
  timeouts". The model does not infer that the early return skips the release, even with the code in front of it.
  This matches Steps 5–6: it is a model-reasoning limit, not missing evidence.
- **retry-policy (regression check):** "configuration changed to a higher timeout and increased retries".
  Increased retries is right; **"higher timeout" is wrong**, since 2000 → 250 ms is a reduction. Partially correct,
  with a factual error.

**Conclusion of the sweeps:**
- With change expansion, error/lifecycle context, from-scratch synthesis and the causal gate, the live faults
  that alert are found or partly found. Wrong answers are now rare, but details can still be misread (timeout
  direction).
- Leak-style mechanisms that require reading control flow remain beyond qwen3:4b.

## Faults that never reached a user-facing alert (`runs/live_faults_v3i_localgit`, dev_v3e, extended alerts)

**Why they never alerted:** at 3 requests/s, index-migration and pool-override never cross the checkout error or
latency alerts. Their only signal is inside the database:
- index-migration: payment-svc `db_query_p95_ms` rises from ~5 to ~370 ms;
- pool-override: `db_pool_wait_p95_ms` rises from ~1 to ~460–585 ms.

The extended alert set (`--alerts extended`) adds `db_query_p95_ms > 200 for 30s` and
`db_pool_wait_p95_ms > 200 for 30s` for payment-svc. These two alerts were added **after** seeing those signals,
which is noted here. The run without them is kept in `runs/live_faults_v3h_no_db_alerts`.

| Fault | Alert that fired | Result | Provisional verdict |
|---|---|---|---|
| index-migration | db_query_p95_ms > 200 for 30s | "payment-svc: database index removal causing increased query latency due to missing index on customer creation" | **correct** (first investigation of this fault) |
| pool-override | db_pool_wait_p95_ms > 200 for 30s | insufficient evidence; the top hypothesis, "database pool timeout configuration change causing connection delays", was refused for lacking a runtime-effect citation | abstained |

**Why pool-override is hard to find:**
- The override is an environment variable set at an operator restart (`d-766`, same version, same build).
- No deployment, commit or config record shows the new pool size.
- The only trace is runtime behaviour, so abstaining is a defensible answer.

## Summary over all six live fault types (development, best settings, latest run of each)

| Fault | Kind | Verdict |
|---|---|---|
| retry-policy | code (shared client) | correct in 3 of the last 4 runs; one run misread the timeout direction |
| endpoint-port | config | correct (exact) |
| payment-stop | operator stop | correct (from lifecycle logs) |
| index-migration | schema/code | correct (once database alerts exist) |
| pool-fastpath | code (connection leak) | partially correct: right change and component, the leak is not inferred |
| pool-override | operator env override | abstained |

All of this comes from one live run per configuration, scored by me against the injected ground truth. These are
development results, not a re-run of the 47-run evaluation.

## Repeat run of all six faults (`runs/live_faults_repeat2`, dev_v3e, extended alerts)

| Fault | First run | Repeat |
|---|---|---|
| retry-policy | correct | **wrong**: "payment-svc DB pool exhausted due to high request volume from checkout-svc" (a consequence, not the cause). The gate accepted it because local git attributed the shared-library commit to every service. Fixed afterwards (`d742132`: a shared-library commit names no service; deployment records say who received it). Not yet re-run. |
| index-migration | correct | correct |
| endpoint-port | correct | correct |
| payment-stop | correct | correct ("timing out … due to payment-svc being stopped") |
| pool-override | abstained | no alert (pool wait stayed under the 200 ms alert this time) |
| pool-fastpath | partially correct | partially correct (same: fast path → pool timeouts, no leak) |

**Repeat totals:** 3 correct, 1 partially correct, 1 wrong, 1 not alerted. The wrong one came from a data
attribution flaw that is now fixed.

**Stable across both runs:** endpoint-port, payment-stop, index-migration (correct); pool-fastpath (partial).

## Retry-policy rerun after the attribution fix (`runs/live_faults_retryfix`, dev_v3e, extended alerts)

Command: `python scripts/live_fault_sweep.py --faults retry-policy --config configs/dev_v3e.yaml --alerts extended --out runs/live_faults_retryfix`

- **Provenance** (`provenance.json`): BlackBox `950b073` (clean at process start), `configs/dev_v3e.yaml` sha256
  `3e578967…`, effective config sha256 `c231449b…`, qwen3:4b digest `359d7dd4…`, demo-shop fault commit `0a56709`.
- **Attribution:** the connector now attributes the real fault commit `0a56709` (libs/httpclient/policy.py +
  services/checkout/VERSION) to `checkout-svc` only, in both `list_commits` and `get_commit` (checked directly against
  the demo-shop repository; unit tests in `tests/test_local_git.py`).
- **Result: insufficient evidence.** The fix was **not exercised** by the investigation:
  - the alert fired at 17:30:00Z, but the GPU was at 91–92 °C and the sweep's thermal gate held the investigation
    until 17:37:54Z;
  - the sweep re-opens the incident at investigation start using the latest alert episode, which by then began at
    17:37:05Z (payment-svc error rate), so the window was 17:32:05–17:37:54Z;
  - deploy `d-792` (17:29:43Z) was outside that window, change expansion did not follow it, the commit was never
    fetched, and the only hypothesis ("payment-svc database connection pool exhaustion") had no change, so it was
    reported as insufficient evidence.
- One run, abstained, and it says nothing about stability. The harness defect (a delayed start moves the window
  past the change) is recorded in `docs/KNOWN_DEFECTS.md` (D19).

**Correction about earlier recorded revisions.** `output.json` → `config.code_revision` was read from `git HEAD` at
run time, but a sweep process runs the code it imported at start. In `runs/live_faults_repeat2` the retry-policy
run records `e0fdbcc`, and the other four investigated runs record `d742132`; all five actually ran `e0fdbcc` code,
because the process started at 11:58Z, before `d742132` was committed (12:07Z). New runs write `provenance.json`
with the revision taken at process start (`blackbox/provenance.py`).

## Retry-policy with the corrected incident window (`runs/live_faults_windowfix`, dev_v3e, extended alerts)

Command: `python scripts/live_fault_sweep.py --faults retry-policy --config configs/dev_v3e.yaml --alerts extended --out runs/live_faults_windowfix`.
BlackBox `a53160d` (clean), same settings and model digest as before; fault commit `04a9433`, deploy `d-811`, build `b-1278`.

**Window (D19 fix): working.**
- The payment-svc error-rate alert first fired at 19:32:07Z, with its first onset at 19:31:57Z.
- The incident was created at 19:32:14.951Z, which is the cutoff.
- The window starts at 19:27:07Z; the investigation started at 19:32:15Z.
- Deploy `d-811` (19:31:42Z) is inside the window.

**Retrieval and attribution: working.**
- Change expansion fetched `deploy:d-811`, `build:b-1278` and `commit:04a9433` (full diff: 2000→250 ms timeout, 1→4
  retries, retry on timeout/5xx, no backoff).
- All three records carry `service: checkout-svc`.
- Runtime context added payment-svc "charge failed … UNIQUE constraint failed: payments.order_id" errors.

**Result: insufficient evidence (abstained), unedited headline "No reliable root cause identified."**
- In the loop rounds, every hypothesis was payment-svc database pool or latency, citing metrics only. The gate
  refused each as "runtime effect missing".
- Synthesis wrote three hypotheses. The first cites the right change chain ("Checkout service deployed new version
  with retry logic for payment failures", `deploy:d-811`, `build:b-1278`, `commit:04a9433`). But each one names
  **payment-svc** as the component, because that is where the alert fired.
- The causal gate refused all three: "change evidence missing: … cites no retrieved deployment, commit, build or
  config record of payment-svc".
- With correct attribution, the checkout-svc change no longer passes as a payment-svc cause. This is the case the
  `libs/` fix was meant to stop (the repeat2 run accepted a payment-svc cause before it).
- What remains is the model labelling the symptom's service as the component. It is a model limit, so per this
  task I did not change the gate or the model.

One run; not a stability or accuracy claim.

## Component ownership correction (`runs/live_faults_ownership`, dev_v3e, extended alerts)

**Finding from the saved runs.**
- Across every saved synthesis output, 14 hypotheses named a component that differs from the service owning the
  change they cite as the cause.
- In 11 of those 14, the component is the alerting service: for example, the windowfix retry run named
  payment-svc while citing checkout-svc's deploy, build and commit.
- The synthesis prompt defined the component only as "the service, and the part of it if the evidence shows it".

**Correction (`c3aeb4b`).** It is general and names no incident, service or fault.
- **Prompt and schema:** the synthesis prompt defines the component as the service that owns the cause, which is
  not necessarily the alerting service. It describes both directions: a caller's change, and a fault inside the
  called service.
- **New field:** a separate `symptom_service` field records where the symptom is seen.
- **Version:** the synthesis prompt version is now recorded in `context.synthesis.prompt_version`.
- **Gate:** the causal gate is stricter in one way: a component naming more than one service is refused as
  ambiguous.
- **Tests:** `tests/test_component_ownership.py` (9 tests).

**Live check, one injection each.**
- **retry-policy** (fault commit `f7fddbc`, deploy `d-818`, build `b-1279`; BlackBox `c3aeb4b`, clean):
  - The window was kept despite a 6-minute thermal wait (D19 fix working).
  - Deploy, build and commit were retrieved, all attributed to `checkout-svc`.
  - The **synthesis call timed out after 240 s** (GPU at 95 °C), so the corrected prompt produced no output.
  - The round-loop hypotheses (payment-svc database, metrics only) were refused for a missing runtime effect.
  - Result: **insufficient evidence**. This does not test the correction.
- **payment-stop:** injected and alerted (incident `LIVE-3CBFC7`), but **not investigated**. The GPU stayed at
  88–89 °C under load from other desktop applications, so the sweep's 80 °C gate never opened; the sweep was
  stopped after 52 minutes. See `NOT_INVESTIGATED.txt`.

The correction is therefore covered by unit tests only. Its live effect is still unmeasured.
