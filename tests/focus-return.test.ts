// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from "vitest";
import { act, createElement, useRef, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
import { ConfirmProvider, useConfirm } from "../src/confirm-dialog.js";
import { InfoDrawerShell, PanelScrim } from "../src/info-drawer.js";

const mounted: Root[] = [];

async function mount(node: ReactNode): Promise<void> {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  mounted.push(root);
  await act(async () => {
    root.render(node);
  });
}

afterEach(async () => {
  while (mounted.length) {
    const root = mounted.pop();
    if (!root) continue;
    await act(async () => {
      root.unmount();
    });
  }
  document.body.replaceChildren();
});

function buttonByText(label: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find((button) => button.textContent === label);
  if (!found) throw new Error(`找不到按钮「${label}」`);
  return found as HTMLButtonElement;
}

function DrawerHarness() {
  const infoButton = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(true);
  const close = () => setOpen(false);
  return createElement(
    "div",
    null,
    createElement("button", { ref: infoButton, type: "button" }, "信息"),
    open ? createElement(PanelScrim, { onClose: close }) : null,
    createElement(InfoDrawerShell, { layout: "drawer", open, onClose: close, returnFocus: infoButton, title: "信息" }, "正文"),
  );
}

function PolishTrigger() {
  const ask = useConfirm();
  return createElement(
    "button",
    {
      type: "button",
      onClick: () => {
        void ask({ title: "重新整理", message: "继续？", confirmLabel: "继续" });
      },
    },
    "重新润色",
  );
}

async function openPolishConfirm(): Promise<void> {
  await mount(createElement(ConfirmProvider, null, createElement(PolishTrigger)));
  const trigger = buttonByText("重新润色");
  trigger.focus();
  await act(async () => {
    trigger.click();
  });
  expect(document.activeElement?.textContent).toBe("取消");
}

describe("信息抽屉遮罩", () => {
  it("mousedown 不关抽屉，click 才关，焦点回到「信息」", async () => {
    await mount(createElement(DrawerHarness));
    const scrim = document.querySelector(".panel-scrim");
    if (!(scrim instanceof HTMLButtonElement)) throw new Error("找不到遮罩");
    const down = new MouseEvent("mousedown", { bubbles: true, cancelable: true });
    await act(async () => {
      scrim.dispatchEvent(down);
      scrim.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true }));
    });
    expect(down.defaultPrevented).toBe(true);
    expect(document.querySelector(".panel-scrim")).not.toBeNull();
    await act(async () => {
      scrim.click();
    });
    expect(document.querySelector(".panel-scrim")).toBeNull();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement?.textContent).toBe("信息");
    expect(document.activeElement).not.toBe(document.body);
  });

  it("按 Esc 后焦点回到「信息」", async () => {
    await mount(createElement(DrawerHarness));
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    });
    expect(document.querySelector(".panel-scrim")).toBeNull();
    expect(document.activeElement?.textContent).toBe("信息");
  });

  it("点「关闭」后焦点回到「信息」", async () => {
    await mount(createElement(DrawerHarness));
    await act(async () => {
      buttonByText("关闭").click();
    });
    expect(document.querySelector(".panel-scrim")).toBeNull();
    expect(document.activeElement?.textContent).toBe("信息");
  });
});

describe("确认框焦点", () => {
  it("按 Esc 后焦点回到「重新润色」", async () => {
    await openPolishConfirm();
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement?.textContent).toBe("重新润色");
    expect(document.activeElement).not.toBe(document.body);
  });

  it("点「取消」后焦点回到「重新润色」", async () => {
    await openPolishConfirm();
    await act(async () => {
      buttonByText("取消").click();
    });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement?.textContent).toBe("重新润色");
    expect(document.activeElement).not.toBe(document.body);
  });
});
