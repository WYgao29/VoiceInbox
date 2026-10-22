import { get as httpGet } from "node:http";
import { parseCsrf } from "../shared/csrf.js";
import { isVoiceInboxPage } from "../shared/health.js";

export interface ProbeResult {
  port: number;
  reachable: boolean;
  voiceinbox: boolean;
  token: string;
}

export function probeVoiceInbox(port: number, timeoutMs = 1500): Promise<ProbeResult> {
  return new Promise((resolve) => {
    const req = httpGet(
      {
        hostname: "127.0.0.1",
        port,
        path: "/api/health",
        timeout: timeoutMs,
        headers: { Accept: "text/html", Host: `127.0.0.1:${port}` },
      },
      (res) => {
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
          resolve({
            port,
            reachable: true,
            voiceinbox,
            token: parseCsrf(body, res.headers["set-cookie"]) || "",
          });
        });
      },
    );
    const miss = () => resolve({ port, reachable: false, voiceinbox: false, token: "" });
    req.on("error", miss);
    req.on("timeout", () => {
      req.destroy();
      miss();
    });
  });
}
