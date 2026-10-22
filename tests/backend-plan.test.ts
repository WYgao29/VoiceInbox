import { describe, expect, it } from "vitest";
import { decideBackend } from "../shared/backend-plan.js";
import { isVoiceInboxPage } from "../shared/health.js";
import { parseCsrf } from "../shared/csrf.js";
import { buildUpstreamHeaders } from "../shared/headers.js";

describe("decideBackend", () => {
  it("reuses a healthy server on 8791 and does not own it", () => {
    const decision = decideBackend({
      requestedPort: null,
      probes: [{ port: 8791, reachable: true, voiceinbox: true }],
    });
    expect(decision).toMatchObject({ action: "reuse", port: 8791, owned: false });
  });

  it("spawns on 8791 when nothing is listening", () => {
    const decision = decideBackend({
      requestedPort: null,
      probes: [{ port: 8791, reachable: false, voiceinbox: false }],
    });
    expect(decision).toMatchObject({ action: "spawn", port: 8791, owned: true });
  });

  it("does not take over a foreign listener on 8791", () => {
    const decision = decideBackend({
      requestedPort: null,
      probes: [
        { port: 8791, reachable: true, voiceinbox: false },
        { port: 8793, reachable: false, voiceinbox: false },
      ],
    });
    expect(decision).toMatchObject({ action: "spawn", port: 8793, owned: true });
  });

  it("spawns a temporary backend on the requested port", () => {
    const decision = decideBackend({
      requestedPort: 8792,
      probes: [
        { port: 8791, reachable: true, voiceinbox: true },
        { port: 8792, reachable: false, voiceinbox: false },
      ],
    });
    expect(decision).toMatchObject({ action: "spawn", port: 8792, owned: true });
  });

  it("refuses to own an existing server just because the port was requested", () => {
    const decision = decideBackend({
      requestedPort: 8792,
      probes: [{ port: 8792, reachable: true, voiceinbox: true }],
    });
    expect(decision).toMatchObject({ action: "reuse", port: 8792, owned: false });
  });

  it("rejects blocked ports", () => {
    expect(decideBackend({ requestedPort: 8765, probes: [] }).action).toBe("fail");
    expect(decideBackend({ requestedPort: 8000, probes: [] }).action).toBe("fail");
  });
});

describe("health and csrf", () => {
  it("recognizes the voiceinbox page and reads the token", () => {
    const html = '<meta name="csrf-token" content="abc_DEF-123">语音备忘录';
    expect(isVoiceInboxPage(200, "voiceinbox", html)).toBe(true);
    expect(isVoiceInboxPage(200, "nginx", html)).toBe(false);
    expect(isVoiceInboxPage(200, "voiceinbox Python/3.14", '{"ok": true, "name": "VoiceInbox"}')).toBe(true);
    expect(isVoiceInboxPage(404, "voiceinbox", '{"ok": true, "name": "VoiceInbox"}')).toBe(false);
    expect(parseCsrf(html, "voiceinbox_csrf=abc_DEF-123; Path=/")).toBe("abc_DEF-123");
    expect(parseCsrf('{"ok":true}', "voiceinbox_csrf=abc_DEF-123; Path=/; HttpOnly")).toBe("abc_DEF-123");
    expect(parseCsrf("<html></html>", undefined)).toBeNull();
  });
});

describe("upstream headers", () => {
  it("rewrites origin, host, referer, and csrf for the python server", () => {
    const headers = buildUpstreamHeaders(
      {
        Host: "127.0.0.1:43123",
        Origin: "http://127.0.0.1:43123",
        Referer: "http://127.0.0.1:43123/recordings",
        Cookie: "other=1",
        "Content-Type": "application/json",
        "Sec-Fetch-Site": "same-origin",
        Connection: "close",
      },
      { backendPort: 8792, token: "tok", method: "POST" },
    );
    expect(headers.Host).toBe("127.0.0.1:8792");
    expect(headers.Origin).toBe("http://127.0.0.1:8792");
    expect(headers.Referer).toBe("http://127.0.0.1:8792/");
    expect(headers.Cookie).toBe("voiceinbox_csrf=tok");
    expect(headers["X-CSRF-Token"]).toBe("tok");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers.Connection).toBeUndefined();
  });

  it("does not send a csrf header on GET", () => {
    const headers = buildUpstreamHeaders({}, { backendPort: 8791, token: "tok", method: "GET" });
    expect(headers["X-CSRF-Token"]).toBeUndefined();
  });
});
