import { describe, expect, it } from "vitest";
import { destinationFromHash } from "../src/hash-view.js";
import { applyPlaybackRate } from "../src/playback.js";

describe("hash views", () => {
  it("maps #system to the settings page", () => {
    expect(destinationFromHash("#system")).toEqual({ kind: "view", view: "settings" });
    expect(destinationFromHash("settings")).toEqual({ kind: "view", view: "settings" });
    expect(destinationFromHash("#today")).toEqual({ kind: "view", view: "today" });
    expect(destinationFromHash("#")).toBeNull();
  });
});

describe("playback rate", () => {
  it("sets the rate on the element that is about to play", () => {
    const audio = { playbackRate: 1, defaultPlaybackRate: 1 };
    applyPlaybackRate(audio, 1.5);
    expect(audio).toEqual({ playbackRate: 1.5, defaultPlaybackRate: 1.5 });
    applyPlaybackRate(audio, 0);
    expect(audio.playbackRate).toBe(1.5);
  });
});
