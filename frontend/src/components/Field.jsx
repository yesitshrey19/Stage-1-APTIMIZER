import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

export const NumField = ({ label, value, onChange, suffix, step = 1, testid, disabled, hint }) => (
  <div className="space-y-1">
    <Label htmlFor={testid} className="text-[11px] uppercase tracking-wide text-slate-500">{label}</Label>
    <div className="relative">
      <Input
        type="number"
        id={testid}
        aria-label={label}
        step={step}
        disabled={disabled}
        data-testid={testid}
        value={value ?? ""}
        onChange={(e) => { if (e.target.value === "" || Number.isFinite(e.target.valueAsNumber)) onChange(e.target.value === "" ? 0 : e.target.valueAsNumber); }}
        className="h-9 rounded-sm font-mono text-sm pr-12"
      />
      {suffix && (
        <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-slate-400 font-mono">
          {suffix}
        </span>
      )}
    </div>
    {hint && <p className="text-[10px] leading-snug text-slate-500">{hint}</p>}
  </div>
);

export const TextField = ({ label, value, onChange, testid, disabled, placeholder }) => (
  <div className="space-y-1">
    <Label htmlFor={testid} className="text-[11px] uppercase tracking-wide text-slate-500">{label}</Label>
    <Input
      data-testid={testid}
      id={testid}
      aria-label={label}
      disabled={disabled}
      placeholder={placeholder}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className="h-9 rounded-sm text-sm"
    />
  </div>
);

export const Metric = ({ label, value, unit, testid, tone, hint }) => (
  <div className="min-w-0 border border-slate-200 bg-white rounded-sm px-3 py-2" data-testid={testid}>
    <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
    <div
      className={`break-words font-mono text-lg leading-tight ${
        tone === "danger" ? "text-red-600" : tone === "success" ? "text-emerald-600" : "text-slate-900"
      }`}
    >
      {value}
      {unit && <span className="text-[11px] text-slate-400 ml-1">{unit}</span>}
    </div>
    {hint && <div className="mt-0.5 text-[11px] text-slate-500">{hint}</div>}
  </div>
);

export const Section = ({ title, actions, children, testid, description }) => (
  <section className="border border-slate-200 bg-white rounded-sm" data-testid={testid}>
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-2.5">
      <div className="min-w-0">
        <h3 className="text-sm font-semibold tracking-tight text-slate-900">{title}</h3>
        {description && <p className="text-[11px] text-slate-500">{description}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
    </header>
    <div className="p-4">{children}</div>
  </section>
);
