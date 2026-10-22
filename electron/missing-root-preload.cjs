const { ipcRenderer } = require("electron");

window.addEventListener("DOMContentLoaded", () => {
  const button = document.getElementById("quit");
  if (!button) return;
  button.addEventListener("click", () => {
    ipcRenderer.send("voiceinbox:quit");
  });
});
