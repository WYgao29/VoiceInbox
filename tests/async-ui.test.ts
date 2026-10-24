// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConfirmProvider } from "../src/confirm-dialog.js";
import { InboxProvider, useInbox } from "../src/inbox.js";
import { Recordings } from "../src/views/Recordings.js";
import { Today } from "../src/views/Today.js";
import { InfoPanel } from "../src/views/InfoPanel.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let inbox: ReturnType<typeof useInbox>;
let root: Root;
let holdPath: string;
let failPath: string;
let release: (response: Response) => void;
const record = (id: string) => ({ id, title: `录音 ${id}`, group: "done", has_text: true, has_audio: true, duration_s: 125, text: "测试原文", day_group: "today", analysis: { route: "full", llm: true, todos: [{ key: "a", text: "第一项待办" }, { key: "b", text: "第二项待办" }] } });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

function Harness() {
  inbox = useInbox();
  return createElement("div", null,
    inbox.state.view === "today" ? createElement(Today) : createElement(Recordings),
    createElement(InfoPanel, { layout: "dock", open: true, onClose: () => undefined, returnFocus: { current: null } }),
  );
}

async function mount() {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(createElement(ConfirmProvider, null, createElement(InboxProvider, null, createElement(Harness)))));
}

function button(label: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find((el) => el.textContent === label);
  if (!found) throw new Error(`找不到 ${label}`);
  return found;
}

beforeEach(() => {
  vi.useFakeTimers();
  location.hash = "";
  holdPath = "";
  failPath = "";
  vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
    if (path === holdPath) return new Promise<Response>((resolve) => { release = resolve; });
    if (path === failPath) return json({ error: "暂时无法读取" }, 503);
    if (options?.method === "POST") return json({});
    if (path.startsWith("/api/recordings?")) return json({ items: [record("one"), record("two")], counts: { all: 2 }, facets: {} });
    if (path.startsWith("/api/recordings/")) return json({ item: record(path.split("/")[3]) });
    if (path.startsWith("/api/today")) return json({ date: "2026-10-23", items: [record("one")], todos: [{ id: "one", key: "a", text: "第一项待办" }, { id: "one", key: "b", text: "第二项待办" }], days: [{ date: "2026-10-23", count: 1 }, { date: "2026-10-19", count: 1 }] });
    return json({});
  }));
});

afterEach(async () => {
  await act(async () => root?.unmount());
  document.body.replaceChildren();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("读取状态和恢复", () => {
  it("选择录音后显示读取状态，失败可原位重试", async () => {
    await mount();
    holdPath = "/api/recordings/two";
    let reading: Promise<void>;
    await act(async () => { reading = inbox.select("two"); });
    expect(document.querySelector('.detail')?.textContent).toContain("正在读取录音");
    expect(document.querySelector('.detail')?.textContent).not.toContain("选一条录音");
    await act(async () => { release(json({ error: "录音读取失败" }, 503)); await reading; });
    expect(document.querySelector('.detail')?.textContent).toContain("录音读取失败");
    holdPath = "";
    await act(async () => button("重试读取").click());
    expect(inbox.state.detail?.id).toBe("two");
    expect(inbox.state.detailError).toBe("");
  });

  it("过期的详情失败不能覆盖后来选中的录音", async () => {
    await mount();
    holdPath = "/api/recordings/two";
    let reading: Promise<void>;
    await act(async () => { reading = inbox.select("two"); });
    await act(async () => inbox.select("one"));
    await act(async () => { release(json({ error: "旧请求失败" }, 503)); await reading; });
    expect(inbox.state.detail?.id).toBe("one");
    expect(inbox.state.detailError).toBe("");
  });

  it("首次读取今天失败会显示原因和重试入口", async () => {
    await mount();
    failPath = "/api/today";
    await act(async () => inbox.setView("today"));
    expect(document.body.textContent).toContain("当日内容未能读取");
    expect(document.body.textContent).toContain("暂时无法读取");
    failPath = "";
    await act(async () => button("重试读取").click());
    expect(document.body.textContent).toContain("这天的录音");
    expect(inbox.state.todayError).toBe("");
  });
});

describe("待办与播放反馈", () => {
  it("待办即时显示保存状态，阻止重复提交，失败后恢复原值", async () => {
    await mount();
    await act(async () => inbox.setTab("todos"));
    holdPath = "/api/todos";
    let saving: Promise<void>;
    await act(async () => { saving = inbox.toggleTodo("one", "a", true); });
    const checkbox = document.querySelector<HTMLInputElement>('.detail .todo-list input')!;
    checkbox.focus();
    expect(checkbox.checked).toBe(true);
    expect(checkbox.getAttribute("aria-disabled")).toBe("true");
    expect(document.querySelector('.detail .todo-list')?.textContent).toContain("保存中");
    await act(async () => inbox.toggleTodo("one", "a", false));
    expect(vi.mocked(fetch).mock.calls.filter(([path]) => path === "/api/todos")).toHaveLength(1);
    await act(async () => { release(json({ error: "待办保存失败" }, 503)); await saving; });
    expect(checkbox.checked).toBe(false);
    expect(checkbox.getAttribute("aria-disabled")).toBe("false");
    expect(document.activeElement).toBe(checkbox);
    expect(document.querySelector('.detail .todo-list')?.textContent).toContain("待办保存失败");
  });

  it("完成后移除的待办把键盘焦点交给下一项", async () => {
    await mount();
    await act(async () => inbox.setView("today"));
    const checkbox = document.querySelector<HTMLInputElement>('[data-todo-list] input')!;
    checkbox.focus();
    await act(async () => checkbox.click());
    await act(async () => vi.advanceTimersByTimeAsync(32));
    expect(document.querySelector('[data-todo-list]')?.textContent).not.toContain("第一项待办");
    expect(document.activeElement).toBe(document.querySelector('[data-todo-list] input'));
  });

  it("播放失败可见且可以重新加载，同时保留已知时长", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "play").mockRejectedValue(new Error("无法解码"));
    const load = vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
    await mount();
    expect(document.querySelector('.player-bar')?.textContent).toContain("2:05");
    await act(async () => document.querySelector<HTMLButtonElement>('.player-toggle')?.click());
    expect(document.querySelector('.player-status')?.textContent).toContain("音频未能播放");
    await act(async () => button("重新加载音频").click());
    expect(load).toHaveBeenCalledOnce();
  });

  it("内容标签可通过方向键切换并同步选中语义与面板", async () => {
    await mount();
    const first = document.querySelector<HTMLButtonElement>('[role=tab][aria-selected=true]')!;
    first.focus();
    await act(async () => first.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true })));
    expect(inbox.state.tab).toBe("todos");
    expect(document.activeElement?.id).toBe("detail-tab-todos");
    expect(document.querySelectorAll('[role=tab][aria-selected=true]')).toHaveLength(1);
    expect(document.querySelector('[role=tabpanel]')?.getAttribute("aria-labelledby")).toBe("detail-tab-todos");
  });
});
