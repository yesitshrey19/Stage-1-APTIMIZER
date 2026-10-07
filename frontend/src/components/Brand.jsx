/**
 * One definition of the Aptimizer lockup.
 *
 * `light` swaps to a variant whose navy strokes are lifted to white, for dark grounds --
 * the standard mark is navy on transparent and disappears against them.
 */
export function BrandMark({ className = "h-8 w-auto", light = false }) {
  return (
    <img
      src={light ? "/logo-mark-light.png" : "/logo-mark.png"}
      alt=""
      aria-hidden="true"
      className={className}
      draggable="false"
    />
  );
}

export function Brand({
  className = "",
  markClass = "h-8 w-auto",
  wordClass = "text-[15px]",
  light = false,
  showWord = true,
  testid = "brand",
}) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`} data-testid={testid}>
      <BrandMark className={markClass} light={light} />
      {showWord && (
        <span className={`font-semibold tracking-tight ${light ? "text-white" : "text-[#102a4d]"} ${wordClass}`}>
          APTIMIZER
        </span>
      )}
    </span>
  );
}
