export async function api<T>(path: string, options: { method?: string; json?: unknown } = {}): Promise<T> {
  const headers = new Headers();
  let body: string | undefined;
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  }
  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body,
    credentials: "same-origin",
  });
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message =
      payload && typeof payload === "object" && "error" in payload && payload.error
        ? String(payload.error)
        : "请求失败";
    throw new Error(message);
  }
  return payload as T;
}

export function uploadFile(
  file: File,
  onProgress: (ratio: number, phase: "upload" | "transcribe") => void,
): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable || !event.total) return;
      const ratio = Math.min(100, Math.round((event.loaded / event.total) * 100));
      onProgress(ratio, ratio >= 100 ? "transcribe" : "upload");
    };
    xhr.upload.onload = () => onProgress(100, "transcribe");
    xhr.onload = () => {
      let payload: Record<string, unknown> = {};
      try {
        const parsed = JSON.parse(xhr.responseText || "{}") as unknown;
        if (parsed && typeof parsed === "object") payload = parsed as Record<string, unknown>;
      } catch {
        payload = {};
      }
      if (xhr.status >= 400 && !payload.error && !payload.status_label) payload.error = "导入没有完成";
      resolve(payload);
    };
    xhr.onerror = () => reject(new Error("上传失败"));
    const form = new FormData();
    form.append("file", file, file.name);
    xhr.send(form);
  });
}

export function openObsidian(uri: string): void {
  if (!uri.startsWith("obsidian://")) return;
  if (window.voiceinboxDesktop) {
    void window.voiceinboxDesktop.openExternal(uri);
    return;
  }
  window.location.href = uri;
}

export async function desktopBackend(): Promise<DesktopBackend | null> {
  try {
    return (await window.voiceinboxDesktop?.backend()) ?? null;
  } catch {
    return null;
  }
}
