import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const page = await readFile(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
const header = await readFile(new URL('../src/components/Header.astro', import.meta.url), 'utf8');

test('homepage presents a stable product-value hero and combined market takeaways', () => {
  assert.match(page, /最新ETF行动清单/);
  assert.match(page, /最近交易日关注什么、等待什么、如何控制仓位/);
  assert.match(page, /最新重点/);
  assert.match(page, /查看最新行动清单/);
  assert.doesNotMatch(page, /双市场今日仪表盘/);
  assert.doesNotMatch(page, />今日行动</);
});

test('homepage uses the shared public-copy and take-profit contracts', () => {
  assert.match(page, /publicHomeCopy/);
  assert.match(page, /isTakeProfitSide/);
  assert.match(page, /计划关注价/);
  assert.match(page, /风险退出价/);
  assert.match(page, /距关注价/);
  assert.match(page, /等待价格确认/);
  assert.match(page, /趋势达标/);
  assert.doesNotMatch(page, />兑现侧</);
  assert.doesNotMatch(page, />伏击侧</);
  assert.doesNotMatch(page, />中观闸门</);
  assert.match(page, /当前没有触发交易信号/);
  assert.doesNotMatch(page, /\{aStage \|\|/);
});

test('homepage has accessible actions and market-specific freshness', () => {
  assert.match(page, /<main id="main-content" tabindex="-1">/);
  assert.match(page, /A股更新 \{aUpdatedShort\}/);
  assert.match(page, /美股更新 \{usUpdatedShort\}/);
  assert.match(page, /min-height:44px/);
  assert.match(header, /isHome \? '每日ETF机会 · 仓位 · 风控' : '伏击 · 兑现 · 复盘'/);
  assert.match(header, /body\.home-page[\s\S]*online-count[\s\S]*display:\s*none/);
});

test('homepage action count matches rendered cards', () => {
  assert.match(page, /优先关注/);
  assert.match(page, /aActions\.length/);
  assert.match(page, /usActions\.length/);
});
