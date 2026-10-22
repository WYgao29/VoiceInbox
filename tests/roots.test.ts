import { chmodSync, existsSync, mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { findProjectRoot, isProjectRoot, missingRootDialog, missingRootHtml, resolveProjectRoot } from "../electron/roots.js";

const made: string[] = [];

function makeRoot(): string {
  const dir = mkdtempSync(join(tmpdir(), "voiceinbox-root-"));
  made.push(dir);
  mkdirSync(join(dir, "voiceinbox"), { recursive: true });
  writeFileSync(join(dir, "voiceinbox", "web.py"), "# test\n");
  mkdirSync(join(dir, ".venv", "bin"), { recursive: true });
  const python = join(dir, ".venv", "bin", "python");
  writeFileSync(python, "#!/bin/sh\n");
  chmodSync(python, 0o755);
  return dir;
}

afterEach(() => {
  while (made.length) {
    const dir = made.pop();
    if (dir) rmSync(dir, { recursive: true, force: true });
  }
});

describe("resolveProjectRoot", () => {
  it("uses a valid VOICEINBOX_ROOT", () => {
    const root = makeRoot();
    const looked = resolveProjectRoot(join(root, "release"), []);
    expect(looked).toMatchObject({ root, kind: "ok" });
  });

  it("does not search elsewhere when VOICEINBOX_ROOT is unusable", () => {
    const root = makeRoot();
    const looked = resolveProjectRoot("/tmp/voiceinbox-not-a-repo", [root]);
    expect(looked.root).toBeNull();
    expect(looked.kind).toBe("bad-env");
    expect(looked.envRoot).toBe("/tmp/voiceinbox-not-a-repo");
    expect(isProjectRoot(root)).toBe(true);
  });

  it("walks fallbacks only when the variable is unset", () => {
    const root = makeRoot();
    expect(resolveProjectRoot("  ", [join(root, "release")]).root).toBe(root);
    expect(findProjectRoot(["/tmp/voiceinbox-not-a-repo"])).toBeNull();
  });

  it("treats a dangling symlink as a project root", () => {
    const root = makeRoot();
    rmSync(join(root, ".venv"), { recursive: true, force: true });
    const launcher = join(root, "bin", "voiceinbox-python");
    mkdirSync(join(root, "bin"));
    symlinkSync("missing-python-target", launcher);
    expect(launcher.startsWith(root)).toBe(true);
    expect(root.startsWith(tmpdir())).toBe(true);
    expect(existsSync(launcher)).toBe(false);
    expect(isProjectRoot(root)).toBe(true);
    expect(findProjectRoot([join(root, "release")])).toBe(root);
    expect(resolveProjectRoot(root, ["/tmp/voiceinbox-not-a-repo"]).kind).toBe("ok");

    const webOnly = makeRoot();
    rmSync(join(webOnly, "voiceinbox", "web.py"));
    symlinkSync("missing-web", join(webOnly, "voiceinbox", "web.py"));
    expect(existsSync(join(webOnly, "voiceinbox", "web.py"))).toBe(false);
    expect(isProjectRoot(webOnly)).toBe(true);
  });

  it("counts a path that cannot be stated, and only ENOENT as missing", () => {
    const webBlocked = makeRoot();
    rmSync(join(webBlocked, "voiceinbox"), { recursive: true, force: true });
    writeFileSync(join(webBlocked, "voiceinbox"), "not a directory\n");
    expect(isProjectRoot(webBlocked)).toBe(true);

    const pythonBlocked = makeRoot();
    rmSync(join(pythonBlocked, ".venv"), { recursive: true, force: true });
    writeFileSync(join(pythonBlocked, "bin"), "not a directory\n");
    expect(isProjectRoot(pythonBlocked)).toBe(true);

    const missing = makeRoot();
    rmSync(join(missing, "voiceinbox", "web.py"));
    expect(isProjectRoot(missing)).toBe(false);
  });

  it("still rejects a directory with no web.py entry", () => {
    const root = makeRoot();
    rmSync(join(root, "voiceinbox", "web.py"));
    mkdirSync(join(root, "bin"));
    symlinkSync("missing-python-target", join(root, "bin", "voiceinbox-python"));
    expect(isProjectRoot(root)).toBe(false);
  });
});

describe("missing root dialog", () => {
  it("explains the missing backend and both ways to fix it", () => {
    const info = missingRootDialog("missing", "");
    const text = `${info.title}\n${info.message}\n${info.detail}`;
    expect(text).toContain("未捆绑后端");
    expect(text).toContain("release/");
    expect(text).toContain("VOICEINBOX_ROOT");
    expect(text).toContain("config.toml");
  });

  it("names the bad VOICEINBOX_ROOT and escapes it in the page", () => {
    const info = missingRootDialog("bad-env", "/tmp/<nope>");
    expect(info.detail).toContain("/tmp/<nope>");
    expect(info.detail).toContain("未捆绑后端");
    const html = missingRootHtml(info);
    expect(html).toContain("/tmp/&lt;nope&gt;");
    expect(html).not.toContain("<nope>");
    expect(html).toContain('id="quit"');
  });
});
