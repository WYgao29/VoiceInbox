import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// Packager appends .icns and .icon itself. Passing build/icon.icns makes the
// missing Icon Composer file warn about that old path:
// Could not find icon "build/icon.icns" with extension ".icon"
export function packagerIconPath(iconPath) {
  const ext = path.extname(iconPath);
  if (ext === ".icns" || ext === ".icon" || ext === ".ico") return iconPath.slice(0, -ext.length);
  return iconPath;
}

export function isLegacyIconWarning(line) {
  return line.includes("Could not find icon") && line.includes('with extension ".icon"');
}

const ignore = String.raw`^/(\.venv|\.git|release|tests|src|electron|scripts|testdata|voiceinbox|launchd|bin|node_modules|__pycache__|\.pytest_cache|config\.toml|build)`;

export const packagerArgs = [
  ".",
  "VoiceInbox",
  "--platform=darwin",
  "--arch=arm64",
  "--out=release",
  "--overwrite",
  "--no-asar",
  "--no-prune",
  `--icon=${packagerIconPath("build/icon.icns")}`,
  "--app-bundle-id=com.wayne.voiceinbox.desktop",
  `--ignore=${ignore}`,
];

function forwardStderr(chunk, pending) {
  const text = pending + chunk;
  const lines = text.split("\n");
  const rest = lines.pop() ?? "";
  for (const line of lines) {
    if (!isLegacyIconWarning(line)) process.stderr.write(`${line}\n`);
  }
  return rest;
}

function run() {
  const bin = path.join(root, "node_modules/@electron/packager/bin/electron-packager.mjs");
  const child = spawn(process.execPath, [bin, ...packagerArgs], { cwd: root, stdio: ["inherit", "inherit", "pipe"] });
  let pending = "";
  child.stderr.on("data", (chunk) => {
    pending = forwardStderr(String(chunk), pending);
  });
  child.stderr.on("end", () => {
    if (pending && !isLegacyIconWarning(pending)) process.stderr.write(pending);
  });
  child.on("error", (error) => {
    console.error(error);
    process.exit(1);
  });
  child.on("exit", (code) => {
    process.exit(code ?? 1);
  });
}

const isDirect = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirect) run();
