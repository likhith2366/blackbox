"""Agent 4: viewer/build.py renders saved runs read-only (hand-built TEST FIXTURE runs)."""
from __future__ import annotations

import json
import sys

from blackbox.schemas import (CitationCheck, EvidenceRecord, FinalReport, InvestigationStep, RankedCause,
                              SystemOutput, ToolRequest, ToolResult)
from viewer.build import build


def _write_run(d, out: SystemOutput, meta: dict):
    d.mkdir(parents=True)
    (d / "output.json").write_text(out.model_dump_json(), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / "input.json").write_text(json.dumps({"run_id": d.name, "system": out.system}), encoding="utf-8")


def _out():
    ev = EvidenceRecord(evidence_id="log:qa1", source="logs", title="QA FIXTURE <script>x</script>",
                        content="QA FIXTURE body line")
    step = InvestigationStep(
        round=1, model_decision="continue", stop_decision="stop_diagnosis", stop_reason="h1 supported",
        orchestration_rules=["first-tool rule: QA"], hypothesis_changes=["+ h1 QA"],
        tool_requests=[ToolRequest(call_id="r1c1", round=1, tool="search_logs", args={"q": 1}),
                       ToolRequest(call_id="r1c2", round=1, tool="search_logs", args={"q": 1})],
        tool_results=[ToolResult(call_id="r1c1", tool="search_logs", status="ok", records=[ev]),
                      ToolResult(call_id="r1c2", tool="search_logs", status="ok", records=[ev])],
        duplicate_calls=["r1c2==r1c1"], context={"prompt_tokens_est": 1234, "num_ctx": 8192},
        raw_output='{"decision": "continue"}')
    rep = FinalReport(incident_id="INC-9001", system="blackbox", status="diagnosis", headline="QA headline",
                      ranked_causes=[RankedCause(rank=1, component="payment-svc", mechanism="qa", evidence_ids=["log:qa1"])],
                      citation_audit=[CitationCheck(where="cause1", raw="qa1", resolved="log:qa1", method="suffix"),
                                      CitationCheck(where="cause1", raw="log:FAKE", method="invalid")],
                      invalid_citations=["log:FAKE"])
    return SystemOutput(system="blackbox", incident_id="INC-9001", status="completed", report=rep, steps=[step],
                        evidence_seen=[ev], rounds_used=1, tool_calls_used=2)


def test_viewer_renders_everything_and_escapes(tmp_path):
    batch = tmp_path / "runs" / "qa_batch"
    _write_run(batch / "INC-9001_blackbox_none_r0", _out(), {"status": "completed", "wall_seconds": 1.5})
    (batch / "INC-9001_b0_none_r0").mkdir(parents=True)
    (batch / "INC-9001_b0_none_r0" / "meta.json").write_text(json.dumps({"status": "exception", "exception": "boom"}))
    (batch / "_interrupted" / "x").mkdir(parents=True)
    (batch / "_interrupted" / "x" / "meta.json").write_text("{}")
    loaded_before = set(sys.modules)
    runs = build(batch, tmp_path / "out")
    assert len(runs) == 2                                   # _interrupted skipped
    html = (tmp_path / "out" / "INC-9001_blackbox_none_r0.html").read_text(encoding="utf-8")
    for needle in ("QA headline", "stop_diagnosis", "first-tool rule", "cache hit (same as r1c1)", "1234",
                   'id="ev-log_qa1"', "suffix (recovered)", "log:FAKE", "QA FIXTURE body line", "cited by"):
        assert needle in html, needle
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert "STUB" not in html
    idx = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert "exception" in idx and "INC-9001_b0_none_r0" in idx
    newly = set(sys.modules) - loaded_before
    assert not any(m.startswith(("blackbox.evidence.backend", "blackbox.investigator.coordinator")) for m in newly)


def test_stub_runs_are_marked(tmp_path):
    batch = tmp_path / "runs" / "_dev_stub" / "dry"
    _write_run(batch / "STUB-0001_blackbox_none_r0", _out(), {"status": "completed", "stub": True})
    build(batch, tmp_path / "out")
    assert "STUB: development output" in (tmp_path / "out" / "STUB-0001_blackbox_none_r0.html").read_text(encoding="utf-8")
    assert "STUB" in (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
