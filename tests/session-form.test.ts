import { describe, expect, it, vi } from "vitest";
import { createSessionForm } from "../src/session-form.js";

const messages = { success: "已保存", failure: "保存失败" };

describe("会话草稿", () => {
  it("后台刷新保留未保存的修改，已保存的表单跟随服务端", () => {
    const form = createSessionForm({ text: "初始值", enabled: true });
    form.setValue({ text: "草稿", enabled: false });
    form.syncServer({ text: "后台新值", enabled: true });
    expect(form.getSnapshot()).toMatchObject({ value: { text: "草稿", enabled: false }, dirty: true });
    form.setValue({ text: "后台新值", enabled: true });
    expect(form.getSnapshot().dirty).toBe(false);
    form.syncServer({ text: "下一次刷新", enabled: false });
    expect(form.getSnapshot().value).toEqual({ text: "下一次刷新", enabled: false });
  });

  it("页面取消订阅后仍保留输入和保存状态，阻止重复提交", async () => {
    const form = createSessionForm("原值");
    const listener = vi.fn();
    const unsubscribe = form.subscribe(listener);
    form.setValue("草稿");
    let finish!: (saved: boolean) => void;
    const send = vi.fn(() => new Promise<boolean>((resolve) => { finish = resolve; }));
    const pending = form.submit(send, messages);
    unsubscribe();
    await form.submit(send, messages);
    expect(send).toHaveBeenCalledOnce();
    expect(form.getSnapshot()).toMatchObject({ value: "草稿", pending: true, dirty: true });
    finish(true);
    await pending;
    expect(form.getSnapshot()).toMatchObject({ value: "草稿", pending: false, dirty: false, feedback: { tone: "success" } });
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("保存失败或请求抛错时保留草稿并允许重试", async () => {
    const form = createSessionForm("原值");
    form.setValue("草稿");
    await form.submit(async () => false, messages);
    expect(form.getSnapshot()).toMatchObject({ value: "草稿", pending: false, dirty: true, feedback: { tone: "error" } });
    await form.submit(async () => { throw new Error("断开连接"); }, messages);
    expect(form.getSnapshot()).toMatchObject({ value: "草稿", pending: false, dirty: true });
    await form.submit(async () => true, messages);
    expect(form.getSnapshot().dirty).toBe(false);
  });

  it("保存完成时采用服务端规范化结果，后续输入仍保留为草稿", async () => {
    const form = createSessionForm("原值");
    form.setValue("甲、甲");
    await form.submit(async () => {
      form.syncServer("甲");
      return true;
    }, messages);
    expect(form.getSnapshot()).toMatchObject({ value: "甲", dirty: false });
    form.setValue("乙");
    await form.submit(async () => {
      form.setValue("丙");
      form.syncServer("乙");
      return true;
    }, messages);
    expect(form.getSnapshot()).toMatchObject({ value: "丙", dirty: true });
  });

  it("添加成功清空已提交纠错，但保留请求期间新输入的纠错", async () => {
    const empty = { wrong: "", right: "" };
    const form = createSessionForm(empty);
    const options = { ...messages, savedValue: () => empty };
    form.setValue({ wrong: "甲", right: "乙" });
    await form.submit(async () => true, options);
    expect(form.getSnapshot()).toMatchObject({ value: empty, dirty: false });
    form.setValue({ wrong: "甲", right: "乙" });
    await form.submit(async () => {
      form.setValue({ wrong: "丙", right: "丁" });
      return true;
    }, options);
    expect(form.getSnapshot()).toMatchObject({ value: { wrong: "丙", right: "丁" }, dirty: true });
  });
});
