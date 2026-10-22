import { createServer } from "node:http";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { startUiServer } from "../electron/server.js";

const closers: Array<() => Promise<void>> = [];

afterEach(async () => {
  while (closers.length) {
    const close = closers.pop();
    if (close) await close();
  }
});

function listen(server: ReturnType<typeof createServer>): Promise<number> {
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve(typeof address === "object" && address ? address.port : 0);
    });
  });
}

describe("ui proxy", () => {
  it("injects the voiceinbox csrf material and serves the shell", async () => {
    let seen: Record<string, string | string[] | undefined> = {};
    const backend = createServer((req, res) => {
      req.resume();
      seen = req.headers;
      res.setHeader("Content-Type", "application/json");
      const ok =
        req.headers.host === `127.0.0.1:${backendPort}` &&
        req.headers.origin === `http://127.0.0.1:${backendPort}` &&
        String(req.headers.referer || "").startsWith(`http://127.0.0.1:${backendPort}`) &&
        String(req.headers.cookie || "").includes("voiceinbox_csrf=tok") &&
        req.headers["x-csrf-token"] === "tok";
      res.end(JSON.stringify({ ok }));
    });
    const backendPort = await listen(backend);
    closers.push(() => new Promise((resolve) => backend.close(() => resolve())));

    const root = mkdtempSync(join(tmpdir(), "voiceinbox-ui-"));
    writeFileSync(join(root, "index.html"), "<!doctype html><title>VoiceInbox</title>");
    const ui = await startUiServer({ backendPort, token: "tok", mode: "static", staticRoot: root });
    closers.push(ui.close);

    const page = await fetch(`http://127.0.0.1:${ui.port}/`);
    expect(await page.text()).toContain("VoiceInbox");
    const api = await fetch(`http://127.0.0.1:${ui.port}/api/ping`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: `http://127.0.0.1:${ui.port}` },
      body: "{}",
    });
    expect(await api.json()).toEqual({ ok: true });
    expect(seen["x-csrf-token"]).toBe("tok");

    const escaped = await fetch(`http://127.0.0.1:${ui.port}/../../etc/passwd`);
    expect(escaped.status).toBe(200);
    expect(await escaped.text()).toContain("VoiceInbox");
  });
});
