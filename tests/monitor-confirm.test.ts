import { describe, expect, it, vi } from "vitest";
import { MONITOR_WRITE_WARNING, performMonitorWrite, type MonitorWrite } from "../src/monitor-confirm.js";

const ACTIONS: MonitorWrite[] = ["enable", "pause", "interval", "watch", "run"];

describe("monitor writes", () => {
  it("取消确认时不发请求", async () => {
    for (const action of ACTIONS) {
      const send = vi.fn(async () => "sent");
      const confirm = vi.fn(() => false);
      const result = await performMonitorWrite({
        confirm,
        action,
        detail: action === "interval" ? "15" : "on",
        send,
      });
      expect(result).toBe("cancelled");
      expect(send).not.toHaveBeenCalled();
      expect(confirm).toHaveBeenCalledOnce();
      expect(String(confirm.mock.calls[0]?.[0])).toContain(MONITOR_WRITE_WARNING);
    }
  });

  it("确认之后才发送一次", async () => {
    const send = vi.fn(async () => "sent");
    const result = await performMonitorWrite({
      confirm: () => true,
      action: "run",
      send,
    });
    expect(result).toBe("sent");
    expect(send).toHaveBeenCalledOnce();
  });
});
