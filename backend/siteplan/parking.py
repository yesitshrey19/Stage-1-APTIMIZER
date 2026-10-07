"""Surface parking bays lining the circulation network.

In a real site plan the drives are flanked by stalls almost continuously — it is what
makes a scheme read as a place rather than a massing diagram, and it is where most of the
NBC ECS requirement is actually satisfied on a low-coverage site.

Bays are generated after the towers are placed, into whatever open land is left next to a
road. They are laid perpendicular to the road edge (90-degree bays, the layout SP:21
sizes the 6.0 m aisle for), walking along the road boundary at one stall pitch and
keeping every stall that fits clear of buildings, amenities and other stalls.
"""
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from shapely.affinity import rotate, translate
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import prep

from .config import SiteLayoutConfig
from .frame import polygons_of


@dataclass
class ParkingBay:
    polygon: Polygon
    cx: float
    cy: float
    rotation_deg: float


def _stall(cx: float, cy: float, w: float, d: float, angle_deg: float) -> Polygon:
    return translate(rotate(box(-w / 2, -d / 2, w / 2, d / 2), angle_deg, origin=(0, 0)), cx, cy)


def generate_bays(roads: BaseGeometry, available: BaseGeometry,
                  cfg: SiteLayoutConfig) -> List[ParkingBay]:
    """Perpendicular stalls along both faces of the road network.

    `available` is the land a stall may occupy — the packable region minus towers and
    amenities — so bays can never overlap a building by construction.
    """
    p = cfg.parking
    if not p.enabled or roads is None or roads.is_empty or available.is_empty:
        return []

    stall_w, stall_d = p.stall_width, p.stall_depth
    avail_prep = prep(available)
    placed: List[Polygon] = []
    placed_union: Optional[BaseGeometry] = None
    bays: List[ParkingBay] = []

    # The perimeter ring is an annulus: its *interior* boundary is the one facing the
    # buildable core, so walking only the exterior put every candidate stall out in the
    # setback where nothing can go. Both faces of every road get walked.
    faces = []
    for road in polygons_of(roads):
        faces.append((road, road.exterior))
        faces.extend((road, hole) for hole in road.interiors)

    for road, ring in faces:
        length = ring.length
        if length < stall_w:
            continue
        steps = int(length // stall_w)
        for i in range(steps):
            s = i * stall_w
            a = ring.interpolate(s)
            b = ring.interpolate(min(s + stall_w, length))
            tx, ty = b.x - a.x, b.y - a.y
            mag = math.hypot(tx, ty)
            if mag < 1e-9:
                continue
            tx, ty = tx / mag, ty / mag
            # Outward from the road face is whichever normal leaves the road polygon.
            for nx, ny in ((-ty, tx), (ty, -tx)):
                mid_x, mid_y = (a.x + b.x) / 2.0, (a.y + b.y) / 2.0
                cx = mid_x + nx * (stall_d / 2.0 + p.kerb_offset)
                cy = mid_y + ny * (stall_d / 2.0 + p.kerb_offset)
                if road.contains(Point(cx, cy)):
                    continue    # that side points back into the carriageway
                stall = _stall(cx, cy, stall_w, stall_d, math.degrees(math.atan2(ty, tx)))
                if not avail_prep.contains(stall):
                    continue
                if placed_union is not None and stall.intersects(placed_union):
                    continue
                placed.append(stall)
                placed_union = unary_union(placed) if len(placed) % 24 == 0 else (
                    placed_union.union(stall) if placed_union is not None else stall)
                bays.append(ParkingBay(polygon=stall, cx=cx, cy=cy,
                                       rotation_deg=math.degrees(math.atan2(ty, tx))))
    return bays


def bays_geometry(bays: Sequence[ParkingBay]) -> BaseGeometry:
    return unary_union([b.polygon for b in bays]) if bays else Polygon()
