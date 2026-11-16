"""Agent 4 integration QA. Shared setup for tests/integration.

Safety: every test in this directory runs with Ollama blocked. OllamaAdapter.chat and
blackbox.model.adapter.requests.post raise immediately, so no test can trigger inference
even if a code path unexpectedly reaches the real adapter.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
if str(FIXTURES) not in sys.path:
    sys.path.insert(0, str(FIXTURES))


class OllamaBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_model(monkeypatch):
    import blackbox.model.adapter as adapter

    def refuse(*a, **k):
        raise OllamaBlocked("tests/integration must never call the model")

    monkeypatch.setattr(adapter.OllamaAdapter, "chat", refuse)
    monkeypatch.setattr(adapter.requests, "post", refuse)
    yield


@pytest.fixture
def mini_snapshot(tmp_path):
    """A labelled QA test-fixture snapshot (INC-9001) written in scenarios.build's on-disk format."""
    from qa_snapshot import write_mini_snapshot
    root = tmp_path / "snapshots"
    write_mini_snapshot(root / "INC-9001")
    return root
