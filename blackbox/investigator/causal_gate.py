"""Causal-evidence gate (development option `diagnosis_gate: causal`; the default and frozen gate is v1).

The gate decides whether the cited evidence is SUFFICIENT for the top hypothesis. It does not decide what
the true cause is. It only looks at the hypothesis text and at records that were actually retrieved, and
it uses no incident ids, SHAs, answer keys or expected mechanisms.

Roles a supported diagnosis needs (each role must be filled by a cited record):

- change (only for a change-based hypothesis): a deployment, commit, build or config record of the
  candidate component. The words "recent commit" are not a change record.
- mechanism: something that shows HOW the fault works, not only that it happened:
  a commit retrieved with its diff, a configuration, a component metric that is not a pure symptom series
  (error rate, latency, timeouts, request rate), or a log/trace/span whose text contains a specific term of
  the stated mechanism. A deployment record (a timestamp and a version) never fills this role.
- effect: a runtime record (log, trace, span, metric) showing the symptom, distinct from the mechanism
  record. If the change and the effect both have timestamps, the effect must not start before the change.

So two error-rate or timeout metrics can fill the effect role, but never the change or mechanism role.
A hypothesis that is not change-based needs only mechanism and effect.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterable, Optional

from blackbox.schemas import EvidenceRecord, Hypothesis

CHANGE_SOURCES = {"deployments", "commits", "builds", "configs"}
EFFECT_SOURCES = {"logs", "traces", "metrics"}
# metric names that only measure the symptom (that requests fail or are slow), not the mechanism
SYMPTOM_METRIC = re.compile(r"error|latency|duration|timeout|5xx|4xx|status|requests?_per|throughput|rps|qps|apdex",
                            re.I)
# words that say THAT something changed or failed, not HOW it fails
GENERIC = set("""
a an the of in on at to for from by with and or due because caused causing cause causes leading leads led which
that this its it is are was were be been being as after before since during when while than then into via
recent recently new latest last change changes changed changing deploy deploys deployed deployment deployments
release released rollout version versions update updated upgrade commit commits code build regression
error errors erroring failure failures fail fails failed failing fault faulty issue issues problem problems bug
spike spikes spiking increase increased increasing rise rising high higher elevated surge drop dropped
rate rates timeout timeouts timed time times out slow slowness slower latency degraded degradation
unavailable outage down unhealthy instability unstable transient intermittent overload overloaded
service services svc upstream downstream dependency call calls request requests response responses
system component server client side internal external possible possibly likely potential some
""".split())
_TOKEN = re.compile(r"[a-z][a-z_]{3,}")
_POINT = re.compile(r"(\d\d:\d\d:\d\d) (-?[\d.]+)")
CHANGE_WORDS = re.compile(r"\b(deploy\w*|commit\w*|releas\w*|rollout|rolled|version|upgrad\w*|regression|"
                          r"introduc\w*|new code|code change|config\w* change|change[sd]?)\b", re.I)
TOLERANCE = timedelta(seconds=10)


def _stem(w: str) -> str:
    for suf, rep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: len(w) - len(suf)] + rep
    return w


def specific_terms(text: str, services: Iterable[str]) -> set[str]:
    """Mechanism words that are not generic and not service names (stemmed)."""
    names = set()
    for s in services:
        names |= set(re.split(r"[^a-z]+", s.lower()))
    return {_stem(w) for w in _TOKEN.findall(text.lower()) if w not in GENERIC and w not in names
            and _stem(w) not in GENERIC}


def _ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _services(r: EvidenceRecord) -> set[str]:
    return {x.strip().lower() for x in (r.service or "").split(",") if x.strip()}


def _of_component(r: EvidenceRecord, component: str) -> bool:
    c = component.lower()
    return any(s in c for s in _services(r))


def _metric_name(r: EvidenceRecord) -> str:
    return str(r.attributes.get("metric") or r.title.split(" ")[-1])


def has_diff(r: EvidenceRecord) -> bool:
    return r.source == "commits" and ("diff --git" in r.content or "\n@@ " in r.content or "\n+++ " in r.content)


def onset(r: EvidenceRecord) -> Optional[datetime]:
    """When the symptom in this record starts. For a metric: the start of the latest run of points clearly above
    the series minimum (an earlier episode that already ended is a different event)."""
    if r.source == "logs":
        return _ts(r.attributes.get("first_seen")) or _ts(r.event_time)
    if r.source == "traces":
        return _ts(r.event_time)
    if r.source == "metrics":
        start = _ts(r.event_time)
        pts = _POINT.findall(r.content)
        if start is None or len(pts) < 2:
            return start
        vals = [float(v) for _, v in pts]
        lo, hi = min(vals), max(vals)
        if hi <= lo:
            return None          # flat series: no onset to compare
        thr = lo + 0.25 * (hi - lo)
        day, prev = start.date(), None
        run_start, last_start = None, None
        for hhmmss, v in pts:
            t = datetime.combine(day, datetime.strptime(hhmmss, "%H:%M:%S").time(), start.tzinfo)
            if prev is not None and t < prev:
                day += timedelta(days=1)
                t += timedelta(days=1)
            prev = t
            if float(v) > thr:
                if run_start is None:
                    run_start = t
                last_start = run_start
            else:
                run_start = None
        # the start of the LATEST elevated episode: an earlier, already-ended episode is a different event
        return last_start
    return None


def is_change_based(h: Hypothesis, cited: list[EvidenceRecord]) -> bool:
    return bool(CHANGE_WORDS.search(h.mechanism)) or any(r.source in ("deployments", "commits", "builds")
                                                         for r in cited)


def check(h: Optional[Hypothesis], seen: dict[str, EvidenceRecord],
          unavailable: Iterable[str] = (), window_start: Optional[str] = None) -> tuple[bool, str, dict]:
    """Return (ok, reason, roles). The reason is neutral: it names what is missing, never what to find."""
    roles: dict = {"change_based": None, "change": [], "mechanism": [], "effect": [], "timing": "n/a"}
    if h is None:
        return False, "no live hypothesis", roles
    unavailable = set(unavailable)
    cited = [seen[i] for i in h.supporting if i in seen]
    services = {s for r in seen.values() for s in _services(r)}
    comp_ok = any(s in h.component.lower() for s in services)
    terms = specific_terms(h.mechanism, services)
    change_based = is_change_based(h, cited)
    roles["change_based"] = change_based
    hid = h.hypothesis_id

    if not comp_ok:
        return False, (f"{hid}: component missing: '{h.component}' is not a service seen in the retrieved "
                       f"evidence"), roles
    if not terms:
        return False, (f"{hid}: mechanism not stated: '{h.mechanism}' names a symptom or a change, not how "
                       f"the fault produces the symptom"), roles

    # change role
    change = [r for r in cited if r.source in CHANGE_SOURCES and _of_component(r, h.component)]
    roles["change"] = [r.evidence_id for r in change]

    # mechanism role
    mech = []
    for r in cited:
        if r.source == "deployments":
            continue                         # a timestamp and a version are not a mechanism
        if has_diff(r) and _of_component(r, h.component):
            mech.append(r)
        elif r.source == "configs" and _of_component(r, h.component):
            mech.append(r)
        elif r.source == "metrics" and _of_component(r, h.component) and not SYMPTOM_METRIC.search(_metric_name(r)):
            mech.append(r)
        elif r.source in ("logs", "traces") and terms & specific_terms(r.content + " " + r.title, ()):
            mech.append(r)
    roles["mechanism"] = [r.evidence_id for r in mech]

    # effect role: a runtime record not already used as the only mechanism evidence
    effect = [r for r in cited if r.source in EFFECT_SOURCES]
    roles["effect"] = [r.evidence_id for r in effect]

    def missing(what: str, sources: set[str]) -> str:
        gone = sorted(sources & unavailable)
        return what + (f" ({', '.join(gone)} unavailable in this snapshot)" if gone else "")

    if change_based and not change:
        return False, f"{hid}: " + missing(
            f"change evidence missing: the hypothesis attributes the fault to a change but cites no retrieved "
            f"deployment, commit, build or config record of {h.component}", {"deployments", "commits", "configs"}), roles
    if change_based and window_start:
        # a change-based claim needs a change that happened in the incident window (deployments, commits, builds;
        # a config file's commit time is not when it took effect, so configs do not count here)
        dated = [r for r in change if r.source in ("deployments", "commits", "builds") and _ts(r.event_time)]
        if dated and all(_ts(r.event_time) < _ts(window_start) for r in dated):
            latest = max(_ts(r.event_time) for r in dated)
            roles["timing"] = "change before window"
            return False, (f"{hid}: change outside the incident window: every cited change predates "
                           f"{window_start} (latest {latest.isoformat()})"), roles
    if not mech:
        return False, f"{hid}: " + missing(
            "mechanism evidence missing: no cited commit diff, configuration, component metric other than "
            "error/latency/request rate, or log/trace that shows the stated mechanism",
            {"commits", "configs", "metrics", "logs", "traces"}), roles
    if not effect or (len(effect) == 1 and len(mech) == 1 and effect[0].evidence_id == mech[0].evidence_id):
        return False, f"{hid}: " + missing(
            "runtime effect missing: no cited log, trace or metric (besides the mechanism record) shows the "
            "symptom", {"logs", "traces", "metrics"}), roles

    # timing: only where both sides have timestamps
    if change_based:
        t_change = min((t for t in (_ts(r.event_time) for r in change if r.source != "configs") if t), default=None)
        onsets = [t for t in (onset(r) for r in effect) if t]
        if t_change and onsets:
            if all(t < t_change - TOLERANCE for t in onsets):
                roles["timing"] = "inconsistent"
                return False, (f"{hid}: timing inconsistent: every cited effect starts before the cited change "
                               f"({min(onsets).isoformat()} < {t_change.isoformat()})"), roles
            roles["timing"] = "consistent"
        else:
            roles["timing"] = "no timestamps"
    return True, (f"{hid} supported: change={roles['change'] or '-'} mechanism={roles['mechanism']} "
                  f"effect={roles['effect']} timing={roles['timing']}"), roles
