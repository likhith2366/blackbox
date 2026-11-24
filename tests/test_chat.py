"""Chat assistant: tool use, answer verification against cited evidence. Scripted model, fake backend."""
from __future__ import annotations

from blackbox.investigator.chat import ChatSession, verify
from blackbox.schemas import EvidenceRecord, RunConfig

from tests.test_investigator_core import FakeBackend, ScriptedAdapter


def R(i, content):
    return EvidenceRecord(evidence_id=i, source="logs", content=content, title="")


EV = {"log:a1": R("log:a1", "ERROR payment-svc db pool acquire timed out after 1000ms"),
      "deploy:d-590": R("deploy:d-590", "deployment d-590 of payment-svc version 2.5.0 commit=c24dd44")}


def test_verified_answer():
    v = verify("payment-svc 2.5.0 (deploy:d-590) times out after 1000ms (log:a1).", ["log:a1", "deploy:d-590"], EV)
    assert v["verdict"] == "verified" and v["unsupported_tokens"] == []


def test_unsupported_detail_is_flagged():
    v = verify("d-590 shipped 2.6.0 and pool waits reach 3000ms.", ["deploy:d-590"], EV)
    assert v["verdict"] == "partly verified"
    assert "2.6.0" in v["unsupported_tokens"] and "3000ms" in v["unsupported_tokens"]


def test_unknown_citation_and_uncited_claims():
    assert verify("It was deploy:d-1.", ["deploy:d-1"], EV)["verdict"] == "not verified"
    assert verify("The pool leaked after 2.5.0.", [], EV)["verdict"] == "not verified"
    assert verify("I could not find enough evidence to tell.", [], EV)["verdict"] == "no evidence"


def test_session_uses_tools_then_answers_with_checked_citations():
    outputs = [
        {"tool_calls": [{"tool": "search_logs", "args": {"query": "pool"}}], "answer": None},
        {"tool_calls": [], "answer": "Pool waits of 900ms appear in log:a1.", "citations": ["log:a1"], "confidence": "medium"},
    ]
    s = ChatSession(FakeBackend(), RunConfig(), adapter=ScriptedAdapter(outputs))
    t = s.ask("why are orders failing?")
    assert t.tools and t.tools[0]["tool"] == "search_logs" and t.tools[0]["status"] == "ok"
    assert "log:a1" in s.evidence
    assert t.verification["verdict"] == "verified"
    d = s.to_dict()
    assert [x["role"] for x in d["turns"]] == ["user", "assistant"]


def test_session_refuses_identical_repeat_calls_and_stops_at_budget():
    call = {"tool_calls": [{"tool": "search_logs", "args": {"query": "pool"}}], "answer": None}
    s = ChatSession(FakeBackend(), RunConfig(), adapter=ScriptedAdapter([call] * 4 + ["not json"]))
    t = s.ask("anything?")
    assert len(t.tools) == 1                       # the repeats were not executed
    assert "could not reach an answer" in t.text
    assert t.verification["verdict"] == "no evidence"


def test_answer_only_step_after_the_tool_budget():
    call = {"tool_calls": [{"tool": "search_logs", "args": {"query": "pool"}}], "answer": None}
    final = {"answer": "Pool waits of 900ms (log:a1).", "citations": ["log:a1"], "confidence": "low"}
    s = ChatSession(FakeBackend(), RunConfig(), adapter=ScriptedAdapter([call] * 4 + [final]))
    t = s.ask("why?")
    assert t.citations == ["log:a1"] and t.verification["verdict"] == "verified"


def test_quoted_json_values_count_as_supported():
    ev = {"log:q": R("log:q", '{"msg":"timed out","attempts":5}')}
    assert verify("Calls show attempts:5 (log:q).", ["log:q"], ev)["unsupported_tokens"] == []


def test_absence_claims_need_a_query_of_that_source():
    v = verify("Errors come from payment-svc (log:a1). No recent deployments are visible.", ["log:a1"], EV,
               queried={"search_logs"})
    assert v["verdict"] == "partly verified" and v["unqueried_absence_claims"] == ["deployments"]
    v = verify("Errors come from payment-svc (log:a1). No recent deployments are visible.", ["log:a1"], EV,
               queried={"search_logs", "list_deployments"})
    assert v["verdict"] == "verified"


def test_chat_follows_a_deployment_in_the_window_to_its_commit():
    from tests.test_dev_options import DeployBackend
    outputs = [{"tool_calls": [{"tool": "list_deployments", "args": {}}], "answer": None},
               {"tool_calls": [], "answer": "d-9 shipped commit:abc1234.", "citations": ["deploy:d-9", "commit:abc1234"]}]
    b = DeployBackend()
    s = ChatSession(b, RunConfig(), adapter=ScriptedAdapter(outputs))
    t = s.ask("what changed?")
    assert "get_commit:abc1234" in b.calls and any(x.get("auto") for x in t.tools)
    assert t.verification["verdict"] == "verified"


def test_absence_claim_with_words_in_between_is_caught():
    v = verify("Timeouts (log:a1). No evidence of recent deployments or commits is available.", ["log:a1"], EV,
               queried={"search_logs"})
    assert set(v["unqueried_absence_claims"]) >= {"deployments", "commits"} and v["verdict"] == "partly verified"


def test_v_prefixed_versions_and_alert_details_are_not_flagged():
    ev = {"deploy:d-9": R("deploy:d-9", "deployment d-9 of checkout-svc version 1.9.0")}
    v = verify("checkout-svc v1.9.0 (deploy:d-9) broke orders; error_rate > 0.1 for 15s.", ["deploy:d-9"], ev,
               context="error_rate > 0.1 for 15s")
    assert v["unsupported_tokens"] == [] and v["verdict"] == "verified"
