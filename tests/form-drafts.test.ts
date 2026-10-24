// @vitest-environment happy-dom
import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Settings } from "../src/views/Settings.js";
import { Vocab } from "../src/views/Vocab.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const inbox = vi.hoisted(() => ({
  state: {
    busy: false,
    monitor: { state: "off", desired: { interval_min: 15 }, presets: [1, 5, 15, 30, 60] },
    settingsFile: { since: "2026-10-01", max_per_run: 20, language: "Chinese" },
    featuresFile: { llm_enabled: true, obsidian_enabled: true, digest_enabled: true, flash_max_s: 20 },
    envOverrides: [],
    featureMeta: {},
    hotwordText: "原热词",
    rules: [],
  },
  saveSettings: vi.fn(async (_body: unknown): Promise<boolean> => true),
  saveFeatures: vi.fn(async (_body: unknown): Promise<boolean> => false),
  saveHotwords: vi.fn(async (_text: string): Promise<boolean> => false),
  addRule: vi.fn(async (_wrong: string, _right: string): Promise<boolean> => true),
  refreshSystem: vi.fn(),
}));
vi.mock("../src/inbox.js", () => ({ useInbox: () => inbox }));
vi.mock("../src/confirm-dialog.js", () => ({ useConfirm: () => vi.fn(async () => false) }));

let root: Root | undefined;
async function show(node: ReactNode) {
  if (!root) {
    const host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  }
  await act(async () => root?.render(node));
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = undefined;
  document.body.replaceChildren();
});

async function enter(field: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submit(form: HTMLFormElement) {
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}
function settingsForms() {
  return [...document.querySelectorAll<HTMLFormElement>("form.panel")];
}
function input(form: Element, index = 0) {
  return form.querySelectorAll<HTMLInputElement>("input")[index];
}

describe("设置与词库表单", () => {
  it("所有设置草稿离开后恢复，刷新不覆盖，保存分别显示结果并防止重复发送", async () => {
    await show(createElement(Settings));
    let [tx, ft] = settingsForms();
    await enter(input(tx), "2026-11-10");
    await enter(input(tx, 1), "38");
    await act(async () => {
      const language = tx.querySelector("select")!;
      language.value = "English";
      language.dispatchEvent(new Event("change", { bubbles: true }));
    });
    for (const checkbox of ft.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')) {
      await act(async () => checkbox.click());
    }
    await enter(input(ft, 3), "72");
    await enter(document.querySelector<HTMLInputElement>('[aria-label="自定义分钟数"]')!, "47");
    await show(createElement("div", null, "搜索结果"));
    inbox.state.settingsFile = { since: "2026-10-20", max_per_run: 10, language: "Chinese" };
    inbox.state.featuresFile = { llm_enabled: true, obsidian_enabled: true, digest_enabled: true, flash_max_s: 30 };
    inbox.state.monitor.desired.interval_min = 30;
    await show(createElement(Settings));
    [tx, ft] = settingsForms();
    expect(input(tx).value).toBe("2026-11-10");
    expect(input(tx, 1).value).toBe("38");
    expect(tx.querySelector("select")?.value).toBe("English");
    expect([...ft.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].map((field) => field.checked)).toEqual([false, false, false]);
    expect(input(ft, 3).value).toBe("72");
    expect(document.querySelector<HTMLInputElement>('[aria-label="自定义分钟数"]')?.value).toBe("47");
    expect(document.querySelectorAll(".unsaved")).toHaveLength(3);
    expect(document.querySelector('[aria-label="检查间隔"] [aria-pressed="true"]')?.textContent).toContain("30");

    let finish!: (saved: boolean) => void;
    inbox.saveSettings.mockImplementationOnce(() => new Promise<boolean>((resolve) => { finish = resolve; }));
    await submit(tx);
    await submit(tx);
    expect(inbox.saveSettings).toHaveBeenCalledOnce();
    expect(tx.getAttribute("aria-busy")).toBe("true");
    await show(createElement("div", null, "其他页面"));
    await show(createElement(Settings));
    [tx, ft] = settingsForms();
    expect(tx.querySelector("button")?.disabled).toBe(true);
    await act(async () => finish(true));
    expect(tx.querySelector(".unsaved")).toBeNull();
    expect(tx.querySelector('[role="status"]')?.textContent).toContain("已保存");
    await submit(ft);
    expect(ft.querySelector('[data-tone="error"]')?.textContent).toContain("没有保存");
    expect(input(ft, 3).value).toBe("72");
    expect(ft.querySelector(".unsaved")).not.toBeNull();
  });

  it("热词和纠错草稿经页面卸载后恢复，失败保留、成功同步或清空", async () => {
    await show(createElement(Vocab));
    await enter(document.querySelector("textarea")!, "草稿热词、草稿热词");
    let correction = document.querySelector<HTMLFormElement>("form.fix-row")!;
    await enter(input(correction), "错误草稿");
    await enter(input(correction, 1), "正确草稿");
    await show(createElement("div", null, "搜索结果"));
    inbox.state.hotwordText = "后台热词";
    await show(createElement(Vocab));
    const textarea = document.querySelector("textarea")!;
    correction = document.querySelector<HTMLFormElement>("form.fix-row")!;
    expect(textarea.value).toBe("草稿热词、草稿热词");
    expect(input(correction).value).toBe("错误草稿");
    expect(input(correction, 1).value).toBe("正确草稿");
    expect(textarea.getAttribute("aria-labelledby")).toBe("hotwords-title");
    expect(textarea.getAttribute("aria-describedby")).toBe("hotwords-description");
    const hotwords = document.querySelector<HTMLFormElement>("form.panel")!;
    await submit(hotwords);
    expect(hotwords.querySelector('[data-tone="error"]')?.textContent).toContain("没有保存");
    expect(textarea.value).toBe("草稿热词、草稿热词");
    inbox.saveHotwords.mockImplementationOnce(async () => {
      inbox.state.hotwordText = "草稿热词";
      return true;
    });
    await submit(hotwords);
    expect(textarea.value).toBe("草稿热词");
    expect(hotwords.querySelector(".unsaved")).toBeNull();
    expect(hotwords.querySelector('[data-tone="success"]')).not.toBeNull();
    await enter(textarea, "草稿热词，草稿热词");
    inbox.saveHotwords.mockResolvedValueOnce(true);
    await submit(hotwords);
    expect(textarea.value).toBe("草稿热词");
    expect(hotwords.querySelector(".unsaved")).toBeNull();
    await submit(correction);
    expect(inbox.addRule).toHaveBeenCalledWith("错误草稿", "正确草稿");
    expect(input(correction).value).toBe("");
    expect(input(correction, 1).value).toBe("");
    expect(document.querySelectorAll(".unsaved")).toHaveLength(0);
    expect(document.querySelectorAll('[data-tone="success"]')).toHaveLength(2);
  });
});
