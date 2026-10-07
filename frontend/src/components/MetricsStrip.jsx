import { AlertTriangle } from "lucide-react";
import { int, num } from "../lib/format";

const Item = ({ label, value, unit, testid, tone, vertical }) => (
  <div
    className={
      vertical
        ? "px-4 py-2.5 border-b border-slate-200 last:border-b-0"
        : "px-4 py-2 border-r border-slate-200 last:border-r-0 min-w-[124px]"
    }
    data-testid={testid}
  >
    <div className="text-[10px] uppercase tracking-wider text-slate-500 whitespace-nowrap">{label}</div>
    <div
      className={`font-mono text-base leading-tight transition-colors ${
        tone === "danger" ? "text-red-600" : tone === "success" ? "text-emerald-600" : "text-slate-900"
      }`}
    >
      {value}
      {unit && <span className="text-[10px] text-slate-400 ml-0.5">{unit}</span>}
    </div>
  </div>
);

/**
 * @param {boolean} vertical  stack top-to-bottom for a sidebar instead of side-by-side
 *                            for a strip. Same figures either way -- only the layout
 *                            direction and each item's dividing border change.
 */
/**
 * @param {"fresh"|"recomputing"|"stale"} state  whether these figures were computed from
 *        the project as it stands. A stale rail is dimmed and says so: a number nobody can
 *        tell is out of date is more dangerous than no number at all.
 */
export const MetricsStrip = ({ analysis, vertical = false, state = "fresh", at = null }) => {
  if (!analysis)
    return vertical ? (
      <div className="w-full bg-white" />
    ) : (
      <div className="h-14 border-b border-slate-200 bg-white" />
    );
  const a = analysis?.areas || {};
  const dimmed = state === "stale" ? "opacity-60" : "";
  return (
    <div
      className={
        (vertical
          ? "flex flex-col overflow-y-auto bg-white"
          : "flex overflow-x-auto border-b border-slate-200 bg-white sticky top-0 z-20")
        + (dimmed ? ` ${dimmed}` : "")
      }
      data-testid="metrics-strip"
    >
      {state === "stale" && (
        <div
          className="flex items-start gap-2 border-b border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-snug text-amber-900"
          data-testid="metrics-stale-banner"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
          <span>
            <span className="font-semibold">Not recalculated.</span> These figures are from
            {at ? ` ${at.toLocaleTimeString()}` : " an earlier version of the project"} and do
            not reflect your latest edits. Retrying…
          </span>
        </div>
      )}
      <Item vertical={vertical} label="Plot Area" value={num(a?.plot_area_sqm, 0)} unit="m²" testid="metric-plot-area" />
      <Item vertical={vertical} label="In Acres" value={num(a?.plot_area_acres, 3)} unit="acres" testid="metric-plot-acres" />
      <Item vertical={vertical} label="Gr. Coverage" value={num(a?.ground_coverage_pct, 1)} unit="%" testid="metric-coverage" />
      <Item vertical={vertical} label="FAR" value={num(a?.far, 2)} testid="metric-far" />
      <Item vertical={vertical} label="FSI" value={num(a?.fsi, 2)} testid="metric-fsi" />
      <Item vertical={vertical} label="Open Space" value={num(a?.open_space_pct, 1)} unit="%" testid="metric-open-space" />
      <Item vertical={vertical} label="Units (yield)" value={int(a?.total_units)} testid="metric-units" />
      <Item vertical={vertical} label="Density" value={num(a?.density_units_per_acre, 1)} unit="/acre" testid="metric-density" />
    </div>
  );
};
