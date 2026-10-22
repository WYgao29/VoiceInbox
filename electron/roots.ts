import { lstatSync } from "node:fs";
import { dirname, join } from "node:path";
import { choosePython } from "./python.js";

function isEnoent(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && (error as { code?: unknown }).code === "ENOENT";
}

/** 目录项在不在。lstat 不跟随链接，坏软链接也算在。只有 ENOENT 算没有。 */
function entryExists(filePath: string): boolean {
  try {
    lstatSync(filePath);
    return true;
  } catch (error) {
    return !isEnoent(error);
  }
}

export function isProjectRoot(dir: string): boolean {
  if (!entryExists(join(dir, "voiceinbox", "web.py"))) return false;
  return entryExists(choosePython(dir).path);
}

export function findProjectRoot(starts: string[]): string | null {
  for (const start of starts) {
    if (!start) continue;
    let dir = start;
    for (let step = 0; step < 8; step += 1) {
      if (isProjectRoot(dir)) return dir;
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }
  return null;
}

export interface RootLookup {
  root: string | null;
  kind: "ok" | "bad-env" | "missing";
  envRoot: string;
}

/** VOICEINBOX_ROOT 一旦设置，就只认它（含向上 8 层）。指错时不再改去应用所在目录找。 */
export function resolveProjectRoot(envRoot: string, fallbacks: string[]): RootLookup {
  const trimmed = envRoot.trim();
  if (trimmed) {
    const root = findProjectRoot([trimmed]);
    if (root) return { root, kind: "ok", envRoot: trimmed };
    return { root: null, kind: "bad-env", envRoot: trimmed };
  }
  const root = findProjectRoot(fallbacks);
  if (root) return { root, kind: "ok", envRoot: "" };
  return { root: null, kind: "missing", envRoot: "" };
}

export interface MissingRootCopy {
  title: string;
  message: string;
  detail: string;
}

const BUNDLE_LIMIT =
  "这个 App 未捆绑后端。打包时排除了 voiceinbox、.venv、bin 和 config.toml，安装包里没有 Python，也没有配置。";

const HOW_TO_FIX = [
  "解决办法：",
  "1. 把 VoiceInbox.app 留在仓库的 release/ 目录里（例如 release/VoiceInbox-darwin-arm64/）。应用会从所在位置向上查找仓库。",
  "2. 或者设置环境变量 VOICEINBOX_ROOT，指向仓库根目录。这个目录里要有 voiceinbox/web.py，以及 bin/voiceinbox-python 或 .venv/bin/python。",
  "",
  "例如：",
  'VOICEINBOX_ROOT="/绝对路径/仓库" open "VoiceInbox.app"',
  'VOICEINBOX_ROOT="/绝对路径/仓库" "VoiceInbox.app/Contents/MacOS/VoiceInbox"',
].join("\n");

export function missingRootDialog(kind: "bad-env" | "missing", envRoot: string): MissingRootCopy {
  if (kind === "bad-env") {
    return {
      title: "找不到 VoiceInbox 仓库",
      message: "这个 App 未捆绑后端",
      detail: [
        `当前 VOICEINBOX_ROOT：${envRoot}`,
        "这个路径不是 VoiceInbox 仓库。设置了这个环境变量之后，应用只认这里，不会再改去别的目录找。",
        "",
        BUNDLE_LIMIT,
        "",
        "解决办法：",
        "1. 把 VOICEINBOX_ROOT 改成仓库根目录（里面要有 voiceinbox/web.py，以及 bin/voiceinbox-python 或 .venv/bin/python）。",
        "2. 或者去掉这个环境变量，并把 VoiceInbox.app 留在仓库的 release/ 目录里。",
        "",
        "例如：",
        'VOICEINBOX_ROOT="/绝对路径/仓库" open "VoiceInbox.app"',
        'VOICEINBOX_ROOT="/绝对路径/仓库" "VoiceInbox.app/Contents/MacOS/VoiceInbox"',
      ].join("\n"),
    };
  }
  return {
    title: "找不到 VoiceInbox 仓库",
    message: "这个 App 未捆绑后端",
    detail: [BUNDLE_LIMIT, "从应用所在位置和当前目录向上都没有找到仓库。", "", HOW_TO_FIX].join("\n"),
  };
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function missingRootHtml(info: MissingRootCopy): string {
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>${escapeHtml(info.title)}</title>
<style>
  body { margin: 0; background: #f6f4ef; color: #1c1917; font: 15px/1.55 -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", sans-serif; }
  main { padding: 28px 32px 32px; }
  h1 { margin: 0 0 12px; font-size: 22px; line-height: 1.35; }
  pre { margin: 0; white-space: pre-wrap; font: inherit; }
  button { margin-top: 20px; height: 36px; padding: 0 16px; border: 0; border-radius: 8px; background: #1e40af; color: #fff; font: inherit; }
</style>
</head>
<body>
<main>
  <h1>${escapeHtml(info.message)}</h1>
  <pre>${escapeHtml(info.detail)}</pre>
  <button id="quit" type="button">退出</button>
</main>
</body>
</html>
`;
}
