import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildLowChipOccurrenceLookup,
  lowChipOccurrenceLabel,
  lowChipOccurrenceTitle,
} from '../src/lib/lowChipOccurrences.mjs';

const lookup = buildLowChipOccurrenceLookup([
  { date: '2026-08-01', intersection: ['A', 'B'] },
  { date: '2026-08-04', intersection: ['A'] },
  { date: '2026-08-05', intersection: ['A', 'B'] },
  { date: '2026-08-06', intersection: ['B'] },
]);

test('identifies new, continuous, and re-entry occurrences by snapshot trading day', () => {
  assert.deepEqual(lookup['2026-08-01'].A, {
    appearances: 1, episodes: 1, streak: 1, isNew: true, isReentry: false,
  });
  assert.equal(lowChipOccurrenceLabel(lookup['2026-08-04'].A), '连续2日');
  assert.equal(lowChipOccurrenceLabel(lookup['2026-08-05'].B), '第2次入池');
  assert.equal(lowChipOccurrenceLabel(lookup['2026-08-06'].B), '第2轮 · 连续2日');
  assert.equal(lowChipOccurrenceTitle(lookup['2026-08-06'].B), '累计入池3日 · 共2轮 · 本轮连续2日');
});

test('weekends and holidays do not break continuity between adjacent snapshots', () => {
  assert.equal(lookup['2026-08-04'].A.streak, 2);
  assert.equal(lookup['2026-08-04'].A.episodes, 1);
});
