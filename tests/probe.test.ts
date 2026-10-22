import { createServer } from "node:http";
import { describe, expect, it } from "vitest";
import { probeVoiceInbox } from "../electron/probe.js";

describe("probeVoiceInbox", () => {
  it("reads the csrf cookie from /api/health", async () => {
    let path = "";
    const server = createServer((req, res) => {
      path = req.url || "";
      res.writeHead(200, {
        Server: "voiceinbox",
        "Content-Type": "application/json; charset=utf-8",
        "Set-Cookie": "voiceinbox_csrf=abc_DEF-123; Path=/; HttpOnly; SameSite=Strict",
      });
      res.end(JSON.stringify({ ok: true, name: "VoiceInbox" }));
    });
    const port = await new Promise<number>((resolve) => {
      server.listen(0, "127.0.0.1", () => {
        const address = server.address();
        resolve(typeof address === "object" && address ? address.port : 0);
      });
    });
    try {
      const result = await probeVoiceInbox(port);
      expect(path.split("?")[0]).toBe("/api/health");
      expect(result).toMatchObject({ reachable: true, voiceinbox: true, token: "abc_DEF-123" });
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
});
