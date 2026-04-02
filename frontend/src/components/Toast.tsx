import { createContext, useContext, useCallback, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

type ToastType = "info" | "success" | "warning" | "error";

interface Toast {
  id: number;
  type: ToastType;
  title: string;
  description?: string;
}

interface ToastCtx {
  toast: (t: Omit<Toast, "id">) => void;
}

const Ctx = createContext<ToastCtx>({ toast: () => {} });

export function useToast() {
  return useContext(Ctx);
}

let nextId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const toast = useCallback((t: Omit<Toast, "id">) => {
    const id = ++nextId;
    setToasts((prev) => [...prev, { ...t, id }]);
    setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== id)), 5000);
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((x) => x.id !== id));
  }, []);

  return (
    <Ctx value={{ toast }}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-80">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              "rounded-lg border p-3 shadow-lg animate-in slide-in-from-right",
              t.type === "success" && "border-green-500/30 bg-green-950/80",
              t.type === "error" && "border-red-500/30 bg-red-950/80",
              t.type === "warning" && "border-yellow-500/30 bg-yellow-950/80",
              t.type === "info" && "border-blue-500/30 bg-blue-950/80",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm font-medium text-text-primary">{t.title}</p>
                {t.description && (
                  <p className="mt-0.5 text-xs text-text-secondary">{t.description}</p>
                )}
              </div>
              <button onClick={() => dismiss(t.id)} className="text-text-muted hover:text-text-primary">
                <X size={14} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </Ctx>
  );
}
