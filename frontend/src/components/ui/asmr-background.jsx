import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

/**
 * ASMRBackground
 *
 * A high-density canvas particle field. Charcoal dust and glass shards drift
 * with a frozen-static jitter; within `magneticRadius` of the pointer they are
 * pulled inward and swirled around it, picking up a "friction glow" as they
 * accelerate.
 *
 * The canvas sizes itself to this component's own box (not the window), so it
 * works full-bleed or embedded. `children` render above it.
 */
export const ASMRBackground = ({
  className,
  children,
  particleCount = 1000,
  magneticRadius = 280,
  vortexStrength = 0.07,
  pullStrength = 0.12,
  backgroundColor = "#0a0a0c",
  trailColor = "rgba(10, 10, 12, 0.18)",
  // "r, g, b" triples - the alpha is applied per particle at draw time.
  glassColor = "240, 245, 255",
  dustColor = "80, 80, 85",
  glowColor = "180, 220, 255",
  showCursor = true,
  ...props
}) => {
  const containerRef = useRef(null);
  const canvasRef = useRef(null);
  const cursorRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const prefersReducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;

    let width = 0;
    let height = 0;
    let frameId = 0;
    let particles = [];

    // Pointer in canvas space, parked far offscreen until it actually moves.
    const mouse = { x: -1000, y: -1000, clientX: -100, clientY: -100 };

    class Particle {
      constructor() {
        this.frictionGlow = 0;
        this.reset();
      }

      reset() {
        this.x = Math.random() * width;
        this.y = Math.random() * height;
        this.size = Math.random() * 1.5 + 0.5;
        this.vx = (Math.random() - 0.5) * 0.2;
        this.vy = (Math.random() - 0.5) * 0.2;
        // 70% charcoal dust, 30% glass
        const isGlass = Math.random() > 0.7;
        this.color = isGlass ? glassColor : dustColor;
        this.alpha = Math.random() * 0.4 + 0.1;
        this.rotation = Math.random() * Math.PI * 2;
        this.rotationSpeed = (Math.random() - 0.5) * 0.05;
      }

      update() {
        const dx = mouse.x - this.x;
        const dy = mouse.y - this.y;
        const dist = Math.hypot(dx, dy);

        // A dist of exactly 0 would divide by zero and poison x/y with NaN,
        // dropping the particle out of the field permanently.
        if (dist > 0 && dist < magneticRadius) {
          const force = (magneticRadius - dist) / magneticRadius;

          // Pull toward the pointer...
          this.vx += (dx / dist) * force * pullStrength;
          this.vy += (dy / dist) * force * pullStrength;

          // ...plus a perpendicular component, which is what makes it swirl.
          this.vx += (dy / dist) * force * vortexStrength * 10;
          this.vy -= (dx / dist) * force * vortexStrength * 10;

          this.frictionGlow = force * 0.7;
        } else {
          this.frictionGlow *= 0.92;
        }

        this.x += this.vx;
        this.y += this.vy;

        // Damping, then jitter so the field still breathes when untouched.
        this.vx *= 0.95;
        this.vy *= 0.95;
        this.vx += (Math.random() - 0.5) * 0.04;
        this.vy += (Math.random() - 0.5) * 0.04;

        this.rotation +=
          this.rotationSpeed + (Math.abs(this.vx) + Math.abs(this.vy)) * 0.05;

        if (this.x < -20) this.x = width + 20;
        if (this.x > width + 20) this.x = -20;
        if (this.y < -20) this.y = height + 20;
        if (this.y > height + 20) this.y = -20;
      }

      draw() {
        ctx.save();
        ctx.translate(this.x, this.y);
        ctx.rotate(this.rotation);

        const finalAlpha = Math.min(this.alpha + this.frictionGlow, 0.9);
        ctx.fillStyle = `rgba(${this.color}, ${finalAlpha})`;

        if (this.frictionGlow > 0.3) {
          ctx.shadowBlur = 8 * this.frictionGlow;
          ctx.shadowColor = `rgba(${glowColor}, ${this.frictionGlow})`;
        }

        // Sharp shard geometry
        ctx.beginPath();
        ctx.moveTo(0, -this.size * 2.5);
        ctx.lineTo(this.size, 0);
        ctx.lineTo(0, this.size * 2.5);
        ctx.lineTo(-this.size, 0);
        ctx.closePath();
        ctx.fill();

        ctx.restore();
      }
    }

    const resize = () => {
      const rect = container.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      // Everything below draws in CSS pixels; the transform handles density.
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const populate = () => {
      particles = Array.from({ length: particleCount }, () => new Particle());
    };

    const drawStaticFrame = () => {
      ctx.fillStyle = backgroundColor;
      ctx.fillRect(0, 0, width, height);
      for (const p of particles) p.draw();
    };

    const render = () => {
      // Washing over the previous frame instead of clearing leaves the trails.
      ctx.fillStyle = trailColor;
      ctx.fillRect(0, 0, width, height);

      for (const p of particles) {
        p.update();
        p.draw();
      }

      if (cursorRef.current) {
        cursorRef.current.style.transform = `translate(${mouse.clientX}px, ${mouse.clientY}px) translate(-50%, -50%)`;
      }

      frameId = requestAnimationFrame(render);
    };

    const track = (clientX, clientY) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = clientX - rect.left;
      mouse.y = clientY - rect.top;
      mouse.clientX = clientX;
      mouse.clientY = clientY;
    };

    const handleMouseMove = (e) => track(e.clientX, e.clientY);
    const handleTouchMove = (e) => {
      if (e.touches[0]) track(e.touches[0].clientX, e.touches[0].clientY);
    };
    const handleLeave = () => {
      mouse.x = -1000;
      mouse.y = -1000;
    };

    resize();
    populate();

    const observer = new ResizeObserver(() => {
      const rect = container.getBoundingClientRect();
      if (
        Math.round(rect.width) === Math.round(width) &&
        Math.round(rect.height) === Math.round(height)
      ) {
        return;
      }
      resize();
      populate();
      if (prefersReducedMotion) drawStaticFrame();
    });
    observer.observe(container);

    if (prefersReducedMotion) {
      // One still frame, no loop and no pointer reactivity.
      drawStaticFrame();
      return () => observer.disconnect();
    }

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("touchmove", handleTouchMove, { passive: true });
    document.addEventListener("mouseleave", handleLeave);

    render();

    return () => {
      observer.disconnect();
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("touchmove", handleTouchMove);
      document.removeEventListener("mouseleave", handleLeave);
      cancelAnimationFrame(frameId);
    };
  }, [
    particleCount,
    magneticRadius,
    vortexStrength,
    pullStrength,
    backgroundColor,
    trailColor,
    glassColor,
    dustColor,
    glowColor,
  ]);

  return (
    <div
      ref={containerRef}
      className={cn(
        "relative w-full h-screen overflow-hidden",
        showCursor && "cursor-none",
        className
      )}
      style={{ backgroundColor }}
      {...props}
    >
      <canvas ref={canvasRef} className="absolute inset-0 block h-full w-full" />

      {children ? <div className="relative z-10 h-full">{children}</div> : null}

      {showCursor ? (
        <div
          ref={cursorRef}
          aria-hidden="true"
          className="pointer-events-none fixed left-0 top-0 z-50 h-4 w-4 rounded-full border border-white/20"
          style={{ transform: "translate(-100px, -100px)" }}
        />
      ) : null}
    </div>
  );
};

export default ASMRBackground;
