import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { nextTabbableIndex } from "../src/focus-trap.js";
import { InfoDrawerShell } from "../src/info-drawer.js";

describe("info drawer", () => {
  it("打开时是模态对话框，关闭时 inert，停靠时两者都没有", () => {
    const open = renderToStaticMarkup(
      createElement(InfoDrawerShell, { layout: "drawer", open: true, onClose: () => undefined, title: "信息" }, "正文"),
    );
    const closed = renderToStaticMarkup(
      createElement(InfoDrawerShell, { layout: "drawer", open: false, onClose: () => undefined, title: "信息" }, "正文"),
    );
    const dock = renderToStaticMarkup(
      createElement(InfoDrawerShell, { layout: "dock", open: false, onClose: () => undefined, title: "信息" }, "正文"),
    );
    expect(open).toContain('role="dialog"');
    expect(open).toContain('aria-modal="true"');
    expect(open).not.toContain("inert");
    expect(open).toContain("关闭");
    expect(open).toContain("panel-close");
    expect(closed).toContain("inert");
    expect(closed).not.toContain('role="dialog"');
    expect(closed).not.toContain("aria-modal");
    expect(dock).not.toContain("inert");
    expect(dock).not.toContain('role="dialog"');
  });

  it("Tab 和 Shift+Tab 在抽屉里循环", () => {
    expect(nextTabbableIndex(3, 0, false)).toBe(1);
    expect(nextTabbableIndex(3, 2, false)).toBe(0);
    expect(nextTabbableIndex(3, 0, true)).toBe(2);
    expect(nextTabbableIndex(3, 1, true)).toBe(0);
    expect(nextTabbableIndex(3, -1, false)).toBe(0);
    expect(nextTabbableIndex(3, -1, true)).toBe(2);
    expect(nextTabbableIndex(1, 0, false)).toBe(0);
    expect(nextTabbableIndex(0, 0, false)).toBe(-1);
  });
});

describe("drawer close button", () => {
  it("关闭按钮在抽屉里垂直居中", () => {
    const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
    expect(css).toMatch(/button\.panel-close\s*\{[^}]*align-items:\s*center[^}]*justify-content:\s*center/);
    expect(css).toMatch(/\.panel-toggle,\s*\.panel-close\s*\{[^}]*display:\s*inline-flex[^}]*align-items:\s*center/);
  });
});

describe("native alerts", () => {
  it("录音和信息栏不再用 window.alert", () => {
    const recordings = readFileSync(new URL("../src/views/Recordings.tsx", import.meta.url), "utf8");
    const info = readFileSync(new URL("../src/views/InfoPanel.tsx", import.meta.url), "utf8");
    expect(recordings).not.toContain("window.alert");
    expect(info).not.toContain("window.alert");
    expect(readFileSync(new URL("../src/inbox.tsx", import.meta.url), "utf8")).toContain("todoSaveError");
  });
});
