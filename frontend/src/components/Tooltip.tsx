import { useState, useRef, useCallback, useEffect } from "react";
import ReactDOM from "react-dom";

interface TooltipProps {
  content: string;
  children: React.ReactElement;
  delay?: number;
}

export function Tooltip({ content, children, delay = 400 }: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = useCallback(() => {
    timerRef.current = setTimeout(() => {
      if (!triggerRef.current) return;
      const rect = triggerRef.current.getBoundingClientRect();
      setCoords({ top: rect.top, left: rect.left + rect.width / 2 });
      setVisible(true);
    }, delay);
  }, [delay]);

  const hide = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
    setVisible(false);
  }, []);

  // Reposition after render so we can clamp to viewport
  useEffect(() => {
    if (!visible || !tipRef.current || !coords) return;
    const tip = tipRef.current;
    const tipRect = tip.getBoundingClientRect();
    const pad = 8;

    let left = coords.left - tipRect.width / 2;
    if (left < pad) left = pad;
    if (left + tipRect.width > window.innerWidth - pad) left = window.innerWidth - pad - tipRect.width;

    let top = coords.top - tipRect.height - 6;
    if (top < pad) top = coords.top + (triggerRef.current?.getBoundingClientRect().height ?? 0) + 6;

    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
    tip.style.opacity = "1";
  }, [visible, coords]);

  return (
    <>
      {/* Clone the child to attach ref + handlers */}
      <span
        ref={triggerRef as React.RefObject<HTMLSpanElement>}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        style={{ display: "inline-flex" }}
      >
        {children}
      </span>
      {visible &&
        coords &&
        ReactDOM.createPortal(
          <div
            ref={tipRef}
            role="tooltip"
            style={{ position: "fixed", top: 0, left: 0, opacity: 0, zIndex: 9999 }}
            className="pointer-events-none max-w-sm rounded-lg border border-surface-border bg-surface-light px-3 py-2 text-xs text-text-secondary shadow-lg whitespace-pre-line"
          >
            {content}
          </div>,
          document.body,
        )}
    </>
  );
}
