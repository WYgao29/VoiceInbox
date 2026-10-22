import { existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { iconComposerManifest, writeIconComposer } from "../scripts/icon-composer.mjs";
import { iconLookupPath, packagerArgs, packagerIconPath } from "../scripts/package-mac.mjs";

describe("packager icon path", () => {
  it("去掉扩展名，让 .icns 和 .icon 都从 build/icon 解析", () => {
    expect(packagerIconPath("build/icon.icns")).toBe("build/icon");
    expect(packagerIconPath("build/icon.icon")).toBe("build/icon");
    expect(packagerIconPath("build/icon")).toBe("build/icon");
    expect(iconLookupPath("build/icon", ".icns")).toBe(path.join("build", "icon.icns"));
    expect(iconLookupPath("build/icon", ".icon")).toBe(path.join("build", "icon.icon"));
    expect(iconLookupPath("build/icon.icns", ".icon")).toBe(path.join("build", "icon.icon"));
    expect(packagerArgs).toContain("--icon=build/icon");
    expect(packagerArgs.join(" ")).not.toContain("icon.icns");
    expect(packagerArgs.join(" ")).toContain("config\\.toml");
    expect(packagerArgs.join(" ")).toContain("voiceinbox");
    const script = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).scripts["package:mac"];
    expect(script).toContain("scripts/package-mac.mjs");
    expect(script).not.toContain("icon.icns");
  });

  it("原样转发打包输出，不再丢掉 Could not find icon", () => {
    const source = readFileSync(new URL("../scripts/package-mac.mjs", import.meta.url), "utf8");
    expect(source).toContain('stdio: "inherit"');
    expect(source).not.toContain("isLegacyIconWarning");
    expect(source).not.toContain("Could not find icon");
    expect(source).not.toContain("forwardStderr");
  });

  it("make-icon 写出 Icon Composer 包，两种扩展名都在", () => {
    const script = readFileSync(new URL("../scripts/make-icon.sh", import.meta.url), "utf8");
    expect(script).toContain('scripts/icon-composer.mjs" "$work/icon.svg" "$root/build/icon.icon"');
    const manifest = iconComposerManifest();
    expect(manifest.groups[0].layers[0]["image-name"]).toBe("mark.svg");
    const dir = mkdtempSync(path.join(tmpdir(), "voiceinbox-icon-"));
    try {
      const svg = path.join(dir, "mark.svg");
      writeFileSync(svg, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"></svg>');
      const stem = path.join(dir, "icon");
      writeFileSync(`${stem}.icns`, "");
      writeIconComposer(svg, `${stem}.icon`);
      const option = packagerIconPath(`${stem}.icns`);
      const icns = iconLookupPath(option, ".icns");
      const icon = iconLookupPath(option, ".icon");
      expect(existsSync(icns)).toBe(true);
      expect(statSync(icon).isDirectory()).toBe(true);
      expect(existsSync(path.join(icon, "icon.json"))).toBe(true);
      expect(existsSync(path.join(icon, "Assets", "mark.svg"))).toBe(true);
      const written = JSON.parse(readFileSync(path.join(icon, "icon.json"), "utf8"));
      expect(written.groups).toHaveLength(1);
      expect(written.groups[0].layers[0]["image-name"]).toBe("mark.svg");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
