"""腾讯 fqkline 双 host 兜底的行为回归。

背景（2026-10-21）：`web.ifzq.gtimg.cn` 会间歇性返 HTTP 501（腾讯云 WAF），
同一时刻 `ifzq.gtimg.cn` 正常。四个脚本各自硬编码了 `web.` 单 host，于是
一次 WAF 就把「A股14:30 阶段中观因子」「低筹码追踪 K 线」「bar cache」
「回测价格」同时打成空数据/异常，而 headwind_level 会静默从 3 级降到 1 级。

契约：单 host 被 WAF 拒绝时必须自动切到另一个 host，而不是抛错或返回空。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body

    def close(self):
        return None


def _payload(symbol: str, dates: list[str]) -> bytes:
    rows = [[d, "10.0", "10.5", "10.6", "9.9", "1000"] for d in dates]
    return json.dumps({"code": 0, "msg": "", "data": {symbol: {"qfqday": rows}}}).encode()


def test_mid_macro_falls_back_to_second_host(monkeypatch):
    mod = _load("generate_a_share_mid_macro", "scripts/generate_a_share_mid_macro.py")
    seen: list[str] = []

    def fake_fetch_json(url, timeout=20):
        seen.append(url)
        if "web.ifzq.gtimg.cn" in url:
            raise urllib.error.HTTPError(url, 501, "Not Implemented", None, None)
        return json.loads(_payload("sh511260", ["2026-10-20", "2026-10-21"]))

    monkeypatch.setattr(mod, "fetch_json", fake_fetch_json)
    bars = mod.fetch_tencent_bars("sh511260", 60)

    assert bars and bars[-1]["date"] == "2026-10-21"
    # 首选 host 是可用那个，不该先去撞 501
    assert "web.ifzq.gtimg.cn" not in seen[0]
    assert all("ifzq.gtimg.cn" in url for url in seen)


def test_mid_macro_raises_only_when_both_hosts_fail(monkeypatch):
    """双腾讯 host 都失败且同花顺兜底也不可用时必须抛错，不得返回空 K 线。

    2026-10-23：df8b83a 给 `fetch_tencent_bars` 加了同花顺收盘兜底
    （两个腾讯 host 都 501 时启用），这个旧断言没有 mock 兜底，
    于是真去请求同花顺并拿到 60 根 K 线，断言失效并在 22:30
    夜间发布的候选全量 pytest 中失败，阻塞了整条发布链。
    兜底本身在 tests/test_a_share_mid_macro.py 已有覆盖。
    """
    mod = _load("generate_a_share_mid_macro_2", "scripts/generate_a_share_mid_macro.py")

    def boom(url, timeout=20):
        raise urllib.error.HTTPError(url, 501, "Not Implemented", None, None)

    def boom_ths(symbol, count=60):
        raise OSError("ths unavailable")

    monkeypatch.setattr(mod, "fetch_json", boom)
    monkeypatch.setattr(mod, "fetch_ths_bars", boom_ths)
    try:
        mod.fetch_tencent_bars("sh511260", 60)
    except RuntimeError as exc:
        assert "unavailable" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("both hosts failing must raise, not return empty bars")


def test_tracking_tencent_daily_falls_back_to_second_host(monkeypatch):
    mod = _load("update_low_chip_tracking_hosts", "scripts/update_low_chip_tracking.py")
    seen: list[str] = []

    def fake_urlopen(req, timeout=20):
        url = req.full_url
        seen.append(url)
        if "web.ifzq.gtimg.cn" in url:
            raise urllib.error.HTTPError(url, 501, "Not Implemented", None, None)
        return _Resp(_payload("sh600363", ["2026-10-20", "2026-10-21"]))

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    bars = mod.tencent_daily("600363.SH", "2026-10-20", "2026-10-21")

    assert [b["date"] for b in bars] == ["2026-10-20", "2026-10-21"]
    assert "web.ifzq.gtimg.cn" not in seen[0]
    # 501 是硬拒绝：每个 host 只撞一次，不做 1/2/5s 空转重试
    assert len(seen) == 1


def test_tracking_tencent_daily_raises_when_both_hosts_reject(monkeypatch):
    mod = _load("update_low_chip_tracking_hosts2", "scripts/update_low_chip_tracking.py")

    def fake_urlopen(req, timeout=20):
        raise urllib.error.HTTPError(req.full_url, 501, "Not Implemented", None, None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    try:
        mod.tencent_daily("600363.SH", "2026-10-20", "2026-10-21")
    except RuntimeError as exc:
        assert "unavailable" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("both hosts rejected must raise RuntimeError")


def test_bar_cache_falls_back_to_second_host(monkeypatch):
    mod = _load("update_a_share_bar_cache_hosts", "scripts/update_a_share_bar_cache.py")
    seen: list[str] = []

    def fake_urlopen(req, timeout=30):
        url = req.full_url
        seen.append(url)
        if "web.ifzq.gtimg.cn" in url:
            raise urllib.error.HTTPError(url, 501, "Not Implemented", None, None)
        return _Resp(_payload("sz159992", ["2026-10-20", "2026-10-21"]))

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    bars = mod.fetch_tencent_history({"code": "159992", "market": "XSHE"}, 60)

    assert bars and bars[-1]["trade_date"] == "2026-10-21"
    assert "web.ifzq.gtimg.cn" not in seen[0]


def test_no_script_hardcodes_only_the_waf_host():
    """源码级门禁：四个脚本都必须出现无 `web.` 前缀的可用 host。"""
    for rel in (
        "scripts/generate_a_share_mid_macro.py",
        "scripts/update_low_chip_tracking.py",
        "scripts/update_a_share_bar_cache.py",
        "scripts/backtest_garden_predictions.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert '"ifzq.gtimg.cn"' in text or "('ifzq.gtimg.cn'" in text or "ifzq.gtimg.cn\"" in text, rel
