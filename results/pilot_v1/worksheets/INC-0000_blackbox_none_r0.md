# Review worksheet: INC-0000_blackbox_none_r0

- Incident: `INC-0000` (scenario 0, pilot)
- System: `blackbox`  ablation: `none`  repeat: 0
- Harness status: **completed**

All automated verdicts below are PROVISIONAL. Reviewer fields are intentionally blank; two reviewers fill them independently, then resolve disagreements.

## Expected answer (evaluator-private)

- Expected status: `diagnosis`
- Component (accepted): checkout-svc shared HTTP client (libs/httpclient retry policy), checkout-svc, libs/httpclient, shared http client, httpclient retry policy, checkout payment client
- Mechanism (accepted): retry amplification: shared client (httpclient 3.2.0 in checkout-svc 1.9.0) now retries timeouts/5xx up to 4 times with a 250ms per-attempt timeout and no backoff; slow large-charge calls are abandoned and repeated, payment calls multiply (~5/s -> ~20/s), and the concurrent duplicate charge attempts make payment-svc fail with SQLite 'database is locked' and UNIQUE-constraint errors (HTTP 500), so orders fail. The payment-side DB errors are a consequence, not the root cause, retry amplification, retry storm, aggressive retries with short timeout, per-attempt timeout too short (250ms) with retries, retrying timeouts multiplies downstream calls, retries overload payment-svc database causing lock contention
- Decisive evidence: commit:7addfee, deploy:d-511, metric:17f535fce8, log:5a0955ffc1
- Distractors: metric:75680f44c4

## System report

- Status: `insufficient_evidence`
- Headline: No reliable root cause identified.
- Unresolved: Why did payment-svc timeout events occur?
- Next check: Check payment-svc logs for timeout events around 2026-11-20T02:04:02Z
- System-reported invalid citations: h1

## Diagnosis verdict

- Automated (provisional): top-1 no, top-3 no, outcome correct no
- Reviewer A: ______  Reviewer B: ______  Resolved: ______

## Claims

Supplied evidence for support checks: `evidence_seen` (11 ids). Split compound claims before grading.

### c1 (factual)

checkout-svc error rate increased to 0.4167 at 2026-11-20T02:04:42.727Z

- Automated (provisional): cited, pending semantic review
- Cited evidence:
    - [metric:83cc53b310] checkout-svc error_rate | checkout-svc error_rate (5.0s points, 2026-11-20T02:03:04.072Z .. 2026-11-20T02:05:37.845Z): min=0 max=0.9583 last=0.6667 02:03:04 0; 02:03:09 0; 02:03:14 0; 02:03:19 0; 02:03:24 0; 02:03:29 0; 02:03:34 0; 02:03:39 0; 02:03:44 0; 02:03:49 0; 02:03:54 0; 02:03:59 0; 02:04:00 0; 02:04:07 0; 02:04:12 0; 02:04:17 0.8095; 02:04:22 0.3182; 02:04:27 0; 02:04:32 0.2667; 02:04:37 0.9583; 02:04:42 0.4167; 02:04:47 0.7308; 02:04:52 0.6667; 02:04:57 0.6333; 02:05:02 0.6; 02:05:07 0 [...]
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

### c2 (factual)

checkout-svc POST /orders requests are failing with timeouts

- Automated (provisional): cited, pending semantic review
- Cited evidence:
    - [trace:a1bc8a0dcd88d6ec08be5b6a64400a5c] checkout-svc POST /orders 945.83ms error | trace a1bc8a0dcd88d6ec08be5b6a64400a5c root=checkout-svc POST /orders start=2026-11-20T02:04:02.725Z duration=945.83ms status=error spans=21 services=checkout-svc,payment-svc - checkout-svc POST /orders 945.83ms ok {"http.route":"/orders","order.id":"ord-0300318","http.status_code":201} - checkout-svc POST /charge 258.02ms error (timeout after 250ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":250} - payment-svc db.pool.acquire 0.0m [...]
    - [trace:2ae8fe01a9cb14deaa5754d3c82bde50] checkout-svc POST /orders 729.41ms error | trace 2ae8fe01a9cb14deaa5754d3c82bde50 root=checkout-svc POST /orders start=2026-11-20T02:04:02.936Z duration=729.41ms status=error spans=18 services=checkout-svc,payment-svc - checkout-svc POST /orders 729.41ms ok {"http.route":"/orders","order.id":"ord-0300320","http.status_code":201} - checkout-svc POST /charge 262.14ms error (timeout after 250ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":250} - payment-svc POST /charge 721.88m [...]
    - [trace:0aa1f6ffc7264a85d034f99acbe45571] checkout-svc POST /orders 688.8ms error | trace 0aa1f6ffc7264a85d034f99acbe45571 root=checkout-svc POST /orders start=2026-11-20T02:04:02.985Z duration=688.8ms status=error spans=18 services=checkout-svc,payment-svc - checkout-svc POST /charge 262.24ms error (timeout after 250ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":250} - checkout-svc POST /orders 688.8ms ok {"http.route":"/orders","order.id":"ord-0300321","http.status_code":201} - payment-svc db.pool.acquire 0.0ms o [...]
    - [trace:9f037d1aa8d52a8db31719a2c622a500] checkout-svc POST /orders 439.01ms error | trace 9f037d1aa8d52a8db31719a2c622a500 root=checkout-svc POST /orders start=2026-11-20T02:04:03.413Z duration=439.01ms status=error spans=18 services=checkout-svc,payment-svc - checkout-svc POST /orders 439.01ms ok {"http.route":"/orders","order.id":"ord-0300328","http.status_code":201} - checkout-svc POST /charge 253.12ms error (timeout after 250ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":250} - payment-svc POST /charge 404.34m [...]
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

### c3 (tentative)

checkout-svc error rate is correlated with payment-svc timeout events

- Automated (provisional): tentative (not counted)
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

## Report citation audit (objective: how each written citation resolved)

| where | as written | resolved | method |
|---|---|---|---|
| claim c1 | `metric:83cc53b310` | metric:83cc53b310 | exact |
| claim c2 | `trace:a1bc8a0dcd88d6ec08be5b6a64400a5c` | trace:a1bc8a0dcd88d6ec08be5b6a64400a5c | exact |
| claim c2 | `trace:2ae8fe01a9cb14deaa5754d3c82bde50` | trace:2ae8fe01a9cb14deaa5754d3c82bde50 | exact |
| claim c2 | `trace:0aa1f6ffc7264a85d034f99acbe45571` | trace:0aa1f6ffc7264a85d034f99acbe45571 | exact |
| claim c2 | `trace:9f037d1aa8d52a8db31719a2c622a500` | trace:9f037d1aa8d52a8db31719a2c622a500 | exact |
| claim c3 | `h1` | - | invalid |

## Trajectory (objective)

- Tool calls requested: 6; accepted (budget consumed): 6 = backend executions 3 + cache hits 3; refused: 0
- Redundant: 3 (cache hits included); failed: 0
- Citations, ledger: 5 (exact 5, recovered 0, ambiguous 0, invalid 0); report: 6 (exact 5, recovered 0, ambiguous 0, invalid 1)
- Context: max prompt tokens 4851; truncated by server (reported) no; truncation (evaluator estimate) no; notebook records omitted (max) 0; baseline omitted ids N/A
- Orchestration interventions (coordinator, not model): 6 (first-tool 1, diagnosis gate 5)
- Decisive coverage: 0.00 (0/4); calls to first decisive: N/A
- Budget violations: 0; unauthorized attempted/executed: 0/0
- Tokens: 25379; wall s: 292.09
