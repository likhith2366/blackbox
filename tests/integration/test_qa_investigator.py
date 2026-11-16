"""Agent 4 QA: investigator guardrails under adversarial scripted models and broken backends.

Test doubles only (tests/integration/fixtures/qa_doubles.py, labelled TEST FIXTURE).
Ollama is blocked by conftest.py. xfail(strict=True) tests document defects in Agent 1's
files; patches are proposed in docs/QA_FINDINGS.md (finding ids QA-Fnn).
"""
from __future__ import annotations

import json

import pytest

from qa_doubles import (REPORT_DIAG, REPORT_INSUFF, ROUND, H, ModelError, QABackend,
                        ScriptedAdapter)

from blackbox.investigator.common import check_ids, resolve_citation
from blackbox.investigator.coordinator import Coordinator
from blackbox.model.adapter import count_tokens
from blackbox.schemas import RunConfig


def run(outputs, backend=None, **cfg):
    adapter = ScriptedAdapter(outputs)
    c = Coordinator(backend or QABackend(), RunConfig(**cfg), adapter=adapter)
    return c.run(), adapter, c


def executed(out):
    return [r for s in out.steps for r in s.tool_results if r.status not in ("denied", "budget_exceeded")]


# ======================================================================= citations
def test_padded_or_extended_id_is_not_recovered():
    """Regression for QA-F01 (fixed by Agent 1 with an id-boundary check)."""
    assert resolve_citation("log:12", {"log:1"}) == (None, "invalid")
    assert resolve_citation("log:9f2c41ff", {"log:9f2c41"})[0] is None
    assert resolve_citation("metric:m1x", {"metric:m1"})[0] is None


# regression for QA-F01b (fixed by Agent 1 at 9335b82)
def test_two_ids_where_one_prefixes_the_other_are_ambiguous():
    assert resolve_citation("log:a1, log:a12", {"log:a1", "log:a12"}) == (None, "ambiguous")


def test_comment_id_embedded_in_text_still_resolves():
    """Guard for the QA-F01b patch: a ticket id that prefixes a comment id must not cause ambiguity."""
    seen = {"jira:DEMO-12", "jira:DEMO-12#c3"}
    assert resolve_citation("see jira:DEMO-12#c3 (Lena)", seen) == ("jira:DEMO-12#c3", "embedded")
    assert resolve_citation("log:a12 slow", {"log:a1", "log:a12"}) == ("log:a12", "embedded")


def test_recovery_keeps_raw_text_and_logs_every_mapping():
    seen = {"log:a1", "metric:m1", "span:a1"}
    norm, audit = [], []
    valid, invalid = check_ids(["[log:a1]", "m1", "a1", "log:zz", "metric:m1: pool 20/20"], seen, norm, audit, "h1")
    assert valid == ["log:a1", "metric:m1"]
    assert invalid == ["a1", "log:zz"]           # a1 is a suffix of two ids: refused
    assert {(c.raw, c.method) for c in audit} == {("[log:a1]", "exact"), ("m1", "suffix"), ("a1", "ambiguous"),
                                                 ("log:zz", "invalid"), ("metric:m1: pool 20/20", "embedded")}
    assert all(c.resolved is None for c in audit if c.method in ("ambiguous", "invalid"))
    assert norm == ["m1 -> metric:m1", "metric:m1: pool 20/20 -> metric:m1"]


def test_ledger_cannot_cite_evidence_from_the_same_round():
    """Citations are checked before this round's tools run, so a model cannot pre-cite ids."""
    out, _, _ = run([ROUND([("search_logs", {})], hyps=[H(sup=["log:qa1", "log:qa2"])], decision="continue"),
                     ROUND([], decision="insufficient_evidence"), REPORT_INSUFF])
    assert out.steps[0].hypotheses_after[0].supporting == []
    assert set(out.steps[0].invalid_citations) == {"log:qa1", "log:qa2"}


# ======================================================================= budgets
def test_cached_repeats_consume_budget_and_cap_total_calls():
    same = [("search_logs", {"service": "x"})] * 3
    out, _, _ = run([ROUND(same)] * 6 + [REPORT_INSUFF])
    assert out.tool_calls_used == 12 and len(executed(out)) == 12
    assert out.budget_violations >= 3        # round 5 refused, then stop_budget
    assert sum(len(s.duplicate_calls) for s in out.steps) == 11


def test_denied_calls_never_execute_and_take_a_round_slot():
    b = QABackend()
    out, _, _ = run([ROUND([("run_shell", {"cmd": "rm -rf /"}), ("delete_logs", {}), ("restart_service", {}),
                            ("search_logs", {})]),
                     ROUND([], decision="insufficient_evidence"), REPORT_INSUFF], backend=b)
    st = [r.status for r in out.steps[0].tool_results]
    assert st == ["denied", "denied", "denied", "budget_exceeded"]
    assert b.calls == [] and out.unauthorized_executed == 0 and len(out.unauthorized_attempts) == 3
    assert out.tool_calls_used == 0


def test_invalid_argument_calls_consume_budget():
    bad = [("get_metrics", {"service": ""}), ("search_logs", {"limit": 999}), ("search_logs", {"bogus": 1})]
    out, _, _ = run([ROUND(bad)] * 4 + [ROUND([], decision="insufficient_evidence"), REPORT_INSUFF])
    assert all(r.status == "invalid_args" for r in executed(out))
    assert out.tool_calls_used == 12 and len(executed(out)) == 12


def test_malformed_rounds_do_not_reset_or_extend_limits():
    script = ["not json", ROUND([("search_logs", {"q": 1})] * 3), "{oops", ROUND([("search_logs", {})] * 3),
              "x", ROUND([("get_metrics", {"service": "p"})] * 3)] + ["nope"] * 10
    out, adapter, _ = run(script)
    assert out.rounds_used <= 6
    assert out.tool_calls_used <= 12
    per_round = {}
    for s in out.steps:
        per_round[s.round] = sum(1 for r in s.tool_results if r.status not in ("denied", "budget_exceeded"))
    assert max(per_round.values()) <= 3


def test_adversarial_script_never_exceeds_any_limit():
    many = [("search_logs", {"query": str(i)}) for i in range(8)] + [("get_metrics", {"service": "p"})] * 4
    out, _, _ = run([ROUND(many)] * 10 + [REPORT_INSUFF])
    per_round = {}
    for r in executed(out):
        rnd = int(r.call_id[1:].split("c")[0])
        per_round[rnd] = per_round.get(rnd, 0) + 1
    assert len(executed(out)) <= 12 and max(per_round.values()) <= 3 and out.rounds_used <= 6


# ======================================================================= robustness
def test_timeout_raise_and_connector_failure_become_observations():
    for backend, want in [(QABackend(slow=1.5), "timeout"), (QABackend(mode="raise"), "error"),
                          (QABackend(fail_after=0), "error")]:
        out, _, _ = run([ROUND([("search_logs", {})]), ROUND([], decision="insufficient_evidence"), REPORT_INSUFF],
                        backend=backend, tool_timeout_s=0.3)
        assert out.steps[0].tool_results[0].status == want
        assert out.status == "completed" and out.report.status == "insufficient_evidence"


def test_connector_without_method_is_an_error_not_a_crash():
    out, _, _ = run([ROUND([("get_trace", {"trace_id": "t1"})]), ROUND([], decision="insufficient_evidence"),
                     REPORT_INSUFF])
    assert out.steps[0].tool_results[0].status == "error"


@pytest.mark.parametrize("mode", ["none", "dict", "bad_record"])
# regression for QA-F03 (fixed by Agent 1 at 9335b82)
def test_malformed_backend_result_is_an_error_observation(mode):
    out, _, _ = run([ROUND([("search_logs", {})]), ROUND([], decision="insufficient_evidence"), REPORT_INSUFF],
                    backend=QABackend(mode=mode))
    assert out.steps[0].tool_results[0].status == "error"


@pytest.mark.parametrize("bad", [
    {"hypotheses": ["payment-svc is broken"], "tool_calls": [], "decision": "continue", "rationale": ""},
    {"hypotheses": [H()], "tool_calls": "search_logs", "decision": "continue", "rationale": ""},
    {"hypotheses": [H()], "tool_calls": ["search_logs"], "decision": "continue", "rationale": ""},
])
# regression for QA-F04 (fixed by Agent 1 at 9335b82)
def test_wrongly_typed_model_json_does_not_crash(bad):
    out, _, _ = run([bad, ROUND([("search_logs", {})]), ROUND([], decision="insufficient_evidence"), REPORT_INSUFF])
    assert out.status in ("completed", "failed")


# regression for QA-F04 (fixed by Agent 1 at 9335b82)
def test_non_object_args_are_invalid_not_rewritten():
    out, _, _ = run([{"hypotheses": [H()], "tool_calls": [{"tool": "search_logs", "args": "payment-svc"}],
                      "decision": "continue", "rationale": ""},
                     ROUND([], decision="insufficient_evidence"), REPORT_INSUFF])
    assert out.steps[0].tool_results[0].status == "invalid_args"


def test_transport_error_in_first_round_fails_the_run():
    out, _, _ = run([ModelError("model request failed: connection refused"), ModelError("down")])
    assert out.status == "failed" and out.steps[0].stop_decision == "stop_error"


# regression for QA-F02 (fixed by Agent 1 at 9335b82)
def test_transport_error_mid_run_is_not_a_completed_diagnosis():
    out, _, _ = run([ROUND([("search_logs", {}), ("get_metrics", {"service": "payment-svc"})]),
                     ROUND([("list_deployments", {})], hyps=[H(sup=["log:qa1", "metric:qa1"])]),
                     ModelError("model request failed: connection refused"), ModelError("down"),
                     ModelError("down")])
    assert out.steps[-1].stop_decision == "stop_error"
    assert not (out.status == "completed" and out.report and out.report.status == "diagnosis")


def test_run_timeout_is_checked_between_rounds_only():
    """Characterisation: run_timeout_s is not a hard bound; the round in flight and the report
    call still run. With defaults the overshoot can reach ~4 x model_timeout_s (QA-F06)."""
    adapter = ScriptedAdapter([ROUND([("search_logs", {})])] * 6 + [REPORT_INSUFF], sleep=0.25)
    out = Coordinator(QABackend(), RunConfig(run_timeout_s=0.3), adapter=adapter).run()
    assert out.steps[-1].stop_decision == "stop_timeout" or len(out.steps) <= 2
    assert out.wall_seconds > 0.3


# ======================================================================= cache isolation
def test_coordinator_cache_is_per_run():
    b = QABackend()
    script = lambda: [ROUND([("search_logs", {"service": "x"})]), ROUND([], decision="insufficient_evidence"),  # noqa: E731
                      REPORT_INSUFF]
    Coordinator(b, RunConfig(), adapter=ScriptedAdapter(script())).run()
    Coordinator(b, RunConfig(), adapter=ScriptedAdapter(script())).run()
    assert b.calls.count("search_logs") == 2


def test_failed_calls_are_not_cached():
    b = QABackend(fail_after=0)
    run([ROUND([("search_logs", {})]), ROUND([("search_logs", {})]), ROUND([], decision="insufficient_evidence"),
         REPORT_INSUFF], backend=b)
    assert b.calls.count("search_logs") == 2


# ======================================================================= context accounting
def _chat_template_tokens(messages) -> int:
    from blackbox.model.adapter import _tokenizer
    tok = _tokenizer()
    ids = tok.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, enable_thinking=False)
    return len(ids)


def test_round_estimate_matches_exact_rendered_prompt():
    long_text = "QA FIXTURE " + "pool wait timeout acquiring connection " * 12
    out, adapter, _ = run([ROUND([("search_logs", {}), ("get_metrics", {"service": "p"})]),
                           ROUND([("list_deployments", {})], hyps=[H(sup=["log:qa1", "metric:qa1"])]),
                           ROUND([], decision="insufficient_evidence"), REPORT_INSUFF],
                          backend=QABackend(n_logs=25, log_text=long_text))
    for step, msgs in zip(out.steps, adapter.messages):
        ctx = step.context
        exact = count_tokens(msgs[0]["content"]) + count_tokens(msgs[1]["content"])
        assert ctx["prompt_tokens_est"] == exact + 20
        templ = _chat_template_tokens(msgs)
        assert abs(ctx["prompt_tokens_est"] - templ) <= 25, (ctx["prompt_tokens_est"], templ)
        assert ctx["prompt_tokens_est"] + ctx["num_predict"] <= ctx["num_ctx"]
        for k in ("num_ctx", "num_predict", "fixed_tokens"):
            assert k in ctx
    rep_msgs = adapter.messages[len(out.steps)]
    rc = out.context["report_call"]
    assert rc["prompt_tokens_est"] == count_tokens(rep_msgs[0]["content"]) + count_tokens(rep_msgs[1]["content"]) + 20


def test_notebook_degrades_to_fit_small_context():
    long_text = "QA FIXTURE " + "pool wait timeout acquiring connection " * 14
    out, adapter, _ = run([ROUND([("search_logs", {})]), ROUND([("get_metrics", {"service": "p"})]),
                           ROUND([], decision="insufficient_evidence"), REPORT_INSUFF],
                          backend=QABackend(n_logs=30, log_text=long_text), num_ctx=4096, num_predict=600)
    for step in out.steps:
        assert step.context["prompt_tokens_est"] + 600 <= 4096, step.context
    assert any(s.context.get("records_one_line", 0) or s.context.get("records_omitted", 0) for s in out.steps)


# regression for QA-F05 (fixed by Agent 1 at 9335b82)
def test_prompt_never_exceeds_window_even_with_large_fixed_part():
    notes = "QA FIXTURE long operator notes. " * 700
    out, _, _ = run([ROUND([("search_logs", {})]), ROUND([], decision="insufficient_evidence"), REPORT_INSUFF],
                    backend=QABackend(notes=notes, n_logs=5), num_ctx=4096, num_predict=600)
    for step in out.steps:
        assert step.context["prompt_tokens_est"] + 600 <= 4096
