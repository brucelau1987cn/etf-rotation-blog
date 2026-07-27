import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("publish_futures", ROOT / "scripts/publish_futures_compass.py")
assert SPEC and SPEC.loader
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


def result(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def test_futures_publisher_refreshes_validates_builds_commits_and_deploys(monkeypatch):
    calls = []

    def fake_run(command, check=True, **kwargs):
        calls.append(command)
        if command == ["git", "branch", "--show-current"]:
            return result("main\n")
        if command[:3] == ["git", "diff", "--cached"]:
            return result(returncode=0)
        if command[:4] == ["git", "diff", "--quiet", "--"]:
            return result(returncode=0 if len(calls) < 4 else 1)
        if command[:3] == ["git", "merge-base", "--is-ancestor"]:
            return result(returncode=0)
        return result()

    monkeypatch.setattr(publisher, "run", fake_run)
    probes = []
    monkeypatch.setattr(publisher, "release_pages", lambda urls: probes.extend(urls))
    publisher.publish("day-close")

    assert [publisher.FUTURES_PYTHON, "scripts/run_futures_compass_maintenance.py", "--slot", "day-close"] in calls
    assert [publisher.FUTURES_PYTHON, "scripts/validate_futures_compass.py"] in calls
    assert ["npm", "run", "build"] in calls
    assert ["git", "commit", "--only", "-m", "data: refresh futures compass day-close", "--", publisher.SNAPSHOT] in calls
    assert probes == [
        "https://etf.peekabo.cc/futures-compass/",
        "https://etf.peekabo.cc/data/futures-compass.json",
    ]
