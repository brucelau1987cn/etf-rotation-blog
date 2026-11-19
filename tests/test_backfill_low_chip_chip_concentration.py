"""Historical concentration backfill date gates, with offline provider responses."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backfill_low_chip_chip_concentration as backfill  # noqa: E402

DAY = "2026-11-12"
CODE = "000401.SZ"


@pytest.fixture
def history(tmp_path, monkeypatch):
    queries = []

    def fake_query(args, timeout):
        query = args[args.index("-q") + 1]
        queries.append(query)
        field = query.split()[-1]
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "code_count": 1, "has_more": False,
            "datas": [{"股票代码": CODE, field: 8.4}],
        }))

    monkeypatch.setattr(backfill, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(backfill, "REQUEST_INTERVAL_S", 0)
    monkeypatch.setattr(backfill.cc, "_run_query", fake_query)
    return tmp_path, queries


def _snapshot(directory, stem, date_fields, **overrides):
    path = directory / f"{stem}.json"
    payload = {"intersection": [CODE], "enrichments": {CODE: {"industry": "水泥"}}}
    payload.update(date_fields)
    payload.update(overrides)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path, path.read_bytes()


@pytest.mark.parametrize(("stem", "date_fields"), [
    pytest.param(DAY, {"data_as_of": DAY + "garbage"}, id="suffix"),
    pytest.param(DAY, {"data_as_of": DAY + "T00:00:00"}, id="timestamp"),
    pytest.param(DAY, {}, id="missing"),
    pytest.param(DAY, {"data_as_of": ""}, id="empty"),
    pytest.param(DAY, {"data_as_of": None}, id="null"),
    pytest.param(DAY, {"data_as_of": 20261112}, id="non-string"),
    pytest.param(DAY, {"data_as_of": "2026-11-13"}, id="mismatch"),
    pytest.param("2026-02-29", {"data_as_of": "2026-02-29"}, id="impossible-calendar-date"),
])
def test_rejects_invalid_snapshot_dates_without_query_or_write(history, monkeypatch, capsys, stem, date_fields):
    directory, queries = history
    path, before = _snapshot(directory, stem, date_fields)
    monkeypatch.setattr(sys, "argv", ["backfill", "--day", stem, "--force"])

    status = backfill.main()

    assert (status, queries, path.read_bytes()) == (1, [], before)
    error = capsys.readouterr().err
    assert path.name in error
    assert "data_as_of" in error


@pytest.mark.parametrize("overrides", [
    pytest.param({"intersection": []}, id="empty-pool"),
    pytest.param({"enrichments": {CODE: {"chip_concentration": {"status": "ok"}}}}, id="already-complete"),
])
def test_invalid_snapshot_date_blocks_normal_skip(history, monkeypatch, capsys, overrides):
    directory, queries = history
    path, before = _snapshot(directory, DAY, {"data_as_of": DAY + "garbage"}, **overrides)
    monkeypatch.setattr(sys, "argv", ["backfill", "--day", DAY, "--dry-run"])

    status = backfill.main()

    assert (status, queries, path.read_bytes()) == (1, [], before)
    output = capsys.readouterr()
    assert path.name in output.err
    assert "skipped" not in output.out


def test_backfills_selected_day_with_its_exact_date(history, monkeypatch):
    directory, queries = history
    path, before = _snapshot(directory, DAY, {"data_as_of": DAY})
    other, other_before = _snapshot(directory, "2026-11-13", {})
    monkeypatch.setattr(sys, "argv", ["backfill", "--day", DAY])

    assert backfill.main() == 0

    assert queries == [f"{CODE} 集中度90[20261112]"]
    assert other.read_bytes() == other_before
    payload = json.loads(path.read_bytes())
    contract = payload.pop("chip_concentration_contract")
    item = payload["enrichments"][CODE].pop("chip_concentration")
    assert (contract["as_of"], item["as_of"], item["concentration_90"]) == (DAY, DAY, 8.4)
    assert payload == json.loads(before)


def test_date_errors_keep_nonzero_status_after_backfilling_other_days(history, monkeypatch, capsys):
    directory, queries = history
    bad, before = _snapshot(directory, "2026-11-11", {"data_as_of": DAY})
    good, _ = _snapshot(directory, DAY, {"data_as_of": DAY})
    monkeypatch.setattr(sys, "argv", ["backfill"])

    assert backfill.main() == 1

    assert bad.read_bytes() == before
    assert queries == [f"{CODE} 集中度90[20261112]"]
    assert json.loads(good.read_bytes())["chip_concentration_contract"]["as_of"] == DAY
    assert bad.name in capsys.readouterr().err
