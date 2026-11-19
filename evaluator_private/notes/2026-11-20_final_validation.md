# Final validation notes (Agent 2, 2026-11-20 ~04:05Z). Proposals only; nothing frozen was changed.

## Config ids under no_provenance
Under no_provenance, get_config(service) returns the latest repository version at the cutoff, with the
note "deployed version unknown". The text "deployed" in that note is a negative statement. The
config content holds no deploy ids, build ids or digests.
The config id carries the sha of the commit that introduced that config version. It equals a deployed
commit in exactly one case:
- INC-2691 (s3, frozen): config:checkout-svc@7595162 == commit of deploy d-545 (the fault deploy).
In every other snapshot the config shas (bc913fb, b1e5a60) are older baseline-history commits that no
deployment record in the snapshot names. INC-1846 has no configs source at all.
Proposed fix (not applied; the backend belongs to Agent 1):
- In the no_provenance view only, mask the sha in config ids and in the content header, e.g.
  config:checkout-svc@rev2 with revision ordinals, and in attributes.versions.
- Agent 3's "config ids identical to un-ablated" rule would then need an exception for configs
  under no_provenance.
- The frozen answer key keeps config:checkout-svc@7595162 for ablation "none".
- Caveat: commit 7595162 itself stays searchable (CONTRACT s8), and its commit time lies a few
  seconds before the (time-only) deployment of checkout-svc. The temporal link can still be inferred
  either way; only the explicit id equality would be removed.
Affected frozen ablation cell: INC-2691 x no_provenance.

## Retrieval risk in s3 (backend grouping)
search_logs groups lines by service|level|msg|field names. The two checkout "checkout-svc starting"
lines fall into one group: the baseline line (base_url 18102) and the decisive log:df9fe83f35
(base_url 18112). A query without a time window or "18112" therefore returns the baseline line with
"x2 similar". The decisive id is reachable through a time window after the deploy, a query "18112",
or the config/commit evidence. Suggestion for Agent 1: do not merge startup lines whose field values
differ, or show the latest occurrence of each group.
