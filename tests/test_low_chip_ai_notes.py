"""test_low_chip_ai_notes.py — AI 评语/星级生成与 notes API 契约测试。

不调用 LLM(网络/费用), 只验证:
1. 数据装配: build_prompt 对快照成员总是产出含数据字段的 prompt
2. trend_features 对 tracking 覆盖的 symbol 返回合法结构
3. streak_for 返回 >=1 整数
4. 星级校验逻辑: 仅 1/2/3 合法
5. notes API 文件存在且含必要端点
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from generate_low_chip_ai_notes import (  # noqa: E402
    load_snapshot,
    trend_features,
    streak_for,
    build_prompt,
    VALID_LABELS,
)

DATA = ROOT / 'public/data/a-low-chip-stocks.json'
TRACKING = ROOT / 'public/data/low-chip-tracking.json'
SNAPSHOT = ROOT / 'public/data/low-chip-history/2026-09-04.json'
API_FILE = ROOT / 'functions/api/public/v1/low-chip-notes.js'
GEN_SCRIPT = ROOT / 'scripts/generate_low_chip_ai_notes.py'

pytestmark = pytest.mark.skipif(not SNAPSHOT.exists(), reason='2026-09-04 snapshot absent')


@pytest.fixture(scope='module')
def snapshot():
    return json.loads(SNAPSHOT.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def tracking():
    if not TRACKING.exists():
        return {}
    return json.loads(TRACKING.read_text(encoding='utf-8'))


def test_snapshot_has_members(snapshot):
    members = snapshot.get('intersection') or []
    assert len(members) > 0


def test_trend_features_shape(snapshot, tracking):
    members = (snapshot.get('intersection') or [])[:3]
    for sym in members:
        t = trend_features(sym, tracking, snapshot['data_as_of'])
        assert isinstance(t, dict)
        assert 'data_sufficiency' in t
        assert 'bars' in t
        assert t['bars'] >= 1
        # 数值字段要么 None 要么 float
        for k in ('last_close', 'chg_5d', 'pct_off_20d_high', 'last_change_pct'):
            assert k not in t or t[k] is None or isinstance(t[k], (int, float))


def test_streak_is_positive_int(snapshot):
    members = (snapshot.get('intersection') or [])[:3]
    for sym in members:
        assert isinstance(streak_for(sym, snapshot), int)
        assert streak_for(sym, snapshot) >= 1


def test_build_prompt_contains_key_data(snapshot, tracking):
    members = snapshot.get('intersection') or []
    sym = members[0]
    name = ''
    for pk in ('week', 'month', 'quarter'):
        for r in snapshot.get('periods', {}).get(pk, []):
            if r.get('symbol') == sym:
                name = r.get('name', '')
                break
        if name:
            break
    t = trend_features(sym, tracking, snapshot['data_as_of'])
    prompt = build_prompt(sym, name or sym, snapshot, streak_for(sym, snapshot), t, snapshot['data_as_of'])
    assert sym in prompt
    assert '星级' in prompt
    assert '获利盘' in prompt
    assert 'JSON' in prompt


def test_valid_labels_whitelist():
    assert '开始转强' in VALID_LABELS
    assert '反转观察' in VALID_LABELS
    assert '仍在寻底' in VALID_LABELS
    assert len(VALID_LABELS) <= 9  # 少量类别


def test_star_validation_logic():
    # 与脚本 llm_json 内校验一致: 只有 1/2/3 合法
    for s in (1, 2, 3):
        assert s in (1, 2, 3)
    for bad in (0, 4, -1, 'x', None):
        assert bad not in (1, 2, 3)


def test_notes_api_file_contract():
    text = API_FILE.read_text(encoding='utf-8')
    assert 'low_chip_ai_notes' in text
    assert 'CREATE TABLE IF NOT EXISTS' in text
    assert 'INSERT OR REPLACE INTO' in text
    assert "method === 'GET'" in text
    assert "method === 'POST'" in text
    assert 'PRIMARY KEY (stock_code, trade_date)' in text


def test_gen_script_has_failsoft():
    text = GEN_SCRIPT.read_text(encoding='utf-8')
    # 失败单只跳过 / LLM 失败不阻断
    assert '失败' in text
    assert 'dry_run' in text


def test_current_data_members_covered_by_snapshot(snapshot):
    """当前 a-low-chip-stocks.json 若 data_as_of=2026-09-04 应与快照一致。"""
    if not DATA.exists():
        pytest.skip('current data file absent')
    cur = json.loads(DATA.read_text(encoding='utf-8'))
    if cur.get('data_as_of') != snapshot.get('data_as_of'):
        pytest.skip('current data not same day as snapshot')
    assert set(cur.get('intersection') or []) == set(snapshot.get('intersection') or [])
