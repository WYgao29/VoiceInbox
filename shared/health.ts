export function isVoiceInboxPage(status: number, server: string | undefined, body: string): boolean {
  if (status !== 200) return false;
  if (!String(server || "").toLowerCase().includes("voiceinbox")) return false;
  return body.includes("csrf-token") || body.includes("语音备忘录") || body.includes("VoiceInbox");
}
