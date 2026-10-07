"""Digital Twin & Smart Construction Platform.

Implements:
1. IoT Readiness: device registry for the project.
2. Smart Sensor Integration: concrete maturity (ASTM C1074), environment and structural telemetry.
3. 4D Progress Tracking: earned value (PV / EV / AC, SPI, CPI) against the project's own CPM programme.
4. Quality & Safety Monitoring: cube tests, defect log, permits and safety statistics.
5. Delay Prediction: Monte Carlo simulation over the programme's activity network.
6. Predictive Maintenance & Facility Management: asset register and maintenance calendar.

WHERE THE NUMBERS COME FROM
--------------------------
Everything that can be derived from the project is derived: planned value and the floor plan
of work come from the CPM programme, and the delay forecast samples that programme's own
durations. Everything that can only come from site -- sensor readings, progress, actual cost,
cube results, defects, assets -- is read from `project["site_data"]`. When a block has no site
records it returns clearly-marked sample data (`data_source: "sample"`) so the screen can be
demonstrated, and it never turns sample data into site advice: in particular no formwork
stripping clearance is ever issued without real readings and a mix calibration.

site_data shape (all optional):
    status_date: "YYYY-MM-DD"
    devices: [{id, type, location, model, battery_pct, status, telemetry_param}]
    maturity: {sensor_id, member, grade_mpa, span_m, blended_cement,
               readings: [{hours, temp_c}], calibration: [{maturity_c_h, strength_mpa}]}
    environment: {...}, structural: {...}
    progress: {activity_pct: {activity_id: 0-100}, actual_cost_inr: float}
    cube_tests: [...], defects: [...], safety: {...}, assets: [...], maintenance: [...]
"""
from __future__ import annotations

import bisect
import hashlib
import math
import random
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

SAMPLE_NOTE = ("Sample data: no site records or sensors are connected to this project. "
               "Illustrative only - not for site decisions.")

# Concrete maturity (ASTM C1074): Nurse-Saul datum and Arrhenius activation energy (Q = E/R)
# for ordinary Portland cement where no mix-specific values have been measured.
DATUM_TEMP_C = 0.0
ACTIVATION_Q_K = 5000.0
REFERENCE_TEMP_C = 20.0
STRIP_STRENGTH_FRACTION = 0.70

# Duration uncertainty applied to every activity in the delay simulation: a triangular
# multiplier on its planned duration. Right-skewed, because overruns outnumber early finishes.
DURATION_TRIANGLE = (0.90, 1.00, 1.30)
MC_ITERATIONS = 1000
MC_SEED = 20261007


def _site(project: Dict[str, Any]) -> Dict[str, Any]:
    return project.get("site_data") or {}


def _status_date(project: Dict[str, Any]) -> date:
    raw = _site(project).get("status_date")
    try:
        return date.fromisoformat(str(raw)) if raw else date.today()
    except ValueError:
        return date.today()


def _programme(project: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The same analysis and programme the Programme module shows."""
    import engine
    import schedule
    analysis = engine.analyse(project)
    plan = schedule.plan_schedule(project, analysis, project.get("schedule") or {})
    return analysis, plan


def _d(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- 1. IoT Readiness & Registry

SAMPLE_DEVICES = [
    {"id": "IOT-CONC-01", "type": "Concrete Maturity & Temp Sensor", "location": "Slab pour (example)",
     "model": "Embedded wireless maturity logger", "battery_pct": None, "status": "sample",
     "telemetry_param": "Core temperature (°C) & maturity (°C·h)"},
    {"id": "IOT-ENV-01", "type": "Site Weather & Dust Monitor", "location": "Site entrance (example)",
     "model": "PM2.5 / PM10 monitor with anemometer", "battery_pct": None, "status": "sample",
     "telemetry_param": "PM2.5 (µg/m³), wind speed (m/s), temperature, RH%"},
    {"id": "IOT-STR-01", "type": "Tiltmeter & Settlement Gauge", "location": "Foundation (example)",
     "model": "MEMS tiltmeter", "battery_pct": None, "status": "sample",
     "telemetry_param": "Tilt (arcsec), settlement (mm)"},
    {"id": "IOT-CRANE-01", "type": "Tower Crane Load & Wind Sensor", "location": "Tower crane (example)",
     "model": "Crane load moment indicator", "battery_pct": None, "status": "sample",
     "telemetry_param": "Hook load (t), slew angle, wind gust cut-off"},
    {"id": "IOT-NOISE-01", "type": "Noise Monitor", "location": "Site boundary (example)",
     "model": "Class 1 sound level meter", "battery_pct": None, "status": "sample",
     "telemetry_param": "Leq dB(A)"},
]


def iot_registry(project: Dict[str, Any]) -> Dict[str, Any]:
    """Device registry: the project's own devices, or a labelled sample layout."""
    devices = _site(project).get("devices") or []
    if devices:
        return {
            "ok": True, "data_source": "site", "note": "",
            "total_sensors_deployed": len(devices),
            "devices_online": sum(1 for d in devices if str(d.get("status", "")).lower() == "online"),
            "gateway_status": _site(project).get("gateway_status") or "Reported by site",
            "polling_interval_seconds": _site(project).get("polling_interval_seconds"),
            "devices": devices,
        }
    return {
        "ok": True, "data_source": "sample", "note": SAMPLE_NOTE,
        "total_sensors_deployed": 0, "devices_online": 0,
        "gateway_status": "No gateway connected",
        "polling_interval_seconds": None,
        "devices": [dict(d) for d in SAMPLE_DEVICES],
    }


# --------------------------------------------------------------------------- 2. Smart Sensor Telemetry

def maturity_from_readings(readings: List[Dict[str, Any]]) -> Dict[str, float]:
    """Temperature-time factor (Nurse-Saul, °C·h) and equivalent age at 20 °C (Arrhenius, h).

    `readings` are cumulative: hours since the pour and the concrete temperature then.
    Each interval uses the mean of its two end temperatures.
    """
    pts = sorted(((float(r["hours"]), float(r["temp_c"])) for r in readings
                  if r.get("hours") is not None and r.get("temp_c") is not None), key=lambda p: p[0])
    ttf, eq_age = 0.0, 0.0
    t_ref = REFERENCE_TEMP_C + 273.15
    for (h0, c0), (h1, c1) in zip(pts, pts[1:]):
        dt = max(h1 - h0, 0.0)
        tc = (c0 + c1) / 2.0
        ttf += max(tc - DATUM_TEMP_C, 0.0) * dt
        eq_age += math.exp(-ACTIVATION_Q_K * (1.0 / (tc + 273.15) - 1.0 / t_ref)) * dt
    return {"age_hours": pts[-1][0] if pts else 0.0, "ttf_c_h": ttf, "equivalent_age_h": eq_age,
            "last_temp_c": pts[-1][1] if pts else None}


def strength_from_calibration(maturity: float, calibration: List[Dict[str, Any]]) -> Optional[float]:
    """Strength from a mix-specific strength-maturity curve, interpolated in log(maturity).

    No extrapolation beyond the measured curve: below its first point the strength is
    unknown, and above its last point it is capped at the last measured strength.
    """
    pts = sorted(((float(c["maturity_c_h"]), float(c["strength_mpa"])) for c in calibration
                  if c.get("maturity_c_h") and c.get("strength_mpa") is not None))
    if len(pts) < 2 or maturity < pts[0][0]:
        return None
    if maturity >= pts[-1][0]:
        return pts[-1][1]
    xs = [p[0] for p in pts]
    i = bisect.bisect_right(xs, maturity)
    (m0, s0), (m1, s1) = pts[i - 1], pts[i]
    f = (math.log(maturity) - math.log(m0)) / (math.log(m1) - math.log(m0))
    return s0 + f * (s1 - s0)


def _span(project: Dict[str, Any], fallback: float = 5.0) -> float:
    e = project.get("engineering") or {}
    try:
        return max(float(e.get("grid_bay_x_m") or fallback), float(e.get("grid_bay_y_m") or fallback))
    except (TypeError, ValueError):
        return fallback


def sensor_telemetry(project: Dict[str, Any]) -> Dict[str, Any]:
    """Concrete maturity and site telemetry from site records; sample data otherwise."""
    import schedule
    site = _site(project)
    mat = site.get("maturity") or {}
    readings = mat.get("readings") or []
    span = float(mat.get("span_m") or _span(project))
    min_days, min_basis = schedule.prop_removal_days(span, bool(mat.get("blended_cement")))
    grade = float(mat.get("grade_mpa") or (project.get("engineering") or {}).get("concrete_grade") or 25)

    if len(readings) >= 2:
        m = maturity_from_readings(readings)
        strength = strength_from_calibration(m["ttf_c_h"], mat.get("calibration") or [])
        age_days = m["age_hours"] / 24.0
        time_ok = age_days >= min_days
        if strength is None:
            advisory = (f"KEEP PROPS: strength not estimated - the maturity method needs a mix-specific "
                        f"strength-maturity calibration (ASTM C1074). Follow the IS 456 Cl. 11.3 minimum "
                        f"of {min_days:g} days ({min_basis}); {age_days:.1f} days elapsed.")
            strip_ok = False
        else:
            strength_ok = strength >= STRIP_STRENGTH_FRACTION * grade
            strip_ok = strength_ok and time_ok
            advisory = (("PROPS MAY BE REMOVED: " if strip_ok else "KEEP PROPS: ")
                        + f"estimated {strength:.1f} MPa vs {STRIP_STRENGTH_FRACTION:.0%} of M{grade:g} "
                        + f"({STRIP_STRENGTH_FRACTION * grade:.1f} MPa); {age_days:.1f} of the "
                        + f"{min_days:g}-day IS 456 Cl. 11.3 minimum elapsed. Confirm with site cubes.")
        conc = {
            "sensor_id": mat.get("sensor_id") or "site",
            "member": mat.get("member"),
            "current_core_temp_celsius": m["last_temp_c"],
            "ambient_temp_celsius": mat.get("ambient_temp_c"),
            "curing_age_hours": round(m["age_hours"], 1),
            "equivalent_age_maturity_index": round(m["ttf_c_h"]),
            "equivalent_age_hours_at_20c": round(m["equivalent_age_h"], 1),
            "estimated_compressive_strength_mpa": round(strength, 1) if strength is not None else None,
            "target_strength_mpa": grade,
            "minimum_prop_removal_days": min_days,
            "strip_ok": strip_ok,
            "formwork_stripping_advisory": advisory,
            "data_source": "site",
        }
    else:
        conc = {
            "sensor_id": "IOT-CONC-01",
            "member": "Example slab",
            "current_core_temp_celsius": 34.2,
            "ambient_temp_celsius": 28.5,
            "curing_age_hours": 72,
            "equivalent_age_maturity_index": 2304,   # 72 h x 32 °C above a 0 °C datum
            "equivalent_age_hours_at_20c": None,
            "estimated_compressive_strength_mpa": None,
            "target_strength_mpa": grade,
            "minimum_prop_removal_days": min_days,
            "strip_ok": False,
            "formwork_stripping_advisory": (
                f"SAMPLE DATA - no maturity sensor connected, so no stripping clearance is given. "
                f"IS 456 Cl. 11.3 minimum for this project's {span:g} m span: {min_days:g} days ({min_basis})."),
            "data_source": "sample",
        }

    env = site.get("environment")
    struct = site.get("structural")
    return {
        "ok": True,
        "data_source": "site" if conc["data_source"] == "site" or env or struct else "sample",
        "note": "" if (conc["data_source"] == "site" and env and struct) else SAMPLE_NOTE,
        "timestamp": site.get("status_date") or "No live feed",
        "concrete_curing_maturity": conc,
        "environmental_telemetry": env or {
            "pm2_5_ug_m3": 42.0, "pm2_5_status": "Sample value", "pm10_ug_m3": 78.0,
            "ambient_noise_dba": 68.4, "noise_threshold_dba": 75.0,
            "wind_speed_m_s": 4.2, "wind_gust_cutoff_m_s": 14.0, "data_source": "sample",
        },
        "structural_health_telemetry": struct or {
            "foundation_tilt_arcsec": 1.2, "tilt_threshold_arcsec": 15.0,
            "settlement_mm": 2.1, "settlement_threshold_mm": 25.0,
            "status": "Sample values - no instrument connected", "data_source": "sample",
        },
    }


# --------------------------------------------------------------------------- 3. 4D Progress Tracking (EVM)

def _planned_fraction(start: Optional[date], finish: Optional[date], on: date) -> float:
    if not start or not finish:
        return 0.0
    if on < start:
        return 0.0
    if on >= finish:
        return 1.0
    return ((on - start).days + 1) / ((finish - start).days + 1)


def track_progress_4d(project: Dict[str, Any]) -> Dict[str, Any]:
    """Earned value against the project's own programme.

    PV is the programme's cost-loaded plan to the status date. EV needs recorded progress
    and AC needs recorded actual cost; without them SPI and CPI are not reported rather
    than invented.
    """
    try:
        _, plan = _programme(project)
    except Exception as exc:
        return {"ok": False, "error": f"Programme unavailable: {exc}"}
    acts = plan.get("activities") or []
    on = _status_date(project)
    progress = _site(project).get("progress") or {}
    pct_by_id = {str(k): max(0.0, min(float(v), 100.0)) for k, v in (progress.get("activity_pct") or {}).items()}
    actual_cost = progress.get("actual_cost_inr")

    bac = math.fsum(float(a.get("cost") or 0) for a in acts)
    pv = math.fsum(float(a.get("cost") or 0) * _planned_fraction(_d(a.get("start")), _d(a.get("finish")), on)
                   for a in acts)
    ev = (math.fsum(float(a.get("cost") or 0) * pct_by_id.get(str(a["id"]), 0.0) / 100.0 for a in acts)
          if pct_by_id else None)
    ac = float(actual_cost) if actual_cost is not None else None
    spi = round(ev / pv, 2) if ev is not None and pv > 0 else None
    cpi = round(ev / ac, 2) if ev is not None and ac else None

    if spi is None:
        status = "No progress recorded - showing the plan"
    elif spi >= 1.0:
        status = "On or ahead of schedule"
    elif spi >= 0.95:
        status = "Slightly behind schedule"
    else:
        status = "Behind schedule"

    # Floor by floor: planned window and status from the programme; recorded % if any.
    floors: Dict[int, List[Dict[str, Any]]] = {}
    for a in acts:
        f = a.get("floor")
        if isinstance(f, int) and f > 0:
            floors.setdefault(f, []).append(a)
    floor_status = []
    for f in sorted(floors):
        group = floors[f]
        starts = [d for d in (_d(a.get("start")) for a in group) if d]
        finishes = [d for d in (_d(a.get("finish")) for a in group) if d]
        cost = math.fsum(float(a.get("cost") or 0) for a in group) or 1.0
        if pct_by_id:
            pct = math.fsum(float(a.get("cost") or 0) * pct_by_id.get(str(a["id"]), 0.0) for a in group) / cost
            basis = "recorded"
        else:
            pct = 100.0 * math.fsum(float(a.get("cost") or 0) * _planned_fraction(_d(a.get("start")), _d(a.get("finish")), on)
                                    for a in group) / cost
            basis = "planned"
        state = "completed" if pct >= 99.5 else "in_progress" if pct > 0 else "planned"
        floor_status.append({
            "floor": f"Level {f}",
            "status": state,
            "completion_pct": round(pct, 1),
            "basis": basis,
            "planned_start": min(starts).isoformat() if starts else None,
            "planned_date": max(finishes).isoformat() if finishes else None,
            "actual_date": "Recorded" if basis == "recorded" else "Not recorded",
        })

    crit = [a for a in acts if a.get("critical")]
    upcoming = next((a for a in sorted(crit, key=lambda a: str(a.get("start"))) if _d(a.get("finish")) and _d(a.get("finish")) >= on), None)
    to_cr = lambda v: round(v / 1e7, 2) if v is not None else None
    completion = (ev / bac * 100.0) if ev is not None and bac else (pv / bac * 100.0 if bac else 0.0)
    return {
        "ok": True,
        "data_source": "site" if pct_by_id else "programme",
        "status_date": on.isoformat(),
        "project_completion_pct": round(completion, 1),
        "completion_basis": "recorded progress" if ev is not None else "planned to status date",
        "schedule_status": status,
        "earned_value_metrics": {
            "budget_at_completion_inr_cr": to_cr(bac),
            "planned_value_inr_cr": to_cr(pv),
            "earned_value_inr_cr": to_cr(ev),
            "actual_cost_inr_cr": to_cr(ac),
            "schedule_performance_index_spi": spi,
            "cost_performance_index_cpi": cpi,
            "cost_variance_inr_cr": to_cr(ev - ac) if ev is not None and ac is not None else None,
            "schedule_variance_inr_cr": to_cr(ev - pv) if ev is not None else None,
        },
        "floor_4d_breakdown": floor_status,
        "critical_path_status": (f"{len(crit)} activities on the critical path; next: {upcoming['name']} "
                                 f"({upcoming.get('start')} to {upcoming.get('finish')})." if upcoming else
                                 f"{len(crit)} activities on the critical path."),
        "programme_finish": plan.get("finish"),
    }


# --------------------------------------------------------------------------- 4. Quality & Safety Monitoring

SAMPLE_CUBES = [
    {"batch_id": "SAMPLE-01", "member": "Columns (example)", "grade": "M35", "test_7d_mpa": 27.8, "test_28d_mpa": 39.4, "target_mpa": 35.0, "status": "PASS"},
    {"batch_id": "SAMPLE-02", "member": "Slab (example)", "grade": "M30", "test_7d_mpa": 23.2, "test_28d_mpa": 34.6, "target_mpa": 30.0, "status": "PASS"},
    {"batch_id": "SAMPLE-03", "member": "Retaining wall (example)", "grade": "M30", "test_7d_mpa": 24.1, "test_28d_mpa": 35.2, "target_mpa": 30.0, "status": "PASS"},
    {"batch_id": "SAMPLE-04", "member": "Columns (example)", "grade": "M35", "test_7d_mpa": 26.5, "test_28d_mpa": "Awaiting 28D", "target_mpa": 35.0, "status": "IN PROGRESS"},
]


def quality_safety_audit(project: Dict[str, Any]) -> Dict[str, Any]:
    """Cube tests, defects and safety from site records; labelled sample data otherwise."""
    site = _site(project)
    cubes = site.get("cube_tests")
    defects = site.get("defects")
    safety = site.get("safety")
    from_site = bool(cubes or defects or safety)
    cubes = cubes if cubes is not None else ([dict(c) for c in SAMPLE_CUBES] if not from_site else [])
    decided = [c for c in cubes if str(c.get("status", "")).upper() in ("PASS", "FAIL")]
    passed = sum(1 for c in decided if str(c.get("status")).upper() == "PASS")
    score = round(passed / len(decided) * 100, 1) if decided else None
    return {
        "ok": True,
        "data_source": "site" if from_site else "sample",
        "note": "" if from_site else SAMPLE_NOTE,
        "quality_index_score": score,
        "quality_index_basis": "share of decided cube results that passed",
        "cube_tests": cubes,
        "defects_log": defects if defects is not None else ([] if from_site else [
            {"id": "SAMPLE-D1", "location": "Beam (example)", "issue": "Minor honeycombing at congested stirrups",
             "severity": "Low", "remediation": "Polymer-modified mortar repair", "status": "Resolved"},
        ]),
        "safety": safety if safety is not None else ({} if from_site else {
            "safe_man_hours_worked": None, "lost_time_injuries_lti": None, "near_misses_reported": None,
            "ppe_compliance_rate_pct": None, "active_work_permits": [],
            "safety_risk_index": "No safety records entered",
        }),
    }


# --------------------------------------------------------------------------- 5. Delay Prediction & Risk Simulation

def _network(acts: List[Dict[str, Any]]):
    index = {a["id"]: a for a in acts}
    preds = {}
    for i in index:
        preds[i] = [(p.get("id"), str(p.get("type") or "FS").upper(), float(p.get("lag_days") or 0))
                    for p in (index[i].get("predecessors") or []) if p.get("id") in index]
    # Topological order (Kahn), keeping the programme's listing order among equals.
    indeg = {i: len(preds[i]) for i in index}
    succs: Dict[str, List[str]] = {i: [] for i in index}
    for i in index:
        for p, _, _ in preds[i]:
            succs[p].append(i)
    ready = [i for i in index if indeg[i] == 0]
    ids: List[str] = []
    while ready:
        i = ready.pop(0)
        ids.append(i)
        for s in succs[i]:
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)
    if len(ids) != len(index):
        raise ValueError("The programme's logic contains a loop; it cannot be simulated.")
    return ids, index, preds


def _forward(ids, preds, dur) -> Tuple[Dict[str, float], Dict[str, Optional[str]]]:
    """Forward pass in working days; returns early finishes and each activity's driver."""
    es, ef, driver = {}, {}, {}
    for i in ids:
        start, drv = 0.0, None
        for p, kind, lag in preds[i]:
            if kind == "SS":
                cand = es[p] + lag
            elif kind == "FF":
                cand = ef[p] + lag - dur[i]
            else:
                cand = ef[p] + lag
            if cand > start:
                start, drv = cand, p
        es[i], ef[i], driver[i] = start, start + dur[i], drv
    return ef, driver


def predict_delays(project: Dict[str, Any], iterations: int = MC_ITERATIONS) -> Dict[str, Any]:
    """Monte Carlo over the programme: every activity's duration is sampled and the network
    re-run, so the forecast reflects this project's logic and critical path."""
    try:
        _, plan = _programme(project)
    except Exception as exc:
        return {"ok": False, "error": f"Programme unavailable: {exc}"}
    acts = [a for a in (plan.get("activities") or []) if a.get("id")]
    if not acts:
        return {"ok": False, "error": "The programme has no activities to simulate."}
    ids, index, preds = _network(acts)
    base_dur = {i: float(index[i].get("work_days") or 0) for i in ids}
    base_ef, _ = _forward(ids, preds, base_dur)
    base_finish_wd = max(base_ef.values()) or 1.0

    start = _d(plan.get("start")) or date.today()
    finish = _d(plan.get("finish")) or start
    cal_per_wd = max(((finish - start).days + 1) / base_finish_wd, 1.0)

    lo, mode, hi = (_site(project).get("duration_uncertainty") or {}).get("triangle") or DURATION_TRIANGLE
    rng = random.Random(MC_SEED)
    finishes, on_path = [], {i: 0 for i in ids}
    overrun = {i: 0.0 for i in ids}
    for _ in range(max(int(iterations), 50)):
        dur = {i: base_dur[i] * rng.triangular(lo, hi, mode) for i in ids}
        ef, driver = _forward(ids, preds, dur)
        last = max(ef, key=ef.get)
        finishes.append(ef[last])
        node = last
        while node is not None:
            on_path[node] += 1
            overrun[node] += dur[node] - base_dur[node]
            node = driver[node]
    n = len(finishes)
    finishes.sort()

    def date_at(wd: float) -> str:
        return (start + timedelta(days=round(wd * cal_per_wd) - 1)).isoformat()

    p50, p80, p90 = (finishes[min(int(q * n), n - 1)] for q in (0.5, 0.8, 0.9))
    on_time = sum(1 for f in finishes if f <= base_finish_wd + 1e-9) / n * 100.0
    # Ranked by the delay each activity actually put on the finish across all runs, which
    # weighs how often it drove completion by how far it overran when it did.
    ranked = sorted((i for i in ids if base_dur[i] > 0 and not index[i].get("milestone")),
                    key=lambda i: overrun[i], reverse=True)
    top = ranked[:3]
    risks = [{
        "factor": index[i].get("name"),
        "probability_pct": round(on_path[i] / n * 100.0, 1),
        "impact_days": round(overrun[i] / max(on_path[i], 1) * cal_per_wd, 1),
        "mitigation": "Drives completion in most runs - protect it with float, a standby crew or earlier procurement.",
    } for i in top]
    lead = top[0] if top else None
    status = "HEALTHY" if on_time >= 80 else "AT RISK" if on_time >= 50 else "LIKELY LATE"
    return {
        "ok": True,
        "data_source": "programme",
        "method": (f"{n} Monte Carlo runs over {len(ids)} programme activities; each duration x "
                   f"triangular({lo:g}, {mode:g}, {hi:g}); fixed seed so results repeat."),
        "baseline_completion_date": plan.get("finish"),
        "predicted_completion_date": date_at(p50),
        "p80_completion_date": date_at(p80),
        "p90_completion_date": date_at(p90),
        "variance_days": round((p50 - base_finish_wd) * cal_per_wd),
        "on_time_probability_pct": round(on_time, 1),
        "monte_carlo_iterations": n,
        "risk_factors_analyzed": risks,
        "critical_path_sensitivity": {
            "most_critical_activity": index[lead].get("name") if lead else None,
            "float_buffer_days": index[lead].get("total_float_days") if lead else None,
            "status": status,
        },
    }


# --------------------------------------------------------------------------- 6. Facility Management & Predictive Maintenance

SAMPLE_ASSETS = [
    {"tag": "PUMP-01", "name": "Domestic water booster pump (example)", "location": "Pump room",
     "operating_hours": 1420, "lifecycle_expectancy_hours": 25000, "health_score_pct": 96.5,
     "vibration_level_mm_s": 1.4, "next_service_due": None, "status": "Sample"},
    {"tag": "LIFT-01", "name": "Passenger lift (example)", "location": "Tower core",
     "operating_hours": 2840, "lifecycle_expectancy_hours": 50000, "health_score_pct": 94.0,
     "next_service_due": None, "status": "Sample"},
    {"tag": "STP-01", "name": "STP aeration blower (example)", "location": "STP enclosure",
     "operating_hours": 4210, "lifecycle_expectancy_hours": 30000, "health_score_pct": 89.0,
     "vibration_level_mm_s": 2.1, "next_service_due": None, "status": "Sample"},
    {"tag": "PV-01", "name": "Rooftop solar inverter (example)", "location": "Terrace",
     "operating_hours": 3100, "lifecycle_expectancy_hours": 40000, "health_score_pct": 98.0,
     "next_service_due": None, "status": "Sample"},
]


def facility_management(project: Dict[str, Any]) -> Dict[str, Any]:
    """Asset register and maintenance calendar from site records; labelled sample otherwise."""
    site = _site(project)
    assets = site.get("assets") or []
    from_site = bool(assets)
    if not from_site:
        assets = [dict(a) for a in SAMPLE_ASSETS]
    scores = [float(a["health_score_pct"]) for a in assets if a.get("health_score_pct") is not None]
    # A stable id: Python's hash() is salted per process, so it changed on every restart.
    key = str(project.get("_id") or project.get("id") or project.get("name") or "facility")
    return {
        "ok": True,
        "data_source": "site" if from_site else "sample",
        "note": "" if from_site else SAMPLE_NOTE,
        "facility_id": "FM-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:4].upper(),
        "assets_monitored": len(assets) if from_site else 0,
        "overall_facility_health_pct": round(sum(scores) / len(scores), 1) if scores else None,
        "assets": assets,
        "preventive_maintenance_calendar": site.get("maintenance") or ([] if from_site else [
            {"date": None, "task": "Quarterly lift inspection (example)", "technician": "Lift maintenance contractor"},
            {"date": None, "task": "Pump seal and impeller check (example)", "technician": "MEP maintenance team"},
        ]),
    }
