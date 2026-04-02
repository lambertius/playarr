import { useEffect, useRef, useCallback, useState } from "react";

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  alpha: number;
  color: string;
  size: number;
  decay: number;
}

interface Shell {
  x: number;
  y: number;
  vy: number;
  targetY: number;
  color: string;
  exploded: boolean;
}

const COLORS = [
  "#ff4444", "#ff8800", "#ffcc00", "#44ff44",
  "#44ccff", "#8844ff", "#ff44cc", "#ffffff",
  "#ff6644", "#44ffcc", "#ffaa44", "#cc44ff",
];

const FADE_DURATION = 3000; // 3 second fade-out

function randomColor() {
  return COLORS[Math.floor(Math.random() * COLORS.length)];
}

interface FireworksProps {
  duration?: number; // ms, default 8000
  onComplete: () => void;
}

export function Fireworks({ duration = 8000, onComplete }: FireworksProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const [fading, setFading] = useState(false);

  const animate = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;

    const particles: Particle[] = [];
    const shells: Shell[] = [];
    const startTime = performance.now();
    let lastShellTime = 0;

    function spawnShell() {
      const x = Math.random() * canvas!.width * 0.8 + canvas!.width * 0.1;
      shells.push({
        x,
        y: canvas!.height,
        vy: -(8 + Math.random() * 6),
        targetY: canvas!.height * (0.15 + Math.random() * 0.35),
        color: randomColor(),
        exploded: false,
      });
    }

    function explode(shell: Shell) {
      const count = 60 + Math.floor(Math.random() * 40);
      for (let i = 0; i < count; i++) {
        const angle = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.3;
        const speed = 2 + Math.random() * 4;
        particles.push({
          x: shell.x,
          y: shell.y,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed,
          alpha: 1,
          color: Math.random() > 0.3 ? shell.color : randomColor(),
          size: 1.5 + Math.random() * 2,
          decay: 0.012 + Math.random() * 0.015,
        });
      }
    }

    let fadeStarted = false;

    function frame(now: number) {
      const elapsed = now - startTime;

      ctx!.fillStyle = "rgba(0, 0, 0, 0.15)";
      ctx!.fillRect(0, 0, canvas!.width, canvas!.height);

      // Spawn shells throughout the duration (taper during the last 40%)
      const spawnWindow = duration * 0.7;
      if (elapsed < spawnWindow && now - lastShellTime > 120 + Math.random() * 200) {
        spawnShell();
        if (Math.random() > 0.5) spawnShell(); // double burst
        lastShellTime = now;
      }

      // Update shells
      for (const shell of shells) {
        if (!shell.exploded) {
          shell.y += shell.vy;
          shell.vy += 0.05; // gravity (light for ascent)

          // Draw trail
          ctx!.beginPath();
          ctx!.arc(shell.x, shell.y, 2, 0, Math.PI * 2);
          ctx!.fillStyle = shell.color;
          ctx!.fill();

          if (shell.y <= shell.targetY || shell.vy >= 0) {
            shell.exploded = true;
            explode(shell);
          }
        }
      }

      // Update particles
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        p.x += p.vx;
        p.y += p.vy;
        p.vy += 0.06; // gravity
        p.vx *= 0.99; // drag
        p.alpha -= p.decay;

        if (p.alpha <= 0) {
          particles.splice(i, 1);
          continue;
        }

        ctx!.beginPath();
        ctx!.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx!.fillStyle = p.color;
        ctx!.globalAlpha = p.alpha;
        ctx!.fill();
        ctx!.globalAlpha = 1;
      }

      // When duration is reached, start the CSS fade-out
      if (elapsed >= duration && !fadeStarted) {
        fadeStarted = true;
        setFading(true);
        // Fire onComplete after the fade finishes
        setTimeout(onComplete, FADE_DURATION);
      }

      // Keep animating the canvas during the fade-out so particles settle naturally
      if (!fadeStarted || particles.length > 0) {
        animRef.current = requestAnimationFrame(frame);
      }
    }

    // Clear to black initially
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    animRef.current = requestAnimationFrame(frame);
  }, [duration, onComplete]);

  useEffect(() => {
    animate();
    return () => cancelAnimationFrame(animRef.current);
  }, [animate]);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 z-[9999] pointer-events-none"
      style={{
        width: "100vw",
        height: "100vh",
        opacity: fading ? 0 : 1,
        transition: `opacity ${FADE_DURATION}ms ease-out`,
      }}
    />
  );
}
