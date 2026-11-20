"""Shared paths and loaders for the evaluation package.

The scenario manifest format is owned by Evidence. CONTRACT.md s6 fixes its content
(incident_id -> scenario number, type, variant_of, frozen flag, build command, capture
time) but not its exact shape, so `load_manifest` accepts the plausible shapes:
  {"INC-0001": {...}, ...}
  {"incidents": [{"incident_id": "INC-0001", ...}, ...]}  (or "scenarios"/"entries")
  [{"incident_id": "INC-0001", ...}, ...]
and normalises each entry to a ManifestEntry.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from blackbox.schemas import AnswerKey, SystemOutput

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = REPO_ROOT / "runs"
RESULTS_ROOT = REPO_ROOT / "results"
STUB_ROOT = RUNS_ROOT / "_dev_stub"
PRIVATE_ROOT = REPO_ROOT / "evaluator_private"
DEFAULT_MANIFEST = PRIVATE_ROOT / "scenario_manifest.json"
DEFAULT_ANSWER_KEYS = PRIVATE_ROOT / "answer_keys"
AGENT1_LOG = REPO_ROOT / "coordination" / "agent1_log.md"
AGENT2_LOG = REPO_ROOT / "coordination" / "agent2_log.md"

PRIMARY_DIAGNOSABLE_SCENARIOS = (1, 2, 3, 4)      # Milestone 2 s4/s5
INSUFFICIENT_SCENARIOS = (5, 6)
HEALTHY_SCENARIOS = (7,)
ROBUSTNESS_SCENARIOS = (7, 8, 9)

SCENARIO_TYPE_BY_NUMBER = {
    0: "pilot", 1: "misleading_symptom", 2: "misleading_symptom",
    3: "unrelated_change", 4: "unrelated_change",
    5: "insufficient_evidence", 6: "insufficient_evidence",
    7: "healthy", 8: "connector_failure", 9: "prompt_injection",
}


@dataclass
class ManifestEntry:
    incident_id: str
    scenario_number: int
    scenario_type: str
    variant_of: Optional[str] = None
    frozen: bool = False
    superseded: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def _as_bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y"}
    return bool(v)


def _entry_from(incident_id: str, d: dict[str, Any]) -> ManifestEntry:
    num = d.get("scenario_number", d.get("scenario", d.get("number")))
    if num is None:
        raise ValueError(f"manifest entry {incident_id} has no scenario number")
    num = int(num)
    stype = d.get("scenario_type", d.get("type")) or SCENARIO_TYPE_BY_NUMBER.get(num, "unknown")
    superseded = _as_bool(d.get("superseded", False)) or bool(d.get("superseded_by")) \
        or str(d.get("status", "")).lower() == "superseded"
    return ManifestEntry(
        incident_id=incident_id,
        scenario_number=num,
        scenario_type=str(stype),
        variant_of=d.get("variant_of"),
        frozen=_as_bool(d.get("frozen", False)),
        superseded=superseded,
        raw=dict(d),
    )


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, ManifestEntry]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items: list[tuple[str, dict]] = []
    if isinstance(data, list):
        items = [(e["incident_id"], e) for e in data]
    elif isinstance(data, dict):
        for key in ("incidents", "scenarios", "entries"):
            if key in data and isinstance(data[key], (list, dict)):
                inner = data[key]
                if isinstance(inner, list):
                    items = [(e["incident_id"], e) for e in inner]
                else:
                    items = [(k, v) for k, v in inner.items() if isinstance(v, dict)]
                break
        else:
            items = [(k, v) for k, v in data.items()
                     if isinstance(v, dict) and re.match(r"^[A-Z]+-\d+", k)]
    out: dict[str, ManifestEntry] = {}
    for iid, d in items:
        out[iid] = _entry_from(iid, d)
    return out


def active_entries(manifest: dict[str, ManifestEntry]) -> list[ManifestEntry]:
    """Non-superseded entries, ordered by scenario number then incident id."""
    return sorted((e for e in manifest.values() if not e.superseded),
                  key=lambda e: (e.scenario_number, e.incident_id))


def load_answer_key(incident_id: str, keys_dir: Path = DEFAULT_ANSWER_KEYS) -> Optional[AnswerKey]:
    p = Path(keys_dir) / f"{incident_id}.json"
    if not p.exists():
        return None
    return AnswerKey.model_validate_json(p.read_text(encoding="utf-8"))


def load_answer_keys(keys_dir: Path = DEFAULT_ANSWER_KEYS) -> dict[str, AnswerKey]:
    out: dict[str, AnswerKey] = {}
    d = Path(keys_dir)
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        k = AnswerKey.model_validate_json(p.read_text(encoding="utf-8"))
        out[k.incident_id] = k
    return out


def log_has_marker(path: Path, marker: str) -> bool:
    try:
        return marker in Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return False


def pipeline_ready() -> bool:
    return log_has_marker(AGENT1_LOG, "PIPELINE READY")


def pilot_ready() -> bool:
    return log_has_marker(AGENT2_LOG, "PILOT READY")


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- run records
@dataclass
class RunRecord:
    run_id: str
    run_dir: Path
    input: dict[str, Any]
    meta: dict[str, Any]
    output: Optional[SystemOutput]
    output_error: Optional[str] = None     # output.json missing or invalid

    @property
    def stub(self) -> bool:
        return bool(self.input.get("stub") or self.meta.get("stub"))

    @property
    def incident_id(self) -> str:
        return self.input["incident_id"]

    @property
    def system(self) -> str:
        return self.input["system"]

    @property
    def ablation(self) -> str:
        return self.input.get("ablation", "none")

    @property
    def repeat(self) -> int:
        return int(self.input.get("repeat_index", 0))

    @property
    def run_status(self) -> str:
        """Harness-level status: completed / failed / timeout / exception / skipped_dependency / running."""
        return self.meta.get("status", "unknown")


def load_run(run_dir: Path) -> Optional[RunRecord]:
    run_dir = Path(run_dir)
    ip, mp, op = run_dir / "input.json", run_dir / "meta.json", run_dir / "output.json"
    if not ip.exists():
        return None
    inp = read_json(ip)
    meta = read_json(mp) if mp.exists() else {"status": "running"}
    out, err = None, None
    if op.exists():
        try:
            out = SystemOutput.model_validate_json(op.read_text(encoding="utf-8"))
        except Exception as e:  # invalid output is itself a result
            err = f"invalid output.json: {e}"
    else:
        err = "output.json missing"
    return RunRecord(run_id=inp.get("run_id", run_dir.name), run_dir=run_dir,
                     input=inp, meta=meta, output=out, output_error=err)


def load_batch_runs(batch_dir: Path) -> list[RunRecord]:
    recs = []
    for d in sorted(Path(batch_dir).iterdir()):
        if d.is_dir() and not d.name.startswith("_"):
            r = load_run(d)
            if r is not None:
                recs.append(r)
    return recs


def resolve_batch_dir(batch: str, stub: bool) -> Path:
    return (STUB_ROOT if stub else RUNS_ROOT) / batch


def results_dir_for(batch: str, stub: bool) -> Path:
    """Stub batches never reach results/: their dev output stays under runs/_dev_stub/."""
    if stub:
        return STUB_ROOT / batch / "_results"
    return RESULTS_ROOT / batch
