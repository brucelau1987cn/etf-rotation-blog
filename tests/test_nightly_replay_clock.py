"""Tests for the historical-replay clock plumbing.

A missed trade date can only be replayed if the 22:00 gate and the stage runner
let the caller supply the trade date. Both must keep evaluating the SAME rules —
the override only replaces `datetime.now(CN)`, never a gate condition.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

CN = ZoneInfo("Asia/Shanghai")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prepare_module():
    return _load("prepare_replay_test", "prepare_a_share_nightly.py")


def test_prepare_forwards_replay_clock_to_the_gate(prepare_module, monkeypatch, tmp_path):
    commands = []

    def fake_run_json(command, **kwargs):
        commands.append(command)
        return {"decision": "blocked", "reason": "nightly precheck-cache not completed"}

    monkeypatch.setattr(prepare_module, "run_json", fake_run_json)
    state = tmp_path / "state.json"

    prepare_module.prepare(now=datetime(2026, 10, 30, 22, 35, tzinfo=CN), state_path=state)

    assert commands == [[
        "python3",
        "scripts/check_a_share_cron_gate.py",
        "--stage",
        "22:00",
        "--now",
        "2026-10-30T22:35:00+08:00",
    ]]


def test_prepare_without_clock_leaves_the_gate_on_the_wall_clock(prepare_module, monkeypatch, tmp_path):
    commands = []

    def fake_run_json(command, **kwargs):
        commands.append(command)
        return {"decision": "blocked", "reason": "outside 22:00 execution window"}

    monkeypatch.setattr(prepare_module, "run_json", fake_run_json)

    prepare_module.prepare(state_path=tmp_path / "state.json")

    assert commands == [["python3", "scripts/check_a_share_cron_gate.py", "--stage", "22:00"]]


def test_stage_runner_replay_clock_pins_marker_and_prepare_stage(monkeypatch, tmp_path):
    runner = _load("nightly_stage_replay_test", "run_a_share_nightly_stage.py")
    as_of = datetime(2026, 10, 30, 22, 10, tzinfo=CN)
    monkeypatch.setattr(runner, "AS_OF", as_of)

    assert runner.now_iso() == "2026-10-30T22:10:00+08:00"
    assert runner.stage_command("prepare")[-2:] == ["--now", "2026-10-30T22:10:00+08:00"]
    # Stages that derive their trade date from the cache must NOT be rewritten.
    assert runner.stage_command("cache") == list(runner.STAGES["cache"])


def test_stage_runner_without_replay_clock_keeps_global_stage_commands(monkeypatch):
    runner = _load("nightly_stage_normal_test", "run_a_share_nightly_stage.py")
    monkeypatch.setattr(runner, "AS_OF", None)

    for name in runner.STAGES:
        assert runner.stage_command(name) == list(runner.STAGES[name])