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
const target = join(root, 'functions', 'api', 'public', 'v1', 'quote.js');

if (!existsSync(source)) {
  console.error(`edge-quote-api source not found: ${source}`);
  process.exit(1);
}

const text = readFileSync(source, 'utf8');
if (!text.includes('export async function onRequestGet') && !text.includes('export function onRequestGet')) {
  console.error('edge-quote-api/src/index.js must export onRequestGet for Pages Functions compatibility');
  process.exit(1);
}
if (!text.includes('export default')) {
  console.error('edge-quote-api/src/index.js must export default { fetch } for Workers compatibility');
  process.exit(1);
}

mkdirSync(dirname(target), { recursive: true });
writeFileSync(target, text);
console.log(`synced quote handler:\n  ${source}\n  -> ${target}`);
