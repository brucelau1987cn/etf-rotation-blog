# A-share nightly: GitHub knocks, CTYUN computes

## Goal

Keep heavy compute on this CTYUN host, while GitHub Actions only triggers on schedule.

```text
GitHub schedule
  -> HTTPS POST /trigger
  -> CTYUN localhost trigger service
  -> local nightly scripts
```

## Local service

- systemd: `a-share-nightly-trigger.service`
- listen: `127.0.0.1:18090`
- token: `/root/.hermes/secrets/a-share-nightly-trigger.token`
- runner: `/root/.hermes/scripts/run_a_share_nightly_stage.py`
- status: `/root/.hermes/state/a-share-nightly-trigger-status.json`

### Health

```bash
curl -sS http://127.0.0.1:18090/healthz
```

### Manual trigger

```bash
TOKEN=$(cat /root/.hermes/secrets/a-share-nightly-trigger.token)
curl -sS -X POST http://127.0.0.1:18090/trigger \
  -H "content-type: application/json" \
  -H "x-nightly-token: $TOKEN" \
  -d '{"stage":"precheck","sync":true}'
```

Stages:

- `precheck`
- `cache`
- `prepare`
- `content`
- `publish`
- `chain` (`precheck -> cache -> prepare`)

## GitHub Actions

Workflow: `.github/workflows/nightly-trigger.yml`

Repo secrets required:

1. `A_SHARE_NIGHTLY_TRIGGER_URL`
2. `A_SHARE_NIGHTLY_TRIGGER_TOKEN`

Schedule (UTC):

- `50 12 * * 1-5` → 20:50 CST precheck
- `0 13 * * 1-5` → 21:00 CST cache
- `50 13 * * 1-5` → 21:50 CST prepare

## Public endpoint (live)

- URL: `https://nightly-trigger.peekabo.cc/trigger`
- Health: `https://nightly-trigger.peekabo.cc/healthz`
- Tunnel: `etf-nightly-trigger` (`2fd083b4-9238-4473-9168-d9c2b16f7400`)
- systemd: `cloudflared-etf-nightly-trigger.service`
- Local service: `a-share-nightly-trigger.service` on `127.0.0.1:18090`

GitHub secrets:

- `A_SHARE_NIGHTLY_TRIGGER_URL=https://nightly-trigger.peekabo.cc/trigger`
- `A_SHARE_NIGHTLY_TRIGGER_TOKEN=<host token>`

Verified:

- public healthz ok
- public token auth ok / bad token 401
- workflow_dispatch precheck success (run `30276063493`)
- local status file updated to precheck ok

## Public exposure options

Service binds localhost only. External path is Cloudflare Tunnel:

1. Existing reverse proxy / FRP route to `127.0.0.1:18090/trigger`
2. Cloudflare Tunnel / Zero Trust protected hostname
3. Keep Hermes cron primary; use GitHub as secondary knocker after exposure is ready

Do **not** expose this port raw to the public internet without HTTPS + token.

## Fallback

Hermes local crons remain primary for now:

- 20:50 precheck
- 21:00 cache
- 21:50 prepare
- 22:00 content
- 22:30 publish

GitHub knocker is additive, not a hard cutover.
