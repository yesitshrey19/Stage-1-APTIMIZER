"""Development finance tests. The timing cases are the reason this file exists --
profit is arithmetic anyone can check, but IRR and payback are where a plausible-looking
model quietly lies."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, finance as F
from defaults import default_project


@pytest.fixture(scope="module")
def base():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    a = engine.analyse(p)
    return p, a, F.analyse(p, a, {"sale_rate_per_sqft": 6500, "land_cost": 150_000_000})


def test_saleable_area_is_the_towers_only_never_the_clubhouse(base):
    """Revenue is priced on the homes' super built-up. The clubhouse is recovered through
    the common-area loading, so it is never priced again as a separate area."""
    _, a, r = base
    towers_only = sum(t["super_builtup_sqm"] for t in a["areas"]["towers"])
    assert r["saleable"]["total_sqft"] == pytest.approx(towers_only * F.SQFT_PER_SQM, rel=1e-6)
    assert a["areas"]["society_amenities_sqm"] > 0
    assert towers_only == pytest.approx(a["areas"]["super_builtup_area_sqm"], rel=1e-9)
    assert r["saleable"]["total_units"] == a["areas"]["total_units"]


def test_revenue_is_the_sum_of_its_unit_types(base):
    _, _, r = base
    assert sum(x["revenue"] for x in r["revenue"]["by_type"]) == pytest.approx(
        r["revenue"]["from_sales"], rel=1e-6)


def test_a_per_type_rate_overrides_the_default(base):
    p, a, r = base
    hi = F.analyse(p, a, {"sale_rate_per_sqft": 6500, "sale_rate_by_type": {"1bhk": 9000}})
    assert hi["revenue"]["from_sales"] > r["revenue"]["from_sales"]


def test_construction_spend_follows_an_s_curve_and_still_sums_whole():
    """Rounding the thirds must never lose or invent rupees."""
    for months in (1, 2, 5, 12, 30, 31):
        series = F._s_curve(1_000_000.0, months)
        assert len(series) == months
        assert sum(series) == pytest.approx(1_000_000.0)
    mid = F._s_curve(100.0, 30)
    assert sum(mid[10:20]) > sum(mid[:10])      # the frame costs more than the foundation


def test_break_even_rate_is_below_the_asking_rate_when_the_project_profits(base):
    _, _, r = base
    assert r["profit"]["net"] > 0
    assert r["break_even"]["sale_rate_per_sqft"] < r["config"]["sale_rate_per_sqft"]
    assert 0 < r["break_even"]["pct_of_stock"] < 100


def test_a_loss_making_scheme_reports_a_loss_not_a_nonsense_return(base):
    """At a sale rate under cost the model must go negative, not wrap around."""
    p, a, _ = base
    bad = F.analyse(p, a, {"sale_rate_per_sqft": 1500, "land_cost": 150_000_000})
    assert bad["profit"]["net"] < 0
    assert bad["profit"]["margin_pct"] < 0
    assert bad["timing"]["payback_month"] is None      # it never pays back
    assert bad["break_even"]["pct_of_stock"] > 100     # more stock than exists


def test_irr_is_none_rather_than_fabricated_when_cash_never_turns(base):
    """With no revenue at all there is no rate that discounts the flows to zero, and the
    honest answer is no answer. A dire-but-real project still gets a number."""
    p, a, _ = base
    none_at_all = F.analyse(p, a, {"sale_rate_per_sqft": 0})
    assert none_at_all["profit"]["irr_pct"] is None
    dire = F.analyse(p, a, {"sale_rate_per_sqft": 100})
    assert dire["profit"]["irr_pct"] < -50


def test_cash_flow_is_continuous_and_its_cumulative_is_the_running_sum(base):
    _, _, r = base
    rows = r["cash_flow"]
    assert [x["month"] for x in rows] == list(range(len(rows)))
    running = 0.0
    for x in rows:
        running += x["net"]
        # Cumulative is carried unrounded and rounded once for display, so summing the
        # rounded column drifts by half a paisa a row. Tolerance is per-rupee, not exact.
        assert x["cumulative"] == pytest.approx(running, abs=1.0)


def test_peak_funding_is_the_deepest_the_hole_ever_gets(base):
    """The promoter has to fund this much before sales catch up -- the number that
    decides whether the project can actually be built."""
    _, _, r = base
    assert r["timing"]["peak_funding_need"] == pytest.approx(
        abs(min(x["cumulative"] for x in r["cash_flow"])), abs=0.05)
    assert r["timing"]["peak_funding_need"] > 0


def test_later_sales_hurt_the_return_without_touching_the_profit(base):
    """The margin is timing-blind; IRR is not. That difference is the point of the module."""
    p, a, r = base
    slow = F.analyse(p, a, {"sale_rate_per_sqft": 6500, "land_cost": 150_000_000,
                            "sales_start_month": 24, "sales_months": 36})
    assert slow["profit"]["net"] == pytest.approx(r["profit"]["net"], rel=1e-6)
    assert slow["profit"]["irr_pct"] < r["profit"]["irr_pct"]
    assert slow["timing"]["payback_month"] > r["timing"]["payback_month"]


def test_config_round_trips_and_ignores_junk(base):
    p, a, _ = base
    r = F.analyse(p, a, {"sale_rate_per_sqft": "7000", "nonsense": 1, "land_cost": None})
    assert r["config"]["sale_rate_per_sqft"] == 7000.0
    assert "nonsense" not in r["config"]
    assert r["config"]["land_cost"] == 0.0
