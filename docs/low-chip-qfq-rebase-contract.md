# Audited low-chip QFQ whole-window rebase

`main(qfq_rebase_contracts={symbol: contract}, target_day=..., ...)` is an explicit Python-only opt-in. The normal CLI retains exact shared-close matching and its existing provider request budgets. This candidate adds zero evidence retrieval requests and exposes no CLI migration switch. Contracts must be prepared and independently reviewed outside the scheduled updater; do bounded, batched corporate-action collection for the explicitly selected conflict set. Production migration, D1 sync, release and cron activation require separate authorization.

## Contract

See `tests/test_low_chip_tracking_qfq_rebase.py::inputs` for an executable example built from archived incident evidence. Each contract contains:

- `schema`: `low-chip-qfq-rebase-v1`; exact `symbol`, one `provider` (`Tencent`, `THS`, `BaoStock`), `adjustment`: `qfq`.
- `previous_basis_as_of`: last stored price date (a conservative audited lower bound for a subsequent action). Prior `price_basis.as_of` must be at/before this cutoff; its full provenance survives. `basis_as_of` equals the explicit target date. The reviewer must verify the source capture basis and ensure actions after the last stored date explain this replacement.
- `stored_sha256`, `prices_sha256`: SHA-256 of `json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()`. The former covers the exact stored daily rows including dated profit observations; the latter covers the exact parsed, single-provider response. Changes in the response or baseline require renewed review.
- `required_dates`: independently audited, complete chronological trading-window dates, including every historical stored date and all intervening required sessions. Source-response hashes bind the approved prices; this list is the reviewed session-coverage assertion. This implementation performs no exchange-calendar lookup. The target close is required until the 20-post-entry-bar window is complete.
- `corporate_actions`: `source=baostock_adjust_factor`, exact `symbol`, `start=previous_basis_as_of`, `end=basis_as_of`, `error_code='0'`, `complete=true`, and a nonempty `rows` list. Rows contain the provider's `code`, `dividOperateDate`, `foreAdjustFactor`, `backAdjustFactor`, `adjustFactor`. Every action falls strictly after the old basis date and at/before the new one; factors are finite and positive. Evidence acquisition must bind this envelope to the actual query and fully consumed successful response. The updater trusts this reviewed injection boundary; it performs structural, interval, symbol and hash validation.

The contract is an explicit audited replacement authorization, with corporate actions explaining the basis change. It does not infer a pricing adjustment formula across providers. THS and BaoStock have distinct QFQ conventions, so arbitrary tolerances and mixed-source repairs remain excluded.

## Write boundary and preserved evidence

The selected provider supplies one complete response. Coverage and contract completion are tracked independently: an earlier provider's target coverage keeps a later contract provider eligible, and an explicit contract also enters provider validation for an already complete window. Ordinary completed records retain their query skip. Tencent skips its old-basis precheck only for a symbol explicitly assigned to a Tencent contract; the downstream whole-window validation remains mandatory. Each approved series atomically replaces that symbol's in-memory price window, recalculates every `change_pct` on that basis, and retains every stored row's dated profit ratio and extra evidence. New ratios alone enter the existing enrichment/batch path. Existing `entry_features`, `entry_financials`, `entry_technical` and `entry_halt` values remain byte-equivalent as JSON values. Provider, basis date, full contract and previous-basis provenance are saved in `price_basis`.

Every supplied contract must succeed. Unknown symbols, stale inputs, missing action evidence, invalid rows, dropped old/intermediate/target dates, response drift and source mismatch leave the destination file unchanged. All-symbol coverage still gates the existing single final atomic JSON write. A successful isolated test does not authorize any production write.

## Offline verification

The fixture preserves the three archived incident samples (`000582.SZ`, `603383.SH`, `002007.SZ`), original THS histories and BaoStock factor/QFQ rows from `qfq-gap-audit-3dixea_6`. The Tencent transport test is a mock of those prices, and is labeled as such by its test construction. The other twelve recorded conflict symbols need individual evidence collection and complete-window approval.

```sh
/usr/bin/python3 -m pytest tests/test_low_chip_tracking_qfq_rebase.py tests/test_low_chip_tracking_retry.py tests/test_low_chip_tracking.py tests/test_low_chip_tracking_coverage_marker.py tests/test_low_chip_tracking_variant_snapshots.py tests/test_low_chip_tracking_shareholder_null.py -q
```

The new regression module blocks socket DNS/connections and subprocess launch. Orchestrator tests write only pytest scratch paths. The repository's production JSON, D1 and release tooling remain untouched.
