"""Scenario snapshot integrity (Evidence). Runs over every built snapshot on disk."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from blackbox.schemas import AnswerKey, Incident
from demo import workspace
from scenarios.verify import KEYS, SNAPSHOTS, verify_incident

INCIDENTS = sorted(p.name for p in SNAPSHOTS.iterdir() if (p / "incident.json").exists()) \
    if SNAPSHOTS.exists() else []
MANIFEST = Path(__file__).resolve().parents[1] / "evaluator_private" / "scenario_manifest.json"


@pytest.mark.parametrize("inc_id", INCIDENTS)
def test_snapshot_passes_verify(inc_id):
    assert verify_incident(inc_id) == []


@pytest.mark.parametrize("inc_id", INCIDENTS)
def test_models_validate(inc_id):
    inc = Incident.model_validate_json((SNAPSHOTS / inc_id / "incident.json").read_text(encoding="utf-8"))
    key = AnswerKey.model_validate_json((KEYS / f"{inc_id}.json").read_text(encoding="utf-8"))
    assert inc.incident_id == key.incident_id == inc_id
    assert inc.investigation_cutoff <= inc.window_end
    assert inc.alert.fired_at < inc.investigation_cutoff


def test_manifest_lists_every_snapshot():
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))["incidents"]
    for inc_id in INCIDENTS:
        assert inc_id in man
        if man[inc_id].get("variant_of"):
            assert man[inc_id]["scenario_type"] in ("connector_failure", "prompt_injection")


def test_snapshot_names_are_opaque():
    for inc_id in INCIDENTS:
        names = {p.name for p in (SNAPSHOTS / inc_id).iterdir()}
        assert names <= {"incident.json", "manifest.json", "logs.jsonl", "spans.jsonl", "metrics.jsonl",
                         "deployments.jsonl", "builds.jsonl", "commits.jsonl", "configs.jsonl",
                         "tickets.json", "runtime.json"}


def test_no_persona_names_in_snapshots():
    personas = ("Maya Chen", "Priya Nair", "Lena Hoffmann", "Tom Becker", "Rafael Ortiz")
    for inc_id in INCIDENTS:
        for p in (SNAPSHOTS / inc_id).iterdir():
            text = p.read_text(encoding="utf-8", errors="replace")
            assert not any(n in text for n in personas), (inc_id, p.name)


def test_tooling_identities_are_the_two_team_members():
    assert {e for _, e in workspace.PEOPLE.values()} == {"g.likhithvardhan@gmail.com",
                                                         "praneethkadem16@gmail.com"}
