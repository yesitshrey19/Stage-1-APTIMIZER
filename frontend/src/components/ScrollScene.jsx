// Hero scroll scene: a 60-frame image sequence scrubbed by page scroll.
//
// Engineered for zero-lag, deterministic 60-frame progression (01..60 and 60..01):
//  1. Frames are fetched and decoded off the main thread via createImageBitmap
//     with a 6-worker priority pool (endpoints & key keyframes first, then fill).
//  2. Scroll progress is driven by Framer Motion / Motion values and smoothed
//     with a responsive, critically-damped spring so wheel notches and trackpad
//     flicks scrub fluidly without sluggish lag or bounce.
//  3. Each repaint draws a single crisp decoded bitmap for the discrete frame
//     index (1..60) and skips work whenever the target frame index is unchanged,
//     avoiding thousands of redundant dual-canvas alpha blends and line ghosting.
//  4. Endpoint frames (1 and 60) always settle even if the visitor scrolls
//     rapidly past the hero boundary.
import { useEffect, useRef } from "react";
import { useReducedMotion, useSpring } from "framer-motion";
import { clamp } from "motion";

const FRAMES = 60;
const LAST = FRAMES - 1;

// Priority decode order: start (0), end (59), midpoint (30), quarters, eighths, then sequential fill.
const PRIORITY = [0, LAST, 30, 15, 45, 7, 22, 37, 52, 3, 11, 18, 26, 33, 41, 48, 56];
const ORDER = [
  ...PRIORITY,
  ...Array.from({ length: FRAMES }, (_, i) => i).filter((i) => !PRIORITY.includes(i)),
];

/** Contain the frame without cropping so the building, dimension lines and mark stay intact. */
const drawContained = (ctx, image, width, height) => {
  const iw = image.width || image.naturalWidth;
  const ih = image.height || image.naturalHeight;
  if (!iw || !ih) return;
  const scale = Math.min(width / iw, height / ih);
  const dw = iw * scale;
  const dh = ih * scale;
  ctx.drawImage(image, (width - dw) / 2, (height - dh) / 2, dw, dh);
};

/** Find the closest decoded frame when the exact target frame is still loading. */
const findNearestFrame = (frames, target) => {
  if (frames[target]) return { image: frames[target], index: target };
  for (let delta = 1; delta < FRAMES; delta += 1) {
    const lo = target - delta;
    if (lo >= 0 && frames[lo]) return { image: frames[lo], index: lo };
    const hi = target + delta;
    if (hi <= LAST && frames[hi]) return { image: frames[hi], index: hi };
  }
  return null;
};

export function ScrollScene({ progress, className = "" }) {
  const canvas = useRef(null);
  const poster = useRef(null);
  const reduced = useReducedMotion();

  // Critically damped, high-response spring: absorbs discrete mouse-wheel steps
  // while tracking scroll position tightly all the way to frames 1 and 60.
  const smoothed = useSpring(progress, {
    stiffness: 340,
    damping: 38,
    mass: 0.32,
    restDelta: 0.0002,
    restSpeed: 0.0002,
  });

  useEffect(() => {
    if (reduced) return undefined;
    const node = canvas.current;
    const ctx = node?.getContext("2d", { alpha: false, desynchronized: true });
    if (!ctx) return undefined;

    let disposed = false;
    let onScreen = true;
    let width = 0;
    let height = 0;
    let dpr = 1;
    let paintedIndex = -1; // discrete frame index (0..59) currently drawn on canvas
    let stale = true;      // true when resize or newly decoded frame needs a repaint
    let raf = 0;
    const frames = new Array(FRAMES).fill(null);

    const resolveTargetIndex = () => {
      const raw = clamp(0, 1, progress.get());
      const springVal = clamp(0, 1, smoothed.get());
      // Snap cleanly at the exact top (0) and bottom (1) of the scroll range so
      // frames 1 and 60 are guaranteed even on rapid flicks.
      if (raw <= 0.002 && springVal < 0.025) return 0;
      if (raw >= 0.998 && springVal > 0.975) return LAST;
      return Math.round(springVal * LAST);
    };

    const paint = (targetIndex) => {
      if (disposed || !width || !height || document.hidden) return;
      const clampedIndex = clamp(0, LAST, targetIndex);
      node.dataset.frame = String(clampedIndex + 1);

      const resolved = findNearestFrame(frames, clampedIndex);
      if (!resolved) return;

      if (!stale && resolved.index === paintedIndex) return;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.fillStyle = "#d7d7ce";
      ctx.fillRect(0, 0, width, height);
      drawContained(ctx, resolved.image, width, height);

      paintedIndex = resolved.index;
      // Keep stale=true if we only drew a fallback neighbor while the exact frame is still decoding.
      stale = resolved.index !== clampedIndex;

      if (node.style.opacity !== "1") {
        node.style.opacity = "1";
        if (poster.current) poster.current.style.opacity = "0";
      }
    };

    const schedule = () => {
      if (!disposed && !raf) raf = requestAnimationFrame(render);
    };

    const render = () => {
      raf = 0;
      if (disposed || document.hidden) return;
      const targetIndex = resolveTargetIndex();
      // Always allow endpoint settling or pending frame updates even as the hero leaves the viewport.
      if (!onScreen && paintedIndex === targetIndex && !stale) return;
      paint(targetIndex);
    };

    const resize = () => {
      const w = node.clientWidth;
      const h = node.clientHeight;
      if (!w || !h || (w === width && h === height)) return;
      width = w;
      height = h;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      node.width = Math.round(width * dpr);
      node.height = Math.round(height * dpr);
      paintedIndex = -1;
      stale = true;
      schedule();
    };

    const observer = new ResizeObserver(resize);
    observer.observe(node);
    resize();

    const visibility = new IntersectionObserver(
      ([entry]) => {
        onScreen = entry.isIntersecting;
        stale = true;
        schedule();
      },
      { rootMargin: "320px 0px" }
    );
    visibility.observe(node);

    const unsubscribeSmoothed = smoothed.on("change", schedule);
    const unsubscribeRaw = progress.on("change", (v) => {
      if (v <= 0.005 || v >= 0.995) {
        stale = true;
      }
      schedule();
    });

    const onVisibility = () => {
      if (!document.hidden) {
        stale = true;
        schedule();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    // 6-worker off-thread decode pool using createImageBitmap (with Image.decode fallback).
    const mobile = window.matchMedia("(max-width: 767px)").matches;
    const assetPath = mobile ? "/frames/mobile" : "/frames";
    const source = (index) => `${assetPath}/${String(index + 1).padStart(2, "0")}.webp`;
    let cursor = 0;

    const decode = async (url) => {
      try {
        if (typeof createImageBitmap === "function") {
          const response = await fetch(url, { cache: "force-cache" });
          if (!response.ok) return null;
          return await createImageBitmap(await response.blob());
        }
        const image = new Image();
        image.decoding = "async";
        image.src = url;
        await image.decode();
        return image;
      } catch {
        return null;
      }
    };

    const worker = async () => {
      while (!disposed) {
        const slot = cursor++;
        if (slot >= ORDER.length) return;
        const index = ORDER[slot];
        const frame = await decode(source(index));
        if (disposed) {
          frame?.close?.();
          return;
        }
        if (!frame) continue;
        frames[index] = frame;
        if (paintedIndex === -1 || index === resolveTargetIndex()) {
          stale = true;
          schedule();
        }
      }
    };
    for (let i = 0; i < 6; i += 1) worker();

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      unsubscribeSmoothed();
      unsubscribeRaw();
      observer.disconnect();
      visibility.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      for (const frame of frames) frame?.close?.();
    };
  }, [reduced, smoothed, progress]);

  return (
    <div className={`absolute inset-0 ${className}`} data-testid="landing-scene">
      <img
        ref={poster}
        src="/frames/01.webp"
        alt="Architectural model of a residential building"
        fetchPriority="high"
        className="absolute inset-0 h-full w-full object-contain transition-opacity duration-300"
        data-testid="landing-scene-poster"
      />
      {!reduced && (
        <canvas
          ref={canvas}
          aria-hidden="true"
          data-testid="landing-scene-canvas"
          className="absolute inset-0 h-full w-full opacity-0 transition-opacity duration-300"
        />
      )}
    </div>
  );
}
