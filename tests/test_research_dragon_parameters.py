"""Offline shadow-research contract tests; fixtures never touch production data."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/research_dragon_parameters.py'


@pytest.fixture(autouse=True)
def block_external_io(monkeypatch):
    import socket
    import subprocess
    def forbidden(*args, **kwargs):
        raise AssertionError('external I/O used by offline test')
    for name in ('create_connection', 'getaddrinfo'):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def module():
    assert SCRIPT.exists(), 'shadow research CLI is missing'
    spec = importlib.util.spec_from_file_location('dragon_research', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_readonly_exact_snapshot_and_output_guard(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, 'SCRATCH', tmp_path.resolve())
    db = tmp_path / 'bars.db'
    with sqlite3.connect(db) as con:
        con.execute('create table bars(code text, trade_date text, open real, high real, low real, close real)')
    with m.connect_readonly(db) as con:
        with pytest.raises(sqlite3.OperationalError):
            con.execute('create table forbidden(x)')
    history = tmp_path / 'history'
    history.mkdir()
    (history / '2026-10-01.json').write_text(json.dumps({'data_as_of': '2026-10-01', 'intersection': ['000001.SZ']}))
    (history / '2026-10-01-copy.json').write_text('{}')
    assert m.load_snapshots(history) == {'2026-10-01': ['000001']}
    for path in [m.ROOT / 'public/data/probe.json', m.ROOT / 'data/probe.json', SCRIPT]:
        with pytest.raises(ValueError):
            m.validate_output(path)
    with pytest.raises(ValueError):
        m.validate_output(Path('/root/dragon-forbidden.json'))
    assert m.validate_output(m.SCRATCH / 'dragon-test.json').parent == m.SCRATCH


def test_load_snapshots_rejects_invalid_calendar_date(tmp_path):
    m = module()
    day = '2026-02-30'
    (tmp_path / f'{day}.json').write_text(json.dumps({
        'data_as_of': day, 'intersection': ['000001.SZ'],
    }))
    with pytest.raises(ValueError):
        m.load_snapshots(tmp_path)


def test_research_membership_minbars_dedup_split_and_next_open():
    from datetime import date, timedelta
    m = module()
    days = [(date(2026, 8, 1) + timedelta(days=i)).isoformat() for i in range(100)]
    bars = {code: [dict(trade_date=d, open=10.0, close=11.0 if code == 'A' else 10.0, high=12.0, low=9.0) for d in days] for code in ['A', 'B', 'FUTURE']}
    # Historical pools differ from the latest membership; FUTURE never enters a historical pool.
    snapshots = {days[i]: ['A', 'B'] for i in range(4, 65)}
    snapshots[days[65]] = ['B', 'FUTURE']
    seen = []
    def evaluator(family, params, prefix):
        seen.append(prefix[-1]['trade_date'])
        return [dict(xg=True, divergence=True, divergence_void=False) for _ in prefix], 5
    report = m.research(snapshots, bars, days[40], min_samples=1, evaluator=evaluator)
    assert len(report['schemes']) == 21
    scheme = report['schemes'][0]
    assert scheme['all']['stock_days'] == 124
    assert scheme['all']['unique_stocks'] == 3
    assert scheme['all']['unique_dates'] == 62
    assert scheme['current_hits'] == 2
    assert scheme['all']['deduplicated_events'] == 9
    metric = scheme['train']['forward']['5']
    assert metric['status'] == 'OK'
    assert metric['mean_return'] == pytest.approx(.05)
    assert metric['mean_excess'] == pytest.approx(0)
    assert scheme['train']['forward']['60']['status'] == 'UNAVAILABLE'
    assert max(seen) == days[65]  # Cached causal series ends at final observation.
    # Training outcomes crossing split are purged.
    assert metric['purged_at_split'] > 0 or scheme['train']['forward']['20']['purged_at_split'] > 0


def test_parameter_constants_restore_on_evaluation_exception(monkeypatch):
    m = module()
    mod = m.signal_module('jinlong')
    original = mod.RATIO_DEEP_DROP
    def fail(bars):
        assert mod.RATIO_DEEP_DROP == .85
        raise RuntimeError('fixture failure')
    monkeypatch.setattr(mod, 'evaluate', fail)
    with pytest.raises(RuntimeError, match='fixture failure'):
        m.evaluate_prefix('jinlong', {'RATIO_DEEP_DROP': .85}, [{}])
    assert mod.RATIO_DEEP_DROP == original


def test_cli_offline_fixture_json_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    import socket
    m = module()
    def forbidden(*args, **kwargs):
        raise AssertionError('network used')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    history = tmp_path / 'history'
    history.mkdir()
    (history / '2026-10-02.json').write_text(json.dumps({'data_as_of': '2026-10-02', 'intersection': ['000001.SZ']}))
    db = tmp_path / 'fixture.db'
    with sqlite3.connect(db) as con:
        con.execute('create table bars(code text, trade_date text, open real, high real, low real, close real)')
        con.execute("insert into bars values('000001','2026-10-02',10,11,9,10)")
    original = db.read_bytes()
    monkeypatch.setattr(m, 'SCRATCH', tmp_path.resolve())
    output = tmp_path / 'report.json'
    assert m.main(['--db', str(db), '--history-dir', str(history), '--output', str(output)]) == 0
    report = json.loads(output.read_text())
    assert report['status'] == 'diagnostic'
    assert len(report['schemes']) == 21
    assert report['schemes'][0]['all']['forward']['5']['status'] == 'UNAVAILABLE'
    assert db.read_bytes() == original
    with pytest.raises(SystemExit):
        m.main(['--db', str(db), '--history-dir', str(history), '--output', str(output)])
    assert json.loads(capsys.readouterr().out)['output'] == str(output)


def test_window_void_and_pool_excess(monkeypatch):
    m = module()
    from datetime import date, timedelta
    days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(35)]
    bars = {code: [dict(trade_date=d, open=10., close=12. if code == 'A' else 10.) for d in days] for code in ['A', 'B']}
    def evaluator(family, params, prefix):
        is_a = prefix[0]['close'] == 12.
        return [dict(xg=is_a and i == 4, divergence=is_a and i == 4, divergence_void=i == 7) for i in range(len(prefix))], 5
    monkeypatch.setattr(m, 'candidate_grid', lambda: iter([('jinlong', {}, 5), ('beili', {}, 10)]))
    report = m.research({days[i]: ['A', 'B'] for i in range(3, 11)}, bars, '2026-10-01', min_samples=1, evaluator=evaluator)
    gold, beili = report['schemes']
    assert gold['all']['stock_days'] == 6  # distance <=5: indices 4 through 9
    assert gold['all']['eligible_stock_days'] == 14  # index 3 lacks MIN_BARS
    assert beili['all']['stock_days'] == 3  # indices 4 through 6; void at index 7
    assert gold['all']['forward']['5']['mean_return'] == pytest.approx(.2)
    assert gold['all']['forward']['5']['mean_excess'] == pytest.approx(.1)


def test_inclusive_20_boundary_and_cached_causal_rows(monkeypatch):
    m = module()
    bars = {'A': [dict(trade_date=f'2026-01-{i + 1:02d}') for i in range(25)]}
    calls = []
    def evaluator(family, params, prefix):
        calls.append(len(prefix))
        return [dict(xg=i == 4) for i in range(len(prefix))], 5
    monkeypatch.setattr(m, 'candidate_grid', lambda: iter([('jinlong', {}, 20), ('jinlong', {}, 5)]))
    report = m.research({'2026-01-24': ['A'], '2026-01-25': ['A']}, bars, evaluator=evaluator)
    assert report['schemes'][0]['all']['stock_days'] == 2
    assert report['schemes'][1]['all']['stock_days'] == 0
    assert calls == [25]


def test_beili_v2_frozen_threshold_remains_voided(monkeypatch):
    m = module()
    mod = m.signal_module('beili')
    monkeypatch.setattr(mod, 'MIN_BARS', 2)
    bars = [dict(trade_date=f'2026-01-{i + 1:02d}', close=10.) for i in range(6)]
    rows = [dict(trade_date=b['trade_date'], close=10., divergence=i == 1,
                 direct_divergence=i == 1, separated_divergence=False,
                 divergence_void=False, difl1=-1., difl2=-2., difl3=-3.,
                 dif=-2.5 if i == 3 else -1., macd_hist=-1., cl1=1., cl2=2., cl3=3.)
            for i, b in enumerate(bars)]
    monkeypatch.setattr(mod, 'evaluate', lambda prefix: rows[:len(prefix)])
    monkeypatch.setattr(m, 'candidate_grid', lambda: iter([('beili', {}, 20)]))
    state = mod.signal_at(bars)
    assert state['voided'] and not state['legacy_voided']
    report = m.research({bars[-1]['trade_date']: ['A']}, {'A': bars})
    assert report['schemes'][0]['current_hits'] == 0
