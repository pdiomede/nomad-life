// Applies the saved theme before the page is drawn, so it never flashes the wrong colors.
// A file instead of an inline script, so the Content-Security-Policy can refuse every inline
// script. Loaded in <head> without defer on purpose.
(function () {
  try {
    var t = localStorage.getItem("theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
    // The dashboard's 183 day marker starts hidden; shown only once switched on in this browser
    // (theme.js keeps the choice).
    if (localStorage.getItem("nl_marker") !== "on") document.documentElement.setAttribute("data-marker", "off");
  } catch (e) {}
})();
