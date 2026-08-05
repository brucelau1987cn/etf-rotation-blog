# Project maintenance notes (2026-08)

Operational notes for maintainers. Product surface lives in root `README.md`.

## Current product locks

1. **Rolling energy matrix columns** are chronological by `triggered_at` (shared buy/sell timeline). Later BUY sits after earlier SELL. Do not restore cycle-rank left-padding.
2. **Jin10 holdings daily** must use `attr_id` + `all=1`. Missing day ≠ zero; zero change **must** render.
3. **Low-chip** lives only at `/rolling/low-chip/` (stock screen). Index/ETF low-chip routes removed.
4. **Rolling insights** are full-market daily pages under `/rolling/insights/`; no single-stock insight pages.
5. **Futures subnav**: 期货罗盘 · 宏观数据 · 金银持仓.
6. **Rolling subnav order**: A股 → 期货 → 港股 → 美股.
7. Site product name is **ETF罗盘** (never 花园).

## Publisher hygiene

- Futures / paper / Pages release hard-fail on foreign dirty paths.
- Exempt only: `public/data/korea-tech-factor-shadow.json`, `public/data/us-selector-shadow.json`.
- Nightly: prepare freezes `base_commit`; any intermediate commit requires re-prepare before 22:30 publish.
- `npm run build` validates A-share + US batches together; a lagging A-share 22:00 shadow can block US close publish.

## After feature work

1. Update root `README.md` (quick links, pages, APIs, structure).
2. Update this file + `references/site-map.md` if routes/APIs change.
3. Page-only: `rm -rf dist && npx astro build && node scripts/inject_public_js_version.mjs dist`.
4. Deploy: `npx wrangler pages deploy dist --project-name etf-rotation-blog --commit-dirty=true`.
5. Probe custom domain with unique `?bust=` and versioned JS hash.
