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


def load_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def deploy_pages() -> str:
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


def release_pages(probes: Iterable[str]) -> str:
    output = deploy_pages()
    purge_custom_domain()
    probe_urls(probes)
    return output
