import { useEffect, useMemo, useRef, useState } from "react";
import { Search, X, ArrowUpRight, Layers } from "lucide-react";
import { WORKSPACE_CATALOG, WORKSPACE_MODULES, PARAMETER_COUNT } from "../../lib/workspaceCatalog";
import { Input } from "../ui/input";
import { Button } from "../ui/button";

export const ParameterDirectory = () => {
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("all");
  const [kind, setKind] = useState("all");
  const [inView, setInView] = useState(false);
  const sectionRef = useRef(null);

  useEffect(() => {
    const el = sectionRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        setInView(entry.isIntersecting);
      },
      { threshold: 0.05, rootMargin: "-40px 0px -40px 0px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const checkHash = () => {
      if (window.location.hash === "#parameters") {
        setInView(true);
      }
    };
    checkHash();
    window.addEventListener("hashchange", checkHash);
    return () => window.removeEventListener("hashchange", checkHash);
  }, []);

  const results = useMemo(() => WORKSPACE_CATALOG.filter(g => group === "all" || g.id === group).flatMap(g => g.modules.map(m => ({ ...m, group: g.label, parameters: m.parameters.filter(p =>
    (kind === "all" || p.kind === kind) && `${m.name} ${p.name} ${p.unit} ${p.basis}`.toLowerCase().includes(query.trim().toLowerCase())
  ) })).filter(m => m.parameters.length)), [query, group, kind]);
  const count = results.reduce((n, m) => n + m.parameters.length, 0);
  const reset = () => { setQuery(""); setGroup("all"); setKind("all"); };

  return (
    <section ref={sectionRef} id="parameters" className="landing-section !pb-6 bg-white scroll-mt-20 relative" data-testid="landing-parameters">
      {/* Floating Vertical Group Dock on the right — only appears when in parameters section */}
      {inView && (
        <aside
          aria-label="Module groups"
          data-testid="parameter-vertical-dock"
          className="fixed right-3 md:right-5 top-1/2 -translate-y-1/2 z-40 flex flex-col gap-1 p-2 bg-white/95 backdrop-blur-md rounded-xl shadow-xl border border-slate-200/90 animate-in fade-in slide-in-from-right-3 duration-300 max-w-[210px]"
        >
          <div className="px-2 py-1 border-b border-slate-100 flex items-center justify-between mb-0.5">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-1">
              <Layers size={11} className="text-blue-600" /> Groups
            </span>
            <span className="text-[10px] font-mono text-blue-600 bg-blue-50 px-1 py-0.2 rounded">
              {results.length}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            {[{ id: "all", label: "All modules" }, ...WORKSPACE_CATALOG].map((g) => {
              const active = group === g.id;
              return (
                <button
                  key={g.id}
                  onClick={() => setGroup(g.id)}
                  aria-pressed={active}
                  data-testid={`parameter-group-${g.id}`}
                  className={`w-full text-left rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                    active
                      ? "bg-blue-600 text-white shadow-2xs font-semibold"
                      : "text-slate-600 hover:bg-slate-100 hover:text-blue-700"
                  }`}
                >
                  {g.label}
                </button>
              );
            })}
          </div>
        </aside>
      )}

      <div className="landing-container">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="landing-eyebrow">02 / Inside the workspace</p>
            <h2 className="landing-title">The parameters behind the project.</h2>
            <p className="mt-3 max-w-2xl text-sm leading-relaxed text-slate-600">{WORKSPACE_MODULES.length} site modules. Inputs, calculated values and estimates—clearly distinguished.</p>
          </div>
          <span className="font-mono text-xs text-slate-500" data-testid="parameter-directory-total">{PARAMETER_COUNT} listed parameters</span>
        </div>

        <div className="mt-8 grid gap-3 sm:grid-cols-[1fr_180px]">
          <div className="relative">
            <Search className="absolute left-3 top-3 h-4 w-4 text-slate-400" />
            <Input aria-label="Search parameters" data-testid="parameter-search" placeholder="Search parameters, units or codes…" value={query} onChange={e => setQuery(e.target.value)} className="h-11 pl-10 pr-10 bg-slate-50" />
            {query && <button onClick={() => setQuery("")} aria-label="Clear search" data-testid="parameter-search-clear" className="absolute right-2 top-2 p-1.5 text-slate-500 hover:text-blue-700"><X size={16} /></button>}
          </div>
          <select value={kind} onChange={e => setKind(e.target.value)} aria-label="Parameter type" data-testid="parameter-type-filter" className="h-11 rounded-md border bg-white px-3 text-sm focus:ring-2 focus:ring-blue-600">
            <option value="all">All parameter types</option>{["Input", "Calculated", "Estimate"].map(t => <option key={t}>{t}</option>)}
          </select>
        </div>

        <div className="my-6 flex flex-wrap items-center justify-between gap-2 border-b pb-4">
          <p aria-live="polite" className="font-mono text-xs text-slate-500" data-testid="parameter-result-count">{count} parameters · {results.length} modules</p>
          {(query || group !== "all" || kind !== "all") && <Button variant="ghost" size="sm" onClick={reset} data-testid="parameter-reset">Reset filters <X size={14} className="ml-2" /></Button>}
        </div>

        {results.length ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5 items-start">
            {results.map(m => (
              <article key={m.id} className="rounded-md border border-slate-200 bg-slate-50/70 p-4 transition-all hover:bg-slate-50 hover:shadow-xs min-w-0" data-testid={`parameter-module-${m.id}`}>
                <div className="flex items-start justify-between gap-2 border-b border-slate-200 pb-2.5">
                  <div className="flex items-start gap-2.5 min-w-0">
                    <m.icon size={18} className="mt-0.5 shrink-0 text-blue-600" />
                    <div className="min-w-0">
                      <h3 className="text-sm font-semibold text-slate-900 truncate">{m.name}</h3>
                      <p className="mt-0.5 text-[11px] leading-tight text-slate-500 line-clamp-1">{m.note}</p>
                    </div>
                  </div>
                  <span className="shrink-0 text-[10px] font-mono text-slate-500 bg-white border border-slate-200 px-1.5 py-0.5 rounded-sm">
                    {m.parameters.length}
                  </span>
                </div>

                {/* Horizontal wrapping chips without horizontal scrollbar */}
                <div className="mt-3 flex flex-wrap gap-1.5 items-center">
                  {m.parameters.map(p => {
                    const slug = p.name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
                    return (
                      <details
                        key={p.name}
                        className="parameter-detail group relative inline-block"
                        data-testid={`parameter-detail-${m.id}-${slug}`}
                      >
                        <summary
                          className="flex cursor-pointer select-none items-center gap-1.5 rounded-md border border-slate-200/90 bg-white px-2 py-1 text-[11px] font-medium text-slate-800 shadow-2xs transition-colors hover:border-blue-400 hover:bg-slate-50 list-none [&::-webkit-details-marker]:hidden focus-visible:outline-blue-600 group-open:border-blue-500 group-open:bg-blue-50/50"
                          data-testid={`parameter-toggle-${m.id}-${slug}`}
                        >
                          <span className="leading-tight">{p.name}</span>
                          {p.unit && <span className="font-mono text-[10px] text-slate-400 leading-tight">({p.unit})</span>}
                          <span className={`shrink-0 rounded px-1 py-0.2 text-[9px] font-medium leading-none ${
                            p.kind === "Input"
                              ? "bg-blue-50 text-blue-700"
                              : p.kind === "Calculated"
                              ? "bg-emerald-50 text-emerald-800"
                              : "bg-amber-50 text-amber-800"
                          }`}>
                            {p.kind}
                          </span>
                        </summary>
                        <div className="absolute left-0 top-full mt-1.5 z-30 w-64 max-w-[calc(100vw-3rem)] rounded-md border border-slate-200 bg-white p-2.5 shadow-xl text-[11px] leading-relaxed text-slate-600 animate-in fade-in zoom-in-95 duration-150">
                          <div className="font-semibold text-slate-900 mb-0.5">{p.name} {p.unit && <span className="font-mono text-slate-400 font-normal">({p.unit})</span>}</div>
                          <div className="text-[10px] text-blue-600 font-medium mb-1">{p.kind} · {m.name}</div>
                          <p className="border-t border-slate-100 pt-1 text-slate-600">{p.basis}</p>
                        </div>
                      </details>
                    );
                  })}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="py-14 text-center" data-testid="parameter-empty-state">
            <Search className="mx-auto text-slate-300" size={28} />
            <h3 className="mt-4 text-sm font-medium">No matching parameters</h3>
            <Button variant="link" onClick={reset} data-testid="parameter-empty-reset">Show all parameters <ArrowUpRight size={14} className="ml-1" /></Button>
          </div>
        )}

        <p className="mt-5 text-xs leading-relaxed text-slate-400" data-testid="parameter-directory-disclaimer">A curated index of the current workspace. Reference data, generated concepts and estimates require project-specific verification.</p>
      </div>
    </section>
  );
};