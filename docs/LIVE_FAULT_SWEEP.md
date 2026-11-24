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
- **D15:** a commit fetched with its diff could not replace the diff-less copy from the commit list (coordinator).
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
