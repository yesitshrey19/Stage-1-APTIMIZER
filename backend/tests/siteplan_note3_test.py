"""NBC 2016 Part 3 Table 4 Note 3: extra open space around blocks longer than 40 m."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from siteplan.config import SiteLayoutConfig  # noqa: E402
from siteplan.envelope import build_envelope  # noqa: E402
from siteplan.fitness import note3_extra  # noqa: E402
from siteplan.plan import plan  # noqa: E402

# A 220 m x 180 m plot in Bengaluru, large enough for the engine to place long bars.
LAT0, LNG0 = 12.95, 77.60
DLAT, DLNG = 180 / 110540, 220 / (111320 * 0.97437)
PLOT = [[LAT0, LNG0], [LAT0 + DLAT, LNG0], [LAT0 + DLAT, LNG0 + DLNG], [LAT0, LNG0 + DLNG]]


@pytest.mark.parametrize("length,height,expected", [
    (40.0, 30.0, 0.0),     # not longer than 40 m: no addition
    (72.0, 30.0, 3.2),     # 0.1 x 72 - 4
    (60.0, 9.0, 0.0),      # Table 4 applies above 10 m only
    (100.0, 150.0, 0.0),   # Table 4 already 20 m above 120 m: total capped at 20 m
    (80.0, 60.0, 3.0),     # 4.0 m, held to 20 - 17 (Table 4 at 60 m)
])
def test_note3_extra(length, height, expected):
    assert note3_extra(length, height) == pytest.approx(expected)


def test_layout_keeps_note3_margin_for_long_blocks():
    cfg = SiteLayoutConfig()
    res = plan(PLOT, None, cfg)
    env = build_envelope(PLOT, None, cfg).envelope
    assert res.towers, "the engine should place towers on a 4 ha plot"
    for t in res.towers:
        extra = note3_extra(max(t.width, t.depth), t.height_m)
        if extra > 0:
            assert env.exterior.distance(t.polygon) >= extra - 1e-6
