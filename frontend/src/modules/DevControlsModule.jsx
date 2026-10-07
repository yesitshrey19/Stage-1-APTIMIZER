import { useEffect, useState } from "react";
import { Metric, NumField, Section } from "../components/Field";
import { Button } from "../components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { api, apiError } from "../lib/api";
import { num } from "../lib/format";
import { AlertTriangle, CheckCircle2, Ruler, Wand2 } from "lucide-react";
import { toast } from "sonner";

const CONFIDENCE = {
  code: { label: "IS / NBC", cls: "bg-emerald-50 border-emerald-300 text-emerald-800" },
  indicative: { label: "Indicative", cls: "bg-amber-50 border-amber-300 text-amber-800" },
};

/**
 * Development controls — the setbacks, height, FAR and yield that govern the site.
 *
 * Split out of the plot drawing tools so the numbers that decide what may be built are not buried
 * under the drawing tools. Every recommended value shows where it came from and whether
 * it still needs checking against the sanctioning authority.
 */
export default function DevControlsModule({ project, analysis, update, readOnly, projectId }) {
  const plot = project.plot || {};
  const areas = analysis?.areas;
  const plotArea = areas?.plot_area_sqm || 0;
  const roadEdges = plot.road_edges || [];
  const widestRoad = roadEdges.reduce((m, r) => Math.max(m, Number(r.width) || 0), 0);

  const stored = project.dev_controls || {};
  const [rec, setRec] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const setbacks = stored.setbacks || { front: 9, rear: 4.5, side: 4.5, default: 6 };
  // The statutory minimum per edge, from the same backend function the envelope check
  // uses -- so the number the user is validated against here and the number the layout
  // engine enforces cannot drift apart.
  const [limits, setLimits] = useState(null);
  useEffect(() => {
    if (!projectId) return;
    api.get(`/projects/${projectId}/setbacks`)
      .then(({ data }) => setLimits(data))
      .catch(() => setLimits(null));
  }, [projectId, setbacks.front, setbacks.rear, setbacks.side, setbacks.default, areas?.max_height_m, project.rev]);

  const limitFor = (edge) =>
    (limits?.edges || []).find((e) => e.edge === edge) || null;

  const setSetback = (key, value) =>
    update((p) => {
      p.dev_controls = p.dev_controls || {};
      p.dev_controls.setbacks = { ...setbacks, [key]: value };
    });

  const runRecommend = async () => {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post("/site-layout/recommend", {
        plot_area_sqm: plotArea,
        road_width_m: widestRoad,
        city: project.engineering?.city || "",
        state: project.engineering?.state || "",
      });
      if (data.ok) { setRec(data); setAppliedAt(null); }
      else setError(data.error || "Could not compute recommendations.");
    } catch (e) {
      setError(apiError(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const [appliedAt, setAppliedAt] = useState(null);
  const applyRecommendation = () => {
    update((p) => {
      p.dev_controls = p.dev_controls || {};
      p.dev_controls.setbacks = rec.setbacks;
      p.dev_controls.far_cap = rec.far_cap;
      p.dev_controls.ground_coverage_cap_pct = rec.ground_coverage_cap_pct;
      p.dev_controls.recommended = {
        floors: rec.floors, units: rec.units, height_m: rec.height_m,
        generated_at: new Date().toISOString(),
      };
      // The statutory setback minimum rises with building height, so the recommended
      // setbacks only hold if the towers take the recommended height as well.
      if (rec.floors && Array.isArray(p.towers)) {
        p.towers = p.towers.map((t) => ({ ...t, floors: rec.floors }));
      }
    });
    setAppliedAt(new Date());
    const sb = rec.setbacks || {};
    toast.success("Recommended controls applied to the project", {
      description: `Setbacks F ${num(sb.front, 1)} / R ${num(sb.rear, 1)} / S ${num(sb.side, 1)} m · FAR ${num(rec.far_cap, 2)} · `
        + `coverage ${num(rec.ground_coverage_cap_pct, 0)}% · ${rec.floors} floors. Generate the layout again to use them.`,
    });
  };

  return (
    <div className="space-y-4">
      <p className="text-[11px] text-amber-800 bg-amber-50 border border-amber-300 rounded-sm px-2.5 py-2 flex gap-2"
         data-testid="devcontrols-disclaimer">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
        <span>
          <span className="font-semibold">Preliminary figures for feasibility only.</span>{" "}
          Setbacks derived from NBC 2016 Part 3 are shown as <em>IS / NBC</em>. FAR and ground
          coverage are set by your municipal development control regulations, vary by zone and
          are revised periodically — they are shown as <em>Indicative</em> and must be confirmed
          with the sanctioning authority for this specific plot before any design decision.
        </span>
      </p>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Metric label="Plot area" value={num(plotArea, 2)} unit="m²" testid="dc-plot-area" />
        <Metric label="Widest abutting road" value={widestRoad ? num(widestRoad, 1) : "—"} unit={widestRoad ? "m" : ""} testid="dc-road-width" />
        <Metric label="Recommended floors" value={stored.recommended?.floors ?? "—"} testid="dc-rec-floors" />
        <Metric label="Estimated units" value={stored.recommended?.units ?? "—"} testid="dc-rec-units" />
      </div>

      <Section
        title="Setbacks"
        description="Measured inward from the plot boundary. Front applies to edges marked road-facing on the plot above; rear is the edge opposite them; side is everything else."
        testid="setbacks-section"
        actions={
          <Button size="sm" variant="outline" className="h-7 rounded-sm text-xs"
                  data-testid="recommend-controls-button"
                  disabled={busy || readOnly || !plotArea}
                  onClick={runRecommend}>
            <Wand2 className="h-3 w-3 mr-1" />
            {busy ? "Calculating…" : "Recommend from plot"}
          </Button>
        }
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {["front", "rear", "side", "default"].map((k) => {
            const lim = limitFor(k);
            return (
              <div key={k} className="space-y-1">
                <NumField
                  label={k === "default" ? "Default (unclassified edges)" : `${k[0].toUpperCase()}${k.slice(1)} setback`}
                  suffix="m"
                  value={setbacks[k]}
                  disabled={readOnly}
                  testid={`dc-setback-${k}`}
                  onChange={(v) => setSetback(k, v)}
                />
                {lim && (
                  lim.ok ? (
                    <p className="text-[10px] text-slate-500 leading-snug"
                      data-testid={`dc-setback-${k}-min`}>
                      Minimum {lim.minimum_m} m. {lim.rule}
                    </p>
                  ) : (
                    <p className="text-[10px] text-red-700 bg-red-50 border border-red-200 rounded-sm px-1.5 py-1 leading-snug"
                      data-testid={`dc-setback-${k}-error`}>
                      <span className="font-semibold">
                        {lim.shortfall_m} m below the {lim.minimum_m} m minimum.
                      </span>{" "}
                      {lim.rule} ({lim.clause}).
                    </p>
                  )
                )}
              </div>
            );
          })}
        </div>
        {limits && !limits.ok && (
          <p className="text-[11px] text-red-800 bg-red-50 border border-red-200 rounded-sm px-2 py-1.5 mt-3"
            data-testid="dc-setbacks-invalid">
            These setbacks are below the statutory minimum and the envelope built from them
            would not be sanctionable. The minimum rises with building height — this project
            is {limits.height_m} m tall on a {Math.round(limits.plot_area_sqm).toLocaleString("en-IN")} m²
            plot. <em>Recommend from plot</em> sets values that clear it.
          </p>
        )}
        {limits?.note && (
          <p className="text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded-sm px-2 py-1.5 mt-2"
            data-testid="dc-setbacks-note">{limits.note}</p>
        )}
        <p className="text-[11px] text-slate-500 mt-2">
          These are the values the site envelope is built from. The layout engine above shows them
          read-only.
        </p>
        <p className="text-[11px] text-slate-500 mt-3 flex items-center gap-1.5">
          <Ruler className="h-3 w-3" />
          NBC 2016 Part 3 ties side and rear open space to building height — a taller block
          legally requires a deeper setback. Use <em>Recommend from plot</em> to derive them.
        </p>
        {error && (
          <p className="text-[11px] text-red-700 bg-red-50 border border-red-200 rounded-sm px-2 py-1 mt-2"
             data-testid="dc-error">{error}</p>
        )}
      </Section>

      {rec && (
        <Section
          title="Recommended controls"
          description={`Derived from a ${num(rec.plot_area_sqm, 0)} m² plot${rec.road_width_m ? ` with a ${num(rec.road_width_m, 1)} m abutting road` : " with no recorded road frontage"}.`}
          testid="recommendation-section"
          actions={
            <div className="flex items-center gap-2">
              {appliedAt && (
                <span className="flex items-center gap-1 text-[11px] font-medium text-emerald-700" data-testid="recommendation-applied">
                  <CheckCircle2 className="h-3.5 w-3.5" /> Applied {appliedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              )}
              <Button size="sm" className="h-7 rounded-sm text-xs" data-testid="apply-recommendation-button"
                      disabled={readOnly} onClick={applyRecommendation}>
                {appliedAt ? "Apply again" : "Apply to project"}
              </Button>
            </div>
          }
        >
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <Metric label="Building height" value={num(rec.height_m, 1)} unit="m" testid="rec-height" />
            <Metric label="Floors" value={rec.floors} testid="rec-floors" />
            <Metric label="Dwelling units" value={rec.units} testid="rec-units" />
            <Metric label="FAR" value={num(rec.far_cap, 2)} testid="rec-far" />
          </div>

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Control</TableHead>
                <TableHead className="w-28">Value</TableHead>
                <TableHead className="w-28">Basis</TableHead>
                <TableHead>Source</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rec.recommendations.map((r) => {
                const c = CONFIDENCE[r.confidence] || CONFIDENCE.indicative;
                return (
                  <TableRow key={r.key} data-testid={`rec-row-${r.key}`}>
                    <TableCell className="py-1.5 text-xs">
                      {r.label}
                      {r.note && <div className="text-[10px] text-slate-500 mt-0.5">{r.note}</div>}
                    </TableCell>
                    <TableCell className="py-1.5 font-mono text-xs whitespace-nowrap">
                      {typeof r.value === "number" ? num(r.value, 2) : r.value} {r.unit}
                    </TableCell>
                    <TableCell className="py-1.5">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-sm border ${c.cls}`}>
                        {c.label}
                      </span>
                      {r.requires_verification && (
                        <div className="text-[10px] text-amber-700 mt-0.5">verify</div>
                      )}
                    </TableCell>
                    <TableCell className="py-1.5 text-[11px] text-slate-600">{r.source}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          {rec.warnings.map((w, i) => (
            <p key={i} className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-sm px-2 py-1 mt-2"
               data-testid={`rec-warning-${i}`}>{w}</p>
          ))}
        </Section>
      )}
    </div>
  );
}
