# Dragon signal calibration specification

Scope: 金龙、银龙、结构底背离 only. Baseline 4836c480. Backup `/root/backups/dragon-signals/20261115-115315`.

## Compatibility and production fixes

- Preserve numeric defaults: 金龙 MA40 ratio 0.74, amplitude 5%, RSI7/20, MACD threshold -1.5; 银龙 RSI9/20 plus negative MACD histogram; structure MACD12/26/9. CROSS equality and vendor LOG semantics remain pending terminal-reference calibration; preserve existing behavior and document the uncertainty.
- Resolve `as_of` before warmup validation. Use bars through the exact target date; history_bars reflects that prefix. Historical signal search starts at the applicable MIN_BARS boundary.
- Preserve window membership as `bars_since_hit <= 20`, explicitly label “距今至多20个交易日 / 0–20 inclusive”. Add <=5 and <=10 research flags; production filter remains the <=20 background filter for 金龙/银龙.
- For bottom structure: preserve raw source event calculations, combine source disappearance branches with OR. Freeze the latest sufficiently warmed signal's branch-specific DIF comparison thresholds. Invalidate if any subsequent DIF crosses below/equal to a frozen active threshold or the source disappearance event fires. Invalidation remains sticky until a new raw signal; a new signal whose segment trough already violates its comparison threshold is invalid immediately. Preserve legacy_voided and raw today independently. Return model version, anchor and invalidation date/reason.
- SSR and fetched-history labels show voided first; a raw today with invalid structure is labelled 失效. Tooltip distinguishes background signal and active structure. Preserve existing compact layout, button order and other filters.

## Research only

Offline parameter CLI uses read-only SQLite and exact date snapshots, fixed chronological train/validation split and nonoverlapping stock cohorts. Ratio .74/.80/.85, silver 9/20,9/25,14/20 and <=5/10/20 observation windows are diagnostics. No automatic parameter selection or production promotion. Record sample limitations and forward coverage.

## Backup and recovery

Backup includes complete git bundle; code/tests/UI/current/historical snapshot tar; consistent bars SQLite copy; SHA256 manifest. Save exact D1 GET rows for all 67 formal dates before writes, with checksums. Verify hashes, bundle and SQLite integrity. A recovery run restores only approved paths/columns, checks current values before overwriting, and verifies exact readback. Do not reset unrelated changes or rewrite whole D1 rows.

## Acceptance

- Focused RED/GREEN tests and all repository tests/build pass.
- Independent specification and quality reviewers pass after any fixes.
- Recompute only three overlays from cached bars across current and all formal snapshots; retain all unrelated fields and membership; exact requested=ok+unavailable coverage.
- No real network in tests. Real D1 write is a separate authorized release step, single-column updates only with exact post-write readback.
- Commit/push before Pages publication. Verify deployment commit/status and D1 field equality on every date. Browser validation covers local built page; authenticated production browser validation requires a saved authorized login.
