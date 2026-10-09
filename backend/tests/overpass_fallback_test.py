"""Overpass: a working mirror is raced, and an older cached copy is used when every mirror fails."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gis  # noqa: E402

COORDS = [[12.97, 77.59], [12.97, 77.591], [12.971, 77.591], [12.971, 77.59]]


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(gis, "_CACHE_DIR", str(tmp_path))
    return tmp_path


def _all_down(query):
    raise RuntimeError("all Overpass endpoints failed")


def test_french_mirror_is_raced():
    assert "https://overpass.openstreetmap.fr/api/interpreter" in gis.OVERPASS_ENDPOINTS


def test_expired_cache_used_when_every_mirror_fails(tmp_cache, monkeypatch):
    query = gis._overpass_query(gis.bbox(COORDS, 500), 500)
    gis._cache_write(query, {"elements": [{"type": "way", "id": 1, "tags": {"highway": "residential"},
                                           "geometry": [{"lat": 12.97, "lon": 77.59},
                                                        {"lat": 12.971, "lon": 77.591}]}]})
    old = time.time() - gis._CACHE_TTL_S - 3600
    os.utime(gis._cache_path(query), (old, old))
    assert gis._cache_read(query) is None                    # expired for normal reads
    monkeypatch.setattr(gis, "_race_mirrors", _all_down)
    features, status = gis.fetch_overpass(COORDS, 500)
    assert status["ok"] is True and "older copy" in status["endpoint"]
    assert features["roads"]


def test_no_cache_and_every_mirror_down_reports_failure(tmp_cache, monkeypatch):
    monkeypatch.setattr(gis, "_race_mirrors", _all_down)
    features, status = gis.fetch_overpass(COORDS, 500)
    assert status["ok"] is False and features["roads"] == []
