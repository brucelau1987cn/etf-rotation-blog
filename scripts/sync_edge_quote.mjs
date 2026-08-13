#!/usr/bin/env node
/**
 * Copy edge-quote-api/src/index.js -> functions/api/public/v1/quote.js
 * Requires sibling checkout at ../edge-quote-api (or EDGE_QUOTE_API_ROOT).
 */
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const sourceRoot = resolve(process.env.EDGE_QUOTE_API_ROOT || join(root, '..', 'edge-quote-api'));
const source = join(sourceRoot, 'src', 'index.js');
const chipSource = join(sourceRoot, 'src', 'chip.js');
const baostockSource = join(sourceRoot, 'src', 'baostock.js');
const target = join(root, 'functions', 'api', 'public', 'v1', 'quote.js');
const chipRouteTarget = join(root, 'functions', 'api', 'public', 'v1', 'chip.js');
const chipHelperTarget = join(root, 'functions', 'api', 'public', 'v1', '_chip.js');
const baostockHelperTarget = join(root, 'functions', 'api', 'public', 'v1', '_baostock.js');

if (!existsSync(source) || !existsSync(chipSource) || !existsSync(baostockSource)) {
  console.error(`edge-quote-api source not found: ${source} / ${chipSource} / ${baostockSource}`);
  process.exit(1);
}

const text = readFileSync(source, 'utf8')
  .replaceAll("from './chip.js'", "from './_chip.js'")
  .replaceAll("from './baostock.js'", "from './_baostock.js'");
if (!text.includes('export async function onRequestGet') && !text.includes('export function onRequestGet')) {
  console.error('edge-quote-api/src/index.js must export onRequestGet for Pages Functions compatibility');
  process.exit(1);
}
if (!text.includes('export default')) {
  console.error('edge-quote-api/src/index.js must export default { fetch } for Workers compatibility');
  process.exit(1);
}

mkdirSync(dirname(target), { recursive: true });
mkdirSync(dirname(chipRouteTarget), { recursive: true });
writeFileSync(target, text);
writeFileSync(chipRouteTarget, "/** Thin Pages route: /api/public/v1/chip shares quote.js (path-dispatched chip handler). */\nexport { onRequestGet, parseSymbol } from './quote.js';\n");
copyFileSync(chipSource, chipHelperTarget);
copyFileSync(baostockSource, baostockHelperTarget);
console.log(`synced quote/chip handlers:\n  ${source}\n  -> ${target}\n  -> ${chipRouteTarget}\n  ${chipSource}\n  -> ${chipHelperTarget}\n  ${baostockSource}\n  -> ${baostockHelperTarget}`);
