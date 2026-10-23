import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import { spawn, type ChildProcess } from "node:child_process";
import { createWriteStream } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { decideBackend, DEFAULT_PORT, FALLBACK_PORTS } from "../shared/backend-plan.js";
import { choosePython, type PythonChoice } from "./python.js";
import { probeVoiceInbox } from "./probe.js";
import { isExplainWindowSender } from "./quit.js";
import { missingRootDialog, missingRootHtml, resolveProjectRoot, type MissingRootCopy } from "./roots.js";
import { startUiServer } from "./server.js";
import { capturePlan, captureViews } from "./shots.js";

app.setName("VoiceInbox");

const here = dirname(fileURLToPath(import.meta.url));
const dev = process.argv.includes("--dev");

function readArg(name: string): string | null {
  const prefix = `${name}=`;
  const hit = process.argv.find((arg) => arg.startsWith(prefix));
  if (hit) return hit.slice(prefix.length);
  const index = process.argv.indexOf(name);
  if (index >= 0 && process.argv[index + 1] && !process.argv[index + 1].startsWith("--")) return process.argv[index + 1];
  return null;
}

const requestedPortText = readArg("--backend-port") || process.env.VOICEINBOX_BACKEND_PORT || "";
const requestedPort = requestedPortText ? Number(requestedPortText) : null;
const screenshotDir = readArg("--screenshot-dir") || process.env.VOICEINBOX_SCREENSHOT_DIR || "";
const screenshotPlan = readArg("--screenshot-plan") || process.env.VOICEINBOX_SCREENSHOT_PLAN || "";

let window: BrowserWindow | null = null;
let explainWindow: BrowserWindow | null = null;
let ownedPid: number | null = null;
let ownedChild: ChildProcess | null = null;
let closeUi: (() => Promise<void>) | null = null;
let backendInfo = {
  port: 0,
  owned: false,
  reason: "",
  python: "",
  pythonSource: "" as "" | PythonChoice["source"],
  pythonExecutable: false,
};
let quitting = false;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function stopOwned(): void {
  const pid = ownedPid;
  ownedPid = null;
  const child = ownedChild;
  ownedChild = null;
  if (!pid) return;
  try {
    process.kill(pid, "SIGTERM");
  } catch {
    return;
  }
  const timer = setTimeout(() => {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      /* already gone */
    }
  }, 1200);
  timer.unref?.();
  child?.once("exit", () => clearTimeout(timer));
}

async function waitUntilHealthy(port: number, child: ChildProcess): Promise<boolean> {
  const started = Date.now();
  let exited = false;
  child.once("exit", () => {
    exited = true;
  });
  child.once("error", () => {
    exited = true;
  });
  while (Date.now() - started < 20000) {
    if (exited) return false;
    const probe = await probeVoiceInbox(port, 800);
    if (probe.voiceinbox && probe.token) return true;
    await sleep(250);
  }
  return false;
}

function spawnBackend(root: string, port: number, logPath: string, python: string): ChildProcess {
  const log = createWriteStream(logPath, { flags: "a" });
  log.write(`\n[${new Date().toISOString()}] spawn ${python} port ${port}\n`);
  const child = spawn(python, ["-m", "voiceinbox", "web", "--port", String(port)], {
    cwd: root,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.once("error", (error) => {
    log.write(`spawn error ${error.message}\n`);
  });
  child.stdout?.pipe(log);
  child.stderr?.pipe(log);
  return child;
}

function createWindow(uiPort: number): BrowserWindow {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 720,
    title: "VoiceInbox",
    backgroundColor: "#ffffff",
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 16, y: 16 },
    webPreferences: {
      preload: join(here, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.webContents.setBackgroundThrottling(false);
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("obsidian://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(`http://127.0.0.1:${uiPort}`)) event.preventDefault();
  });
  win.on("closed", () => {
    window = null;
  });
  void win.loadURL(`http://127.0.0.1:${uiPort}/`);
  return win;
}

async function showMissingRoot(info: MissingRootCopy): Promise<void> {
  console.error(`${info.title}\n${info.message}\n${info.detail}`);
  const htmlPath = join(app.getPath("temp"), "voiceinbox-missing-root.html");
  await writeFile(htmlPath, missingRootHtml(info), "utf8");
  const win = new BrowserWindow({
    width: 720,
    height: 680,
    minWidth: 520,
    minHeight: 420,
    title: info.title,
    show: true,
    autoHideMenuBar: true,
    backgroundColor: "#f6f4ef",
    webPreferences: {
      preload: join(here, "missing-root-preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.setMenuBarVisibility(false);
  explainWindow = win;
  window = win;
  win.on("closed", () => {
    if (window === win) window = null;
    if (explainWindow === win) explainWindow = null;
  });
  try {
    await win.loadFile(htmlPath);
  } catch (error) {
    dialog.showErrorBox(info.message, `${info.detail}\n\n${error instanceof Error ? error.message : ""}`.trim());
    app.quit();
    return;
  }
  if (!screenshotDir) return;
  await sleep(200);
  await mkdir(screenshotDir, { recursive: true });
  const file = join(screenshotDir, "missing-root.png");
  await writeFile(file, (await win.webContents.capturePage()).toPNG());
  console.log(file);
  app.quit();
}

async function boot(): Promise<void> {
  const looked = resolveProjectRoot(process.env.VOICEINBOX_ROOT || "", [
    app.getAppPath(),
    process.cwd(),
    dirname(process.execPath),
  ]);
  if (!looked.root) {
    const kind = looked.kind === "bad-env" ? "bad-env" : "missing";
    await showMissingRoot(missingRootDialog(kind, looked.envRoot));
    return;
  }
  const root = looked.root;
  const choice = choosePython(root);
  const pythonInfo = {
    python: choice.path,
    pythonSource: choice.source,
    pythonExecutable: choice.executable,
  };
  const probes = [];
  for (const port of requestedPort != null ? [requestedPort] : [DEFAULT_PORT]) probes.push(await probeVoiceInbox(port));
  if (requestedPort == null && probes[0]?.reachable && !probes[0]?.voiceinbox) {
    probes.push(...await Promise.all(FALLBACK_PORTS.map((port) => probeVoiceInbox(port))));
  }
  const decision = decideBackend({ requestedPort, probes });
  if (decision.action === "fail") {
    dialog.showErrorBox("后端没有起来", decision.reason);
    app.quit();
    return;
  }
  let token = probes.find((probe) => probe.port === decision.port)?.token || "";
  if (decision.action === "spawn" && !choice.executable) {
    const detail =
      choice.source === "launcher"
        ? `${choice.path} 存在但不可执行。本应用不会改用 .venv/bin/python，也不会重新生成、签名或修改这个文件。`
        : `没有可执行的 bin/voiceinbox-python，也没有可执行的 ${choice.path}。`;
    dialog.showErrorBox("没有可用的 Python", detail);
    app.quit();
    return;
  }
  if (decision.action === "spawn") {
    const logDir = app.getPath("userData");
    await mkdir(logDir, { recursive: true });
    const child = spawnBackend(root, decision.port, join(logDir, "backend.log"), choice.path);
    ownedChild = child;
    ownedPid = child.pid ?? null;
    const healthy = await waitUntilHealthy(decision.port, child);
    if (!healthy) {
      const again = await probeVoiceInbox(decision.port);
      stopOwned();
      if (again.voiceinbox && again.token) {
        token = again.token;
        backendInfo = { port: decision.port, owned: false, reason: "端口上已经有 voiceinbox，退出时不关闭", ...pythonInfo };
      } else {
        dialog.showErrorBox(
          "后端没有起来",
          `没能用 ${choice.path} 在 127.0.0.1:${decision.port} 启动 voiceinbox。日志在「${join(logDir, "backend.log")}」。`,
        );
        app.quit();
        return;
      }
    } else {
      const ready = await probeVoiceInbox(decision.port);
      token = ready.token;
      backendInfo = { port: decision.port, owned: true, reason: decision.reason, ...pythonInfo };
    }
  } else {
    if (!token) token = (await probeVoiceInbox(decision.port)).token;
    backendInfo = { port: decision.port, owned: false, reason: decision.reason, ...pythonInfo };
  }
  if (!token) {
    dialog.showErrorBox("后端没有起来", "没有读到 CSRF 令牌。");
    stopOwned();
    app.quit();
    return;
  }
  const ui = await startUiServer({
    backendPort: backendInfo.port,
    token,
    mode: dev ? "vite" : "static",
    staticRoot: join(here, "../../dist"),
    vitePort: 5173,
    refreshToken: async () => (await probeVoiceInbox(backendInfo.port)).token || token,
  });
  closeUi = ui.close;
  window = createWindow(ui.port);
  if (screenshotDir) {
    window.webContents.once("did-finish-load", () => {
      void (async () => {
        try {
          await sleep(400);
          const files = screenshotPlan
            ? await capturePlan(window!, screenshotDir, screenshotPlan)
            : await captureViews(window!, screenshotDir);
          console.log(files.join("\n"));
        } catch (error) {
          console.error(error);
        } finally {
          app.quit();
        }
      })();
    });
  }
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!window) return;
    if (window.isMinimized()) window.restore();
    window.focus();
  });
  ipcMain.handle("voiceinbox:backend", () => backendInfo);
  ipcMain.on("voiceinbox:quit", (event) => {
    const sender = explainWindow && !explainWindow.isDestroyed() ? explainWindow.webContents : null;
    if (!isExplainWindowSender(event.sender, sender)) return;
    app.quit();
  });
  ipcMain.handle("voiceinbox:open-external", (_event, url: unknown) => {
    if (typeof url === "string" && url.startsWith("obsidian://")) void shell.openExternal(url);
  });
  app.on("before-quit", () => {
    if (quitting) return;
    quitting = true;
    stopOwned();
    void closeUi?.();
  });
  app.on("window-all-closed", () => app.quit());
  app.whenReady().then(boot).catch((error: unknown) => {
    dialog.showErrorBox("VoiceInbox 没有启动", error instanceof Error ? error.message : "未知错误");
    stopOwned();
    app.quit();
  });
}
