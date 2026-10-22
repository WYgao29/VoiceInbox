import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Icon Composer document. electron-packager looks beside the icon stem for
// this .icon bundle and compiles it with actool; a missing bundle warns
// "Could not find icon … with extension .icon".
export function iconComposerManifest() {
  return {
    fill: { solid: "extended-srgb:1.00000,1.00000,1.00000,1.00000" },
    groups: [
      {
        layers: [
          {
            "image-name": "mark.svg",
            name: "mark",
            hidden: false,
            opacity: 1,
          },
        ],
      },
    ],
    "supported-platforms": { squares: ["macOS"] },
  };
}

export function writeIconComposer(svgPath, destDir) {
  const svg = readFileSync(svgPath);
  rmSync(destDir, { recursive: true, force: true });
  mkdirSync(path.join(destDir, "Assets"), { recursive: true });
  writeFileSync(path.join(destDir, "Assets", "mark.svg"), svg);
  writeFileSync(path.join(destDir, "icon.json"), `${JSON.stringify(iconComposerManifest(), null, 2)}\n`);
}

const isDirect = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirect) {
  const [svgPath, destDir] = process.argv.slice(2);
  if (!svgPath || !destDir) {
    console.error("usage: icon-composer.mjs <svg> <dest.icon>");
    process.exit(1);
  }
  writeIconComposer(svgPath, destDir);
}
