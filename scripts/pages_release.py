#!/usr/bin/env python3
"""Direct Cloudflare Pages deployment, cache purge, and production probes."""
from __future__ import annotations

import json
import os
import subprocess
import time
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
        exists = subprocess.run(
            ["git", "cat-file", "-e", f"HEAD:{path}"], cwd=ROOT, capture_output=True,
        )
        if exists.returncode != 0:
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
    command = ["npx", "wrangler", "pages", "deploy", "dist", "--project-name", PROJECT, "--commit-dirty=true"]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=True)
    output = (getattr(result, "stdout", "") or "") + "\n" + (getattr(result, "stderr", "") or "")
    if "pages.dev" not in output and "Deployment complete" not in output:
        raise RuntimeError("Wrangler output lacks deployment completion evidence")
    return output.strip()


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
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
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
    bust = int(time.time())
    for url in urls:
        separator = "&" if "?" in url else "?"
        request = urllib.request.Request(
            f"{url}{separator}bust={bust}",
            headers={"User-Agent": "HermesPagesRelease/1.0", "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(f"production probe failed: {url} HTTP {response.status}")
            response.read(1024)


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
