# Review worksheet: INC-0000_b0_none_r0

- Incident: `INC-0000` (scenario 0, pilot)
- System: `b0`  ablation: `none`  repeat: 0
- Harness status: **completed**

All automated verdicts below are PROVISIONAL. Reviewer fields are intentionally blank; two reviewers fill them independently, then resolve disagreements.

## Expected answer (evaluator-private)

- Expected status: `diagnosis`
- Component (accepted): checkout-svc shared HTTP client (libs/httpclient retry policy), checkout-svc, libs/httpclient, shared http client, httpclient retry policy, checkout payment client
- Mechanism (accepted): retry amplification: shared client (httpclient 3.2.0 in checkout-svc 1.9.0) now retries timeouts/5xx up to 4 times with a 250ms per-attempt timeout and no backoff; slow large-charge calls are abandoned and repeated, payment calls multiply (~5/s -> ~20/s), and the concurrent duplicate charge attempts make payment-svc fail with SQLite 'database is locked' and UNIQUE-constraint errors (HTTP 500), so orders fail. The payment-side DB errors are a consequence, not the root cause, retry amplification, retry storm, aggressive retries with short timeout, per-attempt timeout too short (250ms) with retries, retrying timeouts multiplies downstream calls, retries overload payment-svc database causing lock contention
- Decisive evidence: commit:7addfee, deploy:d-511, metric:17f535fce8, log:5a0955ffc1
- Distractors: metric:75680f44c4

## System report

- Status: `diagnosis`
- Headline: Most likely cause: payment-svc - database lock contention
- Cause #1: **payment-svc** / database lock contention (confidence high; cites log:36351de666, log:10c300e979, log:a342b25a91, log:6c3e98399b, log:92a48e970a, log:5c32511142)
- Cause #2: **checkout-svc** / retrying payment service with HTTP 500 (confidence medium; cites log:92a48e970a, log:22448160c5, log:047f543746)
- Cause #3: **payment-svc** / database lock contention (confidence low; cites log:5c32511142, log:3097c3e3f0, log:d260c3ba11, log:2635495b50, log:61db69831d, log:7b3451becd)
- Unresolved: Why is the database locked?
- Next check: Check payment-svc database connection pool configuration and monitor database lock events
- Suggested fix (untested): (untested) Increase payment-svc database connection pool size

## Diagnosis verdict

- Automated (provisional): top-1 no, top-3 no, outcome correct no
- Reviewer A: ______  Reviewer B: ______  Resolved: ______

## Claims

Supplied evidence for support checks: `evidence_budget.included_ids` (35 ids). Split compound claims before grading.

### c1 (factual)

payment-svc database is locked for multiple orders

- Automated (provisional): cited, pending semantic review
- Cited evidence:
    - [log:36351de666] ERROR payment-svc: charge failed | 2026-11-20T02:05:41.524Z ERROR payment-svc charge failed {"trace_id":"a566c26a4cd86a23206d8bd78ffae6f0","span_id":"5266d5a6d82aedb5","order_id":"ord-0300797","error":"OperationalError: database is locked"}
    - [log:10c300e979] ERROR payment-svc: charge failed | 2026-11-20T02:05:41.595Z ERROR payment-svc charge failed {"trace_id":"a566c26a4cd86a23206d8bd78ffae6f0","span_id":"5c7a64cf03b60be3","order_id":"ord-0300797","error":"OperationalError: database is locked"}
    - [log:a342b25a91] ERROR payment-svc: charge failed | 2026-11-20T02:05:41.646Z ERROR payment-svc charge failed {"trace_id":"a566c26a4cd86a23206d8bd78ffae6f0","span_id":"9f6dbbf2e74ab167","order_id":"ord-0300797","error":"OperationalError: database is locked"}
    - [log:6c3e98399b] ERROR payment-svc: charge failed | 2026-11-20T02:05:41.698Z ERROR payment-svc charge failed {"trace_id":"a566c26a4cd86a23206d8bd78ffae6f0","span_id":"7fec3a942e3ba04a","order_id":"ord-0300797","error":"OperationalError: database is locked"}
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

### c2 (factual)

checkout-svc retries payment service when payment fails

- Automated (provisional): cited, pending semantic review
- Cited evidence:
    - [log:92a48e970a] ERROR checkout-svc: payment service returned HTTP 500 | 2026-11-20T02:05:41.700Z ERROR checkout-svc payment service returned HTTP 500 {"trace_id":"a566c26a4cd86a23206d8bd78ffae6f0","span_id":"aa2ef9e099142b12","order_id":"ord-0300797","attempts":5,"body":"{\"error\": \"internal error\"}"}
    - [log:22448160c5] ERROR checkout-svc: payment service returned HTTP 500 | 2026-11-20T02:05:41.908Z ERROR checkout-svc payment service returned HTTP 500 {"trace_id":"50d8982545f8d1ce29c03789e189a725","span_id":"131e71c550233440","order_id":"ord-0300799","attempts":5,"body":"{\"error\": \"internal error\"}"}
    - [log:047f543746] ERROR checkout-svc: payment service returned HTTP 500 | 2026-11-20T02:05:42.074Z ERROR checkout-svc payment service returned HTTP 500 {"trace_id":"4a1241450af604f00900e68422b13c11","span_id":"d3e3d32bd00f9714","order_id":"ord-0300801","attempts":5,"body":"{\"error\": \"internal error\"}"}
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

### c3 (factual)

payment-svc database lock errors persist for multiple orders

- Automated (provisional): cited, pending semantic review
- Cited evidence:
    - [log:5c32511142] ERROR payment-svc: charge failed | 2026-11-20T02:05:41.803Z ERROR payment-svc charge failed {"trace_id":"50d8982545f8d1ce29c03789e189a725","span_id":"e87dfcdf6e3151d5","order_id":"ord-0300799","error":"OperationalError: database is locked"}
    - [log:3097c3e3f0] ERROR payment-svc: charge failed | 2026-11-20T02:05:41.822Z ERROR payment-svc charge failed {"trace_id":"50d8982545f8d1ce29c03789e189a725","span_id":"378cbf54781bac4c","order_id":"ord-0300799","error":"OperationalError: database is locked"}
    - [log:d260c3ba11] ERROR payment-svc: charge failed | 2026-11-20T02:05:42.093Z ERROR payment-svc charge failed {"trace_id":"882b2e5fbc65c82c6bba61bc6e967c82","span_id":"36b99f50d8476668","order_id":"ord-0300802","error":"OperationalError: database is locked"}
    - [log:2635495b50] ERROR payment-svc: charge failed | 2026-11-20T02:05:42.118Z ERROR payment-svc charge failed {"trace_id":"882b2e5fbc65c82c6bba61bc6e967c82","span_id":"ca3c2208e5e03c29","order_id":"ord-0300802","error":"OperationalError: database is locked"}
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

## Report citation audit (objective: how each written citation resolved)

| where | as written | resolved | method |
|---|---|---|---|
| cause1 | `log:36351de666` | log:36351de666 | exact |
| cause1 | `log:10c300e979` | log:10c300e979 | exact |
| cause1 | `log:a342b25a91` | log:a342b25a91 | exact |
| cause1 | `log:6c3e98399b` | log:6c3e98399b | exact |
| cause1 | `log:92a48e970a` | log:92a48e970a | exact |
| cause1 | `log:5c32511142` | log:5c32511142 | exact |
| cause2 | `log:92a48e970a` | log:92a48e970a | exact |
| cause2 | `log:22448160c5` | log:22448160c5 | exact |
| cause2 | `log:047f543746` | log:047f543746 | exact |
| cause3 | `log:5c32511142` | log:5c32511142 | exact |
| cause3 | `log:3097c3e3f0` | log:3097c3e3f0 | exact |
| cause3 | `log:d260c3ba11` | log:d260c3ba11 | exact |
| cause3 | `log:2635495b50` | log:2635495b50 | exact |
| cause3 | `log:61db69831d` | log:61db69831d | exact |
| cause3 | `log:7b3451becd` | log:7b3451becd | exact |
| claim c1 | `log:36351de666` | log:36351de666 | exact |
| claim c1 | `log:10c300e979` | log:10c300e979 | exact |
| claim c1 | `log:a342b25a91` | log:a342b25a91 | exact |
| claim c1 | `log:6c3e98399b` | log:6c3e98399b | exact |
| claim c2 | `log:92a48e970a` | log:92a48e970a | exact |
| claim c2 | `log:22448160c5` | log:22448160c5 | exact |
| claim c2 | `log:047f543746` | log:047f543746 | exact |
| claim c3 | `log:5c32511142` | log:5c32511142 | exact |
| claim c3 | `log:3097c3e3f0` | log:3097c3e3f0 | exact |
| claim c3 | `log:d260c3ba11` | log:d260c3ba11 | exact |
| claim c3 | `log:2635495b50` | log:2635495b50 | exact |

## Trajectory (objective)

- Tool calls requested: 0; accepted (budget consumed): 0 = backend executions 0 + cache hits 0; refused: 0
- Redundant: 0 (cache hits included); failed: 0
- Citations, ledger: 0 (exact 0, recovered 0, ambiguous 0, invalid 0); report: 26 (exact 26, recovered 0, ambiguous 0, invalid 0)
- Context: max prompt tokens 6240; truncated by server (reported) no; truncation (evaluator estimate) no; notebook records omitted (max) N/A; baseline omitted ids 3210
- Orchestration interventions (coordinator, not model): 0 (first-tool 0, diagnosis gate 0)
- Decisive coverage: 0.00 (0/4); calls to first decisive: N/A
- Budget violations: 0; unauthorized attempted/executed: 0/0
- Tokens: 6944; wall s: 111.22
