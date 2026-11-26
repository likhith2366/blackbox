"""Component = the service that owns the cause, not the alerting service (development options synthesis + causal gate).

Records are built in memory; no model, no live stack. The cases mirror the live faults by shape only:
a caller-side change that makes the called service report errors, a fault inside the called service, a hypothesis
whose component disagrees with the change it cites, and a component that leaves ownership open.
"""
from __future__ import annotations

from blackbox.investigator import causal_gate, synthesis
from blackbox.schemas import EvidenceRecord, Hypothesis

DAY = "2026-11-26"


def metric(service, name, values, start="10:00:00"):
    h, m, s = map(int, start.split(":"))
    pts = []
    for n, v in enumerate(values):
        t = h * 3600 + m * 60 + s + n * 5
        pts.append(f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d} {v}")
    return EvidenceRecord(evidence_id=f"metric:{service}-{name}", source="metrics", service=service,
                          event_time=f"{DAY}T{start}.000Z", title=f"{service} {name}", attributes={"metric": name},
                          content=f"{service} {name} (5s steps): min={min(values)} max={max(values)}\n" + "; ".join(pts))


def log(eid, service, t, text):
    return EvidenceRecord(evidence_id=eid, source="logs", service=service, event_time=f"{DAY}T{t}.000Z",
                          title=f"{text.split()[0]} {service}: {' '.join(text.split()[1:])}", content=text,
                          attributes={"first_seen": f"{DAY}T{t}.000Z"})


# --- a caller-side change: checkout's shared HTTP client retries more with a shorter timeout -------------------------
CK_DEPLOY = EvidenceRecord(evidence_id="deploy:d-11", source="deployments", service="checkout-svc",
                           event_time=f"{DAY}T10:00:05.000Z", version="1.9.0", title="deployment of checkout-svc 1.9.0",
                           content="deployment d-11 of checkout-svc version 1.9.0 build=b-11 commit=c0ffee1")
CK_COMMIT = EvidenceRecord(
    evidence_id="commit:c0ffee1", source="commits", service="checkout-svc", event_time=f"{DAY}T10:00:01.000Z",
    title="retry transient payment failures in shared client",
    content="commit c0ffee1\nretry transient payment failures in shared client\nfiles: M libs/httpclient/policy.py\n"
            "M services/checkout/VERSION\n\ndiff --git a/libs/httpclient/policy.py b/libs/httpclient/policy.py\n"
            "@@ -1,4 +1,4 @@\n-DEFAULT_TIMEOUT_MS = 2000\n-MAX_RETRIES = 1\n+DEFAULT_TIMEOUT_MS = 250\n+MAX_RETRIES = 4")
PAY_ERR = metric("payment-svc", "error_rate", [0, 0, 0, 0, 0.2, 0.3, 0.25])
CK_ATTEMPTS = metric("checkout-svc", "upstream_attempts_per_s", [3, 3, 3, 3, 9, 12, 11])
PAY_DUP = log("log:dup", "payment-svc", "10:00:21", "ERROR charge failed IntegrityError duplicate order_id retries")
CALLER = {r.evidence_id: r for r in (CK_DEPLOY, CK_COMMIT, PAY_ERR, CK_ATTEMPTS, PAY_DUP)}

# --- a fault inside the called service: payment-svc stopped by an operator -------------------------------------------
STOP = log("log:stop", "payment-svc", "10:00:10", "INFO payment-svc shutdown requested operator request")
STOPPED = log("log:stopped", "payment-svc", "10:00:11", "INFO payment-svc stopped")
CK_TIMEOUT = log("log:to", "checkout-svc", "10:00:14", "ERROR checkout-svc payment call timed out after 2000ms")
CK_ERR = metric("checkout-svc", "error_rate", [0, 0, 0, 1, 1, 1, 1])
OLD_CK_DEPLOY = CK_DEPLOY.model_copy(update={"evidence_id": "deploy:d-9", "event_time": f"{DAY}T09:00:00.000Z"})
DOWNSTREAM = {r.evidence_id: r for r in (STOP, STOPPED, CK_TIMEOUT, CK_ERR, OLD_CK_DEPLOY)}


def hyp(component, mechanism, *ids):
    return Hypothesis(hypothesis_id="s1", component=component, mechanism=mechanism, status="supported",
                      confidence="medium", supporting=list(ids))


def gate(h, seen):
    ok, why, _ = causal_gate.check(h, seen, window_start=f"{DAY}T09:55:00.000Z")
    return ok, why


def test_caller_side_change_is_accepted_with_the_caller_as_component():
    h = hyp("checkout-svc (shared HTTP client)",
            "the client retry policy change sends up to 4 retries with a 250 ms timeout, multiplying calls to payment-svc",
            "deploy:d-11", "commit:c0ffee1", "metric:checkout-svc-upstream_attempts_per_s", "metric:payment-svc-error_rate",
            "log:dup")
    ok, why = gate(h, CALLER)
    assert ok, why


def test_caller_side_change_is_refused_when_the_component_is_the_alerting_service():
    # the live retry run: right change chain, component = the service that alerted
    h = hyp("payment-svc", "checkout retry logic sends more requests, increasing database load and errors",
            "deploy:d-11", "commit:c0ffee1", "metric:payment-svc-error_rate", "log:dup")
    ok, why = gate(h, CALLER)
    assert not ok and "change evidence missing" in why and "payment-svc" in why


def test_genuine_downstream_fault_is_accepted_with_the_downstream_service():
    h = hyp("payment-svc", "payment-svc was shut down by an operator request so checkout calls time out",
            "log:stop", "log:stopped", "log:to", "metric:checkout-svc-error_rate")
    ok, why = gate(h, DOWNSTREAM)
    assert ok, why


def test_downstream_fault_is_not_pinned_on_the_caller_by_an_older_caller_deploy():
    # a rule that always blamed the calling service would accept this; the gate must not
    h = hyp("checkout-svc", "a checkout-svc deployment broke payment calls",
            "deploy:d-9", "log:to", "metric:checkout-svc-error_rate")
    ok, why = gate(h, DOWNSTREAM)
    assert not ok and ("outside the incident window" in why or "mechanism" in why)


def test_component_that_disagrees_with_its_cited_change_is_refused():
    h = hyp("payment-svc", "a new client retry policy in payment-svc retries failed charges",
            "commit:c0ffee1", "metric:payment-svc-error_rate", "log:dup")
    ok, why = gate(h, CALLER)
    assert not ok and "change evidence missing" in why


def test_component_naming_two_services_is_ambiguous_and_refused():
    h = hyp("checkout-svc / payment-svc", "retries multiply calls between the services",
            "deploy:d-11", "commit:c0ffee1", "metric:payment-svc-error_rate", "log:dup")
    ok, why = gate(h, CALLER)
    assert not ok and "component ambiguous" in why


def test_component_that_names_no_service_is_refused():
    h = hyp("the network", "packets between the services are dropped", "log:dup", "metric:payment-svc-error_rate")
    ok, why = gate(h, CALLER)
    assert not ok and "component missing" in why


def test_shared_library_change_without_an_owning_service_abstains():
    lib_only = CK_COMMIT.model_copy(update={"evidence_id": "commit:11b0000", "service": None})
    seen = {**{k: v for k, v in CALLER.items() if k not in ("deploy:d-11", "commit:c0ffee1")}, lib_only.evidence_id: lib_only}
    h = hyp("checkout-svc", "the shared client retry policy change multiplies calls to payment-svc",
            "commit:11b0000", "metric:checkout-svc-upstream_attempts_per_s", "metric:payment-svc-error_rate")
    ok, why = gate(h, seen)
    assert not ok and "change evidence missing" in why


def test_synthesis_asks_for_the_owner_and_the_symptom_service_separately():
    item = synthesis.SCHEMA["properties"]["hypotheses"]["items"]
    assert {"component", "symptom_service"} <= set(item["required"])
    p = synthesis.PROMPT.lower()
    assert "owns the cause" in p and "not necessarily the alerting service" in p
    # both directions are described, so the instruction does not favour callers or called services
    assert "the component is the caller" in p and "makes that service the component" in p
    # no incident-specific content
    import re
    for word in ("retry", "retries", "payment", "checkout", "port", "pool", "database", "timeout"):
        assert not re.search(rf"{word}", p), word
