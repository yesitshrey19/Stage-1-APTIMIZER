// Scroll reveal primitives for the landing page.
//
// Motion is opt-in per element rather than a global page effect, so a section
// that should not move (dense data, tables) simply does not get wrapped.
// Every animation here collapses to a plain fade when the visitor has asked
// for reduced motion, and to nothing at all when the element is already in
// view on first paint.
import { motion, useReducedMotion } from "framer-motion";

// Shared easing. Slight overshoot free, so structural elements settle rather
// than bounce. The design guidelines call for restraint, not personality.
const EASE = [0.22, 0.61, 0.36, 1];

/**
 * Fade and lift a block into view the first time it crosses the viewport.
 *
 * Above the fold content should pass `immediate`, which animates on mount
 * instead of waiting for a scroll observer. Content that is already on screen
 * at first paint has nothing to wait for, and tying it to an observer means a
 * blank hero for as long as that observer has not reported.
 *
 * @param {number}  delay     seconds to wait before starting
 * @param {number}  y         pixels to travel upward, 0 for a pure fade
 * @param {string}  as        element tag to render, defaults to div
 * @param {boolean} immediate animate on mount rather than on scroll
 */
export function Reveal({ children, delay = 0, y = 24, as = "div", immediate = false, className, ...rest }) {
  const reduced = useReducedMotion();
  const Tag = motion[as] || motion.div;

  const from = reduced ? { opacity: 0 } : { opacity: 0, y };
  const to = reduced ? { opacity: 1 } : { opacity: 1, y: 0 };
  const transition = { duration: reduced ? 0.2 : 0.55, delay, ease: EASE };

  if (immediate) {
    return (
      <Tag className={className} initial={from} animate={to} transition={transition} {...rest}>
        {children}
      </Tag>
    );
  }

  return (
    <Tag
      className={className}
      initial={from}
      whileInView={to}
      viewport={{ once: true, margin: "0px 0px -12% 0px" }}
      transition={transition}
      {...rest}
    >
      {children}
    </Tag>
  );
}

/**
 * Parent that releases its children one after another. Pair with RevealItem.
 * Stagger reads as a list being drawn in order, which suits a pipeline or a
 * module grid better than every card arriving at once.
 */
export function Stagger({ children, step = 0.07, delay = 0, className, ...rest }) {
  const reduced = useReducedMotion();

  return (
    <motion.div
      className={className}
      initial="hidden"
      whileInView="shown"
      viewport={{ once: true, margin: "0px 0px -10% 0px" }}
      variants={{
        hidden: {},
        shown: { transition: { staggerChildren: reduced ? 0 : step, delayChildren: delay } },
      }}
      {...rest}
    >
      {children}
    </motion.div>
  );
}

/** A single child of Stagger. */
export function RevealItem({ children, y = 18, className, ...rest }) {
  const reduced = useReducedMotion();

  return (
    <motion.div
      className={className}
      variants={{
        hidden: reduced ? { opacity: 0 } : { opacity: 0, y },
        shown: {
          opacity: 1,
          y: 0,
          transition: { duration: reduced ? 0.2 : 0.5, ease: EASE },
        },
      }}
      {...rest}
    >
      {children}
    </motion.div>
  );
}
