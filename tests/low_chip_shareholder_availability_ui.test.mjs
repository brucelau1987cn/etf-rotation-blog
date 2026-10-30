import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/pages/rolling/low-chip.astro', import.meta.url), 'utf8');
const currentMapper = source.slice(source.indexOf('const rows = intersectionRows.map('), source.indexOf('const initialClientData'));
const historyObject = source.slice(source.indexOf('data.rows.push({') + 'data.rows.push('.length, source.indexOf('\n                });', source.indexOf('data.rows.push({')));
const renderer = source.slice(source.indexOf('      function rowHtml('), source.indexOf('      function renderIndustryEtfSection('));

function current(enrichment) {
  return vm.runInNewContext(currentMapper + '\nrows[0]', {
    intersectionRows: [{ symbol: '600269', name: '测试', price: 10, change_percent: 0 }],
    lowChipData: { data_as_of: '2026-10-30', enrichments: { '600269': enrichment } },
    occurrenceLookup: {}, profitBySymbol: {}, yearProfitBySymbol: {},
    nameInitials: () => '', compositeScore: () => 50, recoLabel: () => '低',
  });
}
function history(nature, legacy = {}) {
  return vm.runInNewContext('(' + historyObject + '})', {
    row: { ...legacy, shareholder_nature: nature }, code: '600269', stockName: '测试',
    stockInitialsByCode: {}, themes: [], occurrenceLookup: {}, day: '2026-10-29', hasPeriods: false,
  });
}
function html(row) {
  return vm.runInNewContext(renderer + '\nrowHtml(row, 0, "2026-10-30")', {
    row, PAGE_SIZE: 6, esc: (v) => String(v ?? ''), fmt: (v) => String(v),
    fmtInt: (v) => String(v ?? '—'), fmtMetric: (v) => String(v ?? '—'),
    chipFocusClass: () => '', compositeOf: () => 50, renderIndustryEtfSection: () => '',
  });
}

test('current and historical unknown shareholder evidence stays null and renders pending', () => {
  const availability = { top10_shareholders: 'UNAVAILABLE', industry: 'AVAILABLE' };
  const nature = { quality_shareholder: null, institutional_shareholder: null, availability, status: 'degraded' };
  const rows = [current({ ...nature, enrichment_availability: availability, enrichment_status: 'degraded' }), history(nature)];
  for (const row of rows) {
    assert.equal(row.qualityShareholder, null);
    assert.equal(row.institutionalShareholder, null);
    assert.equal(row.enrichmentAvailability.top10_shareholders, 'UNAVAILABLE');
    assert.equal(row.enrichmentStatus, 'degraded');
    assert.match(html(row), /股东数据待补/);
  }
});

test('known false stays false and existing positive shareholder badges are preserved', () => {
  for (const flags of [[false, false], [true, false], [false, true]]) {
    const nature = { quality_shareholder: flags[0], institutional_shareholder: flags[1], availability: { top10_shareholders: 'AVAILABLE' }, status: 'ok' };
    for (const row of [current({ ...nature, enrichment_availability: nature.availability, enrichment_status: nature.status }), history(nature)]) {
      assert.equal(row.qualityShareholder, flags[0]);
      assert.equal(row.institutionalShareholder, flags[1]);
      assert.doesNotMatch(html(row), /股东数据待补/);
      if (flags[0]) assert.match(html(row), /优质股东 ✓/);
      if (flags[1]) assert.match(html(row), /机构股东 ●/);
    }
  }
});

test('legacy rows lacking top10 evidence render pending in both paths', () => {
  for (const row of [current({}), history(null)]) assert.match(html(row), /股东数据待补/);
});

test('top10 availability controls pending independently of industry degradation', () => {
  for (const top10 of ['UNAVAILABLE', 'AVAILABLE']) {
    const availability = { top10_shareholders: top10, industry: 'UNAVAILABLE' };
    const nature = { quality_shareholder: false, institutional_shareholder: false, availability, status: 'degraded' };
    for (const row of [current({ ...nature, enrichment_availability: availability, enrichment_status: 'degraded' }), history(nature)]) {
      assert.equal(html(row).includes('股东数据待补'), top10 === 'UNAVAILABLE');
      assert.equal(row.enrichmentStatus, 'degraded');
    }
  }
});

test('D1 explicit nested unknown takes precedence over legacy quality values', () => {
  for (const legacy of [false, true]) {
    const row = history({ quality_shareholder: null, institutional_shareholder: null }, { quality_shareholder: legacy });
    assert.equal(row.qualityShareholder, null);
    assert.match(html(row), /股东数据待补/);
    assert.doesNotMatch(html(row), /优质股东 ✓/);
  }
  for (const legacy of [false, true]) {
    assert.equal(history({}, { quality_shareholder: legacy }).qualityShareholder, legacy);
  }
});

test('either unknown flag renders pending in current and D1 paths after positive badges', () => {
  for (const flags of [[false, null], [null, false], [true, null], [null, true]]) {
    const nature = { quality_shareholder: flags[0], institutional_shareholder: flags[1] };
    for (const row of [current(nature), history(nature)]) {
      const rendered = html(row);
      assert.equal(rendered.includes('股东数据待补'), !flags.includes(true));
      if (flags[0] === true) assert.match(rendered, /优质股东 ✓/);
      if (flags[1] === true) assert.match(rendered, /机构股东 ●/);
    }
  }
});

test('SSR has the same explicit unavailable and unknown badge branch', () => {
  const ssr = source.slice(source.indexOf('{row.qualityShareholder ? ('), source.indexOf('<div class="chip-row-metrics"'));
  assert.match(ssr, /enrichmentAvailability\?\.top10_shareholders === 'UNAVAILABLE'/);
  assert.match(ssr, /row\.qualityShareholder == null \|\| row\.institutionalShareholder == null/);
  assert.ok(ssr.indexOf('row.institutionalShareholder ?') < ssr.indexOf("top10_shareholders === 'UNAVAILABLE'"));
  assert.match(ssr, /股东数据待补/);
});
