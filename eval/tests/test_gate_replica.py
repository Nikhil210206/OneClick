import json
from pathlib import Path

import pytest

import gate_replica
from evalkit import sets
from evalkit.client import ApiClient

FIXTURES = Path(__file__).parent / "fixtures"
VARIATIONS = [
    "phone display shows nothing",
    "why is my screen empty",
    "blank screen samsung",
    "the display went totally dark",
    "screen stays white with no text",
    "screen blank help please",
    "nothing is showing on the display at all",
    "ugh my screen is blank again",
    "scren blnk no txt",
]


def _results_file(tmp_path, n_rows, response):
    kit = sets.load_kit()[:n_rows]
    lines = [{"query": r.query, "query_variations": VARIATIONS, "response": response} for r in kit]
    path = tmp_path / "results.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


def test_offline_full_coverage_passes_gates(tmp_path):
    good = json.loads((FIXTURES / "compliant_response.json").read_text())
    out = tmp_path / "gates.json"
    code = gate_replica.main(
        ["--results", str(_results_file(tmp_path, 20, good)), "--out", str(out), "--enforce", "G3,G4,G5"]
    )
    report = json.loads(out.read_text())
    assert code == 0
    assert report["gates"]["G3"]["pass"] and report["gates"]["G4"]["value"] == 1.0
    assert report["blocks"]["A1"]["points"] == 15 and report["blocks"]["A5"]["points"] == 5
    assert report["blocks"]["A2"]["detail"]["catalog_validity"] == 1.0


def test_offline_low_coverage_fails_enforced_g3(tmp_path):
    out = tmp_path / "gates.json"
    code = gate_replica.main(
        [
            "--results",
            str(_results_file(tmp_path, 10, {"contexts": []})),
            "--out",
            str(out),
            "--enforce",
            "G3",
        ]
    )
    assert code == 1
    assert json.loads(out.read_text())["gates"]["G3"]["value"] == 0.5


def test_enforcing_an_unmeasured_gate_fails(tmp_path):
    path = _results_file(tmp_path, 20, {"contexts": []})
    assert (
        gate_replica.main(["--results", str(path), "--out", str(tmp_path / "g.json"), "--enforce", "G2"]) == 1
    )


def test_live_against_skeleton_api(tmp_path, monkeypatch):
    """The real api/app skeleton, in-process: health ok, every body schema-valid, no leaks."""
    pytest.importorskip("fastapi")
    from evalkit.paths import use_api_package

    use_api_package()
    from app.main import app
    from fastapi.testclient import TestClient

    monkeypatch.setattr(gate_replica, "load_set", lambda name: [])
    monkeypatch.setattr(
        gate_replica, "ApiClient", lambda url, timeout: ApiClient(url, http=TestClient(app, base_url=url))
    )
    out = tmp_path / "gates.json"
    code = gate_replica.main(["--api", "http://testserver", "--out", str(out), "--enforce", "G2,G4,G5"])
    report = json.loads(out.read_text())
    assert code == 0
    assert report["hygiene"]["always_200"]["pass"] and report["hygiene"]["pure_json"]["pass"]
    assert report["latency"]["repeat"]["n"] == 20
    assert any("empty contexts" in n for n in report["notes"])
