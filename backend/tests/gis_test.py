"""GIS & site-intelligence endpoint tests (V2). Run against the live preview URL:
    pytest /app/backend/tests/gis_test.py -v
"""
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Defaults to the local dev server. The previous default was a preview deployment
# that no longer exists and answers 404, so these modules skipped everywhere and
# gated nothing -- a dead default is worse than no default, because it looks live.
BASE = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api"
# Read from the environment the server itself reads, so these follow the deployment
# instead of a literal that stops matching the moment ADMIN_PASSWORD is changed --
# which does not fail as a wrong password, it fails as a 429 lockout five attempts
# later, and that is a much harder thing to read off a test report.
ADMIN = {"email": os.environ.get("ADMIN_EMAIL", "admin@aptimizer.com"),
         "password": os.environ.get("ADMIN_PASSWORD", "Admin@123")}

# This module drives a DEPLOYED backend over HTTP, not the in-process TestClient the rest
# of the suite uses. Without a reachable deployment every test in it fails on a 404 from
# whatever host the default URL points at — which for a long time made a clean run look
# like thirteen broken features, and meant the suite could not gate anything. Point
# REACT_APP_BACKEND_URL at a running server to run these; otherwise they skip, because a
# test that cannot reach its subject has not found a defect.
pytestmark = pytest.mark.e2e

try:
    _probe = requests.get(f"{API}/", timeout=5)
    # The API root answers 200 when a real backend is behind the URL. A 404 here is a
    # proxy or a parked domain replying for a deployment that is gone — reachable in the
    # TCP sense and useless in every other, which is exactly the case this guards.
    _reachable = _probe.status_code == 200
except Exception as _exc:                                    # noqa: BLE001
    _reachable = False
    _why = _exc
else:
    _why = f"HTTP {_probe.status_code}"
if not _reachable:
    pytest.skip(f"No backend at {BASE} ({_why}) — set REACT_APP_BACKEND_URL to run these",
                allow_module_level=True)


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json=ADMIN, timeout=30)
    assert r.status_code in (200, 503), r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def project(headers):
    r = requests.post(f"{API}/projects", json={"name": "TEST_GIS_V2", "client": "QA", "location": "Bengaluru",
                                               "plot_reference": "GIS-1"}, headers=headers, timeout=30)
    assert r.status_code in (200, 503), r.text
    pid = r.json()["id"]
    yield pid
    requests.delete(f"{API}/projects/{pid}", headers=headers, timeout=30)


@pytest.fixture(scope="module")
def gis(project, headers):
    r = requests.post(f"{API}/projects/{project}/gis/analyse", json={"radius_m": 400},
                      headers=headers, timeout=180)
    assert r.status_code in (200, 503), r.text
    return r.json()["gis"]


def test_gis_empty_before_analysis(headers):
    r = requests.post(f"{API}/projects", json={"name": "TEST_GIS_EMPTY"}, headers=headers, timeout=30)
    pid = r.json()["id"]
    try:
        g = requests.get(f"{API}/projects/{pid}/gis", headers=headers, timeout=30).json()
        assert g["gis"] is None and g["has_polygon"] is True and g["stale"] is False
        ai = requests.post(f"{API}/projects/{pid}/gis/ai-summary", headers=headers, timeout=60)
        assert ai.status_code == 400
    finally:
        requests.delete(f"{API}/projects/{pid}", headers=headers, timeout=30)


def test_gis_requires_polygon(headers):
    r = requests.post(f"{API}/projects", json={"name": "TEST_GIS_NOPOLY"}, headers=headers, timeout=30)
    pid = r.json()["id"]
    try:
        requests.put(f"{API}/projects/{pid}", json={"updates": {"plot": {"coordinates": [], "orientation_deg": 0}}},
                     headers=headers, timeout=30)
        bad = requests.post(f"{API}/projects/{pid}/gis/analyse", json={"radius_m": 300}, headers=headers, timeout=60)
        assert bad.status_code == 400
        assert "polygon" in bad.json()["detail"].lower()
    finally:
        requests.delete(f"{API}/projects/{pid}", headers=headers, timeout=30)


def test_features_detected(gis):
    for key in ("buildings", "roads", "green", "water", "transit"):
        assert key in gis["features"]
    assert gis["feature_counts"]["roads"] > 0, "expected roads near the default Bengaluru plot"
    road = gis["features"]["roads"][0]
    assert {"id", "kind", "geometry", "distance_m"} <= set(road)
    assert road["road_width_m"] > 0


def test_terrain_and_slope(gis):
    t = gis["terrain"]
    assert t["available"] is True
    assert t["min_m"] <= t["mean_m"] <= t["max_m"]
    assert len(t["profile"]) == 11
    assert t["avg_slope_pct"] >= 0
    assert t["slope_class"] in ("flat", "gentle", "moderate", "steep")


def test_flood_rule_based(gis):
    f = gis["flood"]
    assert f["level"] in ("low", "moderate", "high")
    assert 0 <= f["score"] <= 100
    assert isinstance(f["reasons"], list) and f["reasons"]


def test_wind_and_sun(gis):
    w = gis["wind"]
    assert w["region"] and w["prevailing"] and len(w["rose"]) == 8
    s = gis["sun"]
    assert len(s["paths"]) == 3
    for p in s["paths"]:
        assert p["points"], f"no sun points for {p['key']}"
        assert 0 < p["peak_elevation"] <= 90
        assert p["daylight_hours"] > 6
    assert len(s["facades"]) == 4
    assert s["orientation_deg"] == 0


def test_accessibility_and_scores(gis):
    a = gis["accessibility"]
    assert 0 <= a["score"] <= 100
    assert a["nearest_road_m"] is not None
    s = gis["suitability"]
    assert 0 <= s["score"] <= 100
    assert s["grade"] in ("excellent", "good", "fair", "poor")
    # Six factors since seismic (IS 1893) and IS 875-3 wind design joined the score.
    assert len(s["breakdown"]) == 6
    assert sum(b["weight_pct"] for b in s["breakdown"]) == 100
    b = gis["buildability"]
    assert isinstance(b["buildable"], bool)
    assert b["flags"]


def test_persisted_and_staleness(project, headers, gis):
    stored = requests.get(f"{API}/projects/{project}/gis", headers=headers, timeout=30).json()
    assert stored["gis"]["polygon_signature"] == gis["polygon_signature"]
    assert stored["stale"] is False

    proj = requests.get(f"{API}/projects/{project}", headers=headers, timeout=30).json()
    plot = proj["plot"]
    # Move the whole boundary ~45 m north-east. (Appending a stray vertex used to be the
    # move, but that makes the boundary cross itself, which the API now rejects with 422 --
    # and the test never looked at the response, so it read as a staleness bug.)
    plot["coordinates"] = [[lat + 0.0004, lng + 0.0004] for lat, lng in plot["coordinates"]]
    r = requests.put(f"{API}/projects/{project}", json={"updates": {"plot": plot}}, headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    after = requests.get(f"{API}/projects/{project}/gis", headers=headers, timeout=30).json()
    assert after["stale"] is True, "GIS must be flagged stale after the plot polygon changes"


def test_activity_logged(project, headers, gis):
    acts = requests.get(f"{API}/projects/{project}/activity", headers=headers, timeout=30).json()
    assert any(a["action"] == "gis.analysed" for a in acts)


def test_ai_summary(project, headers, gis):
    r = requests.post(f"{API}/projects/{project}/gis/ai-summary", headers=headers, timeout=180)
    assert r.status_code in (200, 503), r.text
    data = r.json()
    # Whatever the deployment configured, including the fallback chain and the app's
    # built-in default: provider fallback is a feature, so the answer may legitimately
    # come from an alternate. Pinning one vendor's model here tied the test to one
    # machine's .env and failed on every other.
    import ai as ailib
    _configured = [os.environ.get("GEMINI_MODEL", "")] + [
        m.strip() for m in (os.environ.get("GEMINI_FALLBACK_MODELS") or "").split(",")] + \
        [ailib.DEFAULT_GEMINI_MODEL] + list(ailib.GEMINI_FALLBACK_MODELS) + \
        [os.environ.get("GROQ_MODEL", ""), ailib.DEFAULT_GROQ_MODEL] + list(ailib.GROQ_FALLBACK_MODELS)
    assert data["model"] in [m for m in _configured if m], data["model"]
    assert len(data["text"]) > 200
    stored = requests.get(f"{API}/projects/{project}/gis", headers=headers, timeout=30).json()
    assert stored["gis"]["ai_summary"]["text"] == data["text"]


def test_viewer_cannot_run_gis(project, headers):
    requests.post(f"{API}/auth/register", json={"name": "Viewer", "email": "viewer@aptimizer.com",
                                                "password": "Viewer@123", "role": "viewer"}, timeout=30)
    v = requests.post(f"{API}/auth/login", json={"email": "viewer@aptimizer.com", "password": "Viewer@123"},
                      timeout=30).json()
    vh = {"Authorization": f"Bearer {v['access_token']}"}
    requests.post(f"{API}/projects/{project}/shares", json={"email": "viewer@aptimizer.com", "role": "viewer"},
                  headers=headers, timeout=30)
    r = requests.post(f"{API}/projects/{project}/gis/analyse", json={"radius_m": 300}, headers=vh, timeout=60)
    assert r.status_code == 403
    assert requests.get(f"{API}/projects/{project}/gis", headers=vh, timeout=30).status_code == 200
