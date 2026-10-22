const HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-connection",
  "transfer-encoding",
  "upgrade",
  "te",
  "trailer",
]);

const REPLACED = new Set(["host", "origin", "referer", "cookie", "x-csrf-token", "sec-fetch-site"]);

export function buildUpstreamHeaders(
  incoming: Record<string, string | string[] | undefined>,
  input: { backendPort: number; token: string; method: string },
): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(incoming)) {
    if (value === undefined) continue;
    const lower = key.toLowerCase();
    if (HOP.has(lower) || REPLACED.has(lower)) continue;
    headers[key] = Array.isArray(value) ? value.join(", ") : value;
  }
  const origin = `http://127.0.0.1:${input.backendPort}`;
  headers.Host = `127.0.0.1:${input.backendPort}`;
  headers.Origin = origin;
  headers.Referer = `${origin}/`;
  headers.Cookie = `voiceinbox_csrf=${input.token}`;
  headers["Sec-Fetch-Site"] = "same-origin";
  if (input.method.toUpperCase() !== "GET" && input.method.toUpperCase() !== "HEAD") {
    headers["X-CSRF-Token"] = input.token;
  }
  return headers;
}
