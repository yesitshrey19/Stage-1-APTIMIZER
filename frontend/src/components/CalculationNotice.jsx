import { AlertTriangle } from "lucide-react";

// The engine reports its basis for the whole project model. This prototype covers the
// site stage only, so lines about later-stage outputs (quantities, rates, structure) are
// left out rather than shown against figures this build does not display.
const OUT_OF_SCOPE = /\b(BOQ|INR|foundation|stair|lift|rates?|productivity|material ratios|structural|loads?)\b/i;
const inScope = (text) => !OUT_OF_SCOPE.test(text || "");
const scopeSentences = (text) =>
  (text || "").split(/(?<=\.)\s+/).filter(inScope).join(" ");

export const CalculationNotice = ({ basis }) => {
  if (!basis) return null;
  const warnings = (basis.warnings || []).filter((w) => inScope(w.message));
  const limitations = (basis.limitations || []).filter(inScope);
  return <details className="border-y border-amber-200 bg-amber-50/70 text-amber-950" data-testid="calculation-basis-notice">
    <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 px-3 py-2.5 text-xs" data-testid="calculation-basis-toggle">
      <span className="flex items-center gap-2"><AlertTriangle size={14} className="shrink-0" /><strong>Preliminary · engineer review required</strong></span>
      <span className="text-[11px]">{warnings.length} input warnings · View basis</span>
    </summary>
    <div className="border-t border-amber-200 px-4 py-3 text-xs leading-relaxed" data-testid="calculation-basis-details">
      {warnings.length > 0 && <ul className="mb-3 list-disc space-y-1 pl-4">{warnings.map(w => <li key={w.id} data-testid={`calculation-warning-${w.id}`}>{w.message}</li>)}</ul>}
      <p className="font-medium">Area method: {basis.area_method}</p><p className="mt-2">{scopeSentences(basis.precision_policy)}</p>
      <ul className="mt-3 list-disc space-y-1 pl-4">{limitations.map((text, i) => <li key={text} data-testid={`calculation-limitation-${i}`}>{text}</li>)}</ul>
      <p className="mt-3">Site checks support early decisions and do not constitute statutory approval. A qualified engineer must review before design proceeds.</p>
    </div>
  </details>;
};
