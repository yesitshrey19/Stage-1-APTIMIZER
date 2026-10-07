"""
Tests for Aptimizer X+ Generative Design:
- Facade Concepts & Energy Metrics
- Landscape & Central Park Zones
- Automated Parking Layout Generator
"""

import pytest
from generative_design import generate_facade_options, generate_landscape_zones, generate_parking_layout


def test_facade_options_generation():
    project = {
        "towers": [{"floors": 16}]
    }
    facades = generate_facade_options(project)
    assert len(facades) == 5
    ids = [f["id"] for f in facades]
    assert "contemporary_glass" in ids
    assert "terracotta_louvre" in ids
    assert "brutalist_fluted" in ids
    assert "biophilic_green" in ids
    assert "neoclassical_stucco" in ids

    for f in facades:
        assert 0.20 <= f["window_to_wall_ratio"] <= 0.60
        assert f["shading_coefficient"] > 0
        assert f["u_value_w_m2k"] > 0
        assert "ai_prompt" in f


def test_landscape_zoning():
    landscape = generate_landscape_zones(4500.0)
    assert landscape["total_open_space_sqm"] == 4500.0
    assert landscape["softscape_pct"] >= 50.0  # High green ratio
    assert len(landscape["zones"]) == 5
    
    zone_keys = [z["key"] for z in landscape["zones"]]
    assert "central_lawn" in zone_keys
    assert "water_swale" in zone_keys
    assert "jogging_promenade" in zone_keys


def test_parking_layout_generation():
    # Orthogonal 90 degree
    ortho = generate_parking_layout(4000.0, "orthogonal")
    assert ortho["layout_type"] == "orthogonal"
    assert ortho["angle_deg"] == 90
    assert ortho["total_capacity_bays"] > 80
    assert ortho["accessible_bays"] >= 1
    assert ortho["ev_charging_bays"] >= 2
    assert ortho["aisle_driveway_width_m"] == 6.0

    # Herringbone 45 degree
    herringbone = generate_parking_layout(4000.0, "herringbone_45")
    assert herringbone["angle_deg"] == 45
    assert herringbone["aisle_driveway_width_m"] == 3.8
    assert herringbone["total_capacity_bays"] > ortho["total_capacity_bays"]
