const TOKEN = /^[A-Za-z0-9_-]+$/;

export function parseCsrf(html: string, setCookie: string | string[] | undefined): string | null {
  const meta =
    html.match(/<meta[^>]*name=["']csrf-token["'][^>]*content=["']([^"']+)["']/i) ||
    html.match(/<meta[^>]*content=["']([^"']+)["'][^>]*name=["']csrf-token["']/i);
  const fromMeta = meta?.[1] ?? "";
  const cookieHeader = Array.isArray(setCookie) ? setCookie.join("; ") : setCookie || "";
  const cookieMatch = cookieHeader.match(/(?:^|;\s*)voiceinbox_csrf=([^;]+)/);
  const fromCookie = cookieMatch?.[1]?.trim() ?? "";
  if (fromMeta && TOKEN.test(fromMeta)) return fromMeta;
  if (fromCookie && TOKEN.test(fromCookie)) return fromCookie;
  return null;
}
