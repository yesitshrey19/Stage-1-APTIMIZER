"""Validation and safeguard regressions for /analyse and project saves."""
def _api_client(api_project):
    # Shared authenticated HTTP client for validation route tests.
    client, _ = api_project
    return client


def _project_id(api_project):
    return api_project[1]


def _valid_project_payload():
    return {
        "plot": {
            "coordinates": [[12.9700, 77.5900], [12.9700, 77.5910], [12.9710, 77.5910], [12.9710, 77.5900]],
            "road_edges": [],
        },
        "config": {"wall_thickness_factor": 0.10, "common_area_loading": 0.25, "fsi_factor": 1.0},
        "towers": [{
            "id": "t1", "name": "Tower A", "floors": 4,
            "floor_height": 3.0, "footprint_area": 250.0, "common_area": 20.0,
            "corridor_width": 1.5, "corridor_length": 12.0,
            "units": [{"type": "2bhk", "count": 2, "carpet_area": 80.0, "balcony_area": 8.0}],
            "staircases": [{"count": 1, "width": 1.5}],
            "lifts": [{"count": 1, "capacity": 8}],
        }],
    }


def test_analyse_rejects_invalid_numeric_and_shapes_with_422(api_project):
    client = _api_client(api_project)
    invalid = _valid_project_payload()
    invalid["towers"][0]["corridor_width"] = -1
    invalid["towers"][0]["floors"] = 3.5
    invalid["rates"] = {"cement": "abc"}
    invalid["config"]["fsi_factor"] = "Infinity"
    invalid["plot"]["coordinates"] = [[12.9700, 77.5900], [12.9710, 77.5910], [12.9700, 77.5910], [12.9710, 77.5900]]

    res = client.post("/api/analyse", json={"project": invalid})
    assert res.status_code == 422
    detail = res.json()
    assert "issues" in detail


def test_analyse_rejects_wrong_container_shapes_with_422(api_project):
    client = _api_client(api_project)
    payload = _valid_project_payload()
    payload["towers"] = {"not": "a list"}

    res = client.post("/api/analyse", json={"project": payload})
    assert res.status_code == 422
    assert "issues" in res.json()


def test_put_rejects_invalid_updates_and_does_not_change_document(api_project, mongo):
    client = _api_client(api_project)
    pid = _project_id(api_project)
    before = mongo.projects.find_one({"_id": __import__("server").oid(pid)})

    bad = {
        "updates": {
            "towers": [{"id": "t1", "floors": 2.25, "corridor_width": -2, "units": []}],
            "plot": {"coordinates": [[0, 0], [1, 1], [0, 1], [1, 0]]},
        },
        "rev": before.get("rev"),
    }
    res = client.put(f"/api/projects/{pid}", json=bad)
    assert res.status_code == 422

    after = mongo.projects.find_one({"_id": __import__("server").oid(pid)})
    assert after.get("rev") == before.get("rev")
    assert after.get("updated_at") == before.get("updated_at")


def test_valid_draft_with_missing_data_remains_usable(api_project):
    client = _api_client(api_project)
    draft = {"plot": {"coordinates": []}, "towers": []}
    res = client.post("/api/analyse", json={"project": draft})
    assert res.status_code == 200
    data = res.json()
    assert "areas" in data and "calculation_basis" in data


def test_calculation_basis_preliminary_and_expected_warnings(api_project):
    client = _api_client(api_project)
    payload = _valid_project_payload()
    payload["plot"]["is_placeholder"] = True
    payload["dev_controls"] = {}
    payload["engineering"] = {}

    res = client.post("/api/analyse", json={"project": payload})
    assert res.status_code == 200
    basis = res.json()["calculation_basis"]
    warning_ids = {w["id"] for w in basis["warnings"]}
    assert basis["status"] == "preliminary"
    assert basis["certified"] is False
    assert {"placeholder-boundary", "default-controls", "soil-unverified"}.issubset(warning_ids)


def test_takeoff_exception_is_labeled_fallback(api_project, monkeypatch):
    client = _api_client(api_project)
    payload = _valid_project_payload()

    import engine

    def _boom(*_args, **_kwargs):
        raise RuntimeError("forced takeoff failure")

    monkeypatch.setattr(engine.takeofflib, "structural_takeoff", _boom)
    res = client.post("/api/analyse", json={"project": payload})
    assert res.status_code == 200
    basis = res.json()["calculation_basis"]
    fallback = next((w for w in basis["warnings"] if w["id"] == "takeoff-fallback"), None)
    assert fallback is not None
    assert "ratio-based estimates" in fallback["message"]
