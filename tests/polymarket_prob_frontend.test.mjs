import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../public/js/jin10-calendar-app.js', import.meta.url), 'utf8',
);

test('loadPolymarket 在每个 pill 结算后都继续处理下一个', () => {
  // 早期实现 `if (!resp.ok) { ...; return; }` 会中断递归，同页剩余 pill 卡在「…」。
  const block = source.slice(
    source.indexOf('const loadPolymarket'),
    source.indexOf('// 只显示中国和美国的信息'),
  );
  assert.ok(block.length > 0, 'loadPolymarket block must exist');
  assert.ok(
    !/if \(!resp\.ok\)[^\n]*return;/.test(block),
    'a non-OK response must not early-return before the recursion continues',
  );
  assert.match(block, /loadPolymarket\(\);\s*\/\/ 继续下一项/, 'recursion must stay at the end');
});

test('没有活跃市场时隐藏 pill，不展示定格的 100%/0% 概率', () => {
  const block = source.slice(
    source.indexOf('const loadPolymarket'),
    source.indexOf('// 只显示中国和美国的信息'),
  );
  assert.match(block, /el\.remove\(\)/, 'expired or failed pill should be removed, not shown as N/A');
});
