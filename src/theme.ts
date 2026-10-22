import type { ThemeChoice } from "../shared/types.js";

export function readThemeChoice(): ThemeChoice {
  try {
    const saved = localStorage.getItem("voiceinbox-theme");
    if (saved === "light" || saved === "dark" || saved === "system") return saved;
  } catch {
    /* private mode */
  }
  return "system";
}

export function applyTheme(choice: ThemeChoice): void {
  const dark =
    choice === "dark" ||
    (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.themeChoice = choice;
  try {
    localStorage.setItem("voiceinbox-theme", choice);
  } catch {
    /* ignore */
  }
}
