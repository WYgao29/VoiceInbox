import { createContext, useCallback, useContext, useRef, useState, type ReactNode, type RefObject } from "react";
import type { ConfirmRequest } from "./ui-state";
import { useDismissable } from "./dismissable";
import { useModalFocus } from "./focus-trap";

type Pending = ConfirmRequest & { resolve: (value: boolean) => void };

const ConfirmContext = createContext<((request: ConfirmRequest) => Promise<boolean>) | null>(null);
const DismissContext = createContext<(() => void) | null>(null);

export function ConfirmDialogView({
  request,
  onCancel,
  onConfirm,
  returnFocus,
}: {
  request: ConfirmRequest;
  onCancel: () => void;
  onConfirm: () => void;
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  const root = useRef<HTMLDivElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  useModalFocus(true, root, returnFocus, cancelRef);
  useDismissable(true, onCancel, root, "click");
  return (
    <div className="scrim" onClick={onCancel}>
      <div
        className="dialog"
        ref={root}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-description"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="confirm-title">{request.title}</h2>
        <p id="confirm-description" className="dialog-description">{request.message}</p>
        <div className="dialog-actions">
          <button ref={cancelRef} type="button" className="secondary" autoFocus onClick={onCancel}>
            {request.cancelLabel || "取消"}
          </button>
          <button type="button" className={request.danger ? "danger" : "primary"} onClick={onConfirm}>
            {request.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function activeTrigger(): HTMLElement | null {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement)) return null;
  if (active === document.body || active === document.documentElement) return null;
  // Action items disappear when they open a confirmation. Keep the stable
  // trigger so cancelling can restore focus after that action group is gone.
  return active.closest(".menu-pop")?.parentElement?.querySelector<HTMLElement>(":scope > button") || active;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const pendingRef = useRef<Pending | null>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const restoreOnClose = useRef<HTMLElement | null>(null);

  const settle = useCallback((value: boolean) => {
    const current = pendingRef.current;
    if (!current) return;
    // Destructive recording actions choose a surviving row after their request
    // completes. Other confirmations can safely return to their trigger.
    restoreOnClose.current = value && current.danger ? null : returnTo.current;
    pendingRef.current = null;
    returnTo.current = null;
    current.resolve(value);
    setPending(null);
  }, []);

  const ask = useCallback(
    (request: ConfirmRequest) => {
      const previous = pendingRef.current;
      if (!previous) returnTo.current = activeTrigger();
      if (previous) previous.resolve(false);
      return new Promise<boolean>((resolve) => {
        const next = { ...request, resolve };
        pendingRef.current = next;
        setPending(next);
      });
    },
    [],
  );

  return (
    <ConfirmContext.Provider value={ask}>
      <DismissContext.Provider value={() => settle(false)}>
        {children}
        {pending ? <ConfirmDialogView request={pending} returnFocus={restoreOnClose} onCancel={() => settle(false)} onConfirm={() => settle(true)} /> : null}
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
