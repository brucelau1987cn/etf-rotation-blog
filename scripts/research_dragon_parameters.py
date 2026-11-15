#!/usr/bin/env python3
"""Read-only, offline diagnostic research for low-chip dragon signals."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path('/root/.hermes/cache/scratch').resolve()


def connect_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def validate_output(path):
    path = Path(path).expanduser().resolve()
    if not path.is_relative_to(SCRATCH) or path.is_relative_to(ROOT):
        raise ValueError('output must be inside scratch and outside the repository')
    if path.exists():
        raise ValueError('output must be a new file')
    return path


def load_snapshots(directory):
    snapshots = {}
    for path in sorted(Path(directory).glob('*.json')):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', path.name):
            continue
        payload = json.loads(path.read_text())
        day = path.stem
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError(f'snapshot date must be YYYY-MM-DD: {path}')
        if payload.get('data_as_of') != day:
            raise ValueError(f'snapshot date mismatch: {path}')
        snapshots[day] = sorted({row.split('.')[0] for row in payload['intersection']})
    if not snapshots:
        raise ValueError('no exact-date historical snapshots')
    return snapshots


def signal_module(family):
    import importlib
    import sys
    if str(ROOT / 'scripts') not in sys.path:
        sys.path.insert(0, str(ROOT / 'scripts'))
    return importlib.import_module(family + '_signal')


def evaluate_prefix(family, params, prefix):
    """Sequential scoped overrides; restore production globals even on failure."""
    mod = signal_module(family)
    original = {key: getattr(mod, key) for key in params}
    try:
        for key, value in params.items():
            setattr(mod, key, value)
        return mod.evaluate(prefix), mod.MIN_BARS
    finally:
        for key, value in original.items():
            setattr(mod, key, value)


def candidate_grid():
    for ratio in (.74, .80, .85):
        for window in (5, 10, 20):
            yield 'jinlong', {'RATIO_DEEP_DROP': ratio}, window
    for period, level in ((9, 20), (9, 25), (14, 20)):
        for window in (5, 10, 20):
            yield 'yinlong', {'RSI_PERIOD': period, 'RSI_LEVEL': level}, window
    for window in (5, 10, 20):
        yield 'beili', {}, window


def forward_return(series, index, horizon, cutoff=None):
    """Next available bar open -> close of the h-th held available bar."""
    import math
    end = index + horizon
    if end >= len(series):
        return None, 'insufficient_future_bars'
    if cutoff and series[end]['trade_date'] >= cutoff:
        return None, 'purged_at_split'
    opening = series[index + 1]['open']
    close = series[end]['close']
    if opening is None or close is None or not math.isfinite(opening) or not math.isfinite(close) or opening <= 0 or close <= 0:
        return None, 'invalid_price'
    return close / opening - 1, None


def research(snapshots, bars, split_date='2026-10-01', *, min_samples=5, evaluator=None):
    from statistics import fmean
    use_signal_at = evaluator is None
    evaluator = evaluator or evaluate_prefix
    dates = sorted(snapshots)
    indices = {code: {b['trade_date']: i for i, b in enumerate(series)} for code, series in bars.items()}
    cache = {}
    schemes = []
    for family, params, window in candidate_grid():
        eligible, hits, events = [], [], []
        last_event = {}
        for day in dates:
            for code in snapshots[day]:
                index = indices.get(code, {}).get(day)
                if index is None:
                    continue
                key = (family, tuple(sorted(params.items())), code)
                if family == 'beili' and use_signal_at:
                    state_key = key + (day,)
                    if state_key not in cache:
                        cache[state_key] = signal_module(family).signal_at(bars[code], day)
                    state = cache[state_key]
                    if state['status'] != 'ok':
                        continue
                    age = state['bars_since_hit']
                    hit = state['active'] and age is not None and age <= window
                else:
                    if key not in cache:
                        # evaluate is causal; compute once up to the final observation.
                        stop = max((i for d, i in indices[code].items() if d <= dates[-1]), default=-1)
                        computed, min_bars = evaluator(family, params, bars[code][:stop + 1])
                        field = 'divergence' if family == 'beili' else 'xg'
                        cache[key] = ([(row[field], row.get('divergence_void', False))
                                       for row in computed], min_bars)
                    rows, min_bars = cache[key]
                    if index + 1 < min_bars:
                        continue
                    start = max(min_bars - 1, index - window)
                    last_hit = next((j for j in range(index, start - 1, -1) if rows[j][0]), None)
                    hit = last_hit is not None
                    if last_hit is not None and family == 'beili':
                        hit = not any(rows[j][1] for j in range(last_hit + 1, index + 1))
                record = (day, code, index)
                eligible.append(record)
                if hit:
                    hits.append(record)
                    if index - last_event.get(code, -1000000) >= 20:
                        events.append(record)
                        last_event[code] = index
        partitions = {}
        for name in ('all', 'train', 'validation'):
            selected = [r for r in hits if name == 'all' or (r[0] < split_date) == (name == 'train')]
            dedup = [r for r in events if name == 'all' or (r[0] < split_date) == (name == 'train')]
            pool = [r for r in eligible if name == 'all' or (r[0] < split_date) == (name == 'train')]
            cutoff = split_date if name == 'train' else None
            forward = {}
            for horizon in (5, 10, 20, 60):
                pool_by_date = {}
                for day, code, index in pool:
                    value, _ = forward_return(bars[code], index, horizon, cutoff)
                    if value is not None:
                        pool_by_date.setdefault(day, []).append(value)
                returns, excess = [], []
                reasons = {}
                for day, code, index in dedup:
                    value, reason = forward_return(bars[code], index, horizon, cutoff)
                    if reason:
                        reasons[reason] = reasons.get(reason, 0) + 1
                    elif pool_by_date.get(day):
                        returns.append(value)
                        excess.append(value - fmean(pool_by_date[day]))
                sufficient = len(returns) >= min_samples
                forward[str(horizon)] = {
                    'status': 'OK' if sufficient else 'UNAVAILABLE',
                    'reason': None if sufficient else 'insufficient_samples',
                    'n': len(returns), 'min_samples': min_samples,
                    'mean_return': fmean(returns) if sufficient else None,
                    'mean_excess': fmean(excess) if sufficient else None,
                    'purged_at_split': reasons.get('purged_at_split', 0),
                    'unavailable_reasons': reasons,
                }
            partitions[name] = {
                'stock_days': len(selected),
                'unique_stocks': len({r[1] for r in selected}),
                'unique_dates': len({r[0] for r in selected}),
                'eligible_stock_days': len(pool),
                'deduplicated_events': len(dedup), 'forward': forward,
            }
        schemes.append({'family': family, 'parameters': params, 'window_bars': window,
                        'current_hits': sum(r[0] == dates[-1] for r in hits), **partitions})
    return {
        'schema_version': 'dragon-parameter-shadow-v1',
        'status': 'diagnostic', 'split_date': split_date,
        'train_rule': 'signal_date < split_date; outcome close date < split_date',
        'validation_rule': 'signal_date >= split_date',
        'current_as_of': dates[-1], 'snapshot_dates': len(dates),
        'assumptions': {
            'entry': 'next available bar open',
            'exit': 'close of h-th available bar after signal date',
            'return_unit': 'decimal', 'costs': 'ignored', 'execution_constraints': 'ignored',
            'adjustment': 'cached qfq; point-in-time adjustment vintages unavailable',
            'benchmark': 'same-date historical members with family MIN_BARS and evaluable horizon',
            'dedup': 'per scheme and stock, accepted observation indices differ by >=20; continuous across split',
            'window': 'bars_since_hit <= window (W+1 bars); warmup excluded; beili signal_at v2 frozen invalidation',
            'interpretation': 'diagnostic only; parameter optimality and deployability unestablished',
        },
        'schemes': schemes,
    }


def main(argv=None):
    import argparse
    from datetime import date
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data/local/jinlong-bars.db')
    parser.add_argument('--history-dir', type=Path, default=ROOT / 'public/data/low-chip-history')
    parser.add_argument('--output', type=Path, required=True, help='new JSON file under /root/.hermes/cache/scratch')
    parser.add_argument('--split-date', default='2026-10-01')
    parser.add_argument('--min-samples', type=int, default=5)
    args = parser.parse_args(argv)
    try:
        output = validate_output(args.output)
        if date.fromisoformat(args.split_date).isoformat() != args.split_date:
            raise ValueError('split-date must be YYYY-MM-DD')
        if args.min_samples < 1:
            raise ValueError('min-samples must be positive')
        snapshots = load_snapshots(args.history_dir)
        codes = {code for members in snapshots.values() for code in members}
        bars = {}
        with connect_readonly(args.db) as db:
            db.row_factory = sqlite3.Row
            for code in sorted(codes):
                bars[code] = [dict(row) for row in db.execute(
                    'SELECT trade_date, open, high, low, close FROM bars WHERE code=? ORDER BY trade_date',
                    (code,))]
        report = research(snapshots, bars, args.split_date, min_samples=args.min_samples)
        report['inputs'] = {'db': str(args.db.resolve()), 'history_dir': str(args.history_dir.resolve())}
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create: a concurrently created destination remains protected.
        with output.open('x') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        print(json.dumps({'status': 'diagnostic', 'output': str(output), 'schemes': len(report['schemes'])}))
        return 0
    except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
