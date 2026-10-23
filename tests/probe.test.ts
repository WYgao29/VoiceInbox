import { createServer as createHttpServer } from "node:http";
import { createServer as createTcpServer, type Server, type Socket } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import { probeVoiceInbox } from "../electron/probe.js";
import { decideBackend } from "../shared/backend-plan.js";

const servers: Server[] = [];
const sockets = new Set<Socket>();
const timers = new Set<ReturnType<typeof setInterval>>();

async function listen(server: Server): Promise<number> {
  servers.push(server);
  server.on("connection", (socket: Socket) => {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
    socket.resume();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("没有监听端口");
  return address.port;
}

async function freePort(): Promise<number> {
  const server = createTcpServer();
  const port = await listen(server);
  await new Promise<void>((resolve) => server.close(() => resolve()));
  return port;
}

async function probe(port: number, timeout = 60) {
  let deadline: ReturnType<typeof setTimeout> | undefined;
  try {
    const result = await Promise.race([
      probeVoiceInbox(port, timeout),
      new Promise<never>((_resolve, reject) => {
        deadline = setTimeout(() => reject(new Error("探针没有在限定时间内结束")), 600);
      }),
    ]);
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(sockets.size).toBe(0);
    return result;
  } finally {
    clearTimeout(deadline);
  }
}

afterEach(async () => {
  for (const timer of timers) clearInterval(timer);
  timers.clear();
  for (const socket of sockets) socket.destroy();
  sockets.clear();
  while (servers.length) {
    const server = servers.pop();
    if (server?.listening) await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

describe("probeVoiceInbox", () => {
  it("reads the csrf cookie from /api/health", async () => {
    let path = "";
    const server = createHttpServer((req, res) => {
      path = req.url || "";
      res.writeHead(200, {
        Server: "voiceinbox",
        "Content-Type": "application/json; charset=utf-8",
        "Set-Cookie": "voiceinbox_csrf=abc_DEF-123; Path=/; HttpOnly; SameSite=Strict",
      });
      res.end(JSON.stringify({ ok: true, name: "VoiceInbox" }));
    });
    const result = await probe(await listen(server), 200);
    expect(path.split("?")[0]).toBe("/api/health");
    expect(result).toMatchObject({ reachable: true, voiceinbox: true, token: "abc_DEF-123" });
  });

  it("marks a refused connection as free", async () => {
    expect(await probe(await freePort())).toMatchObject({ reachable: false, voiceinbox: false });
  });

  it("does not mark a silent TCP listener as free", async () => {
    const server = createTcpServer((socket) => socket.on("data", () => undefined));
    expect(await probe(await listen(server))).toMatchObject({ reachable: true, voiceinbox: false });
  });

  it("does not mark invalid HTTP as free", async () => {
    const server = createTcpServer((socket) => socket.end("not an HTTP response\r\n"));
    expect(await probe(await listen(server))).toMatchObject({ reachable: true, voiceinbox: false });
  });

  it("does not mark a listener that disconnects before responding as free", async () => {
    const server = createTcpServer((socket) => socket.destroy());
    expect(await probe(await listen(server))).toMatchObject({ reachable: true, voiceinbox: false });
  });

  it("finishes when an HTTP response is interrupted", async () => {
    const server = createTcpServer((socket) => {
      socket.once("data", () => socket.end("HTTP/1.1 200 OK\r\nContent-Length: 100\r\nServer: voiceinbox\r\n\r\nVoiceInbox"));
    });
    expect(await probe(await listen(server))).toMatchObject({ reachable: true, voiceinbox: false, token: "" });
  });

  it("enforces a total deadline even if response bytes keep arriving", async () => {
    const server = createTcpServer((socket) => {
      socket.once("data", () => {
        socket.write("HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n");
        const timer = setInterval(() => socket.write("x"), 10);
        timers.add(timer);
        socket.once("close", () => {
          clearInterval(timer);
          timers.delete(timer);
        });
      });
    });
    expect(await probe(await listen(server))).toMatchObject({ reachable: true, voiceinbox: false });
  });

  it("skips an occupied fallback after a real probe and uses a free one", async () => {
    const occupiedPort = await listen(createTcpServer((socket) => socket.on("data", () => undefined)));
    const availablePort = await freePort();
    const occupied = await probe(occupiedPort);
    const available = await probe(availablePort);
    expect(decideBackend({
      requestedPort: null,
      probes: [{ port: 8791, reachable: true, voiceinbox: false }, occupied, available],
      fallbackPorts: [occupiedPort, availablePort],
    })).toMatchObject({ action: "spawn", port: availablePort });
    expect(decideBackend({ requestedPort: occupiedPort, probes: [occupied] }).action).toBe("fail");
  });
});
