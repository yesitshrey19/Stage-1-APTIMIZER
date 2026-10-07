import { useState } from "react";
import { MapContainer, TileLayer, Polygon, Marker, Polyline, LayersControl, Tooltip, useMapEvents } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Button } from "../components/ui/button";
import { MousePointer2, Pencil, Trash2, Undo2 } from "lucide-react";

const vertexIcon = L.divIcon({
  className: "",
  html: '<div style="width:12px;height:12px;border-radius:50%;background:#2563EB;border:2px solid #fff;box-shadow:0 0 0 1px #0F172A"></div>',
  iconSize: [12, 12],
  iconAnchor: [6, 6],
});

const ClickCatcher = ({ active, onAdd }) => {
  useMapEvents({
    click(e) {
      if (active) onAdd([e.latlng.lat, e.latlng.lng]);
    },
  });
  return null;
};

/**
 * Extra styled geometry drawn over the plot — the site layout engine's output.
 * Each overlay is { key, polygons, style }, where `polygons` is the engine's
 * [[exteriorRing, ...holeRings], ...] nesting of [lat, lng] pairs, which Leaflet's
 * Polygon accepts directly. Kept generic so stages 2 and 3 (roads, amenities, towers)
 * render through the same prop without touching this component again.
 */
const Overlays = ({ overlays }) =>
  overlays.flatMap((o) => [
    ...(o.polygons || []).map((rings, i) => (
      <Polygon key={`${o.key}-${i}`} positions={rings} pathOptions={o.style}>
        {o.label && <Tooltip sticky>{o.label}</Tooltip>}
      </Polygon>
    )),
    ...(o.lines || []).map((line, i) => (
      <Polyline key={`${o.key}-line-${i}`} positions={line.path} pathOptions={o.lineStyle || o.style}>
        {line.label && <Tooltip sticky>{line.label}</Tooltip>}
      </Polyline>
    )),
  ]);

export const PlotMap = ({ coordinates = [], roadEdges = [], overlays = [], onChange, readOnly }) => {
  const [drawing, setDrawing] = useState(false);
  const center = coordinates.length
    ? [
        coordinates.reduce((s, c) => s + c[0], 0) / coordinates.length,
        coordinates.reduce((s, c) => s + c[1], 0) / coordinates.length,
      ]
    : [12.9716, 77.5946];

  const roadIdx = new Set(roadEdges.map((r) => r.edge_index));
  const edges = coordinates.map((c, i) => [c, coordinates[(i + 1) % coordinates.length]]);

  return (
    <div className="relative">
      <div className="absolute z-[500] top-2 left-2 flex gap-1.5 bg-white border border-slate-200 rounded-sm p-1">
        <Button
          size="sm"
          data-testid="map-draw-toggle"
          disabled={readOnly}
          variant={drawing ? "default" : "outline"}
          className="h-7 rounded-sm text-xs"
          onClick={() => setDrawing((d) => !d)}
        >
          {drawing ? <Pencil className="h-3 w-3 mr-1" /> : <MousePointer2 className="h-3 w-3 mr-1" />}
          {drawing ? "Drawing: click map" : "Draw plot"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          data-testid="map-undo-vertex"
          disabled={readOnly || !coordinates.length}
          className="h-7 rounded-sm text-xs"
          onClick={() => onChange(coordinates.slice(0, -1))}
        >
          <Undo2 className="h-3 w-3" />
        </Button>
        <Button
          size="sm"
          variant="outline"
          data-testid="map-clear-plot"
          disabled={readOnly || !coordinates.length}
          className="h-7 rounded-sm text-xs"
          onClick={() => onChange([])}
        >
          <Trash2 className="h-3 w-3" />
        </Button>
      </div>
      <MapContainer center={center} zoom={17} style={{ height: 420, width: "100%" }} className="rounded-sm z-0">
        <LayersControl position="topright">
          <LayersControl.BaseLayer checked name="OpenStreetMap">
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution="&copy; OpenStreetMap contributors"
            />
          </LayersControl.BaseLayer>
          <LayersControl.BaseLayer name="Satellite">
            <TileLayer
              url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
              attribution="Tiles &copy; Esri"
            />
          </LayersControl.BaseLayer>
        </LayersControl>
        <ClickCatcher active={drawing && !readOnly} onAdd={(p) => onChange([...coordinates, p])} />
        {coordinates.length >= 3 && (
          <Polygon positions={coordinates} pathOptions={{ color: "#2563EB", weight: 2, fillOpacity: 0.15 }} />
        )}
        <Overlays overlays={overlays} />
        {edges.map(
          ([a, b], i) =>
            roadIdx.has(i) && (
              <Polyline key={`road-${i}`} positions={[a, b]} pathOptions={{ color: "#F59E0B", weight: 6, opacity: 0.9 }} />
            )
        )}
        {coordinates.map((c, i) => (
          <Marker
            key={`v-${i}`}
            position={c}
            icon={vertexIcon}
            draggable={!readOnly}
            eventHandlers={{
              dragend: (e) => {
                const { lat, lng } = e.target.getLatLng();
                const next = coordinates.map((p, idx) => (idx === i ? [lat, lng] : p));
                onChange(next);
              },
            }}
          />
        ))}
      </MapContainer>
    </div>
  );
};
