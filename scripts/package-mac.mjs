import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// Packager appends .icns and .icon itself. Pass the stem so both
// build/icon.icns and build/icon.icon (from scripts/make-icon.sh) resolve.
export function packagerIconPath(iconPath) {
  const ext = path.extname(iconPath);
  if (ext === ".icns" || ext === ".icon" || ext === ".ico") return iconPath.slice(0, -ext.length);
  return iconPath;
}

export function iconLookupPath(iconOption, targetExt) {
  const ext = path.extname(iconOption);
  if (ext === targetExt) return iconOption;
  return path.join(path.dirname(iconOption), path.basename(iconOption, ext) + targetExt);
}

const ignore = String.raw`^/(\.venv|\.git|release|tests|src|electron|scripts|testdata|voiceinbox|launchd|bin|node_modules|__pycache__|\.pytest_cache|config\.toml|build)`;

const iconOption = packagerIconPath("build/icon.icns");

export const packagerArgs = [
  ".",
  "VoiceInbox",
  "--platform=darwin",
  "--arch=arm64",
  "--out=release",
  "--overwrite",
  "--no-asar",
  "--no-prune",
  `--icon=${iconOption}`,
  "--app-bundle-id=com.wayne.voiceinbox.desktop",
  `--ignore=${ignore}`,
];

function run() {
  const bin = path.join(root, "node_modules/@electron/packager/bin/electron-packager.mjs");
  const child = spawn(process.execPath, [bin, ...packagerArgs], { cwd: root, stdio: "inherit" });
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
