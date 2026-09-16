import sqlite3

from scripts.rebuild_low_chip_base_from_raw_pool import build_payload


def test_rebuilds_three_period_intersection_and_excludes_bj():
    conn = sqlite3.connect(':memory:')
    conn.executescript('''
      CREATE TABLE low_chip_raw_pool_meta(
        trade_date TEXT, threshold REAL, universe TEXT, listing_cutoff TEXT,
        listing_min_days INTEGER, intersection_count INTEGER, generated_at TEXT
      );
      CREATE TABLE low_chip_raw_pool(
        trade_date TEXT, stock_code TEXT, period TEXT, stock_name TEXT,
        profit_ratio REAL, price REAL, change_percent REAL
      );
    ''')
    conn.execute("INSERT INTO low_chip_raw_pool_meta VALUES(?,?,?,?,?,?,?)", (
        '2026-09-16', 1.5, 'A股', '2026-06-18', 90, 2, '2026-09-16T16:00:00+08:00',
    ))
    for period in ('week', 'month', 'quarter'):
        conn.executemany("INSERT INTO low_chip_raw_pool VALUES(?,?,?,?,?,?,?)", [
            ('2026-09-16', '000001.SZ', period, '平安银行', 0.2, 10, -1),
            ('2026-09-16', '920001.BJ', period, '北交样本', 0.3, 20, -2),
        ])

    payload = build_payload(conn, '2026-09-16')

    assert payload['intersection_before_filters'] == ['000001.SZ', '920001.BJ']
    assert payload['intersection'] == ['000001.SZ']
    assert payload['filters']['excluded_bj'] == ['920001.BJ']
    assert payload['counts'] == {'week': 2, 'month': 2, 'quarter': 2, 'year': 0}
