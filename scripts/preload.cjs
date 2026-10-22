const { copyFileSync } = require("node:fs");
copyFileSync("electron/preload.cjs", "dist-electron/electron/preload.cjs");
copyFileSync("electron/missing-root-preload.cjs", "dist-electron/electron/missing-root-preload.cjs");
