(() => {
  // Easy Auth can append session material to the post-login URL; the app uses its cookie.
  if (window.location.hash.startsWith("#token=")) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  const param = new URLSearchParams(window.location.search).get("scoutTheme");
  const theme = param === "light" || param === "dark"
    ? param
    : (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", theme);
})();
