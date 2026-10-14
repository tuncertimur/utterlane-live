import { useEffect } from "react";
import { useI18n } from "../i18n.ts";
import { noticeTitle } from "../labels.ts";
import type { Toast } from "../store.ts";
import { AlertIcon, CheckIcon, CloseIcon, InfoIcon } from "./Icons.tsx";

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  const i18n = useI18n();
  useEffect(() => {
    const ms = toast.level === "error" ? 12_000 : toast.level === "warn" ? 8_000 : 5_000;
    const handle = window.setTimeout(() => onDismiss(toast.id), ms);
    return () => window.clearTimeout(handle);
  }, [toast.id, toast.level, onDismiss]);
  const Icon = toast.level === "info" ? (toast.code === "local_ok" ? CheckIcon : InfoIcon) : AlertIcon;
  return (
    <div className="toast" data-level={toast.level} role={toast.level === "error" ? "alert" : "status"}>
      <Icon size={17} className="toast__icon" />
      <div className="toast__body">
        <span className="toast__title">{noticeTitle(i18n, toast.level)}</span>
        <p>{toast.key ? i18n.t(toast.key) : toast.message}</p>
      </div>
      <button type="button" className="icon-btn icon-btn--small" onClick={() => onDismiss(toast.id)} aria-label={i18n.t("toast.dismiss")}>
        <CloseIcon size={14} />
      </button>
    </div>
  );
}

export function Toasts({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}
