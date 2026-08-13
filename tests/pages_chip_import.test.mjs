import assert from 'node:assert/strict';
import test from 'node:test';
import { parseSymbol as quoteParse } from '../functions/api/public/v1/quote.js';
import { parseSymbol as chipParse } from '../functions/api/public/v1/chip.js';
import { parseSymbol as klineParse } from '../functions/api/public/v1/kline.js';

test('generated Pages quote and chip routes import without cycles', () => {
  assert.equal(typeof quoteParse, 'function');
  assert.equal(typeof chipParse, 'function');
});

test('chip route is a thin re-export of quote, not a duplicated handler', async () => {
  const { readFileSync } = await import('node:fs');
  const chip = readFileSync(new URL('../functions/api/public/v1/chip.js', import.meta.url), 'utf8');
  assert.match(chip, /export \{[\s\S]*onRequestGet[\s\S]*\} from ['\"]\.\/quote\.js['\"]/);
  assert.ok(!chip.includes('export function parseSymbol'), 'chip.js must not duplicate quote.js');
});

test('Yahoo continuous aliases stay synchronized across generated routes', () => {
  for (const [input, expected] of [['SI=F', 'hf_XAG'], ['GC=F', 'hf_XAU'], ['CL=F', 'hf_CL']]) {
    assert.equal(quoteParse(input).tencent, expected);
    assert.equal(chipParse(input).tencent, expected);
    assert.equal(klineParse(input).tencent, expected);
  }
});