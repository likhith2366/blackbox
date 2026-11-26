# Live demo rehearsal, 2026-11-26 (development, not evaluation)

All saved under `runs/rehearsal_2026-11-26/`. Outputs are unedited: each job's files were copied byte-for-byte
from `runs/ui_jobs/<job>/`; `copies_sha256.json` confirms all 12 copies are identical to the originals.
The formal eval_v1 (47 runs) is not involved. Three changes, one run each, are not an accuracy estimate.

## How it was run

Everything was stopped at the start, then started in this order (commands from the repo root):

```
docker compose -f tools/observability/docker-compose.yml --profile ci up -d     # Loki, Alloy, Prometheus, Tempo, Grafana, Jenkins
python -c "...controller.baseline(RUNTIME/'telemetry'/'stack')"                    # demo shop baseline (see LIVE_INTEGRATION.md)
python -m demo.telemetry_bridge
python -m demo.loadgen --duration 10800 --rps 3
DEMO_TELEMETRY=stack python -m demo.control_api                                     # :8710, reset writes where Alloy reads
python -m blackbox.api                                                              # :8700
(cd demo/web && npm run dev -- --port 5174) ; (cd ui/blackbox-web && npm run dev -- --port 5173)
python scripts/verify_live_stack.py --out runs/rehearsal_2026-11-26/source_verification.json
python demo/web/e2e/rehearse_live.py --out runs/rehearsal_2026-11-26 --gpu-max 85 endpoint-port payment-stop h-payment-currency-log
```

For each change the driver uses only UI clicks:
1. Shop operator page: reset, then a healthy order.
2. Wait 330 s, so the previous change is outside the 5-minute incident lookback.
3. Inject from the operator page, then place an order.
4. Wait up to 4 minutes for any alert in the extended set.
5. In BlackBox: **New investigation** with live stack, **improved** settings and the alert rule that fired.
6. Screenshots of the report, trace, evidence and details tabs, then **Ask BlackBox**.

The change id appears only on the operator page.

## Provenance (from each job's `provenance.json` and `meta.json`)

| | Value |
|---|---|
| BlackBox code loaded by the API | `1c65f87`, clean at API start |
| Settings | `configs/dev_v3e.yaml` ("improved"), sha256 `3e5789670a5c…`; effective config sha256 `c231449be980…` |
| Model | qwen3:4b, Ollama digest `359d7dd4bcda…`, temperature 0, seed 7 |
| Chat | standard settings (`configs/dev.yaml`), prompt `chat_v1@8318c5f55b` |
| Code source | local git `blackbox-demo-shop`, pinned to the deployed SHA |
| Demo-shop changes | endpoint-port `c736e86` (deploy `d-799`, build `b-1276`); payment-stop = operator stop (no commit); harmless `51b22a2` (deploy `d-804`). Tagged and pushed as `fault/rehearsal_2026-11-26/*`. |

The UI code changed once during the rehearsal: after the endpoint-port chat page went blank, the chat view fix
(`6fd4452`) was hot-reloaded before payment-stop. This change touched only the UI; the investigation code was
not changed.

## Live sources

| Source | Response at rehearsal (`source_verification.json`, `/api/live/sources`) | Used in the two investigations |
|---|---|---|
| Loki | live; the newest ERROR line matched Loki's raw API (same id and timestamp) | queried and cited (13 log citations) |
| Prometheus | live; the last `db_pool_in_use` point matched (the comparison checks only the last point; the series reached -3, see D21) | queried and cited |
| Tempo | live; 18 spans matched the raw trace | queried (traces and spans), **not cited** |
| Local git (code) | live; `get_commit`, `get_config` (`config/payment-svc.yaml @ b7ae74f`) answered | queried; commit `c736e86` cited (endpoint-port) |
| Deployments / builds | live demo-platform records | queried; build `b-1276` cited; deploy `d-799` cited in the chat answer |
| Configs | `get_config` answered in verification | **not called** by either investigation (config change seen via the commit diff) |
| GitHub Actions | answered `list_workflow_runs` (run #44) in verification, after fix D20 | available, **not used** in an investigation |
| Jenkins | container live (`/api/live/sources`) | available, **not used** in an investigation |
| Jira | **not configured** (credentials not set) | – |

Each source states its status explicitly; no live source fell back to a snapshot.

## Outcomes

| Change | Alert | BlackBox top cause (verbatim) | Verdict (my reading against the injected change) | Chat |
|---|---|---|---|---|
| endpoint-port | `checkout-svc:error_rate>0.1:15` | "checkout-svc's payment client configuration changed to use a non-existent port in the sidecar"; cites `commit:c736e86`, `build:b-1276` and 4 Loki lines | **correct**: right component, right change, wrong-port mechanism ("in the sidecar" echoes the commit message). Ranks 2–3 ("timeout too low", "payment-svc overloaded") are wrong but lower. 497 s. | **verified** (6/6 citations known, 0 unsupported tokens): names deploy d-799, commit c736e86, 18102→18112 |
| payment-stop | `checkout-svc:error_rate>0.1:15` | "payment-svc was shut down by an operator request, causing checkout-svc to time out on payment calls"; cites the "shutdown requested" and "payment-svc stopped" lines | **correct**. 482 s. | **not verified**: "No deployments… most likely cause is a configuration change or a commit…" is speculation with no citations, after one `list_deployments` call. A poor answer, shown as such by the verifier. |
| h-payment-currency-log (harmless) | **none within 4 min** | not investigated | **no-alert control**: the harmless change raised no alert, so nothing was investigated. This is **not** a BlackBox abstention, and it does not test BlackBox's false-positive behaviour. | – |

Orders: the healthy orders succeeded (HTTP 201); orders after the endpoint-port and payment-stop changes failed in
the shop with HTTP 504 "payment timeout" (screenshots `04_order_after_change.png`). The order after the harmless change succeeded (HTTP 201).

## Problems found during the rehearsal

- **Chat page went blank** after opening a session (`TypeError: destroy is not a function`): the effect returned
  `scrollIntoView`'s Promise in current Edge. Fixed in `6fd4452`. The endpoint-port question was then asked in the
  same open session through the UI after the rehearsal; its live cutoff stays at 18:11:55Z. The driver had lost
  that change's in-memory record. `steps.json` keeps its error, and `steps_reconstructed.json` is rebuilt from
  the driver log. The driver now keeps partial records (`019bd42`).
- **GPU heat:** other desktop applications kept the idle GPU at 82–87 °C. The first attempt waited for 75 °C and
  was stopped (`_aborted_attempt1/`). Each investigation took about 8 minutes, against about 1.5 minutes when the
  GPU is cool.
- **Duplicate React keys** on the run page for repeated metric citations (console warning only).

## Not shown by this rehearsal

- BlackBox's behaviour on a harmless change that does raise an alert.
- The repeatability of these results.
- Retry-policy: see `docs/LIVE_FAULT_SWEEP.md`. The rerun abstained because the window missed the change (D19).
