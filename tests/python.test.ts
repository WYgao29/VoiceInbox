import { chmodSync, existsSync, lstatSync, mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { choosePython } from "../electron/python.js";
import { isProjectRoot } from "../electron/roots.js";

const made: string[] = [];

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), "voiceinbox-py-"));
  made.push(dir);
  mkdirSync(join(dir, "voiceinbox"), { recursive: true });
  writeFileSync(join(dir, "voiceinbox", "web.py"), "# test\n");
  return dir;
}

function put(dir: string, rel: string, mode: number): string {
  const path = join(dir, rel);
  mkdirSync(join(path, ".."), { recursive: true });
  writeFileSync(path, "#!/bin/sh\n");
  chmodSync(path, mode);
  return path;
}

afterEach(() => {
  while (made.length) {
    const dir = made.pop();
    if (dir) rmSync(dir, { recursive: true, force: true });
  }
});

describe("choosePython", () => {
  it("uses bin/voiceinbox-python when that file exists and is executable", () => {
    const dir = scratch();
    const launcher = put(dir, "bin/voiceinbox-python", 0o755);
    put(dir, ".venv/bin/python", 0o755);
    expect(choosePython(dir)).toEqual({ path: launcher, source: "launcher", executable: true });
    expect(isProjectRoot(dir)).toBe(true);
  });

  it("falls back to .venv only when the launcher file is absent", () => {
    const dir = scratch();
    const venv = put(dir, ".venv/bin/python", 0o755);
    expect(choosePython(dir)).toEqual({ path: venv, source: "venv", executable: true });
  });

  it("does not fall back when the launcher exists but is not executable", () => {
    const dir = scratch();
    const launcher = put(dir, "bin/voiceinbox-python", 0o644);
    const venv = put(dir, ".venv/bin/python", 0o755);
    const choice = choosePython(dir);
    expect(choice.path).toBe(launcher);
    expect(choice.path).not.toBe(venv);
    expect(choice.executable).toBe(false);
    expect(isProjectRoot(dir)).toBe(true);
  });

  it("accepts a root that only has the launcher", () => {
    const dir = scratch();
    put(dir, "bin/voiceinbox-python", 0o755);
    expect(isProjectRoot(dir)).toBe(true);
    expect(isProjectRoot(scratch())).toBe(false);
  });

  it("keeps a dangling launcher symlink and does not use .venv", () => {
    const dir = scratch();
    expect(dir.startsWith(tmpdir())).toBe(true);
    const launcher = join(dir, "bin", "voiceinbox-python");
    mkdirSync(join(dir, "bin"));
    symlinkSync("missing-python-target", launcher);
    const venv = put(dir, ".venv/bin/python", 0o755);
    expect(launcher).not.toBe(join(process.cwd(), "bin", "voiceinbox-python"));
    expect(lstatSync(launcher).isSymbolicLink()).toBe(true);
    expect(existsSync(launcher)).toBe(false);
    const choice = choosePython(dir);
    expect(choice).toEqual({ path: launcher, source: "launcher", executable: false });
    expect(choice.path).not.toBe(venv);
  });

  it("falls back only on ENOENT, not when the launcher path cannot be stated", () => {
    const dir = scratch();
    writeFileSync(join(dir, "bin"), "not a directory\n");
    const venv = put(dir, ".venv/bin/python", 0o755);
    const choice = choosePython(dir);
    expect(choice.source).toBe("launcher");
    expect(choice.path).toBe(join(dir, "bin", "voiceinbox-python"));
    expect(choice.executable).toBe(false);
    expect(choice.path).not.toBe(venv);
  });
});
