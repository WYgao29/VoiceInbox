// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Recording } from "../shared/types.js";
import { ConfirmProvider } from "../src/confirm-dialog.js";
import { InboxProvider, useInbox, type TodayData } from "../src/inbox.js";
import { Recordings } from "../src/views/Recordings.js";
import { Today } from "../src/views/Today.js";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ids = ["111111111111", "222222222222", "333333333333"];
let inbox: ReturnType<typeof useInbox>;
let root: Root | undefined;
let records: Recording[];
let failedIds: string[];
let deleteStatus: number;
let digest: NonNullable<TodayData["digest"]>;
let requests: { path: string; method: string; body?: unknown }[];
let releaseDelete: (() => void) | undefined;
let holdDelete: boolean;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function Harness() {
  inbox = useInbox();
  return createElement("div", null,
    createElement("p", { role: "status" }, inbox.state.notice),
    inbox.state.view === "today" ? createElement(Today) : createElement(Recordings),
  );
}

async function mount() {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(createElement(ConfirmProvider, null, createElement(InboxProvider, null, createElement(Harness)))));
}

function button(text: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find((node) => node.textContent === text);
  if (!found) throw new Error(`找不到按钮「${text}」`);
  return found;
}

async function click(text: string) {
  await act(async () => button(text).click());
}

function checkbox(label: string): HTMLInputElement {
  const found = document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
  if (!found) throw new Error(`找不到复选框「${label}」`);
  return found;
}

async function check(label: string) {
  await act(async () => checkbox(label).click());
}

async function confirmDelete() {
  await click("删除");
  await act(async () => vi.advanceTimersByTimeAsync(0));
}

function deleteRequests() {
  return requests.filter((request) => request.path === "/api/recordings/delete");
}

beforeEach(() => {
  vi.useFakeTimers();
  location.hash = "";
  records = ids.map((id, index) => ({ id, title: `录音 ${index + 1}`, group: "done", has_text: true, text: "测试转写", has_audio: true, day_group: "today" }));
  failedIds = [];
  deleteStatus = 200;
  holdDelete = false;
  releaseDelete = undefined;
  digest = { state: "ready", md: "旧的摘要内容", uri: "obsidian://open?file=test" };
  requests = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
    const method = options?.method || "GET";
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    requests.push({ path, method, body });
    if (path === "/api/recordings/delete") {
      if (holdDelete) await new Promise<void>((resolve) => { releaseDelete = resolve; });
      if (deleteStatus !== 200) return json({ error: "另一轮正在进行，这次没有删除。" }, deleteStatus);
      const selected = body.ids as string[];
      const deleted = selected.filter((id) => !failedIds.includes(id));
      records = records.filter((item) => !deleted.includes(item.id)).map((item) => failedIds.includes(item.id)
        ? { ...item, group: "failed", status_label: "删除未完成", deletion_pending: true, reason: "笔记暂时无法写入" }
        : item);
      digest = { state: records.length ? "pending" : "none", md: "" };
      return json({ ok: failedIds.length === 0, deleted_ids: deleted, failed: selected.filter((id) => failedIds.includes(id)).map((id) => ({ id, error: "笔记暂时无法写入" })), digest_days: ["2026-10-23"] });
    }
    if (path.startsWith("/api/recordings?")) {
      const params = new URLSearchParams(path.split("?")[1]);
      const visible = records.filter((item) => (params.get("status") === "all" || item.group === params.get("status")) && (!params.get("q") || item.title?.includes(params.get("q")!)));
      return json({ items: visible, counts: { all: records.length, done: records.filter((item) => item.group === "done").length, failed: records.filter((item) => item.group === "failed").length }, facets: {} });
    }
    const detail = /^\/api\/recordings\/([0-9a-f]+)$/.exec(path);
    if (detail) {
      const item = records.find((recording) => recording.id === detail[1]);
      return item ? json({ item }) : json({ error: "录音不存在" }, 404);
    }
    if (path.startsWith("/api/today")) return json({ date: "2026-10-23", items: records.filter((item) => !item.deletion_pending), todos: [], days: [{ date: "2026-10-23", count: records.length }], digest, digest_ready: true });
    if (path === "/api/replacements") return json({ rules: [] });
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

describe("录音删除", () => {
  it.each(["done", "pending", "processing", "failed", "skipped"])("%s 录音详情始终提供单条删除", async (group) => {
    records[0] = { ...records[0], group, has_text: false, has_audio: false };
    await mount();
    await act(async () => inbox.select(ids[0]));
    expect(button("删除录音").disabled).toBe(false);
  });

  it("确认默认取消；取消保留音频和数据并恢复触发按钮焦点", async () => {
    await mount();
    const audio = inbox.audioRef.current!;
    const pause = vi.spyOn(audio, "pause");
    const trigger = button("删除录音");
    trigger.focus();
    await click("删除录音");
    expect(document.activeElement?.textContent).toBe("取消");
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("录音 1");
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("原件");
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("手写");
    expect(pause).not.toHaveBeenCalled();
    await click("取消");
    expect(document.activeElement).toBe(trigger);
    expect(deleteRequests()).toHaveLength(0);
    expect(records).toHaveLength(3);
  });

  it("选择模式支持当前结果全选、取消勾选和退出", async () => {
    await mount();
    await click("选择");
    await check("全选当前结果");
    expect(inbox.state.checkedIds).toEqual(ids);
    await check("选择 录音 2");
    expect(inbox.state.checkedIds).toEqual([ids[0], ids[2]]);
    expect(checkbox("全选当前结果").indeterminate).toBe(true);
    await click("完成选择");
    expect(inbox.state.checkedIds).toEqual([]);
    expect(document.querySelector('.rec-check')).toBeNull();
  });

  it("搜索和各项筛选变化清空勾选", async () => {
    await mount();
    const changes: (() => void)[] = [
      () => inbox.setQuery("录音"), () => inbox.setStatus("all"), () => inbox.setKind("full"),
      () => inbox.setScene("会议"), () => inbox.setTag("项目"), () => inbox.setFrom("2026-10-01"),
      () => inbox.setTo("2026-10-23"), () => inbox.clearDates(), () => inbox.clearBrowse(),
    ];
    for (const change of changes) {
      await act(async () => {
        inbox.setSelecting(true);
        inbox.toggleRecordingSelection(ids[0]);
      });
      expect(inbox.state.checkedIds).toContain(ids[0]);
      await act(async () => change());
      expect(inbox.state.checkedIds).toEqual([]);
    }
  });

  it("搜索等待期间勾选的旧结果不会留在新的结果选择中", async () => {
    await mount();
    await click("选择");
    await act(async () => inbox.setQuery("录音 2"));
    await check("选择 录音 1");
    expect(inbox.state.checkedIds).toEqual([ids[0]]);
    await act(async () => vi.advanceTimersByTimeAsync(200));
    expect(inbox.state.items.map((item) => item.id)).toEqual([ids[1]]);
    expect(inbox.state.checkedIds).toEqual([]);
  });

  it("确认后先暂停待删音频；成功刷新计数和今天，并选下一条", async () => {
    await mount();
    await act(async () => inbox.select(ids[1]));
    const pause = vi.spyOn(inbox.audioRef.current!, "pause");
    holdDelete = true;
    await click("删除录音");
    await click("删除");
    expect(pause).toHaveBeenCalledOnce();
    expect(inbox.state.busy).toBe(true);
    expect(inbox.state.items).toHaveLength(3);
    await act(async () => releaseDelete?.());
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(deleteRequests()[0].body).toEqual({ ids: [ids[1]] });
    expect(inbox.state.selectedId).toBe(ids[2]);
    expect(inbox.state.counts.all).toBe(2);
    expect(inbox.state.today?.items?.map((item) => item.id)).toEqual([ids[0], ids[2]]);
    expect(document.activeElement?.getAttribute("data-id")).toBe(ids[2]);
  });

  it("删除列表末条后选上一条", async () => {
    await mount();
    await act(async () => inbox.select(ids[2]));
    await click("删除录音");
    await confirmDelete();
    expect(inbox.state.selectedId).toBe(ids[1]);
  });

  it("删除全部后清空详情、勾选和播放器，并把焦点移到列表标题", async () => {
    await mount();
    await click("选择");
    await check("全选当前结果");
    await click("删除所选（3）");
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("3 条录音");
    await confirmDelete();
    expect(inbox.state.selectedId).toBe("");
    expect(inbox.state.detail).toBeNull();
    expect(inbox.state.checkedIds).toEqual([]);
    expect(document.querySelector("audio")).toBeNull();
    expect(document.activeElement?.id).toBe("recordings-list-heading");
  });

  it("逐项结果仅移除成功项，保留失败选择并允许重试删除", async () => {
    await mount();
    await click("选择");
    await check("选择 录音 1");
    await check("选择 录音 2");
    failedIds = [ids[1]];
    await click("删除所选（2）");
    await confirmDelete();
    expect(inbox.state.checkedIds).toEqual([ids[1]]);
    expect(inbox.state.items.some((item) => item.id === ids[0])).toBe(false);
    expect(document.body.textContent).toContain("笔记暂时无法写入");
    expect(button("删除所选（1）").disabled).toBe(false);
    failedIds = [];
    await click("删除所选（1）");
    await confirmDelete();
    expect(inbox.state.checkedIds).toEqual([]);
  });

  it("删除部分失败且页面刷新失败时立即隐藏待删文本、播放和旧摘要", async () => {
    await mount();
    await act(async () => inbox.setView("today"));
    await act(async () => inbox.setView("recordings"));
    const originalFetch = vi.mocked(fetch).getMockImplementation()!;
    let refreshFails = false;
    vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
      if (refreshFails && (!options?.method || options.method === "GET")) throw new Error("刷新暂时失败");
      const response = await originalFetch(path, options);
      if (path === "/api/recordings/delete") {
        refreshFails = true;
        return json({ ...await response.json(), digest_days: [] });
      }
      return response;
    }));
    failedIds = [ids[0]];
    await click("删除录音");
    await confirmDelete();
    expect(inbox.state.detail?.deletion_pending).toBe(true);
    expect(inbox.state.detail?.text).toBe("");
    expect(document.querySelector("audio")).toBeNull();
    expect(inbox.state.today?.items?.some((item) => item.id === ids[0])).toBe(false);
    expect(inbox.state.today?.digest?.md).toBe("");
    expect(button("重试删除").disabled).toBe(false);
  });

  it("删除当天最后一条且刷新失败时，即使无需重建也立即移除旧摘要", async () => {
    records = [records[0]];
    await mount();
    await act(async () => inbox.setView("today"));
    await act(async () => inbox.setView("recordings"));
    const originalFetch = vi.mocked(fetch).getMockImplementation()!;
    let refreshFails = false;
    vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
      if (refreshFails && (!options?.method || options.method === "GET")) throw new Error("刷新暂时失败");
      const response = await originalFetch(path, options);
      if (path === "/api/recordings/delete") {
        refreshFails = true;
        return json({ ...await response.json(), digest_days: [] });
      }
      return response;
    }));
    await click("删除录音");
    await confirmDelete();
    expect(inbox.state.today?.items).toEqual([]);
    expect(inbox.state.today?.digest).toEqual({ state: "none", md: "" });
    await act(async () => inbox.setView("today"));
    expect(document.body.textContent).not.toContain("旧的摘要内容");
  });

  it("删除前发出的详情请求不会覆盖删除失败状态", async () => {
    await mount();
    const originalFetch = vi.mocked(fetch).getMockImplementation()!;
    let release: (() => void) | undefined;
    let held = false;
    const stale = { ...records[0] };
    vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
      if (path === `/api/recordings/${ids[0]}` && !held) {
        held = true;
        await new Promise<void>((resolve) => { release = resolve; });
        return json({ item: stale });
      }
      return originalFetch(path, options);
    }));
    let reading: Promise<void> | undefined;
    await act(async () => { reading = inbox.select(ids[0], { keep: true }); });
    failedIds = [ids[0]];
    await click("删除录音");
    await confirmDelete();
    await act(async () => { release?.(); await reading; });
    expect(inbox.state.detail?.deletion_pending).toBe(true);
    expect(document.querySelector("audio")).toBeNull();
  });

  it.each([400, 403, 409])("接口 %s 失败时保留列表和勾选并显示错误", async (status) => {
    await mount();
    await click("选择");
    await check("选择 录音 1");
    deleteStatus = status;
    await click("删除所选（1）");
    await confirmDelete();
    expect(inbox.state.checkedIds).toEqual([ids[0]]);
    expect(inbox.state.items).toHaveLength(3);
    expect(inbox.state.notice).toBe("另一轮正在进行，这次没有删除。");
  });

  it("删除未完成条目仅提供重试删除，不允许播放、重转或总结", async () => {
    records[0] = { ...records[0], deletion_pending: true, group: "failed", status_label: "删除未完成", reason: "笔记暂时无法写入", analysis: { llm: true } };
    await mount();
    await act(async () => inbox.select(ids[0]));
    expect(button("重试删除").disabled).toBe(false);
    expect(document.querySelector("audio")).toBeNull();
    expect(document.querySelector(".dtabs")).toBeNull();
    expect(document.body.textContent).not.toContain("重试这条");
    expect(document.body.textContent).not.toContain("重新生成总结");
    await act(async () => {
      await inbox.retryOne(ids[0]);
      await inbox.summarize(ids[0], "auto");
      await inbox.retranscribe(ids[0], "", "");
    });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(requests.some((request) => /\/(retry|summarize|retranscribe)$/.test(request.path))).toBe(false);
  });
});

describe("删除后的当日摘要", () => {
  it.each(["pending", "failed"] as const)("%s 显示状态和重试入口，隐藏过期摘要", async (state) => {
    digest = { state, md: "不该再展示的旧摘要", error: state === "failed" ? "本机模型没有响应" : undefined };
    await mount();
    await act(async () => inbox.setView("today"));
    expect(button("重试生成").disabled).toBe(false);
    expect(document.body.textContent).not.toContain("不该再展示的旧摘要");
    expect(document.body.textContent).toContain(state === "pending" ? "摘要等待重新生成" : "本机模型没有响应");
  });

  it("今天页轮询取得后台重新生成的摘要", async () => {
    digest = { state: "pending" };
    await mount();
    await act(async () => inbox.setView("today"));
    digest = { state: "ready", md: "已经更新的摘要" };
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(document.body.textContent).toContain("已经更新的摘要");
  });
});
