import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { BrowserWindow } from "electron";

const SHOTS = [
  ["recordings-light", "light", "recordings"],
  ["today-light", "light", "today"],
  ["vocab-light", "light", "vocab"],
  ["settings-light", "light", "settings"],
  ["recordings-dark", "dark", "recordings"],
  ["today-dark", "dark", "today"],
  ["vocab-dark", "dark", "vocab"],
  ["settings-dark", "dark", "settings"],
] as const;

export async function captureViews(win: BrowserWindow, dir: string): Promise<string[]> {
  await mkdir(dir, { recursive: true });
  const written: string[] = [];
  for (const [name, theme, view] of SHOTS) {
    await win.webContents.executeJavaScript(
      `(() => {
        const spec = ${JSON.stringify({ theme, view })};
        const started = Date.now();
        const wait = () => new Promise((resolve, reject) => {
          const tick = () => {
            if (window.__voiceinboxShot) {
              resolve(window.__voiceinboxShot(spec));
              return;
            }
            if (Date.now() - started > 20000) reject(new Error("界面没有准备好"));
            else setTimeout(tick, 100);
          };
          tick();
        });
        return wait();
      })()`,
      true,
    );
    const image = await win.webContents.capturePage();
    const file = join(dir, `${name}.png`);
    await writeFile(file, image.toPNG());
    written.push(file);
  }
  return written;
}
