/**
 * Pre-renders the fireworks animation to a WebM blob via captureStream + MediaRecorder.
 * The recording happens once in the background (real-time on a hidden canvas), then
 * party mode just plays the resulting <video> — zero CPU during playback.
 */

const COLORS = [
  "#ff4444", "#ff8800", "#ffcc00", "#44ff44",
  "#44ccff", "#8844ff", "#ff44cc", "#ffffff",
  "#ff6644", "#44ffcc", "#ffaa44", "#cc44ff",
];

const FADE_MS = 3000; // fade-out tail appended to the recording

interface Particle {
  x: number; y: number; vx: number; vy: number;
  alpha: number; color: string; size: number; decay: number;
}

interface Shell {
  x: number; y: number; vy: number;
  targetY: number; color: string; exploded: boolean;
}

function randomColor() {
  return COLORS[Math.floor(Math.random() * COLORS.length)];
}

/** Render & capture a fireworks animation. Returns a blob URL for a WebM video. */
export function prerenderFireworks(durationMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const canvas = document.createElement("canvas");
    // Use the current viewport size so it looks crisp at fullscreen
    const W = window.innerWidth;
    const H = window.innerHeight;
    canvas.width = W;
    canvas.height = H;

    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) { reject(new Error("no 2d context")); return; }

    // Pick a supported mimeType
    const mimeType = MediaRecorder.isTypeSupported("video/webm; codecs=vp9")
      ? "video/webm; codecs=vp9"
      : "video/webm";

    const stream = canvas.captureStream(60);
    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 4_000_000 });
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: "video/webm" });
      resolve(URL.createObjectURL(blob));
    };
    recorder.onerror = () => reject(new Error("MediaRecorder error"));
    recorder.start();

    // ── Run the fireworks animation ──────────────────────────
    const particles: Particle[] = [];
    const shells: Shell[] = [];
    const startTime = performance.now();
    let lastShellTime = 0;

    function spawnShell() {
      shells.push({
        x: Math.random() * W * 0.8 + W * 0.1,
        y: H,
        vy: -(8 + Math.random() * 6),
        targetY: H * (0.15 + Math.random() * 0.35),
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
          x: shell.x, y: shell.y,
          vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
          alpha: 1,
          color: Math.random() > 0.3 ? shell.color : randomColor(),
          size: 1.5 + Math.random() * 2,
          decay: 0.012 + Math.random() * 0.015,
        });
      }
    }

    const totalMs = durationMs + FADE_MS;
    let stopped = false;

    function frame(now: number) {
      if (stopped) return;
      const elapsed = now - startTime;

      // Trail fade
      ctx!.fillStyle = "rgba(0,0,0,0.15)";
      ctx!.fillRect(0, 0, W, H);

      // Spawn shells during the active window
      const spawnWindow = durationMs * 0.7;
      if (elapsed < spawnWindow && now - lastShellTime > 120 + Math.random() * 200) {
        spawnShell();
        if (Math.random() > 0.5) spawnShell();
        lastShellTime = now;
      }

      // Update + draw shells
      let sw = 0;
      for (let i = 0; i < shells.length; i++) {
        const s = shells[i];
        if (s.exploded) continue;
        s.y += s.vy;
        s.vy += 0.05;
        ctx!.beginPath();
        ctx!.arc(s.x, s.y, 2, 0, Math.PI * 2);
        ctx!.fillStyle = s.color;
        ctx!.fill();
        if (s.y <= s.targetY || s.vy >= 0) {
          s.exploded = true;
          explode(s);
          continue;
        }
        shells[sw++] = s;
      }
      shells.length = sw;

      // Update + draw particles
      let pw = 0;
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        p.x += p.vx; p.y += p.vy;
        p.vy += 0.06; p.vx *= 0.99;
        p.alpha -= p.decay;
        if (p.alpha <= 0) continue;
        ctx!.globalAlpha = p.alpha;
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx!.fillStyle = p.color;
        ctx!.fill();
        particles[pw++] = p;
      }
      particles.length = pw;
      ctx!.globalAlpha = 1;

      // Fade-to-black during the tail
      if (elapsed > durationMs) {
        const fadeProgress = Math.min(1, (elapsed - durationMs) / FADE_MS);
        ctx!.fillStyle = `rgba(0,0,0,${(fadeProgress * 0.12).toFixed(3)})`;
        ctx!.fillRect(0, 0, W, H);
      }

      // Stop when the full duration (including fade tail) is reached and particles are gone
      if (elapsed >= totalMs && particles.length === 0) {
        // Final black frame
        ctx!.fillStyle = "#000";
        ctx!.fillRect(0, 0, W, H);
        stopped = true;
        recorder.stop();
        return;
      }

      requestAnimationFrame(frame);
    }

    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);
    requestAnimationFrame(frame);
  });
}

// ── Singleton cache ──────────────────────────────────────────
let cachedUrl: string | null = null;
let cachedDuration: number | null = null;
let renderPromise: Promise<string> | null = null;

/** Get or create the pre-rendered fireworks video blob URL. */
export function getOrPrerender(durationMs: number): Promise<string> {
  // If we already have a matching render, return it
  if (cachedUrl && cachedDuration === durationMs) return Promise.resolve(cachedUrl);

  // If a render is in progress for the same duration, await it
  if (renderPromise && cachedDuration === durationMs) return renderPromise;

  // Invalidate old cache
  if (cachedUrl) { URL.revokeObjectURL(cachedUrl); cachedUrl = null; }
  cachedDuration = durationMs;

  renderPromise = prerenderFireworks(durationMs).then((url) => {
    cachedUrl = url;
    renderPromise = null;
    return url;
  });

  return renderPromise;
}

/** Invalidate the cache (e.g. when settings change). */
export function invalidateFireworksCache() {
  if (cachedUrl) { URL.revokeObjectURL(cachedUrl); cachedUrl = null; }
  cachedDuration = null;
  renderPromise = null;
}

/** Check if a pre-rendered blob is already available synchronously. */
export function getCachedFireworksUrl(): string | null {
  return cachedUrl;
}
