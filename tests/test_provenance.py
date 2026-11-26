"""Live-run provenance: code revision at process start, settings-file hash, effective-config hash."""
from __future__ import annotations

from pathlib import Path

import yaml

from blackbox import provenance
from blackbox.schemas import RunConfig

REPO = Path(__file__).resolve().parents[1]


def test_record_names_code_revision_and_config_hashes():
    path = REPO / "configs" / "dev_v3e.yaml"
    cfg = RunConfig(**yaml.safe_load(path.read_text(encoding="utf-8")))
    rec = provenance.record(path, cfg, fault="retry-policy")
    assert rec["blackbox_commit"] == provenance.AT_IMPORT["blackbox_commit"]
    assert len(rec["config_file_sha256"]) == 64 and rec["config_file_sha256"] == provenance.file_sha256(path)
    assert rec["config_file"] == "configs/dev_v3e.yaml"
    assert len(rec["config_effective_sha256"]) == 64
    assert rec["fault"] == "retry-policy" and rec["model"] == "qwen3:4b"


def test_effective_hash_changes_with_settings_but_not_with_code_revision():
    path = REPO / "configs" / "dev_v3e.yaml"
    cfg = RunConfig(**yaml.safe_load(path.read_text(encoding="utf-8")))
    a = provenance.record(path, cfg)["config_effective_sha256"]
    b = provenance.record(path, cfg.model_copy(update={"code_revision": "abc1234"}))["config_effective_sha256"]
    c = provenance.record(path, cfg.model_copy(update={"synthesis": False}))["config_effective_sha256"]
    assert a == b and a != c


def test_revision_is_fixed_at_import(monkeypatch):
    first = provenance.short_revision()
    monkeypatch.setattr(provenance, "_git", lambda *a, **k: "0" * 40)   # the checkout moving later changes nothing
    assert provenance.short_revision() == first
