const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("voiceinboxDesktop", {
  isDesktop: true,
  backend: () => ipcRenderer.invoke("voiceinbox:backend"),
  openExternal: (url) => ipcRenderer.invoke("voiceinbox:open-external", url),
});
