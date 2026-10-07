import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { motion, useMotionValueEvent, useReducedMotion, useScroll, useSpring, useTransform } from "framer-motion";
import { ArrowRight, ArrowDown, ArrowUpRight, Menu, X, Check, Compass, Ruler, Layers, ShieldCheck, BookOpen, AlertTriangle } from "lucide-react";
import { Brand } from "../components/Brand";
import { ScrollScene } from "../components/ScrollScene";
import { useAuth } from "../context/AuthContext";
import { Reveal, RevealItem, Stagger } from "../components/landing/Reveal";
import ReactiveButton from "../components/landing/ReactiveButton";
import CountUp from "../components/landing/CountUp";
import { ParameterDirectory } from "../components/landing/ParameterDirectory";
import { WORKSPACE_MODULES, PARAMETER_COUNT } from "../lib/workspaceCatalog";
import "./Landing.css";

const NAV = [["Workspace", "workspace"], ["Parameters", "parameters"], ["Pipeline", "pipeline"], ["Assurance", "intelligence"]];
const PIPELINE = [
  ["Boundary", "Validate the plot polygon and establish the site geometry."],
  ["Envelope", "Apply front, rear and side setbacks to the buildable area."],
  ["Circulation", "Reserve the fire-tender route and internal access roads."],
  ["Amenities", "Allocate the clubhouse, open space and shared facilities."],
  ["Tower placement", "Explore footprints, orientation, floor counts and unit yield."],
  ["Evaluation", "Check containment, spacing and supported project constraints."],
];
const CODES = [
  ["Setbacks & open space", "NBC 2016 Part 3"], ["Inter-building spacing", "NBC 2016 Part 3"],
  ["Seismic zone & site class", "IS 1893 (Part 1):2016"], ["Design wind speed", "IS 875 (Part 3):2015"],
  ["Township land-use planning", "UDPFI Guidelines"], ["Plot area & FAR", "Local DCR (indicative)"],
];

export default function Landing() {
  const { user, logout } = useAuth();
  const hero = useRef(null);
  const reduced = useReducedMotion();
  const [menu, setMenu] = useState(false);
  const [solid, setSolid] = useState(false);
  const { scrollYProgress, scrollY } = useScroll();
  // The ref must be measured in its owning component, after its DOM has committed.
  // Map [0, 0.92] -> [0, 1] so frame 60 completes while the sticky stage is still pinned in view.
  const { scrollYProgress: rawHeroProgress } = useScroll({ target: hero, offset: ["start 64px", "end end"] });
  const heroProgress = useTransform(rawHeroProgress, [0, 0.92], [0, 1]);
  // The reading bar is spring-followed: it keeps pace with the page while the
  // wheel is still settling instead of snapping on every scroll event.
  const barProgress = useSpring(scrollYProgress, { stiffness: 240, damping: 40, mass: 0.4, restDelta: 0.001 });
  // The hero copy rises and yields as the model completes. Transform and
  // opacity only -- both run on the compositor, so this costs the scroll
  // nothing, and both are inert when the hero is static (reduced motion).
  const headingY = useTransform(heroProgress, [0, 1], [0, -48]);
  const headingFade = useTransform(heroProgress, [0.4, 0.92], [1, 0.08]);
  const footFade = useTransform(heroProgress, [0.4, 0.92], [1, 0.18]);
  useMotionValueEvent(scrollY, "change", value => setSolid(previous => previous === (value > 20) ? previous : value > 20));
  const href = user ? "/projects" : "/register";
  const cta = user ? "Open workspace" : "Start a project";

  return <div className="landing-page" data-testid="landing-page">
    <a href="#workspace" className="landing-skip" data-testid="landing-skip-content">Skip to workspace overview</a>
    <motion.div aria-hidden="true" className="fixed inset-x-0 top-0 z-50 h-0.5 origin-left bg-blue-600" style={{ scaleX: barProgress }} data-testid="landing-scroll-progress" />
    <header className={`landing-nav ${solid ? "landing-nav-solid" : ""}`} data-testid="landing-nav">
      <div className="landing-container flex h-16 items-center justify-between gap-3">
        <Link to="/" aria-label="Aptimizer home" data-testid="landing-nav-brand"><Brand testid="landing-nav-brand-lockup" markClass="h-8 w-auto" wordClass="text-lg" /></Link>
        <nav className="hidden items-center gap-7 lg:flex" aria-label="Main navigation">{NAV.map(([name, id]) => <a key={id} href={`#${id}`} data-testid={`landing-nav-${id}`} className="landing-nav-link">{name}</a>)}</nav>
        <div className="flex items-center gap-2">
          {user ? (
            <div className="flex items-center gap-2 mr-1">
              <span className="hidden md:inline-block text-xs text-slate-500 font-mono truncate max-w-[140px]">
                {user.email || user.name || "Signed in"}
              </span>
              <button
                type="button"
                onClick={logout}
                className="text-xs text-slate-500 hover:text-red-600 px-1.5 py-1 transition-colors"
                data-testid="landing-nav-sign-out"
              >
                Sign out
              </button>
            </div>
          ) : (
            <Link
              to="/login"
              className="text-xs font-semibold text-slate-700 hover:text-blue-600 px-3 py-1.5 rounded-sm border border-slate-200 hover:border-blue-400 bg-white shadow-2xs mr-1 transition-colors"
              data-testid="landing-nav-sign-in"
            >
              Sign in
            </Link>
          )}
          <ReactiveButton as={Link} to={href} size="sm" icon={<ArrowUpRight size={15} />} className="!px-3" data-testid="landing-nav-primary-cta">{cta}</ReactiveButton>
          <button className="p-2 lg:hidden text-slate-600" aria-label={menu ? "Close navigation" : "Open navigation"} aria-expanded={menu} aria-controls="landing-mobile-nav" onClick={() => setMenu(!menu)} data-testid="landing-menu-toggle">{menu ? <X size={20} /> : <Menu size={20} />}</button>
        </div>
      </div>
      {menu && (
        <nav id="landing-mobile-nav" className="landing-container grid gap-1 border-t py-4 lg:hidden" aria-label="Mobile navigation" data-testid="landing-mobile-nav">
          {NAV.map(([name, id]) => (
            <a key={id} href={`#${id}`} onClick={() => setMenu(false)} data-testid={`landing-mobile-${id}`} className="py-2 text-sm text-slate-700">
              {name}
            </a>
          ))}
          {!user && (
            <Link to="/login" onClick={() => setMenu(false)} data-testid="landing-mobile-login" className="py-2 text-sm font-semibold text-blue-600">
              Sign in
            </Link>
          )}
          {user && (
            <button
              type="button"
              onClick={() => { logout(); setMenu(false); }}
              data-testid="landing-mobile-logout"
              className="py-2 text-sm text-left font-medium text-red-600"
            >
              Sign out ({user.email || user.name})
            </button>
          )}
        </nav>
      )}
    </header>

    <main>
      <section ref={hero} className={`landing-hero ${reduced ? "landing-hero-static" : ""}`} data-testid="landing-hero">
        <div className="landing-stage">
          <ScrollScene progress={heroProgress} className="landing-scene-media" />
          <motion.div className="landing-hero-heading landing-container" style={{ y: headingY, opacity: headingFade }}>
            <p className="landing-eyebrow">Aptimizer / Stage 1 — Site</p>
            <h1 data-testid="landing-heading">From the first line.<br /><span>To the bigger picture.</span></h1>
          </motion.div>
          <motion.div className="landing-hero-bottom landing-container" style={{ opacity: footFade }}>
            <p className="max-w-xs text-xs leading-relaxed text-slate-700">Plot. Setbacks. Site context.</p>
            <a href="#workspace" data-testid="landing-explore" className="flex items-center gap-3 text-xs font-medium text-slate-800">Explore Aptimizer <ArrowDown size={16} /></a>
          </motion.div>
        </div>
      </section>

      <section id="workspace" className="landing-section landing-overview scroll-mt-16" data-testid="landing-workspace">
        <div className="landing-container">
          <div className="grid items-end gap-10 lg:grid-cols-[1.2fr_1fr]">
            <Reveal><p className="landing-eyebrow">01 / Stage 1 — Site</p><h2 className="landing-display">Understand the land.<br /><span className="text-blue-600">Before you design on it.</span></h2></Reveal>
            <Reveal delay={0.06}><p className="text-sm leading-relaxed text-slate-600">Draw the plot boundary, apply NBC setbacks and development controls, generate an optimised site layout, analyse terrain, flood, seismic, wind and sun, and zone a township master plan — all from one project model.</p>
              <ReactiveButton as={Link} to={href} icon={<ArrowRight size={16} />} className="mt-6" data-testid="landing-hero-primary-cta">{cta}</ReactiveButton></Reveal>
          </div>
          <div className="landing-statline" data-testid="landing-metrics">
            {[[WORKSPACE_MODULES.length, "site modules"], [PARAMETER_COUNT, "listed parameters"], [6, "layout stages"], [1, "project stage", 2]].map(([value, label, pad = 0], i) => <div key={label} data-testid={`landing-metric-${i}`}><strong><CountUp value={value} pad={pad} duration={1.2 + i * 0.15} /></strong><span>{label}</span></div>)}
          </div>
          <Stagger className="grid gap-7 sm:grid-cols-2 lg:grid-cols-4" step={0.04}>
            {WORKSPACE_MODULES.map((m, i) => <RevealItem key={m.id} className="min-w-0" data-testid={`landing-workspace-module-${m.id}`}>
              <div className="flex items-center justify-between border-t border-slate-300 pt-4"><m.icon size={19} className="text-blue-600" /><span className="font-mono text-[10px] text-slate-400">0{i + 1}</span></div>
              <h3 className="mt-4 text-sm font-semibold">{m.name}</h3>
              <p className="mt-2 text-xs leading-relaxed text-slate-500">{m.note}</p>
            </RevealItem>)}
          </Stagger>
        </div>
      </section>

      <ParameterDirectory />

      <section id="pipeline" className="landing-section !pt-8 landing-pipeline scroll-mt-16" data-testid="landing-pipeline">
        <div className="landing-container"><Reveal><p className="landing-eyebrow">03 / The layout pipeline</p><h2 className="landing-title">From boundary to a considered scheme.</h2></Reveal>
          <Stagger className="mt-10 grid gap-x-12 gap-y-8 sm:grid-cols-2 lg:grid-cols-3" step={0.05}>{PIPELINE.map(([name, body], i) => <RevealItem key={name} data-testid={`landing-pipeline-step-${i}`}>
            <div className="border-t border-slate-300 pt-5"><span className="font-mono text-xs text-blue-600">0{i + 1}</span><h3 className="mt-4 text-base font-semibold">{name}</h3><p className="mt-2 max-w-xs text-sm leading-relaxed text-slate-600">{body}</p></div>
          </RevealItem>)}</Stagger>
        </div>
      </section>

      <section id="modules" className="landing-section bg-white scroll-mt-16" data-testid="landing-modules">
        <div className="landing-container">
          <div className="flex items-end justify-between gap-8">
            <Reveal><p className="landing-eyebrow">04 / Engineering references</p><h2 className="landing-title">A reference behind the check.</h2><p className="mt-4 max-w-2xl text-sm leading-relaxed text-slate-600">Site checks reference Indian standards and local controls. Confirm the applicable edition, amendments and project-specific requirements with your engineer.</p></Reveal>
            <BookOpen className="mb-1 hidden shrink-0 text-blue-600 sm:block" size={30} />
          </div>
          <div className="mt-8 grid gap-x-10 sm:grid-cols-2 lg:grid-cols-3">{CODES.map(([name, code], i) => <div key={name} data-testid={`landing-code-${i}`} className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-t border-slate-200 py-3.5"><span className="text-sm text-slate-700">{name}</span><span className="font-mono text-xs text-blue-700">{code}</span></div>)}</div>
        </div>
      </section>

      <section id="intelligence" className="landing-section landing-assurance scroll-mt-16" data-testid="landing-intelligence">
        <div className="landing-container">
          <Reveal><p className="landing-eyebrow">05 / Engineering, with accountability</p><h2 className="landing-title">Know the basis. Not just the number.</h2></Reveal>
          <div className="mt-10 grid gap-12 lg:grid-cols-[1fr_0.85fr]">
            <div className="space-y-7">{[
              [Compass, "Inputs stay visible", "Site geometry, setbacks and development controls remain distinct from computed outputs."],
              [Ruler, "Arithmetic you can follow", "Plot area, envelope and FAR figures show their inputs. Core calculations preserve precision before display formatting."],
              [Layers, "Estimates are identified", "Indicative FAR caps, regional wind and seismic references and public map data are assumptions—not field surveys."],
              [ShieldCheck, "Review is part of the workflow", "Stale analyses and save conflicts are surfaced. Outputs support early site decisions; they do not replace engineering approval."],
            ].map(([Icon, title, text], i) => <div key={title} className="flex gap-4" data-testid={`landing-assurance-${i}`}><Icon size={19} className="mt-1 shrink-0 text-blue-600" /><div><h3 className="text-sm font-semibold">{title}</h3><p className="mt-2 text-sm leading-relaxed text-slate-600">{text}</p></div></div>)}</div>
            <div>
              <div className="rounded-md border border-slate-300 bg-white" data-testid="landing-calculation-example"><div className="flex flex-wrap items-center justify-between gap-2 border-b px-5 py-4"><span className="text-sm font-medium">Permitted built-up area</span><span className="font-mono text-[10px] text-slate-500">ILLUSTRATIVE EXAMPLE</span></div>
                <dl className="px-5 py-2">{[["Plot area", "4,000.00 m²"], ["Configured FAR", "2.50"], ["Basis", "Plot area × FAR"]].map(([label, value]) => <div key={label} className="flex flex-wrap justify-between gap-2 py-3 text-sm"><dt className="text-slate-500">{label}</dt><dd className="font-mono text-xs">{value}</dd></div>)}</dl>
                <div className="flex items-center justify-between gap-3 border-t bg-blue-50 px-5 py-5"><span className="flex items-center gap-2 text-xs text-blue-800"><Check size={15} /> Result</span><strong className="font-mono text-xl text-blue-700" data-testid="landing-example-result">10,000.00 m²</strong></div>
              </div>
              <p className="mt-5 flex items-start gap-2 text-xs leading-relaxed text-slate-600" data-testid="landing-engineering-disclaimer"><AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-600" />Preliminary site planning and decision support. Not a cadastral survey, geotechnical report, or statutory approval. Qualified review is required before design proceeds.</p>
            </div>
          </div>
        </div>
      </section>
      <section className="landing-section bg-[#11231f] text-white" data-testid="landing-cta"><div className="landing-container flex flex-wrap items-center justify-between gap-8"><div><p className="font-mono text-xs text-emerald-300">YOUR NEXT PROJECT</p><h2 className="mt-4 text-lg font-semibold">Start with the site.</h2></div><ReactiveButton as={Link} to={href} size="lg" icon={<ArrowUpRight size={18} />} data-testid="landing-cta-primary">{cta}</ReactiveButton></div></section>
    </main>
    <footer className="border-t bg-white py-8" data-testid="landing-footer">
      <div className="landing-container flex flex-wrap items-center justify-between gap-6">
        <Brand testid="landing-footer-brand-lockup" markClass="h-7 w-auto" wordClass="text-base" />
        <p className="text-xs text-slate-500">Stage 1 (Site) prototype · Indian codes</p>
        <div className="flex items-center gap-5">
          {!user && <Link to="/login" className="landing-nav-link" data-testid="landing-footer-sign-in">Sign in</Link>}
          <a href="#parameters" className="landing-nav-link" data-testid="landing-footer-parameters">Parameter directory <ArrowUpRight className="inline ml-1" size={13} /></a>
        </div>
      </div>
    </footer>
  </div>;
}