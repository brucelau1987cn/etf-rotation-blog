# CF BaoStock Python migration

## Audited consumers

- `scripts/check_a_share_cron_gate.py`: D1 exchange calendar → authenticated CF BaoStock → local BaoStock.
- `scripts/update_low_chip_tracking.py`: Tencent QFQ daily bars → authenticated CF BaoStock → local BaoStock; all-source failure preserves existing rows.
- `/root/.hermes/scripts/update_low_chip_and_release.py` invokes both repository scripts.
- Paused A-share cron jobs (`08:30`, `11:40`, `14:30`, `22:00`) use the shared calendar gate.

## Runtime configuration

```text
CF_BAOSTOCK_BASE_URL=https://etf.peekabo.cc
CF_BAOSTOCK_TOKEN=<bearer token>
```

Store credentials outside the repository. The client accepts only an HTTPS origin without URL credentials, query, or fragment.

## Gateway contract

### Calendar

```text
GET /api/internal/v1/baostock/calendar?start=YYYY-MM-DD&end=YYYY-MM-DD
Authorization: Bearer <token>
```

Response:

```json
{"ok":true,"source":"baostock","start":"YYYY-MM-DD","end":"YYYY-MM-DD","count":1,"records":[{"date":"YYYY-MM-DD","is_trading_day":true}]}
```

### QFQ daily bars

```text
GET /api/internal/v1/baostock/qfq?symbols=000858.SZ,sh.600000&start=YYYY-MM-DD&end=YYYY-MM-DD&fields=date,close,volume,amount,turn,tradestatus
Authorization: Bearer <token>
```

- 1–5 unique A-share symbols per request.
- Inclusive date span: at most 366 days.
- Adjustment is fixed to BaoStock `adjustflag=2` (QFQ).
- Fields are restricted by the server whitelist.

Response:

```json
{"ok":true,"source":"baostock","adjust":"qfq","start":"YYYY-MM-DD","end":"YYYY-MM-DD","fields":["date","close","volume","amount","turn","tradestatus"],"symbol_count":1,"count":1,"results":[{"symbol":"sz.000858","count":1,"records":[{"date":"YYYY-MM-DD","close":10.5,"volume":1000,"amount":10500,"turn":1.25,"tradestatus":"1"}]}]}
```

The Python client accepts suffix form (`000858.SZ`) and BaoStock form (`sz.000858`), then normalizes responses to requested keys. Missing, malformed, partial, or wrong-source responses fail closed.
