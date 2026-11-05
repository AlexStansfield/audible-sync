/**
 * Toasts (Patterns.dc.html): bottom right, newest on top, full width on a phone. A
 * success closes itself after 4 s; an error stays until dismissed and carries a title
 * and the API's `detail`.
 */
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { errorDetail } from "./ErrorCard";
import { Icon } from "./Icon";
import styles from "./Toasts.module.css";

export const SUCCESS_LIFETIME = 4_000;

type Toast = { id: number; kind: "success" | "error"; title: string; detail?: string };

export type Toaster = {
  success: (message: string) => void;
  /** An error with a title; `detail` is a string or an error whose detail is shown. */
  error: (title: string, detail?: unknown) => void;
};

const ToastContext = createContext<Toaster | null>(null);

export function useToast(): Toaster {
  const toaster = useContext(ToastContext);
  if (!toaster) {
    throw new Error("useToast needs a <ToastProvider> above it");
  }
  return toaster;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const toaster = useMemo<Toaster>(
    () => ({
      success: (message) => {
        const id = next.current++;
        setToasts((current) => [{ id, kind: "success", title: message }, ...current]);
      },
      error: (title, detail) => {
        const id = next.current++;
        const text = detail === undefined ? undefined : typeof detail === "string" ? detail : errorDetail(detail);
        setToasts((current) => [{ id, kind: "error", title, detail: text }, ...current]);
      },
    }),
    [],
  );

  return (
    <ToastContext.Provider value={toaster}>
      {children}
      <section aria-label="Notifications" className={styles.region}>
        {toasts.map((toast) => (
          <ToastItem key={toast.id} toast={toast} dismiss={dismiss} />
        ))}
      </section>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast, dismiss }: { toast: Toast; dismiss: (id: number) => void }) {
  const success = toast.kind === "success";
  const { id } = toast;

  // `dismiss` is stable, so a toast arriving later does not restart this one's timer
  useEffect(() => {
    if (!success) return;
    const timer = setTimeout(() => dismiss(id), SUCCESS_LIFETIME);
    return () => clearTimeout(timer);
  }, [success, id, dismiss]);

  return (
    <div role={success ? "status" : "alert"} className={`${styles.toast} ${success ? "" : styles.error}`}>
      <span className={`${styles.mark} ${success ? styles.markOk : styles.markBad}`}>
        <Icon name={success ? "check" : "alert"} size={15} strokeWidth={2.7} />
      </span>
      <span className={styles.text}>
        <span className={success ? undefined : styles.title}>{toast.title}</span>
        {toast.detail && <span className={styles.detail}>{toast.detail}</span>}
      </span>
      <button type="button" className={styles.dismiss} aria-label="Dismiss" onClick={() => dismiss(id)}>
        <Icon name="close" size={14} strokeWidth={2.4} />
      </button>
    </div>
  );
}
