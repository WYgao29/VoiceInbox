import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import { hasOpenModal, isTopModal, tabbableElements } from "./focus-trap";

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export function useDismissable(
  open: boolean,
  close: () => void,
  root: RefObject<HTMLElement | null>,
  pointer: "pointerdown" | "click" = "pointerdown",
): void {
  const returnTo = useRef<HTMLElement | null>(null);
  const restore = useRef(false);

  useIsoLayoutEffect(() => {
    if (!open) return;
    const element = root.current;
    const popover = element?.querySelector<HTMLElement>(".menu-pop");
    if (!element || !popover) return;
    returnTo.current = element.querySelector<HTMLButtonElement>(":scope > button") || null;
    restore.current = true;
    (tabbableElements(popover)[0] || popover).focus();
    return () => {
      const trigger = returnTo.current;
      if (restore.current && trigger?.isConnected && !hasOpenModal()) trigger.focus();
      returnTo.current = null;
    };
  }, [open, root]);

  useEffect(() => {
    if (!open) return;
    const ownsDismissal = () => !hasOpenModal() || isTopModal(root.current);
    const onPointer = (event: Event) => {
      const el = root.current;
      if (!ownsDismissal() || !el || !(event.target instanceof Node) || el.contains(event.target)) return;
      // An outside click keeps focus on its own target; Escape/action selection
      // returns focus to the control that opened the action group.
      restore.current = false;
      close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !ownsDismissal()) return;
      event.preventDefault();
      event.stopPropagation();
      close();
    };
    const onFocus = (event: FocusEvent) => {
      const element = root.current;
      if (!element?.querySelector(".menu-pop") || !(event.target instanceof Node) || element.contains(event.target)) return;
      restore.current = false;
      close();
    };
    // The opening click has already passed capture before React mounts this
    // listener. Listening in capture avoids treating that same click as outside.
    document.addEventListener(pointer, onPointer, true);
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("focusin", onFocus);
    return () => {
      document.removeEventListener(pointer, onPointer, true);
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("focusin", onFocus);
    };
  }, [open, close, root, pointer]);
}
