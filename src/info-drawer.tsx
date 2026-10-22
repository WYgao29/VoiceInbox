import { useRef, type ReactNode, type RefObject } from "react";
import { useDismissable } from "./dismissable";
import { useDrawerFocus } from "./focus-trap";

export function InfoDrawerShell({
  layout,
  open,
  onClose,
  returnFocus,
  title,
  children,
}: {
  layout: "dock" | "drawer";
  open: boolean;
  onClose: () => void;
  returnFocus?: RefObject<HTMLElement | null>;
  title: string;
  children: ReactNode;
}) {
  const root = useRef<HTMLElement | null>(null);
  const dialog = layout === "drawer" && open;
  useDismissable(dialog, onClose, root);
  useDrawerFocus(dialog, root, returnFocus);
  return (
    <aside
      className="info-pane"
      aria-label="信息"
      ref={root}
      role={dialog ? "dialog" : undefined}
      aria-modal={dialog ? true : undefined}
      inert={layout === "drawer" && !open ? true : undefined}
      tabIndex={dialog ? -1 : undefined}
    >
      <div className="col-head">
        <h2>{title}</h2>
        <button type="button" className="secondary panel-close" onClick={onClose}>
          关闭
        </button>
      </div>
      <div className="info-body">{children}</div>
    </aside>
  );
}
