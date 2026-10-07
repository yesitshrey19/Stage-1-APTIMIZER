
/**
 * AppBackground
 *
 * Static drafting grid. The previous hidden, continuously rendering canvas
 * competed with the hero and recalculations even behind opaque app surfaces.
 */
const AppBackground = () => {
  return (
    <div aria-hidden="true" className="fixed inset-0 -z-10 pointer-events-none"
      style={{ backgroundImage: "linear-gradient(#dbe5ed55 1px, transparent 1px), linear-gradient(90deg, #dbe5ed55 1px, transparent 1px)", backgroundSize: "40px 40px" }} />
  );
};

export default AppBackground;
