import os
import select
import subprocess
import sys
import threading

from scripts import a_share_nightly_contract as contract


def test_nightly_contract_has_no_paper_trading_lock():
    assert not hasattr(contract, "paper_publish_lock")
    assert not hasattr(contract, "PAPER_LOCK")


def child_lock_command(lock_path):
    return [
        sys.executable,
        "-u",
        "-c",
        (
            "import sys; "
            "from pathlib import Path; "
            "from scripts import a_share_nightly_contract as c; "
            "c.SITE_PUBLISH_LOCK = Path(sys.argv[1]); "
            "sys.stdin.readline(); "
            "ctx = c.site_publish_lock(); ctx.__enter__(); "
            "print('acquired', flush=True); "
            "sys.stdin.readline(); ctx.__exit__(None, None, None)"
        ),
        str(lock_path),
    ]


def test_site_publish_lock_sets_lease_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "SITE_PUBLISH_LOCK", tmp_path / "site.lock")
    monkeypatch.delenv("SITE_PUBLISH_LOCK_HELD", raising=False)
    with contract.site_publish_lock():
        marker = os.environ["SITE_PUBLISH_LOCK_HELD"]
        assert marker.startswith(f"{os.getpid()}:")
        assert (tmp_path / "site.lock.lease").is_file()
    assert "SITE_PUBLISH_LOCK_HELD" not in os.environ
    assert not (tmp_path / "site.lock.lease").exists()


def test_site_publish_lock_fd_is_not_inherited_by_child(tmp_path, monkeypatch):
    lock_path = tmp_path / "site.lock"
    monkeypatch.setattr(contract, "SITE_PUBLISH_LOCK", lock_path)
    with contract.site_publish_lock():
        parent_inode = lock_path.stat().st_ino
        child = subprocess.run(
            [sys.executable, "-c", "import os; print(' '.join(os.readlink('/proc/self/fd/'+x) for x in os.listdir('/proc/self/fd') if os.path.exists('/proc/self/fd/'+x)))"],
            text=True,
            capture_output=True,
            close_fds=False,
            check=True,
        )
        assert str(lock_path) not in child.stdout
        assert parent_inode > 0


def test_site_publish_lock_is_same_thread_reentrant(tmp_path, monkeypatch):
    lock_path = tmp_path / "site.lock"
    monkeypatch.setattr(contract, "SITE_PUBLISH_LOCK", lock_path)
    with contract.site_publish_lock():
        marker = os.environ["SITE_PUBLISH_LOCK_HELD"]
        with contract.site_publish_lock():
            assert os.environ["SITE_PUBLISH_LOCK_HELD"] == marker


def test_site_publish_lock_excludes_competing_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "SITE_PUBLISH_LOCK", tmp_path / "site.lock")
    attempted = threading.Event()
    acquired = threading.Event()

    def contender():
        attempted.set()
        with contract.site_publish_lock():
            acquired.set()

    with contract.site_publish_lock():
        thread = threading.Thread(target=contender)
        thread.start()
        assert attempted.wait(1)
        assert not acquired.wait(0.2)
    thread.join(1)
    assert acquired.is_set()


def test_site_publish_lock_excludes_competing_process(tmp_path, monkeypatch):
    lock_path = tmp_path / "site.lock"
    monkeypatch.setattr(contract, "SITE_PUBLISH_LOCK", lock_path)
    with contract.site_publish_lock():
        env = os.environ.copy()
        env.pop("SITE_PUBLISH_LOCK_HELD", None)
        child = subprocess.Popen(
            child_lock_command(lock_path),
            cwd=contract.Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        child.stdin.write("go\n")
        child.stdin.flush()
        assert not select.select([child.stdout], [], [], 0.2)[0]
    assert child.stdout.readline().strip() == "acquired"
    child.stdin.write("done\n")
    child.stdin.flush()
    assert child.wait(timeout=2) == 0


def test_inherited_child_reuses_live_ancestor_lease(tmp_path, monkeypatch):
    lock_path = tmp_path / "site.lock"
    monkeypatch.setattr(contract, "SITE_PUBLISH_LOCK", lock_path)
    with contract.site_publish_lock():
        child = subprocess.Popen(
            child_lock_command(lock_path),
            cwd=contract.Path(__file__).resolve().parents[1],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        child.stdin.write("go\n")
        child.stdin.flush()
        assert select.select([child.stdout], [], [], 1)[0]
        assert child.stdout.readline().strip() == "acquired"
        child.stdin.write("done\n")
        child.stdin.flush()
        assert child.wait(timeout=2) == 0


def test_delayed_inherited_child_cannot_reuse_released_lease(tmp_path, monkeypatch):
    lock_path = tmp_path / "site.lock"
    monkeypatch.setattr(contract, "SITE_PUBLISH_LOCK", lock_path)
    contender_acquired = threading.Event()
    release_contender = threading.Event()

    with contract.site_publish_lock():
        child = subprocess.Popen(
            child_lock_command(lock_path),
            cwd=contract.Path(__file__).resolve().parents[1],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def contender():
        with contract.site_publish_lock():
            contender_acquired.set()
            release_contender.wait(2)

    thread = threading.Thread(target=contender)
    thread.start()
    assert contender_acquired.wait(1)
    child.stdin.write("go\n")
    child.stdin.flush()
    assert not select.select([child.stdout], [], [], 0.2)[0]
    release_contender.set()
    thread.join(1)
    assert child.stdout.readline().strip() == "acquired"
    child.stdin.write("done\n")
    child.stdin.flush()
    assert child.wait(timeout=2) == 0


def test_plain_inherited_marker_has_no_lock_authority(tmp_path, monkeypatch):
    lock_path = tmp_path / "site.lock"
    monkeypatch.setattr(contract, "SITE_PUBLISH_LOCK", lock_path)
    monkeypatch.setenv("SITE_PUBLISH_LOCK_HELD", "1")
    with contract.site_publish_lock():
        assert lock_path.exists()
