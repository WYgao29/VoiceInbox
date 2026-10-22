(function () {
  try {
    var saved = localStorage.getItem("voiceinbox-theme") || "system";
    var choice = saved === "light" || saved === "dark" ? saved : "system";
    var dark =
      choice === "dark" ||
      (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.dataset.themeChoice = choice;
  } catch (error) {
    document.documentElement.dataset.theme = "light";
    document.documentElement.dataset.themeChoice = "system";
  }
})();
