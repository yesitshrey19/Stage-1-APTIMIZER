import { useEffect, useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, Download, FileText, Globe2, Landmark, LayoutGrid, Loader2, Map, TriangleAlert } from "lucide-react";
import { api, apiError, downloadFile } from "../lib/api";
import { Section } from "../components/Field";
import { Button } from "../components/ui/button";
import { defaultTownshipArea, readTownshipParams } from "./TownshipModule";

/** Ready-to-export PDF reports for the Stage 1 (Site) modules. */
export default function SiteReportsModule({ project, projectId, goToModule, analysis }) {
  const [busy, setBusy] = useState("");
  const [gisReady, setGisReady] = useState(null);

  // The GIS result lives on the server, not in the workspace copy of the project, so ask.
  useEffect(() => {
    api.get(`/projects/${projectId}/gis`)
      .then(({ data }) => setGisReady(!!data.gis && !data.stale))
      .catch(() => setGisReady(false));
  }, [projectId]);

  const hasPlot = (project.plot?.coordinates || []).length >= 3;
  const hasLayout = !!project.site_layout?.towers;
  // Same defaults the Township module opens with, so the report matches the screen.
  const township = readTownshipParams(projectId) || {
    township_area_sqm: defaultTownshipArea(analysis),
    is_mixed_use: true,
  };
  const townshipQuery = `?township_area_sqm=${encodeURIComponent(township.township_area_sqm)}&is_mixed_use=${township.is_mixed_use}`;

  const REPORTS = [
    { id: "plot", title: "Plot & Setbacks Report", icon: Map, module: "plot",
      body: "Plot geometry, road-facing edges, and every setback checked against the NBC 2016 Part 3 minimum.",
      ready: hasPlot, hint: "Draw the plot boundary first." },
    { id: "layout", title: "Site Layout & Massing Report", icon: LayoutGrid, module: "plot",
      body: "Land budget, packed towers, achieved FAR, coverage, roads, amenities and green space from the layout engine.",
      ready: hasLayout, hint: "Generate the optimised layout in Plot & Setbacks first." },
    { id: "site", title: "Site Analysis Report", icon: Globe2, module: "gis",
      body: "Suitability, buildability, terrain, flood, seismic, wind, sun path, solar potential and surrounding context.",
      ready: gisReady, hint: gisReady === false ? "Run (or re-run) the analysis in GIS Intelligence first." : "Checking…" },
    { id: "township", title: "Township & Master Plan Report", icon: Landmark, module: "township",
      body: "Land-use zoning, sector breakdown, dwelling and population yield, and the circulation strategy.",
      ready: true, query: townshipQuery,
      note: "" },
  ];

  const slug = (project.name || "project").replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 40);

  const download = async (id, query = "") => {
    setBusy(id);
    try {
      await downloadFile(`/projects/${projectId}/reports/${id}${query}`, `${slug}_${id}.pdf`);
      toast.success(id === "site-stage" ? "Site stage report set downloaded" : "Report downloaded");
    } catch (e) {
      toast.error(e.message || apiError(e.response?.data?.detail));
    } finally {
      setBusy("");
    }
  };

  const readyCount = REPORTS.filter((r) => r.ready).length;

  return (
    <div className="space-y-4">
      <Section
        title="Site stage report set"
        description={`All Stage 1 reports in one PDF with a contents page — ${readyCount} of ${REPORTS.length} fully populated.`}
        testid="site-reports-all"
        actions={
          <Button className="rounded-sm h-8" disabled={!!busy} onClick={() => download("site-stage", townshipQuery)}
                  data-testid="download-site-stage">
            {busy === "site-stage" ? <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" /> : <Download className="h-3.5 w-3.5 mr-1.5" />}
            {busy === "site-stage" ? "Preparing…" : "Download all (PDF)"}
          </Button>
        }
      >
        <p className="text-xs text-slate-500">
          Reports are generated from the project as it is saved now. Re-download after changing the plot, the
          layout or the analysis to get updated figures.
        </p>
      </Section>

      <div className="grid gap-4 md:grid-cols-2">
        {REPORTS.map((r) => (
          <div key={r.id} className="flex flex-col rounded-sm border border-slate-200 bg-white p-4" data-testid={`report-card-${r.id}`}>
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-sm bg-blue-50 text-blue-700">
                <r.icon className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="text-sm font-semibold tracking-tight text-slate-900">{r.title}</h3>
                <p className="mt-1 text-xs leading-relaxed text-slate-500">{r.body}</p>
              </div>
            </div>
            <div className="mt-4 flex items-center justify-between gap-2 border-t border-slate-100 pt-3">
              {r.ready ? (
                <span className="flex items-center gap-1 text-[11px] font-medium text-emerald-700">
                  <CheckCircle2 className="h-3.5 w-3.5" /> {r.note || "Ready to export"}
                </span>
              ) : (
                <button type="button" onClick={() => goToModule?.(r.module)}
                        className="flex items-center gap-1 text-left text-[11px] font-medium text-amber-700 hover:underline">
                  <TriangleAlert className="h-3.5 w-3.5 shrink-0" /> {r.hint}
                </button>
              )}
              <Button size="sm" variant="outline" className="h-7 shrink-0 rounded-sm text-xs" disabled={!!busy}
                      onClick={() => download(r.id, r.query || "")} data-testid={`download-report-${r.id}`}>
                {busy === r.id ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <FileText className="h-3 w-3 mr-1" />}
                PDF
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
