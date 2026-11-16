"""LiveBackend wiring with labelled test doubles (no network, no model)."""
from types import SimpleNamespace

from blackbox.evidence.live import LiveBackend
from blackbox.schemas import Alert, BackendResult, EvidenceRecord, Incident, SourceStatus

T = "2026-11-19T10:00:00Z"


class SnapDouble:   # TEST FIXTURE
    incident_id, ablation = "INC-TEST", "none"

    def incident(self):
        return Incident(incident_id="INC-TEST", alert=Alert(alert_id="a", title="t", service="checkout-svc",
                        signal="error_rate", condition="> 0.05", fired_at=T), window_start=T, window_end=T,
                        investigation_cutoff=T, services=["checkout-svc"], available_sources=["commits", "configs"])

    def source_status(self):
        return [SourceStatus(source=s, available=True, healthy=True, fixture=True) for s in ("commits", "configs", "logs")]

    def list_deployments(self, service=None, start=None, end=None):
        return BackendResult(status="ok", records=[EvidenceRecord(evidence_id="deploy:d-1", source="deployments",
                             content="deploy d-1", attributes={"commit": "abc1234"})])

    def search_traces(self, **kw):
        return BackendResult(status="empty")


class GitHubDouble:   # TEST FIXTURE
    repo = "o/r"

    def __init__(self):
        self.calls = []

    def list_commits(self, **kw):
        self.calls.append(("list_commits", kw))
        return BackendResult(status="ok", records=[EvidenceRecord(evidence_id="gh:commit:o/r@abc1234", source="commits",
                             content="retry change")])

    def get_file(self, path, ref, allow_mutable_ref=False):
        self.calls.append(("get_file", path, ref))
        return BackendResult(status="ok", records=[EvidenceRecord(evidence_id=f"gh:file:o/r@{ref}:{path}", source="configs")])

    def get_commit(self, sha):
        self.calls.append(("get_commit", sha))
        return BackendResult(status="ok")


def test_reads_are_pinned_to_the_deployed_commit_and_capped_at_cutoff():
    gh = GitHubDouble()
    b = LiveBackend(SnapDouble(), github=gh)
    assert b.get_config("checkout-svc").records[0].evidence_id == "gh:file:o/r@abc1234:config/checkout-svc.yaml"
    b.search_commits()
    kw = [c for c in gh.calls if c[0] == "list_commits"][0][1]
    assert kw["sha"] == "abc1234" and kw["until"] == T
    b.get_commit("gh:commit:o/r@abc1234")
    assert ("get_commit", "abc1234") in gh.calls


def test_unknown_service_config_is_unavailable_and_sources_are_labelled_live():
    b = LiveBackend(SnapDouble(), github=GitHubDouble())
    assert b.get_config("nope-svc").status == "unavailable"
    st = {s.source: s for s in b.source_status()}
    assert st["commits"].fixture is False and "live GitHub" in st["commits"].detail
    assert b.search_traces().status == "empty"           # delegated to the snapshot


def test_live_mode_refuses_ablations():
    snap = SnapDouble(); snap.ablation = "no_jira"
    try:
        LiveBackend(snap, github=GitHubDouble())
    except ValueError:
        return
    raise AssertionError("ablation accepted in live mode")
