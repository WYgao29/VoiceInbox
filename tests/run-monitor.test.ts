import { describe, expect, it, vi } from "vitest";
import { executeRunMonitor } from "../src/run-monitor.js";

describe("runMonitor", () => {
  it("忙的时候不发请求", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const request = vi.fn();
    const onStart = vi.fn();
    await executeRunMonitor({
      busy: true,
      request,
      onStart,
      onSuccess: vi.fn(),
      onFailure: vi.fn(),
      scheduleRefresh: vi.fn(),
    });
    expect(request).not.toHaveBeenCalled();
    expect(onStart).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("成功时只用传入的请求，并带上提示", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const order: string[] = [];
    const request = vi.fn(async () => {
      order.push("request");
      return { message: "已开始检查这一轮", running: true };
    });
    const onSuccess = vi.fn();
    await executeRunMonitor({
      busy: false,
      request,
      onStart: () => order.push("start"),
      onSuccess,
      onFailure: vi.fn(),
      scheduleRefresh: () => order.push("refresh"),
    });
    expect(order).toEqual(["start", "request", "refresh"]);
    expect(request).toHaveBeenCalledOnce();
    expect(onSuccess).toHaveBeenCalledWith({ message: "已开始检查这一轮", running: true }, "已开始检查这一轮");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("没有消息时用默认提示", async () => {
    const onSuccess = vi.fn();
    await executeRunMonitor({
      busy: false,
      request: async () => ({}),
      onStart: vi.fn(),
      onSuccess,
      onFailure: vi.fn(),
      scheduleRefresh: vi.fn(),
    });
    expect(onSuccess).toHaveBeenCalledWith({}, "已开始检查");
  });

  it("失败时给出可见提示，且不调用真实接口", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const request = vi.fn(async () => {
      throw new Error("后台任务没有响应");
    });
    const onFailure = vi.fn();
    const onSuccess = vi.fn();
    const scheduleRefresh = vi.fn();
    await executeRunMonitor({
      busy: false,
      request,
      onStart: vi.fn(),
      onSuccess,
      onFailure,
      scheduleRefresh,
    });
    expect(request).toHaveBeenCalledOnce();
    expect(onSuccess).not.toHaveBeenCalled();
    expect(scheduleRefresh).not.toHaveBeenCalled();
    expect(onFailure).toHaveBeenCalledWith("后台任务没有响应");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("空错误也有一句可见说明", async () => {
    const onFailure = vi.fn();
    await executeRunMonitor({
      busy: false,
      request: async () => {
        throw new Error("");
      },
      onStart: vi.fn(),
      onSuccess: vi.fn(),
      onFailure,
      scheduleRefresh: vi.fn(),
    });
    expect(onFailure).toHaveBeenCalledWith("没有开始检查");
  });
});
