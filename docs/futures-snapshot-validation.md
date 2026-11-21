# Futures snapshot validation

`npm run build` runs `validate_futures_compass.py --watchlist config/futures-watchlist.json`.
The versioned configuration supplies the independent complete-coverage denominator.
Coherent deletions from snapshot items, count, expected_count and ranking fail validation.
All existing freshness and item-quality gates remain active.

The initial export was read from the D1-backed public endpoint
`https://etf.peekabo.cc/api/public/v1/futures-watchlist` and compared field-for-field
against the installed `data/local/futures/watchlist.json` (11 enabled instruments).
Only the endpoint's sort_order was projected away to match managed normalization.
The export contains public metadata only.

The futures publisher invokes the validator in default managed mode with
`--export-watchlist config/futures-watchlist.json`. The validator resolves managed
configuration once, validates the snapshot against those exact rows, then writes
those same rows atomically. Configuration changes and snapshot changes share the
publisher's PUBLISH_FILES scope, clean-input check, change detection, commit and
rollback. Additions and disabled instruments therefore propagate on the next
successful owning publication. The existing managed lookup policy remains fresh
cache, D1 endpoint, stale cache, bootstrap defaults; its availability semantics
remain unchanged.

Module import performs no network requests or cache writes. Python WATCHLIST has
no external direct consumers; fetch_realtime, fetch_daily_bars,
fetch_warehouse_receipts and run_iwencai_review resolve configuration at runtime.
The browser's separate WATCHLIST continues to use its public endpoint.

Reproduce the deterministic build gate:

```sh
python3 scripts/validate_futures_compass.py --watchlist config/futures-watchlist.json
```
