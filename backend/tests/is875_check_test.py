"""IS 875 (Part 3):2015 + Amd 2 code check (9 Oct): wind speeds, cyclone belt, pd floor, k3 flag."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gis  # noqa: E402
import iscodes as C  # noqa: E402

# Annex A as substituted by Amendment No. 2 (2020), for the cities in the app's table.
ANNEX_A = {"Bengaluru": 33, "Mysuru": 33, "Pune": 39, "Hyderabad": 44, "Mumbai": 44, "Delhi": 50,
           "Chennai": 50, "Guwahati": 50, "Surat": 44, "Lucknow": 50, "Bhopal": 47, "Raipur": 44,
           "Vadodara": 39, "Kolkata": 50, "Jaipur": 47, "Chandigarh": 50, "Ludhiana": 50, "Amritsar": 50}


@pytest.mark.parametrize("city,vb", ANNEX_A.items())
def test_vb_matches_annex_a(city, vb):
    assert C.CITIES[city][2] == vb


@pytest.mark.parametrize("city", ["Gurugram", "Noida", "Faridabad", "Meerut", "Jalandhar", "Haridwar"])
def test_cities_beside_50_ms_annex_cities_take_50(city):
    assert C.CITIES[city][2] == 50


def test_guntur_and_vadodara_in_cyclone_belt():
    assert C.wind_kd("Guntur") == 1.0
    assert C.wind_kd("Vadodara") == 1.0
    assert C.wind_kd("Rajkot") == 0.90
    assert C.wind_kd("Hyderabad") == 0.90


def test_pd_never_below_70_percent_of_pz():
    assert C.WIND_PD_MIN_FRACTION == 0.70
    assert C.WIND_KA * C.WIND_KC * min(C.wind_kd("Delhi"), 1.0) >= C.WIND_PD_MIN_FRACTION


def test_k3_flag_on_slopes_over_3_degrees():
    ref = C.city_reference("Bengaluru")
    steep = gis.wind_profile(12.97, 77.59, ref, 30, None, {"available": True, "avg_slope_pct": 8.0})
    flat = gis.wind_profile(12.97, 77.59, ref, 30, None, {"available": True, "avg_slope_pct": 2.0})
    assert steep["is875_design"]["k3_check_required"] is True
    assert "Annex C" in steep["is875_design"]["note"]
    assert flat["is875_design"]["k3_check_required"] is False
