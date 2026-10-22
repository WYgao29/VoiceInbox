export async function executeRunMonitor<T extends { message?: string }>(options: {
  busy: boolean;
  request: () => Promise<T>;
  onStart: () => void;
  onSuccess: (data: T, message: string) => void;
  onFailure: (message: string) => void;
  scheduleRefresh: () => void;
}): Promise<void> {
  if (options.busy) return;
  options.onStart();
  try {
    const data = await options.request();
    const message = data.message || "已开始检查";
    options.onSuccess(data, message);
    options.scheduleRefresh();
  } catch (error) {
    const message = error instanceof Error && error.message ? error.message : "没有开始检查";
    options.onFailure(message);
  }
}
