import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { ConfirmRequest } from "./ui-state";

type Pending = ConfirmRequest & { resolve: (value: boolean) => void };

const ConfirmContext = createContext<((request: ConfirmRequest) => Promise<boolean>) | null>(null);
const DismissContext = createContext<(() => void) | null>(null);

export function ConfirmDialogView({
  request,
  onCancel,
  onConfirm,
}: {
  request: ConfirmRequest;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="scrim" onClick={onCancel}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="confirm-title">{request.title}</h2>
        <p className="dialog-description">{request.message}</p>
        <div className="dialog-actions">
          <button type="button" className="secondary" autoFocus={Boolean(request.danger)} onClick={onCancel}>
            {request.cancelLabel || "取消"}
          </button>
          <button type="button" className={request.danger ? "danger" : "primary"} autoFocus={!request.danger} onClick={onConfirm}>
            {request.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const pendingRef = useRef<Pending | null>(null);

  const settle = useCallback((value: boolean) => {
    const current = pendingRef.current;
    pendingRef.current = null;
    current?.resolve(value);
    setPending(null);
  }, []);

  const ask = useCallback(
    (request: ConfirmRequest) => {
      const previous = pendingRef.current;
      if (previous) previous.resolve(false);
      return new Promise<boolean>((resolve) => {
        const next = { ...request, resolve };
        pendingRef.current = next;
        setPending(next);
      });
    },
    [],
  );

  useEffect(() => {
    if (!pending) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      settle(false);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [pending, settle]);

  return (
    <ConfirmContext.Provider value={ask}>
      <DismissContext.Provider value={() => settle(false)}>
        {children}
        {pending ? <ConfirmDialogView request={pending} onCancel={() => settle(false)} onConfirm={() => settle(true)} /> : null}
      </DismissContext.Provider>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): (request: ConfirmRequest) => Promise<boolean> {
  const ask = useContext(ConfirmContext);
  if (!ask) throw new Error("确认对话框尚未就绪");
  return ask;
}

export function useDismissConfirm(): () => void {
  const dismiss = useContext(DismissContext);
  if (!dismiss) throw new Error("确认对话框尚未就绪");
  return dismiss;
}
