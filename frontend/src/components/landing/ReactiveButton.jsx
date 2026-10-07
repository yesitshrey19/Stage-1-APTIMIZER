// Landing page call to action.
//
// The app's shadcn Button is the right control inside the product, where the
// design guidelines ask for transition-colors and nothing more. A landing page
// CTA is a different surface, so this adds three responses on top:
//
//   1. a sheen that tracks the pointer across the face of the button
//   2. a press that scales the button down, so a click feels answered
//   3. a trailing icon that slides on hover
//
// All three are motion-safe only, so a visitor with prefers-reduced-motion set
// gets the colour change and the focus ring and none of the movement.
// Transitions name the properties they animate, never `all`.
import { forwardRef, useCallback, useRef, useState } from "react";
import { cn } from "../../lib/utils";

const VARIANTS = {
  primary: "bg-blue-600 text-white hover:bg-blue-700 border border-blue-600 hover:border-blue-700",
  outline: "bg-white text-slate-900 border border-slate-300 hover:bg-slate-50 hover:border-slate-400",
  ghost: "bg-transparent text-slate-700 border border-transparent hover:bg-slate-100 hover:text-slate-900",
};

const SIZES = {
  default: "h-11 px-6 text-sm",
  lg: "h-12 px-8 text-base",
  sm: "h-9 px-4 text-sm",
};

/**
 * @param {string} variant  primary | outline | ghost
 * @param {string} size     sm | default | lg
 * @param {any}    as       element or component to render, defaults to button
 * @param {node}   icon     optional trailing icon, slides right on hover
 */
const ReactiveButton = forwardRef(function ReactiveButton(
  { children, variant = "primary", size = "default", as: Comp = "button", icon, className, ...rest },
  ref,
) {
  const localRef = useRef(null);
  const [sheen, setSheen] = useState({ x: "50%", y: "50%", on: false });

  // Track the pointer in element space so the sheen sits under the cursor.
  const onPointerMove = useCallback((e) => {
    const node = localRef.current;
    if (!node) return;
    const box = node.getBoundingClientRect();
    setSheen({
      x: `${((e.clientX - box.left) / box.width) * 100}%`,
      y: `${((e.clientY - box.top) / box.height) * 100}%`,
      on: true,
    });
  }, []);

  const onPointerLeave = useCallback(() => {
    setSheen((s) => ({ ...s, on: false }));
  }, []);

  const setRefs = useCallback(
    (node) => {
      localRef.current = node;
      if (typeof ref === "function") ref(node);
      else if (ref) ref.current = node;
    },
    [ref],
  );

  // The sheen is white over the solid brand fill and blue over light fills,
  // so it stays visible without washing the label out.
  const sheenColor = variant === "primary" ? "rgba(255,255,255,0.28)" : "rgba(37,99,235,0.12)";

  return (
    <Comp
      ref={setRefs}
      onPointerMove={onPointerMove}
      onPointerLeave={onPointerLeave}
      className={cn(
        "group relative isolate inline-flex select-none items-center justify-center gap-2",
        "overflow-hidden rounded-md font-medium tracking-tight",
        "transition-[background-color,border-color,color,box-shadow,transform] duration-200 ease-out",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2",
        "disabled:pointer-events-none disabled:opacity-50",
        "motion-safe:active:scale-[0.975]",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {/* Pointer tracked sheen. Sits behind the label, never intercepts clicks. */}
      <span
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-0 -z-10",
          "transition-opacity duration-300 ease-out motion-reduce:hidden",
          sheen.on ? "opacity-100" : "opacity-0",
        )}
        style={{
          background: `radial-gradient(20rem circle at ${sheen.x} ${sheen.y}, ${sheenColor}, transparent 45%)`,
        }}
      />
      <span className="relative">{children}</span>
      {icon ? (
        <span
          aria-hidden="true"
          className="relative transition-transform duration-200 ease-out motion-safe:group-hover:translate-x-1"
        >
          {icon}
        </span>
      ) : null}
    </Comp>
  );
});

export default ReactiveButton;
