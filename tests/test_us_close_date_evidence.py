"""A resumed close must earn its date evidence from the pinned candidate."""
from contextlib import nullcontext
import json

import pytest

from test_us_close_entry import DAY, OLD, close, data, git, pub


@pytest.mark.parametrize(('file', 'field', 'value', 'reason'), [
    pytest.param(None, None, None, 'pool model_date differs from target trade date', id='all-blobs-stale'),
    pytest.param('us-etf-pool.json', 'model_date', None, 'pool model_date is missing', id='pool-date-missing'),
    pytest.param('us-etf-pool.json', 'session_state', 'open', 'pool session_state must be closed', id='pool-open'),
    pytest.param('us-etf-garden.json', 'date', OLD, 'garden close identity differs from pool', id='garden-date'),
    pytest.param('us-etf-garden.json', 'stage', 'intraday', 'garden close identity differs from pool', id='garden-stage'),
    pytest.param('us-etf-garden.json', 'session_state', 'open', 'garden close identity differs from pool', id='garden-open'),
    pytest.param('us-compass-health.json', 'model_date', OLD, 'health model_date differs from pool', id='health-date'),
    pytest.param('us-macro-dashboard.json', 'market', {}, 'macro primary date is missing', id='macro-date-missing'),
    pytest.param('us-macro-dashboard.json', 'market', {'spy': {'date': OLD}}, 'macro primary date differs from pool', id='macro-date-stale'),
    pytest.param('us-etf-flower-history.json', 'records', [{'date': OLD}, {'date': DAY}], 'latest history date differs from target trade date', id='history-head-date'),
    pytest.param('us-etf-backtest.json', None, None, 'us-etf-backtest.json', id='owned-json-invalid'),
])
@pytest.mark.parametrize('phase', ['committed', 'error'])
def test_resume_rejects_bad_pinned_date_evidence_before_receipt_write(close, monkeypatch, phase, file, field, value, reason):
    root, bare, baseline, state, drafts, events, faults = close
    monkeypatch.setattr(pub, 'site_publish_lock', nullcontext)
    target_day = DAY if file else '2026-11-20'
    data(root, DAY)
    if not file:
        # Keep the blobs at 11-19 while receipt and title claim 11-20.
        # Whitespace makes the candidate distinct without changing its data.
        path = root / pub.US_OWNED_FILES[0]
        path.write_bytes(path.read_bytes() + b'\n')
    else:
        path = root / 'public/data' / file
        if field is None:
            path.write_text('{"value": NaN}', encoding='utf-8')
        else:
            payload = json.loads(path.read_bytes())
            payload[field] = value
            path.write_text(json.dumps(payload), encoding='utf-8')
    # Simulate a candidate already accepted by the remote, with a matching
    # subject and pending receipt. All three Git refs point to the same SHA.
    git(root, 'commit', '--only', '-qm', pub.OWNED_COMMIT_PREFIXES[0] + target_day, '--', *pub.US_OWNED_FILES)
    git(root, 'push', 'origin', 'main')
    git(root, 'fetch', 'origin', 'main')
    sha = git(root, 'rev-parse', 'HEAD')
    assert sha != baseline and git(root, 'rev-parse', 'origin/main') == git(bare, 'rev-parse', 'HEAD') == sha
    assert git(root, 'show', '-s', '--format=%s', 'HEAD') == pub.OWNED_COMMIT_PREFIXES[0] + target_day
    receipt = {'phase': phase, 'trade_date': target_day, 'commit': sha, 'pushed': False,
               'deployed': False, 'verified': False, 'deployment_url': None}
    if phase == 'error':
        receipt['error'] = 'legacy interrupted push'
    before = (json.dumps(receipt, indent=3) + '\n\n').encode()
    state.write_bytes(before)
    owned_before = {path: (root / path).read_bytes() for path in pub.US_OWNED_FILES}

    def unexpected(*args, **kwargs):
        pytest.fail('date blocker crossed into provider or build runtime')

    monkeypatch.setattr(pub, 'latest_spy_date', unexpected)
    monkeypatch.setattr(pub, 'prepare_build_runtime', unexpected)
    faults['collect'] = unexpected
    with pytest.raises(RuntimeError, match=reason):
        pub.cli()
    assert state.read_bytes() == before, 'invalid pinned date evidence advanced the push receipt'
    assert reason in json.loads(state.with_suffix('.error.json').read_bytes())['error']
    assert not events
    assert git(root, 'rev-parse', 'HEAD') == git(bare, 'rev-parse', 'HEAD') == sha
    assert git(root, 'diff', '--cached', '--name-only') == ''
    assert all((root / path).read_bytes() == raw for path, raw in owned_before.items())
    assert all((root / path).read_text() == text for path, text in drafts.items())
