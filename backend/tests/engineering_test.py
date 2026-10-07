"""IS/NBC engineering layer tests (V2). Run against the live preview URL:
    pytest /app/backend/tests/engineering_test.py -v
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

MODULE_IDS = ["loads", "seismic", "foundation", "mix", "water", "storm",
              "parking_nbc", "fire", "accessibility", "green", "grid", "carbon", "trees"]

# Every module cites the clause behind each number it reports; that is the whole point of
# the engineering layer, and this list is the one exemption.
#
# `trees` is governed by municipal building bye-laws and the National Forest Policy 1988.
# Neither is a BIS or NBC designation, so the clause registry cannot carry one and a
# reference there would have to be invented -- which is the failure the citation guard
# exists to prevent, not a gap to paper over.
#
# Named explicitly rather than inferred from a module's `codes`, so that a NEW module
# arriving with no clauses fails this test instead of quietly qualifying for the exemption.
CLAUSE_EXEMPT = {"trees"}


@pytest.fixture(scope="module")
def headers():
    r = requests.post(f"{API}/auth/login", json=ADMIN, timeout=30)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def project(headers):
    r = requests.post(f"{API}/projects", json={"name": "TEST_ENG_V2", "client": "QA",
                                               "location": "Bengaluru", "plot_reference": "ENG-1"},
                      headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    yield pid
    requests.delete(f"{API}/projects/{pid}", headers=headers, timeout=30)


@pytest.fixture(scope="module")
def doc(project, headers):
    return requests.get(f"{API}/projects/{project}", headers=headers, timeout=30).json()


def analyse(headers, proj):
    r = requests.post(f"{API}/engineering/analyse", json={"project": proj}, headers=headers, timeout=60)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def eng(doc, headers):
    return analyse(headers, doc)


# ---------------------------------------------------------------- structure
def test_requires_auth(doc):
    r = requests.post(f"{API}/engineering/analyse", json={"project": doc}, timeout=30)
    assert r.status_code in (401, 403)


def test_all_modules_present(eng):
    assert sorted(eng["modules"].keys()) == sorted(MODULE_IDS)
    for mid in MODULE_IDS:
        m = eng["modules"][mid]
        assert m["title"] and m["codes"]
        assert m.get("outputs") or m.get("checks"), f"{mid} has neither outputs nor checks"


def test_every_output_has_clause_reference(eng):
    for mid, m in eng["modules"].items():
        outputs = m.get("outputs", []) + m.get("checks", [])
        if mid in CLAUSE_EXEMPT:
            # All-or-nothing, deliberately: a module that is exempt because no standard
            # governs it must be uniformly clause-less. A partial set means a reference was
            # dropped from a module that used to carry them, which is a regression.
            assert all(o["clause"] is None for o in outputs), (
                f"{mid} is clause-exempt, so every output must be; a mixed set means one "
                f"was lost rather than never present")
            continue
        for o in outputs:
            assert o["clause"], f"{mid} → {o['label']} has no clause"
            assert o["clause"]["code"] and o["clause"]["clause"]


def test_project_engineering_endpoint(project, headers):
    r = requests.get(f"{API}/projects/{project}/engineering", headers=headers, timeout=60)
    assert r.status_code == 200, r.text
    assert sorted(r.json()["modules"].keys()) == sorted(MODULE_IDS)


def test_summary_keys(eng):
    s = eng["summary"]
    for k in ["seismic_zone", "base_shear_kn", "column_size", "foundation", "mix_ratio",
              "water_demand_lpd", "stp_kld", "rwh_annual_l", "fire_score", "parking_score",
              "accessibility_score", "green_rating"]:
        assert k in s and s[k] not in (None, "")


# ---------------------------------------------------------------- city / state / override
def test_city_reference_from_city(doc, headers):
    p = {**doc, "engineering": {**(doc.get("engineering") or {}), "city": "Bengaluru", "state": "Karnataka"}}
    c = analyse(headers, p)["city_reference"]
    assert c["source"] == "city" and c["zone"] == "II" and c["wind_speed"] == 33


def test_city_reference_state_fallback(doc, headers):
    p = {**doc, "engineering": {**(doc.get("engineering") or {}), "city": "Nowhere Town", "state": "Gujarat"}}
    c = analyse(headers, p)["city_reference"]
    assert c["source"] == "state" and c["zone"]


def test_city_reference_national_default(doc, headers):
    p = {**doc, "engineering": {**(doc.get("engineering") or {}), "city": "Nowhere", "state": "Nowhere"}}
    c = analyse(headers, p)["city_reference"]
    assert c["source"] == "default" and c["wind_speed"] > 0


def test_high_seismic_city_raises_base_shear(doc, headers):
    low = analyse(headers, {**doc, "engineering": {"city": "Chennai", "state": "Tamil Nadu"}})
    high = analyse(headers, {**doc, "engineering": {"city": "Guwahati", "state": "Assam"}})
    assert high["modules"]["seismic"]["zone"] == "V"
    assert high["summary"]["base_shear_kn"] > low["summary"]["base_shear_kn"]


def test_rain_intensity_override(doc, headers):
    p = {**doc, "engineering": {"city": "Bengaluru", "rain_intensity_override": 120}}
    storm = analyse(headers, p)["modules"]["storm"]
    vals = {o["label"]: o["value"] for o in storm["outputs"]}
    assert any("120" in str(v) for v in vals.values())


def test_cities_endpoint():
    r = requests.get(f"{API}/cities", timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert d["count"] >= 50
    assert d["soils"] and d["exposures"] and d["structural_systems"] and d["green_checklist"]
    q = requests.get(f"{API}/cities?q=pune", timeout=30).json()
    assert q["count"] >= 1 and q["cities"][0]["state"]


def test_engineering_config_persists(project, headers):
    r = requests.put(f"{API}/projects/{project}",
                     json={"updates": {"engineering": {"city": "Jaipur", "state": "Rajasthan",
                                                       "soil_type": "soft clay", "concrete_grade": 30}}},
                     headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    assert r.json()["engineering"]["city"] == "Jaipur"
    e = requests.get(f"{API}/projects/{project}/engineering", headers=headers, timeout=60).json()
    assert e["config"]["city"] == "Jaipur" and e["config"]["concrete_grade"] == 30
    assert e["city_reference"]["city"] == "Jaipur"
    # restore so later fixtures/report tests use the original city
    requests.put(f"{API}/projects/{project}", json={"updates": {"engineering": {}}}, headers=headers, timeout=30)


# ---------------------------------------------------------------- graceful degradation
def test_missing_inputs_reported_not_crashed(headers):
    r = requests.post(f"{API}/projects", json={"name": "TEST_ENG_EMPTY"}, headers=headers, timeout=30)
    pid = r.json()["id"]
    try:
        p = requests.get(f"{API}/projects/{pid}", headers=headers, timeout=30).json()
        p["towers"] = []
        p["plot"] = {**(p.get("plot") or {}), "length": 0, "width": 0}
        d = analyse(headers, p)
        assert isinstance(d["missing_inputs"], list) and d["missing_inputs"]
        assert sorted(d["modules"].keys()) == sorted(MODULE_IDS)
    finally:
        requests.delete(f"{API}/projects/{pid}", headers=headers, timeout=30)


# ---------------------------------------------------------------- module interactions
def test_unknown_soil_falls_back_to_default(doc, headers):
    d = analyse(headers, {**doc, "engineering": {"soil_type": "soft_clay"}})
    assert d["config"]["soil_type"] == "soft clay"
    d2 = analyse(headers, {**doc, "engineering": {"soil_type": "martian regolith"}})
    assert d2["config"]["soil_type"] == "dense sand"


def test_loads_feed_foundation(doc, headers):
    d = analyse(headers, doc)
    col = d["modules"]["loads"]["derived"]["column_load"]
    fnd = {o["label"]: o["value"] for o in d["modules"]["foundation"]["outputs"]}
    assert abs(fnd["Column service load"] - round(col / 1.5, 1)) < 0.5
    assert fnd["Required footing area"] > 0


def test_poor_soil_switches_to_deep_foundation(doc, headers):
    d = analyse(headers, {**doc, "engineering": {"soil_type": "soft clay"}})
    f = d["modules"]["foundation"]
    assert "aft" in f["recommendation"]["value"] or "ile" in f["recommendation"]["value"]
    assert any(w["severity"] == "critical" for w in f["warnings"]) or f["warnings"]


def test_mix_design_links_to_boq(eng):
    link = eng["modules"]["mix"]["boq_link"]
    assert link["volume_cum"] > 0 and len(link["rows"]) == 4
    cement = next(r for r in link["rows"] if r["material"] == "Cement")
    assert cement["total"] == pytest.approx(cement["per_cum"] * link["volume_cum"], rel=0.01)
    assert cement["bags"] == pytest.approx(cement["total"] / 50.0, rel=0.01)


def test_severe_exposure_raises_cement(doc, headers):
    mod = analyse(headers, {**doc, "engineering": {"exposure_condition": "moderate"}})["modules"]["mix"]
    sev = analyse(headers, {**doc, "engineering": {"exposure_condition": "very severe"}})["modules"]["mix"]
    g = lambda m: {o["label"]: o["value"] for o in m["outputs"]}["Cement content"]
    assert g(sev) >= g(mod)


def test_water_and_storm_auto_credit_green(eng):
    items = {i["id"]: i for i in eng["modules"]["green"]["items"]}
    auto = [i for i in items.values() if i.get("auto_credited")]
    assert auto, "no green credit auto-awarded from water/storm modules"
    assert eng["modules"]["green"]["categories"]


def test_green_checklist_increases_rating(doc, headers):
    all_on = {i["id"]: True for i in analyse(headers, doc)["modules"]["green"]["items"]}
    high = analyse(headers, {**doc, "engineering": {"green_checklist": all_on}})["modules"]["green"]
    base = analyse(headers, {**doc, "engineering": {"green_checklist": {}}})["modules"]["green"]
    score = lambda m: {o["label"]: o["value"] for o in m["outputs"]}["Score"]
    assert score(high) == 100.0 and score(high) > score(base)


def test_fire_checks_and_score(eng):
    f = eng["modules"]["fire"]
    assert f["checks"] and all(c["status"] in ("pass", "fail") for c in f["checks"])
    assert 0 <= f["score"] <= 100 and f["total"] == len(f["checks"])


def test_accessibility_checks_and_score(eng):
    a = eng["modules"]["accessibility"]
    assert a["checks"] and 0 <= a["score"] <= 100


def test_narrow_door_fails_accessibility(doc, headers):
    a = analyse(headers, {**doc, "engineering": {"door_width_mm": 600, "pedestrian_ramp_slope": 6}})["modules"]["accessibility"]
    assert any(c["status"] == "fail" for c in a["checks"])


def test_parking_nbc_score(eng):
    p = eng["modules"]["parking_nbc"]
    assert p["checks"] and 0 <= p["score"] <= 100


def test_grid_optimizer_options(eng):
    g = eng["modules"]["grid"]
    assert g["options"] and all("columns" in o for o in g["options"])


# ---------------------------------------------------------------- code library
@pytest.mark.parametrize("q,expect", [
    ("seismic", "IS 1893"), ("wind", "IS 875"), ("parking", "Part 8"),
    ("fire", "Part 4"), ("accessibility", "Part 3"), ("concrete mix", "IS 10262"),
    ("water supply", "IS 1172"), ("rainwater", "IS 3764"), ("foundation", "IS 6403"),
])
def test_code_library_search(q, expect):
    r = requests.get(f"{API}/iscodes", params={"q": q}, timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert d["count"] > 0, f"'{q}' returned nothing"
    assert any(expect in e["code"] for e in d["entries"]), f"'{q}' → {[e['code'] for e in d['entries']]}"


def test_every_clause_deeplink_resolves(eng):
    ids = {e["id"] for e in requests.get(f"{API}/iscodes", timeout=30).json()["entries"]}
    seen = set()
    for mid, m in eng["modules"].items():
        refs = [o["clause"] for o in m.get("outputs", []) + m.get("checks", [])]
        refs += [m["recommendation"]["clause"]] if m.get("recommendation") else []
        for c in refs:
            if c is None:
                # Only where no standard governs the module. Anywhere else this is the
                # missing citation the previous test already failed on, and walking into
                # it here would report an AttributeError instead of the real finding.
                assert mid in CLAUSE_EXEMPT, f"{mid} has an output with no clause"
                continue
            assert c.get("library_id"), f"{c['code']} has no library deep-link"
            assert c["library_id"] in ids
            seen.add(c["library_id"])
    for lid in seen:
        d = requests.get(f"{API}/iscodes", params={"id": lid}, timeout=30).json()
        assert d["count"] == 1 and d["entries"][0]["id"] == lid


def test_code_library_full_list():
    d = requests.get(f"{API}/iscodes", timeout=30).json()
    assert d["count"] >= 12
    codes = " ".join(e["code"] for e in d["entries"])
    for c in ["IS 456", "IS 875", "IS 1893", "IS 1172", "IS 10262", "IS 3861", "Part 3", "Part 4"]:
        assert c in codes, f"{c} missing from library"
    for e in d["entries"]:
        assert e["clause"] and e["topic"] and e["key_value"]


# ---------------------------------------------------------------- reports
@pytest.mark.parametrize("rtype", ["structural", "water", "fire", "accessibility", "engineering", "executive"])
def test_engineering_reports(project, headers, rtype):
    r = requests.get(f"{API}/projects/{project}/reports/{rtype}", headers=headers, timeout=90)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF" and len(r.content) > 2000
