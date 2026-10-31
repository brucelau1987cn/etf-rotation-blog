from email.message import Message
from types import SimpleNamespace
from pathlib import Path
import io
import json
import subprocess

import pytest

from scripts import pages_release


def test_pages_release_deploys_then_purges_and_probes(monkeypatch):
    calls = []
    monkeypatch.setattr(pages_release, "load_env_file", lambda path: {"CLOUDFLARE_API_TOKEN": "test"})
    monkeypatch.setattr(pages_release, "ensure_release_scope", lambda: None)
    monkeypatch.setattr(pages_release, "restore_tracked_public_files", lambda: None)
    monkeypatch.setattr(
        pages_release.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(stdout="Deployment complete! https://test.pages.dev", returncode=0),
    )
    monkeypatch.setattr(pages_release, "purge_custom_domain", lambda: calls.append(["purge"]))
    monkeypatch.setattr(pages_release, "probe_urls", lambda urls: calls.append(["probe", *urls]))
    monkeypatch.setattr(pages_release, "probe_json_matches", lambda matches: calls.append(["json", *matches]))

    pages_release.release_pages(
        ["https://etf.peekabo.cc/a-compass/"],
        {"https://etf.peekabo.cc/data/etf-garden-pool.json": Path("public/data/etf-garden-pool.json")},
    )

    assert calls[0] == ["npx", "--yes", "wrangler@4.153.0", "pages", "deploy", "dist", "--project-name", "etf-rotation-blog", "--commit-dirty=true"]
    assert calls[1] == ["purge"]
    assert calls[2] == ["probe", "https://etf.peekabo.cc/a-compass/"]
    assert calls[3] == ["json", "https://etf.peekabo.cc/data/etf-garden-pool.json"]


def test_deploy_command_pins_wrangler_version(monkeypatch):
    """`npx wrangler` resolves the newest release, which the npm mirror may not have.

    2026-10-28: latest wrangler required miniflare@5.20261023.1-beta that
    registry.npmmirror.com had not synced, so every publisher's deploy step died.
    The command must name an explicit version spec, never bare `wrangler`.
    """
    calls = []
    monkeypatch.delenv("WRANGLER_SPEC", raising=False)
    monkeypatch.setattr(pages_release, "load_env_file", lambda path: {"CLOUDFLARE_API_TOKEN": "test"})
    monkeypatch.setattr(pages_release, "ensure_release_scope", lambda: None)
    monkeypatch.setattr(pages_release, "restore_tracked_public_files", lambda: None)
    monkeypatch.setattr(
        pages_release.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(stdout="Deployment complete! https://test.pages.dev", returncode=0),
    )

    output = pages_release.deploy_pages()

    assert "Deployment complete" in output
    assert calls[0][:3] == ["npx", "--yes", "wrangler@4.153.0"]
    assert "wrangler" not in calls[0]


def test_deploy_command_honours_wrangler_spec_override(monkeypatch):
    calls = []
    monkeypatch.setenv("WRANGLER_SPEC", "wrangler@9.9.9")
    monkeypatch.setattr(pages_release, "load_env_file", lambda path: {"CLOUDFLARE_API_TOKEN": "test"})
    monkeypatch.setattr(pages_release, "ensure_release_scope", lambda: None)
    monkeypatch.setattr(pages_release, "restore_tracked_public_files", lambda: None)
    monkeypatch.setattr(
        pages_release.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(stdout="Deployment complete! https://test.pages.dev", returncode=0),
    )

    pages_release.deploy_pages()

    assert calls[0][2] == "wrangler@9.9.9"


@pytest.mark.parametrize("bad", ["wrangler", "wrangler@latest", "  ", "wrangler@4", "evil-pkg@1.2.3"])
def test_wrangler_spec_rejects_unpinned_values(monkeypatch, bad):
    monkeypatch.setenv("WRANGLER_SPEC", bad)
    if bad.strip():
        with pytest.raises(RuntimeError, match="WRANGLER_SPEC"):
            pages_release.wrangler_spec()
    else:
        # Empty/whitespace override falls back to the pin instead of failing.
        assert pages_release.wrangler_spec() == pages_release.WRANGLER_SPEC_DEFAULT


def test_deploy_retries_a_transient_wrkler_failure(monkeypatch):
    """A flaky upload socket must not kill the publish.

    2026-10-28 17:39: the precious-inventory publisher died at the deploy step
    with `TypeError: fetch failed` while api.cloudflare.com was unreachable
    over the direct path (7.0s, then a socket error). The upload had already
    committed locally, so a single transient failure cost the whole publish.
    """
    calls = []

    def fake_run(command, **kwargs):
        calls.append(kwargs.get("env", {}))
        if len(calls) == 1:
            return SimpleNamespace(stdout="", stderr="TypeError: fetch failed", returncode=1)
        return SimpleNamespace(stdout="Deployment complete! https://test.pages.dev", returncode=0)

    monkeypatch.setattr(pages_release, "load_env_file", lambda path: {"CLOUDFLARE_API_TOKEN": "test"})
    monkeypatch.setattr(pages_release, "ensure_release_scope", lambda: None)
    monkeypatch.setattr(pages_release, "restore_tracked_public_files", lambda: None)
    monkeypatch.setattr(pages_release.subprocess, "run", fake_run)
    monkeypatch.setattr(pages_release.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pages_release, "DEPLOY_ATTEMPTS", 3)

    output = pages_release.deploy_pages()

    assert "Deployment complete" in output
    assert len(calls) == 2


def test_deploy_gives_up_after_the_retry_budget(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="", stderr="fetch failed", returncode=1)

    monkeypatch.setattr(pages_release, "load_env_file", lambda path: {"CLOUDFLARE_API_TOKEN": "test"})
    monkeypatch.setattr(pages_release, "ensure_release_scope", lambda: None)
    monkeypatch.setattr(pages_release, "restore_tracked_public_files", lambda: None)
    monkeypatch.setattr(pages_release.subprocess, "run", fake_run)
    monkeypatch.setattr(pages_release.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pages_release, "DEPLOY_ATTEMPTS", 2)

    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        pages_release.deploy_pages()

    assert len(calls) == 2


def test_deploy_routes_cloudflare_traffic_through_the_proxy_bridge(monkeypatch):
    """Wrangler must reach Cloudflare over the local SOCKS bridge when it exists."""
    monkeypatch.setattr(pages_release, "load_env_file", lambda path: {"CLOUDFLARE_API_TOKEN": "test"})
    monkeypatch.setattr(pages_release, "ensure_release_scope", lambda: None)
    monkeypatch.setattr(pages_release, "restore_tracked_public_files", lambda: None)
    monkeypatch.setattr(pages_release, "proxy_bridge_available", lambda *_a, **_kw: True)

    envs = []

    def fake_run(command, **kwargs):
        envs.append(kwargs.get("env", {}))
        return SimpleNamespace(stdout="Deployment complete! https://test.pages.dev", returncode=0)

    monkeypatch.setattr(pages_release.subprocess, "run", fake_run)

    pages_release.deploy_pages()

    assert envs[0]["HTTPS_PROXY"] == pages_release.PROXY_BRIDGE
    assert envs[0]["HTTP_PROXY"] == pages_release.PROXY_BRIDGE
    assert envs[0]["NODE_USE_ENV_PROXY"] == "1"


def test_proxy_bridge_target_parsing_handles_the_scheme():
    """The bridge URL carries an http:// scheme; a naive rpartition(':') would
    resolve the host as 'http://127.0.0.1' and the check would always fail, so
    wrangler would silently keep using the flaky direct path.
    """
    assert pages_release._split_proxy_target("http://127.0.0.1:8899") == ("127.0.0.1", 8899)
    assert pages_release._split_proxy_target("127.0.0.1:8899") == ("127.0.0.1", 8899)
    assert pages_release._split_proxy_target("https://proxy.example:443") == ("proxy.example", 443)


def test_proxy_bridge_availability_uses_the_parsed_host(monkeypatch):
    seen = []

    class FakeSocket:
        def __init__(self, address, *args, **kwargs):
            seen.append(address)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(pages_release.socket, "create_connection", FakeSocket)

    assert pages_release.proxy_bridge_available("http://127.0.0.1:8899") is True
    assert seen == [("127.0.0.1", 8899)]

    def refuse(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(pages_release.socket, "create_connection", refuse)
    assert pages_release.proxy_bridge_available("http://127.0.0.1:8899") is False


def test_deploy_falls_back_to_direct_when_the_bridge_is_down(monkeypatch):
    monkeypatch.setattr(pages_release, "load_env_file", lambda path: {"CLOUDFLARE_API_TOKEN": "test"})
    monkeypatch.setattr(pages_release, "ensure_release_scope", lambda: None)
    monkeypatch.setattr(pages_release, "restore_tracked_public_files", lambda: None)
    monkeypatch.setattr(pages_release, "proxy_bridge_available", lambda *_a, **_kw: False)

    envs = []

    def fake_run(command, **kwargs):
        envs.append(kwargs.get("env", {}))
        return SimpleNamespace(stdout="Deployment complete! https://test.pages.dev", returncode=0)

    monkeypatch.setattr(pages_release.subprocess, "run", fake_run)

    pages_release.deploy_pages()

    assert "HTTPS_PROXY" not in envs[0]
    assert "NODE_USE_ENV_PROXY" not in envs[0]


def test_cloudflare_api_request_retries_transient_transport_errors(monkeypatch):
    """api.cloudflare.com over the direct path failed with a socket error on
    2026-10-28; the purge must retry instead of killing the publish."""
    attempts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"success": True, "result": []}).encode()

    def fake_open(request, timeout=30):
        attempts.append(request)
        if len(attempts) == 1:
            raise OSError(99, "Cannot assign requested address")
        return Response()

    monkeypatch.setattr(pages_release, "load_env_file", lambda path: {
        "CLOUDFLARE_EMAIL": "a@b.c", "CLOUDFLARE_API_KEY": "k",
    })
    monkeypatch.setattr(pages_release, "proxy_bridge_available", lambda *_a, **_kw: False)
    monkeypatch.setattr(pages_release.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pages_release.urllib.request, "build_opener", lambda *_a, **_kw: SimpleNamespace(open=fake_open))

    payload = pages_release.cloudflare_request("https://api.cloudflare.com/client/v4/zones")

    assert payload["success"] is True
    assert len(attempts) == 2


def test_npm_deploy_entrypoint_uses_the_same_pinned_spec():
    """`npm run deploy:pages` (used by release_dual_live.sh) must not re-resolve latest."""
    scripts = json.loads((Path(__file__).resolve().parents[1] / "package.json").read_text(encoding="utf-8"))["scripts"]
    deploy = scripts["deploy:pages"]
    assert "npx --yes wrangler@4.153.0 pages deploy" in deploy
    assert "npx wrangler pages deploy" not in deploy


def test_release_scope_allows_only_known_external_shadow_snapshots():
    assert pages_release.foreign_dirty_paths([" M public/data/korea-tech-factor-shadow.json"]) == []
    assert pages_release.foreign_dirty_paths([" M public/data/us-selector-shadow.json"]) == []
    assert pages_release.foreign_dirty_paths(["?? public/js/uncommitted.js"]) == ["public/js/uncommitted.js"]


def test_restore_tracked_public_files_skips_path_absent_from_head(tmp_path, monkeypatch):
    future = "public/data/future.json"
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["git", "ls-tree", "--name-only", "HEAD", "--", future]:
            return subprocess.CompletedProcess(command, 0, stdout=b"")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(pages_release, "ROOT", tmp_path)
    monkeypatch.setattr(pages_release, "EXTERNAL_DIRTY", (future,))
    monkeypatch.setattr(pages_release.subprocess, "run", fake_run)

    pages_release.restore_tracked_public_files()

    assert not (tmp_path / "dist/data/future.json").exists()
    assert [call[0] for call in calls] == [
        ["git", "ls-tree", "--name-only", "HEAD", "--", future],
    ]
    assert calls[0][1]["cwd"] == tmp_path
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["check"] is True
    assert calls[0][1].get("shell") is not True


def test_restore_tracked_public_files_restores_tracked_path(tmp_path, monkeypatch):
    tracked = "public/data/tracked.json"
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["git", "ls-tree", "--name-only", "HEAD", "--", tracked]:
            return subprocess.CompletedProcess(command, 0, stdout=f"{tracked}\n".encode())
        if command == ["git", "show", f"HEAD:{tracked}"]:
            return subprocess.CompletedProcess(command, 0, stdout=b'{"tracked": true}\n')
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(pages_release, "ROOT", tmp_path)
    monkeypatch.setattr(pages_release, "EXTERNAL_DIRTY", (tracked,))
    monkeypatch.setattr(pages_release.subprocess, "run", fake_run)

    pages_release.restore_tracked_public_files()

    assert (tmp_path / "dist/data/tracked.json").read_bytes() == b'{"tracked": true}\n'
    assert [call[0] for call in calls] == [
        ["git", "ls-tree", "--name-only", "HEAD", "--", tracked],
        ["git", "show", f"HEAD:{tracked}"],
    ]
    assert all(call[1]["cwd"] == tmp_path for call in calls)
    assert all(call[1]["capture_output"] is True for call in calls)
    assert all(call[1]["check"] is True for call in calls)
    assert all(call[1].get("shell") is not True for call in calls)


def test_restore_tracked_public_files_propagates_fatal_head_lookup(tmp_path, monkeypatch):
    tracked = "public/data/tracked.json"
    error = subprocess.CalledProcessError(
        128,
        ["git", "ls-tree", "--name-only", "HEAD", "--", tracked],
    )

    def fake_run(command, **kwargs):
        raise error

    monkeypatch.setattr(pages_release, "ROOT", tmp_path)
    monkeypatch.setattr(pages_release, "EXTERNAL_DIRTY", (tracked,))
    monkeypatch.setattr(pages_release.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError) as caught:
        pages_release.restore_tracked_public_files()

    assert caught.value is error
    assert not (tmp_path / "dist/data/tracked.json").exists()


def test_restore_tracked_public_files_skips_non_public_paths(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pages_release, "ROOT", tmp_path)
    monkeypatch.setattr(pages_release, "EXTERNAL_DIRTY", ("src/content/blog/future.md",))
    monkeypatch.setattr(pages_release.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    pages_release.restore_tracked_public_files()

    assert calls == []


def test_url_probe_retries_transient_socket_failure(monkeypatch):
    attempts = []

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, _limit):
            return b"ok"

    def fake_urlopen(*args, **kwargs):
        attempts.append((args, kwargs))
        if len(attempts) == 1:
            raise OSError(99, "Cannot assign requested address")
        return Response()

    monkeypatch.setattr(pages_release.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pages_release.time, "sleep", lambda seconds: None)
    busts = iter((1, 2))
    monkeypatch.setattr(pages_release.time, "time_ns", lambda: next(busts))
    monkeypatch.setattr(pages_release, "URL_PROBE_ATTEMPTS", 2)
    pages_release.probe_urls(["https://example.test/page/"])
    assert len(attempts) == 2
    assert attempts[0][0][0].full_url != attempts[1][0][0].full_url


def test_url_probe_stops_after_retry_budget(monkeypatch):
    attempts = []
    sleeps = []

    def fake_urlopen(*args, **kwargs):
        attempts.append((args, kwargs))
        raise OSError(99, "Cannot assign requested address")

    monkeypatch.setattr(pages_release.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pages_release.time, "sleep", lambda seconds: sleeps.append(seconds))
    busts = iter((1, 2, 3))
    monkeypatch.setattr(pages_release.time, "time_ns", lambda: next(busts))
    monkeypatch.setattr(pages_release, "URL_PROBE_ATTEMPTS", 3)
    with pytest.raises(RuntimeError, match="after 3 attempts") as caught:
        pages_release.probe_urls(["https://example.test/page/"])
    assert isinstance(caught.value.__cause__, OSError)
    assert len(attempts) == 3
    assert sleeps == [pages_release.URL_PROBE_DELAY_SECONDS] * 2
    assert len({call[0][0].full_url for call in attempts}) == 3


def test_url_probe_does_not_retry_http_failure(monkeypatch):
    attempts = []

    def fake_urlopen(request, **kwargs):
        attempts.append((request, kwargs))
        raise pages_release.urllib.error.HTTPError(
            request.full_url, 503, "Service Unavailable", Message(), None,
        )

    monkeypatch.setattr(pages_release.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pages_release.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        pages_release.probe_urls(["https://example.test/page/"])
    assert len(attempts) == 1


def test_json_probe_retries_during_pages_eventual_consistency(tmp_path, monkeypatch):
    expected_path = tmp_path / "snapshot.json"
    expected_path.write_text(json.dumps({"generated_at": "new"}), encoding="utf-8")
    responses = [io.BytesIO(json.dumps({"generated_at": "old"}).encode()), io.BytesIO(json.dumps({"generated_at": "new"}).encode())]
    monkeypatch.setattr(pages_release.urllib.request, "urlopen", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(pages_release.time, "sleep", lambda seconds: None)
    pages_release.probe_json_matches({"https://example.test/data.json": expected_path})
    assert responses == []


def test_quarantine_restores_only_unowned_tracked_shadow_files(monkeypatch):
    """A publisher must not bake another job's dirty bytes into the catalog.

    2026-10-31: the weekly US publish regenerated catalog.json while
    a-han-precise-signals.json held uncommitted 2026-10-30 output. The committed
    catalog described bytes that never entered the commit, so every clean-tree
    build afterwards regenerated a different catalog and the A-share nightly
    candidate gate died with "candidate build rewrote managed public artifacts".
    """
    dirty = [
        " M public/data/a-han-precise-signals.json",
        " M public/data/us-compass-research.json",
        " M public/data/catalog.json",
        " M scripts/publish_us_compass_research.py",
        "?? src/content/blog/2026-10-30.md",
    ]
    checked_out = []
    missing_from_head: set[str] = set()  # every shadow path here is tracked at HEAD

    def fake_run(command, **kwargs):
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout="\n".join(dirty), returncode=0)
        if command[:2] == ["git", "cat-file"]:
            path = command[-1].split(":", 1)[1]
            return SimpleNamespace(stdout="", returncode=1 if path in missing_from_head else 0)
        if command[:2] == ["git", "checkout"]:
            checked_out.append(command[-1])
            return SimpleNamespace(stdout="", returncode=0)
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(pages_release.subprocess, "run", fake_run)

    restored = pages_release.quarantine_unowned_shadow_dirty(
        {"public/data/us-compass-research.json", "public/data/catalog.json"},
    )

    # Owned files are left alone; the tracked unowned shadow file is restored.
    assert restored == ["public/data/a-han-precise-signals.json"]
    assert checked_out == ["public/data/a-han-precise-signals.json"]


def test_quarantine_never_touches_code_or_untracked_paths(monkeypatch):
    dirty = [" M scripts/pages_release.py", "?? tests/brand_new.py"]

    def fake_run(command, **kwargs):
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout="\n".join(dirty), returncode=0)
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(pages_release.subprocess, "run", fake_run)

    assert pages_release.quarantine_unowned_shadow_dirty() == []


def test_quarantine_skips_shadow_paths_absent_from_head(monkeypatch):
    """Untracked shadow output has no committed bytes to restore to."""
    dirty = [" M public/data/us-selector-shadow.json"]

    def fake_run(command, **kwargs):
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout="\n".join(dirty), returncode=0)
        if command[:2] == ["git", "cat-file"]:
            return SimpleNamespace(stdout="", returncode=1)
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(pages_release.subprocess, "run", fake_run)

    assert pages_release.quarantine_unowned_shadow_dirty() == []
