/** 只有说明窗口自己的 webContents 可以要求退出。别的对象、空窗口一律拒绝。 */
export function isExplainWindowSender(sender: unknown, explainSender: unknown): boolean {
  return explainSender != null && sender === explainSender;
}
