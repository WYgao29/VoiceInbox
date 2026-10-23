import type { BackendDecision, Probe } from "./types.js";

export const DEFAULT_PORT = 8791;
export const FALLBACK_PORTS = [8793, 8794, 8797, 8798];
export const BLOCKED_PORTS = new Set([8000, 8765]);

function usable(port: number): boolean {
  return Number.isInteger(port) && port >= 1 && port <= 65535 && !BLOCKED_PORTS.has(port);
}

function findProbe(probes: Probe[], port: number): Probe | undefined {
  return probes.find((probe) => probe.port === port);
}

export function decideBackend(input: {
  requestedPort: number | null;
  probes: Probe[];
  fallbackPorts?: number[];
}): BackendDecision {
  const requested = input.requestedPort;
  if (requested != null) {
    if (!usable(requested)) {
      return { action: "fail", port: requested, owned: false, reason: "这个端口不能用来启动后端" };
    }
    const probe = findProbe(input.probes, requested);
    if (probe?.reachable && probe.voiceinbox) {
      return {
        action: "reuse",
        port: requested,
        owned: false,
        reason: "指定端口上已有 voiceinbox，退出时不关闭",
      };
    }
    if (probe?.reachable !== false) {
      return { action: "fail", port: requested, owned: false, reason: "指定端口被其他程序占用或无法确认空闲" };
    }
    return { action: "spawn", port: requested, owned: true, reason: "在指定端口启动临时后端" };
  }

  const main = findProbe(input.probes, DEFAULT_PORT);
  if (main?.reachable && main.voiceinbox) {
    return {
      action: "reuse",
      port: DEFAULT_PORT,
      owned: false,
      reason: "复用 8791 上已有的 voiceinbox，退出时不关闭",
    };
  }
  if (!main) {
    return { action: "fail", port: DEFAULT_PORT, owned: false, reason: "尚未确认 8791 是否空闲" };
  }
  if (main.reachable === false) {
    return { action: "spawn", port: DEFAULT_PORT, owned: true, reason: "8791 空闲，由本程序启动后端" };
  }
  const fallbacks = input.fallbackPorts ?? FALLBACK_PORTS;
  const free = fallbacks.find((port) => usable(port) && findProbe(input.probes, port)?.reachable === false);
  if (free == null) {
    return { action: "fail", port: DEFAULT_PORT, owned: false, reason: "8791 被占用，且没有空闲备用端口" };
  }
  return { action: "spawn", port: free, owned: true, reason: "8791 不是 voiceinbox，改用备用端口" };
}
