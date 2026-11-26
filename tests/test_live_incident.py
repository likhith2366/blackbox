"""Live incident timing (D19): which alert episode opens the incident, where the window starts, where the cutoff is.

A fake Prometheus serves a 5-second error-rate series; nothing here needs the live stack.
"""
from __future__ import annotations

from blackbox.connectors.prometheus import _iso, _ts
from blackbox.evidence.live_incident import chain, episodes, open_live_incident
from blackbox.evidence.live_stack import LiveStackBackend
from blackbox.schemas import BackendResult, EvidenceRecord

T0 = _ts("2026-11-26T17:00:00Z")          # series start
RULE = ("checkout-svc", "error_rate", ">", 0.1, 15)


class FakeProm:
    """error_rate samples every 5 s; `bad` is a list of (start, end) offsets in seconds where the rate is 0.5."""

    def __init__(self, bad, length_s=3600):
        self.pts = [(T0 + i, 0.5 if any(a <= i <= b for a, b in bad) else 0.0) for i in range(0, length_s, 5)]
        self.calls = []

    def _get(self, path, params):
        self.calls.append(params)
        s, e = float(params["start"]), float(params["end"])
        return {"result": [{"values": [[t, str(v)] for t, v in self.pts if s <= t <= e]}]}


def at(offset_s):
    return T0 + offset_s


def open_at(prom, now_offset, since_offset=None):
    return open_live_incident(*RULE, prom=prom, now=at(now_offset),
                              since=_iso(at(since_offset)) if since_offset is not None else None)


def test_episode_fires_after_for_seconds_of_breach():
    eps = episodes([(0, 0.5), (5, 0.5), (10, 0.5), (15, 0.0), (20, 0.5)], ">", 0.1, 15)
    assert len(eps) == 1 and eps[0]["onset"] == 0 and eps[0]["fired"] == 10 and eps[0]["end"] == 10


def test_delayed_investigation_keeps_the_first_onset():
    # fault deployed at +600 s; errors from +610; the rate dips and re-fires while the fault is active (as in the
    # retry-policy run); the investigation is opened 8 minutes after the first firing
    prom = FakeProm(bad=[(610, 700), (760, 900), (1000, 1100)])
    inc, timing = open_at(prom, now_offset=1100, since_offset=590)
    assert timing["episodes_in_incident"] == 3
    assert inc.alert.fired_at == _iso(at(620))                      # first firing, not the latest (+1010)
    assert _ts(inc.window_start) == at(590)                           # bounded by `since` (620 - 300 < 590)
    assert _ts(inc.window_start) <= at(600)                           # the deploy at +600 is inside the window
    # the old rule (latest episode, 300 s lookback) would have started the window at +710, after the deploy
    assert timing["latest_fired_at"] == _iso(at(1010))


def test_repeated_episodes_are_one_incident_and_reopening_gives_the_same_incident():
    prom = FakeProm(bad=[(1000, 1060), (1120, 1180), (1240, 1300)])
    a, ta = open_at(prom, now_offset=1100)
    b, tb = open_at(prom, now_offset=1400)                           # re-opened later: more episodes, same onset
    assert ta["episodes_in_incident"] == 1 and tb["episodes_in_incident"] == 3
    assert a.incident_id == b.incident_id and a.alert.fired_at == b.alert.fired_at
    assert a.window_start == b.window_start == _iso(at(1010 - 300))
    assert _ts(b.investigation_cutoff) == at(1400)                    # each opening has its own cutoff


def test_an_earlier_unrelated_episode_is_not_pulled_in():
    # an unrelated incident at +300..+400, then 20 quiet minutes, then the current fault at +1600
    prom = FakeProm(bad=[(300, 400), (1600, 1700)])
    inc, timing = open_at(prom, now_offset=1720)
    assert timing["episodes_found"] == 2 and timing["episodes_in_incident"] == 1
    assert inc.alert.fired_at == _iso(at(1610))
    assert _ts(inc.window_start) == at(1310)                          # 300 s before the current firing only
    assert _ts(inc.window_start) > at(400)                            # an unrelated change at +350 stays outside


def test_chain_stops_at_the_first_quiet_gap_longer_than_the_lookback():
    eps = [{"onset": 0, "fired": 10, "end": 50}, {"onset": 400, "fired": 410, "end": 450},
           {"onset": 600, "fired": 610, "end": 700}]
    assert [e["onset"] for e in chain(eps, 300)] == [400, 600]        # 50 -> 400 is a 350 s gap
    assert [e["onset"] for e in chain(eps, 400)] == [0, 400, 600]


def test_episodes_after_the_cutoff_are_ignored():
    prom = FakeProm(bad=[(1000, 1100), (1150, 1300)])
    inc, timing = open_at(prom, now_offset=1120)
    assert timing["latest_fired_at"] == _iso(at(1010)) and timing["episodes_in_incident"] == 1
    assert all(float(c["end"]) <= at(1120) for c in prom.calls)


class Recorder:
    def __init__(self, records):
        self.records, self.calls = records, []

    def search(self, **kw):
        self.calls.append(kw)
        return BackendResult(status="ok", records=list(self.records), total_matches=len(self.records))


def _rec(eid, t):
    return EvidenceRecord(evidence_id=eid, source="logs", service="checkout-svc", event_time=t, available_time=t,
                          title=eid, content=eid)


def test_a_late_investigation_sees_nothing_after_the_cutoff():
    # incident created at +1100; the investigation runs much later; queries end at the cutoff and later records,
    # if a source returns them anyway, are removed
    prom = FakeProm(bad=[(610, 700)])
    inc, _ = open_at(prom, now_offset=1100, since_offset=590)
    loki = Recorder([_rec("log:before", _iso(at(1000)))])
    b = LiveStackBackend(inc, loki=loki)
    b.search_logs(service="checkout-svc", end=_iso(at(5000)))
    assert loki.calls[-1]["end"] == inc.investigation_cutoff
    late = BackendResult(status="ok", records=[_rec("log:before", _iso(at(1000))), _rec("log:after", _iso(at(1500)))],
                         total_matches=2)
    kept = b._before_cutoff(late)
    assert [r.evidence_id for r in kept.records] == ["log:before"]
