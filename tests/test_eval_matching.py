import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blackbox.schemas import AnswerKey, FinalReport, RankedCause  # noqa: E402
from eval.matching import (component_matches, match_rank, mechanism_matches,  # noqa: E402
                           tokens)


def key(**kw):
    base = dict(incident_id="INC-1", scenario_number=1, scenario_type="misleading_symptom",
                is_primary=True, diagnosable=True, expected_status="diagnosis",
                component="payment-svc", mechanism="connection pool exhaustion",
                accepted_components=["payment service"],
                accepted_mechanisms=["db connection pool exhausted"])
    base.update(kw)
    return AnswerKey(**base)


def report(status="diagnosis", causes=()):
    return FinalReport(incident_id="INC-1", system="blackbox", status=status, headline="h",
                       ranked_causes=[RankedCause(rank=i + 1, component=c, mechanism=m)
                                      for i, (c, m) in enumerate(causes)])


def test_tokens_normalise():
    assert tokens("Payment-SVC: Pools!") == ["payment", "svc", "pool"]


def test_component_contiguous_and_accepted():
    k = key()
    assert component_matches("payment-svc connection layer", ["payment-svc"])
    assert component_matches("The Payment Service", ["payment service"])
    assert not component_matches("checkout-svc", ["payment-svc", "payment service"])
    assert not component_matches("svc payment", ["payment-svc"])  # order matters
    assert not component_matches("anything", [])
    assert k.accepted_components == ["payment service"]


def test_mechanism_phrase_or_all_content_tokens():
    acc = ["connection pool exhaustion", "db connection pool exhausted"]
    assert mechanism_matches("exhaustion of the connection pool", ["connection pool exhaustion"])
    assert mechanism_matches("the db connection pool was exhausted under load", acc)
    assert not mechanism_matches("slow DNS resolution", acc)
    assert not mechanism_matches("connection refused", acc)


def test_match_rank_requires_both_and_diagnosis_status():
    k = key()
    r = report(causes=[("checkout-svc", "timeout"),
                       ("payment-svc", "connection pool exhaustion")])
    assert match_rank(r, k) == 2
    # component right, mechanism wrong -> no match
    assert match_rank(report(causes=[("payment-svc", "disk full")]), k) is None
    # abstention listing the right cause is still a miss
    assert match_rank(report("insufficient_evidence",
                             [("payment-svc", "connection pool exhaustion")]), k) is None
    assert match_rank(None, k) is None


def test_only_top_three_considered():
    k = key()
    causes = [("a", "x"), ("b", "y"), ("c", "z"), ("payment-svc", "connection pool exhaustion")]
    assert match_rank(report(causes=causes), k) is None
