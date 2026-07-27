from types import SimpleNamespace
from pathlib import Path
import io
import json

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


def test_json_probe_retries_during_pages_eventual_consistency(tmp_path, monkeypatch):
    expected_path = tmp_path / "snapshot.json"
    expected_path.write_text(json.dumps({"generated_at": "new"}), encoding="utf-8")
    responses = [io.BytesIO(json.dumps({"generated_at": "old"}).encode()), io.BytesIO(json.dumps({"generated_at": "new"}).encode())]
    monkeypatch.setattr(pages_release.urllib.request, "urlopen", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(pages_release.time, "sleep", lambda seconds: None)
    pages_release.probe_json_matches({"https://example.test/data.json": expected_path})
    assert responses == []
