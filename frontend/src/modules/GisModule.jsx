import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, RefreshCw, Sparkles, TriangleAlert, XCircle } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, apiError } from "../lib/api";
import { GisMap } from "../components/GisMap";
import { SunPathDiagram, WindRose } from "../components/SiteDiagrams";
import { Metric, Section } from "../components/Field";
import ConsultantPanel from "../components/ConsultantPanel";
import { Markdown } from "../components/Markdown";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Progress } from "../components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { dt, int, money, num } from "../lib/format";

const SEVERITY = {
  critical: { cls: "bg-red-50 border-red-200 text-red-800", Icon: XCircle },
  warning: { cls: "bg-amber-50 border-amber-200 text-amber-800", Icon: TriangleAlert },
  ok: { cls: "bg-emerald-50 border-emerald-200 text-emerald-800", Icon: CheckCircle2 },
};

// Why a stored analysis is out of date. The backend returns the reason rather than a bare
// flag, because "the boundary moved" and "the rules changed" need different answers from
// the user and blaming the wrong one makes the warning untrustworthy.
const STALE_MESSAGES = {
  polygon: "The plot boundary has changed since this analysis was run. Click “Re-run analysis” to update it.",
  rules: "Aptimizer’s site analysis has been improved since this was run, so some figures may change. "
       + "Click “Re-run analysis” to update the results.",
};

// Runs that are still in flight, keyed by project. They live outside the component so an
// analysis started here keeps going -- and still lands in the workspace -- when the user
// switches to another module, and a remount picks the running request back up instead of
// showing an idle button over a request that has not finished.
const gisRuns = new Map();
const aiRuns = new Map();

export default function GisModule({ project, projectId, readOnly, setProject }) {
  const [gis, setGis] = useState(null);
  const [stale, setStale] = useState(false);
  const [staleReason, setStaleReason] = useState(null);
  const [hasPolygon, setHasPolygon] = useState(true);
  const [radius, setRadius] = useState(500);
  const [busy, setBusy] = useState(gisRuns.has(projectId));
  const [aiBusy, setAiBusy] = useState(aiRuns.has(projectId));
  const [aiStatus, setAiStatus] = useState(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/projects/${projectId}/gis`);
      if (!mounted.current) return;
      setGis(data.gis);
      setStale(data.stale);
      setStaleReason(data.stale_reason ?? null);
      setHasPolygon(data.has_polygon);
      if (data.gis?.radius_m) setRadius(data.gis.radius_m);
    } catch (e) {
      toast.error(apiError(e.response?.data?.detail));
    }
  }, [projectId]);

  // Show a run's result if this view is still (or again) on screen when it lands.
  const follow = useCallback((promise, setRunning, onData) => {
    setRunning(true);
    promise
      .then((data) => { if (data && mounted.current) onData(data); })
      .finally(() => { if (mounted.current) setRunning(false); });
  }, []);

  useEffect(() => {
    load();
    api.get("/ai/status").then(({ data }) => mounted.current && setAiStatus(data)).catch(() => {});
    if (gisRuns.has(projectId)) follow(gisRuns.get(projectId), setBusy, (d) => { setGis(d.gis); setStale(false); setStaleReason(null); });
    if (aiRuns.has(projectId)) follow(aiRuns.get(projectId), setAiBusy, (d) => setGis((g) => ({ ...g, ai_summary: d })));
  }, [load, follow, projectId]);

  // Both endpoints write the project and bump its revision; the workspace takes the new
  // revision so its next autosave is not refused as a conflict with this run.
  const takeRev = (rev) => {
    if (rev !== undefined && rev !== null) setProject?.((prev) => (prev ? { ...prev, rev } : prev));
  };

  const run = () => {
    if (gisRuns.has(projectId)) return;
    const started = Date.now();
    const promise = api.post(`/projects/${projectId}/gis/analyse`, { radius_m: Number(radius) })
      .then(({ data }) => {
        takeRev(data.rev);
        const secs = ((Date.now() - started) / 1000).toFixed(1);
        if (data.gis.sources?.overpass?.ok === false) {
          toast.warning(`Analysed in ${secs} s, but the map servers did not respond`, {
            description: "Roads, water bodies and buildings are missing. Re-run the analysis in a minute.",
          });
        } else {
          toast.success(`Site analysed in ${secs} s — suitability ${data.gis.suitability.score}/100`);
        }
        return data;
      })
      .catch((e) => {
        const status = e.response?.status;
        toast.error(status === 502 || status === 504
          ? "Public map/elevation services are busy — please run the analysis again."
          : apiError(e.response?.data?.detail));
        return null;
      })
      .finally(() => gisRuns.delete(projectId));
    gisRuns.set(projectId, promise);
    follow(promise, setBusy, (d) => { setGis(d.gis); setStale(false); setStaleReason(null); });
  };

  const runAi = () => {
    if (aiRuns.has(projectId)) return;
    const promise = api.post(`/projects/${projectId}/gis/ai-summary`)
      .then(({ data }) => {
        const { rev, ...summary } = data;
        takeRev(rev);
        toast.success("AI site analysis generated");
        return summary;
      })
      .catch((e) => { toast.error(apiError(e.response?.data?.detail)); return null; })
      .finally(() => aiRuns.delete(projectId));
    aiRuns.set(projectId, promise);
    follow(promise, setAiBusy, (d) => setGis((g) => ({ ...g, ai_summary: d })));
  };

  const aiReady = aiStatus?.configured !== false;

  // A new study radius is a different map query; start downloading it while the user
  // is still deciding to press Run.
  useEffect(() => {
    const r = Number(radius);
    if (!r || r < 100 || r > 2000) return;
    const t = setTimeout(() => api.post(`/projects/${projectId}/gis/prefetch`, { radius_m: r }).catch(() => {}), 800);
    return () => clearTimeout(t);
  }, [radius, projectId]);

  const coords = project.plot?.coordinates || [];

  return (
    <div className="space-y-4">
      <Section
        title="Site intelligence"
        description={`Reads the plot polygon from Plot & Setbacks (${coords.length} vertices) — OpenStreetMap features, terrain elevation, solar and wind analysis.`}
        testid="gis-control-section"
        actions={
          <div className="flex items-end gap-2">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-slate-500">Radius</div>
              <Input type="number" min={100} max={2000} step={100} value={radius}
                onChange={(e) => setRadius(e.target.value)} disabled={readOnly}
                className="h-8 w-24 font-mono text-xs rounded-sm" data-testid="gis-radius-input" />
            </div>
            <Button onClick={run} disabled={busy || readOnly || !hasPolygon} className="rounded-sm h-8" data-testid="run-gis-button">
              <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${busy ? "animate-spin" : ""}`} />
              {busy ? "Analysing…" : gis ? "Re-run analysis" : "Run site analysis"}
            </Button>
          </div>
        }
      >
        {!hasPolygon && (
          <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-sm px-3 py-2" data-testid="gis-no-polygon">
            Draw a plot polygon with at least 3 vertices in Plot &amp; Setbacks first.
          </p>
        )}
        {stale && (
          <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-sm px-3 py-2 flex items-center gap-2" data-testid="gis-stale-banner">
            <AlertTriangle className="h-4 w-4" />
            {STALE_MESSAGES[staleReason] || STALE_MESSAGES.polygon}
          </p>
        )}
        {gis && (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 mt-3">
            <Metric label="Suitability" value={`${gis.suitability.score}`} unit={`/100 ${gis.suitability.grade}`}
              tone={gis.suitability.score >= 65 ? "success" : "danger"} testid="gis-suitability-score" />
            <Metric label="Avg slope" value={gis.terrain.avg_slope_pct ?? "—"} unit={`% ${gis.terrain.slope_class}`} testid="gis-slope"
              hint={gis.terrain.slope_confidence === "low" ? gis.terrain.slope_note : undefined} />
            <Metric label="Flood risk" value={gis.flood.level} tone={gis.flood.level === "low" ? "success" : "danger"} testid="gis-flood-level" />
            <Metric label="Nearest road" value={gis.accessibility.nearest_road_m ?? "—"} unit="m" testid="gis-nearest-road" />
            <Metric label="Buildings nearby" value={`${int(gis.feature_counts.buildings)}${gis.feature_counts_capped?.buildings ? "+" : ""}`} testid="gis-building-count" />
            <Metric label="Analysed" value={gis.generated_at ? dt(gis.generated_at).split(",")[0] : "—"} testid="gis-generated-at" />
          </div>
        )}
        {gis && gis.sources?.overpass && !gis.sources.overpass.ok && (
          <p className="mt-3 text-sm text-red-800 bg-red-50 border border-red-200 rounded-sm px-3 py-2 flex items-center gap-2"
             data-testid="gis-map-data-failed">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            The public map servers (OpenStreetMap) did not respond, so roads, water bodies and nearby buildings are
            missing from this analysis and the scores below are incomplete. Click “Re-run analysis” in a minute.
          </p>
        )}
        {gis && gis.sources?.elevation && !gis.sources.elevation.ok && (
          <p className="mt-3 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-sm px-3 py-2 flex items-center gap-2"
             data-testid="gis-elevation-failed">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            Elevation data was unavailable, so terrain, slope and part of the flood check are missing. Re-run to retry.
          </p>
        )}
        {gis && (
          <p className="text-[11px] text-slate-500 mt-2 font-mono" data-testid="gis-sources">
            overpass: {gis.sources.overpass.ok ? "ok" : `failed (${gis.sources.overpass.error})`} · elevation:{" "}
            {gis.sources.elevation.ok ? "ok" : `failed (${gis.sources.elevation.error})`}
          </p>
        )}
      </Section>

      {gis && (
        <>
          <Section title="Detected site context" description="Buildings, roads, green cover, water bodies, natural waterways and transit around the plot" testid="gis-map-section">
            <GisMap coordinates={coords} features={gis.features} />
          </Section>

          <div className="grid lg:grid-cols-2 gap-4">
            <Section title={`Site suitability — ${gis.suitability.score}/100 (${gis.suitability.grade})`} testid="gis-suitability-section">
              <div className="space-y-3">
                {gis.suitability.breakdown.map((b) => (
                  <div key={b.factor} data-testid={`suitability-factor-${b.factor.split(" ")[0].toLowerCase()}`}>
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-medium text-slate-700">
                        {b.factor} <span className="text-slate-400">· weight {b.weight_pct}%</span>
                      </span>
                      <span className="font-mono">
                        {b.score} → +{b.contribution}
                      </span>
                    </div>
                    <Progress value={b.score} className="h-1.5 mt-1" />
                    <p className="text-[11px] text-slate-500 mt-0.5">{b.note}</p>
                  </div>
                ))}
              </div>
            </Section>

            <Section
              title={`Buildability — ${gis.buildability.buildable ? "no hard constraints" : `${gis.buildability.critical_count} critical constraint(s)`}`}
              testid="gis-buildability-section"
            >
              <ul className="space-y-2">
                {gis.buildability.flags.map((f) => {
                  const { cls, Icon } = SEVERITY[f.severity] || SEVERITY.ok;
                  return (
                    <li key={f.id} className={`border rounded-sm px-3 py-2 flex gap-2 ${cls}`} data-testid={`buildability-flag-${f.id}`}>
                      <Icon className="h-4 w-4 mt-0.5 shrink-0" />
                      <div>
                        <div className="text-sm font-semibold">{f.title}</div>
                        <div className="text-xs">{f.detail}</div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </Section>
          </div>

          <div className="grid lg:grid-cols-2 gap-4">
            <Section title="Terrain & elevation profile" testid="gis-terrain-section">
              {gis.terrain.available ? (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                    <Metric label="Min" value={num(gis.terrain.min_m, 1)} unit="m" testid="terrain-min" />
                    <Metric label="Max" value={num(gis.terrain.max_m, 1)} unit="m" testid="terrain-max" />
                    <Metric label="Mean" value={num(gis.terrain.mean_m, 1)} unit="m" testid="terrain-mean" />
                    <Metric label="Relief" value={num(gis.terrain.relief_m, 1)} unit="m" testid="terrain-relief" />
                  </div>
                  <ResponsiveContainer width="100%" height={220}>
                    <AreaChart data={gis.terrain.profile}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                      <XAxis dataKey="distance_m" tick={{ fontSize: 10, fontFamily: "JetBrains Mono" }} unit="m" />
                      <YAxis domain={["dataMin - 2", "dataMax + 2"]} tick={{ fontSize: 10, fontFamily: "JetBrains Mono" }} unit="m" />
                      <Tooltip />
                      <Area type="monotone" dataKey="elevation_m" stroke="#2563EB" fill="#DBEAFE" strokeWidth={2} />
                    </AreaChart>
                  </ResponsiveContainer>
                  <p className="text-[11px] text-slate-500 mt-1">
                    Profile across the plot diagonal ({num(gis.terrain.horizontal_run_m, 0)} m run) ·{" "}
                    {gis.terrain.samples.length} in-plot elevation samples · surrounding ground mean{" "}
                    {num(gis.terrain.ring_mean_m, 1)} m
                  </p>
                </>
              ) : (
                <p className="text-sm text-slate-500">Elevation data unavailable for this location.</p>
              )}
            </Section>

            <Section title={`Flood risk — ${gis.flood.level} (${gis.flood.score}/100)`} testid="gis-flood-section">
              <ul className="space-y-1.5 text-sm">
                {gis.flood.reasons.map((r, i) => (
                  <li key={i} className="flex gap-2" data-testid={`flood-reason-${i}`}>
                    <AlertTriangle className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" />
                    {r}
                  </li>
                ))}
              </ul>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-3">
                {/* Named for what was actually detected — a storm drain crossing the plot is
                    not a lake, and calling it one is what made this panel look wrong. */}
                <Metric label={`Nearest ${(gis.flood.nearest_water_label || "water body").toLowerCase()}`}
                  value={gis.flood.nearest_water_m ?? "none"} unit="m" testid="flood-nearest-water" />
                <Metric label="Elevation vs surroundings" value={gis.flood.elevation_delta_m ?? "—"} unit="m" testid="flood-delta" />
                <Metric label="Nearest storm-water drain" value={gis.flood.nearest_drain_m ?? "none mapped"}
                  unit={gis.flood.nearest_drain_m != null ? "m" : ""} testid="flood-nearest-drain" />
              </div>
              {(gis.flood.plinth_height_m != null || gis.flood.design_response) && (
                <div className="mt-3 border border-sky-200 bg-sky-50 rounded-sm px-3 py-2" data-testid="flood-design-response">
                  <div className="text-xs font-semibold text-sky-900 uppercase tracking-wide">Design response</div>
                  {gis.flood.plinth_height_m != null && (
                    <div className="mt-2">
                      <Metric label="Min. plinth height" value={num(gis.flood.plinth_height_m, 2)} unit="m above road level"
                        testid="flood-plinth-height" />
                    </div>
                  )}
                  <ul className="space-y-1 mt-2">
                    {(gis.flood.design_response || []).map((d, i) => (
                      <li key={i} className="text-xs text-sky-900 flex gap-1.5" data-testid={`flood-response-${i}`}>
                        <span aria-hidden>›</span>{d}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Section>

            {gis.seismic && (
              <Section title={`Earthquake hazard — seismic zone ${gis.seismic.zone}`}
                description="Site-level ground hazard per IS 1893:2016 — what the ground does at this location"
                testid="gis-seismic-section">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <Metric label="Zone" value={`${gis.seismic.zone} — ${gis.seismic.zone_label || ""}`} testid="seismic-zone" />
                  <Metric label="Site class" value={gis.seismic.site_class} unit={`soil factor ${gis.seismic.soil_factor}`}
                    testid="seismic-site-class" />
                  <Metric label="PGA, Z (IS 1893)" value={gis.seismic.pga_rock_g} unit="g" testid="seismic-pga-rock" />
                  <Metric label="Surface shaking (screening)" value={gis.seismic.pga_surface_g} unit="g"
                    tone={gis.seismic.pga_surface_g >= 0.24 ? "danger" : "default"} testid="seismic-pga-surface" />
                </div>
                <ul className="space-y-2 mt-3">
                  {(gis.seismic.risk_flags || []).map((r) => {
                    const { cls, Icon } = SEVERITY[r.severity] || SEVERITY.ok;
                    return (
                      <li key={r.id} className={`border rounded-sm px-3 py-2 flex gap-2 ${cls}`} data-testid={`seismic-flag-${r.id}`}>
                        <Icon className="h-4 w-4 mt-0.5 shrink-0" />
                        <div>
                          <div className="text-sm font-semibold">{r.title}</div>
                          <div className="text-xs">{r.detail}</div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
                <p className="text-xs text-slate-600 mt-2">{gis.seismic.guidance}</p>
              </Section>
            )}
          </div>

          <div className="grid lg:grid-cols-2 gap-4">
            <Section title="Sun path & facade guidance" description="Relative to the plot orientation set in Plot & Setbacks" testid="gis-sun-section">
              <SunPathDiagram sun={gis.sun} />
              <Table className="mt-3">
                <TableHeader>
                  <TableRow>
                    <TableHead>Facade</TableHead>
                    <TableHead className="text-right">Bearing</TableHead>
                    <TableHead className="text-right">Sun hrs</TableHead>
                    <TableHead>Guidance</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {gis.sun.facades.map((f) => (
                    <TableRow key={f.facade} data-testid={`facade-row-${f.bearing_deg}`}>
                      <TableCell className="py-1.5 font-medium">{f.facade}</TableCell>
                      <TableCell className="py-1.5 text-right font-mono">{f.bearing_deg}°</TableCell>
                      <TableCell className="py-1.5 text-right font-mono">{f.sun_hours_equinox}</TableCell>
                      <TableCell className="py-1.5 text-xs text-slate-600">{f.recommendation}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Section>

            {gis.solar && (
              <Section
                title="Rooftop solar potential"
                description="What the terrace could generate, from the same sun geometry as the path above"
                testid="gis-solar-section"
              >
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <Metric label="System size" value={num(gis.solar.installable_kwp, 1)} unit="kWp"
                    testid="solar-kwp" />
                  <Metric label="Yearly generation" value={int(gis.solar.annual_yield_kwh)} unit="kWh"
                    testid="solar-yield" />
                  <Metric label="Yearly saving" value={money(gis.solar.annual_saving_inr, "INR")}
                    testid="solar-saving" />
                  <Metric label="Pays for itself in"
                    value={gis.solar.payback_years == null ? "—" : num(gis.solar.payback_years, 1)}
                    unit="years" testid="solar-payback" />
                </div>
                <p className="text-[11px] text-slate-500 mt-3">
                  {num(gis.solar.usable_area_sqm, 0)} m² of the {num(gis.solar.roof_area_sqm, 0)} m²
                  terrace is usable once lifts, tanks and access paths are taken out, at roughly
                  10 m² per kWp for tilted rows. This site receives{" "}
                  <span className="font-semibold text-slate-700">
                    {num(gis.solar.insolation.annual_kwh_per_sqm, 0)} kWh/m² a year
                  </span>{" "}
                  ({num(gis.solar.insolation.daily_average_kwh_per_sqm, 1)} a day
                  {gis.solar.insolation.source ? `, ${gis.solar.insolation.source}` : ""}), giving{" "}
                  {int(gis.solar.specific_yield_kwh_per_kwp)} kWh per kWp installed. Payback is
                  against a ₹{gis.solar.config.tariff_per_kwh}/kWh tariff and ignores any subsidy or
                  export price, both of which vary by state. Avoids about{" "}
                  {num(gis.solar.co2_avoided_tonnes_per_yr, 1)} tonnes of CO₂ a year.
                </p>
                <ResponsiveContainer width="100%" height={160}>
                  <BarChart data={gis.solar.insolation.monthly.map((m, i) => ({
                    month: ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][i],
                    kwh: m.kwh_per_sqm_month,
                  }))} margin={{ top: 12, right: 8, left: 8, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                    <XAxis dataKey="month" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} />
                    <Tooltip formatter={(v) => `${v} kWh/m²`} />
                    <Bar dataKey="kwh" fill="#F59E0B" radius={[2, 2, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </Section>
            )}

            <Section title={`Wind — ${gis.wind.region}`} testid="gis-wind-section">
              <WindRose wind={gis.wind} />
              <div className="grid grid-cols-3 gap-3 mt-2">
                <Metric label="Prevailing" value={gis.wind.prevailing} testid="wind-prevailing" />
                <Metric label="Summer" value={gis.wind.summer} testid="wind-summer" />
                <Metric label="Mean speed" value={gis.wind.mean_speed_ms} unit="m/s" testid="wind-speed"
                  hint={gis.wind.mean_speed_source} />
              </div>
              <p className="text-xs text-slate-600 mt-2">{gis.wind.guidance}</p>
              {gis.wind.is875_design && (
                <div className="mt-3 border rounded-sm px-3 py-2" data-testid="wind-design-block">
                  <div className="text-xs font-semibold text-slate-700 uppercase tracking-wide">
                    {gis.wind.is875_design.code} — structural design wind
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
                    <Metric label="Basic speed Vb" value={gis.wind.is875_design.basic_wind_speed_vb_ms} unit="m/s"
                      testid="wind-vb" />
                    <Metric label="Design speed Vz" value={gis.wind.is875_design.design_wind_speed_terrain2_ms} unit="m/s"
                      testid="wind-vz" />
                    <Metric label="Pressure pz" value={gis.wind.is875_design.design_wind_pressure_terrain2_knm2} unit="kN/m²"
                      testid="wind-pz" />
                    <Metric label="At height" value={gis.wind.is875_design.design_height_m} unit="m (terrain 2)"
                      testid="wind-design-height" />
                  </div>
                  <p className="text-[11px] text-slate-500 mt-2">{gis.wind.is875_design.note}</p>
                </div>
              )}
            </Section>

            <Section title={`Accessibility — ${gis.accessibility.score}/100`} testid="gis-accessibility-section">
              <ul className="space-y-1 text-sm">
                {gis.accessibility.notes.map((n, i) => (
                  <li key={i} className="text-slate-700" data-testid={`access-note-${i}`}>
                    · {n}
                  </li>
                ))}
              </ul>
            </Section>
          </div>

          <Section
            title="AI site analysis"
            description="A plain-language summary generated from the suitability score, buildability flags and every analysis above"
            testid="gis-ai-section"
            actions={
              <Button onClick={runAi} disabled={aiBusy || readOnly || !aiReady} variant="ai" className="rounded-sm h-8" data-testid="run-ai-summary-button">
                <Sparkles className={`h-3.5 w-3.5 mr-1.5 ${aiBusy ? "animate-pulse" : ""}`} />
                {aiBusy ? "Generating…" : gis.ai_summary ? "Regenerate" : "Generate AI analysis"}
              </Button>
            }
          >
            {gis.ai_summary ? (
              <>
                <Markdown text={gis.ai_summary.text} />
                <p className="text-[11px] text-slate-400 font-mono mt-3">
                  {gis.ai_summary.model} · {dt(gis.ai_summary.generated_at)}
                </p>
              </>
            ) : !aiReady ? (
              <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-sm px-3 py-2" data-testid="ai-not-configured">
                AI is not configured on the server. {aiStatus?.detail}
              </p>
            ) : (
              <p className="text-sm text-slate-500">
                Generate a natural-language summary of this site's strengths, risks and design recommendations.
              </p>
            )}
          </Section>
          <ConsultantPanel
            projectId={projectId}
            saved={project?.ai?.consult}
            defaultTopic="flood"
            readOnly={readOnly}
            aiReady={aiReady}
            gis={gis}
            testid="ai-consult-gis"
          />
        </>
      )}
    </div>
  );
}
