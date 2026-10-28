"""风险预审硬超时必须走软跳过分支，不能穿透整条 16:00 发布链路。

背景：run_risk_staging_gate 用 subprocess.run(..., timeout=360) 调
attach_low_chip_risks.py。2026-10-19 腾讯 K 线被 WAF 拦截、BaoStock 挂死，
153 只股票全走 stock-api CLI 兜底导致超时，抛出的 subprocess.TimeoutExpired
属于 SubprocessError 而非 RuntimeError，原 `except RuntimeError` 没接住，
整条低筹码发布链崩溃（连续第 4 次失败）。
"""
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_test_support import require_local_script

SCRIPTS = Path.home() / '.hermes/scripts'
require_local_script(SCRIPTS / 'update_low_chip_and_release.py')


@pytest.fixture(scope='module')
def release_module():
    sys.path.insert(0, str(SCRIPTS))
    import update_low_chip_and_release as mod
    return mod


def test_risk_gate_timeout_is_treated_as_skippable(release_module):
    """TimeoutExpired 必须能被调用点捕获为「跳过」而非致命错误。"""

    def fake_runner(args, timeout=900, env=None):
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)

    with pytest.raises(subprocess.SubprocessError):
        release_module.run_risk_staging_gate(runner=fake_runner, attempts=1)


def test_caller_catches_subprocess_error():
    """检查真实调用点同时捕获 RuntimeError 与 SubprocessError。"""
    source = (SCRIPTS / 'update_low_chip_and_release.py').read_text(encoding='utf-8')
    marker = 'risk_staging = run_risk_staging_gate()'
    idx = source.index(marker)
    # except 子句在调用点之后（try 块之后），取其后 300 字符
    window = source[idx: idx + 300]
    assert 'except (RuntimeError, subprocess.SubprocessError)' in window, (
        'risk staging gate 的 except 必须同时覆盖 subprocess.SubprocessError，'
        '否则 360s 硬超时会让整条发布链崩溃'
    )
