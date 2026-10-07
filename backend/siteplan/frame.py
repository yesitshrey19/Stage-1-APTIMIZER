"""Local tangent-plane projection: lat/lng <-> metres.

Everything below this module works in plain metres, so every geometry operation is
planar Euclidean and the layout engine is testable with hand-written squares and
L-shapes. Nothing under `siteplan/` except this file ever sees a lat/lng.

The scale constants match `frontend/src/lib/scene.js` (R_LAT / R_LNG) so the Leaflet map,
the Three.js scene and the layout engine all share one frame. Over a plot of a few
hundred metres the equirectangular error is millimetres — far below any setback
tolerance — so pyproj/GDAL would buy nothing here.

Axis convention: +x east, +y NORTH (mathematical). `scene.js` uses +z SOUTH, so the 3D
consumer negates y. Keeping the maths convention here means signed area is positive for
counter-clockwise rings, which is what the outward-normal logic in envelope.py assumes.
"""
import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

R_LAT = 110540.0
R_LNG = 111320.0


@dataclass(frozen=True)
class LocalFrame:
    """Equirectangular projection about a fixed origin."""
    lat0: float
    lng0: float

    @classmethod
    def from_coords(cls, coords: Sequence[Sequence[float]]) -> "LocalFrame":
        n = len(coords)
        if not n:
            raise ValueError("cannot build a frame from an empty coordinate list")
        return cls(sum(c[0] for c in coords) / n, sum(c[1] for c in coords) / n)

    @property
    def _k(self) -> float:
        return math.cos(math.radians(self.lat0))

    def to_local(self, lat: float, lng: float) -> Tuple[float, float]:
        return ((lng - self.lng0) * R_LNG * self._k, (lat - self.lat0) * R_LAT)

    def to_latlng(self, x: float, y: float) -> List[float]:
        return [round(self.lat0 + y / R_LAT, 8),
                round(self.lng0 + x / (R_LNG * self._k), 8)]

    def ring_to_local(self, coords: Iterable[Sequence[float]]) -> List[Tuple[float, float]]:
        return [self.to_local(c[0], c[1]) for c in coords]

    def ring_to_latlng(self, points: Iterable[Sequence[float]]) -> List[List[float]]:
        return [self.to_latlng(p[0], p[1]) for p in points]

    @property
    def origin(self) -> List[float]:
        return [self.lat0, self.lng0]


def polygons_of(geom: BaseGeometry) -> List[Polygon]:
    """Flatten any geometry to its polygonal parts, largest first. Empty for non-areal."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        parts = [geom]
    elif isinstance(geom, MultiPolygon):
        parts = list(geom.geoms)
    elif hasattr(geom, "geoms"):  # GeometryCollection from a messy difference
        parts = [g for g in geom.geoms if isinstance(g, Polygon)]
    else:
        return []
    return sorted((p for p in parts if not p.is_empty and p.area > 0),
                  key=lambda p: p.area, reverse=True)


def geom_to_latlng(geom: BaseGeometry, frame: LocalFrame) -> List[List[List[List[float]]]]:
    """Shapely geometry -> Leaflet-ready rings.

    Returns one entry per polygon part; each entry is [exterior_ring, *hole_rings] and
    each ring is a list of [lat, lng]. Leaflet's `Polygon positions` accepts exactly
    this nesting, so the renderer needs no conversion of its own.
    """
    out: List[List[List[List[float]]]] = []
    for poly in polygons_of(geom):
        rings = [frame.ring_to_latlng(poly.exterior.coords[:-1])]
        rings += [frame.ring_to_latlng(r.coords[:-1]) for r in poly.interiors]
        out.append(rings)
    return out


def geom_to_local(geom: BaseGeometry) -> List[List[List[List[float]]]]:
    """Same shape as `geom_to_latlng` but in metres — used by tests and debugging."""
    out: List[List[List[List[float]]]] = []
    for poly in polygons_of(geom):
        rings = [[[round(x, 3), round(y, 3)] for x, y in poly.exterior.coords[:-1]]]
        rings += [[[round(x, 3), round(y, 3)] for x, y in r.coords[:-1]] for r in poly.interiors]
        out.append(rings)
    return out
