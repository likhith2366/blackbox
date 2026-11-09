"""Single entry point for the three systems. Used by the CLI and by eval/ (Agent 3)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from blackbox.baselines.single_call import run_single_call
from blackbox.evidence.interface import EvidenceBackend
from blackbox.investigator.common import load_prompt
from blackbox.investigator.coordinator import Coordinator
from blackbox.schemas import EvidenceRecord, RunConfig, SystemName, SystemOutput

REPO_ROOT = Path(__file__).resolve().parent.parent


def code_revision() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "blackbox"], cwd=REPO_ROOT,
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return f"{rev}{'+dirty' if dirty else ''}" or "unknown"
    except Exception:
        return "unknown"


def prompt_version(system: SystemName, config: RunConfig) -> str:
    names = [config.round_prompt, config.report_prompt] if system == "blackbox" else [config.baseline_prompt]
    return "+".join(load_prompt(n)[1] for n in names)


def run_system(system: SystemName, backend: EvidenceBackend, config: RunConfig,
               b1_evidence: Optional[list[EvidenceRecord]] = None, on_event=None) -> SystemOutput:
    cfg = config.model_copy(update={
        "code_revision": config.code_revision or code_revision(),
        "prompt_version": prompt_version(system, config),
    })
    if system == "blackbox":
        return Coordinator(backend, cfg, on_event=on_event).run()
    if system in ("b0", "b1"):
        return run_single_call(system, backend, cfg, b1_evidence=b1_evidence)
    raise ValueError(f"unknown system {system!r}")
