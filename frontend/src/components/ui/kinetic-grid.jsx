import { useCallback, useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

// ─── Constants ────────────────────────────────────────────────────────────

const CELL_SIZE = 55;
const INFLUENCE_RADIUS = 260;
const MAX_WARP = 24;
const DOT_SPACING = 28;
const LERP_SPEED = 0.08;
const NODE_BASE_RADIUS = 1.8;
const NODE_ACTIVE_RADIUS = 3.2;

// Every colour the grid needs, per background it's meant to sit on. The
// resting (inactive) line/node/dot colours are NOT just a dimmer version of
// "active" -- they're white-based in the original component because it only
// ever shipped on dark grounds. Reusing that on a light background would
// render a white grid on white, i.e. nothing, so light gets its own
// dark-on-light resting palette, not a swapped accent colour on unchanged
// resting values.
const THEMES = {
  light: {
    bg: "#ffffff",
    dot: "rgba(37, 99, 235, 0.10)",
    lineBase: { r: 37, g: 99, b: 235, a: 0.08 },
    nodeBase: { r: 37, g: 99, b: 235, a: 0.16 },
    lineActive: { r: 37, g: 99, b: 235, a: 0.85 },
    nodeActive: { r: 37, g: 99, b: 235, a: 1 },
    glow: "37,99,235",
    ripple: "59,130,246",
  },
  dark: {
    bg: "#161618",
    dot: "rgba(255,255,255,0.05)",
    lineBase: { r: 255, g: 255, b: 255, a: 0.13 },
    nodeBase: { r: 255, g: 255, b: 255, a: 0.2 },
    lineActive: { r: 74, g: 158, b: 255, a: 0.9 },
    nodeActive: { r: 74, g: 158, b: 255, a: 1 },
    glow: "74,158,255",
    ripple: "100,180,255",
  },
  monochrome: {
    bg: "#000000",
    dot: "rgba(255,255,255,0.05)",
    lineBase: { r: 255, g: 255, b: 255, a: 0.13 },
    nodeBase: { r: 255, g: 255, b: 255, a: 0.2 },
    lineActive: { r: 255, g: 255, b: 255, a: 0.9 },
    nodeActive: { r: 255, g: 255, b: 255, a: 1 },
    glow: "255,255,255",
    ripple: "255,255,255",
  },
};

// ─── Helpers ──────────────────────────────────────────────────────────────

function lerpN(a, b, t) {
  return a + (b - a) * t;
}

function lerpColor(base, active, t) {
  const r = Math.round(lerpN(base.r, active.r, t));
  const g = Math.round(lerpN(base.g, active.g, t));
  const b = Math.round(lerpN(base.b, active.b, t));
  const a = lerpN(base.a, active.a, t);
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
}

/**
 * Full-viewport canvas grid that warps toward the pointer and ripples on
 * click. Fixed to the viewport regardless of where it's mounted in the tree,
 * so it works equally as a section background (wrapping `children`) or as a
 * bare app-wide background (mounted with none).
 *
 * @param {"light"|"dark"|"monochrome"} theme
 */
export default function KineticGrid({ children, className, theme = "dark" }) {
  const canvasRef = useRef(null);

  const mouseRef = useRef({ x: -9999, y: -9999 });
  const targetMouseRef = useRef({ x: -9999, y: -9999 });
  const ripplesRef = useRef([]);
  const rafRef = useRef(0);
  const sizeRef = useRef({ w: 0, h: 0 });
  const dprRef = useRef(1);

  const getWarpedPoint = useCallback((gx, gy, col, row, mouse, ripples, cols, rows) => {
    // Edge pin -- smoothly locks boundary rows/cols in place.
    const edgeMargin = 1.5;
    const colPin = Math.min(col / edgeMargin, (cols - 1 - col) / edgeMargin, 1);
    const rowPin = Math.min(row / edgeMargin, (rows - 1 - row) / edgeMargin, 1);
    const pinFactor = colPin * colPin * rowPin * rowPin;

    const dx = gx - mouse.x;
    const dy = gy - mouse.y;
    const dist = Math.sqrt(dx * dx + dy * dy);

    const proximity = Math.max(0, 1 - dist / INFLUENCE_RADIUS) * pinFactor;

    let rx = 0;
    let ry = 0;
    for (const r of ripples) {
      const rdx = gx - r.x;
      const rdy = gy - r.y;
      const rdist = Math.sqrt(rdx * rdx + rdy * rdy);
      const waveWidth = 55;
      const diff = rdist - r.radius;
      if (Math.abs(diff) < waveWidth) {
        const strength = (1 - Math.abs(diff) / waveWidth) * r.opacity * 18 * pinFactor;
        const angle = Math.atan2(rdy, rdx);
        const sign = diff < 0 ? -1 : 1;
        rx += Math.cos(angle) * strength * sign * -1;
        ry += Math.sin(angle) * strength * sign * -1;
      }
    }

    if (dist < INFLUENCE_RADIUS && dist > 0 && pinFactor > 0) {
      const t = dist / INFLUENCE_RADIUS;
      const eased = t < 0.01 ? 0 : (1 - t) * (1 - t) * Math.min(1, dist / 60);
      const warpAmt = eased * MAX_WARP * pinFactor;
      const angle = Math.atan2(dy, dx);
      return {
        pt: { x: gx - Math.cos(angle) * warpAmt + rx, y: gy - Math.sin(angle) * warpAmt + ry },
        proximity,
      };
    }

    return { pt: { x: gx + rx, y: gy + ry }, proximity };
  }, []);

  const draw = useCallback(
    (now) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const { w: W, h: H } = sizeRef.current;
      const mouse = mouseRef.current;
      const ripples = ripplesRef.current;
      const t = THEMES[theme] ?? THEMES.dark;

      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = t.bg;
      ctx.fillRect(0, 0, W, H);

      ctx.fillStyle = t.dot;
      for (let x = DOT_SPACING / 2; x < W; x += DOT_SPACING) {
        for (let y = DOT_SPACING / 2; y < H; y += DOT_SPACING) {
          ctx.beginPath();
          ctx.arc(x, y, 0.7, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      for (let i = ripples.length - 1; i >= 0; i--) {
        const r = ripples[i];
        const age = (now - r.born) / 1000;
        r.radius = Math.max(0, age * 400);
        r.opacity = Math.max(0, 1 - age * 1.2);
        if (r.opacity <= 0) ripples.splice(i, 1);
      }

      const cols = Math.max(2, Math.ceil(W / CELL_SIZE)) + 1;
      const rows = Math.max(2, Math.ceil(H / CELL_SIZE)) + 1;
      const cellW = W / (cols - 1);
      const cellH = H / (rows - 1);

      const pts = [];
      const prox = [];

      for (let row = 0; row < rows; row++) {
        pts[row] = [];
        prox[row] = [];
        for (let col = 0; col < cols; col++) {
          const { pt, proximity } = getWarpedPoint(col * cellW, row * cellH, col, row, mouse, ripples, cols, rows);
          pts[row][col] = pt;
          prox[row][col] = proximity;
        }
      }

      const drawSeg = (p1, p2, pr1, pr2) => {
        const avg = (pr1 + pr2) / 2;
        const smooth = avg * avg * (3 - 2 * avg);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.strokeStyle = lerpColor(t.lineBase, t.lineActive, smooth);
        ctx.lineWidth = lerpN(0.8, 1.5, smooth);
        ctx.stroke();
      };

      ctx.lineCap = "butt";

      for (let row = 0; row < rows; row++)
        for (let col = 0; col < cols - 1; col++)
          drawSeg(pts[row][col], pts[row][col + 1], prox[row][col], prox[row][col + 1]);

      for (let col = 0; col < cols; col++)
        for (let row = 0; row < rows - 1; row++)
          drawSeg(pts[row][col], pts[row + 1][col], prox[row][col], prox[row + 1][col]);

      for (let row = 0; row < rows; row++) {
        for (let col = 0; col < cols; col++) {
          const p = pts[row][col];
          const pr = prox[row][col];
          const smooth = pr * pr * (3 - 2 * pr);
          const r = lerpN(NODE_BASE_RADIUS, NODE_ACTIVE_RADIUS, smooth);

          if (smooth > 0.3) {
            const glowR = r + lerpN(0, 6, (smooth - 0.3) / 0.7);
            const grd = ctx.createRadialGradient(p.x, p.y, r * 0.5, p.x, p.y, glowR);
            grd.addColorStop(0, `rgba(${t.glow},${(smooth * 0.3).toFixed(3)})`);
            grd.addColorStop(1, `rgba(${t.glow},0)`);
            ctx.beginPath();
            ctx.arc(p.x, p.y, glowR, 0, Math.PI * 2);
            ctx.fillStyle = grd;
            ctx.fill();
          }

          ctx.beginPath();
          ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
          ctx.fillStyle = lerpColor(t.nodeBase, t.nodeActive, smooth);
          ctx.fill();
        }
      }

      for (const r of ripples) {
        const safeRadius = Math.max(0, r.radius);
        ctx.beginPath();
        ctx.arc(r.x, r.y, safeRadius, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(${t.ripple},${(r.opacity * 0.28).toFixed(3)})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    },
    [getWarpedPoint, theme],
  );

  const animate = useCallback(
    (now) => {
      const m = mouseRef.current;
      const target = targetMouseRef.current;
      m.x = lerpN(m.x, target.x, LERP_SPEED);
      m.y = lerpN(m.y, target.y, LERP_SPEED);
      draw(now);
      rafRef.current = requestAnimationFrame(animate);
    },
    [draw],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const setSize = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      dprRef.current = dpr;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
      sizeRef.current = { w, h };
    };

    setSize();

    if (prefersReducedMotion) {
      // One still frame with the pointer parked off-canvas -- a fully
      // resting grid, no loop, no pointer or click tracking to react to.
      draw(performance.now());
      const onResize = () => {
        setSize();
        draw(performance.now());
      };
      window.addEventListener("resize", onResize);
      return () => window.removeEventListener("resize", onResize);
    }

    window.addEventListener("resize", setSize);

    const onMouseMove = (e) => {
      targetMouseRef.current = { x: e.clientX, y: e.clientY };
    };

    const onClick = (e) => {
      ripplesRef.current.push({ x: e.clientX, y: e.clientY, radius: 0, opacity: 1, born: performance.now() });
    };

    // Mouse tracking and click ripples disabled — the grid stays static.
    // The warp-toward-cursor effect caused the whole page to look like it
    // was dragging with the pointer instead of scrolling cleanly.
    rafRef.current = requestAnimationFrame(animate);

    return () => {
      window.removeEventListener("resize", setSize);
      cancelAnimationFrame(rafRef.current);
    };
  }, [animate, draw]);

  return (
    <div
      className={cn(
        "relative w-full min-h-screen overflow-hidden",
        theme === "light" ? "bg-white" : theme === "monochrome" ? "bg-black" : "bg-[#161618]",
        className,
      )}
    >
      <canvas ref={canvasRef} className="fixed inset-0 w-full h-full z-0 pointer-events-none" />
      {children ? <div className="relative z-10 w-full h-full">{children}</div> : null}
    </div>
  );
}
