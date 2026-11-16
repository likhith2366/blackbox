"""Agent 4 QA: `python -m blackbox.cli` argument and config handling (in-process, no model).

blackbox.systems.run_system is replaced by a sentinel that raises, so no test can start an
investigation; the conftest also blocks Ollama.
"""
from __future__ import annotations

import pytest

pytest.importorskip("blackbox.evidence.backend")

import blackbox.systems as systems  # noqa: E402
from blackbox.cli import load_config, main  # noqa: E402
from blackbox.schemas import SystemOutput  # noqa: E402


class Reached(Exception):
    pass


@pytest.fixture(autouse=True)
def _no_runs(monkeypatch):
    def sentinel(*a, **k):
        raise Reached("run_system reached")
    monkeypatch.setattr(systems, "run_system", sentinel)


def _base(tmp_path, **kw):
    out = SystemOutput(**{"system": "blackbox", "incident_id": "INC-9001", "status": "completed", **kw})
    p = tmp_path / "base.json"
    p.write_text(out.model_dump_json(), encoding="utf-8")
    return str(p)


def inv(snap, *extra):
    return ["investigate", "--incident", "INC-9001", "--snapshot-root", str(snap), "--out", "unused", *extra]


def test_dev_config_loads_and_matches_contract():
    cfg = load_config("configs/dev.yaml")
    assert (cfg.num_ctx, cfg.max_rounds, cfg.max_tool_calls, cfg.max_calls_per_round) == (8192, 6, 12, 3)


def test_missing_b1_source_is_a_usage_error(mini_snapshot):
    with pytest.raises(SystemExit) as e:
        main(inv(mini_snapshot, "--system", "b1"))
    assert e.value.code == 2


@pytest.mark.parametrize("kw", [{"system": "b0"}, {"incident_id": "INC-9002"}, {"ablation": "no_jira"}])
def test_wrong_b1_source_is_refused(mini_snapshot, tmp_path, kw):
    with pytest.raises(SystemExit) as e:
        main(inv(mini_snapshot, "--system", "b1", "--b1-from", _base(tmp_path, **kw)))
    assert e.value.code == 2


def test_valid_b1_source_reaches_run_system(mini_snapshot, tmp_path):
    with pytest.raises(Reached):
        main(inv(mini_snapshot, "--system", "b1", "--b1-from", _base(tmp_path)))


# QA-F09 fixed at 963c2ee (merged 59e3c0d); marker removed by Agent 1 while Agent 4 is inactive
def test_b1_with_ablation_is_refused(mini_snapshot, tmp_path):
    with pytest.raises(SystemExit):
        main(inv(mini_snapshot, "--system", "b1", "--ablation", "no_jira", "--b1-from", _base(tmp_path)))


# QA-F09 fixed at 963c2ee (merged 59e3c0d); marker removed by Agent 1 while Agent 4 is inactive
def test_config_typo_is_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("num_ctxx: 4096\n", encoding="utf-8")
    with pytest.raises(Exception):
        load_config(str(p))


@pytest.mark.parametrize("argv_kind", ["missing_incident", "bad_yaml", "missing_b1_file", "show_missing"])
# QA-F09 fixed at 963c2ee (merged 59e3c0d); marker removed by Agent 1 while Agent 4 is inactive
def test_user_errors_give_clean_exit(mini_snapshot, tmp_path, argv_kind):
    bad = tmp_path / "bad.yaml"
    bad.write_text("num_ctx: [unclosed\n", encoding="utf-8")
    argv = {"missing_incident": ["investigate", "--incident", "INC-9999", "--snapshot-root", str(mini_snapshot),
                                 "--system", "blackbox", "--out", "unused"],
            "bad_yaml": inv(mini_snapshot, "--system", "blackbox", "--config", str(bad)),
            "missing_b1_file": inv(mini_snapshot, "--system", "b1", "--b1-from", str(tmp_path / "nope.json")),
            "show_missing": ["show", str(tmp_path / "nope.json")]}[argv_kind]
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2
