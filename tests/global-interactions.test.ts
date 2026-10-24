// @vitest-environment happy-dom
import { act, createElement, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App.js";
import { ConfirmProvider, useConfirm } from "../src/confirm-dialog.js";
import { hasOpenModal } from "../src/focus-trap.js";
import { InfoDrawerShell, PanelScrim } from "../src/info-drawer.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
const records = ["111111111111", "222222222222"].map((id, index) => ({
  id, title: `录音 ${index + 1}`, group: "done", day_group: "today", has_audio: true, has_text: true, text: "转写内容",
}));

async function mount(node = createElement(App)) {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(node));
}

function button(label: string): HTMLButtonElement {
  const element = [...document.querySelectorAll("button")].find((item) => item.textContent === label);
  if (!element) throw new Error(`找不到按钮「${label}」`);
  return element;
}

async function key(value: string, target: EventTarget = document.activeElement || document, shiftKey = false) {
  const event = new KeyboardEvent("keydown", { key: value, shiftKey, bubbles: true, cancelable: true });
  await act(async () => { target.dispatchEvent(event); });
  return event;
}

beforeEach(() => {
  location.hash = "";
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockImplementation(function () {
    return (this.closest("[hidden], [inert]") ? [] : [new DOMRect(0, 0, 100, 40)]) as unknown as DOMRectList;
  });
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    let data: unknown = {};
    if (path.startsWith("/api/recordings?")) data = { items: records, counts: { done: 2, all: 2 }, facets: {} };
    else if (path.startsWith("/api/recordings/")) data = { item: records.find((item) => path.endsWith(item.id)) };
    else if (path === "/api/replacements") data = { rules: [] };
    else if (path.startsWith("/api/today")) data = { date: "2026-10-23", items: [], days: [] };
    return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
  }));
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = undefined;
  document.body.replaceChildren();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  expect(hasOpenModal()).toBe(false);
});

describe("全局快捷键", () => {
  it.each(["button", "a", "summary"])("%s 及其子元素保留空格键原生行为", async (tag) => {
    await mount();
    const audio = document.querySelector("audio")!;
    const play = vi.spyOn(audio, "play");
    const control = document.createElement(tag);
    if (tag === "a") control.setAttribute("href", "#native-control");
    control.tabIndex = 0;
    const label = document.createElement("span");
    label.textContent = "原生操作";
    control.append(label);
    document.querySelector(".main-body")!.append(control);
    control.focus();
    const event = await key(" ", label);
    expect(event.defaultPrevented).toBe(false);
    expect(play).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(control);
    await key("Escape", label);
    expect(document.activeElement).not.toBe(control);
  });

  it("聚焦录音卡后仍能用 j 切换和 / 搜索", async () => {
    await mount();
    const first = document.querySelector<HTMLButtonElement>('.rec-card')!;
    first.focus();
    await key("j");
    expect(location.hash).toBe(`#${records[1].id}`);
    await key("/");
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    expect(document.activeElement?.id).toBe("query");
  });

  it("确认框打开期间 j、t、/、数字和空格均不操作背景，Escape 只关闭确认框", async () => {
    await mount();
    const selected = location.hash;
    const audio = document.querySelector("audio")!;
    const play = vi.spyOn(audio, "play");
    const trigger = button("删除录音");
    trigger.focus();
    await act(async () => trigger.click());
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(document.activeElement).toBe(button("取消"));
    expect(trigger.closest("[inert]")).not.toBeNull();
    for (const value of ["j", "t", "/", "4", " ", "?"]) await key(value);
    expect(location.hash).toBe(selected);
    expect(document.querySelector("h1")?.textContent).toBe("录音");
    expect(document.activeElement).toBe(button("取消"));
    expect(play).not.toHaveBeenCalled();
    await key("Tab");
    expect(document.activeElement).toBe(button("删除"));
    await key("Tab");
    expect(document.activeElement).toBe(button("取消"));
    await key("Tab", document.activeElement!, true);
    expect(document.activeElement).toBe(button("删除"));
    await key("Escape");
    expect(dialog.isConnected).toBe(false);
    expect(document.activeElement).toBe(trigger);
    expect(trigger.closest("[inert]")).toBeNull();
  });

  it("操作弹层聚焦首个动作，Escape 恢复触发按钮；Tab 离开会关闭且保留新焦点", async () => {
    await mount();
    const trigger = document.querySelector<HTMLButtonElement>('button[aria-label="更多"]')!;
    trigger.focus();
    await act(async () => trigger.click());
    expect(document.activeElement).toBe(button("重试所有失败的"));
    expect(document.querySelector('#app-actions')?.getAttribute("role")).toBe("group");
    await key("Escape");
    expect(document.querySelector('#app-actions')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    await act(async () => trigger.click());
    const next = button("导入音频");
    await act(async () => next.focus());
    expect(document.querySelector('#app-actions')).toBeNull();
    expect(document.activeElement).toBe(next);
  });

  it("帮助框聚焦关闭按钮，循环 Tab，并在关闭后恢复更多按钮", async () => {
    await mount();
    const trigger = document.querySelector<HTMLButtonElement>('button[aria-label="更多"]')!;
    trigger.focus();
    await act(async () => trigger.click());
    await act(async () => button("键盘快捷键").click());
    expect(document.activeElement).toBe(button("知道了"));
    await key("Tab");
    expect(document.activeElement).toBe(button("知道了"));
    await key("t");
    expect(document.querySelector("h1")?.textContent).toBe("录音");
    await key("Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("操作项打开确认框后取消，焦点回到仍存在的更多按钮", async () => {
    await mount();
    const trigger = document.querySelector<HTMLButtonElement>('button[aria-label="更多"]')!;
    trigger.focus();
    await act(async () => trigger.click());
    await act(async () => button("重试所有失败的").click());
    expect(document.activeElement).toBe(button("取消"));
    await key("Escape");
    expect(document.activeElement).toBe(trigger);
  });
});

function NestedDialog() {
  const ask = useConfirm();
  const trigger = useRef<HTMLButtonElement | null>(null);
  const [drawer, setDrawer] = useState(false);
  return createElement("div", null,
    createElement("button", { ref: trigger, onClick: () => setDrawer(true) }, "信息"),
    drawer ? createElement(PanelScrim, { onClose: () => setDrawer(false) }) : null,
    createElement(InfoDrawerShell, { layout: "drawer", open: drawer, onClose: () => setDrawer(false), returnFocus: trigger, title: "信息" },
      createElement("button", { onClick: () => { void ask({ title: "继续整理", message: "继续？", confirmLabel: "继续" }); } }, "重新整理")),
  );
}

describe("多层模态框", () => {
  it("确认框独占焦点和 Escape，关闭后回到抽屉，再关闭抽屉回到信息按钮", async () => {
    await mount(createElement(ConfirmProvider, null, createElement(NestedDialog)));
    const trigger = button("信息");
    trigger.focus();
    await act(async () => trigger.click());
    const drawer = document.querySelector<HTMLElement>(".info-pane")!;
    expect(document.querySelector(".panel-scrim")?.closest("[inert]")).toBeNull();
    const action = button("重新整理");
    action.focus();
    await act(async () => action.click());
    expect(drawer.closest("[inert]")).not.toBeNull();
    await key("Tab");
    expect(document.activeElement).toBe(button("继续"));
    await key("Escape");
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(document.activeElement).toBe(action);
    expect(drawer.closest("[inert]")).toBeNull();
    expect(trigger.closest("[inert]")).not.toBeNull();
    await key("Escape");
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(0);
    expect(document.activeElement).toBe(trigger);
    expect(trigger.closest("[inert]")).toBeNull();
    expect(drawer.hasAttribute("inert")).toBe(true);
  });

  it("非删除确认后恢复按钮焦点，原有 inert 状态仍被保留", async () => {
    const hidden = document.createElement("div");
    hidden.setAttribute("inert", "");
    document.body.append(hidden);
    await mount(createElement(ConfirmProvider, null, createElement(NestedDialog)));
    await act(async () => button("信息").click());
    const action = button("重新整理");
    action.focus();
    await act(async () => action.click());
    await act(async () => button("继续").click());
    expect(document.activeElement).toBe(action);
    expect(hidden.hasAttribute("inert")).toBe(true);
  });
});
