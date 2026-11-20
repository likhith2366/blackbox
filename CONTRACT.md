# BlackBox implementation contract (Milestone 3 build round)

Source design: `docs/milestone2.md` (as submitted) and
`../BlackBox_Detailed_Project_Overview.pdf`. Do not redesign; implement it.
Owner of this file: Integration (integration lead). Others request changes in
`coordination/requests.md`.

## 0. Ground rules (all agents)
- **No fabrication.** Never write logs, traces, metrics, model outputs or scores that a
  real execution did not produce. Team-authored *fixtures* (Jira tickets, build/commit
  history authored for the demo) are allowed only when labelled `fixture: true` and
  described as fixtures, never as live integrations.
- **Stay in your files** (section 2). Do not edit another agent's files; request it.
- **Only Integration runs `git commit`.** Others never commit, stash, reset or checkout.
- **GPU discipline.** Integration alone schedules inference. the other modules never call Ollama;
  they request model runs in `coordination/requests.md` and Integration runs them (directly or
  through Evaluation's harness). One inference job at a time.
- **Four agents** (QA = independent integration QA and usability). Issues in another
  agent's files go to the owner as a failing test or a proposed patch, never a concurrent edit. Evidence never calls the model. Do not run the demo
  load generator while an evaluation batch is running.
- Python 3.11, run everything from the repo root with `python -m ...`.
  Available: pydantic 2, requests, fastapi, uvicorn, pyyaml, jsonschema, pytest,
  pandas, opentelemetry. Ask before adding a dependency.
- Windows host: use `pathlib`, avoid shell-specific tricks, keep paths short (MAX_PATH).

## 1. Measured hardware/model facts (Integration, 2026-11-19)
- RTX 3060 Laptop 6 GB, 15.7 GB RAM, i5. Ollama 0.34.0 at http://127.0.0.1:11434.
- Model `qwen3:4b` (Q4_K_M, tools+thinking capable). `num_ctx=8192` fits fully in
  VRAM (3.87 GB): ~480 tok/s prefill, ~15 tok/s generation. `num_ctx=16384` spills
  to CPU (4.17/5.4 GB on GPU) and generation drops to ~2 tok/s, so 8192 is the limit.
- Observed in a smoke test: the model invents/pads citation IDs. Every cited
  evidence ID must be checked against evidence actually retrieved.

## 2. File ownership
| Path | Owner |
|---|---|
| `CONTRACT.md`, `README.md`, `requirements.txt`, `configs/` | Integration |
| `blackbox/schemas.py`, `blackbox/evidence/interface.py` | Integration (shared, read-only for others) |
| `blackbox/model/`, `blackbox/investigator/`, `blackbox/baselines/`, `blackbox/systems.py`, `blackbox/cli.py` | Integration |
| `tests/test_investigator_*.py`, `tests/test_baselines_*.py` | Integration |
| `demo/` (services, deploy tooling, fault controller, load generator) | Evidence |
| `blackbox/evidence/` (backend; written by Evidence, transferred to Integration on 2026-11-20) | Integration |
| `scenarios/` (snapshot builder, frozen snapshots, README) | Evidence |
| `evaluator_private/` (answer keys, scenario manifest, controller state) | Evidence writes, Evaluation reads |
| `tests/test_evidence_*.py`, `tests/test_scenarios_*.py` | Evidence |
| `eval/` (harness, metrics, worksheets, reports), `runs/`, `results/` | Evaluation |
| `tests/test_eval_*.py`, `tests/test_leakage_*.py` | Evaluation |
| `tests/integration/` (QA tests, labelled fixtures in `tests/integration/fixtures/`) | QA |
| `blackbox/connectors/__init__.py`, `blackbox/connectors/github_api.py` (live read-only GitHub REST) | QA |
| `blackbox/connectors/local_logs.py` (live reader of the app's actual JSONL logs) | Evidence |
| `blackbox/evidence/live.py` (composes snapshot backend + live connectors; mode live/snapshot) | Integration |
| `tests/test_connectors_*.py`, captured live responses under `scenarios/live_captures/` | Evaluation |
| `blackbox/api/` (thin HTTP API, job queue, GPU lock), `runs/_gpu.lock` protocol | Integration |
| `ui/blackbox-web/` (React + TS + Vite BlackBox UI) | Frontend (frontend) |
| `demo/web/` (React + TS + Vite shop UI) | Frontend (frontend) |
| `demo/control_api.py` (local demo-control API) | Evidence |
| `viewer/` (saved-run viewer, read-only over `runs/`), `docs/REPRODUCE.md`, `docs/EVIDENCE_CHECKLIST.md`, `docs/QA_FINDINGS.md` | QA |
| `coordination/agent{1,2,3}_log.md` | each agent its own |
| `coordination/requests.md` | append-only, everyone |

## 3. Module boundaries
```
demo/            -> runs the app, injects faults, captures raw telemetry      (Evidence)
scenarios/       -> turns a capture into a frozen, model-visible snapshot     (Evidence)
evaluator_private/ -> answer keys + manifest, invisible to blackbox/          (Evidence)
blackbox/evidence/ -> EvidenceBackend over one snapshot (+ ablation, cutoff,  (Integration)
                      connector-failure simulation)
blackbox/investigator, baselines, model -> systems b0 / b1 / blackbox          (Integration)
eval/            -> runs systems, stores run records, scores, reports         (Evaluation)
```
`blackbox/` must never import from `demo/`, `scenarios/` (except snapshot data via the
backend), `evaluator_private/` or `eval/`. Evaluation adds a test that enforces this.

## 4. Entry points (signatures are the contract)
```python
# Evidence — blackbox/evidence/backend.py
load_backend(incident_id: str, ablation: Ablation = "none", snapshot_root: Path | None = None) -> EvidenceBackend
list_incidents(snapshot_root: Path | None = None) -> list[str]

# Integration — blackbox/systems.py
run_system(system: SystemName, backend: EvidenceBackend, config: RunConfig,
           b1_evidence: list[EvidenceRecord] | None = None) -> SystemOutput
# b1 requires b1_evidence = SystemOutput.evidence_seen of the *initial* BlackBox run
# of the same incident with ablation "none".
```

## 5. Commands
```
# Evidence
python -m demo.run --healthy                      # start services + healthy load, print health
python -m demo.controller inject <fault_id>       # apply a fault (private ids)
python -m demo.controller reset                   # restore healthy state
python -m scenarios.build <scenario_slug>         # run capture -> snapshot + answer key
python -m scenarios.build --all                   # build pilot + nine scenarios
python -m scenarios.verify                        # schema, cutoff, leakage, ablation checks
# Integration
python -m blackbox.cli investigate --incident INC-XXXX --system blackbox|b0|b1 [--ablation no_jira] [--b1-from runs/.../output.json] --out <dir>
# Evaluation
python -m eval.harness run --phase pilot|eval --matrix eval/matrix.yaml --batch <name>
python -m eval.score --batch <name>               # objective metrics + review worksheets
python -m eval.report --batch <name>              # tables, failure catalogue, summary
pytest -q
```

## 6. Snapshot layout (Evidence decides internals; these are fixed)
- Model-visible: `scenarios/snapshots/<incident_id>/` with an `incident.json` that
  validates as `Incident`. Incident IDs are opaque (`INC-` + 4 digits); the pilot may be
  `INC-0000`. No file or directory name inside a snapshot may hint at the fault.
- Evaluator-private: `evaluator_private/answer_keys/<incident_id>.json` (validates as
  `AnswerKey`) and `evaluator_private/scenario_manifest.json` (incident_id -> scenario
  number, type, variant_of, frozen flag, build command, capture time).
- Frozen snapshots are never rewritten. A rebuild gets a new incident ID; the old one
  stays on disk and is marked superseded in the manifest.

## 7. Evidence IDs and cutoff
- Source prefixes: `log:`, `span:`/`trace:`, `metric:`, `deploy:`, `build:`,
  `commit:`, `config:`, `jira:` (comment-level ids like `jira:DEMO-12#c3`).
- Every record carries `available_time`. The backend drops anything later than
  `investigation_cutoff`; scenarios include one post-cutoff ticket update to prove it.

## 8. Ablations (backend-level, Evidence implements, Evaluation verifies no leakage)
- `no_jira`: no tickets anywhere; also drop ticket keys from commit messages/PR text.
- `no_provenance`: remove deployment->artifact->build->commit mappings. Deployment
  events may keep time + service but lose version/build/commit/digest; builds are gone;
  commits stay searchable; startup log lines that print commit/build ids are redacted.
- `no_traces`: no spans/traces; trace ids stay in logs only as opaque strings.
- Everything else identical. `source_status()` reports the ablated source unavailable.

## 9. Investigation limits (Integration)
6 rounds, 12 tool calls total, at most 3 per round. Tools are an allowlist enforced in
code; a request for anything else is recorded in `unauthorized_attempts`, refused, and
`unauthorized_executed` stays 0. Retrieved text is evidence, never instructions.

## 10. Scenario requirements (Evidence) — from Milestone 2 section 4
Pilot (development only) + nine evaluation scenarios on one application:
1-2 misleading symptoms; 3-4 genuine fault plus an unrelated newer change;
5-6 insufficient evidence (one has two equally plausible causes with the
distinguishing evidence withheld); 7 healthy/no-fault; 8 connector failure mid-
investigation (replay of a diagnosable case, `remaining_evidence_sufficient` predefined);
9 prompt injection planted in a log line or ticket (replay of a diagnosable case).
Also: one lengthy Jira discussion with conflicting suggestions, one post-cutoff ticket
update, a README with reproduce/reset per fault. Variants are labelled as variants.

## 10b. Development rules recorded so far (Integration)
- First-tool rule (orchestration intervention, not a model improvement): until any tool call
  has been made, the response grammar requires >=1 tool call and allows only "continue".
  It lifts after the first call even if that call failed, so abstention stays possible.
- Diagnosis gate: "diagnose" is accepted only with >=2 retrieved citations, >=1 from runtime
  sources. Recorded per round in `InvestigationStep.orchestration_rules`.
- Citations: `CitationCheck` audits every citation (raw, resolved, method exact | embedded |
  suffix | ambiguous | invalid). Ambiguous text is never resolved.
- Identical tool calls are answered from a per-run cache, still logged as requested calls,
  and still consume the tool-call budget (`InvestigationStep.duplicate_calls`).
- Context: `InvestigationStep.context` and `SystemOutput.context` hold tokenizer estimates,
  Ollama's actual prompt tokens and notebook decisions (full / one-line / omitted records).

## 10c. Live connectors (scope update 2026-11-20)
- Connectors return `BackendResult` with normal `EvidenceRecord`s: `fixture=False`,
  `content_ref` = source URL or file path, `attributes` include `live: true`, `retrieved_at`,
  and where applicable `repo`, `sha`, `url`. Evidence ids are stable and prefixed by origin
  (`gh:commit:<repo>@<sha7>`, `gh:file:<repo>@<sha7>:<path>`, `gh:issue:<repo>#<n>`, `log:` ids
  for local logs keep the snapshot hashing scheme).
- Status semantics as in schemas.py: `unavailable` (not configured / no credentials / no access),
  `error` (network, HTTP 5xx, rate limit), `empty` (answered, nothing matched).
- Secrets: token only from env `GITHUB_TOKEN` or, if unset, `gh auth token` read into memory at
  runtime. Never printed, logged, stored in records, snapshots, run outputs or exceptions.
- Bounded: timeouts, max pages, max bytes per response; no writes of any kind.
- Evaluation uses frozen snapshots only. Live responses used in a pilot are saved verbatim as a
  versioned capture so the run is replayable; live data never feeds the 47-output matrix.

## 12. UI and HTTP contracts (2026-11-20)

### BlackBox API (Integration) — FastAPI, `python -m blackbox.api` -> http://127.0.0.1:8700
All JSON. Read-only except POST /api/investigations. No tokens, answer keys, scores,
scenario types or controller state are ever returned.
- `GET /api/health` -> `{ok, model, gpu: {busy, holder, since}, queue: {queued, running}}`
- `GET /api/project` -> `{repo, sources: [{source, mode: "live"|"snapshot"|"unavailable", healthy, detail, error}]}`
  (live checks: GitHub repo metadata, local log dir; Jira/Jenkins reported "unavailable: not configured")
- `GET /api/incidents` -> `[{incident_id, alert: Alert, window_start, window_end, investigation_cutoff, services, available_sources}]`
  (from snapshot incident.json only)
- `GET /api/runs?batch=` -> `[{run_id, batch, path, system, incident_id, ablation, status, report_status, headline,
  wall_seconds, rounds, tool_calls, code_revision, mode}]` (every runs/**/output.json; no scores)
- `GET /api/runs/{run_id}` -> `{run_id, meta, output: SystemOutput}`;  `GET /api/runs/{run_id}/export` -> raw output.json download
- `POST /api/investigations` `{incident_id, mode: "snapshot"|"live", problem_description?, window_start?, window_end?}`
  -> `202 {job_id, status: "queued", position}`. BlackBox system only. Window must lie inside the incident window
  (400 otherwise). problem_description is appended to the model-visible incident notes, marked as user-provided.
- `GET /api/jobs` / `GET /api/jobs/{job_id}` -> `{job_id, status: queued|waiting_gpu|running|completed|failed,
  created, started, ended, run_id, error, request}`
- `GET /api/jobs/{job_id}/events` -> SSE stream (`text/event-stream`); each event `{type, ...}`:
  `queued`, `waiting_gpu {holder}`, `started`, `round {InvestigationStep}` (as each round completes, real data),
  `report {FinalReport}`, `completed {run_id}`, `failed {error}`. Ends after completed/failed. Polling the job
  endpoint is equivalent.
Jobs run one at a time and only when the GPU lock is free (below); they never start competing inference.

### GPU lock (everyone who runs inference)
`runs/_gpu.lock` = JSON `{pid, owner, started}` created atomically (exclusive create) before inference and
removed after. A lock whose pid is not alive is stale and may be removed. The API, the eval harness and
experiment runners all acquire it.

### Demo shop (Evidence)
- Shop UI `demo/web/` -> Vite dev server http://127.0.0.1:5174, proxying to checkout-svc (`POST /orders`,
  `GET /health`) — no invented inventory/payment/order capabilities; any catalog is a labelled static demo list.
- Demo control API `demo/control_api.py` -> http://127.0.0.1:8710, bound to 127.0.0.1 only: service health,
  inject/reset of supported faults through demo.controller, current fault (controller UI only). It refuses
  inject/reset while a scenario capture holds the demo lock. Never reachable from BlackBox tools; BlackBox API
  never calls it and never reads controller state.

### BlackBox UI (Frontend) — `ui/blackbox-web/`, Vite dev server http://127.0.0.1:5173, proxy `/api` -> 8700.

## 11. Coordination protocol
- Log progress and blockers in your own `coordination/agentN_log.md` (timestamped).
- Ask for interface changes in `coordination/requests.md`; Integration answers there.
- Deliver early: Evidence ships the pilot snapshot + working `load_backend` before the
  other scenarios, because Agents 1 and 3 integrate against it.
