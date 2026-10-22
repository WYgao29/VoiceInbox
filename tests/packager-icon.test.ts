import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { isLegacyIconWarning, packagerArgs, packagerIconPath } from "../scripts/package-mac.mjs";

describe("packager icon path", () => {
  it("去掉 .icns 扩展名，避免 .icon 旧提示点到 build/icon.icns", () => {
    expect(packagerIconPath("build/icon.icns")).toBe("build/icon");
    expect(packagerIconPath("build/icon.icon")).toBe("build/icon");
    expect(packagerIconPath("build/icon")).toBe("build/icon");
    expect(packagerArgs).toContain("--icon=build/icon");
    expect(packagerArgs.join(" ")).not.toContain("icon.icns");
    expect(packagerArgs.join(" ")).toContain("config\\.toml");
    expect(packagerArgs.join(" ")).toContain("voiceinbox");
    const script = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).scripts["package:mac"];
    expect(script).toContain("scripts/package-mac.mjs");
    expect(script).not.toContain("icon.icns");
  });

  it("只丢掉缺失 .icon 的旧提示，缺 .icns 仍要看见", () => {
    expect(isLegacyIconWarning('WARNING: Could not find icon "build/icon.icns" with extension ".icon", skipping this app icon format')).toBe(true);
    expect(isLegacyIconWarning('WARNING: Could not find icon "build/icon" with extension ".icon", skipping this app icon format')).toBe(true);
    expect(isLegacyIconWarning('WARNING: Could not find icon "build/icon" with extension ".icns", skipping this app icon format')).toBe(false);
    expect(isLegacyIconWarning("Code sign failed; please retry manually.")).toBe(false);
  });
});
