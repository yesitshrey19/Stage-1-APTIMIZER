"""Headless 2D architectural floor plan renderer.

Generates presentation-ready, furnished architectural floor plan drawings
for tower floor plates, featuring realistic furniture blocks (beds, sofas,
dining sets, kitchen counters with hobs & sinks, bathroom vanities & WCs,
patio sets), architectural wall poché, door swings, window fenestrations,
unit boundary outlines, North arrow compass, dimension annotations, and title block.
"""
from __future__ import annotations

import io
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Arc, Circle, FancyBboxPatch, Polygon, Rectangle

# Premium architectural color palette
ROOM_COLORS: Dict[str, str] = {
    "living": "#F8FAFC",       # elegant light neutral/off-white
    "dining": "#FAF5FF",       # soft lavender tint
    "bedroom": "#F1F5F9",      # modern cool neutral
    "kitchen": "#FFFBEB",      # warm cream
    "bathroom": "#F0FDF4",     # spa light mint
    "balcony": "#F8FAFC",      # light stone
    "terrace": "#F8FAFC",
    "utility": "#FEF3C7",      # utility warm yellow
    "common": "#F1F5F9",       # corridor neutral
    "closet": "#FDF2F8",       # dressing pink tint
    "entrance": "#FAF5FF",     # foyer tint
    "foyer": "#FAF5FF",
    "study": "#F0F9FF",        # quiet sky tint
    "family": "#F8FAFC",
    "office": "#F0F9FF",
    "pooja": "#FEF9C3",        # auspicious soft gold
    "servant": "#F8FAFC",
    "shaft": "#E2E8F0",        # mechanical grey
    "pantry": "#FFFBEB",
    "passage": "#F8FAFC",
}

DEFAULT_ROOM_COLOR = "#F8FAFC"


def _get_rooms_for_tower(tower: Dict[str, Any], floor: Optional[int] = None) -> Tuple[List[Dict[str, Any]], int]:
    """Retrieve the room list for the specified floor, or fallback to the primary available floor."""
    floor_layouts = tower.get("floor_layouts") or {}
    chosen_floor = floor if floor is not None else 1

    if str(chosen_floor) in floor_layouts:
        rooms = (floor_layouts[str(chosen_floor)] or {}).get("rooms") or []
        if rooms:
            return rooms, chosen_floor

    if chosen_floor in floor_layouts:
        rooms = (floor_layouts[chosen_floor] or {}).get("rooms") or []
        if rooms:
            return rooms, chosen_floor

    if floor_layouts:
        for k in sorted(floor_layouts.keys(), key=lambda x: int(x) if str(x).isdigit() else 999):
            rooms = (floor_layouts[k] or {}).get("rooms") or []
            if rooms:
                fl_num = int(k) if str(k).isdigit() else 1
                return rooms, fl_num

    direct_rooms = tower.get("rooms") or []
    if direct_rooms:
        return direct_rooms, 1

    try:
        import aifloorplan
        rooms, _ = aifloorplan.generate_architectural_template(tower, chosen_floor)
        if rooms:
            return rooms, chosen_floor
    except Exception:
        pass

    return [], chosen_floor


def _draw_furniture_blocks(ax, rx: float, ry: float, rw: float, rh: float, rtype: str, rname: str):
    """Draw realistic architectural furniture and fixture vector blocks into the room."""
    # -------------------------------------------------------------
    # BEDROOM: Queen/King Bed + Nightstands + Pillows + Wardrobe
    # -------------------------------------------------------------
    if rtype == "bedroom":
        is_master = "master" in rname.lower()
        bed_w = min(2.0 if is_master else 1.6, rw * 0.55)
        bed_l = min(2.1 if is_master else 1.9, rh * 0.65)
        
        # Position bed against the top/north wall
        bx = rx + (rw - bed_w) / 2.0
        by = ry + 0.35
        
        # Bed base / mattress
        bed_box = FancyBboxPatch(
            (bx, by), bed_w, bed_l,
            boxstyle="round,pad=0.03,rounding_size=0.12",
            facecolor="#FFFFFF", edgecolor="#94A3B8", linewidth=0.9, zorder=4
        )
        ax.add_patch(bed_box)
        
        # Headboard
        headboard = Rectangle(
            (bx - 0.08, by - 0.12), bed_w + 0.16, 0.12,
            facecolor="#475569", edgecolor="#1E293B", linewidth=1.0, zorder=5
        )
        ax.add_patch(headboard)
        
        # Duvet cover line
        duvet_y = by + bed_l * 0.4
        ax.plot([bx + 0.05, bx + bed_w - 0.05], [duvet_y, duvet_y], color="#CBD5E1", linewidth=0.8, linestyle="--", zorder=5)
        
        # Pillows
        pil_w = (bed_w - 0.3) / 2.0
        pil_h = min(0.4, bed_l * 0.22)
        p1 = FancyBboxPatch((bx + 0.08, by + 0.06), pil_w, pil_h, boxstyle="round,pad=0.02,rounding_size=0.06", facecolor="#F8FAFC", edgecolor="#94A3B8", linewidth=0.7, zorder=5)
        p2 = FancyBboxPatch((bx + bed_w - pil_w - 0.08, by + 0.06), pil_w, pil_h, boxstyle="round,pad=0.02,rounding_size=0.06", facecolor="#F8FAFC", edgecolor="#94A3B8", linewidth=0.7, zorder=5)
        ax.add_patch(p1)
        ax.add_patch(p2)
        
        # Nightstands on both sides if space permits
        if bx - rx >= 0.45:
            ns1 = FancyBboxPatch((bx - 0.42, by), 0.38, 0.38, boxstyle="round,pad=0.01,rounding_size=0.04", facecolor="#E2E8F0", edgecolor="#94A3B8", linewidth=0.7, zorder=4)
            ax.add_patch(ns1)
            ax.add_patch(Circle((bx - 0.23, by + 0.19), 0.08, facecolor="#FDE047", edgecolor="#CA8A04", linewidth=0.5, zorder=5))
        if (rx + rw) - (bx + bed_w) >= 0.45:
            ns2 = FancyBboxPatch((bx + bed_w + 0.04, by), 0.38, 0.38, boxstyle="round,pad=0.01,rounding_size=0.04", facecolor="#E2E8F0", edgecolor="#94A3B8", linewidth=0.7, zorder=4)
            ax.add_patch(ns2)
            ax.add_patch(Circle((bx + bed_w + 0.23, by + 0.19), 0.08, facecolor="#FDE047", edgecolor="#CA8A04", linewidth=0.5, zorder=5))

        # Built-in Wardrobe along bottom wall
        if rh >= 3.2:
            ward_h = 0.55
            ward_w = min(rw * 0.7, 2.8)
            ward_x = rx + (rw - ward_w) / 2.0
            ward_y = ry + rh - ward_h - 0.08
            ward = Rectangle((ward_x, ward_y), ward_w, ward_h, facecolor="#F1F5F9", edgecolor="#64748B", linewidth=0.8, linestyle="--", zorder=4)
            ax.add_patch(ward)
            # Wardrobe door division
            ax.plot([ward_x + ward_w * 0.5, ward_x + ward_w * 0.5], [ward_y, ward_y + ward_h], color="#94A3B8", linewidth=0.6, zorder=5)

    # -------------------------------------------------------------
    # LIVING ROOM: Sofa + Coffee Table + TV Media Unit + Rug
    # -------------------------------------------------------------
    elif rtype in ("living", "family"):
        sofa_w = min(rw * 0.55, 2.4)
        sofa_d = min(rh * 0.22, 0.85)
        sx = rx + 0.4
        sy = ry + 0.4

        # Area Rug
        rug_w = min(rw * 0.75, 3.2)
        rug_h = min(rh * 0.65, 2.6)
        rug_x = rx + (rw - rug_w) / 2.0
        rug_y = ry + (rh - rug_h) / 2.0
        rug = FancyBboxPatch((rug_x, rug_y), rug_w, rug_h, boxstyle="round,pad=0.05,rounding_size=0.2", facecolor="#F1F5F9", edgecolor="#E2E8F0", linewidth=0.8, linestyle=":", zorder=3)
        ax.add_patch(rug)

        # 3-Seater Sofa
        sofa_back = FancyBboxPatch((sx, sy), sofa_w, sofa_d, boxstyle="round,pad=0.02,rounding_size=0.1", facecolor="#E2E8F0", edgecolor="#64748B", linewidth=0.9, zorder=4)
        ax.add_patch(sofa_back)
        # Cushions
        c_w = (sofa_w - 0.16) / 3.0
        for ci in range(3):
            cush = FancyBboxPatch((sx + 0.08 + ci * c_w, sy + 0.15), c_w - 0.04, sofa_d - 0.18, boxstyle="round,pad=0.01,rounding_size=0.05", facecolor="#F8FAFC", edgecolor="#94A3B8", linewidth=0.6, zorder=5)
            ax.add_patch(cush)

        # Coffee Table
        ct_w = min(sofa_w * 0.6, 1.2)
        ct_d = 0.55
        ct_x = sx + (sofa_w - ct_w) / 2.0
        ct_y = sy + sofa_d + 0.35
        if ct_y + ct_d < ry + rh - 0.4:
            coffee_table = FancyBboxPatch((ct_x, ct_y), ct_w, ct_d, boxstyle="round,pad=0.02,rounding_size=0.08", facecolor="#FFFFFF", edgecolor="#64748B", linewidth=0.8, zorder=4)
            ax.add_patch(coffee_table)

        # TV Media Credenza on opposite wall
        tv_w = min(rw * 0.5, 2.2)
        tv_d = 0.38
        tv_x = rx + (rw - tv_w) / 2.0
        tv_y = ry + rh - tv_d - 0.08
        tv_unit = Rectangle((tv_x, tv_y), tv_w, tv_d, facecolor="#334155", edgecolor="#0F172A", linewidth=0.8, zorder=4)
        ax.add_patch(tv_unit)
        ax.text(tv_x + tv_w / 2.0, tv_y + tv_d / 2.0, "MEDIA CONSOLE", ha="center", va="center", fontsize=4.5, color="#FFFFFF", family="monospace", zorder=5)

    # -------------------------------------------------------------
    # DINING ROOM: Dining Table with 4-6 Chairs
    # -------------------------------------------------------------
    elif rtype in ("dining", "pantry"):
        tbl_w = min(rw * 0.55, 1.6)
        tbl_h = min(rh * 0.45, 0.95)
        tx = rx + (rw - tbl_w) / 2.0
        ty = ry + (rh - tbl_h) / 2.0

        # Dining table
        table = FancyBboxPatch((tx, ty), tbl_w, tbl_h, boxstyle="round,pad=0.03,rounding_size=0.15", facecolor="#FFFFFF", edgecolor="#475569", linewidth=0.9, zorder=4)
        ax.add_patch(table)

        # Chairs along top and bottom
        chair_w, chair_d = 0.36, 0.22
        # Top chairs
        ax.add_patch(FancyBboxPatch((tx + 0.15, ty - chair_d - 0.04), chair_w, chair_d, boxstyle="round,pad=0.01,rounding_size=0.05", facecolor="#E2E8F0", edgecolor="#64748B", linewidth=0.6, zorder=4))
        ax.add_patch(FancyBboxPatch((tx + tbl_w - chair_w - 0.15, ty - chair_d - 0.04), chair_w, chair_d, boxstyle="round,pad=0.01,rounding_size=0.05", facecolor="#E2E8F0", edgecolor="#64748B", linewidth=0.6, zorder=4))
        # Bottom chairs
        ax.add_patch(FancyBboxPatch((tx + 0.15, ty + tbl_h + 0.04), chair_w, chair_d, boxstyle="round,pad=0.01,rounding_size=0.05", facecolor="#E2E8F0", edgecolor="#64748B", linewidth=0.6, zorder=4))
        ax.add_patch(FancyBboxPatch((tx + tbl_w - chair_w - 0.15, ty + tbl_h + 0.04), chair_w, chair_d, boxstyle="round,pad=0.01,rounding_size=0.05", facecolor="#E2E8F0", edgecolor="#64748B", linewidth=0.6, zorder=4))

    # -------------------------------------------------------------
    # KITCHEN: Countertop + Double Sink + 4-Burner Hob + Fridge
    # -------------------------------------------------------------
    elif rtype == "kitchen":
        counter_d = min(0.62, rw * 0.35)
        # Main Countertop along top edge
        ax.add_patch(Rectangle((rx, ry), rw, counter_d, facecolor="#FEF3C7", edgecolor="#D97706", linewidth=0.8, zorder=4))

        # Kitchen Sink (Double Bowl)
        sink_w, sink_d = 0.8, 0.44
        sink_x = rx + 0.4
        sink_y = ry + (counter_d - sink_d) / 2.0
        if sink_x + sink_w < rx + rw - 0.2:
            ax.add_patch(Rectangle((sink_x, sink_y), sink_w, sink_d, facecolor="#F1F5F9", edgecolor="#64748B", linewidth=0.8, zorder=5))
            half_w = (sink_w - 0.06) / 2.0
            ax.add_patch(FancyBboxPatch((sink_x + 0.02, sink_y + 0.02), half_w, sink_d - 0.04, boxstyle="round,pad=0.01,rounding_size=0.04", facecolor="#FFFFFF", edgecolor="#94A3B8", linewidth=0.6, zorder=6))
            ax.add_patch(FancyBboxPatch((sink_x + half_w + 0.04, sink_y + 0.02), half_w, sink_d - 0.04, boxstyle="round,pad=0.01,rounding_size=0.04", facecolor="#FFFFFF", edgecolor="#94A3B8", linewidth=0.6, zorder=6))

        # 4-Burner Cooking Gas Hob
        hob_w, hob_d = 0.72, 0.45
        hob_x = rx + rw - hob_w - 0.4
        hob_y = ry + (counter_d - hob_d) / 2.0
        if hob_x > sink_x + sink_w + 0.3:
            ax.add_patch(Rectangle((hob_x, hob_y), hob_w, hob_d, facecolor="#1E293B", edgecolor="#0F172A", linewidth=0.8, zorder=5))
            # 4 Burners
            for bxi, byi in [(0.2, 0.13), (0.52, 0.13), (0.2, 0.32), (0.52, 0.32)]:
                ax.add_patch(Circle((hob_x + bxi, hob_y + byi), 0.07, facecolor="#94A3B8", edgecolor="#CBD5E1", linewidth=0.6, zorder=6))

        # Refrigerator in corner
        ref_s = min(0.75, rw * 0.4)
        if rh - counter_d >= ref_s + 0.2:
            ref_x = rx + rw - ref_s - 0.08
            ref_y = ry + rh - ref_s - 0.08
            ax.add_patch(FancyBboxPatch((ref_x, ref_y), ref_s, ref_s, boxstyle="round,pad=0.02,rounding_size=0.06", facecolor="#E2E8F0", edgecolor="#64748B", linewidth=0.8, zorder=4))
            ax.text(ref_x + ref_s / 2.0, ref_y + ref_s / 2.0, "FRIDGE", ha="center", va="center", fontsize=5.0, fontweight="bold", color="#475569", family="monospace", zorder=5)

    # -------------------------------------------------------------
    # BATHROOM: WC Commode + Vanity Sink + Glass Shower Stall
    # -------------------------------------------------------------
    elif rtype == "bathroom":
        # WC Commode
        wc_x = rx + 0.3
        wc_y = ry + 0.15
        # Tank
        ax.add_patch(FancyBboxPatch((wc_x, wc_y), 0.45, 0.18, boxstyle="round,pad=0.01,rounding_size=0.04", facecolor="#FFFFFF", edgecolor="#64748B", linewidth=0.7, zorder=4))
        # Bowl
        ax.add_patch(FancyBboxPatch((wc_x + 0.05, wc_y + 0.18), 0.35, 0.36, boxstyle="round,pad=0.02,rounding_size=0.15", facecolor="#FFFFFF", edgecolor="#64748B", linewidth=0.8, zorder=4))

        # Vanity Washbasin
        if rw >= 2.0:
            van_w, van_d = 0.65, 0.42
            van_x = rx + rw - van_w - 0.15
            van_y = ry + 0.15
            ax.add_patch(FancyBboxPatch((van_x, van_y), van_w, van_d, boxstyle="round,pad=0.01,rounding_size=0.05", facecolor="#E2E8F0", edgecolor="#64748B", linewidth=0.7, zorder=4))
            ax.add_patch(Circle((van_x + van_w / 2.0, van_y + van_d / 2.0), 0.14, facecolor="#FFFFFF", edgecolor="#94A3B8", linewidth=0.6, zorder=5))

        # Walk-in Shower Enclosure
        if rh >= 2.2:
            shw_h = min(rh * 0.45, 1.0)
            shw_y = ry + rh - shw_h - 0.05
            ax.add_patch(Rectangle((rx + 0.05, shw_y), rw - 0.1, shw_h, facecolor="#E0F2FE", edgecolor="#38BDF8", linewidth=0.8, linestyle="--", zorder=4))
            # Shower drain
            ax.add_patch(Circle((rx + rw / 2.0, shw_y + shw_h / 2.0), 0.06, facecolor="#0284C7", edgecolor="#0369A1", linewidth=0.5, zorder=5))

    # -------------------------------------------------------------
    # BALCONY / TERRACE: Timber Deck Slats + Outdoor Chairs
    # -------------------------------------------------------------
    elif rtype in ("balcony", "terrace"):
        # Outdoor decking slats pattern
        slat_step = 0.35
        cur_y = ry + slat_step
        while cur_y < ry + rh - 0.1:
            ax.plot([rx + 0.08, rx + rw - 0.08], [cur_y, cur_y], color="#CBD5E1", linewidth=0.5, zorder=3)
            cur_y += slat_step
        # Small bistro outdoor coffee set
        if rw >= 2.0 and rh >= 1.2:
            bx = rx + rw / 2.0
            by = ry + rh / 2.0
            ax.add_patch(Circle((bx, by), 0.22, facecolor="#FFFFFF", edgecolor="#64748B", linewidth=0.7, zorder=4))
            ax.add_patch(Circle((bx - 0.35, by), 0.14, facecolor="#E2E8F0", edgecolor="#94A3B8", linewidth=0.6, zorder=4))
            ax.add_patch(Circle((bx + 0.35, by), 0.14, facecolor="#E2E8F0", edgecolor="#94A3B8", linewidth=0.6, zorder=4))

    # -------------------------------------------------------------
    # STUDY / OFFICE: Work Desk + Executive Chair + Laptop
    # -------------------------------------------------------------
    elif rtype in ("study", "office"):
        desk_w = min(1.4, rw * 0.6)
        desk_d = 0.6
        dx = rx + (rw - desk_w) / 2.0
        dy = ry + 0.3
        ax.add_patch(FancyBboxPatch((dx, dy), desk_w, desk_d, boxstyle="round,pad=0.02,rounding_size=0.06", facecolor="#E2E8F0", edgecolor="#475569", linewidth=0.8, zorder=4))
        # Laptop
        ax.add_patch(Rectangle((dx + desk_w / 2.0 - 0.18, dy + 0.15), 0.36, 0.26, facecolor="#334155", edgecolor="#0F172A", linewidth=0.5, zorder=5))
        # Chair
        ax.add_patch(Circle((dx + desk_w / 2.0, dy + desk_d + 0.25), 0.2, facecolor="#475569", edgecolor="#1E293B", linewidth=0.7, zorder=4))

    # -------------------------------------------------------------
    # POOJA ROOM: Altar Platform with Sacred Mandir Motif
    # -------------------------------------------------------------
    elif rtype == "pooja":
        altar_w = min(rw * 0.7, 1.2)
        altar_d = 0.45
        ax.add_patch(FancyBboxPatch((rx + (rw - altar_w) / 2.0, ry + 0.2), altar_w, altar_d, boxstyle="round,pad=0.02,rounding_size=0.08", facecolor="#FEF08A", edgecolor="#CA8A04", linewidth=0.8, zorder=4))
        ax.add_patch(Circle((rx + rw / 2.0, ry + 0.2 + altar_d / 2.0), 0.1, facecolor="#EA580C", edgecolor="#9A3412", linewidth=0.6, zorder=5))


def render_floorplan_image(
    tower: Dict[str, Any],
    floor: Optional[int] = None,
    project_name: str = "",
    dpi: int = 150,
    show_title_block: bool = True,
) -> bytes:
    """Render a presentation-quality 2D furnished architectural floor plan drawing.

    Returns:
        bytes: PNG image bytes.
    """
    rooms, floor_num = _get_rooms_for_tower(tower, floor)
    # Stored layouts are free-form dicts (hand-edited, legacy or AI-written). Every pass
    # below reads geometry directly, so it is normalised once here with the defaults the
    # renderer has always used, instead of a missing key crashing the whole drawing.
    def _num(v, default):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default
    rooms = [{**r, "x": _num(r.get("x"), 0.0), "y": _num(r.get("y"), 0.0),
              "w": max(_num(r.get("w"), 3.0), 0.3), "h": max(_num(r.get("h"), 3.0), 0.3)}
             for r in rooms if isinstance(r, dict)]
    tower_name = tower.get("name") or "Tower"

    if not rooms:
        fig = Figure(figsize=(8, 6), dpi=dpi, facecolor="#F8FAFC")
        ax = fig.add_subplot(111)
        ax.set_facecolor("#FFFFFF")
        ax.text(
            0.5, 0.5,
            f"No Floor Layout Generated Yet\n{tower_name} (Floor {floor_num})\n\nGenerate a floor plan in Apartment Planning to view the layout.",
            ha="center", va="center", fontsize=12, color="#64748B", family="sans-serif"
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color("#CBD5E1")
        buf = io.BytesIO()
        FigureCanvasAgg(fig).print_png(buf)
        return buf.getvalue()

    # Calculate bounding geometry
    xs = [float(r.get("x", 0)) for r in rooms]
    ys = [float(r.get("y", 0)) for r in rooms]
    x2s = [float(r.get("x", 0)) + max(float(r.get("w", 3)), 0.5) for r in rooms]
    y2s = [float(r.get("y", 0)) + max(float(r.get("h", 3)), 0.5) for r in rooms]

    min_x, max_x = min(xs), max(x2s)
    min_y, max_y = min(ys), max(y2s)
    span_x = max(max_x - min_x, 10.0)
    span_y = max(max_y - min_y, 10.0)

    # Architectural margins (compact to emphasize the floor plan)
    pad_left = max(span_x * 0.06, 1.8)
    pad_right = max(span_x * 0.06, 1.8)
    pad_top = max(span_y * 0.08, 2.2)
    pad_bottom = max(span_y * 0.14, 3.4) if show_title_block else max(span_y * 0.06, 1.6)

    total_w = span_x + pad_left + pad_right
    total_h = span_y + pad_top + pad_bottom

    fig_w = 12.0
    fig_h = max(fig_w * (total_h / total_w), 8.0)
    if fig_h > 15.0:
        fig_h = 15.0
        fig_w = max(fig_h * (total_w / total_h), 8.5)

    fig = Figure(figsize=(fig_w, fig_h), dpi=dpi, facecolor="#F8FAFC")
    ax = fig.add_subplot(111)
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax.set_facecolor("#FFFFFF")

    # Y increases downward: North = top, South = bottom
    ax.set_xlim(min_x - pad_left, max_x + pad_right)
    ax.set_ylim(max_y + pad_bottom, min_y - pad_top)
    ax.set_aspect("equal")
    ax.axis("off")

    # Subtle structural grid in background
    gx_start = math.floor(min_x - pad_left)
    gx_end = math.ceil(max_x + pad_right)
    gy_start = math.floor(min_y - pad_top)
    gy_end = math.ceil(max_y + pad_bottom)
    for gx in range(gx_start, gx_end + 1, 2):
        ax.axvline(gx, color="#F1F5F9", linewidth=0.5, zorder=1)
    for gy in range(gy_start, gy_end + 1, 2):
        ax.axhline(gy, color="#F1F5F9", linewidth=0.5, zorder=1)

    # Unit bounding groups
    unit_map: Dict[Any, List[Dict[str, Any]]] = {}
    for r in rooms:
        uid = r.get("unit_id")
        if uid is not None:
            unit_map.setdefault(uid, []).append(r)

    # Draw unit boundary outlines and badges
    # Numbered across the whole plate: unit_index restarts on each side of the corridor,
    # which labelled two different flats "UNIT 1".
    for unit_no, (uid, urooms) in enumerate(sorted(unit_map.items(), key=lambda kv: (
            min(float(r.get("y", 0)) for r in kv[1]), min(float(r.get("x", 0)) for r in kv[1]))), 1):
        uxs = [float(r.get("x", 0)) for r in urooms]
        uys = [float(r.get("y", 0)) for r in urooms]
        ux2s = [float(r.get("x", 0)) + max(float(r.get("w", 3)), 0.5) for r in urooms]
        uy2s = [float(r.get("y", 0)) + max(float(r.get("h", 3)), 0.5) for r in urooms]
        ux, uy = min(uxs), min(uys)
        uw, uh = max(ux2s) - ux, max(uy2s) - uy
        u_type = str(urooms[0].get("unit_type") or "").upper()

        # Architectural unit border (Blue dashed line)
        unit_rect = Rectangle(
            (ux, uy), uw, uh,
            fill=False, edgecolor="#2563EB", linewidth=1.4,
            linestyle=(0, (5, 3)), zorder=2
        )
        ax.add_patch(unit_rect)

        # Unit header pill badge
        badge_w = min(max(uw * 0.45, 3.4), uw)
        badge_h = 0.82
        badge_x = ux + 0.2
        badge_y = uy - badge_h - 0.15
        if badge_y < min_y - pad_top + 1.2:
            badge_y = uy + 0.15

        badge_box = FancyBboxPatch(
            (badge_x, badge_y), badge_w, badge_h,
            boxstyle="round,pad=0.1,rounding_size=0.25",
            facecolor="#EFF6FF", edgecolor="#BFDBFE",
            linewidth=1.0, zorder=7
        )
        ax.add_patch(badge_box)
        unit_label = f"UNIT {unit_no} · {u_type}" if u_type else f"UNIT {unit_no}"
        ax.text(
            badge_x + badge_w / 2.0, badge_y + badge_h / 2.0,
            unit_label,
            ha="center", va="center",
            fontsize=7.5, fontweight="bold",
            color="#1E40AF", family="monospace", zorder=8
        )

    # ------------------------------------------------------------------ drawing scale
    # Walls, openings and lettering are drawn at true size, so everything is converted from
    # metres to points with the drawing's actual scale.
    ppm = min(fig_w * 72.0 / total_w, fig_h * 72.0 / total_h)      # points per metre
    WALL_EXT, WALL_INT, RAIL = 0.23, 0.115, 0.05
    POCHE = "#1E293B"

    def lw_of(metres: float) -> float:
        return max(metres * ppm, 0.4)

    def fit_pt(text: str, width_m: float, height_m: float, max_pt: float, min_pt: float = 3.4,
               bold: bool = False):
        if not text:
            return None
        char = 0.64 if bold else 0.56
        by_w = width_m * ppm * 0.88 / (len(text) * char)
        by_h = height_m * ppm * 0.42
        pt = min(max_pt, by_w, by_h)
        return pt if pt >= min_pt else None

    by_id = {str(r.get("id")): r for r in rooms}
    has_corridor_room = any(str(r.get("type")) == "common" for r in rooms)

    # Unit envelopes and their corridor side, from the rooms' own metadata.
    unit_box: Dict[Any, Tuple[float, float, float, float]] = {}
    unit_entry: Dict[Any, str] = {}
    for uid, urooms in unit_map.items():
        ux = min(float(r.get("x", 0)) for r in urooms)
        uy = min(float(r.get("y", 0)) for r in urooms)
        ux2 = max(float(r.get("x", 0)) + float(r.get("w", 0)) for r in urooms)
        uy2 = max(float(r.get("y", 0)) + float(r.get("h", 0)) for r in urooms)
        unit_box[uid] = (ux, uy, ux2, uy2)
        entry = next((r for r in urooms if r.get("main_entrance")), None)
        unit_entry[uid] = str((entry or {}).get("entry_edge") or "S").upper()

    def edges_of(r):
        x, y, w, h = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
        return {"N": ((x, y), (x + w, y)), "S": ((x, y + h), (x + w, y + h)),
                "W": ((x, y), (x, y + h)), "E": ((x + w, y), (x + w, y + h))}

    def on_unit_side(r, side, eps=0.05):
        box = unit_box.get(r.get("unit_id"))
        if not box:
            return False
        ux, uy, ux2, uy2 = box
        x, y, w, h = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
        return {"N": abs(y - uy) < eps, "S": abs(y + h - uy2) < eps,
                "W": abs(x - ux) < eps, "E": abs(x + w - ux2) < eps}[side]

    def shared_edge(a, b, eps=0.05):
        """(side of a, start, end) of the wall a and b share, or None."""
        ax_, ay, aw, ah = float(a["x"]), float(a["y"]), float(a["w"]), float(a["h"])
        bx, by_, bw, bh = float(b["x"]), float(b["y"]), float(b["w"]), float(b["h"])
        xo0, xo1 = max(ax_, bx), min(ax_ + aw, bx + bw)
        yo0, yo1 = max(ay, by_), min(ay + ah, by_ + bh)
        if xo1 - xo0 > 0.3:
            if abs(ay + ah - by_) < eps:
                return "S", xo0, xo1
            if abs(ay - (by_ + bh)) < eps:
                return "N", xo0, xo1
        if yo1 - yo0 > 0.3:
            if abs(ax_ + aw - bx) < eps:
                return "E", yo0, yo1
            if abs(ax_ - (bx + bw)) < eps:
                return "W", yo0, yo1
        return None

    def target_of(r):
        # `door_via` names the room a door physically opens into when `door_to` is its role
        # (a bedroom's door gives onto the passage, through the bedroom's own lobby).
        t = r.get("door_via") or r.get("door_to") or r.get("door_child_of")
        if not t:
            return None
        uid = r.get("unit_id")
        for key in (f"{uid}-{t}", str(t)):
            if key in by_id and by_id[key] is not r:
                return by_id[key]
        return next((c for c in rooms if c is not r and c.get("unit_id") == uid
                     and str(c.get("name", "")).lower() == str(t).lower()), None)

    # ------------------------------------------------------------------ corridor (fallback only)
    corridor_w = float(tower.get("corridor_width") or 0)
    if corridor_w > 0 and not has_corridor_room:
        corr_y = max_y - corridor_w
        ax.add_patch(Rectangle((min_x, corr_y), span_x, corridor_w, facecolor="#E2E8F0",
                               edgecolor="none", zorder=2))

    # ------------------------------------------------------------------ floors and furniture
    for r in rooms:
        rx, ry = float(r.get("x", 0)), float(r.get("y", 0))
        rw, rh = max(float(r.get("w", 3)), 0.3), max(float(r.get("h", 3)), 0.3)
        rtype = str(r.get("type") or "common").lower()
        rname = str(r.get("name") or rtype.replace("_", " ").title())
        ax.add_patch(Rectangle((rx, ry), rw, rh, facecolor=ROOM_COLORS.get(rtype, DEFAULT_ROOM_COLOR),
                               edgecolor="none", zorder=3))
        if rtype in ("balcony", "terrace"):
            for i in range(1, int(rw / 0.15)):
                ax.plot([rx + i * 0.15, rx + i * 0.15], [ry, ry + rh], color="#E7E5E4", linewidth=0.4, zorder=3)
        if rtype == "shaft":
            ax.plot([rx, rx + rw], [ry, ry + rh], color="#94A3B8", linewidth=0.8, zorder=4)
            ax.plot([rx, rx + rw], [ry + rh, ry], color="#94A3B8", linewidth=0.8, zorder=4)
        if rtype not in ("passage", "common", "shaft", "storage", "balcony", "terrace"):
            _draw_furniture_blocks(ax, rx, ry, rw, rh, rtype, rname)

    # ------------------------------------------------------------------ walls (poché)
    for r in rooms:
        rtype = str(r.get("type") or "").lower()
        if rtype in ("balcony", "terrace"):
            continue
        rx, ry, rw, rh = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
        ax.add_patch(Rectangle((rx, ry), rw, rh, fill=False, edgecolor=POCHE,
                               linewidth=lw_of(WALL_INT), joinstyle="miter", zorder=6))
    for r in rooms:
        rtype = str(r.get("type") or "").lower()
        if not r.get("unit_id"):
            continue
        for side, (p0, p1) in edges_of(r).items():
            if not on_unit_side(r, side):
                continue
            if rtype in ("balcony", "terrace"):
                # Railing: two thin lines at the slab edge, no masonry.
                off = 0.06 if side in ("N", "W") else -0.06
                if side in ("N", "S"):
                    ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color=POCHE, linewidth=lw_of(RAIL), zorder=6)
                    ax.plot([p0[0], p1[0]], [p0[1] + off, p1[1] + off], color=POCHE, linewidth=0.5, zorder=6)
                else:
                    ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color=POCHE, linewidth=lw_of(RAIL), zorder=6)
                    ax.plot([p0[0] + off, p1[0] + off], [p0[1], p1[1]], color=POCHE, linewidth=0.5, zorder=6)
            else:
                ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color=POCHE, linewidth=lw_of(WALL_EXT),
                        solid_capstyle="projecting", zorder=6)
    # Balcony side walls are low parapets: thin.
    for r in rooms:
        if str(r.get("type")) in ("balcony", "terrace"):
            rx, ry, rw, rh = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
            ax.add_patch(Rectangle((rx, ry), rw, rh, fill=False, edgecolor=POCHE,
                                   linewidth=lw_of(RAIL), zorder=6))

    # ------------------------------------------------------------------ windows / glazed doors
    def glaze(side, a0, a1, at, thick):
        gap_lw = lw_of(thick) + 0.6
        if side in ("N", "S"):
            ax.plot([a0, a1], [at, at], color="#FFFFFF", linewidth=gap_lw, zorder=7, solid_capstyle="butt")
            for d in (-thick / 4.0, 0.0, thick / 4.0):
                ax.plot([a0, a1], [at + d, at + d], color="#0284C7", linewidth=0.6, zorder=8)
            for e in (a0, a1):
                ax.plot([e, e], [at - thick / 2, at + thick / 2], color=POCHE, linewidth=0.7, zorder=8)
        else:
            ax.plot([at, at], [a0, a1], color="#FFFFFF", linewidth=gap_lw, zorder=7, solid_capstyle="butt")
            for d in (-thick / 4.0, 0.0, thick / 4.0):
                ax.plot([at + d, at + d], [a0, a1], color="#0284C7", linewidth=0.6, zorder=8)
            for e in (a0, a1):
                ax.plot([at - thick / 2, at + thick / 2], [e, e], color=POCHE, linewidth=0.7, zorder=8)

    glazed_types = ("living", "bedroom", "kitchen", "study", "office", "family", "dining", "servant")
    for r in rooms:
        rtype = str(r.get("type") or "").lower()
        if rtype not in glazed_types or not r.get("unit_id"):
            continue
        entry_side = unit_entry.get(r.get("unit_id"), "S")
        done = False
        for b in rooms:
            if b.get("unit_id") != r.get("unit_id") or str(b.get("type")) not in ("balcony", "terrace"):
                continue
            se = shared_edge(r, b)
            if se:
                side, s0, s1 = se
                at = float(r["y"]) if side == "N" else float(r["y"]) + float(r["h"]) if side == "S" else \
                    float(r["x"]) if side == "W" else float(r["x"]) + float(r["w"])
                span = s1 - s0
                glaze(side, s0 + span * 0.15, s1 - span * 0.15, at, WALL_INT)   # glazed door to balcony
                done = True
        for side, (p0, p1) in edges_of(r).items():
            if side == entry_side or not on_unit_side(r, side):
                continue
            if side in ("N", "S"):
                span = p1[0] - p0[0]
                glaze(side, p0[0] + span * 0.2, p1[0] - span * 0.2, p0[1], WALL_EXT)
            else:
                span = p1[1] - p0[1]
                glaze(side, p0[1] + span * 0.2, p1[1] - span * 0.2, p0[0], WALL_EXT)
            done = True
        _ = done

    # ------------------------------------------------------------------ doors
    def door(side, hinge_x, hinge_y, leaf, thick):
        """Opening cut through the wall plus a leaf swinging into the room on `side`'s inside."""
        if side in ("N", "S"):
            ax.plot([hinge_x, hinge_x + leaf], [hinge_y, hinge_y], color="#FFFFFF",
                    linewidth=lw_of(thick) + 0.8, zorder=7, solid_capstyle="butt")
            sgn = 1 if side == "N" else -1
            ax.plot([hinge_x, hinge_x], [hinge_y, hinge_y + sgn * leaf], color=POCHE, linewidth=0.9, zorder=8)
            ax.add_patch(Arc((hinge_x, hinge_y), leaf * 2, leaf * 2, theta1=0 if side == "N" else 270,
                             theta2=90 if side == "N" else 360, color="#64748B", linewidth=0.6, zorder=8))
        else:
            ax.plot([hinge_x, hinge_x], [hinge_y, hinge_y + leaf], color="#FFFFFF",
                    linewidth=lw_of(thick) + 0.8, zorder=7, solid_capstyle="butt")
            sgn = 1 if side == "W" else -1
            ax.plot([hinge_x, hinge_x + sgn * leaf], [hinge_y, hinge_y], color=POCHE, linewidth=0.9, zorder=8)
            ax.add_patch(Arc((hinge_x, hinge_y), leaf * 2, leaf * 2, theta1=0 if side == "W" else 90,
                             theta2=90 if side == "W" else 180, color="#64748B", linewidth=0.6, zorder=8))

    leaf_of = {"bedroom": 0.9, "bathroom": 0.75, "kitchen": 0.8, "servant": 0.75, "closet": 0.75}
    for r in rooms:
        rtype = str(r.get("type") or "").lower()
        rx, ry, rw, rh = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
        if r.get("main_entrance") or (r.get("service_access") and rtype == "servant"):
            side = str(r.get("entry_edge") or "S").upper()
            leaf = 1.1 if r.get("main_entrance") else 0.9
            if side in ("N", "S"):
                hx = rx + max((rw - leaf) / 2.0, 0.1)
                door(side, hx, ry if side == "N" else ry + rh, min(leaf, rw - 0.2), WALL_EXT)
            else:
                hy = ry + max((rh - leaf) / 2.0, 0.1)
                door(side, rx if side == "W" else rx + rw, hy, min(leaf, rh - 0.2), WALL_EXT)
            continue
        if rtype in ("balcony", "terrace", "passage", "common", "shaft") or r.get("unit_id") is None:
            continue
        t = target_of(r)
        if not t:
            continue
        se = shared_edge(r, t)
        if not se:
            continue
        side, s0, s1 = se
        leaf = min(leaf_of.get(rtype, 0.9), s1 - s0 - 0.25)
        if leaf < 0.55:
            continue
        a0 = s0 + 0.12
        if side == "N":
            door("N", a0, ry, leaf, WALL_INT)
        elif side == "S":
            door("S", a0, ry + rh, leaf, WALL_INT)
        elif side == "W":
            door("W", rx, a0, leaf, WALL_INT)
        else:
            door("E", rx + rw, a0, leaf, WALL_INT)

    # ------------------------------------------------------------------ labels sized to fit
    for r in rooms:
        rx, ry = float(r.get("x", 0)), float(r.get("y", 0))
        rw, rh = max(float(r.get("w", 3)), 0.3), max(float(r.get("h", 3)), 0.3)
        rtype = str(r.get("type") or "common").lower()
        rname = str(r.get("name") or rtype.replace("_", " ").title())
        if rtype == "common":
            rname = "Corridor"
        cx = rx + rw / 2.0
        cy = ry + rh * (0.70 if rtype == "bedroom" and rh >= 3.4 else 0.5)
        dims = f"{rw:.2f} × {rh:.2f} m"
        short = (rname.replace("Utility / Dry Balcony", "Utility").replace("Breakfast & Crockery", "Breakfast")
                 .replace("Bathroom", "Bath").replace("Bedroom", "Bed").replace("Ensuite", "Ens.")
                 .replace("Master", "Mstr").replace("Entrance ", "").replace("Private ", "")
                 .replace("Kitchen Store", "Store").replace(" Lobby", " Lby"))
        name_pt = fit_pt(rname.upper(), rw, rh, 6.8, bold=True)
        label = rname.upper()
        if name_pt is None:
            name_pt = fit_pt(short.upper(), rw, rh, 6.0, min_pt=3.0, bold=True)
            label = short.upper()
        if name_pt is None:
            continue
        dim_pt = fit_pt(dims, rw, rh * 0.5, min(name_pt * 0.82, 5.6), min_pt=3.0)
        gap = name_pt / ppm * 0.75
        if dim_pt:
            ax.text(cx, cy - gap * 0.55, label, ha="center", va="center", fontsize=name_pt,
                    fontweight="bold", color="#0F172A", family="sans-serif", zorder=9,
                    bbox=dict(boxstyle="round,pad=0.15", facecolor="#FFFFFF", edgecolor="none", alpha=0.75))
            ax.text(cx, cy + gap * 0.75, dims, ha="center", va="center", fontsize=dim_pt,
                    color="#475569", family="sans-serif", zorder=9)
        else:
            ax.text(cx, cy, label, ha="center", va="center", fontsize=name_pt, fontweight="bold",
                    color="#0F172A", family="sans-serif", zorder=9,
                    bbox=dict(boxstyle="round,pad=0.12", facecolor="#FFFFFF", edgecolor="none", alpha=0.75))

    # ------------------------------------------------------------------ overall dimensions
    def dim_line(x0, y0, x1, y1, text, horizontal):
        ax.plot([x0, x1], [y0, y1], color="#475569", linewidth=0.6, zorder=8)
        tick = 0.25
        for (px, py) in ((x0, y0), (x1, y1)):
            ax.plot([px - tick, px + tick], [py + tick, py - tick], color="#475569", linewidth=0.8, zorder=8)
            if horizontal:
                ax.plot([px, px], [py - 0.15, py + 0.6], color="#94A3B8", linewidth=0.4, zorder=8)
            else:
                ax.plot([px - 0.15, px + 0.6], [py, py], color="#94A3B8", linewidth=0.4, zorder=8)
        if horizontal:
            ax.text((x0 + x1) / 2, y0 - 0.2, text, ha="center", va="bottom", fontsize=6, color="#334155", zorder=8)
        else:
            ax.text(x0 - 0.2, (y0 + y1) / 2, text, ha="right", va="center", fontsize=6, color="#334155",
                    rotation=90, zorder=8)

    dim_line(min_x, min_y - 1.9, max_x, min_y - 1.9, f"{max_x - min_x:.2f} m", True)
    dim_line(min_x - 1.0, min_y, min_x - 1.0, max_y, f"{max_y - min_y:.2f} m", False)

    # -------------------------------------------------------------
    # Architectural Compass / North Arrow (Top Left)
    # -------------------------------------------------------------
    compass_cx = min_x - pad_left + 1.6
    compass_cy = min_y - pad_top + 1.6
    cr = 1.0

    compass_bg = Circle((compass_cx, compass_cy), cr, facecolor="#FFFFFF", edgecolor="#CBD5E1", linewidth=1.2, zorder=8)
    ax.add_patch(compass_bg)
    compass_inner = Circle((compass_cx, compass_cy), cr * 0.8, fill=False, edgecolor="#E2E8F0", linestyle="--", linewidth=0.8, zorder=8)
    ax.add_patch(compass_inner)

    north_tip = (compass_cx, compass_cy - cr * 0.75)
    south_tip = (compass_cx, compass_cy + cr * 0.75)
    left_mid = (compass_cx - cr * 0.22, compass_cy)
    right_mid = (compass_cx + cr * 0.22, compass_cy)

    p_north = Polygon([north_tip, right_mid, (compass_cx, compass_cy), left_mid], facecolor="#2563EB", edgecolor="none", zorder=9)
    p_south = Polygon([south_tip, right_mid, (compass_cx, compass_cy), left_mid], facecolor="#94A3B8", edgecolor="none", zorder=9)
    ax.add_patch(p_north)
    ax.add_patch(p_south)

    ax.text(compass_cx, compass_cy - cr * 0.85, "N", ha="center", va="bottom", fontsize=8, fontweight="bold", color="#1E40AF", family="monospace", zorder=10)
    ax.text(compass_cx, compass_cy + cr * 0.85, "S", ha="center", va="top", fontsize=6, fontweight="bold", color="#64748B", family="monospace", zorder=10)
    ax.text(compass_cx + cr * 0.85, compass_cy, "E", ha="left", va="center", fontsize=6, fontweight="bold", color="#64748B", family="monospace", zorder=10)
    ax.text(compass_cx - cr * 0.85, compass_cy, "W", ha="right", va="center", fontsize=6, fontweight="bold", color="#64748B", family="monospace", zorder=10)

    # -------------------------------------------------------------
    # Architectural Presentation Title Block (Bottom Right)
    # -------------------------------------------------------------
    if show_title_block:
        tb_w = max(min(span_x * 0.65, 12.0), 7.5)
        # Wide enough for its longest line at the drawing's scale (the header used to run
        # out of the box on small plates).
        longest = max(len(f"{tower_name.upper()} — FURNISHED PROPOSED FLOOR PLATE") * 8.5,
                      len("APTIMIZER · ARCHITECTURAL PRESENTATION DRAWING") * 6.5, 62 * 7.0)
        tb_w = max(tb_w, longest * 0.62 / ppm + 0.8)
        tb_h = 3.2
        tb_x = max_x + pad_right - tb_w - 0.5
        tb_y = max_y + pad_bottom - tb_h - 0.4

        tb_box = FancyBboxPatch(
            (tb_x, tb_y), tb_w, tb_h,
            boxstyle="round,pad=0.1,rounding_size=0.2",
            facecolor="#FFFFFF", edgecolor="#0F172A",
            linewidth=1.5, zorder=8
        )
        ax.add_patch(tb_box)

        tb_hdr = FancyBboxPatch(
            (tb_x, tb_y), tb_w, 0.8,
            boxstyle="round,pad=0.0,rounding_size=0.1",
            facecolor="#0F172A", edgecolor="#0F172A",
            linewidth=0.5, zorder=9
        )
        ax.add_patch(tb_hdr)
        ax.text(
            tb_x + 0.3, tb_y + 0.4,
            "APTIMIZER · ARCHITECTURAL PRESENTATION DRAWING",
            ha="left", va="center",
            fontsize=6.5, fontweight="bold", color="#FFFFFF", family="sans-serif", zorder=10
        )

        proj_str = project_name if project_name else "RESIDENTIAL SCHEME"
        now_str = datetime.now(timezone.utc).strftime("%d %b %Y")
        plate_area = (max_x - min_x) * (max_y - min_y)

        ax.text(tb_x + 0.3, tb_y + 1.25, f"{tower_name.upper()} — FURNISHED PROPOSED FLOOR PLATE",
                ha="left", va="center", fontsize=8.5, fontweight="bold", color="#0F172A", family="sans-serif", zorder=10)
        ax.text(tb_x + 0.3, tb_y + 1.8, f"Level: Floor {floor_num}{' (top floor)' if floor_num == int(tower.get('floors') or 0) else ''}  |  Units: {len(unit_map)} Flats",
                ha="left", va="center", fontsize=7, color="#334155", family="sans-serif", zorder=10)
        ax.text(tb_x + 0.3, tb_y + 2.3, f"Plate drawn: {max_x - min_x:.1f} x {max_y - min_y:.1f} m ({plate_area:,.0f} m²)  |  Rooms: {len(rooms)}",
                ha="left", va="center", fontsize=7, color="#475569", family="sans-serif", zorder=10)
        ax.text(tb_x + 0.3, tb_y + 2.8, f"Project: {proj_str}  |  Date: {now_str}  |  Status: SCHEMATIC DESIGN",
                ha="left", va="center", fontsize=6.5, color="#64748B", family="sans-serif", zorder=10)

    buf = io.BytesIO()
    FigureCanvasAgg(fig).print_png(buf)
    return buf.getvalue()
