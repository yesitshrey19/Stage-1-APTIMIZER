import { useMemo, useState } from "react";

/** Polar sun-path diagram: azimuth = angle from north, radius = 90 - elevation. */
export const SunPathDiagram = ({ sun, size = 320 }) => {
  const [hour, setHour] = useState(12);
  const R = size / 2 - 24;
  const cx = size / 2;
  const cy = size / 2;

  const project = (az, el) => {
    const r = (R * (90 - el)) / 90;
    const a = ((az - 90) * Math.PI) / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };

  const colors = { summer_solstice: "#F59E0B", equinox: "#2563EB", winter_solstice: "#0F172A" };
  const marker = useMemo(() => {
    const eq = sun.paths.find((p) => p.key === "equinox");
    if (!eq) return null;
    const pt = eq.points.reduce((best, p) => (Math.abs(p.hour - hour) < Math.abs(best.hour - hour) ? p : best), eq.points[0]);
    return pt;
  }, [sun, hour]);

  const orientation = sun.orientation_deg || 0;

  return (
    <div data-testid="sun-path-diagram">
      <svg width={size} height={size} className="mx-auto block">
        {[0, 30, 60].map((el) => (
          <circle key={el} cx={cx} cy={cy} r={(R * (90 - el)) / 90} fill="none" stroke="#E2E8F0" />
        ))}
        {["N", "E", "S", "W"].map((d, i) => {
          const [x, y] = project(i * 90, -6);
          return (
            <text key={d} x={x} y={y} fontSize="11" textAnchor="middle" fill="#475569" fontFamily="JetBrains Mono">
              {d}
            </text>
          );
        })}
        {[0, 90, 180, 270].map((a) => {
          const [x, y] = project(a, 0);
          return <line key={a} x1={cx} y1={cy} x2={x} y2={y} stroke="#E2E8F0" />;
        })}
        {/* plot orientation (front facade normal) */}
        {(() => {
          const [x, y] = project(orientation, 0);
          return <line x1={cx} y1={cy} x2={x} y2={y} stroke="#DC2626" strokeWidth="2" strokeDasharray="4 3" />;
        })()}
        {sun.paths.map((p) => (
          <polyline
            key={p.key}
            fill="none"
            stroke={colors[p.key]}
            strokeWidth="2"
            points={p.points.map((pt) => project(pt.azimuth, pt.elevation).join(",")).join(" ")}
          />
        ))}
        {marker &&
          (() => {
            const [x, y] = project(marker.azimuth, marker.elevation);
            return (
              <g>
                <circle cx={x} cy={y} r="7" fill="#F59E0B" stroke="#fff" strokeWidth="2" />
                <text x={cx} y={size - 4} fontSize="10" textAnchor="middle" fill="#475569" fontFamily="JetBrains Mono">
                  {`${marker.hour}h · az ${marker.azimuth}° · alt ${marker.elevation}°`}
                </text>
              </g>
            );
          })()}
      </svg>
      <div className="flex items-center gap-3 mt-2">
        <span className="text-[11px] uppercase tracking-wide text-slate-500">Time</span>
        <input
          type="range"
          min="5"
          max="19"
          step="0.5"
          value={hour}
          data-testid="sun-hour-slider"
          onChange={(e) => setHour(Number(e.target.value))}
          className="flex-1 accent-blue-600"
        />
        <span className="font-mono text-xs w-14 text-right">{hour}:00</span>
      </div>
      <div className="flex gap-3 mt-2 text-[11px]">
        {sun.paths.map((p) => (
          <span key={p.key} className="flex items-center gap-1">
            <span className="h-2 w-3 rounded-sm" style={{ background: colors[p.key] }} />
            {p.label} · {p.daylight_hours}h
          </span>
        ))}
        <span className="flex items-center gap-1 text-red-600">
          <span className="h-2 w-3 rounded-sm bg-red-600" /> plot front ({orientation}°)
        </span>
      </div>
    </div>
  );
};

export const WindRose = ({ wind, size = 260 }) => {
  const R = size / 2 - 26;
  const cx = size / 2;
  const cy = size / 2;
  const order = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  const map = Object.fromEntries(wind.rose.map((r) => [r.direction, r.frequency_pct]));
  const max = Math.max(...wind.rose.map((r) => r.frequency_pct));
  return (
    <svg width={size} height={size} className="mx-auto block" data-testid="wind-rose">
      <circle cx={cx} cy={cy} r={R} fill="none" stroke="#E2E8F0" />
      <circle cx={cx} cy={cy} r={R / 2} fill="none" stroke="#E2E8F0" />
      {order.map((d, i) => {
        const val = map[d] || 0;
        const len = (R * val) / max;
        const a = ((i * 45 - 90) * Math.PI) / 180;
        const x = cx + len * Math.cos(a);
        const y = cy + len * Math.sin(a);
        const lx = cx + (R + 14) * Math.cos(a);
        const ly = cy + (R + 14) * Math.sin(a);
        const prevailing = wind.prevailing.startsWith(d);
        return (
          <g key={d}>
            <line x1={cx} y1={cy} x2={x} y2={y} stroke={prevailing ? "#2563EB" : "#94A3B8"} strokeWidth={prevailing ? 6 : 4} strokeLinecap="round" />
            <text x={lx} y={ly + 3} fontSize="10" textAnchor="middle" fill="#475569" fontFamily="JetBrains Mono">
              {d}
            </text>
          </g>
        );
      })}
    </svg>
  );
};
