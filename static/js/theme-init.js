// Applies the saved theme before the page is drawn, so it never flashes the wrong colors.
// A file instead of an inline script, so the Content-Security-Policy can refuse every inline
// script. Loaded in <head> without defer on purpose.
(function () {
  try {
    var t = localStorage.getItem("theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) {}
})();
