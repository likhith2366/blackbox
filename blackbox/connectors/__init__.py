"""Read-only live connectors (CONTRACT.md s10c). Owner: Agent 4.

Connectors return blackbox.schemas.BackendResult with EvidenceRecords marked live
(attributes.live=True, fixture=False). They never write, never raise to the caller and never
expose credentials. Evaluation uses frozen snapshots only; live data never enters the matrix.
"""
