import {
  AnimatePresence,
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from "framer-motion";
import {
  Children,
  cloneElement,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { cn } from "@/lib/utils";

const DOCK_HEIGHT = 128;
const DEFAULT_MAGNIFICATION = 80;
const DEFAULT_DISTANCE = 150;
const DEFAULT_PANEL_HEIGHT = 64;
// Hoisted so the default spring config is one stable object across every
// render, not a fresh literal each time -- an unstable spring config was
// tearing down and rebuilding the mouseX subscription on every incidental
// re-render, which is why magnification silently never moved past its
// resting width no matter how the pointer was tracked.
const DEFAULT_SPRING = { mass: 0.1, stiffness: 150, damping: 12 };

const DockContext = createContext(undefined);

function DockProvider({ children, value }) {
  return <DockContext.Provider value={value}>{children}</DockContext.Provider>;
}

function useDock() {
  const context = useContext(DockContext);
  if (!context) {
    throw new Error("useDock must be used within a DockProvider");
  }
  return context;
}

/**
 * Apple-style magnifying dock. Items grow as the pointer nears them and
 * settle back as it moves away or leaves.
 *
 * @param {number} distance       px from the pointer at which magnification has fully fallen off
 * @param {number} panelHeight    resting height of the dock, before any item grows
 * @param {number} magnification  width (px) an item reaches directly under the pointer
 * @param {import("framer-motion").SpringOptions} spring
 */
function Dock({
  children,
  className,
  spring = DEFAULT_SPRING,
  magnification = DEFAULT_MAGNIFICATION,
  distance = DEFAULT_DISTANCE,
  panelHeight = DEFAULT_PANEL_HEIGHT,
}) {
  const mouseX = useMotionValue(Infinity);
  const isHovered = useMotionValue(0);
  // A visitor who has asked for reduced motion gets a flat, non-magnifying
  // dock -- still fully usable, just without the pointer-chasing growth.
  const reduced = useReducedMotion();

  // Stable across renders as long as the props feeding it are -- an object
  // literal recreated every render here would tear down and rebuild every
  // DockItem's mouseX subscription on any incidental re-render higher up
  // the tree, not just when these values actually change.
  const contextValue = useMemo(
    () => ({ mouseX, spring, distance, magnification, reduced }),
    [mouseX, spring, distance, magnification, reduced],
  );

  const maxHeight = useMemo(
    () => Math.max(DOCK_HEIGHT, magnification + magnification / 2 + 4),
    [magnification],
  );

  const heightRow = useTransform(isHovered, [0, 1], [panelHeight, maxHeight]);
  const height = useSpring(heightRow, spring);

  return (
    <motion.div
      style={{ height, scrollbarWidth: "none" }}
      className="mx-2 flex max-w-full items-end overflow-x-auto"
    >
      <motion.div
        onMouseMove={({ clientX }) => {
          // clientX (viewport space) matches getBoundingClientRect() below --
          // pageX would drift out of step on any page with horizontal scroll.
          if (reduced) return;
          isHovered.set(1);
          mouseX.set(clientX);
        }}
        onMouseLeave={() => {
          isHovered.set(0);
          mouseX.set(Infinity);
        }}
        className={cn(
          "mx-auto flex w-fit gap-4 rounded-2xl bg-gray-50 px-4 dark:bg-neutral-900",
          className,
        )}
        style={{ height: panelHeight }}
        role="toolbar"
        aria-label="Application dock"
      >
        <DockProvider value={contextValue}>{children}</DockProvider>
      </motion.div>
    </motion.div>
  );
}

/**
 * One dock entry. Renders as a plain div by default; pass `as` (a component
 * or tag, e.g. `Link` or `"a"`) plus its own props (`to`, `href`, ...) to make
 * the entry an actual link rather than an inert button-shaped div -- a dock
 * used as a menu needs somewhere for each item to actually go.
 */
function DockItem({ children, className, as = "div", ...rest }) {
  const ref = useRef(null);
  const { distance, magnification, mouseX, spring, reduced } = useDock();
  const isHovered = useMotionValue(0);

  // Wrapping `as` in motion() fresh on every render would give React a new
  // component identity each time and remount the node -- memoise on `as`.
  const MotionComp = useMemo(() => motion(as), [as]);

  // One hop straight from mouseX to width (the original shape of this component
  // chained mouseX -> mouseDistance -> width through two useTransform calls;
  // this does the same 3-point piecewise-linear mapping in one, by hand:
  // -distance -> 40, 0 -> magnification, +distance -> 40).
  const widthTransform = useTransform(mouseX, (val) => {
    if (reduced) return 40;
    const domRect = ref.current?.getBoundingClientRect() ?? { x: 0, width: 0 };
    const dist = val - domRect.x - domRect.width / 2;
    const t = Math.min(Math.abs(dist), distance) / distance; // 0 at centre, 1 at/past the edge
    return magnification + (40 - magnification) * t;
  });

  const width = useSpring(widthTransform, spring);
  const isPlainDiv = as === "div";

  return (
    <MotionComp
      ref={ref}
      style={{ width }}
      onHoverStart={() => isHovered.set(1)}
      onHoverEnd={() => isHovered.set(0)}
      onFocus={() => isHovered.set(1)}
      onBlur={() => isHovered.set(0)}
      className={cn("relative inline-flex items-center justify-center", className)}
      tabIndex={isPlainDiv ? 0 : undefined}
      role={isPlainDiv ? "button" : undefined}
      aria-haspopup={isPlainDiv ? "true" : undefined}
      {...rest}
    >
      {Children.map(children, (child) => cloneElement(child, { width, isHovered }))}
    </MotionComp>
  );
}

// width and isHovered below arrive via DockItem's cloneElement, not as
// props a caller passes directly.
function DockLabel({ children, className, isHovered }) {
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const unsubscribe = isHovered.on("change", (latest) => {
      setIsVisible(latest === 1);
    });
    return () => unsubscribe();
  }, [isHovered]);

  return (
    <AnimatePresence>
      {isVisible && (
        <motion.div
          initial={{ opacity: 0, y: 0 }}
          animate={{ opacity: 1, y: -10 }}
          exit={{ opacity: 0, y: 0 }}
          transition={{ duration: 0.2 }}
          className={cn(
            "absolute -top-6 left-1/2 w-fit whitespace-pre rounded-md border border-gray-200 bg-gray-100 px-2 py-0.5 text-xs text-neutral-700 dark:border-neutral-900 dark:bg-neutral-800 dark:text-white",
            className,
          )}
          role="tooltip"
          style={{ x: "-50%" }}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function DockIcon({ children, className, width }) {
  const widthTransform = useTransform(width, (val) => val / 2);

  return (
    <motion.div
      style={{ width: widthTransform }}
      className={cn("flex items-center justify-center", className)}
    >
      {children}
    </motion.div>
  );
}

export { Dock, DockIcon, DockItem, DockLabel };
