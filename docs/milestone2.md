# Milestone 2 (as submitted)

BlackBox

Milestone 2  |  System and Evaluation Plan

Likhith Vardhan Goruputi • Praneeth Yashovardhan Kadem • Bhavana Peruri

1  Refined problem and task

BlackBox will help backend developers and on-call engineers investigate failures by connecting runtime evidence with code, deployments and Jira context. Given an alert and an evidence snapshot, it will identify a likely component and failure mechanism, cite supporting records, and state when no reliable diagnosis is possible. The core deliverable is diagnosis; automated repair is a stretch goal.

We will generate runtime logs/traces from local demo services and author labeled demo tickets. No incident dataset or results exist yet; model training is not required.

2  Baselines and comparison design

B0 — Logs only. The team will implement one model call with the alert and captured logs. This is the simplest credible baseline.

B1 — Same evidence without iteration. For each initial BlackBox run, give one model call its retrieved source records, excluding intermediate conclusions. Reserve context for instructions/output; if the evidence union exceeds the remainder, drop trailing records in retrieval order and flag omitted IDs/token counts. Mark that comparison unmatched and exclude it from same-evidence iteration claims. This tests reasoning on retrieved evidence, not retrieval quality or equal compute.

BlackBox — Iterative investigation. A coordinator lets the same model request read-only tools and revise a structured hypothesis ledger. All methods use the same model, decoding settings and final response schema. Record token use and runtime alongside diagnosis quality. Freeze prompts and a tool-call budget after a separate pilot, before the evaluation runs.

3  Architecture and interfaces

Fault controller and evaluator. Run two demo services and a database in Docker Compose, inject faults, and retain the answer key and scoring rubric separately. The investigator cannot access controller files, fault labels or evaluator credentials. Scenario names and filenames must not reveal the answer.

Connector layer. Read GitHub code/diffs, Jira tickets/comments, Jenkins build metadata and local logs, metrics and traces through APIs or MCP. Export versioned snapshots for replay. The minimum tool contract accepts service, time window, query and result limit; returns status, evidence IDs, timestamps, source references and truncation/error flags.

Evidence store and retriever. Use SQLite plus snapshot files. Each record stores ID, source, service, event time, availability time, version/commit and content reference. A links table connects deployments, artifacts, builds, commits and tickets. Retrieve by identifiers, time and text; preserve original passages behind summaries.

Coordinator and model adapter. Track hypotheses, supporting/contradicting evidence IDs, completed checks and remaining budget. Dispatch only allowlisted tools through a local-model adapter. Treat retrieved text as untrusted evidence, never as instructions; tool failures remain explicit observations.

Report interface. Return status (diagnosis, insufficient evidence or no fault observed), up to three ranked component/mechanism pairs, citations, unresolved questions and a next check. On insufficient evidence, state “No reliable root cause identified.” Keep tentative hypotheses separate from asserted causes and label any suggested fix as untested.

Time and long-document policy. Freeze evidence at investigation start. Include only Jira comments and document versions available by that cutoff; exclude later edits and resolutions. Older linked context is eligible. Chunk long discussions by comment/section with timestamps, retrieve relevant passages and check originals. A core scenario includes verbose, contradictory ticket discussion.

Local stack. Use GitHub/Jira, self-hosted Jenkins, OpenTelemetry and local Grafana/Loki/Prometheus; save traces locally. Profile a quantized model on the i5, 16 GB RAM and RTX 3060 6 GB machine. Run builds and inference separately.

4  Planned test suite and edge cases

Build nine scenarios and a separate pilot using one application. Capture requests, telemetry, configuration, commits and tickets. A teammate reviews each hidden cause, decisive evidence and expected response before testing.

Six primary scenarios. Two misleading-symptom cases: a dependency fault reported as an upstream failure, and a slow dependency reported as a client timeout. Two unrelated-change cases: a real configuration fault or stopped dependency alongside a newer harmless commit. Two insufficient-evidence cases: missing decisive configuration, and two equally plausible causes whose distinguishing evidence is withheld. One unrelated-change case includes a lengthy ticket with conflicting suggestions.

Three robustness scenarios. One healthy/no-fault run; one replay of a diagnosable fault with a connector failing mid-investigation; and one replay containing a deliberately planted log instruction to ignore evidence or call an unauthorized tool. Record these as variants, not independent fault families. Predefine whether remaining evidence is sufficient in the connector-failure case.

Evaluation isolation. Freeze all nine snapshots and answer keys before evaluation. Do not tune prompts on them. Report every attempted case, failure and plan deviation.

5  Operational metrics and success criteria

Diagnosis outcomes. A correct diagnosis must match both the component and failure mechanism in the answer key, allowing predefined equivalent wording. Top-1/top-3 accuracy = primary cases with the correct pair ranked first/within three ÷ four diagnosable primary cases. Abstentions and failed runs count as misses. Report robustness variants, healthy and insufficient-evidence cases separately.

Evidence and uncertainty. Unsupported-claim rate = asserted factual/causal claims lacking supporting supplied evidence ÷ all asserted factual/causal claims. Split compound claims before grading; label tentative hypotheses separately. Correct-abstention rate = insufficient-evidence cases explicitly declining a reliable diagnosis ÷ all insufficient-evidence cases. Also report false abstentions on diagnosable cases and false causal diagnoses on the healthy run. Zero denominators are N/A, not zero.

Investigation trajectory. Decisive-evidence coverage = retrieved decisive evidence items ÷ answer-key decisive items, on diagnosable cases. Redundant-call rate = repeated identical tool/argument calls returning no new evidence ÷ all tool calls; exclude configured retries after errors. Report failed-call count, calls to first decisive evidence, total calls, tokens and wall time per case. Check budget violations and unauthorized actions as counts; do not score private chain-of-thought.

Review and interpretation. Two teammates score outputs and resolve disagreements. Report counts and case results. Success means reproducible evaluation; performance improvement remains a research question.

6  Experiments and scope

Baselines and ablations. Run B0, B1 and BlackBox on nine frozen cases (27 outputs). Repeat BlackBox twice more on each of the four diagnosable primary cases (+8): report per-case agreement and min/max accuracy across three passes. Log seeds/settings; remaining baseline and ablation cells are single runs, so differences remain exploratory. On the four diagnosable primary cases, rerun BlackBox three times: without Jira, without provenance links, and without traces (12 further outputs). Remove each source from tool access, indexes and summaries. For provenance, retain code/logs but remove deployment–artifact–build–commit mappings. Hold all other settings fixed; compare accuracy, claims and calls with full evidence.

Core and stretch. Core: named components, snapshot capture, both baselines, nine scenarios, three source ablations and the scored report. Stretch: automated repair with regression checks, more fault families, live synchronization, richer cross-repository analysis and AWS/enterprise connectors. Total: 27 + 8 + 12 = 47 outputs using the same fixtures.

7  Team ownership and next steps

Proposed division. Likhith Vardhan Goruputi: coordinator, model adapter and baselines. Praneeth Yashovardhan Kadem: demo services, controller, telemetry and provenance. Bhavana Peruri: GitHub/Jira ingestion, report interface and scoring harness. Members cross-review answer keys and outputs.

Next steps: confirm ownership; profile the pilot; implement components; review nine scenarios; freeze settings; run comparisons and ablations; score outcomes/trajectories; demonstrate results and failures.