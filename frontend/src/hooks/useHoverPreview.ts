import { useRef, useState, useCallback, useEffect } from "react";

/**
 * Hover-preview controller: ensures only one preview plays across the entire page.
 * Returns { bind, isActive } — spread `bind` on the card container.
 *
 * Strategy:
 * - On mouseenter, start 500ms delay
 * - If still hovering after delay, set this card as the active preview
 * - On mouseleave, cancel and clear
 * - Only one preview may be active at a time (module-level singleton)
 */

let globalActiveId: number | null = null;
const listeners = new Set<() => void>();

function setGlobalActive(id: number | null) {
  globalActiveId = id;
  listeners.forEach((fn) => fn());
}

export function useHoverPreview(videoId: number, delay = 500) {
  const [isActive, setIsActive] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Subscribe to global changes
  useEffect(() => {
    const handler = () => setIsActive(globalActiveId === videoId);
    listeners.add(handler);
    return () => { listeners.delete(handler); };
  }, [videoId]);

  const onMouseEnter = useCallback(() => {
    timerRef.current = setTimeout(() => {
      setGlobalActive(videoId);
    }, delay);
  }, [videoId, delay]);

  const onMouseLeave = useCallback(() => {
    clearTimeout(timerRef.current);
    if (globalActiveId === videoId) {
      setGlobalActive(null);
    }
  }, [videoId]);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      clearTimeout(timerRef.current);
      if (globalActiveId === videoId) {
        setGlobalActive(null);
      }
    };
  }, [videoId]);

  return {
    isActive,
    bind: { onMouseEnter, onMouseLeave },
  };
}
