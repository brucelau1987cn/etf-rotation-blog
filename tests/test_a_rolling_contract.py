import json

def test_json_valid():
    with open("public/data/a-rolling-signals.json") as f:
        data = json.load(f)
    assert data["schema_version"] == "a-rolling-energy-v4"
