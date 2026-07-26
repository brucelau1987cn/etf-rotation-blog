#!/usr/bin/env node
/**
 * Inject a stable build-time query version onto public JS script tags in dist/.
 * Usage: node scripts/inject_public_js_version.mjs [distDir]
 */
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, statSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const distDir = process.argv[2] || 'dist';
const jsDir = join(distDir, 'js');
if (!existsSync(jsDir)) {
  console.log(`[inject_public_js_version] skip: ${jsDir} missing`);
  process.exit(0);
}

const files = readdirSync(jsDir).filter((name) => name.endsWith('.js')).sort();
const hash = createHash('sha1');
for (const name of files) {
  const full = join(jsDir, name);
  hash.update(name);
  hash.update(readFileSync(full));
}
const version = hash.digest('hex').slice(0, 10);
const q = `?v=${version}`;

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...walk(full));
    else if (name.endsWith('.html')) out.push(full);
  }
  return out;
}

const htmlFiles = walk(distDir);
let changed = 0;
const re = /src="(\/js\/[A-Za-z0-9_.-]+\.js)(?:\?[^"]*)?"/g;

for (const file of htmlFiles) {
  const before = readFileSync(file, 'utf8');
  const after = before.replace(re, (_m, path) => `src="${path}${q}"`);
  if (after !== before) {
    writeFileSync(file, after);
    changed += 1;
  }
}

console.log(`[inject_public_js_version] v=${version} html_updated=${changed} js_files=${files.length}`);
