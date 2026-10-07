// Reads a plot boundary from a Google Earth KML or a GeoJSON file.
//
// Returns { coordinates: [[lat, lng], ...], name, polygons } with the largest polygon in
// the file, or throws an Error with a message fit to show the user. Coordinates come back
// in the app's [lat, lng] order; both formats store [lng, lat].

const ringArea = (ring) => {
  // Shoelace on a local equirectangular projection -- only used to pick the largest ring.
  const lat0 = ring.reduce((s, p) => s + p[0], 0) / ring.length;
  const kx = 111320 * Math.cos((lat0 * Math.PI) / 180);
  let a = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const [y1, x1] = ring[i];
    const [y2, x2] = ring[(i + 1) % ring.length];
    a += x1 * kx * y2 * 110540 - x2 * kx * y1 * 110540;
  }
  return Math.abs(a) / 2;
};

const tidy = (ring) => {
  const pts = ring.filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (pts.length > 1 && pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1]) pts.pop();
  return pts.map(([lat, lng]) => [Number(lat.toFixed(7)), Number(lng.toFixed(7))]);
};

function fromKml(text) {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.getElementsByTagName("parsererror").length) throw new Error("This KML file could not be read.");
  const polygons = [];
  const placemarks = Array.from(doc.getElementsByTagNameNS("*", "Placemark"));
  const scopes = placemarks.length ? placemarks : [doc];
  scopes.forEach((pm) => {
    const name = pm.getElementsByTagNameNS?.("*", "name")?.[0]?.textContent?.trim() || "";
    Array.from(pm.getElementsByTagNameNS("*", "outerBoundaryIs")).forEach((ob) => {
      const raw = ob.getElementsByTagNameNS("*", "coordinates")[0]?.textContent || "";
      const ring = raw.trim().split(/\s+/).map((tok) => {
        const [lng, lat] = tok.split(",").map(Number);
        return [lat, lng];
      });
      polygons.push({ name, ring: tidy(ring) });
    });
    // A boundary saved as a path (LineString) rather than a polygon is still a boundary.
    if (!pm.getElementsByTagNameNS("*", "outerBoundaryIs").length) {
      Array.from(pm.getElementsByTagNameNS("*", "LineString")).forEach((ls) => {
        const raw = ls.getElementsByTagNameNS("*", "coordinates")[0]?.textContent || "";
        const ring = raw.trim().split(/\s+/).map((tok) => {
          const [lng, lat] = tok.split(",").map(Number);
          return [lat, lng];
        });
        polygons.push({ name, ring: tidy(ring) });
      });
    }
  });
  return polygons;
}

function fromGeoJson(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error("This GeoJSON file is not valid JSON.");
  }
  const features = data.type === "FeatureCollection" ? data.features
    : data.type === "Feature" ? [data] : [{ geometry: data, properties: {} }];
  const polygons = [];
  (features || []).forEach((f) => {
    const g = f?.geometry || {};
    const name = f?.properties?.name || "";
    const rings = g.type === "Polygon" ? [g.coordinates?.[0]]
      : g.type === "MultiPolygon" ? (g.coordinates || []).map((p) => p?.[0]) : [];
    rings.filter(Boolean).forEach((r) => polygons.push({ name, ring: tidy(r.map(([lng, lat]) => [lat, lng])) }));
  });
  return polygons;
}

export async function readBoundaryFile(file) {
  const ext = (file.name.split(".").pop() || "").toLowerCase();
  if (ext === "kmz") {
    throw new Error("KMZ is a zipped file. In Google Earth, right-click the polygon → Save Place As… → choose KML.");
  }
  const text = await file.text();
  const polygons = ext === "kml" || text.trimStart().startsWith("<") ? fromKml(text) : fromGeoJson(text);
  const usable = polygons.filter((p) => p.ring.length >= 3);
  if (!usable.length) {
    throw new Error("No polygon found. Draw the plot with Google Earth's Add Polygon tool, then save it as KML.");
  }
  const best = usable.reduce((a, b) => (ringArea(b.ring) > ringArea(a.ring) ? b : a));
  const bad = best.ring.some(([lat, lng]) => Math.abs(lat) > 90 || Math.abs(lng) > 180);
  if (bad) throw new Error("The boundary's coordinates are out of range — is the file in latitude/longitude (WGS84)?");
  return { coordinates: best.ring, name: best.name, polygons: usable.length, areaSqm: ringArea(best.ring) };
}
