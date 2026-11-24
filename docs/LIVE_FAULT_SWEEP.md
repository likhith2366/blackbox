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
