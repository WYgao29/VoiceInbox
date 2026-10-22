import { useEffect, type RefObject } from "react";

const TABBABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function nextTabbableIndex(count: number, current: number, backward: boolean): number {
  if (count <= 0) return -1;
  if (backward) return current <= 0 ? count - 1 : current - 1;
  if (current >= count - 1) return 0;
  return current + 1;
}

export function tabbableElements(root: ParentNode): HTMLElement[] {
  const nodes = root.querySelectorAll<HTMLElement>(TABBABLE);
  const result: HTMLElement[] = [];
  for (const el of nodes) {
    if (el.tabIndex < 0) continue;
    if (el.closest("[inert], [hidden]")) continue;
    if (el.getClientRects().length === 0) continue;
    result.push(el);
  }
  return result;
}

export function useDrawerFocus(
  open: boolean,
  root: RefObject<HTMLElement | null>,
  returnFocus: RefObject<HTMLElement | null> | undefined,
): void {
  useEffect(() => {
    if (!open) return;
    const el = root.current;
    if (!el) return;
    const items = tabbableElements(el);
    (items[0] || el).focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const nodes = tabbableElements(el);
      event.preventDefault();
      if (!nodes.length) {
        el.focus();
        return;
      }
      const index = nodes.indexOf(document.activeElement as HTMLElement);
      const next = nextTabbableIndex(nodes.length, index, event.shiftKey);
      nodes[next]?.focus();
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      returnFocus?.current?.focus();
    };
  }, [open, root, returnFocus]);
}
