import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";

type Tone = "success" | "error" | "info";

interface ToastItem {
  id: number;
  message: string;
  tone: Tone;
}

interface ToastContextValue {
  push: (message: string, tone?: Tone) => void;
}

const ToastContext = createContext<ToastContextValue>({ push: () => undefined });

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const counter = useRef(0);

  const push = useCallback((message: string, tone: Tone = "info") => {
    const id = ++counter.current;
    setItems((prev) => [...prev.slice(-3), { id, message, tone }]);
    window.setTimeout(() => {
      setItems((prev) => prev.filter((item) => item.id !== id));
    }, 5200);
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-5 z-[80] flex flex-col items-center gap-2 px-4">
        {items.map((item) => (
          <div
            key={item.id}
            className="pointer-events-auto flex w-full max-w-md items-start gap-2.5 rounded-xl border bg-surface px-3.5 py-2.5 text-sm shadow-card animate-slide-up"
            role="status"
          >
            {item.tone === "success" ? (
              <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-positive" />
            ) : item.tone === "error" ? (
              <AlertTriangle size={16} className="mt-0.5 shrink-0 text-danger" />
            ) : (
              <Info size={16} className="mt-0.5 shrink-0 text-blue" />
            )}
            <p className="flex-1 leading-snug text-ink">{item.message}</p>
            <button
              type="button"
              aria-label="Dismiss notification"
              onClick={() => setItems((prev) => prev.filter((entry) => entry.id !== item.id))}
              className="rounded p-0.5 text-muted hover:bg-surface-muted hover:text-ink"
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
