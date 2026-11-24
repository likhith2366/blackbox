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
