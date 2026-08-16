import test from 'node:test';
import assert from 'node:assert/strict';

import { publicHomeCopy, isTakeProfitSide } from '../src/lib/homeCopy.mjs';

test('publicHomeCopy removes internal model terminology', () => {
  const raw = '2026-08-14夜间最终；今日按计划执行；允许正常伏击与持仓；旧plant 0触发；mid_macro中性；market_regime偏强；canonical position；伏击位2.7；防守线2.5；伏击/兑现';
  const text = publicHomeCopy(raw);
  for (const banned of ['plant', 'mid_macro', 'market_regime', 'canonical', '伏击位', '防守线', '伏击/兑现', '夜间最终', '今日']) {
    assert.equal(text.includes(banned), false, banned);
  }
  assert.match(text, /计划关注价/);
  assert.match(text, /风险退出价/);
  assert.match(text, /关注\/止盈/);
});

test('isTakeProfitSide recognizes both take-profit labels', () => {
  assert.equal(isTakeProfitSide('兑现'), true);
  assert.equal(isTakeProfitSide('止盈观察'), true);
  assert.equal(isTakeProfitSide('等待价格确认'), false);
});
