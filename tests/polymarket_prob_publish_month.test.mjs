import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../public/js/jin10-calendar-app.js', import.meta.url), 'utf8',
);

// 从源码里抽出 nextMonth 实现，独立验证月份推进语义（含跨年）。
function loadNextMonth() {
  const start = source.indexOf('const nextMonth = (ym) => {');
  assert.ok(start > 0, 'nextMonth helper must exist');
  const end = source.indexOf('};', start) + 2;
  const body = source.slice(start, end);
  return new Function(`${body}; return nextMonth;`)();
}

test('nextMonth 把数据月推进到发布月', () => {
  const nextMonth = loadNextMonth();
  assert.equal(nextMonth('2026-10'), '2026-11'); // 10 月 CPI 在 11 月发布
  assert.equal(nextMonth('2026-01'), '2026-02');
  assert.equal(nextMonth('2026-11'), '2026-12');
});

test('nextMonth 跨年正确进位', () => {
  const nextMonth = loadNextMonth();
  assert.equal(nextMonth('2026-12'), '2027-01'); // 12 月数据在次年 1 月发布
});

test('nextMonth 非法输入返回 null，不误判为匹配', () => {
  const nextMonth = loadNextMonth();
  assert.equal(nextMonth(''), null);
  assert.equal(nextMonth(null), null);
  assert.equal(nextMonth('2026-1'), null);
});

test('月份校验比较的是「发布月」而非「数据月」', () => {
  // 关键回归：早期实现直接比较 covered_month === eventMonth，
  // 导致 11-10（发布 10 月数据）的事件被全部误删。
  assert.match(
    source,
    /nextMonth\(d\.covered_month\) !== eventMonth/,
    'must compare next month of covered_month against the event month',
  );
  assert.ok(
    !/d\.covered_month !== eventMonth/.test(source),
    'must not compare covered_month directly against the event month',
  );
});
