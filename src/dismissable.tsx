import { useEffect, type RefObject } from "react";

export function useDismissable(open: boolean, close: () => void, root: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
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
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, close, root]);
}
