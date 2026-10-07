"""Human-readable limits travel with the numbers. No accuracy score is invented."""


def configured(mapping, key, default):
    value = (mapping or {}).get(key)
    return default if value is None or value == "" else value


def calculation_basis(project, quantities):
    plot = project.get("plot") or {}
    warnings = []
    if not plot.get("coordinates") and not (plot.get("length") and plot.get("width")):
        warnings.append({"id": "missing-boundary", "message": "No usable plot geometry is supplied; area-dependent checks cannot establish compliance."})
    if plot.get("is_placeholder"):
        warnings.append({"id": "placeholder-boundary", "message": "The plot boundary is a generated placeholder, not surveyed land."})
    if not (project.get("dev_controls") or {}).get("setbacks"):
        warnings.append({"id": "default-controls", "message": "Project setbacks are not configured. Confirm the governing local development controls."})
    if not (project.get("engineering") or {}).get("soil_type"):
        warnings.append({"id": "soil-unverified", "message": "Soil properties are not project-verified; foundation estimates require a geotechnical investigation."})
    if quantities.get("fallback_reason"):
        warnings.append({"id": "takeoff-fallback", "message": quantities["fallback_reason"]})
    return {
        "status": "preliminary", "certified": False,
        "area_method": "Local equirectangular projection" if plot.get("coordinates") else "Recorded length × width",
        "precision_policy": "Core areas and ratios retain binary floating-point precision through aggregation and checks. Screens format values for display; BOQ line amounts are rounded to INR 0.01, half-up.",
        "limitations": [
            "Mapped boundaries use an approximate local projection, not a survey-grade geodetic measurement.",
            "Stair cores use count × width² × 2.6; lift shafts use 4.5 m² each. Wall and common-area factors are estimating assumptions.",
            "Occupancy, material ratios, productivity, loads, soil references and rates may contain defaults. Verify them for this site.",
            "Supported code checks do not constitute statutory approval or a complete structural design. A qualified engineer must review before construction.",
        ],
        "warnings": warnings,
    }