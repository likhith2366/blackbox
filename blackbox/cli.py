"""Command line entry points.

python -m blackbox.cli investigate --incident INC-0000 --system blackbox --out runs/_manual/x
python -m blackbox.cli investigate --incident INC-0000 --system b1 --b1-from runs/_manual/x/output.json --out ...
python -m blackbox.cli show runs/_manual/x/output.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from blackbox.schemas import RunConfig, SystemOutput


def load_config(path: str | None) -> RunConfig:
    if not path:
        return RunConfig()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping of RunConfig fields")
    unknown = sorted(set(data) - set(RunConfig.model_fields))
    if unknown:  # a typo must not silently fall back to a default (QA-F09)
        raise ValueError(f"{path}: unknown config key(s) {unknown}")
    return RunConfig(**data)


def summarize(out: SystemOutput) -> str:
    r = out.report
    lines = [
        f"{out.system} on {out.incident_id} (ablation={out.ablation}) -> run {out.status}"
        + (f" | error: {out.error}" if out.error else ""),
        f"rounds={out.rounds_used} tool_calls={out.tool_calls_used} model_calls={out.usage.model_calls} "
        f"tokens={out.usage.prompt_tokens}+{out.usage.completion_tokens} wall={out.wall_seconds:.1f}s "
        f"budget_violations={out.budget_violations} unauthorized_attempts={len(out.unauthorized_attempts)} "
        f"evidence_seen={len(out.evidence_seen)}",
    ]
    if out.evidence_budget:
        eb = out.evidence_budget
        lines.append(f"evidence budget: {eb.evidence_tokens_total}/{eb.evidence_token_budget} tokens, "
                     f"included={len(eb.included_ids)} omitted={len(eb.omitted_ids)} matched={eb.matched}")
    if r:
        lines.append(f"REPORT [{r.status}] {r.headline}")
        for c in r.ranked_causes:
            lines.append(f"  {c.rank}. {c.component} :: {c.mechanism} ({c.confidence}) {c.evidence_ids}")
        for c in r.claims:
            lines.append(f"  - ({c.kind}) {c.text} {c.evidence_ids}")
        if r.next_check:
            lines.append(f"  next check: {r.next_check}")
        if r.invalid_citations:
            lines.append(f"  invalid citations removed: {r.invalid_citations}")
    return "\n".join(lines)


def trace(out: SystemOutput) -> str:
    lines = []
    for s in out.steps:
        lines.append(f"--- round {s.round}: model={s.model_decision or '?'} -> {s.stop_decision} "
                     f"({s.stop_reason}) | {s.model_seconds:.1f}s, {s.prompt_tokens}+{s.completion_tokens} tok"
                     + ("" if s.parse_ok else f" | PARSE FAIL: {s.parse_error}"))
        for ch in s.hypothesis_changes:
            lines.append(f"    {ch}")
        for req, res in zip(s.tool_requests, s.tool_results):
            lines.append(f"    {req.call_id} {req.tool}({json.dumps(req.args)[:110]}) -> {res.status} "
                         f"{len(res.records)} rec" + (f" | {res.error[:100]}" if res.error else ""))
        if s.invalid_citations:
            lines.append(f"    invalid citations: {s.invalid_citations}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (FileNotFoundError, ValueError, yaml.YAMLError) as exc:   # user errors: one line, exit 2 (QA-F09)
        print(f"blackbox: error: {exc}".splitlines()[0][:300], file=sys.stderr)
        raise SystemExit(2)


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="blackbox")
    sub = p.add_subparsers(dest="cmd", required=True)
    inv = sub.add_parser("investigate")
    inv.add_argument("--incident", required=True)
    inv.add_argument("--system", choices=["blackbox", "b0", "b1"], required=True)
    inv.add_argument("--ablation", default="none", choices=["none", "no_jira", "no_provenance", "no_traces"])
    inv.add_argument("--b1-from", help="output.json of the initial BlackBox run (required for b1)")
    inv.add_argument("--config", help="YAML with RunConfig overrides")
    inv.add_argument("--snapshot-root")
    inv.add_argument("--live", action="store_true",
                     help="demonstration only: commits/configs from GitHub, logs from the app's JSONL files")
    inv.add_argument("--repo", default="likhith2366/blackbox-demo-shop")
    inv.add_argument("--telemetry-dir", help="live log directory (default: the demo's current telemetry dir)")
    inv.add_argument("--capture-id", help="record GitHub responses verbatim under scenarios/live_captures/<id>/github")
    inv.add_argument("--out", required=True)
    show = sub.add_parser("show")
    show.add_argument("output_json")
    args = p.parse_args(argv)

    if args.cmd == "show":
        if not Path(args.output_json).exists():
            p.error(f"no such file: {args.output_json}")
        out = SystemOutput.model_validate_json(Path(args.output_json).read_text(encoding="utf-8"))
        print(summarize(out))
        print(trace(out))
        return 0

    from blackbox.evidence.backend import load_backend  # Agent 2
    from blackbox.systems import run_system

    cfg = load_config(args.config)
    root = Path(args.snapshot_root) if args.snapshot_root else None
    if args.live:
        if args.ablation != "none" or args.system != "blackbox":
            p.error("--live is a BlackBox demonstration mode; baselines and ablations use frozen snapshots")
        from blackbox.connectors.capture import capture_dir_for
        from blackbox.evidence.live import load_live_backend
        backend = load_live_backend(args.incident, args.repo,
                                    telemetry_dir=Path(args.telemetry_dir) if args.telemetry_dir else None,
                                    capture_dir=capture_dir_for(args.capture_id) if args.capture_id else None,
                                    snapshot_root=root)
    else:
        backend = load_backend(args.incident, ablation=args.ablation, snapshot_root=root)
    b1_evidence = None
    if args.system == "b1":
        if args.ablation != "none":
            p.error("b1 is defined for ablation none only (it replays the full-evidence BlackBox run)")
        if not args.b1_from:
            p.error("--b1-from is required for b1")
        if not Path(args.b1_from).exists():
            p.error(f"no such file: {args.b1_from}")
        base = SystemOutput.model_validate_json(Path(args.b1_from).read_text(encoding="utf-8"))
        if base.system != "blackbox" or base.incident_id != args.incident or base.ablation != "none":
            p.error("--b1-from must be a full-evidence BlackBox run of the same incident")
        b1_evidence = base.evidence_seen
    out = run_system(args.system, backend, cfg, b1_evidence=b1_evidence)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "output.json").write_text(out.model_dump_json(indent=2), encoding="utf-8")
    print(summarize(out))
    if out.steps:
        print(trace(out))
    print(f"saved {out_dir / 'output.json'}")
    return 0 if out.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
