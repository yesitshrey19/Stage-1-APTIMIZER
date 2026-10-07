"""Input guards, not design certification. Missing fields remain draft inputs."""
import math
from shapely.geometry import Polygon


class ProjectInputError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("; ".join(f"{i['field']}: {i['message']}" for i in issues))


def validate_project(project):
    issues = []

    def error(path, message):
        issues.append({"field": path, "message": message})

    def finite_tree(value, path):
        if isinstance(value, float) and not math.isfinite(value):
            error(path, "Must be a finite number")
        elif isinstance(value, dict):
            for k, v in value.items():
                finite_tree(v, f"{path}.{k}" if path else k)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                finite_tree(v, f"{path}[{i}]")

    def number(obj, key, path, minimum=0, maximum=None, integer=False):
        if key not in obj or obj[key] is None:
            return
        v = obj[key]
        name = f"{path}.{key}"
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            error(name, "Must be a finite number")
        elif v < minimum or (maximum is not None and v > maximum):
            error(name, f"Must be between {minimum} and {maximum}" if maximum is not None else f"Must be at least {minimum}")
        elif integer and v != int(v):
            error(name, "Must be a whole number")

    def objects(value, path):
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            error(path, "Must be a list of objects")
            return []
        return value

    finite_tree(project, "")
    for key in ("plot", "config", "parking", "residential_policy", "engineering", "utility_config", "rates", "labour_rates", "equipment_rates", "quantity_ratios", "cost_adders", "wastage_pct", "dev_controls"):
        if key in project and not isinstance(project[key], dict):
            error(key, "Must be an object")
    policy = project.get("residential_policy") or {}
    if isinstance(policy, dict):
        tiers = {"1bhk", "2bhk", "3bhk", "4bhk", "5bhk"}
        cycle = policy.get("unit_type_cycle")
        if cycle is not None and (not isinstance(cycle, list) or not cycle or any(t not in tiers for t in cycle)):
            error("residential_policy.unit_type_cycle", "Must contain one or more supported BHK tiers")
        unit_density = policy.get("units_per_floor") or {}
        if not isinstance(unit_density, dict):
            error("residential_policy.units_per_floor", "Must be an object")
        else:
            for tier, value in unit_density.items():
                if tier not in tiers:
                    error(f"residential_policy.units_per_floor.{tier}", "Unsupported BHK tier")
                else:
                    number({tier: value}, tier, "residential_policy.units_per_floor", minimum=1, maximum=1000, integer=True)
        parking_policy = policy.get("parking_by_type") or {}
        if not isinstance(parking_policy, dict):
            error("residential_policy.parking_by_type", "Must be an object")
        else:
            for tier, rules in parking_policy.items():
                if tier not in tiers or not isinstance(rules, dict):
                    error(f"residential_policy.parking_by_type.{tier}", "Must be a supported BHK parking rule")
                    continue
                for key in ("reserved_cars_per_unit", "reserved_bikes_per_unit", "optional_car_spaces_per_unit"):
                    number(rules, key, f"residential_policy.parking_by_type.{tier}", maximum=100)
    if issues:
        raise ProjectInputError(issues)
    plot = project.get("plot") or {}
    for key in ("length", "width"):
        number(plot, key, "plot")
    coords = plot.get("coordinates") or []
    if coords:
        valid = isinstance(coords, list) and len(coords) >= 3 and all(
            isinstance(c, (list, tuple)) and len(c) == 2 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in c
            ) and -90 <= c[0] <= 90 and -180 <= c[1] <= 180 for c in coords)
        if not valid:
            error("plot.coordinates", "Supply at least three valid [latitude, longitude] points")
        elif not Polygon(coords).is_valid or Polygon(coords).area <= 0:
            error("plot.coordinates", "Boundary must enclose an area without crossing itself")
    for key in ("wall_thickness_factor", "common_area_loading", "fsi_factor"):
        number(project.get("config") or {}, key, "config")
    towers_raw = project["towers"] if "towers" in project and project["towers"] is not None else []
    for i, tower in enumerate(objects(towers_raw, "towers")):
        path = f"towers[{i}]"
        number(tower, "floors", path, maximum=1000, integer=True)
        for key in ("floor_height", "footprint_area", "common_area", "corridor_width", "corridor_length", "max_travel_distance"):
            number(tower, key, path)
        for name in ("units", "staircases", "lifts"):
            sub_raw = tower[name] if name in tower and tower[name] is not None else []
            for j, item in enumerate(objects(sub_raw, f"{path}.{name}")):
                p = f"{path}.{name}[{j}]"
                number(item, "count", p, maximum=100000, integer=True)
                for key in ("carpet_area", "balcony_area", "width", "capacity"):
                    number(item, key, p)
    for group in ("rates", "labour_rates", "equipment_rates", "quantity_ratios", "cost_adders", "wastage_pct"):
        grp = project.get(group) or {}
        if isinstance(grp, dict):
            for key in grp:
                number(grp, key, group)
    amenities_raw = project["society_amenities"] if "society_amenities" in project and project["society_amenities"] is not None else []
    for i, amenity in enumerate(objects(amenities_raw, "society_amenities")):
        number(amenity, "area", f"society_amenities[{i}]")
    rules_raw = project["compliance_rules"] if "compliance_rules" in project and project["compliance_rules"] is not None else []
    for i, rule in enumerate(objects(rules_raw, "compliance_rules")):
        if "threshold" in rule:
            number(rule, "threshold", f"compliance_rules[{i}]", minimum=-1e12)
    parking = project.get("parking") or {}
    number(parking, "basement_levels", "parking", integer=True)
    for key in ("basement_area_per_level", "ground_area", "ratio_per_unit", "area_per_slot"):
        number(parking, key, "parking")
    for key in ("visitor_pct", "ev_pct", "accessible_pct"):
        number(parking, key, "parking", maximum=100)
    if issues:
        raise ProjectInputError(issues)
    return project
