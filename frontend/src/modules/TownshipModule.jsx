import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Building2,
  Landmark,
  TreePine,
  Car,
  Compass,
  Layers,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { api, apiError } from "../lib/api";
import { Section, Metric } from "../components/Field";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { num } from "../lib/format";

// The settings of the last plan generated here, so Site Reports exports the same plan the
// user is looking at. Per-browser convenience only; without it the report uses defaults.
const paramsKey = (projectId) => `aptimizer.township.${projectId}`;
export function readTownshipParams(projectId) {
  try {
    const raw = localStorage.getItem(paramsKey(projectId));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

// The parcel the master plan is drawn over: the project's own plot unless the user has
// generated a plan for a different area before.
export function defaultTownshipArea(analysis) {
  return Math.round(Number(analysis?.areas?.plot_area_sqm) || 0) || 60000;
}

export default function TownshipModule({ project, projectId, analysis }) {
  const saved = readTownshipParams(projectId);
  const [townshipArea, setTownshipArea] = useState(saved?.township_area_sqm ?? defaultTownshipArea(analysis));
  const [isMixedUse, setIsMixedUse] = useState(saved?.is_mixed_use ?? true);
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(false);

  const generatePlan = async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const { data } = await api.post(`/projects/${projectId}/township-plan`, {
        township_area_sqm: Number(townshipArea) || 50000,
        is_mixed_use: isMixedUse,
      });
      setPlan(data);
      try {
        localStorage.setItem(paramsKey(projectId), JSON.stringify({
          township_area_sqm: Number(townshipArea) || 50000, is_mixed_use: isMixedUse,
        }));
      } catch { /* storage unavailable: the report falls back to defaults */ }
      toast.success(
        isMixedUse ? "Mixed-Use Development Plan generated!" : "Township Master Plan generated!",
        { id: "township-plan" }
      );
    } catch (e) {
      toast.error(apiError(e.response?.data?.detail));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (projectId) generatePlan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  return (
    <div className="space-y-6">
      {/* HEADER SECTION */}
      <div className="rounded-lg border border-slate-800 bg-gradient-to-r from-slate-900 via-slate-900 to-indigo-950/40 p-5 text-white">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-600 text-white shadow-lg shadow-indigo-500/30">
            <Building2 className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight">Township & Mixed-Use Master Planner</h1>
            <p className="text-xs text-slate-300">
              Macro-parcel zoning, mixed residential-commercial high-street integration, and multi-sector circulation hierarchy
            </p>
          </div>
        </div>
      </div>

      {/* CONTROLS */}
      <Section title="Master Plan Configuration" description="Set total township parcel area and development model">
        <div className="flex flex-wrap items-end gap-4">
          <div className="w-56">
            <label htmlFor="township-area" className="text-xs font-semibold text-muted-foreground">Township area (m²)</label>
            <Input
              id="township-area"
              type="number"
              min={1000}
              value={townshipArea}
              onChange={(e) => setTownshipArea(e.target.value)}
              className="mt-1"
            />
            <p className="mt-1 text-[10px] text-slate-500">
              Defaults to your plot ({Math.round(Number(analysis?.areas?.plot_area_sqm) || 0).toLocaleString("en-IN")} m²).
              Enter a larger parcel to plan a whole township.
            </p>
          </div>
          <div className="flex items-center gap-2 pb-2">
            <input
              type="checkbox"
              id="mixedUseToggle"
              checked={isMixedUse}
              onChange={(e) => setIsMixedUse(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
            />
            <label htmlFor="mixedUseToggle" className="text-xs font-medium text-slate-700 cursor-pointer">
              Enable Mixed-Use Zoning (Retail Podium + Commercial Galleria)
            </label>
          </div>
          <Button onClick={generatePlan} disabled={loading} className="bg-indigo-600 hover:bg-indigo-700">
            {loading ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
            Generate Master Plan
          </Button>
        </div>
      </Section>

      {plan && (
        <>
          {/* METRICS STRIP */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-5">
            <Metric label="Total Township Area" value={num(plan.township_area_sqm, 0)} unit="m²" hint={`${(plan.township_area_sqm / 4046.86).toFixed(1)} Acres`} />
            <Metric label="Potential Built-Up" value={num(plan.master_plan_metrics?.total_potential_builtup_sqm, 0)} unit="m²" hint={`Blended FAR: ${plan.master_plan_metrics?.blended_far}`} />
            <Metric label="Estimated Dwellings" value={`${plan.master_plan_metrics?.estimated_dwelling_units}`} hint={plan.master_plan_metrics?.estimated_population ? `~${plan.master_plan_metrics.estimated_population.toLocaleString("en-IN")} people` : undefined} />
            <Metric label="Commercial Leasable" value={num(plan.master_plan_metrics?.commercial_leasable_sqm, 0)} unit="m²" hint="GLA Capacity" />
            <Metric label="Open Space Ratio" value={`${plan.master_plan_metrics?.open_space_ratio_pct}%`} hint="Parks & Recreation" />
          </div>

          {/* LAND USE ZONING TABLE */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Section title="Land Use & Zoning Distribution" description="Indicative master plan allocation — Aptimizer planning assumption">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50 border-b border-slate-200">
                    <TableHead className="text-slate-900 font-semibold">Zoning Classification</TableHead>
                    <TableHead className="text-right text-slate-900 font-semibold">Share</TableHead>
                    <TableHead className="text-right text-slate-900 font-semibold">Area (m²)</TableHead>
                    <TableHead className="text-right text-slate-900 font-semibold">Permissible FSI</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {plan.zoning_distribution?.map((z, idx) => (
                    <TableRow key={idx}>
                      <TableCell className="font-medium text-xs text-slate-900">{z.zone}</TableCell>
                      <TableCell className="text-right text-xs font-semibold text-indigo-700">{z.share_pct}%</TableCell>
                      <TableCell className="text-right text-xs text-slate-700">{num(z.area_sqm)} m²</TableCell>
                      <TableCell className="text-right text-xs text-slate-700">{z.permissible_fsi}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {plan.assumptions?.land_use_source && (
                <p className="mt-2 text-[11px] leading-relaxed text-slate-500" data-testid="township-land-use-source">
                  {plan.assumptions.land_use_source}
                </p>
              )}
            </Section>

            {/* SECTORS BREAKDOWN */}
            <Section title="Sector Breakdown" description="Phased development clusters and community hubs">
              <div className="space-y-3">
                {plan.sectors?.map((sec) => (
                  <div key={sec.sector_id} className="rounded-md border border-slate-200 bg-slate-50/70 p-3 text-xs shadow-2xs">
                    <div className="flex items-center justify-between border-b border-slate-200 pb-1.5">
                      <span className="font-bold text-slate-900">{sec.name}</span>
                      <span className="text-slate-500 font-medium">{num(sec.area_sqm)} m²</span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-4 text-slate-700">
                      {sec.units && <div>Dwelling Units: <strong className="text-slate-900">{sec.units}</strong></div>}
                      {sec.towers && <div>Towers: <strong className="text-slate-900">{sec.towers}</strong></div>}
                      {sec.gla_sqm && <div>Commercial GLA: <strong className="text-slate-900">{num(sec.gla_sqm)} m²</strong></div>}
                      {sec.amenities && (
                        <div>Amenities: <strong className="text-slate-900">{sec.amenities.join(", ")}</strong></div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </Section>
          </div>

          {/* CIRCULATION STRATEGY */}
          <Section title="Circulation & Mobility Strategy" description="Segregated pedestrian vs vehicular loops">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3 text-xs">
              <div className="rounded border border-slate-200 bg-slate-50/70 p-3 shadow-2xs">
                <span className="font-bold text-indigo-800">Pedestrian Segregation</span>
                <p className="mt-1 text-slate-700">{plan.circulation_strategy?.segregation}</p>
              </div>
              <div className="rounded border border-slate-200 bg-slate-50/70 p-3 shadow-2xs">
                <span className="font-bold text-indigo-800">Gate Hierarchy</span>
                <p className="mt-1 text-slate-700">{plan.circulation_strategy?.access_points}</p>
              </div>
              <div className="rounded border border-slate-200 bg-slate-50/70 p-3 shadow-2xs">
                <span className="font-bold text-indigo-800">Internal Right of Way</span>
                <p className="mt-1 text-slate-700">
                  {plan.circulation_strategy?.internal_road_width_m}m clear roadway with dedicated cycle track and tree verge.
                </p>
              </div>
            </div>
          </Section>
        </>
      )}
    </div>
  );
}
