import { useEffect, useLayoutEffect, type RefObject } from "react";

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

const TABBABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [contenteditable="true"], [tabindex]:not([tabindex="-1"])';

const modalLayers: HTMLElement[] = [];
const inertBeforeModal = new Map<HTMLElement, string | null>();

export function hasOpenModal(): boolean {
  return modalLayers.length > 0;
}

export function isTopModal(root: HTMLElement | null): boolean {
  return modalLayers.at(-1) === root;
}

// Only the top modal owns focus. Rebuilding its inert ancestors also handles a
// confirmation opened from an information drawer without trapping both layers.
function updateModalBackground(): void {
  for (const [element, previous] of inertBeforeModal) {
    if (previous === null) element.removeAttribute("inert");
    else element.setAttribute("inert", previous);
  }
  inertBeforeModal.clear();
  let current = modalLayers.at(-1);
  while (current && current !== document.body) {
    const parent = current.parentElement;
    if (!parent) break;
    for (const sibling of parent.children) {
      if (!(sibling instanceof HTMLElement) || sibling === current || sibling.hasAttribute("data-modal-backdrop")) continue;
      inertBeforeModal.set(sibling, sibling.getAttribute("inert"));
      sibling.setAttribute("inert", "");
    }
    current = parent;
  }
}

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

export function useModalFocus(
  open: boolean,
  root: RefObject<HTMLElement | null>,
  returnFocus?: RefObject<HTMLElement | null>,
  initialFocus?: RefObject<HTMLElement | null>,
): void {
  useIsoLayoutEffect(() => {
    if (!open) return;
    const el = root.current;
    if (!el) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    modalLayers.push(el);
    updateModalBackground();
    const focusFirst = () => (initialFocus?.current || tabbableElements(el)[0] || el).focus();
    focusFirst();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !isTopModal(el)) return;
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
    const onFocus = (event: FocusEvent) => {
      if (!isTopModal(el) || (event.target instanceof Node && el.contains(event.target))) return;
      focusFirst();
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("focusin", onFocus, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("focusin", onFocus, true);
      const index = modalLayers.lastIndexOf(el);
      if (index >= 0) modalLayers.splice(index, 1);
      updateModalBackground();
      const back = returnFocus ? returnFocus.current : previous;
      if (back?.isConnected && !back.closest("[inert]")) back.focus();
    };
  }, [open, root, returnFocus, initialFocus]);
}

export const useDrawerFocus = useModalFocus;
