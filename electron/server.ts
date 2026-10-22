import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer, request as httpRequest, type IncomingMessage, type ServerResponse } from "node:http";
import { connect } from "node:net";
import { extname, join, sep } from "node:path";
import { buildUpstreamHeaders } from "../shared/headers.js";

export interface UiServerOptions {
  backendPort: number;
  token: string;
  mode: "static" | "vite";
  staticRoot?: string;
  vitePort?: number;
  refreshToken?: () => Promise<string>;
}

const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
  ".ico": "image/x-icon",
  ".map": "application/json",
  ".json": "application/json",
};

function hostOk(req: IncomingMessage, port: number): boolean {
  const host = String(req.headers.host || "");
  return host === `127.0.0.1:${port}` || host === `localhost:${port}`;
}

function fail(res: ServerResponse, code: number, body: string): void {
  if (res.headersSent) return;
  res.writeHead(code, { "Content-Type": "text/plain; charset=utf-8" });
  res.end(body);
}

function pipeUpstream(
  req: IncomingMessage,
  res: ServerResponse,
  port: number,
  headers: Record<string, string | string[] | undefined>,
  onStatus?: (status: number) => boolean,
): void {
  const upstream = httpRequest(
    { hostname: "127.0.0.1", port, path: req.url, method: req.method, headers },
    (up) => {
      if (onStatus && onStatus(up.statusCode || 0)) {
        up.resume();
        return;
      }
      const outgoing = { ...up.headers };
      delete outgoing["set-cookie"];
      res.writeHead(up.statusCode || 502, outgoing);
      up.pipe(res);
    },
  );
  upstream.on("error", () => fail(res, 502, "后端没有响应"));
  const method = (req.method || "GET").toUpperCase();
  if (method === "GET" || method === "HEAD") upstream.end();
  else req.pipe(upstream);
  res.on("close", () => upstream.destroy());
}

function safeFile(root: string, pathname: string): string | null {
  let decoded = pathname;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  const cleaned = decoded.replace(/^\/+/, "");
  if (!cleaned || cleaned.split(/[/\\]/).includes("..")) return join(root, "index.html");
  const file = join(root, cleaned);
  const rootWithSep = root.endsWith(sep) ? root : root + sep;
  if (file !== root && !file.startsWith(rootWithSep)) return null;
  return file;
}

export function startUiServer(options: UiServerOptions): Promise<{ port: number; close: () => Promise<void> }> {
  const session = { token: options.token };
  const server = createServer((req, res) => {
    const port = req.socket.localPort || 0;
    if (!hostOk(req, port)) {
      fail(res, 403, "只接受本机地址");
      return;
    }
    const pathname = (req.url || "/").split("?")[0] || "/";
    if (pathname.startsWith("/api/")) {
      const send = (allowRetry: boolean) => {
        const headers = buildUpstreamHeaders(req.headers, {
          backendPort: options.backendPort,
          token: session.token,
          method: req.method || "GET",
        });
        pipeUpstream(req, res, options.backendPort, headers, (status) => {
          const method = (req.method || "GET").toUpperCase();
          if (allowRetry && status === 403 && options.refreshToken && (method === "GET" || method === "HEAD")) {
            void options.refreshToken().then((token) => {
              session.token = token;
              send(false);
            }).catch(() => fail(res, 502, "后端没有响应"));
            return true;
          }
          return false;
        });
      };
      send(true);
      return;
    }
    if (options.mode === "vite") {
      const headers = { ...req.headers, host: `127.0.0.1:${options.vitePort || 5173}` };
      pipeUpstream(req, res, options.vitePort || 5173, headers);
      return;
    }
    const root = options.staticRoot || "";
    const file = safeFile(root, pathname);
    if (!file) {
      fail(res, 404, "没有这个文件");
      return;
    }
    if (existsSync(file) && statSync(file).isFile()) {
      res.writeHead(200, {
        "Content-Type": TYPES[extname(file)] || "application/octet-stream",
        "Cache-Control": "no-cache",
      });
      createReadStream(file).pipe(res);
      return;
    }
    const index = join(root, "index.html");
    if (existsSync(index)) {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache" });
      createReadStream(index).pipe(res);
      return;
    }
    fail(res, 404, "没有这个文件");
  });

  server.on("upgrade", (req, socket, head) => {
    if (options.mode !== "vite") {
      socket.destroy();
      return;
    }
    const vitePort = options.vitePort || 5173;
    const upstream = connect({ host: "127.0.0.1", port: vitePort }, () => {
      const lines = [`${req.method || "GET"} ${req.url || "/"} HTTP/1.1`];
      for (const [key, value] of Object.entries(req.headers)) {
        if (value === undefined) continue;
        if (key.toLowerCase() === "host") lines.push(`Host: 127.0.0.1:${vitePort}`);
        else lines.push(`${key}: ${Array.isArray(value) ? value.join(", ") : value}`);
      }
      upstream.write(`${lines.join("\r\n")}\r\n\r\n`);
      if (head.length) upstream.write(head);
      upstream.pipe(socket);
      socket.pipe(upstream);
    });
    upstream.on("error", () => socket.destroy());
  });

  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const listenPort = typeof address === "object" && address ? address.port : 0;
      resolve({
        port: listenPort,
        close: () =>
          new Promise((done, failClose) => {
            server.close((error) => (error ? failClose(error) : done()));
          }),
      });
    });
  });
}
