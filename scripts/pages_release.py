#!/usr/bin/env python3
"""Direct Cloudflare Pages deployment, cache purge, and production probes."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
PAGES_ENV = Path("/root/.hermes/credentials/cloudflare-pages.env")
GLOBAL_ENV = Path("/root/.hermes/credentials/cloudflare-global.env")
PROJECT = "etf-rotation-blog"
ZONE_NAME = "peekabo.cc"
EXTERNAL_DIRTY = {
    "public/data/korea-tech-factor-shadow.json",
    "public/data/us-selector-shadow.json",
    "public/data/a-compass-dashboard.json",
    "public/data/a-share-mid-macro.json",
    "public/data/etf-garden-pool.json",
    "public/data/garden-recommendations.json",
    "public/data/model-lab/a-share-research-audit.json",
    "public/data/us-compass-learning.json",
    "public/data/us-compass-health.json",
    "public/data/us-compass-rotation-map.json",
    "public/data/us-compass-risk.json",
    "public/data/us-compass-shadow.json",
    "public/data/us-compass-research.json",
    "public/data/us-etf-backtest.json",
    "public/data/us-etf-flower-history.json",
    "public/data/us-etf-garden.json",
    "public/data/us-etf-pool.json",
    "public/data/us-macro-dashboard.json",
    "src/content/blog/2026-07-29.md",
}
JSON_PROBE_ATTEMPTS = 65
JSON_PROBE_DELAY_SECONDS = 5
URL_PROBE_ATTEMPTS = 5
URL_PROBE_DELAY_SECONDS = 2
# `npx wrangler` resolves the newest published version, and the configured npm
# mirror (registry.npmmirror.com) lags the upstream registry by a patch. On
# 2026-10-28 the resolved wrangler@4.154.0 required miniflare@5.20261023.1-beta,
# which the mirror had not synced yet, so every publisher died at the deploy step
# with `npm error notarget No matching version found for miniflare@...`.
# Pin a known-good spec (4.153.0 -> miniflare@5.20261023.0-beta, present on the
# mirror). `WRANGLER_SPEC` may override the pin for a future upgrade; the value is
# validated so a bare `wrangler` (which would re-resolve "latest") cannot creep back.
WRANGLER_SPEC_DEFAULT = "wrangler@4.153.0"
WRANGLER_SPEC_PATTERN = re.compile(r"^wrangler@\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")

# This host reaches Cloudflare's API and the Pages upload endpoint over a
# flaky public path. Measured on 2026-10-28: a direct api.cloudflare.com
# request took 7.0s and the 17:39 precious-inventory publish died with
# `TypeError: fetch failed`, while the same request through the local
# HTTP-CONNECT bridge (which tunnels through the 206 jump host's SOCKS5)
# took 0.9s and succeeded.
#
# Wrangler honours HTTP_PROXY/HTTPS_PROXY (and needs NODE_USE_ENV_PROXY=1 on
# Node 22+ so the built-in fetch picks them up), so pointing it at the local
# bridge removes the flakiness without touching a single caller.
PROXY_BRIDGE = "http://127.0.0.1:8899"
DEPLOY_ATTEMPTS = 3
DEPLOY_RETRY_DELAY_SECONDS = 20
CLOUDFLARE_REQUEST_ATTEMPTS = 3
CLOUDFLARE_REQUEST_DELAY_SECONDS = 5


def proxy_bridge_available(bridge: str | None = None) -> bool:
    """True when the local HTTP-CONNECT bridge is listening."""
    target = bridge or os.environ.get("HERMES_PROXY_BRIDGE", PROXY_BRIDGE)
    if not target:
        return False
    try:
        host, _, port = target.rpartition(":")
        with socket.create_connection((host or "127.0.0.1", int(port or 80)), timeout=3):
            return True
    except (OSError, ValueError):
        return False


def proxy_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for wrangler: route Cloudflare traffic through the bridge.

    The bridge is optional — when it is not listening, fall back to the direct
    path rather than failing the deploy outright.
    """
    env = dict(base or os.environ)
    bridge = env.get("HERMES_PROXY_BRIDGE", PROXY_BRIDGE)
    if not bridge or not proxy_bridge_available(bridge):
        return env
    env["HTTP_PROXY"] = bridge
    env["HTTPS_PROXY"] = bridge
    env["NODE_USE_ENV_PROXY"] = "1"
    return env


def wrangler_spec() -> str:
    spec = (os.environ.get("WRANGLER_SPEC") or "").strip() or WRANGLER_SPEC_DEFAULT
    if not WRANGLER_SPEC_PATTERN.match(spec):
        raise RuntimeError(
            f"WRANGLER_SPEC must pin an explicit version like wrangler@4.153.0 (got {spec!r})"
        )
    return spec


def load_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def foreign_dirty_paths(lines: list[str]) -> list[str]:
    paths: list[str] = []
    for line in lines:
        path = line[3:].strip() if len(line) >= 4 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in EXTERNAL_DIRTY and not path.startswith("public/data/") and not path.startswith("src/content/blog/"):
            paths.append(path)
    return paths


def ensure_release_scope() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    foreign = foreign_dirty_paths(result.stdout.splitlines())
    if foreign:
        raise RuntimeError(f"Pages release requires a clean worktree; foreign dirty paths: {foreign}")


def restore_tracked_public_files() -> None:
    for path in EXTERNAL_DIRTY:
        if not path.startswith("public/"):
            continue
        lookup = subprocess.run(
            ["git", "ls-tree", "--name-only", "HEAD", "--", path],
            cwd=ROOT, capture_output=True, check=True,
        )
        if lookup.stdout.splitlines() != [path.encode()]:
            continue
        result = subprocess.run(
            ["git", "show", f"HEAD:{path}"], cwd=ROOT, capture_output=True, check=True,
        )
        target = ROOT / "dist" / Path(path).relative_to("public")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.stdout)


def deploy_pages() -> str:
    ensure_release_scope()
    restore_tracked_public_files()
    env = {**os.environ, **load_env_file(PAGES_ENV)}
    if not env.get("CLOUDFLARE_API_TOKEN"):
        raise RuntimeError("CLOUDFLARE_API_TOKEN is missing")
    command = ["npx", "--yes", wrangler_spec(), "pages", "deploy", "dist", "--project-name", PROJECT, "--commit-dirty=true"]
    # A deploy is a single-shot upload: a flaky socket halfway through leaves
    # no deployment behind, so a transient failure must not be fatal. Retry
    # through the bridge, which turns the 2026-10-28 `TypeError: fetch failed`
    # kills into a success.
    last_error = ""
    for attempt in range(1, DEPLOY_ATTEMPTS + 1):
        attempt_env = proxy_env(env)
        result = subprocess.run(command, cwd=ROOT, env=attempt_env, text=True, capture_output=True)
        output = (getattr(result, "stdout", "") or "") + "\n" + (getattr(result, "stderr", "") or "")
        if result.returncode == 0:
            if "pages.dev" not in output and "Deployment complete" not in output:
                raise RuntimeError("Wrangler output lacks deployment completion evidence")
            return output.strip()
        last_error = output.strip()[-800:]
        if attempt < DEPLOY_ATTEMPTS:
            print(
                f"wrangler pages deploy attempt {attempt}/{DEPLOY_ATTEMPTS} failed "
                f"(rc={result.returncode}); retrying in {DEPLOY_RETRY_DELAY_SECONDS}s",
                flush=True,
            )
            time.sleep(DEPLOY_RETRY_DELAY_SECONDS)
    raise RuntimeError(f"wrangler pages deploy failed after {DEPLOY_ATTEMPTS} attempts: {last_error}")


def cloudflare_request(url: str, *, method: str = "GET", body: dict | None = None) -> dict:
    credentials = load_env_file(GLOBAL_ENV)
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "X-Auth-Email": credentials["CLOUDFLARE_EMAIL"],
            "X-Auth-Key": credentials["CLOUDFLARE_API_KEY"],
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode("utf-8") if body is not None else None,
    )
    # api.cloudflare.com is reachable over the local SOCKS bridge far more
    # reliably than over the direct path (7.0s vs 0.9s on 2026-10-28), and a
    # purge that fails after a successful upload leaves the custom domain
    # serving stale assets. When the bridge is down, fall back to direct.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY_BRIDGE, "https": PROXY_BRIDGE})
        if proxy_bridge_available()
        else urllib.request.ProxyHandler({})
    )
    last_error: Exception | None = None
    for attempt in range(1, CLOUDFLARE_REQUEST_ATTEMPTS + 1):
        try:
            with opener.open(request, timeout=30) as response:
                payload = json.load(response)
            break
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt >= CLOUDFLARE_REQUEST_ATTEMPTS:
                raise RuntimeError(
                    f"Cloudflare API request failed after {CLOUDFLARE_REQUEST_ATTEMPTS} attempts: {exc}"
                ) from exc
            time.sleep(CLOUDFLARE_REQUEST_DELAY_SECONDS)
    else:  # pragma: no cover - loop always breaks or raises
        raise RuntimeError(f"Cloudflare API request failed: {last_error}")
    if not payload.get("success"):
        raise RuntimeError(f"Cloudflare API request failed: {payload.get('errors')}")
    return payload


def purge_custom_domain() -> None:
    query = urllib.parse.urlencode({"name": ZONE_NAME, "status": "active", "per_page": 1})
    zones = cloudflare_request(f"https://api.cloudflare.com/client/v4/zones?{query}")
    rows = zones.get("result") or []
    if len(rows) != 1:
        raise RuntimeError(f"Cloudflare zone lookup returned {len(rows)} rows")
    zone_id = rows[0]["id"]
    cloudflare_request(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache",
        method="POST",
        body={"purge_everything": True},
    )


def probe_urls(urls: Iterable[str]) -> None:
    for url in urls:
        separator = "&" if "?" in url else "?"
        for attempt in range(URL_PROBE_ATTEMPTS):
            request = urllib.request.Request(
                f"{url}{separator}bust={time.time_ns()}",
                headers={"User-Agent": "HermesPagesRelease/1.0", "Cache-Control": "no-cache"},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.status != 200:
                        raise RuntimeError(f"production probe failed: {url} HTTP {response.status}")
                    response.read(1024)
                break
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"production probe failed: {url} HTTP {exc.code}") from exc
            except OSError as exc:
                if attempt >= URL_PROBE_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"production probe connection failed after {URL_PROBE_ATTEMPTS} attempts: {url}: {exc}"
                    ) from exc
                time.sleep(URL_PROBE_DELAY_SECONDS)


def probe_json_matches(matches: dict[str, Path]) -> None:
    for url, local_path in matches.items():
        expected = json.loads((local_path if local_path.is_absolute() else ROOT / local_path).read_text(encoding="utf-8"))
        for attempt in range(JSON_PROBE_ATTEMPTS):
            separator = "&" if "?" in url else "?"
            request = urllib.request.Request(
                f"{url}{separator}bust={time.time_ns()}",
                headers={"User-Agent": "HermesPagesRelease/1.0", "Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                actual = json.load(response)
            if actual == expected:
                break
            if attempt < JSON_PROBE_ATTEMPTS - 1:
                time.sleep(JSON_PROBE_DELAY_SECONDS)
        else:
            raise RuntimeError(f"production JSON differs from local release artifact after retries: {url}")


def release_pages(probes: Iterable[str], json_matches: dict[str, Path] | None = None) -> str:
    output = deploy_pages()
    purge_custom_domain()
    probe_urls(probes)
    if json_matches:
        probe_json_matches(json_matches)
    return output
