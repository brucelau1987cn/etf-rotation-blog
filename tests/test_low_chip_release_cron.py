import importlib.util
import json
import subprocess
from io import BytesIO
from pathlib import Path


SCRIPT = Path('/root/.hermes/scripts/update_low_chip_and_release.py')


def load_module():
    spec = importlib.util.spec_from_file_location('low_chip_release_cron', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_d1_metrics_requires_complete_insert_count(tmp_path):
    module = load_module()
    env_file = tmp_path / 'sync.env'
    env_file.write_text('LOW_CHIP_SYNC_TOKEN=test-token\n', encoding='utf-8')
    calls = []

    def runner(args, timeout=900, env=None):
        calls.append((args, env))
        return subprocess.CompletedProcess(args, 0, 'D1 sync: 10/10 inserted (2026-08-14)\n', '')

    token = module.sync_d1_metrics('2026-08-14', 10, runner=runner, env_file=env_file)
    assert token == 'test-token'
    assert calls[0][0][-2:] == ['--day', '2026-08-14']
    assert calls[0][1]['LOW_CHIP_SYNC_TOKEN'] == 'test-token'


def test_verify_d1_api_uses_bearer_and_requires_expected_rows():
    module = load_module()
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                'ok': True,
                'trade_date': '20260814',
                'count': 10,
                'results': [{'stock_code': f'{i:06d}'} for i in range(10)],
            }).encode()

    def opener(request, timeout=30):
        captured['authorization'] = request.get_header('Authorization')
        captured['url'] = request.full_url
        return Response()

    result = module.verify_d1_api('2026-08-14', 10, 'test-token', opener=opener)
    assert result['count'] == 10
    assert captured['authorization'] == 'Bearer test-token'
    assert 'date=2026-08-14' in captured['url']


def test_verify_history_index_checks_every_published_date(tmp_path):
    module = load_module()
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({'items': [
        {'date': '2026-08-14', 'intersection_count': 10},
        {'date': '2026-08-13', 'intersection_count': 4},
        {'date': '2026-08-07', 'intersection_count': 0},
    ]}), encoding='utf-8')
    calls = []

    def verifier(day, count, token):
        calls.append((day, count, token))
        return {'ok': True, 'trade_date': day.replace('-', ''), 'count': count, 'results': [{}] * count}

    result = module.verify_history_index('test-token', index_file=index, verifier=verifier)
    assert result == {'dates': 3, 'rows': 14}
    assert calls == [
        ('2026-08-14', 10, 'test-token'),
        ('2026-08-13', 4, 'test-token'),
        ('2026-08-07', 0, 'test-token'),
    ]


def test_d1_history_gate_runs_before_build_and_release():
    source = SCRIPT.read_text(encoding='utf-8')
    gate = source.index("token = sync_d1_metrics(summary['trade_date'], summary['final'])")
    build = source.index("run(['npm', 'run', 'build']")
    release = source.index('release = run([sys.executable, \'-c\', release_code]')
    assert gate < build < release


def test_fuyao_shadow_audit_runs_after_enrichment_without_changing_formal_gate():
    source = SCRIPT.read_text(encoding='utf-8')
    financials = source.index("run([sys.executable, 'scripts/attach_low_chip_financials.py']")
    shadow = source.index("run([sys.executable, 'scripts/audit_low_chip_fuyao.py', '--soft-fail']")
    archive = source.index("run([sys.executable, 'scripts/archive_low_chip_snapshot.py']")
    assert financials < shadow < archive
    assert "public/data/model-lab/low-chip-fuyao-shadow.json" in source
    assert "fuyao_shadow = json.loads(FUYAO_SHADOW.read_text(encoding='utf-8'))" in source
    assert "'fuyao_shadow': fuyao_shadow.get('status')" in source
    assert "(ROOT / 'dist/data/model-lab/low-chip-fuyao-shadow.json').write_bytes(FUYAO_SHADOW.read_bytes())" in source
