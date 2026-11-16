"""Ablation and cutoff leakage checks against every available snapshot (CONTRACT s7, s8).

For each snapshot the un-ablated backend is probed broadly (eval.probe) to learn what the
ablated information looks like (ticket keys, build ids, artifact digests, deployed commit
shas, trace/span records). Each ablated backend is then probed through every accessible
path: all tool methods (default window, an extremely wide window, per service, direct
lookups of the learned ids), export_logs, source_status and incident() (incident.json).
Every returned byte (records, errors, exception text) is scanned.

- no_jira: no ticket records, no ticket key anywhere, tickets not in
  incident.available_sources, source_status reports tickets unavailable.
- no_provenance: no build records; build ids and artifact digests nowhere (every
  response, all sources); deployed commit shas (full and 7-char forms) never survive in
  deployments, builds, logs, spans/traces or metrics records, nor in the summaries
  (incident() / incident.json, source_status). Exempt from the deployed-sha check only:
  commits (stay searchable, CONTRACT s8), configs (repository versions keyed by commit,
  config:<svc>@<sha7>; scenarios 3/6 deploy a config commit; approved by Agent 1
  2026-11-20) and tickets (team discussion; reported as a warning). Source lists live in
  eval/probe.py (DEPLOYED_SHA_ALLOWED_SOURCES, PROVENANCE_STRICT_SOURCES); builds not available.
- no_traces: no span/trace records, no span:/trace: evidence ids (trace ids may stay in
  logs as opaque strings); traces not available.
- metric and config evidence ids are identical to the un-ablated snapshot ("everything
  else identical").
- every ablation incl. "none": no record after the cutoff; every record has available_time.
Skips with a reason until Agent 2's backend and snapshots exist.
"""
import json
import warnings
from datetime import datetime
from functools import lru_cache

import pytest

from eval.probe import (DEPLOYED_SHA_ALLOWED_SOURCES, PROVENANCE_STRICT_SOURCES, collect,
                        learn_identifiers)

ABLATIONS = ("no_jira", "no_provenance", "no_traces")
ABLATED_SOURCE = {"no_jira": "tickets", "no_provenance": "builds", "no_traces": "traces"}

try:
    from blackbox.evidence.backend import list_incidents, load_backend  # Agent 2
    INCIDENTS = list(list_incidents())
    _SKIP = None if INCIDENTS else "no snapshots under scenarios/snapshots yet"
except Exception as e:  # backend not implemented yet
    INCIDENTS, _SKIP = [], f"evidence backend not available yet: {type(e).__name__}: {e}"

pytestmark = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")
PARAMS = INCIDENTS or ["<none>"]


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@lru_cache(maxsize=None)
def learn(iid):
    be = load_backend(iid, ablation="none")
    first = collect(be)
    L = learn_identifiers(first)
    col = collect(be, {"trace_ids": L["trace_ids"][:20], "build_ids": L["build_ids"],
                       "commit_shas": L["commit_shas"][:20], "ticket_keys": L["ticket_keys"]})
    return L, be.incident().investigation_cutoff, col


@lru_cache(maxsize=None)
def ablated(iid, ablation):
    L, _, _ = learn(iid)
    return collect(load_backend(iid, ablation=ablation),
                   {"trace_ids": L["trace_ids"][:20], "build_ids": L["build_ids"],
                    "commit_shas": L["commit_shas"][:20], "ticket_keys": L["ticket_keys"]})


def _hits(col, needles, record_filter=None):
    found = []
    for n in needles:
        if record_filter is None:
            found += [(lab, n) for lab, txt in col.calls if n in txt]
        else:
            found += [(lab, n, r.evidence_id) for lab, r in col.records
                      if record_filter(r) and n in r.model_dump_json()]
    return found


def _status(col, source):
    for lab, val in col.raw:
        if lab == "source_status":
            for s in val:
                if s.source == source:
                    return s
    return None


def _incident(col):
    return next((v for lab, v in col.raw if lab == "incident"), None)


def _check_summaries(col, ablation):
    src = ABLATED_SOURCE[ablation]
    st = _status(col, src)
    assert st is None or st.available is False, f"{src} still reported available: {st}"
    inc = _incident(col)
    assert inc is not None, "incident() failed"
    assert src not in inc.available_sources, \
        f"incident.available_sources still lists {src} under {ablation}: {inc.available_sources}"


@pytest.mark.parametrize("iid", PARAMS)
def test_no_jira_leak(iid):
    L, _, _ = learn(iid)
    col = ablated(iid, "no_jira")
    recs = [(lab, r.evidence_id) for lab, r in col.records
            if r.source == "tickets" or r.evidence_id.startswith("jira:")]
    assert not recs, f"ticket records under no_jira: {recs[:10]}"
    hits = _hits(col, L["ticket_keys"])
    assert not hits, f"ticket keys under no_jira: {hits[:10]}"
    _check_summaries(col, "no_jira")
    if not L["ticket_keys"]:
        warnings.warn(f"{iid}: no ticket keys in the un-ablated snapshot; key check vacuous")


@pytest.mark.parametrize("iid", PARAMS)
def test_no_provenance_leak(iid):
    L, _, _ = learn(iid)
    col = ablated(iid, "no_provenance")
    builds = [(lab, r.evidence_id) for lab, r in col.records
              if r.source == "builds" or r.evidence_id.startswith("build:")]
    assert not builds, f"build records under no_provenance: {builds[:10]}"
    hits = _hits(col, L["build_ids"] + L["digests"])
    assert not hits, f"build ids / artifact digests under no_provenance: {hits[:10]}"
    sha_hits = _hits(col, L["sha_forms"],
                     record_filter=lambda r: r.source not in DEPLOYED_SHA_ALLOWED_SOURCES)
    assert not sha_hits, f"deployed commit shas outside commits/configs/tickets: {sha_hits[:10]}"
    strict = _hits(col, L["sha_forms"] + L["build_ids"] + L["digests"],
                   record_filter=lambda r: r.source in PROVENANCE_STRICT_SOURCES)
    assert not strict, f"provenance ids in deployments/builds/logs/spans/metrics: {strict[:10]}"
    summaries = [(lab, n) for n in L["sha_forms"] + L["build_ids"] + L["digests"]
                 for lab, txt in col.calls if lab in ("incident", "source_status") and n in txt]
    assert not summaries, f"provenance ids in incident/source_status summaries: {summaries[:10]}"
    t_hits = _hits(col, L["sha_forms"], record_filter=lambda r: r.source == "tickets")
    if t_hits:
        warnings.warn(f"{iid}: deployed shas mentioned in tickets under no_provenance: {t_hits[:5]}")
    _check_summaries(col, "no_provenance")
    if not (L["build_ids"] or L["digests"] or L["deployed_shas"]):
        warnings.warn(f"{iid}: no provenance identifiers learned; checks vacuous")


@pytest.mark.parametrize("iid", PARAMS)
def test_no_traces_leak(iid):
    L, _, _ = learn(iid)
    col = ablated(iid, "no_traces")
    recs = [(lab, r.evidence_id) for lab, r in col.records
            if r.source == "traces" or r.evidence_id.startswith(("span:", "trace:"))]
    assert not recs, f"span/trace records under no_traces: {recs[:10]}"
    hits = _hits(col, L["trace_record_ids"])
    assert not hits, f"span/trace evidence ids under no_traces: {hits[:10]}"
    _check_summaries(col, "no_traces")


@pytest.mark.parametrize("ablation", ABLATIONS)
@pytest.mark.parametrize("iid", PARAMS)
def test_other_sources_unchanged(iid, ablation):
    _, _, base = learn(iid)
    col = ablated(iid, ablation)
    for src in ("metrics", "configs"):
        a = sorted({r.evidence_id for _, r in base.records if r.source == src})
        b = sorted({r.evidence_id for _, r in col.records if r.source == src})
        assert a == b, f"{src} evidence ids differ under {ablation}: only-none={set(a)-set(b)}, only-ablated={set(b)-set(a)}"


@pytest.mark.parametrize("ablation", ("none",) + ABLATIONS)
@pytest.mark.parametrize("iid", PARAMS)
def test_no_post_cutoff_records(iid, ablation):
    _, cutoff, base_col = learn(iid)
    col = base_col if ablation == "none" else ablated(iid, ablation)
    c = _ts(cutoff)
    missing = [(lab, r.evidence_id) for lab, r in col.records if not r.available_time]
    late = [(lab, r.evidence_id, r.available_time) for lab, r in col.records
            if r.available_time and _ts(r.available_time) > c]
    assert not late, f"records after cutoff {cutoff}: {late[:10]}"
    assert not missing, f"records without available_time: {missing[:10]}"


# D15 (docs/KNOWN_DEFECTS.md): the configs exemption hides one real leak. In INC-2691 the config id
# config:checkout-svc@7595162 is the commit sha of fault deploy d-545, so no_provenance still exposes
# the deploy->commit mapping there. That frozen cell is reported but excluded from no_provenance
# conclusions. This test checks config records separately, so any NEW case fails; INC-2691 is a
# strict expected failure until a new snapshot/backend version masks the sha.
D15 = {"INC-2691"}


@pytest.mark.parametrize("iid", [pytest.param(i, marks=pytest.mark.xfail(
    strict=True, reason="D15: config id carries the deployed fault sha (frozen; excluded from "
                        "no_provenance conclusions)")) if i in D15 else i for i in PARAMS])
def test_no_provenance_configs_carry_no_deployed_sha(iid):
    L, _, _ = learn(iid)
    col = ablated(iid, "no_provenance")
    hits = _hits(col, L["sha_forms"], record_filter=lambda r: r.source == "configs")
    assert not hits, f"config records carry a deployed sha under no_provenance: {hits[:5]}"
