"""RBAC verification for V2 GIS endpoints + V1 regression spot checks."""
import os

import pytest
import requests

# Defaults to the local dev server. The previous default was a preview deployment
# that no longer exists and answers 404, so these modules skipped everywhere and
# gated nothing -- a dead default is worse than no default, because it looks live.
BASE = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api"

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


# The deployment's own admin, not a literal. A wrong password here does not fail as a
# wrong password: it fails as a 429 lockout five attempts later, in a different test.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@aptimizer.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@123")


def _login(email, pwd):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _register_if_missing(email, name, role, pwd):
    requests.post(f"{API}/auth/register", json={
        "name": name, "email": email, "password": pwd, "role": role
    }, timeout=30)


def test_rbac_viewer_cannot_run_gis_but_can_read():
    _register_if_missing("viewer@aptimizer.com", "Viewer", "viewer", "Viewer@123")
    admin = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    ah = {"Authorization": f"Bearer {admin}"}

    # create a project + share to viewer
    p = requests.post(f"{API}/projects", json={"name": "TEST_RBAC_V2", "client": "QA",
                                               "location": "Bengaluru"}, headers=ah, timeout=30).json()
    pid = p["id"]
    try:
        requests.post(f"{API}/projects/{pid}/shares",
                      json={"email": "viewer@aptimizer.com", "role": "viewer"},
                      headers=ah, timeout=30)
        viewer = _login("viewer@aptimizer.com", "Viewer@123")
        vh = {"Authorization": f"Bearer {viewer}"}

        # GET /gis allowed
        r_get = requests.get(f"{API}/projects/{pid}/gis", headers=vh, timeout=30)
        assert r_get.status_code == 200, r_get.text

        # POST /gis/analyse -> 403 (viewer role blocked)
        r_post = requests.post(f"{API}/projects/{pid}/gis/analyse", json={"radius_m": 300},
                               headers=vh, timeout=30)
        assert r_post.status_code == 403, f"expected 403, got {r_post.status_code}: {r_post.text}"

        # POST /gis/ai-summary -> 403 (viewer)
        r_ai = requests.post(f"{API}/projects/{pid}/gis/ai-summary", headers=vh, timeout=30)
        assert r_ai.status_code == 403, f"expected 403, got {r_ai.status_code}: {r_ai.text}"
    finally:
        requests.delete(f"{API}/projects/{pid}", headers=ah, timeout=30)


def test_v1_regression_analysis_and_reports():
    """V1 spot check: /analysis still returns expected values and PDF+xlsx still download."""
    admin = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    ah = {"Authorization": f"Bearer {admin}"}

    projects = requests.get(f"{API}/projects", headers=ah, timeout=30).json()
    assert projects, "no projects available"
    pid = projects[0]["id"]

    an = requests.get(f"{API}/projects/{pid}/analysis", headers=ah, timeout=30)
    assert an.status_code == 200, an.text
    body = an.json()
    assert "areas" in body and "compliance" in body and "cost" in body

    # BOQ xlsx download
    x = requests.get(f"{API}/projects/{pid}/boq.xlsx", headers=ah, timeout=60)
    assert x.status_code == 200
    assert len(x.content) > 500
    assert x.headers.get("content-type", "").startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml"
    )

    # Executive PDF download
    pdf = requests.get(f"{API}/projects/{pid}/reports/executive", headers=ah, timeout=60)
    assert pdf.status_code == 200
    assert len(pdf.content) > 500
    assert pdf.content[:4] == b"%PDF"
