import { accessSync, constants, lstatSync, statSync } from "node:fs";
import { join } from "node:path";

export type PythonSource = "launcher" | "venv";

export interface PythonChoice {
  path: string;
  source: PythonSource;
  executable: boolean;
}

export function isExecutableFile(path: string): boolean {
  try {
    if (!statSync(path).isFile()) return false;
    accessSync(path, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function isEnoent(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && (error as { code?: unknown }).code === "ENOENT";
}

/** 路径上有这一项就算在，包括指空了的软链接。只有 ENOENT 算没有。 */
function launcherEntryPresent(launcher: string): boolean {
  try {
    lstatSync(launcher);
    return true;
  } catch (error) {
    return !isEnoent(error);
  }
}

/** 只选择要调用的解释器。不创建、不签名、不修改 bin/voiceinbox-python。 */
export function choosePython(root: string): PythonChoice {
  const launcher = join(root, "bin", "voiceinbox-python");
  if (launcherEntryPresent(launcher)) {
    return { path: launcher, source: "launcher", executable: isExecutableFile(launcher) };
  }
  const venv = join(root, ".venv", "bin", "python");
  return { path: venv, source: "venv", executable: isExecutableFile(venv) };
}
