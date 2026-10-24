import type { Analysis, DetailTab, Recording, Rule } from "./types.js";

export interface Piece {
  text: string;
  mark: boolean;
  title?: string;
}

export type MdBlock =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string; note: boolean }
  | { type: "list"; todo: boolean; items: { text: string; checked: boolean | null }[] }
  | { type: "code"; lang: string; text: string };

export const DETAIL_TAB_KEYS = ["summary", "todos", "mind", "text"] as const;
export const DAY_ORDER = ["today", "yesterday", "earlier"] as const;
export const DAY_LABEL: Record<string, string> = { today: "今天", yesterday: "昨天", earlier: "更早" };
export const STATUS_FILTERS = [
  ["done", "已转写"],
  ["pending", "排队中"],
  ["processing", "转写中"],
  ["failed", "失败"],
  ["skipped", "太短已跳过"],
  ["all", "全部"],
] as const;

export type KeyAction =
  | { type: "search" }
  | { type: "move"; delta: 1 | -1 }
  | { type: "tab"; name: DetailTab }
  | { type: "toggle-play" }
  | { type: "today" }
  | { type: "help" }
  | { type: "escape" };

export function highlightPieces(text: string, query: string): Piece[] {
  const value = text || "";
  const raw = (query || "").trim();
  if (!raw) return [{ text: value, mark: false }];
  const terms = Array.from(new Set([raw, ...raw.split(/\s+/)].filter(Boolean).map((term) => term.toLowerCase())));
  terms.sort((a, b) => b.length - a.length);
  const lower = value.toLowerCase();
  const pieces: Piece[] = [];
  let start = 0;
  while (start < value.length) {
    let found = -1;
    let size = 0;
    for (const term of terms) {
      const at = lower.indexOf(term, start);
      if (at >= 0 && (found < 0 || at < found || (at === found && term.length > size))) {
        found = at;
        size = term.length;
      }
    }
    if (found < 0) break;
    if (found > start) pieces.push({ text: value.slice(start, found), mark: false });
    pieces.push({ text: value.slice(found, found + size), mark: true });
    start = found + size;
  }
  if (start < value.length) pieces.push({ text: value.slice(start), mark: false });
  return pieces.length ? pieces : [{ text: value, mark: false }];
}

export function decorateText(text: string, query: string, rules: Rule[] = []): Piece[] {
  const useful = rules.filter((rule) => rule.right && text.includes(rule.right));
  if (!useful.length) return highlightPieces(text, query);
  useful.sort((a, b) => b.right.length - a.right.length);
  const pieces: Piece[] = [];
  let start = 0;
  while (start < text.length) {
    let found = -1;
    let rule: Rule | null = null;
    for (const candidate of useful) {
      const at = text.indexOf(candidate.right, start);
      if (at >= 0 && (found < 0 || at < found)) {
        found = at;
        rule = candidate;
      }
    }
    if (found < 0 || !rule) break;
    if (found > start) pieces.push(...highlightPieces(text.slice(start, found), query));
    const title = `原来是「${rule.wrong}」`;
    for (const piece of highlightPieces(rule.right, query)) pieces.push({ ...piece, title });
    start = found + rule.right.length;
  }
  if (start < text.length) pieces.push(...highlightPieces(text.slice(start), query));
  return pieces;
}

export function parseMarkdown(source: string, skip: string[] = []): MdBlock[] {
  const lines = String(source || "").split("\n");
  const blocks: MdBlock[] = [];
  let list: Extract<MdBlock, { type: "list" }> | null = null;
  let code: { lang: string; lines: string[] } | null = null;
  let skipping = false;
  const flush = () => {
    list = null;
  };
  for (const line of lines) {
    if (code) {
      if (line.startsWith("```")) {
        blocks.push({ type: "code", lang: code.lang, text: code.lines.join("\n") });
        code = null;
      } else code.lines.push(line);
      continue;
    }
    const heading = /^(#{2,4})\s+(.*)$/.exec(line);
    if (heading) skipping = skip.includes(heading[2].trim());
    if (skipping) continue;
    if (line.startsWith("```")) {
      flush();
      code = { lang: line.slice(3).trim(), lines: [] };
      continue;
    }
    if (heading) {
      flush();
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      continue;
    }
    const todo = /^- \[( |x)\] (.*)$/.exec(line);
    const bullet = /^- (.*)$/.exec(line);
    if (todo || bullet) {
      if (!list) {
        list = { type: "list", todo: Boolean(todo), items: [] };
        blocks.push(list);
      }
      if (todo) list.items.push({ text: todo[2], checked: todo[1] === "x" });
      else if (bullet) list.items.push({ text: bullet[1], checked: null });
      continue;
    }
    flush();
    if (!line.trim()) continue;
    const italic = /^\*(.*)\*$/.exec(line);
    blocks.push({ type: "paragraph", text: italic ? italic[1] : line, note: Boolean(italic) });
  }
  if (code) blocks.push({ type: "code", lang: code.lang, text: code.lines.join("\n") });
  return blocks;
}

export function visibleDetailTabs(item: Pick<Recording, "analysis" | "has_text" | "group"> | null | undefined): DetailTab[] {
  if (!item) return [];
  const failed = item.group === "failed" || item.group === "skipped";
  if (failed && !item.has_text) return [];
  const analysis = item.analysis;
  const tabs: DetailTab[] = [];
  if (analysis) tabs.push("summary");
  const todoCount = analysis?.todos?.length ?? 0;
  if (todoCount > 0 && analysis?.route !== "flash") tabs.push("todos");
  if ((analysis?.summary?.mindmap?.children || []).length) tabs.push("mind");
  tabs.push("text");
  return tabs;
}

export function summaryTabLabel(analysis: Analysis | null | undefined): string {
  return analysis?.route === "flash" ? "闪念" : "纪要";
}

export function tabFromKey(index: number, visible: readonly string[]): DetailTab | null {
  const name = DETAIL_TAB_KEYS[index];
  if (!name || !visible.includes(name)) return null;
  return name;
}

export function moveIndex(current: number, delta: number, length: number): number {
  if (length <= 0) return -1;
  if (current < 0) return 0;
  return Math.max(0, Math.min(length - 1, current + delta));
}

export function interpretKey(
  key: string,
  mods: { meta: boolean; ctrl: boolean; alt: boolean },
  ctx: { typing: boolean; helpOpen: boolean; modalOpen?: boolean; interactive?: boolean; view: string; hasDetail: boolean; canPlay: boolean; visibleTabs: readonly string[] },
): KeyAction | null {
  if (mods.meta || mods.ctrl || mods.alt || ctx.modalOpen || ctx.helpOpen) return null;
  if (key === "Escape") return { type: "escape" };
  if (ctx.typing || (ctx.interactive && key === " ")) return null;
  if (key === "/") return { type: "search" };
  if ((key === "j" || key === "k") && ctx.view === "recordings") return { type: "move", delta: key === "j" ? 1 : -1 };
  if ((key === "1" || key === "2" || key === "3" || key === "4") && ctx.view === "recordings" && ctx.hasDetail) {
    const name = tabFromKey(Number(key) - 1, ctx.visibleTabs);
    return name ? { type: "tab", name } : null;
  }
  if (key === " " && ctx.view === "recordings" && ctx.canPlay) return { type: "toggle-play" };
  if (key === "t") return { type: "today" };
  if (key === "?") return { type: "help" };
  return null;
}

export function clock(seconds: number): string {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function timeOnly(label: string): string {
  const match = String(label || "").match(/[ T](\d{2}):(\d{2})/);
  return match ? `${match[1]}:${match[2]}` : "";
}

export function shortWhen(label: string): string {
  const match = String(label || "").match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!match) return label || "";
  return `${Number(match[2])}月${Number(match[3])}日 ${match[4]}:${match[5]}`;
}

export function weekdayLabel(iso: string, isToday: boolean): string {
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  const week = "日一二三四五六"[parsed.getDay()];
  return `${isToday ? "今天 · " : ""}${parsed.getMonth() + 1}月${parsed.getDate()}日 周${week}`;
}

export function fileStem(title: string): string {
  const cleaned = String(title || "转写")
    .replace(/[\\/:*?"<>|\n\r]+/g, " ")
    .trim()
    .slice(0, 40);
  return cleaned || "转写";
}

export function markdownTranscript(input: { title: string; recordedLabel: string; durationLabel: string; text: string }): string {
  const title = String(input.title || "转写").replace(/[#\n\r]/g, " ").trim() || "转写";
  return [`# ${title}`, "", `- 录音时间：${input.recordedLabel || "—"}`, `- 时长：${input.durationLabel || "—"}`, "", input.text, ""].join("\n");
}

export function recordingTitle(item: Pick<Recording, "display_title" | "title" | "id"> | null | undefined): string {
  return item?.display_title || item?.title || item?.id || "录音";
}
