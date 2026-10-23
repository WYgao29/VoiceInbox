import { createServer } from "node:http";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { startUiServer } from "../electron/server.js";

const closers: Array<() => Promise<void>> = [];
const temporaryRoots: string[] = [];

afterEach(async () => {
  while (closers.length) {
    const close = closers.pop();
    if (close) await close();
  }
  while (temporaryRoots.length) rmSync(temporaryRoots.pop()!, { recursive: true, force: true });
});

function listen(server: ReturnType<typeof createServer>): Promise<number> {
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve(typeof address === "object" && address ? address.port : 0);
    });
  });
}

async function proxyFixture() {
  const requests: Array<{ url: string; headers: Record<string, string | string[] | undefined> }> = [];
  const backend = createServer((req, res) => {
    req.resume();
    requests.push({ url: req.url || "", headers: req.headers });
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ ok: true }));
  });
  const backendPort = await listen(backend);
  closers.push(() => new Promise((resolve) => backend.close(() => resolve())));
  const ui = await startUiServer({ backendPort, token: "tok", mode: "static" });
  closers.push(ui.close);
  return { origin: `http://127.0.0.1:${ui.port}`, backendPort, requests };
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
    temporaryRoots.push(root);
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

  it("rejects cross-site empty POSTs before they reach the backend", async () => {
    const { origin, requests } = await proxyFixture();
    const response = await fetch(`${origin}/api/monitor/run`, {
      method: "POST",
      headers: { Origin: "http://attacker.example", "Sec-Fetch-Site": "cross-site", "Content-Type": "text/plain" },
      body: "",
    });
    expect(response.status).toBe(403);
    expect(requests).toHaveLength(0);
  });

  it("rejects cross-site multipart uploads without forwarding their contents", async () => {
    const { origin, requests } = await proxyFixture();
    const form = new FormData();
    form.append("file", new Blob(["audio"]), "clip.m4a");
    const response = await fetch(`${origin}/api/upload`, {
      method: "POST",
      headers: { Origin: "http://attacker.example", "Sec-Fetch-Site": "cross-site" },
      body: form,
    });
    expect(response.status).toBe(403);
    expect(requests).toHaveLength(0);
  });

  it.each([
    { name: "foreign Origin", headers: { Origin: "http://attacker.example" } },
    { name: "null Origin", headers: { Origin: "null" } },
    { name: "foreign Referer", headers: { Referer: "http://attacker.example/page" } },
    { name: "invalid Referer", headers: { Referer: "not-a-url" } },
    { name: "cross-site fetch metadata", headers: { "Sec-Fetch-Site": "cross-site" } },
    { name: "same-site fetch metadata", headers: { "Sec-Fetch-Site": "same-site" } },
  ])("rejects API reads with $name", async ({ headers }) => {
    const { origin, requests } = await proxyFixture();
    const response = await fetch(`${origin}/api/recordings`, { headers });
    expect(response.status).toBe(403);
    expect(requests).toHaveLength(0);
  });

  it("checks Referer and fetch metadata even when Origin is valid", async () => {
    const { origin, requests } = await proxyFixture();
    for (const extra of [
      { Referer: `${origin}0/recordings` },
      { Referer: "http://attacker.example/page" },
      { "Sec-Fetch-Site": "cross-site" },
      { "Sec-Fetch-Site": "same-site" },
    ]) {
      const response = await fetch(`${origin}/api/hotwords`, {
        method: "POST",
        headers: { Origin: origin, "Content-Type": "application/json", ...extra },
        body: "{}",
      });
      expect(response.status).toBe(403);
    }
    expect(requests).toHaveLength(0);
  });

  it("rejects writes without an Origin and requests from another local port", async () => {
    const { origin, requests } = await proxyFixture();
    const noOrigin = await fetch(`${origin}/api/monitor/run`, {
      method: "POST",
      headers: { Referer: `${origin}/settings`, "Sec-Fetch-Site": "same-origin" },
      body: "",
    });
    expect(noOrigin.status).toBe(403);
    const anotherPort = new URL(origin);
    anotherPort.port = String(Number(anotherPort.port) === 65535 ? 65534 : Number(anotherPort.port) + 1);
    const crossOrigin = await fetch(`${origin}/api/recordings`, { headers: { Origin: anotherPort.origin } });
    expect(crossOrigin.status).toBe(403);
    expect(requests).toHaveLength(0);
  });

  it("allows normal desktop reads, JSON writes, and multipart uploads", async () => {
    const { origin, backendPort, requests } = await proxyFixture();
    const get = await fetch(`${origin}/api/recordings`, {
      headers: { Referer: `${origin}/recordings`, "Sec-Fetch-Site": "same-origin" },
    });
    expect(get.status).toBe(200);
    const post = await fetch(`${origin}/api/hotwords`, {
      method: "POST",
      headers: { Origin: origin, Referer: `${origin}/vocab`, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json" },
      body: '{"text":"Qwen"}',
    });
    expect(post.status).toBe(200);
    const form = new FormData();
    form.append("file", new Blob(["audio"]), "clip.m4a");
    const upload = await fetch(`${origin}/api/upload`, {
      method: "POST",
      headers: { Origin: origin, Referer: `${origin}/recordings`, "Sec-Fetch-Site": "same-origin" },
      body: form,
    });
    expect(upload.status).toBe(200);
    expect(requests).toHaveLength(3);
    for (const { headers } of requests) {
      expect(headers.origin).toBe(`http://127.0.0.1:${backendPort}`);
      expect(headers.cookie).toBe("voiceinbox_csrf=tok");
    }
    expect(requests[0].headers["x-csrf-token"]).toBeUndefined();
    expect(requests[1].headers["x-csrf-token"]).toBe("tok");
    expect(requests[2].headers["x-csrf-token"]).toBe("tok");
  });
});
