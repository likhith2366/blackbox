# Live integration mode (development, not evaluation)

In `live_stack` mode every BlackBox tool is answered by the system it names. Nothing falls back to
`scenarios/snapshots/`, old capture files or local JSONL: a source that is not configured or not reachable
answers `unavailable` with the reason, and a source that answers with nothing answers `empty`.
Snapshot mode and the 47 formal evaluation runs are unchanged.

## Source mapping

| Tool(s) | Source system | Connector | Status on this machine |
|---|---|---|---|
| `search_logs` | Grafana Loki 3.6.20 (logs shipped by Grafana Alloy v1.21.3 from the services' own JSONL files) | `blackbox/connectors/loki.py` | **live, verified** |
| `get_metrics` | Prometheus v3.15.1 (metric points sent by the services' telemetry via Alloy, original timestamps) | `blackbox/connectors/prometheus.py` | **live, verified** |
| `search_traces`, `get_trace` | Grafana Tempo 2.10.10 (spans sent by the services' telemetry via Alloy, OTLP) | `blackbox/connectors/tempo.py` | **live, verified** |
| `list_deployments`, `get_build` | the demo platform's deployment and build records (`demo/.runtime/deployments.jsonl`, `builds/*.json`) | `blackbox/connectors/demo_runtime.py` | **live, verified** |
| `search_commits`, `get_commit`, `get_config` | GitHub REST API, pinned to the deployed commit SHA | `blackbox/connectors/github_api.py` | **live, verified** for commits on GitHub; a commit that exists only locally answers "No commit found" |
| `list_workflow_runs`, `get_workflow_run`, `get_job_log`, `list_run_artifacts` | GitHub Actions API | `github_api.py` | **live, verified** |
| `list_jenkins_builds`, `get_jenkins_console` | Jenkins 2.580.2 LTS (local container, job `demo-shop-ci`) | `blackbox/connectors/jenkins.py` | **live, verified** (build #1, console log) |
| `search_tickets`, `get_ticket` | Jira Cloud REST API v3 | `blackbox/connectors/jira.py` | **implemented, not configured** (mocked contract tests only) |
| `open_record` | re-reads the full original of a shortened record from its source | `blackbox/evidence/live_stack.py` | implemented |

The incident is opened from a live alert evaluated in Prometheus (`blackbox/evidence/live_incident.py`,
e.g. `checkout-svc:error_rate>0.1:15`). The cutoff is the moment the investigation starts.

How telemetry reaches the stack: the shop services (company repo, unchanged) write logs, spans and metric points
as JSON lines. Alloy tails the log files into Loki. `demo/telemetry_bridge.py` tails the span and metric files
as they are written (from the end of each file, so nothing old is replayed) and sends them over OTLP to Alloy,
which forwards spans to Tempo and metrics to Prometheus (remote write). Timestamps, ids and values are unchanged.

## Setup (from the blackbox repo root)

```
# 1. observability stack (Docker Desktop running)
docker compose -f tools/observability/docker-compose.yml up -d
docker compose -f tools/observability/docker-compose.yml --profile ci up -d jenkins     # optional CI
# 2. demo services writing to demo/.runtime/telemetry/stack (the directory Alloy reads)
python -c "from demo.lock import demo_lock; from demo import controller; from demo.paths import RUNTIME; \
  ctx = demo_lock('baseline'); ctx.__enter__(); controller.baseline(str(RUNTIME / 'telemetry' / 'stack'))"
python -m demo.telemetry_bridge                    # keep running
python -m demo.loadgen --duration 3600 --rps 3     # keep running
# 3. a fault and a live investigation
python -m demo.controller inject pool-fastpath
python -m blackbox.cli investigate --live-stack --alert "checkout-svc:error_rate>0.1:15" \
    --system blackbox --config configs/frozen_v1.yaml --out runs/_live/investigation_1
# 4. checks
python -m blackbox.connectors.loki smoke ; python -m blackbox.connectors.prometheus smoke ; python -m blackbox.connectors.tempo smoke
python scripts/verify_live_stack.py
```
UI: start the API and UI as usual; **New investigation → live stack** takes the alert rule; **Connections → Live
stack sources** shows live / not configured / unavailable / error per source; every evidence card shows
`<backend> · live` and its query. Grafana: http://127.0.0.1:3000 → dashboard "BlackBox demo incident".

## Configuration (environment variables or the git-ignored `.env.local`)

| Variable | Default | Used by |
|---|---|---|
| `BLACKBOX_LOKI_URL` | http://127.0.0.1:3100 | Loki |
| `BLACKBOX_PROMETHEUS_URL` | http://127.0.0.1:9090 | Prometheus |
| `BLACKBOX_TEMPO_URL` | http://127.0.0.1:3200 | Tempo |
| `BLACKBOX_DEMO_RUNTIME` | demo/.runtime | deployment/build records |
| `BLACKBOX_GITHUB_REPO`, `GITHUB_TOKEN` (else `gh auth token`) | likhith2366/blackbox-demo-shop | GitHub, Actions |
| `JENKINS_URL`, `JENKINS_USER`, `JENKINS_API_TOKEN`, `JENKINS_JOB` | (none) | Jenkins |
| `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT` | (none) | Jira |
| `BLACKBOX_TELEMETRY_DIR` | demo/.runtime/telemetry/stack | Alloy mount (compose) |
| `GRAFANA_ADMIN_PASSWORD`, `JENKINS_ADMIN_PASSWORD` | change-me-locally | local containers |

Secrets are read from the environment or `.env.local` only, are never logged, and are redacted from error text.
All ports are bound to 127.0.0.1.

## Guarantees and their tests

| Requirement | Where | Test |
|---|---|---|
| read-only | connectors only issue GET (Jenkins/Jira/Loki/Prometheus/Tempo/GitHub) | `test_jira_contract.py` (POST never sent) |
| explicit status, no fallback | `LiveStackBackend` returns `unavailable`/`empty`/`error` | `test_live_stack.py::test_unavailable_versus_no_match`, `::test_live_stack_never_reads_snapshots_or_local_jsonl` |
| cutoff | every window ends at the cutoff; Tempo spans and Jira comments after it are hidden | `::test_window_never_passes_the_cutoff`, `test_jira_contract.py` |
| provenance | `backend`, `mode`, `query`, `source_url` on every record | `::test_provenance_is_stamped_on_every_record` |
| bounded output, full original | content limits per connector; `open_record` returns the full original in parts | `::test_open_record_returns_the_full_original_in_parts` |
| isolation | Jira/Jenkins unconfigured does not affect other sources | `::test_unconfigured_sources_are_isolated_and_honest` |
| secret redaction | tokens removed from errors | `::test_secrets_are_redacted` |
| untrusted text | ticket/console text flagged `untrusted_text`, kept as data | `test_jira_contract.py` |

## Verification on this machine (2026-11-24)

- Fault `pool-fastpath` injected at 03:16:53Z (commit c24dd44, build b-1231, deployment d-590; the commit is local
  to the company repo and not pushed, so GitHub correctly answers "No commit found" for it).
- `scripts/verify_live_stack.py` -> `runs/_live/verification.json`: the payment-svc ERROR line
  "db pool acquire timed out after 1000ms" (03:17:36.981Z, trace 394a318e…) is returned by Loki and by
  BlackBox's `search_logs` with the same timestamp and text; Tempo and BlackBox's `get_trace` return the same 4 spans;
  Prometheus and BlackBox's `get_metrics` agree on `db_pool_in_use` (max 4, last 4). GitHub returned commit b7ae74f and
  Actions run #44663434650; Jenkins returned build `demo-shop-ci#1` (SUCCESS) and its console log.
- Grafana dashboard with the same incident: `docs/live_stack/grafana_incident_dashboard.png`.
- Live investigations (frozen_v1 settings, qwen3:4b), saved unedited:
  - `runs/_live/investigation_1/`: the incident window had grown to 26 min while the job waited for the GPU; the
    Prometheus connector then refused windows over 20 min, the model received that error and abstained after 1 call.
    Fixed afterwards: a long window now uses a wider step, stated in the record.
  - `runs/_live/investigation_2/` (LIVE-CF656E): 6 rounds, 6 calls (1 Prometheus, 5 Tempo searches), 19 live records
    (18 Tempo, 1 Prometheus). Three diagnose requests were refused by gate v1 (one citation each); the report is
    `insufficient_evidence`. It never queried logs, deployments or GitHub. No claim of a correct diagnosis.
