import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { Today } from "../src/views/Today.js";

const inbox = vi.hoisted(() => ({
  state: {
    busy: false,
    today: {
      date: "2026-10-22",
      is_today: true,
      items: [] as { id: string; title?: string; recorded_label?: string; route?: string; scene?: string }[],
      days: [{ date: "2026-10-22", count: 0 }],
      digest_ready: true,
      digest_time: "21:00",
      digest: undefined as { md?: string; path?: string; updated?: string } | undefined,
    },
  },
  setDay: vi.fn(),
  digest: vi.fn(async () => undefined),
  openRecording: vi.fn(async () => undefined),
}));

vi.mock("../src/inbox.js", () => ({
  useInbox: () => inbox,
}));

describe("today page", () => {
  it("没有录音时用 EmptyState", () => {
    inbox.state.today.items = [];
    inbox.state.today.digest = undefined;
    const html = renderToStaticMarkup(createElement(Today));
    expect(html).toContain("empty-state");
    expect(html).toContain("这天没有录音");
    expect(html).toContain("导入音频");
    expect(html).not.toContain("这天还没有当日摘要");
  });

  it("有录音但没有摘要时仍显示摘要说明", () => {
    inbox.state.today.items = [{ id: "a", title: "例会", recorded_label: "2026-10-22 09:00", route: "full", scene: "会议" }];
    inbox.state.today.digest = undefined;
    const html = renderToStaticMarkup(createElement(Today));
    expect(html).not.toContain("empty-state");
    expect(html).toContain("这天还没有当日摘要");
    expect(html).toContain("例会");
  });
});
