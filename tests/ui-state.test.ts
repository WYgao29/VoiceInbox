import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialogView } from "../src/confirm-dialog.js";
import { EmptyState } from "../src/empty-state.js";
import { LoadingSkeleton } from "../src/skeleton.js";
import {
  deleteRuleConfirm,
  importConfirm,
  monitorDialog,
  recordingsEmptyCopy,
  recordingsEmptyKind,
  retranscribeConfirm,
  runIfConfirmed,
  statusTone,
  todayEmptyCopy,
  todoSaveError,
} from "../src/ui-state.js";

describe("recordings empty state", () => {
  const base = { query: "", kind: "", scene: "", tag: "", from: "", to: "", status: "done", total: 0 };

  it("搜索、筛选、完全没有录音用不同文案", () => {
    expect(recordingsEmptyKind({ ...base, query: "没有这个词", total: 4 })).toBe("search");
    expect(recordingsEmptyKind({ ...base, status: "failed", total: 4 })).toBe("filter");
    expect(recordingsEmptyKind({ ...base, tag: "日常", total: 4 })).toBe("filter");
    expect(recordingsEmptyKind(base)).toBe("none");
    expect(recordingsEmptyKind({ ...base, status: "all", total: 0 })).toBe("none");
    expect(recordingsEmptyKind({ ...base, status: "failed", total: 0 })).toBe("none");
    expect(recordingsEmptyKind({ ...base, status: "done", total: 3 })).toBe("filter");
    expect(recordingsEmptyKind({ ...base, status: "all", total: 3 })).toBe("filter");

    const search = recordingsEmptyCopy("search");
    const filter = recordingsEmptyCopy("filter");
    const none = recordingsEmptyCopy("none");
    expect(search.title).not.toBe(filter.title);
    expect(filter.title).not.toBe(none.title);
    expect(search.action).toBe("清空搜索");
    expect(filter.action).toBe("清除筛选");
    expect(none.action).toBe("导入音频");
    expect(none.hint).toContain("语音备忘录");
  });

  it("空状态包含图标、标题、提示和操作", () => {
    const html = renderToStaticMarkup(createElement(EmptyState, { copy: recordingsEmptyCopy("search"), onAction: () => undefined }));
    expect(html).toContain("empty-state");
    expect(html).toContain("<svg");
    expect(html).toContain("没有匹配的录音");
    expect(html).toContain("换一个关键词");
    expect(html).toContain("清空搜索");
  });
});

describe("today empty state", () => {
  it("这天没有录音时给出导入", () => {
    const copy = todayEmptyCopy();
    expect(copy.title).toBe("这天没有录音");
    expect(copy.action).toBe("导入音频");
    expect(copy.icon).toBe("mic");
    const html = renderToStaticMarkup(createElement(EmptyState, { copy, onAction: () => undefined }));
    expect(html).toContain("empty-state");
    expect(html).toContain("这天没有录音");
    expect(html).toContain("导入音频");
    expect(html).not.toContain("window.alert");
  });
});

describe("todo save errors", () => {
  it("失败说明进提示，空错误用没有记下", () => {
    expect(todoSaveError(new Error("网络断了"))).toBe("网络断了");
    expect(todoSaveError(new Error(""))).toBe("没有记下");
    expect(todoSaveError("x")).toBe("没有记下");
  });
});

describe("status tone", () => {
  it("按状态和标签着色", () => {
    expect(statusTone("failed", "失败")).toBe("failed");
    expect(statusTone("processing", "转写中")).toBe("running");
    expect(statusTone("pending", "排队中")).toBe("queued");
    expect(statusTone("skipped", "太短已跳过")).toBe("waiting");
    expect(statusTone("done", "仅转写")).toBe("waiting");
    expect(statusTone("done", "总结没成功")).toBe("failed");
    expect(statusTone("done", "已转写")).toBe("done");
  });
});

describe("confirm gate", () => {
  it("取消则不发请求", async () => {
    const send = vi.fn(async () => undefined);
    const result = await runIfConfirmed(async () => false, send);
    expect(result).toBe("cancelled");
    expect(send).not.toHaveBeenCalled();
  });

  it("确认之后才发送一次", async () => {
    const send = vi.fn(async () => undefined);
    const result = await runIfConfirmed(() => true, send);
    expect(result).toBe("sent");
    expect(send).toHaveBeenCalledOnce();
  });

  it("删除、重转和后台任务用红色确认，导入不是", () => {
    expect(deleteRuleConfirm("甲", "乙")).toMatchObject({ danger: true, confirmLabel: "删除", message: "删掉「甲 → 乙」？" });
    expect(retranscribeConfirm().danger).toBe(true);
    expect(monitorDialog("会修改后台任务 com.wayne.voiceinbox。").danger).toBe(true);
    expect(importConfirm().danger).toBeUndefined();
    expect(importConfirm().message).toContain("9.6GB");
  });

  it("对话框标出危险按钮，并且默认聚焦取消", () => {
    const danger = renderToStaticMarkup(
      createElement(ConfirmDialogView, {
        request: deleteRuleConfirm("甲", "乙"),
        onCancel: () => undefined,
        onConfirm: () => undefined,
      }),
    );
    const plain = renderToStaticMarkup(
      createElement(ConfirmDialogView, {
        request: importConfirm(),
        onCancel: () => undefined,
        onConfirm: () => undefined,
      }),
    );
    expect(danger).toContain('class="danger"');
    expect(danger).toContain("删掉「甲 → 乙」？");
    expect(danger).not.toContain("window.confirm");
    for (const html of [danger, plain]) {
      expect(html).toMatch(/<button[^>]*autofocus[^>]*>取消<\/button>/);
      expect(html).not.toMatch(/<button[^>]*autofocus[^>]*>(继续|删除)<\/button>/);
    }
  });
});

describe("loading skeleton", () => {
  it("读出正在读取的骨架", () => {
    const html = renderToStaticMarkup(createElement(LoadingSkeleton));
    expect(html).toContain("skeleton-screen");
    expect(html).toContain("正在读取");
    expect(html).toContain("skeleton-card");
  });
});
