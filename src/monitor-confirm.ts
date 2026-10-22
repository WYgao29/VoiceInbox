export const MONITOR_JOB = "com.wayne.voiceinbox";
export const MONITOR_WRITE_WARNING = `会修改后台任务 ${MONITOR_JOB}`;

export type MonitorWrite = "enable" | "pause" | "interval" | "watch" | "run";

export function monitorConfirmText(action: MonitorWrite, detail = ""): string {
  const lead = `${MONITOR_WRITE_WARNING}。`;
  if (action === "enable") return `${lead}打开后，这台 Mac 会按间隔自动检查语音备忘录。确定打开？`;
  if (action === "pause") return `${lead}暂停后，新录音不会自动转写，直到你重新打开。确定暂停？`;
  if (action === "interval") return `${lead}检查间隔将改为 ${detail} 分钟。确定修改？`;
  if (action === "watch") return `${lead}「有新录音时立即处理」将改为${detail === "on" ? "打开" : "关闭"}。确定修改？`;
  return `${lead}这会立刻让这个后台任务检查一轮。确定现在检查？`;
}

export async function performMonitorWrite<T>(options: {
  confirm: (message: string) => boolean;
  action: MonitorWrite;
  detail?: string;
  send: () => Promise<T>;
}): Promise<T | "cancelled"> {
  const message = monitorConfirmText(options.action, options.detail ?? "");
  if (!options.confirm(message)) return "cancelled";
  return options.send();
}
