import { useState, useCallback, type ReactNode } from "react";
import ReactDOM from "react-dom";
import { AlertTriangle, X } from "lucide-react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  variant?: "danger" | "default";
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  variant = "default",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) return null;

  return ReactDOM.createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* backdrop */}
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onCancel} />
      {/* dialog */}
      <div
        className="relative z-10 w-full max-w-md rounded-xl border border-surface-border bg-surface-light p-6 shadow-[0_0_40px_rgba(225,29,46,0.08)]"
        style={{ background: "linear-gradient(135deg, rgba(21,25,35,0.97) 0%, rgba(28,34,48,0.92) 100%)" }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
      >
        <button
          onClick={onCancel}
          className="absolute top-4 right-4 text-text-muted hover:text-text-primary"
          aria-label="Close"
        >
          <X size={18} />
        </button>

        <div className="flex items-start gap-3">
          {variant === "danger" && (
            <div className="mt-0.5 flex-shrink-0 text-danger">
              <AlertTriangle size={22} />
            </div>
          )}
          <div>
            <h2 id="confirm-title" className="text-lg font-semibold text-text-primary">
              {title}
            </h2>
            {description && (
              <p className="mt-1 text-sm text-text-secondary">{description}</p>
            )}
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button onClick={onCancel} className="btn-secondary btn-sm">
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={variant === "danger" ? "btn-danger btn-sm" : "btn-primary btn-sm"}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Hook for easy confirm-dialog usage. */
export function useConfirm() {
  const [state, setState] = useState<{
    open: boolean;
    props: Omit<ConfirmDialogProps, "open" | "onConfirm" | "onCancel">;
    resolve: ((ok: boolean) => void) | null;
  }>({ open: false, props: { title: "" }, resolve: null });

  const confirm = useCallback(
    (props: Omit<ConfirmDialogProps, "open" | "onConfirm" | "onCancel">): Promise<boolean> => {
      return new Promise((resolve) => {
        setState({ open: true, props, resolve });
      });
    },
    []
  );

  const dialog: ReactNode = (
    <ConfirmDialog
      open={state.open}
      {...state.props}
      onConfirm={() => {
        state.resolve?.(true);
        setState((s) => ({ ...s, open: false }));
      }}
      onCancel={() => {
        state.resolve?.(false);
        setState((s) => ({ ...s, open: false }));
      }}
    />
  );

  return { confirm, dialog };
}
