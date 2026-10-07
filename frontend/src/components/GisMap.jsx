import { useState } from "react";
import { MapContainer, TileLayer, Polygon, Polyline, CircleMarker, LayersControl, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";

/**
 * OSM keeps areas and centrelines in one feature list. A lake, pond or wetland is a
 * closed ring; a river, stream, canal or storm drain is a CENTRELINE. Leaflet closes
 * whatever ring it is handed, so drawing a centreline as a Polygon painted a filled
 * "water body" across everything the line happened to bound — in practice the plot the
 * user is building on, and the buildings already standing on it. The renderer is chosen
 * from the geometry, never from the layer name.
 */
const LAYERS = [
  ["buildings", "Buildings", "#64748B", 0.45],
  ["roads", "Roads", "#F59E0B", 0],
  ["water_lines", "Waterways", "#0284C7", 0],
  ["green", "Green areas", "#16A34A", 0.3],
  ["water", "Water bodies", "#0EA5E9", 0.45],
  ["transit", "Transit stops", "#7C3AED", 0.9],
];

/** The feature's geometry role. Falls back to reading the ring itself, because an
 *  analysis saved before `geom` existed is still rendered from the project document. */
export const geometryRole = (feature = {}) => {
  if (feature.geom) return feature.geom;
  const g = feature.geometry || [];
  if (g.length === 1) return "point";
  const first = g[0];
  const last = g[g.length - 1];
  return first && last && first[0] === last[0] && first[1] === last[1] ? "area" : "line";
};

/** Drainage and sewage channels are infrastructure, not water: a storm drain running
 *  through a built-up block is not a water body on the site. The backend no longer
 *  classifies them, but an analysis saved before that change still carries them — so
 *  they are dropped here too rather than left on the map until someone re-runs it. */
const ENGINEERED_KINDS = new Set(["drain", "ditch"]);

/** Water rings (lakes, ponds, wetlands) split from water centrelines (rivers, streams,
 *  canals), so the map can draw each correctly and let them be toggled apart. */
export const splitWater = (water = []) => {
  const natural = water.filter((f) => !ENGINEERED_KINDS.has(String(f.kind || "").toLowerCase()));
  return {
    water: natural.filter((f) => geometryRole(f) !== "line"),
    water_lines: natural.filter((f) => geometryRole(f) === "line"),
  };
};

export const GisMap = ({ coordinates = [], features = {}, height = 460 }) => {
  const [visible, setVisible] = useState({
    buildings: true, roads: true, water_lines: true, green: true, water: true, transit: true,
  });
  const center = coordinates.length
    ? [
        coordinates.reduce((s, c) => s + c[0], 0) / coordinates.length,
        coordinates.reduce((s, c) => s + c[1], 0) / coordinates.length,
      ]
    : [12.9716, 77.5946];

  // The backend splits water itself; this merge keeps the toggle counts right even when
  // it does not (an older saved analysis, or a fixture in a test).
  const groups = { ...features, ...splitWater(features.water) };

  return (
    <div className="relative">
      <div className="absolute z-[500] top-2 left-2 bg-white border border-slate-200 rounded-sm p-2 space-y-1" data-testid="gis-layer-toggles">
        {LAYERS.map(([key, label, color]) => (
          <label key={key} className="flex items-center gap-2 text-[11px] cursor-pointer">
            <input
              type="checkbox"
              checked={visible[key]}
              data-testid={`gis-layer-${key}`}
              onChange={(e) => setVisible((v) => ({ ...v, [key]: e.target.checked }))}
            />
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
            {label}
            <span className="font-mono text-slate-400">{(groups[key] || []).length}</span>
          </label>
        ))}
      </div>
      {/* OpenStreetMap is the default here for the same reason it is the default on the
          plot map: the boundary is drawn on it. Satellite imagery in many Indian cities
          is offset from WGS84, so overlaying OSM data on it makes the analysis look
          tens of metres adrift when nothing in the data is wrong. */}
      <MapContainer center={center} zoom={17} style={{ height, width: "100%" }} className="rounded-sm z-0">
        <LayersControl position="topright">
          <LayersControl.BaseLayer checked name="OpenStreetMap">
            <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="&copy; OpenStreetMap" />
          </LayersControl.BaseLayer>
          <LayersControl.BaseLayer name="Satellite">
            <TileLayer
              url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
              attribution="Tiles &copy; Esri"
            />
          </LayersControl.BaseLayer>
        </LayersControl>

        {LAYERS.map(([key, , color, fill]) =>
          visible[key]
            ? (groups[key] || []).slice(0, 500).map((f) => {
                const role = geometryRole(f);
                if (key === "transit" || role === "point") {
                  return (
                    <CircleMarker key={`${key}-${f.id}`} center={f.geometry[0]} radius={5}
                      pathOptions={{ color, fillColor: color, fillOpacity: fill }}>
                      <Tooltip>{`${f.name || f.kind} · ${f.distance_m} m`}</Tooltip>
                    </CircleMarker>
                  );
                }
                if (key === "roads" || role === "line") {
                  return (
                    <Polyline key={`${key}-${f.id}`} positions={f.geometry}
                      pathOptions={{ color, weight: key === "roads" ? 3 : 2.5, opacity: 0.85 }}>
                      <Tooltip>
                        {key === "roads"
                          ? `${f.name || f.kind} · ${f.road_width_m || "?"} m wide · ${f.distance_m} m away`
                          : `${f.name || f.kind} (centreline) · ${f.distance_m} m away`}
                      </Tooltip>
                    </Polyline>
                  );
                }
                return (
                  <Polygon key={`${key}-${f.id}`} positions={f.geometry}
                    pathOptions={{ color, weight: 1, fillOpacity: fill }}>
                    <Tooltip>{`${f.name || f.kind} · ${f.distance_m} m away`}</Tooltip>
                  </Polygon>
                );
              })
            : null
        )}

        {coordinates.length >= 3 && (
          <Polygon positions={coordinates} pathOptions={{ color: "#2563EB", weight: 3, fillOpacity: 0.12 }}>
            <Tooltip>Project plot boundary</Tooltip>
          </Polygon>
        )}
      </MapContainer>
    </div>
  );
};
