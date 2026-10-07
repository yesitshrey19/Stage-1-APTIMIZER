// Number that counts to its value the first time it scrolls into view.
//
// Values render in JetBrains Mono per the design guidelines, and the element
// carries the final value in aria-label so a screen reader announces the
// result rather than a stream of intermediate numbers.
import { useEffect, useRef, useState } from "react";
import { animate, useInView, useReducedMotion } from "framer-motion";

/**
 * @param {number} value    the number to land on
 * @param {string} suffix   text appended after the number, for example "+"
 * @param {number} duration seconds for the full count
 * @param {number} pad      minimum digits, zero-padded ("01"), held through the count
 */
export default function CountUp({ value, suffix = "", duration = 1.1, pad = 0, className, ...rest }) {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -15% 0px" });
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(0);

  useEffect(() => {
    if (!inView) return undefined;

    // Reduced motion still gets the number, just without the count.
    if (reduced) {
      setShown(value);
      return undefined;
    }

    const controls = animate(0, value, {
      duration,
      ease: [0.22, 0.61, 0.36, 1],
      onUpdate: (v) => setShown(Math.round(v)),
    });
    return () => controls.stop();
  }, [inView, value, duration, reduced]);

  return (
    <span
      ref={ref}
      className={className}
      aria-label={`${String(value).padStart(pad, "0")}${suffix}`}
      {...rest}
    >
      <span aria-hidden="true">
        {String(shown).padStart(pad, "0")}
        {suffix}
      </span>
    </span>
  );
}
