import PlotModule from "./PlotModule";
import DevControlsModule from "./DevControlsModule";

/** Plot geometry and the development controls (setbacks, FAR, height) on one page. */
export default function SiteModule(props) {
  return (
    <div className="space-y-4">
      <PlotModule {...props} goToModule={() =>
        document.getElementById("site-controls")?.scrollIntoView({ behavior: "smooth" })
      } />
      <div id="site-controls" className="scroll-mt-24 space-y-2">
        <h2 className="text-sm font-semibold tracking-tight text-slate-900">Setbacks &amp; development controls</h2>
        <DevControlsModule {...props} />
      </div>
    </div>
  );
}
