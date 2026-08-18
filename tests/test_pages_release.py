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
        ["https://etf.peekabo.cc/paper/"],
        {"https://etf.peekabo.cc/data/paper-trading.json": Path("public/data/paper-trading.json")},
    )

    assert calls[0] == ["npx", "wrangler", "pages", "deploy", "dist", "--project-name", "etf-rotation-blog", "--commit-dirty=true"]
    assert calls[1] == ["purge"]
    assert calls[2] == ["probe", "https://etf.peekabo.cc/paper/"]
    assert calls[3] == ["json", "https://etf.peekabo.cc/data/paper-trading.json"]


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
