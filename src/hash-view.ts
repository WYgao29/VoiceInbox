import type { ViewName } from "../shared/types.js";

export function destinationFromHash(hash: string): { kind: "view"; view: ViewName } | { kind: "recording"; id: string } | null {
  const name = hash.replace(/^#/, "").trim();
  if (name === "system" || name === "settings") return { kind: "view", view: "settings" };
  if (name === "today" || name === "vocab") return { kind: "view", view: name };
  if (/^[0-9a-f]{12,64}$/.test(name)) return { kind: "recording", id: name };
  return null;
}
