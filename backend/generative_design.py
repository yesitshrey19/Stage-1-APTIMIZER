"""
Generative Design Engine

Implements autonomous generative architectural capabilities:
1. Facade Design Synthesis: Procedural architectural facade styles with thermal & solar metrics.
2. Landscape & Central Park Generator: Zoning of communal open spaces with permeability analysis.
3. Automated Parking Layout Generator: Procedural bay grids with driveways, turning radii, and ECS analysis.
"""

import math
from typing import Any, Dict, List, Optional


FACADE_STYLES = [
    {
        "id": "contemporary_glass",
        "name": "Contemporary Glazed Ribbon",
        "concept": "Sleek continuous horizontal glazing ribbons with dark aluminium mullions and frameless laminated glass balustrades.",
        "window_to_wall_ratio": 0.45,
        "shading_coefficient": 0.38,
        "u_value_w_m2k": 1.8,
        "embodied_carbon_rating": "Moderate",
        "cost_premium_pct": 12.5,
        "ai_prompt": "Ultra-modern luxury residential skyscraper facade, horizontal bands of floor-to-ceiling double-glazed low-E glass, slim dark bronze architectural mullions, frameless glass balconies with warm ambient soffit illumination, crisp architectural photography, bright daylight."
    },
    {
        "id": "terracotta_louvre",
        "name": "Terracotta & Solar Louvre",
        "concept": "Warm natural terracotta louvres and baguettes offering dynamic passive solar shading with textured clay cladding.",
        "window_to_wall_ratio": 0.35,
        "shading_coefficient": 0.28,
        "u_value_w_m2k": 1.4,
        "embodied_carbon_rating": "Low Carbon (Eco)",
        "cost_premium_pct": 8.0,
        "ai_prompt": "High-end contemporary apartment facade with warm terracotta ceramic louvres, vertical timber-toned solar baffle fins, deep recessed private balconies with lush planters, subtle earth tones, refined architectural texture."
    },
    {
        "id": "brutalist_fluted",
        "name": "Sculptural Fluted Concrete",
        "concept": "Textured exposed fair-faced fluted concrete fins, bold cantilevered box frames, and deeply articulated window embrasures.",
        "window_to_wall_ratio": 0.30,
        "shading_coefficient": 0.24,
        "u_value_w_m2k": 1.3,
        "embodied_carbon_rating": "Standard",
        "cost_premium_pct": 4.5,
        "ai_prompt": "Dramatic modern monumental residential facade, architectural fair-faced fluted concrete panels, geometric staggered box balcony cantilevers, deep window reveals creating bold rhythmic shadows, overcast cinematic lighting."
    },
    {
        "id": "biophilic_green",
        "name": "Biophilic Vertical Sanctuary",
        "concept": "Cascading hydroponic balcony planters, integrated vertical green walls, natural composite louvres, and open sky terraces.",
        "window_to_wall_ratio": 0.40,
        "shading_coefficient": 0.25,
        "u_value_w_m2k": 1.5,
        "embodied_carbon_rating": "Carbon Negative Facade",
        "cost_premium_pct": 15.0,
        "ai_prompt": "Visionary biophilic green residential tower facade, lush cascading hanging gardens and vertical pocket forests on staggered wrap-around balconies, natural timber pergolas, soft sunlight filtering through dense foliage."
    },
    {
        "id": "neoclassical_stucco",
        "name": "Neo-Classical Art Deco",
        "concept": "Symmetrical fluted pilasters, limestone stucco finish, arched pediments, and ornamental laser-cut bronze screens.",
        "window_to_wall_ratio": 0.32,
        "shading_coefficient": 0.32,
        "u_value_w_m2k": 1.6,
        "embodied_carbon_rating": "Standard",
        "cost_premium_pct": 6.0,
        "ai_prompt": "Refined neo-classical luxury residential building facade, pale limestone stucco finish with elegant fluted pilasters, arched French windows with decorative cast-bronze balustrades, classical proportions, golden hour light."
    }
]


def generate_facade_options(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Returns curated procedural facade styles tailored to tower geometry."""
    towers = project.get("towers") or []
    floors = max((int(t.get("floors") or 1) for t in towers), default=12)
    return [
        {
            **style,
            "recommended_for": f"{floors}-storey scheme (Height ~ {floors * 3}m)",
            "annual_energy_savings_pct": round((0.50 - style["window_to_wall_ratio"] * style["shading_coefficient"]) * 35, 1)
        }
        for style in FACADE_STYLES
    ]


def facade_options_ui(options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The same facade concepts with the field names the studio UI renders.

    `generate_facade_options` is the engine contract the tests pin; this adds the UI
    aliases on top so the two never drift apart — the cards read `style_id`, `description`,
    `wwr_pct`, `shading_coeff`, `shgc` and `nano_banana_prompt`.
    """
    out = []
    for f in options:
        f = dict(f)
        f["style_id"] = f["id"]
        f["description"] = f["concept"]
        f["wwr_pct"] = round(f["window_to_wall_ratio"] * 100, 1)
        f["shading_coeff"] = f["shading_coefficient"]
        # SHGC approximated as (1 - external shading cut) x base glass SHGC 0.62, a
        # standard double-glazed unit — a display figure, not a certified thermal value.
        f["shgc"] = round(0.62 * (1.0 - f["shading_coefficient"]), 2)
        f["nano_banana_prompt"] = f["ai_prompt"]
        out.append(f)
    return out


PERMEABILITY = {"Softscape": 0.9, "Water Feature / Eco-Drainage": 1.0, "Permeable Paving": 0.6,
                "Active Recreation": 0.5, "Community Gathering": 0.2}


def generate_landscape_zones(open_space_sqm: float) -> Dict[str, Any]:
    """Generates procedural landscape allocation for central parks and communal open space."""
    # The project's actual open space. It used to be floored at 1,000 m², which laid out a
    # park larger than the site on small plots.
    area = max(float(open_space_sqm or 0.0), 0.0)
    
    zones = [
        {
            "key": "central_lawn",
            "name": "Great Central Lawn & Event Meadow",
            "share_pct": 35.0,
            "area_sqm": round(area * 0.35, 1),
            "type": "Softscape",
            "elements": ["Bermuda sod turf", "Perimeter flowering canopy trees", "Sub-surface drainage system"]
        },
        {
            "key": "water_swale",
            "name": "Bio-Retention Swale & Reflection Basin",
            "share_pct": 15.0,
            "area_sqm": round(area * 0.15, 1),
            "type": "Water Feature / Eco-Drainage",
            "elements": ["Native aquatic reed beds", "Rainwater catchment detention pond", "Stepping stone crossing"]
        },
        {
            "key": "jogging_promenade",
            "name": "Tree-Lined Fitness Loop & Jogging Track",
            "share_pct": 20.0,
            "area_sqm": round(area * 0.20, 1),
            "type": "Permeable Paving",
            "elements": ["EPDM rubberised 1.8m jogging path", "Solar bollard lights", "Aroma garden shrubs"]
        },
        {
            "key": "play_arena",
            "name": "Children's Sensory Court & Multi-Play",
            "share_pct": 15.0,
            "area_sqm": round(area * 0.15, 1),
            "type": "Active Recreation",
            "elements": ["Impact-absorbing synthetic grass", "Timber climbing structures", "Mounded grass play dunes"]
        },
        {
            "key": "amphitheatre",
            "name": "Stepped Amphitheatre & Social Pavilion",
            "share_pct": 15.0,
            "area_sqm": round(area * 0.15, 1),
            "type": "Community Gathering",
            "elements": ["Natural stone tier seating", "Timber pergola trellis", "Open-air projection backdrop"]
        }
    ]
    
    softscape_pct = sum(z["share_pct"] for z in zones if "Softscape" in z["type"] or "Water" in z["type"])
    for z in zones:
        # UI aliases: the studio card renders pct_of_open_space, canopy_cover_pct and a
        # vegetation list. Canopy is per-type: softscape carries the tree canopy, water
        # features a reed fringe, and paved/community zones the shade planting.
        z["pct_of_open_space"] = z["share_pct"]
        z["canopy_cover_pct"] = {"Softscape": 65, "Water Feature / Eco-Drainage": 20,
                                 "Permeable Paving": 40, "Active Recreation": 30,
                                 "Community Gathering": 25}.get(z["type"], 30)
        z["vegetation"] = z["elements"]
    return {
        "total_open_space_sqm": area,
        "softscape_pct": softscape_pct,
        # Area-weighted share of each surface that lets rain soak in.
        "permeability_index": round(sum(z["share_pct"] / 100.0 * PERMEABILITY.get(z["type"], 0.5) for z in zones), 2),
        "zones": zones
    }


def generate_parking_layout(footprint_sqm: float, layout_type: str = "orthogonal") -> Dict[str, Any]:
    """Generates procedural parking bay grid and circulation aisles."""
    area = max(float(footprint_sqm or 3000.0), 500.0)
    
    # Bay sizing: 2.5m x 5.0m = 12.5 m2 per bay (NBC / SP 21)
    stall_w = 2.5
    stall_d = 5.0
    
    if layout_type == "herringbone_45":
        # 45-degree angle parking
        sqm_per_bay = 28.0  # including one-way 3.8m driveway
        angle = 45
        driveway_w = 3.8
    elif layout_type == "herringbone_60":
        # 60-degree angle parking
        sqm_per_bay = 29.5
        angle = 60
        driveway_w = 4.5
    else:
        # Standard 90-degree orthogonal parking
        sqm_per_bay = 32.0  # including two-way 6.0m aisle and columns
        angle = 90
        driveway_w = 6.0
    
    total_capacity = int(area / sqm_per_bay)
    accessible_bays = max(math.ceil(total_capacity * 0.03), 1)  # 3% accessible
    ev_bays = max(math.ceil(total_capacity * 0.10), 2)          # 10% EV charging
    standard_bays = max(total_capacity - accessible_bays - ev_bays, 0)
    
    return {
        "layout_type": layout_type,
        "angle_deg": angle,
        # UI alias: the studio reads the plain form for the layout-type label.
        "layout_type_plain": {"orthogonal": "orthogonal", "herringbone_45": "herringbone",
                              "herringbone_60": "herringbone"}.get(layout_type, layout_type),
        "bay_angle_deg": angle,
        "driveway_width_m": driveway_w,
        "ecs_efficiency_sqm_per_bay": round(area / max(total_capacity, 1), 1),
        "gross_area_sqm": area,
        "stall_dimensions_m": [stall_w, stall_d],
        "aisle_driveway_width_m": driveway_w,
        "total_capacity_bays": total_capacity,
        "standard_bays": standard_bays,
        "accessible_bays": accessible_bays,
        "ev_charging_bays": ev_bays,
        "space_efficiency_sqm_per_ecs": round(area / max(total_capacity, 1), 1),
        "aisle_circulation_area_pct": round((1.0 - (total_capacity * stall_w * stall_d / area)) * 100, 1)
    }
