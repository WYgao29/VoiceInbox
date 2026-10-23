import { get as httpGet, type ClientRequest, type IncomingMessage } from "node:http";
import { parseCsrf } from "../shared/csrf.js";
import { isVoiceInboxPage } from "../shared/health.js";

export interface ProbeResult {
  port: number;
  // Only a refused connection is known to be free; unknown results remain occupied.
  reachable: boolean;
  voiceinbox: boolean;
  token: string;
}

export function probeVoiceInbox(port: number, timeoutMs = 1500): Promise<ProbeResult> {
  return new Promise((resolve) => {
    let connected = false;
    let settled = false;
    let req: ClientRequest | undefined;
    let response: IncomingMessage | undefined;
    const finish = (result: ProbeResult = { port, reachable: true, voiceinbox: false, token: "" }) => {
      if (settled) return;
      settled = true;
      clearTimeout(deadline);
      response?.destroy();
      req?.destroy();
      resolve(result);
    };
    const deadline = setTimeout(() => finish(), timeoutMs);
    req = httpGet(
      {
        hostname: "127.0.0.1",
        port,
        path: "/api/health",
        agent: false,
        headers: { Accept: "text/html", Host: `127.0.0.1:${port}` },
      },
      (res) => {
        response = res;
        const chunks: Buffer[] = [];
        let size = 0;
        res.on("data", (chunk: Buffer) => {
          if (size < 262144) {
            chunks.push(chunk);
            size += chunk.length;
          }
        });
        res.on("end", () => {
          const body = Buffer.concat(chunks).toString("utf8");
          const voiceinbox = isVoiceInboxPage(res.statusCode || 0, String(res.headers.server || ""), body);
          finish({
            port,
            reachable: true,
            voiceinbox,
            token: parseCsrf(body, res.headers["set-cookie"]) || "",
          });
        });
        res.once("aborted", () => finish());
        res.once("error", () => finish());
        res.once("close", () => {
          if (!res.complete) finish();
        });
      },
    );
    req.once("socket", (socket) => {
      if (!socket.connecting) connected = true;
      else socket.once("connect", () => { connected = true; });
    });
    req.once("error", (error: NodeJS.ErrnoException) => {
      finish({ port, reachable: connected || error.code !== "ECONNREFUSED", voiceinbox: false, token: "" });
    });
  });
}
