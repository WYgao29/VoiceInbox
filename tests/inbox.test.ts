// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ConfirmProvider } from "../src/confirm-dialog.js";
import { InboxProvider, useInbox } from "../src/inbox.js";
import { Recordings } from "../src/views/Recordings.js";
import { Settings } from "../src/views/Settings.js";
import { Vocab } from "../src/views/Vocab.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Inbox = ReturnType<typeof useInbox>;
let inbox: Inbox;
let root: Root | undefined;
let rejectedPath = "";
const errorMessage = "输入没有通过校验，没有保存";
const recId = "0123456789ab";
const todayRequests = new Map<string, { resolve: (response: Response) => void; reject: (error: Error) => void }>();

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function Capture() {
  inbox = useInbox();
  return createElement("p", { role: "status" }, inbox.state.notice);
}

async function mount(page?: ReactNode): Promise<void> {
  const host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(createElement(ConfirmProvider, null, createElement(InboxProvider, null, createElement(Capture), page)));
  });
}

async function enter(field: HTMLInputElement | HTMLTextAreaElement, value: string): Promise<void> {
  const prototype = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function submit(form: HTMLFormElement): Promise<void> {
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
}

function button(label: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find((candidate) => candidate.textContent === label);
  if (!found) throw new Error(`找不到按钮 ${label}`);
  return found;
}

beforeEach(() => {
  vi.useFakeTimers();
  rejectedPath = "";
  todayRequests.clear();
  location.hash = "";
  vi.stubGlobal("fetch", vi.fn(async (input: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      if (input === rejectedPath) return json({ error: errorMessage }, 400);
      if (input === "/api/hotwords") return json({ text: "新的热词" });
      return json({});
    }
    if (input.startsWith("/api/today?")) {
      return new Promise<Response>((resolve, reject) => todayRequests.set(input, { resolve, reject }));
    }
    if (input.startsWith("/api/recordings?")) return json({ items: [], counts: {}, facets: {} });
    if (input === `/api/recordings/${recId}`) return json({ item: { id: recId, group: "done", has_text: true, text: "测试转写" } });
    if (input === "/api/settings") return json({ file: { since: "2026-10-01", max_per_run: 10, language: "Chinese" } });
    if (input === "/api/features") return json({ file: { llm_enabled: true, obsidian_enabled: true, digest_enabled: true, flash_max_s: 20 } });
    if (input === "/api/hotwords") return json({ text: "原来的热词" });
    if (input === "/api/replacements") return json({ rules: [{ wrong: "听错", right: "正确" }] });
    return json({});
  }));
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = undefined;
  document.body.replaceChildren();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("日期请求", () => {
  it("较旧的响应不能覆盖最后选择的日期", async () => {
    await mount();
    await act(async () => {
      inbox.setDay("2026-10-20");
      inbox.setDay("2026-10-21");
    });
    await act(async () => todayRequests.get("/api/today?date=2026-10-21")?.resolve(json({ date: "2026-10-21", items: [{ id: "new" }] })));
    await act(async () => todayRequests.get("/api/today?date=2026-10-20")?.resolve(json({ date: "2026-10-20", items: [{ id: "old" }] })));
    expect(inbox.state.day).toBe("2026-10-21");
    expect(inbox.state.today?.items?.[0].id).toBe("new");
  });

  it("较旧请求失败时不干扰已经加载的新日期", async () => {
    await mount();
    await act(async () => {
      inbox.setDay("2026-10-20");
      inbox.setDay("2026-10-21");
    });
    await act(async () => todayRequests.get("/api/today?date=2026-10-21")?.resolve(json({ date: "2026-10-21" })));
    await act(async () => todayRequests.get("/api/today?date=2026-10-20")?.reject(new Error("旧请求断开")));
    expect(inbox.state.day).toBe("2026-10-21");
    expect(inbox.state.notice).toBe("");
  });

  it("最后选择的日期加载失败时仍显示错误", async () => {
    await mount();
    await act(async () => inbox.setDay("2026-10-21"));
    await act(async () => todayRequests.get("/api/today?date=2026-10-21")?.reject(new Error("当前请求断开")));
    expect(inbox.state.notice).toBe("当前请求断开");
  });
});

describe("保存失败", () => {
  const saves: { name: string; path: string; run: (api: Inbox) => Promise<unknown> }[] = [
    { name: "转写设置", path: "/api/settings", run: (api) => api.saveSettings({ since: "2026-99-99", max_per_run: 10, language: "Chinese" }) },
    { name: "功能开关", path: "/api/features", run: (api) => api.saveFeatures({ llm_enabled: false, obsidian_enabled: true, digest_enabled: true, flash_max_s: 20 }) },
    { name: "热词", path: "/api/hotwords", run: (api) => api.saveHotwords("待保存的热词") },
    { name: "纠错表", path: "/api/replacements", run: (api) => api.addRule("听错", "正确") },
  ];

  it.each(saves)("$name 返回失败结果并提示接口错误", async ({ path, run }) => {
    await mount();
    rejectedPath = path;
    await act(async () => {
      await expect(run(inbox)).resolves.toBe(false);
    });
    expect(inbox.state.notice).toBe(errorMessage);
  });

  it("删除纠错失败时提示错误并保留原规则", async () => {
    await mount(createElement(Vocab));
    rejectedPath = "/api/replacements/remove";
    await act(async () => button("删掉").click());
    await act(async () => button("删除").click());
    expect(inbox.state.notice).toBe(errorMessage);
    expect(inbox.state.rules).toEqual([{ wrong: "听错", right: "正确" }]);
  });

  it("设置和功能开关保存失败后保留修改", async () => {
    await mount(createElement(Settings));
    await act(async () => inbox.setView("settings"));
    const forms = document.querySelectorAll<HTMLFormElement>("form.panel");
    const date = forms[0].querySelector<HTMLInputElement>("input")!;
    await enter(date, "2026-99-99");
    rejectedPath = "/api/settings";
    await submit(forms[0]);
    expect(inbox.state.notice).toBe(errorMessage);
    expect(date.value).toBe("2026-99-99");
    expect(inbox.state.settingsFile?.since).toBe("2026-10-01");
    const llm = forms[1].querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    await act(async () => llm.click());
    rejectedPath = "/api/features";
    await submit(forms[1]);
    expect(inbox.state.notice).toBe(errorMessage);
    expect(llm.checked).toBe(false);
    expect(inbox.state.featuresFile?.llm_enabled).toBe(true);
  });

  it("热词和纠错表保存失败后保留输入", async () => {
    await mount(createElement(Vocab));
    await act(async () => inbox.setView("vocab"));
    const words = document.querySelector<HTMLTextAreaElement>("textarea")!;
    await enter(words, "待保存的热词");
    rejectedPath = "/api/hotwords";
    await act(async () => button("保存热词").click());
    expect(inbox.state.notice).toBe(errorMessage);
    expect(words.value).toBe("待保存的热词");
    const form = document.querySelector<HTMLFormElement>("form.fix-row")!;
    const fields = form.querySelectorAll<HTMLInputElement>("input");
    await enter(fields[0], "错误词");
    await enter(fields[1], "正确词");
    rejectedPath = "/api/replacements";
    await submit(form);
    expect(inbox.state.notice).toBe(errorMessage);
    expect(fields[0].value).toBe("错误词");
    expect(fields[1].value).toBe("正确词");
    rejectedPath = "";
    await submit(form);
    expect(fields[0].value).toBe("");
    expect(fields[1].value).toBe("");
  });

  it("录音详情纠错失败后保留输入，成功后才清空", async () => {
    await mount(createElement(Recordings));
    await act(async () => inbox.select(recId));
    const form = document.querySelector<HTMLFormElement>("form.fix-row")!;
    const fields = form.querySelectorAll<HTMLInputElement>("input");
    await enter(fields[0], "错误词");
    await enter(fields[1], "正确词");
    rejectedPath = "/api/replacements";
    await submit(form);
    expect(inbox.state.notice).toBe(errorMessage);
    expect(fields[0].value).toBe("错误词");
    expect(fields[1].value).toBe("正确词");
    rejectedPath = "";
    await submit(form);
    expect(fields[0].value).toBe("");
    expect(fields[1].value).toBe("");
  });
});
