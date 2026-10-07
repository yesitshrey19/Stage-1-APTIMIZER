// Hero drawing: a plot boundary resolving into a packed site layout.
//
// This mirrors the real pipeline rather than decorating the page. The sequence
// is boundary, then setback envelope, then circulation, then towers, which is
// the order the engine actually produces them and the order a site plan is
// drafted by hand. Line weights follow drawing convention: boundary heaviest,
// envelope next, annotation lightest.
import { motion, useReducedMotion } from "framer-motion";

// Drawn boundary of the plot.
const PLOT = "M40,44 L302,32 L440,124 L418,300 L152,332 L40,252 Z";

// Buildable envelope after per edge setbacks.
const ENVELOPE = "M70,72 L296,61 L410,136 L392,276 L166,302 L70,238 Z";

// Perimeter fire tender ring, held just inside the envelope.
const RING = "M86,88 L292,78 L392,143 L376,264 L174,287 L86,228 Z";

// Tower footprints, laid on the packing grid.
const TOWERS = [
  { x: 104, y: 104, w: 56, h: 40 },
  { x: 172, y: 99, w: 56, h: 40 },
  { x: 240, y: 100, w: 56, h: 40 },
  { x: 310, y: 141, w: 52, h: 40 },
  { x: 104, y: 158, w: 56, h: 40 },
  { x: 172, y: 154, w: 56, h: 40 },
  { x: 240, y: 154, w: 56, h: 40 },
  { x: 310, y: 196, w: 52, h: 40 },
  { x: 120, y: 213, w: 56, h: 40 },
  { x: 192, y: 209, w: 56, h: 40 },
  { x: 264, y: 209, w: 56, h: 40 },
];

// Amenity block, placed against circulation in residual open space.
const AMENITY = { x: 186, y: 262, w: 92, h: 26 };

export default function BlueprintPlot({ className }) {
  const reduced = useReducedMotion();

  // With reduced motion the drawing is simply present, fully rendered.
  const draw = (delay, duration = 1.1) =>
    reduced
      ? { initial: { pathLength: 1, opacity: 1 }, animate: { pathLength: 1, opacity: 1 } }
      : {
          initial: { pathLength: 0, opacity: 0 },
          animate: { pathLength: 1, opacity: 1 },
          transition: {
            pathLength: { duration, delay, ease: [0.22, 0.61, 0.36, 1] },
            opacity: { duration: 0.2, delay },
          },
        };

  return (
    <svg
      viewBox="0 0 480 360"
      className={className}
      role="img"
      aria-label="Site plan drawing: plot boundary, setback envelope, perimeter road and packed tower footprints"
      data-testid="landing-blueprint"
    >
      <defs>
        <pattern id="ap-grid" width="16" height="16" patternUnits="userSpaceOnUse">
          <path d="M16 0 L0 0 0 16" fill="none" stroke="#2563eb" strokeOpacity="0.10" strokeWidth="1" />
        </pattern>
        <pattern id="ap-grid-major" width="80" height="80" patternUnits="userSpaceOnUse">
          <path d="M80 0 L0 0 0 80" fill="none" stroke="#2563eb" strokeOpacity="0.16" strokeWidth="1" />
        </pattern>
      </defs>

      {/* Drafting grid */}
      <rect width="480" height="360" fill="url(#ap-grid)" />
      <rect width="480" height="360" fill="url(#ap-grid-major)" />

      {/* Open space inside the envelope, laid under everything else */}
      <motion.path
        d={ENVELOPE}
        fill="#2563eb"
        fillOpacity="0.045"
        initial={reduced ? { opacity: 1 } : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.6, delay: reduced ? 0 : 0.8 }}
      />

      {/* 1. Plot boundary, heaviest line */}
      <motion.path
        d={PLOT}
        fill="none"
        stroke="#0f172a"
        strokeWidth="2.5"
        strokeLinejoin="round"
        {...draw(0.1, 1.3)}
      />

      {/* 2. Setback envelope, dashed as an offset construction line */}
      <motion.path
        d={ENVELOPE}
        fill="none"
        stroke="#2563eb"
        strokeWidth="1.75"
        strokeDasharray="7 5"
        strokeLinejoin="round"
        {...draw(0.85, 1.1)}
      />

      {/* 3. Perimeter circulation ring */}
      <motion.path
        d={RING}
        fill="none"
        stroke="#94a3b8"
        strokeWidth="7"
        strokeOpacity="0.5"
        strokeLinejoin="round"
        {...draw(1.5, 1.0)}
      />

      {/* 4. Tower footprints rising in packing order */}
      {TOWERS.map((t, i) => (
        <motion.rect
          key={`${t.x}-${t.y}`}
          x={t.x}
          y={t.y}
          width={t.w}
          height={t.h}
          rx="1.5"
          fill="#2563eb"
          fillOpacity="0.16"
          stroke="#2563eb"
          strokeWidth="1.75"
          initial={reduced ? { opacity: 1, scale: 1 } : { opacity: 0, scale: 0.82 }}
          animate={{ opacity: 1, scale: 1 }}
          style={{ transformOrigin: `${t.x + t.w / 2}px ${t.y + t.h / 2}px` }}
          transition={{
            duration: reduced ? 0.2 : 0.4,
            delay: reduced ? 0 : 2.15 + i * 0.06,
            ease: [0.22, 0.61, 0.36, 1],
          }}
        />
      ))}

      {/* 5. Amenity block in residual open space */}
      <motion.rect
        x={AMENITY.x}
        y={AMENITY.y}
        width={AMENITY.w}
        height={AMENITY.h}
        rx="1.5"
        fill="#f59e0b"
        fillOpacity="0.18"
        stroke="#d97706"
        strokeWidth="1.5"
        initial={reduced ? { opacity: 1 } : { opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: reduced ? 0.2 : 0.45, delay: reduced ? 0 : 2.95 }}
      />

      {/* Annotation, lightest weight. Setback dimension on the north edge. */}
      <motion.g
        initial={reduced ? { opacity: 1 } : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5, delay: reduced ? 0 : 3.2 }}
      >
        <path d="M302,32 L296,61" stroke="#475569" strokeWidth="0.75" strokeDasharray="2 2" />
        <path d="M318,36 L312,64" stroke="#475569" strokeWidth="0.75" />
        <path d="M315,50 l3,-3 m-3,3 l3,3" stroke="#475569" strokeWidth="0.75" fill="none" />
        <text x="326" y="54" fill="#475569" fontSize="11" fontFamily="JetBrains Mono, monospace">
          6.0 m
        </text>

        {/* North arrow */}
        <g transform="translate(444,44)">
          <path d="M0,-16 L5,6 L0,1 L-5,6 Z" fill="#0f172a" />
          <text x="0" y="20" fill="#475569" fontSize="10" textAnchor="middle" fontFamily="JetBrains Mono, monospace">
            N
          </text>
        </g>

        {/* Scale bar */}
        <g transform="translate(40,344)">
          <rect x="0" y="0" width="30" height="4" fill="#0f172a" />
          <rect x="30" y="0" width="30" height="4" fill="#ffffff" stroke="#0f172a" strokeWidth="0.75" />
          <text x="0" y="16" fill="#475569" fontSize="10" fontFamily="JetBrains Mono, monospace">
            0
          </text>
          <text x="52" y="16" fill="#475569" fontSize="10" fontFamily="JetBrains Mono, monospace">
            20 m
          </text>
        </g>
      </motion.g>
    </svg>
  );
}
