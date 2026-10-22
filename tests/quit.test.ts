import { describe, expect, it } from "vitest";
import { isExplainWindowSender } from "../electron/quit.js";

describe("voiceinbox:quit", () => {
  it("只接受说明窗口自己的 sender", () => {
    const explain = { id: 7 };
    const other = { id: 7 };
    expect(isExplainWindowSender(explain, explain)).toBe(true);
    expect(isExplainWindowSender(other, explain)).toBe(false);
    expect(isExplainWindowSender(explain, null)).toBe(false);
    expect(isExplainWindowSender(null, explain)).toBe(false);
    expect(isExplainWindowSender(undefined, undefined)).toBe(false);
  });
});
