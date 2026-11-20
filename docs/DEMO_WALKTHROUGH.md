# Demo walkthrough (live demonstration: not an evaluation result)

Owner: Evaluation. Last updated 2026-11-20. Commands are run from the repo root unless a step says
otherwise. UI labels are taken from verified screenshots of the running interfaces
(`ui/blackbox-web/screenshots/verified/`, `demo/web/screenshots/verified/`).

**Honesty rules for the demo**
- Show BlackBox's **actual** conclusion, even when it is wrong. Never script or edit a diagnosis.
- The operator reset (step 9) is a **controller action**, not a BlackBox repair. BlackBox only reads
  data; any fix it suggests is untested.
- Live demo results are kept apart from the pilot and from the formal evaluation (eval_v1).
- If something fails live, use the **REPLAY** fallback (end of this page). It must be announced as a
  replay.
- Do not run the demo traffic generator, or start UI investigations, while an evaluation batch
  needs the GPU. The UI header shows "GPU free" / "GPU busy". `runs/_gpu.lock` names the holder.

## 0. Start everything (five terminals)
| # | Command | URL |
|---|---|---|
| 1 | Ollama with `qwen3:4b` must be running | http://127.0.0.1:11434 |
| 2 | `python -m demo.run --healthy --duration 30` (baseline deploy + healthy load; prints health) | checkout-svc :18101, payment-svc :18102 |
| 3 | `python -m demo.control_api` (operator-only; bound to 127.0.0.1) | http://127.0.0.1:8710 |
| 4 | `cd demo/web && npm run dev` (shop + local demo controls) | http://127.0.0.1:5174 |
| 5 | `python -m blackbox.api`, then `cd ui/blackbox-web && npm run dev` | API :8700, UI http://127.0.0.1:5173 |

Check:
- the shop's **Local demo controls → Service health** shows both services "ok";
- the BlackBox UI header shows **API connected** and **GPU free**.

## 1. Healthy checkout
- Open the shop (**Shop** tab) and place an order. It succeeds.
- Screenshots: `demo/web/screenshots/verified/pool-fastpath/01_reset_healthy_baseline.png`,
  `02_healthy_order.png`.

## 2. Inject a fault (operator control panel)
- Go to **Local demo controls → Inject / reset**, select one fault (e.g. `retry-policy`) and press
  **Inject selected fault**.
- This makes a real commit, build and deploy, so it takes tens of seconds.
- **Applied since last reset** then lists the fault.
- Fault ids appear only on this operator page; nothing on it is sent to BlackBox.

## 3. Failed checkout
- In the **Shop**, place orders again. Under the fault, orders fail or time out.
- Under **Generate traffic**, press **Start traffic** (seeded real `POST /orders`) to produce the alert
  condition.
- Screenshots: `demo/web/screenshots/verified/{pool-fastpath,retry-policy}/03_fault_injected.png`,
  `04_order_during_fault.png`, `05_traffic_finished.png`.

## 4. A BlackBox incident
Investigations need an incident: alert, window and cutoff.
- **Route A (preferred once available):** **Create demo incident snapshot → Create snapshot from this
  session** copies this session's real logs, spans, metrics, build/deploy records and repository history
  into a new development snapshot (INC-9xxx).
  - It is never part of the evaluation.
  - It needs the alert condition in the captured metrics (checkout error_rate > 0.10 or p95 > 1500 ms
    for 15 s).
  - Status: being completed by Evidence (KNOWN_DEFECTS D6).
- **Route B (works today):** investigate the existing snapshot incident whose fault matches, e.g.
  **INC-0000** for `retry-policy`. Say explicitly that the incident context comes from a saved snapshot.

## 5. Start the investigation in the UI
- In the BlackBox UI go to **Start investigation**, choose the incident, choose **Evidence mode: live
  (snapshot + live connectors)**, optionally narrow the window and add a problem description, then press
  **Start investigation**.
- Screenshot: `ui/blackbox-web/screenshots/verified/start_followup.png`.
- What live mode reads:
  - **live:** commits and configs from GitHub, pinned to the deployed commit, and the incident's
    captured application logs;
  - **snapshot:** everything else.
- The UI job uses the API's config file (`configs/dev.yaml` for the recorded job), not the frozen
  evaluation config.
- Jobs run one at a time when the GPU lock is free; otherwise the status shows `waiting_gpu`.

## 6. Live progress
- The job page shows **live event stream**. Each round appears as it completes: tool calls with
  arguments and record counts, the hypothesis ledger, citations with their resolution method, the
  stopping decision, and context-token accounting.
- **Orchestration rules** (first-tool rule, diagnosis gate) are shown as coordinator actions, not as
  model behaviour.
- Screenshot: `job_live_running.png`.

## 7. Open the evidence and its sources
- Under **Evidence returned so far**, filter by source, service or time and open records.
- Live log records point to the captured JSONL file. GitHub records, when retrieved, link to the
  commit or file at a pinned sha.
- Screenshot: `run_ui_job_source_open.png`.

## 8. The actual conclusion, even if wrong
- **Results & history** opens the saved run: report status, ranked explanations, claims with
  citations, unresolved questions.
- **Recorded example** (job `20261120T032323-52d9fe`, live, INC-0000):
  - 2 rounds and 2 tool calls; 15 live log records and 20 snapshot spans.
  - Conclusion: "checkout-svc :: payment call timeout". This is a **symptom label, not the cause**
    (retry amplification in the shared client). Gate v1 accepted it (KNOWN_DEFECTS D1).
  - GitHub was not used in this run.
- Present the conclusion as it is. Screenshots: `job_completed.png`, `run_ui_job.png`.

## 9. Operator reset and recovery (not a BlackBox repair)
- **Local demo controls → Reset to healthy baseline**: the controller redeploys the healthy baseline
  (about 12 s in the verified screenshot; HTTP 200 "healthy baseline deployed").
- Place an order in the **Shop**: it succeeds again.
- Say clearly: **the operator** restored service with the controller. BlackBox did not change anything.

## 10. Reopen a saved investigation
- **Results & history** lists every saved run (pilot, UI jobs; evaluation runs are labelled by batch).
- Open a run to see its rounds, evidence and report.
- **Follow-up** starts a NEW job with the earlier run named in the problem description; the earlier run
  is unchanged.
- Screenshots: `runs.png`, `run_pilot_blackbox.png`, `start_followup.png`.

## REPLAY fallback (when a live step fails)
- Open a completed saved run in **Results & history** and say: **"This is a REPLAY of a saved run
  from <time>; it is not live."**
- Recorded live GitHub responses can be replayed offline (`blackbox/connectors/capture.py` ReplaySession;
  format verified by `python -m eval.live_capture verify`).
- A replay shows what really happened. It is never a scripted or corrected diagnosis.

## Screen recording
- ffmpeg is not installed, and nothing heavy will be installed.
- Windows Game Bar is present (`Microsoft.XboxGamingOverlay`): **Win+Alt+R** starts and stops
  recording of the active window.
- Record only real, working interfaces (not fixture mode).

## Verified screenshots available now
- BlackBox UI: `overview.png`, `overview_mobile.png`, `connections.png`, `start_followup.png`,
  `job_live_running.png`, `job_completed.png`, `run_ui_job.png`, `run_ui_job_source_open.png`,
  `runs.png`, `run_pilot_blackbox.png`, `compare_pilot.png`, `incident_timeline.png`.
- Shop: `pool-fastpath/01_reset_healthy_baseline.png`, `02_healthy_order.png`.
- Shop journeys 01-08 for `pool-fastpath` and `retry-policy` (fault injected, failed order, traffic,
  demo incident created, reset, healthy after reset); BlackBox journey `journey_INC-9001/` 01-10.
- Not yet captured: none of the 10 steps is missing.
- Observation in `01_reset_healthy_baseline.png`: **Service health** shows "ok" via the control API
  but "no answer" for the direct `GET /health` column. Check this before recording.
