from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_us_macro.py"

spec = importlib.util.spec_from_file_location("generate_us_macro_sahm", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_parse_fred_sahm_metadata_separates_observation_and_release_dates():
    text = """
    Observations
    Jun 2026: 0.07
    Updated: Jul 2, 2026 8:43 AM CDT
    Next Release Date: Aug 7, 2026
    """

    result = module.parse_fred_sahm_metadata(text)

    assert result == {
        "value": 0.07,
        "observation_period": "2026-06",
        "updated_at": "2026-07-02T08:43:00-05:00",
        "date": "2026-07-02",
        "next_release": {"time": "2026-08-07", "star": None, "consensus": None},
    }


def test_apply_sahm_metadata_uses_release_date_for_card():
    official = {
        "sahm": {
            "value": 0.07,
            "date": "2026-06-01",
            "frequency": "月频",
            "stale": True,
        }
    }
    metadata = {
        "value": 0.07,
        "observation_period": "2026-06",
        "updated_at": "2026-07-02T08:43:00-05:00",
        "date": "2026-07-02",
        "next_release": {"time": "2026-08-07", "star": None, "consensus": None},
    }

    module.apply_sahm_metadata(official, metadata)

    assert official["sahm"]["date"] == "2026-07-02"
    assert official["sahm"]["observation_period"] == "2026-06"
    assert official["sahm"]["updated_at"] == "2026-07-02T08:43:00-05:00"
    assert official["sahm"]["next_release"]["time"] == "2026-08-07"
    assert official["sahm"]["stale"] is False


def test_fred_sahm_metadata_reads_the_official_page():
    html = b"""
    <html><body>
      <div>Jun 2026: 0.07</div>
      <div>Updated: Jul 2, 2026 8:43 AM CDT</div>
      <div>Next Release Date: Aug 7, 2026</div>
    </body></html>
    """

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return html

    with patch.object(module.urllib.request, "urlopen", return_value=Response()):
        result = module.fred_sahm_metadata()

    assert result["date"] == "2026-07-02"
    assert result["next_release"]["time"] == "2026-08-07"
