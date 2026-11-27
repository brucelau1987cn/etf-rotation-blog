"""The formal close entry against disposable Git, with external I/O stubbed."""
import json
from pathlib import Path
import subprocess

import pytest

from scripts import update_us_etf_garden as pub
import pages_release as pages


OLD = "2026-11-18"
DAY = "2026-11-19"
ROUTES = ("us-compass", "us-momentum", "us-macro", "us-compass/history")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def data(root, day):
    payloads = {
        "us-etf-pool.json": {"model_date": day, "session_state": "closed"},
        "us-etf-garden.json": {"date": day, "stage": "美股收盘版", "session_state": "closed"},
        "us-compass-health.json": {"model_date": day},
        "us-etf-flower-history.json": {"records": [{"date": day}]},
        "us-macro-dashboard.json": {"market": {"spy": {"date": day}}},
        "us-compass-learning.json": {"snapshots": [day]},
    }
    for relative in pub.US_OWNED_FILES:
        path = root / relative
        path.write_text(json.dumps(payloads.get(path.name, {"date": day})), encoding="utf-8")


@pytest.fixture
def close(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    root.mkdir()
    git(root, "init", "-qb", "main")
    git(root, "config", "user.name", "fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    for directory in ("public/data", "functions", "scripts", "src"):
        (root / directory).mkdir(parents=True)
    (root / ".gitignore").write_text("dist/\n.build-venv/\nnode_modules/\npublic/data/ignored.json\n")
    (root / "scripts/work.py").write_text("published code")
    (root / "src/page.astro").write_text("published page")
    (root / "functions/api.js").write_text("published function")
    (root / "public/data/foreign.json").write_text("published data")
    data(root, OLD)
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")
    bare = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(root), str(bare))
    git(root, "remote", "add", "origin", str(bare))
    git(root, "fetch", "--quiet", "origin", "main")
    baseline = git(bare, "rev-parse", "HEAD")
    drafts = {
        "scripts/work.py": "edited code", "src/page.astro": "edited page",
        "functions/api.js": "edited function", "functions/new.js": "untracked function",
        "public/data/foreign.json": "edited data", "public/data/new.json": "untracked data",
        "public/data/ignored.json": "ignored data",
    }
    for relative, text in drafts.items():
        (root / relative).write_text(text)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"phase": "published", "trade_date": OLD, "commit": baseline,
                                 "deployed": True, "verified": True, "learning_snapshots": 2}))
    for key, value in {"REPO": root, "STATE": state, "POOL": root / pub.FILES[0],
                       "GARDEN": root / pub.FILES[1]}.items():
        monkeypatch.setattr(pub, key, value)
    monkeypatch.setattr(pages, "ROOT", root)
    monkeypatch.setattr(pub, "latest_spy_date", lambda: DAY)
    monkeypatch.setattr(pub, "session_state", lambda *_: "closed")
    monkeypatch.setattr(pages, "load_env_file", lambda *_: pytest.fail("unexpected credentials access"))
    events = []
    faults = {}
    def build(tree):
        events.append(("build", tree))
        (tree / "dist/data").mkdir(parents=True)
        for path in pub.US_OWNED_FILES:
            (tree / "dist/data" / Path(path).name).write_bytes((tree / path).read_bytes())
        for route in ROUTES:
            dest = tree / "dist" / route / "index.html"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text((tree / "src/page.astro").read_text())
        if "build" in faults:
            faults["build"](tree)
    def run(*args, check=True, cwd=None, env=None):
        if args[0] == "git":
            if args[1] == "push":
                events.append(("push", root))
            return subprocess.run(args, cwd=cwd or root, text=True, capture_output=True, check=check, env=env)
        if "scripts/update_us_etf_bar_cache.py" in args and "collect" in faults:
            faults["collect"]()
        if "scripts/generate_us_etf_garden.py" in args:
            data(root, DAY)
        if "--no-contract-chain" in args:
            build(cwd)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(pub, "run", run)
    # Original boundary: keep the real Pages scope guard; stub only remote work.
    monkeypatch.setattr(pub, "build_static_site", lambda **_: build(root), raising=False)
    real_scope = pages.ensure_release_scope
    def deploy(*, root=None, commit=None, before_upload=None):
        target = root or pub.REPO
        if root is None:
            real_scope()
        else:
            real_scope(root=root)
        if before_upload is not None:
            before_upload()
        events.append(("deploy", target))
        assert git(bare, "rev-parse", "HEAD") == commit
        assert (target / "functions/api.js").read_text() == "published function"
        assert not (target / "functions/new.js").exists()
        assert not (target / "public/data/ignored.json").exists()
        assert (target / "dist/us-compass/index.html").read_text() == "published page"
        assert (target / "public/data/foreign.json").read_text() == "published data"
        return "Deployment complete! https://abc.etf-rotation-blog.pages.dev"
    monkeypatch.setattr(pages, "deploy_pages", deploy)
    def verify(output, commit):
        assert commit == git(bare, "rev-parse", "HEAD")
        events.append(("deployment evidence", commit))
        if "deploy" in faults:
            faults["deploy"]()
        return "https://abc.etf-rotation-blog.pages.dev"
    monkeypatch.setattr(pages, "verify_production_deployment", verify, raising=False)
    monkeypatch.setattr(pages, "purge_custom_domain", lambda: events.append(("purge",)))
    monkeypatch.setattr(pages, "probe_urls", lambda urls: events.append(("urls", tuple(urls))))
    def readback(matches, **kwargs):
        events.append(("readback", matches))
        assert len(matches) == len(pub.US_OWNED_FILES)
        sha = git(bare, "rev-parse", "HEAD")
        for url, raw in matches.items():
            blob = f"{sha}:public/data/{url.rsplit('/', 1)[-1]}"
            assert raw == subprocess.check_output(["git", "-C", str(bare), "show", blob])
        if "readback" in faults:
            faults["readback"]()
    monkeypatch.setattr(pages, "probe_json_matches", readback)
    monkeypatch.setattr(pages, "probe_json_bytes", readback, raising=False)
    return root, bare, baseline, state, drafts, events, faults


@pytest.mark.parametrize("recovery", [False, True])
def test_formal_entry_builds_and_deploys_pinned_tree_despite_foreign_drafts(close, recovery):
    root, bare, baseline, state, drafts, events, _ = close
    if recovery:
        data(root, DAY)
        with pytest.raises(SystemExit) as done:
            pub.main()
        assert done.value.code == 0
    else:
        pub.main()
    assert [e[0] for e in events] == ["build", "push", "deploy", "deployment evidence", "purge", "urls", "readback"]
    candidate = events[0][1]
    assert candidate != root and candidate == events[2][1] and not candidate.exists()
    assert not (root / "dist").exists()
    assert git(root, "diff", "--cached", "--name-only") == ""
    assert git(root, "rev-parse", "HEAD^1") == baseline
    assert set(git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()) == set(pub.US_OWNED_FILES)
    assert all((root / path).read_text() == value for path, value in drafts.items())
    receipt = json.loads(state.read_text())
    assert receipt["phase"] == "published" and receipt["trade_date"] == DAY
    assert receipt["commit"] == git(bare, "rev-parse", "HEAD")
    assert receipt["pushed"] is receipt["deployed"] is receipt["verified"] is True
    assert dict(events[5:])["urls"] == tuple(f"https://etf.peekabo.cc/{route}/" for route in ROUTES)
    assert set(dict(events[5:])["readback"]) == {
        f"https://etf.peekabo.cc/{Path(path).relative_to('public').as_posix()}" for path in pub.US_OWNED_FILES
    }


@pytest.mark.parametrize("kind", ["staged", "owned", "dist", "head", "remote"])
def test_formal_entry_blocks_drift_before_push(close, kind):
    root, bare, baseline, state, _, events, faults = close
    def tamper(tree):
        if kind == "staged":
            git(root, "add", "scripts/work.py")
        elif kind == "owned":
            (root / pub.FILES[0]).write_text("concurrent draft")
        elif kind == "dist":
            (tree / "dist/data/us-etf-pool.json").write_text("wrong build bytes")
        elif kind == "head":
            git(root, "update-ref", "refs/heads/main", baseline)
        else:
            git(bare, "update-ref", "-d", "refs/heads/main")
    faults["build"] = tamper
    with pytest.raises(RuntimeError, match="index|owned|dist|HEAD|remote"):
        pub.main()
    assert [e[0] for e in events] == ["build"]
    receipt = json.loads(state.read_text())
    assert receipt["deployed"] is receipt["verified"] is False


@pytest.mark.parametrize("stage", ["deploy", "readback"])
def test_receipt_distinguishes_push_deploy_and_readback(close, stage):
    _, bare, _, state, _, _, faults = close
    def fail():
        raise RuntimeError(f"failed {stage}")
    faults[stage] = fail
    with pytest.raises(RuntimeError, match=f"failed {stage}"):
        pub.main()
    receipt = json.loads(state.read_text())
    assert receipt["trade_date"] == DAY and receipt["commit"] == git(bare, "rev-parse", "HEAD")
    assert receipt["pushed"] is True
    assert receipt["deployed"] is (stage == "readback")
    assert receipt["verified"] is False


@pytest.mark.parametrize("stage", ["deploy", "readback"])
def test_failed_pushed_close_resumes_exact_sha_before_yahoo(close, monkeypatch, stage):
    root, bare, _, state, drafts, events, faults = close

    def fail():
        raise RuntimeError(f"failed {stage}")

    faults[stage] = fail
    with pytest.raises(RuntimeError, match=f"failed {stage}"):
        pub.main()
    # The scheduled entry records the exception while preserving phase evidence.
    pub.write_state("error", error=f"failed {stage}")
    sha = git(bare, "rev-parse", "HEAD")
    events.clear()
    del faults[stage]
    faults["collect"] = lambda: pytest.fail("recovery fetched bars")
    monkeypatch.setattr(pub, "latest_spy_date", lambda: pytest.fail("recovery queried Yahoo"))

    pub.main()

    assert [e[0] for e in events] == ["build", "deploy", "deployment evidence", "purge", "urls", "readback"]
    tree = events[0][1]
    assert tree == events[1][1] and tree != root and not tree.exists()
    assert git(root, "rev-parse", "HEAD") == git(bare, "rev-parse", "HEAD") == sha
    assert all((root / path).read_text() == value for path, value in drafts.items())
    receipt = json.loads(state.read_text())
    assert (receipt["phase"], receipt["trade_date"], receipt["commit"]) == ("published", DAY, sha)
    assert receipt["pushed"] is receipt["deployed"] is receipt["verified"] is True
    assert "error" not in receipt


def test_push_receipt_interruption_recovers_from_remote_identity(close, monkeypatch):
    root, bare, _, state, _, events, _ = close
    write_state = pub.write_state

    with monkeypatch.context() as patch:
        def interrupt(phase, **fields):
            if phase == "pushed":
                raise RuntimeError("interrupted before recording push")
            write_state(phase, **fields)

        patch.setattr(pub, "write_state", interrupt)
        with pytest.raises(RuntimeError, match="before recording push"):
            pub.main()
    sha = git(bare, "rev-parse", "HEAD")
    assert json.loads(state.read_text())["pushed"] is False
    events.clear()
    monkeypatch.setattr(pub, "latest_spy_date", lambda: pytest.fail("recovery queried Yahoo"))

    pub.main()

    assert git(root, "rev-parse", "HEAD") == git(bare, "rev-parse", "HEAD") == sha
    assert [event[0] for event in events] == ["build", "deploy", "deployment evidence", "purge", "urls", "readback"]
    assert json.loads(state.read_text())["phase"] == "published"


def test_pending_pushed_close_blocks_remote_drift_without_moving_source(close, monkeypatch, tmp_path):
    root, bare, _, state, _, events, faults = close

    def fail_deploy():
        raise RuntimeError("failed deploy")

    faults["deploy"] = fail_deploy
    with pytest.raises(RuntimeError, match="failed deploy"):
        pub.main()
    sha = git(root, "rev-parse", "HEAD")
    other = tmp_path / "other-writer"
    git(tmp_path, "clone", "-q", str(bare), str(other))
    git(other, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--allow-empty", "-qm", "another release")
    git(other, "push", "origin", "main")
    before = state.read_bytes()
    events.clear()
    monkeypatch.setattr(pub, "latest_spy_date", lambda: pytest.fail("pending recovery fell through to Yahoo"))

    with pytest.raises(RuntimeError, match="pending pushed close.*remote main"):
        pub.main()

    assert git(root, "rev-parse", "HEAD") == sha
    assert state.read_bytes() == before and events == []


def test_same_date_cannot_turn_legacy_failure_into_success(close, monkeypatch):
    _, _, baseline, state, _, events, _ = close
    monkeypatch.setattr(pub, "latest_spy_date", lambda: OLD)
    prior = {"phase": "error", "trade_date": OLD, "commit": baseline, "deployed": True, "verified": True}
    state.write_text(json.dumps(prior))
    with pytest.raises(RuntimeError, match="same-date.*unverified"):
        pub.main()
    assert json.loads(state.read_text()) == prior
    assert events == []


def test_same_date_with_matching_verified_receipt_skips_build_and_deploy(close):
    _, bare, _, state, _, events, _ = close
    pub.main()
    sha = git(bare, "rev-parse", "HEAD")
    first = json.loads(state.read_text())
    events.clear()
    with pytest.raises(SystemExit) as done:
        pub.main()
    assert done.value.code == 0 and not events
    receipt = json.loads(state.read_text())
    assert receipt["phase"] == "idempotent" and receipt["commit"] == sha
    assert receipt["target_day"] == first["target_day"] == DAY
    assert receipt["receipt_version"] == first["receipt_version"] == 1
    assert set(receipt["verified_assets"]) == set(pub.US_OWNED_FILES)
    assert receipt["verified_assets"] == first["verified_assets"]
    assert receipt["verified_routes"] == first["verified_routes"] == list(ROUTES)


@pytest.mark.parametrize("mutation", ["legacy", "version", "target", "asset-count", "asset-hash",
                                      "routes", "flags", "deployment"])
def test_same_date_rejects_incomplete_or_different_sha_receipt(close, mutation):
    root, bare, _, state, _, events, _ = close
    pub.main()
    receipt = json.loads(state.read_text())
    if mutation == "legacy":
        receipt = {k: receipt[k] for k in ("phase", "trade_date", "commit", "deployed", "verified")}
    elif mutation == "version":
        receipt["receipt_version"] = True
    elif mutation == "target":
        receipt["target_day"] = OLD
    elif mutation == "asset-count":
        receipt.get("verified_assets", {}).pop(pub.US_OWNED_FILES[0], None)
    elif mutation == "asset-hash":
        receipt.setdefault("verified_assets", {})[pub.US_OWNED_FILES[0]] = "0" * 64
    elif mutation == "routes":
        receipt["verified_routes"] = list(ROUTES[:-1])
    elif mutation == "flags":
        receipt["pushed"] = 1
    elif mutation == "deployment":
        receipt["deployment_url"] = "https://example.invalid/"
    state.write_text(json.dumps(receipt))
    before = state.read_bytes()
    events.clear()
    with pytest.raises(RuntimeError, match="same-date.*unverified"):
        pub.main()
    assert state.read_bytes() == before
    assert not events


def test_same_date_accepts_ancestor_receipt_when_us_payload_unchanged(close):
    """Unrelated commits keep a verified US close valid: the receipt commit is an
    ancestor of HEAD and the US-owned blobs are byte-identical at HEAD."""

    root, bare, _, state, _, events, _ = close
    pub.main()
    receipt = json.loads(state.read_text())
    git(root, "commit", "--only", "-qm", "unrelated page", "--", "src/page.astro")
    git(root, "push", "origin", "main")
    assert git(bare, "rev-parse", "HEAD") != receipt["commit"]
    events.clear()
    with pytest.raises(SystemExit) as done:
        pub.main()
    assert done.value.code == 0 and not events
    refreshed = json.loads(state.read_text())
    assert refreshed["phase"] == "idempotent"
    assert refreshed["receipt_version"] == receipt["receipt_version"]
    assert refreshed["verified_assets"] == receipt["verified_assets"]
    assert refreshed["verified_routes"] == receipt["verified_routes"]