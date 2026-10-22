import { useEffect, type RefObject } from "react";

export function useDismissable(
  open: boolean,
  close: () => void,
  root: RefObject<HTMLElement | null>,
  pointer: "pointerdown" | "click" = "pointerdown",
): void {
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: Event) => {
      const el = root.current;
      if (!el || !(event.target instanceof Node) || el.contains(event.target)) return;
      close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      close();
    };
    document.addEventListener(pointer, onPointer);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener(pointer, onPointer);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, close, root, pointer]);
}
