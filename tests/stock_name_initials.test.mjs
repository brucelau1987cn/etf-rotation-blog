import { strict as assert } from 'node:assert';
import { stockNameInitials } from '../src/lib/stockNameInitials.mjs';
import { readFileSync } from 'node:fs';

assert.equal(stockNameInitials('洋河股份'), 'yhgf');
assert.equal(stockNameInitials('顺丰控股'), 'sfkg');
assert.equal(stockNameInitials('三峡能源'), 'sxny');
assert.equal(stockNameInitials('ST中安'), 'stza');
assert.equal(stockNameInitials(''), '');

const pageSource = readFileSync(new URL('../src/pages/rolling/low-chip.astro', import.meta.url), 'utf8');
assert.match(pageSource, /stockInitialsByCode\[code\] \|\| ''/);
assert.doesNotMatch(pageSource, /nameInitials\(stockName \|\| code\)/);

console.log('stock-name initials tests passed');
