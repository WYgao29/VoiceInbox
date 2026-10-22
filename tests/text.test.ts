import { describe, expect, it } from "vitest";
import {
  clock,
  decorateText,
  fileStem,
  highlightPieces,
  interpretKey,
  markdownTranscript,
  moveIndex,
  parseMarkdown,
  tabFromKey,
  visibleDetailTabs,
  weekdayLabel,
} from "../shared/text.js";

describe("highlight", () => {
  it("marks each search term without treating the text as html", () => {
    const pieces = highlightPieces("去 <b>Sol</b> 项目", "sol 项目");
    expect(pieces.filter((piece) => piece.mark).map((piece) => piece.text)).toEqual(["Sol", "项目"]);
    expect(pieces.map((piece) => piece.text).join("")).toBe("去 <b>Sol</b> 项目");
  });

  it("underlines a corrected word and keeps the original in the title", () => {
    const pieces = decorateText("这是 Sol 项目", "sol", [{ wrong: "扫", right: "Sol" }]);
    const fixed = pieces.filter((piece) => piece.title);
    expect(fixed.map((piece) => piece.text).join("")).toBe("Sol");
    expect(fixed[0]?.title).toBe("原来是「扫」");
    expect(fixed.some((piece) => piece.mark)).toBe(true);
  });
});

describe("markdown", () => {
  it("skips a named section and keeps lists as text", () => {
    const blocks = parseMarkdown("## 概要\n一句\n## 待办\n- [ ] 寄信\n## 其他\n- 保留", ["待办"]);
    expect(blocks.map((block) => block.type)).toEqual(["heading", "paragraph", "heading", "list"]);
    const list = blocks[3];
    expect(list && list.type === "list" && list.items[0]?.text).toBe("保留");
  });
});

describe("keyboard", () => {
  const ctx = { typing: false, helpOpen: false, view: "recordings", hasDetail: true, canPlay: true, visibleTabs: ["summary", "text"] };

  it("maps slash, j, and the visible detail tabs", () => {
    expect(interpretKey("/", { meta: false, ctrl: false, alt: false }, ctx)?.type).toBe("search");
    expect(interpretKey("j", { meta: false, ctrl: false, alt: false }, ctx)).toEqual({ type: "move", delta: 1 });
    expect(interpretKey("k", { meta: false, ctrl: false, alt: false }, ctx)).toEqual({ type: "move", delta: -1 });
    expect(interpretKey("1", { meta: false, ctrl: false, alt: false }, ctx)).toEqual({ type: "tab", name: "summary" });
    expect(interpretKey("2", { meta: false, ctrl: false, alt: false }, ctx)).toBeNull();
    expect(interpretKey("4", { meta: false, ctrl: false, alt: false }, ctx)).toEqual({ type: "tab", name: "text" });
    expect(tabFromKey(1, ["summary", "todos", "mind", "text"])).toBe("todos");
  });

  it("ignores shortcuts while typing, except escape", () => {
    expect(interpretKey("j", { meta: false, ctrl: false, alt: false }, { ...ctx, typing: true })).toBeNull();
    expect(interpretKey("Escape", { meta: false, ctrl: false, alt: false }, { ...ctx, typing: true })?.type).toBe("escape");
    expect(interpretKey("j", { meta: true, ctrl: false, alt: false }, ctx)).toBeNull();
  });

  it("moves within the list", () => {
    expect(moveIndex(-1, 1, 3)).toBe(0);
    expect(moveIndex(2, 1, 3)).toBe(2);
    expect(moveIndex(0, -1, 3)).toBe(0);
  });
});

describe("detail tabs", () => {
  it("hides todos and the mind map when they are empty, and hides everything on a failed item without text", () => {
    expect(visibleDetailTabs({ group: "done", has_text: true, analysis: { route: "full", todos: [], summary: { mindmap: { children: [] } } } })).toEqual(["summary", "text"]);
    expect(visibleDetailTabs({ group: "failed", has_text: false, analysis: null })).toEqual([]);
    expect(visibleDetailTabs({ group: "done", has_text: true, analysis: { route: "flash", todos: [{ key: "a", text: "记" }] } })).toEqual(["summary", "text"]);
  });
});

describe("export helpers", () => {
  it("builds a markdown transcript and a safe file name", () => {
    expect(fileStem('a/b:c')).toBe("a b c");
    expect(markdownTranscript({ title: "周会", recordedLabel: "10:00", durationLabel: "3:00", text: "你好" })).toContain("# 周会");
    expect(clock(65)).toBe("1:05");
    expect(weekdayLabel("2026-10-21", true)).toContain("今天");
  });
});
