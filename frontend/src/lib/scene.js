/** Geometry helpers shared by the 3D scene. Converts the stored lat/lng plot polygon
 *  (Plot Management) into local metres so towers, terrain and GIS features share one frame. */

export const R_LAT = 110540;
export const R_LNG = 111320;

export const originOf = (coords) => [
  coords.reduce((s, c) => s + c[0], 0) / coords.length,
  coords.reduce((s, c) => s + c[1], 0) / coords.length,
];

/** [lat, lng] -> [x (east, m), z (south, m)] */
export const toLocal = ([lat, lng], origin) => {
  const k = Math.cos((origin[0] * Math.PI) / 180);
  return [(lng - origin[1]) * R_LNG * k, -(lat - origin[0]) * R_LAT];
};

export const polyToLocal = (coords, origin) => coords.map((c) => toLocal(c, origin));

export const localBounds = (pts) => {
  const xs = pts.map((p) => p[0]);
  const zs = pts.map((p) => p[1]);
  return {
    minX: Math.min(...xs), maxX: Math.max(...xs),
    minZ: Math.min(...zs), maxZ: Math.max(...zs),
    width: Math.max(...xs) - Math.min(...xs),
    depth: Math.max(...zs) - Math.min(...zs),
    cx: (Math.min(...xs) + Math.max(...xs)) / 2,
    cz: (Math.min(...zs) + Math.max(...zs)) / 2,
  };
};

/** Towers from the site layout engine (backend `siteplan`), which guarantees every
 *  footprint lies inside the setback envelope and clear of roads and amenities.
 *
 *  The engine works in +x east / +y north metres about the plot centroid; the scene uses
 *  +x east / +z SOUTH about the same centroid, so z = -y. Rather than reason about the
 *  rotation sign across that flip, the footprint angle and side lengths are measured
 *  straight off the engine's own polygon after conversion — self-consistent by
 *  construction, whichever way the frames happen to relate. */
/** Engine version this build of the frontend understands. Must match backend
 *  siteplan/version.py — a layout stamped with anything else was produced by different
 *  geometry code and is not safe to render against the current boundary. */
export const ENGINE_VERSION = 4;

/** Digest of a plot ring.
 *
 *  NOT interchangeable with backend siteplan.version.polygon_signature: both hash the same
 *  rounded body string, but the backend uses SHA-1 and this uses FNV-1a, so the two digests
 *  never agree. That is why a layout is stamped with `_client_signature` computed HERE on
 *  the way in — comparing the engine's own stamp against this function would report every
 *  layout as belonging to a different plot.
 */
export const polygonSignature = (coords = []) => {
  if (!coords.length) return "";
  // FNV-1a over the same rounded body the backend hashes; we only need "same or not",
  // so a short non-cryptographic digest compared against a recomputed one is enough.
  const body = coords.map(([a, b]) => `${Number(a).toFixed(8)},${Number(b).toFixed(8)}`).join("|");
  let h = 0x811c9dc5;
  for (let i = 0; i < body.length; i += 1) {
    h ^= body.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16);
};

/** Is a stored layout still a description of this plot, from this engine?
 *
 *  This used to take `coords` and never read it, returning true for any layout that had
 *  towers — so the stale-layout warning it feeds could not fire, and a boundary edited
 *  after a layout was packed went on rendering the old towers as though they belonged to
 *  the new plot. Both stamps are now compared, which is what version.py's own docstring
 *  says consumers do.
 */
export const isLayoutCurrent = (siteLayout, coords = []) => {
  if (!siteLayout) return false;
  const hasContent =
    (Array.isArray(siteLayout.towers) && siteLayout.towers.length > 0)
    || !!siteLayout.roads
    || !!siteLayout.envelope;
  if (!hasContent) return false;

  // Geometry produced by a different build of the engine is not safe to draw against the
  // current boundary, whatever plot it was packed for.
  if (siteLayout.engine_version !== undefined && siteLayout.engine_version !== ENGINE_VERSION)
    return false;

  // A layout stamped on the way in can be checked against the boundary as it stands now.
  // One saved before stamping existed carries no stamp, and an unanswerable question is
  // not evidence of staleness — those keep rendering rather than nagging forever.
  const stamped = siteLayout._client_signature;
  if (!stamped) return true;
  return stamped === polygonSignature(coords);
};

export const engineTowerLayout = (siteLayout, projectTowers = []) => {
  if (!siteLayout) return null;                 // no layout at all -> caller may fall back
  const towers = siteLayout.towers || [];
  // A layout that packed ZERO towers is a real answer, not a missing one: the engine
  // decided nothing fits inside the setback envelope. Returning null here made the caller
  // fall through to the unconstrained fallback, so the app answered "nothing fits" by
  // drawing buildings outside the plot. An empty array keeps that distinction.
  if (!towers.length) return [];
  const byName = new Map(projectTowers.map((t) => [t.name, t]));

  return towers.map((t, i) => {
    const ring = (t.polygons_local?.[0]?.[0] || []).map(([x, y]) => [x, -y]); // -> scene x,z
    let rotationY = 0;
    let w = t.width_m;
    let d = t.depth_m;
    if (ring.length >= 3) {
      const [dx, dz] = [ring[1][0] - ring[0][0], ring[1][1] - ring[0][1]];
      // three.js rotation-y maps local +X to (cos φ, -sin φ) in (x, z).
      rotationY = Math.atan2(-dz, dx);
      w = Math.hypot(dx, dz);
      d = Math.hypot(ring[2][0] - ring[1][0], ring[2][1] - ring[1][1]);
    }
    // Carry the user's unit mix / rooms across when a project tower shares the name, so
    // the detailed and floor-plan views keep working on an engine-generated layout.
    const src = byName.get(t.name) || projectTowers[i] || {};
    return {
      id: `engine-${i}`,
      name: t.name,
      x: t.centre_local[0],
      z: -t.centre_local[1],
      rotationY,
      w,
      d,
      floors: t.floors,
      floorHeight: t.floor_height_m,
      height: t.height_m,
      units: src.units || [],
      rooms: src.rooms || [],
      commonArea: Number(src.common_area) || 0,
      shape: src.shape || "curved",
      penthouses: Number(src.penthouses) || 0,
      fromEngine: true,
    };
  });
};

/** Engine polygons (+y north metres) -> scene polygons (+z south metres).
 *
 *  A polygon is a LIST OF RINGS: the outer boundary followed by any holes. This used to
 *  return `rings[0]` alone, which is why the perimeter access road never appeared as a
 *  road — a ring road is an annulus, and dropping its hole turns it into a solid slab
 *  covering the whole site instead of a band around the edge.
 */
const toScenePolys = (polys) =>
  (polys || []).map((rings) => (rings || []).map((ring) => ring.map(([x, y]) => [x, -y])));

/** Outer boundary only, for shapes that genuinely have no holes. */
const toSceneRings = (polys) => toScenePolys(polys).map((rings) => rings[0] || []);

/** Centre, side lengths and Y rotation of a 4-point ring, measured off the ring itself. */
const rectFromRing = (ring) => {
  const n = ring.length;
  const cx = ring.reduce((s, p) => s + p[0], 0) / n;
  const cz = ring.reduce((s, p) => s + p[1], 0) / n;
  const [dx, dz] = [ring[1][0] - ring[0][0], ring[1][1] - ring[0][1]];
  return {
    x: cx,
    z: cz,
    rotationY: Math.atan2(-dz, dx),
    w: Math.hypot(dx, dz),
    d: Math.hypot(ring[2][0] - ring[1][0], ring[2][1] - ring[1][1]),
  };
};


export const spineTowerLayout = (projectTowers = [], poly = [], bounds = null) => {
  if (!projectTowers.length) return [];
  const b = bounds || localBounds(poly);
  const cx = b.cx;
  const cz = b.cz;
  const spineDistX = Math.min(b.width * 0.22, 26);
  const towerSpacingZ = Math.min(b.depth * 0.26, 36);

  return projectTowers.map((t, i) => {
    const isEast = i % 2 === 0;
    const pairIndex = Math.floor(i / 2);
    const zOffset = (pairIndex - 0.5) * towerSpacingZ;
    const xPos = isEast ? cx + spineDistX : cx - spineDistX;
    const zPos = cz - 10 + zOffset;

    return {
      id: t.id || `tower-${i}`,
      name: t.name,
      x: xPos,
      z: zPos,
      rotationY: 0,
      w: t.width_m || 24,
      d: t.depth_m || 16,
      floors: t.floors || 14,
      floorHeight: t.floor_height || 3.0,
      height: t.height_m || (t.floors || 14) * 3.0,
      units: t.units || [],
      rooms: t.rooms || [],
      commonArea: Number(t.common_area) || 0,
      shape: t.shape || "curved",
      penthouses: Number(t.penthouses) || 0,
      fromEngine: false,
    };
  });
};

/** Reserved roads and amenity blocks in scene coordinates, for the 3D site view.
 *
 *  Returns null when there is no ENGINE layout. It used to fabricate roads, a park and
 *  a clubhouse cut from the plot's axis-aligned BOUNDING BOX — geometry that runs off the
 *  site on any non-rectangular boundary, presented next to a banner claiming the interim
 *  layout has "no access roads". Fabricated site furniture is worse than none; the
 *  fallback exists for towers (better than an empty plot while the engine runs), not for
 *  roads that would be drawn where a real road could never be built.
 */
export const engineSiteShapes = (siteLayout) => {
  const hasRoads =
    siteLayout?.roads?.ring_polygons_local?.length ||
    siteLayout?.roads?.driveway_polygons_local?.length;

  if (hasRoads) {
    const amenities = (siteLayout?.amenities || [])
      .map((a) => {
        const ring = toSceneRings(a.polygons_local)[0] || [];
        const cx = ring.length
          ? ring.reduce((s, p) => s + p[0], 0) / ring.length
          : a.centre_local
          ? a.centre_local[0]
          : 0;
        const cz = ring.length
          ? ring.reduce((s, p) => s + p[1], 0) / ring.length
          : a.centre_local
          ? -a.centre_local[1]
          : 0;
        const w = a.width_m || (ring.length >= 2 ? Math.hypot(ring[1][0] - ring[0][0], ring[1][1] - ring[0][1]) : 24);
        const d = a.depth_m || (ring.length >= 3 ? Math.hypot(ring[2][0] - ring[1][0], ring[2][1] - ring[1][1]) : 16);
        return {
          key: a.key,
          name: a.name,
          height: a.height_m || 10.5,
          floors: a.floors || 3,
          area: a.area_sqm,
          x: cx,
          z: cz,
          w,
          d,
          rotationY: 0,
        };
      })
      .filter(Boolean);

    // No invented oval here: if the engine reserved no landscaped area, drawing a park
    // in the middle of the site would paint green over whatever it actually built.
    const green = toSceneRings(siteLayout?.green?.polygons_local);

    return {
      amenities,
      ring: toScenePolys(siteLayout?.roads?.ring_polygons_local),
      driveways: toScenePolys(siteLayout?.roads?.driveway_polygons_local),
      ringCentrelines: (siteLayout?.roads?.corridors || [])
        .filter((c) => c.kind === "ring")
        .map((c) => (c.centreline_local || []).map(([x, y]) => [x, -y])),
      drivewayCentrelines: (siteLayout?.roads?.corridors || [])
        .filter((c) => c.kind === "spine")
        .map((c) => (c.centreline_local || []).map(([x, y]) => [x, -y])),
      pedestrianLinks: (siteLayout?.pedestrian_links || [])
        .filter((link) => link.path_local?.length >= 2)
        .map((link) => ({
          label: link.label,
          points: link.path_local.map(([x, y]) => [x, -y]),
        })),
      bays: toSceneRings(siteLayout?.surface_parking?.polygons_local),
      green,
      // Area of the reserved landscaped space, so the panel names the pink region on
      // screen rather than a different number that also happens to be called Park.
      parkArea: siteLayout?.green?.area_sqm ?? null,
    };
  }

  return null;
};


/* ---------------------------------------------------------------- containment
 * The fallback layout used to grid towers into the plot's axis-aligned BOUNDING BOX,
 * which puts a footprint outside the real boundary on any plot that is not a rectangle.
 * These helpers make containment testable so the fallback can be constrained instead.
 */

/** Ray casting on the scene-space ring [[x, z], ...]. */
export const pointInPolygon = ([x, z], poly) => {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i, i += 1) {
    const [xi, zi] = poly[i];
    const [xj, zj] = poly[j];
    if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi || 1e-12) + xi) inside = !inside;
  }
  return inside;
};

const cross = (ax, az, bx, bz) => ax * bz - az * bx;

/** Proper segment intersection, used to catch a footprint straddling a concave notch. */
export const segmentsIntersect = (p1, p2, p3, p4) => {
  const d1 = cross(p4[0] - p3[0], p4[1] - p3[1], p1[0] - p3[0], p1[1] - p3[1]);
  const d2 = cross(p4[0] - p3[0], p4[1] - p3[1], p2[0] - p3[0], p2[1] - p3[1]);
  const d3 = cross(p2[0] - p1[0], p2[1] - p1[1], p3[0] - p1[0], p3[1] - p1[1]);
  const d4 = cross(p2[0] - p1[0], p2[1] - p1[1], p4[0] - p1[0], p4[1] - p1[1]);
  return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0));
};

/** Is an axis-aligned w x d footprint centred at (cx, cz) wholly inside `poly`?
 *  Corners inside is not sufficient on a concave plot -- a rectangle can bridge a notch
 *  with every corner in the polygon -- so the edges are tested for crossings too. */
export const rectInsidePolygon = (cx, cz, w, d, poly) => {
  const hw = w / 2;
  const hd = d / 2;
  const corners = [[cx - hw, cz - hd], [cx + hw, cz - hd], [cx + hw, cz + hd], [cx - hw, cz + hd]];
  if (!corners.every((pt) => pointInPolygon(pt, poly))) return false;
  for (let i = 0; i < 4; i += 1) {
    const a = corners[i];
    const b = corners[(i + 1) % 4];
    for (let j = 0, k = poly.length - 1; j < poly.length; k = j, j += 1) {
      if (segmentsIntersect(a, b, poly[k], poly[j])) return false;
    }
  }
  return true;
};

const overlaps = (a, b, gap) =>
  Math.abs(a.x - b.x) < (a.w + b.w) / 2 + gap && Math.abs(a.z - b.z) < (a.d + b.d) / 2 + gap;

/** Fallback layout, used only until the site layout engine has run for this plot.
 *
 *  Unlike the bounding-box grid it replaces, every footprint here is verified to sit
 *  wholly inside the plot ring before it is placed. A tower that cannot be fitted is
 *  returned in `dropped` rather than being drawn somewhere wrong -- showing nothing is
 *  honest, showing a building outside the site is not.
 */
export const constrainedTowerLayout = (towers, poly, bounds) => {
  const gap = 6;
  const step = Math.max(Math.min(bounds.width, bounds.depth) / 40, 2);
  const placed = [];
  const dropped = [];

  const candidates = [];
  for (let z = bounds.minZ + step; z <= bounds.maxZ - step; z += step) {
    for (let x = bounds.minX + step; x <= bounds.maxX - step; x += step) {
      if (pointInPolygon([x, z], poly)) candidates.push([x, z]);
    }
  }
  // Centre-out, so towers cluster in the buildable middle instead of hugging an edge.
  const cx0 = (bounds.minX + bounds.maxX) / 2;
  const cz0 = (bounds.minZ + bounds.maxZ) / 2;
  candidates.sort((a, b) => (a[0] - cx0) ** 2 + (a[1] - cz0) ** 2 - ((b[0] - cx0) ** 2 + (b[1] - cz0) ** 2));

  // Biggest first: a large footprint has the fewest legal positions, so placing it last
  // tends to leave it homeless even when a valid arrangement exists.
  const order = towers
    .map((t, i) => ({ t, i, area: Math.max(Number(t.footprint_area) || 400, 40) }))
    .sort((a, b) => b.area - a.area);

  order.forEach(({ t, i, area }) => {
    const floors = Math.max(Number(t.floors) || 1, 1);
    const fh = Number(t.floor_height) || 3;
    let put = null;
    // Shrink toward the plot if the declared footprint simply will not fit anywhere.
    for (const scale of [1, 0.85, 0.7, 0.55, 0.4]) {
      const side = Math.sqrt(area) * scale;
      const spot = candidates.find(
        ([x, z]) =>
          rectInsidePolygon(x, z, side, side, poly) &&
          !placed.some((q) => overlaps({ x, z, w: side, d: side }, q, gap))
      );
      if (spot) {
        put = { x: spot[0], z: spot[1], w: side, d: side, scale };
        break;
      }
    }
    if (!put) {
      dropped.push(t.name || `Tower ${i + 1}`);
      return;
    }
    placed.push({
      id: t.id, name: t.name, x: put.x, z: put.z, w: put.w, d: put.d,
      floors, floorHeight: fh, height: floors * fh,
      units: t.units || [], rooms: t.rooms || [],
      commonArea: Number(t.common_area) || 0,
      shape: t.shape || "curved",
      penthouses: Number(t.penthouses) || 0,
      order: i, shrunkTo: put.scale < 1 ? put.scale : null,
    });
  });

  placed.sort((a, b) => a.order - b.order);
  placed.dropped = dropped;
  return placed;
};

export const UNIT_COLORS = {
  studio: "#94A3B8",
  "1bhk": "#60A5FA",
  "2bhk": "#2563EB",
  "3bhk": "#F59E0B",
  "4bhk": "#EA580C",
  penthouse: "#7C3AED",
  custom: "#0F172A",
};

export const ROOM_COLORS = {
  living: "#93C5FD",
  bedroom: "#A5B4FC",
  kitchen: "#FCD34D",
  bathroom: "#6EE7B7",
  balcony: "#CBD5E1",
  utility: "#FDBA74",
  common: "#E2E8F0",
};

/** Sun direction vector from azimuth (deg from north, clockwise) and elevation (deg). */
export const sunVector = (azimuth, elevation, dist = 240) => {
  const a = (azimuth * Math.PI) / 180;
  const e = (Math.max(elevation, 1) * Math.PI) / 180;
  return [Math.sin(a) * Math.cos(e) * dist, Math.sin(e) * dist, -Math.cos(a) * Math.cos(e) * dist];
};

/** Interpolate the stored equinox/solstice sun path for an hour of day. */
export const sunAtHour = (sun, hour, pathKey = "equinox") => {
  const path = sun?.paths?.find((p) => p.key === pathKey) || sun?.paths?.[0];
  if (!path || !path.points.length) {
    const el = Math.max(4, 70 - Math.abs(hour - 12) * 9);
    return { azimuth: 90 + (hour - 6) * 15, elevation: el };
  }
  const pts = path.points;
  if (hour <= pts[0].hour) return pts[0];
  if (hour >= pts[pts.length - 1].hour) return pts[pts.length - 1];
  for (let i = 1; i < pts.length; i += 1) {
    if (pts[i].hour >= hour) {
      const a = pts[i - 1];
      const b = pts[i];
      const t = (hour - a.hour) / (b.hour - a.hour || 1);
      return { azimuth: a.azimuth + (b.azimuth - a.azimuth) * t, elevation: a.elevation + (b.elevation - a.elevation) * t };
    }
  }
  return pts[pts.length - 1];
};

/** Grid of terrain heights (metres relative to plot mean) from GIS elevation samples. */
export const terrainHeights = (gis, origin, bounds, seg = 24) => {
  const samples = gis?.terrain?.available ? gis.terrain.samples : [];
  if (!samples.length) return { heights: null, relief: 0 };
  const mean = gis.terrain.mean_m;
  const local = samples.map((s) => {
    const [x, z] = toLocal([s.lat, s.lng], origin);
    return { x, z, h: s.elevation_m - mean };
  });
  const heights = [];
  for (let i = 0; i <= seg; i += 1) {
    for (let j = 0; j <= seg; j += 1) {
      const x = bounds.minX + (bounds.width * j) / seg;
      const z = bounds.minZ + (bounds.depth * i) / seg;
      let wsum = 0;
      let vsum = 0;
      local.forEach((s) => {
        const d2 = (s.x - x) ** 2 + (s.z - z) ** 2 + 1;
        const w = 1 / d2;
        wsum += w;
        vsum += w * s.h;
      });
      heights.push(wsum ? vsum / wsum : 0);
    }
  }
  return { heights, relief: gis.terrain.relief_m, seg };
};

/** Is this feature a centreline rather than an area? A river, stream, canal or drain is
 *  mapped as an open line; a lake, pond or building is a closed ring. Reads `geom` when
 *  the backend supplied it and falls back to the ring itself for older analyses. */
export const isLinearFeature = (f = {}) => {
  if (f.geom) return f.geom === "line";
  const g = f.geometry || [];
  if (g.length < 3) return true;
  const first = g[0];
  const last = g[g.length - 1];
  return !(first && last && first[0] === last[0] && first[1] === last[1]);
};

/** Feature footprints (buildings/green/water) converted to local metres.
 *
 *  Centrelines are dropped: a drain or a river has no footprint, and closing its line
 *  into a ring put a solid block of "water" through the middle of the massing model. */
export const featureShapes = (gis, origin, key, limit = 60) =>
  (gis?.features?.[key] || [])
    .filter((f) => !isLinearFeature(f))
    .slice(0, limit)
    .map((f) => {
      const pts = f.geometry.map((g) => toLocal(g, origin));
      const b = localBounds(pts);
      return { id: f.id, name: f.name || f.kind, pts, ...b, distance: f.distance_m };
    });
