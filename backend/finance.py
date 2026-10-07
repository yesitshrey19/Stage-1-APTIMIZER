"""Development finance: whether the project makes money, and when the money arrives.

Everything here rests on figures the engine has already computed -- saleable area from
`engine.area_metrics`, construction cost from the BOQ. Finance adds only the three things
the engine has no view of: what the flats sell for, what the money costs to borrow, and
when the cash actually moves.

Timing is the whole point. A project can show a healthy margin and still be a bad
investment, because construction is paid for years before the sales come in. Profit is
arithmetic; IRR and payback are the numbers that answer whether the money was worth
committing. So the cash flow is modelled month by month rather than as a single total,
and every headline figure is derived from that series.
"""
from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Tuple
from residential_defaults import storeys_of

SQFT_PER_SQM = 10.7639

# Construction spend is not linear. Site work and foundations are slow and cheap, the
# structural frame is fast and expensive, finishes tail off. The classic S-curve splits
# roughly 15/70/15 across the three thirds of the programme; spreading cost evenly
# instead flatters the early cash position and understates the peak borrowing.
S_CURVE_THIRDS = (0.15, 0.70, 0.15)

# Bisection bounds for the monthly IRR search. -90%/+100% a month is far wider than any
# real development, and a project outside it is telling you something the number cannot.
IRR_LO, IRR_HI = -0.9, 1.0
IRR_STEPS = 200


@dataclass
class FinanceConfig:
    """What the engine cannot know: prices, money and timing."""
    # Sale rate in INR per square foot of saleable (super built-up) area. Indian practice
    # quotes per sqft, so that is what this takes, even though the engine works in m2.
    sale_rate_per_sqft: float = 6500.0
    # Per-unit-type overrides, e.g. {"3bhk": 7200}. A type not named here uses the rate above.
    sale_rate_by_type: Dict[str, float] = field(default_factory=dict)
    other_income: float = 0.0           # covered parking, club membership, transfer fees
    land_cost: float = 0.0
    approval_cost: float = 0.0          # sanction fees, betterment charges, consultants
    marketing_pct: float = 3.0          # of gross revenue
    contingency_pct: float = 5.0        # of construction cost
    # Money
    debt_ratio: float = 60.0            # share of project cost funded by borrowing
    interest_rate_pct: float = 11.0     # annual, on the drawn balance
    discount_rate_pct: float = 12.0     # for NPV; the return the money could earn elsewhere
    # Timing, in months
    construction_months: int = 30
    sales_start_month: int = 6          # launch, usually well before completion
    sales_months: int = 30
    presale_pct: float = 10.0           # share of units sold at launch

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "FinanceConfig":
        cfg = cls()
        for k, v in (data or {}).items():
            if not hasattr(cfg, k) or v is None:
                continue
            cur = getattr(cfg, k)
            try:
                setattr(cfg, k, dict(v) if isinstance(cur, dict) else type(cur)(v))
            except (TypeError, ValueError):
                continue
        cfg.construction_months = max(1, int(cfg.construction_months))
        cfg.sales_months = max(1, int(cfg.sales_months))
        cfg.sales_start_month = max(0, int(cfg.sales_start_month))
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def saleable_areas(project: Dict[str, Any], areas: Dict[str, Any]) -> Dict[str, Any]:
    """Saleable area per unit type, in sqft.

    Each type's share of a tower's super built-up area is its share of that tower's
    carpet-plus-balcony. Allocating rather than recomputing keeps this tied to the exact
    super built-up figure the rest of the app reports -- if the two ever disagreed, the
    revenue would not match the area the client was sold.
    """
    cfg = project.get("config") or {}
    by_type: Dict[str, Dict[str, float]] = {}
    towers = {t.get("id"): t for t in (project.get("towers") or [])}

    for tm in areas.get("towers") or []:
        src = towers.get(tm.get("id")) or {}
        floors = int(tm.get("floors") or 0)
        units = src.get("units") or []
        weights = [(u, (float(u.get("carpet_area") or 0) + float(u.get("balcony_area") or 0))
                    * int(u.get("count") or 0) * storeys_of(u, floors)) for u in units]
        total_w = math.fsum(w for _, w in weights)
        if total_w <= 0:
            continue
        tower_saleable = float(tm.get("super_builtup_sqm") or 0)
        for u, w in weights:
            if w <= 0:
                continue
            key = str(u.get("type") or "custom").lower()
            count = int(u.get("count") or 0) * storeys_of(u, floors)
            sqm = tower_saleable * (w / total_w)
            row = by_type.setdefault(key, {"units": 0, "saleable_sqm": 0.0})
            row["units"] += count
            row["saleable_sqm"] += sqm

    out = []
    for key, row in sorted(by_type.items()):
        sqft = row["saleable_sqm"] * SQFT_PER_SQM
        out.append({
            "type": key,
            "units": int(row["units"]),
            "saleable_sqm": round(row["saleable_sqm"], 2),
            "saleable_sqft": round(sqft, 2),
            "saleable_sqft_raw": sqft,
            "sqft_per_unit": round(sqft / row["units"], 2) if row["units"] else 0.0,
        })
    return {
        "by_type": out,
        "total_sqft": round(math.fsum(r["saleable_sqft_raw"] for r in out), 2),
        "total_units": sum(r["units"] for r in out),
    }


def _s_curve(total: float, months: int) -> List[float]:
    """Spread an amount over `months` on the 15/70/15 construction S-curve."""
    if months <= 0:
        return []
    edges = [months // 3, 2 * months // 3]
    spans = [max(edges[0], 1), max(edges[1] - edges[0], 1), max(months - edges[1], 1)]
    series: List[float] = []
    for share, span in zip(S_CURVE_THIRDS, spans):
        series += [total * share / span] * span
    # Rounding the spans can overshoot or undershoot the month count; trim or pad the
    # tail so the series always sums to `total` over exactly `months`.
    series = series[:months] + [0.0] * max(0, months - len(series))
    drift = total - sum(series)
    if series:
        series[-1] += drift
    return series


def _npv(rate: float, flows: List[float]) -> float:
    return sum(f / ((1 + rate) ** i) for i, f in enumerate(flows))


def _irr(flows: List[float]) -> float | None:
    """Monthly IRR by bisection, or None when the cash flow has no sign change.

    Bisection rather than Newton: it cannot diverge, and a development cash flow is
    well-behaved enough (one sign change) that the bracket is guaranteed to hold.
    """
    if not flows or min(flows) >= 0 or max(flows) <= 0:
        return None
    lo, hi = IRR_LO, IRR_HI
    f_lo = _npv(lo, flows)
    if f_lo * _npv(hi, flows) > 0:
        return None
    for _ in range(IRR_STEPS):
        mid = (lo + hi) / 2
        if f_lo * _npv(mid, flows) <= 0:
            hi = mid
        else:
            lo, f_lo = mid, _npv(mid, flows)
    return (lo + hi) / 2


def cash_flow(cfg: FinanceConfig, construction_cost: float, revenue: float,
              upfront: float) -> Tuple[List[Dict[str, Any]], List[float]]:
    """Month-by-month money in and out, plus the net series the IRR is taken on."""
    horizon = max(cfg.construction_months, cfg.sales_start_month + cfg.sales_months) + 1
    out = _s_curve(construction_cost, cfg.construction_months) + [0.0] * horizon
    presale = revenue * (cfg.presale_pct / 100.0)
    rest = revenue - presale

    rows: List[Dict[str, Any]] = []
    net: List[float] = []
    cumulative = 0.0
    for m in range(horizon):
        spend = out[m] if m < len(out) else 0.0
        if m == 0:
            spend += upfront          # land and approvals are paid before anything moves
        income = 0.0
        if m == cfg.sales_start_month:
            income += presale
        if cfg.sales_start_month <= m < cfg.sales_start_month + cfg.sales_months:
            income += rest / cfg.sales_months
        n = income - spend
        cumulative += n
        net.append(n)
        rows.append({"month": m, "outflow": round(spend, 2), "inflow": round(income, 2),
                     "net": round(n, 2), "cumulative": round(cumulative, 2)})
    return rows, net


IRR_MEANINGFUL_MAX_PCT = 100.0


def analyse(project: Dict[str, Any], analysis: Dict[str, Any],
            config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Full financial picture for a project the engine has already analysed."""
    cfg = FinanceConfig.from_dict(config)
    # The build period drives the cost S-curve and the interest. Unless the user set one,
    # it is the programme's own duration -- a fixed 30 months disagreed with the schedule
    # the app had just computed for the same project.
    months_source = "configured"
    if "construction_months" not in (config or {}):
        months_source = "default"
        try:
            import schedule as schedulelib
            programme = schedulelib.plan_schedule(project, analysis, summary=True)
            months = float(programme.get("duration_months") or 0)
            if programme.get("ok", True) and months > 0:
                cfg.construction_months = max(1, math.ceil(months))
                months_source = "programme"
        except Exception:
            pass
    areas = analysis["areas"]
    sale = saleable_areas(project, areas)

    # Revenue, priced per unit type so a mix change moves the top line.
    revenue = 0.0
    for row in sale["by_type"]:
        raw_rate = cfg.sale_rate_by_type.get(row["type"], cfg.sale_rate_per_sqft)
        rate = float(raw_rate if raw_rate is not None else 0.0)
        row["rate_per_sqft"] = rate
        rev_raw = row.get("saleable_sqft_raw", row["saleable_sqft"]) * rate
        row["revenue"] = round(rev_raw, 2)
        revenue += rev_raw
    gross_revenue = revenue + cfg.other_income

    construction = float(analysis["cost"]["total"] or 0)
    contingency = construction * (cfg.contingency_pct / 100.0)
    marketing = gross_revenue * (cfg.marketing_pct / 100.0)
    upfront = cfg.land_cost + cfg.approval_cost

    # Interest is charged on the average drawn balance over the build, which is roughly
    # half the debt for a smoothly drawn facility -- not the full amount for the full term.
    debt = (construction + contingency + upfront) * (cfg.debt_ratio / 100.0)
    years = cfg.construction_months / 12.0
    finance_cost = debt * (cfg.interest_rate_pct / 100.0) * years * 0.5

    total_cost = construction + contingency + upfront + marketing + finance_cost
    gross_profit = gross_revenue - construction - contingency - upfront
    net_profit = gross_revenue - total_cost

    rows, net = cash_flow(cfg, construction + contingency, gross_revenue, upfront + marketing)
    monthly_irr = _irr(net)
    irr_pct = ((1 + monthly_irr) ** 12 - 1) * 100 if monthly_irr is not None else None

    payback = next((r["month"] for r in rows if r["cumulative"] >= 0), None)
    peak = min((r["cumulative"] for r in rows), default=0.0)

    units = sale["total_units"] or 0
    revenue_per_unit = (gross_revenue / units) if units else 0.0

    warnings = []
    if cfg.land_cost <= 0:
        warnings.append({"severity": "warning", "field": "land_cost",
                         "text": "Land cost is not entered, so profit, ROI, NPV and IRR leave out the "
                                 "largest cost of most Indian projects and will be overstated."})
    if cfg.approval_cost <= 0:
        warnings.append({"severity": "info", "field": "approval_cost",
                         "text": "Approval and sanction costs are not entered."})
    if irr_pct is not None and irr_pct > IRR_MEANINGFUL_MAX_PCT:
        warnings.append({"severity": "warning", "field": "irr_pct",
                         "text": f"IRR of {irr_pct:,.0f}% is not a meaningful return. It happens when "
                                 "little money goes in before sales cash comes back (typically land "
                                 "left at zero); judge the scheme on margin and NPV instead."})
    return {
        "ok": True,
        "warnings": warnings,
        "config": cfg.to_dict(),
        "saleable": sale,
        "revenue": {
            "gross": round(gross_revenue, 2),
            "from_sales": round(revenue, 2),
            "other_income": round(cfg.other_income, 2),
            "per_unit": round(revenue_per_unit, 2),
            "by_type": sale["by_type"],
        },
        "cost": {
            "construction": round(construction, 2),
            "contingency": round(contingency, 2),
            "land": round(cfg.land_cost, 2),
            "approvals": round(cfg.approval_cost, 2),
            "marketing": round(marketing, 2),
            "finance": round(finance_cost, 2),
            "total": round(total_cost, 2),
            "per_saleable_sqft": round(total_cost / sale["total_sqft"], 2) if sale["total_sqft"] else 0.0,
        },
        "profit": {
            "gross": round(gross_profit, 2),
            "net": round(net_profit, 2),
            "margin_pct": round(net_profit / gross_revenue * 100, 2) if gross_revenue else 0.0,
            "roi_pct": round(net_profit / total_cost * 100, 2) if total_cost else 0.0,
            "irr_pct": round(irr_pct, 2) if irr_pct is not None else None,
            "npv": round(_npv((cfg.discount_rate_pct / 100.0) / 12, net), 2),
        },
        "break_even": {
            "units": round(total_cost / revenue_per_unit, 1) if revenue_per_unit else None,
            "units_available": units,
            "sale_rate_per_sqft": round(total_cost / sale["total_sqft"], 2) if sale["total_sqft"] else None,
            "pct_of_stock": round(total_cost / gross_revenue * 100, 1) if gross_revenue else None,
        },
        "timing": {
            "payback_month": payback,
            "peak_funding_need": round(abs(peak), 2),
            "construction_months": cfg.construction_months,
            "construction_months_source": months_source,
        },
        "cash_flow": rows,
        "currency": analysis["cost"].get("currency", "INR"),
    }
