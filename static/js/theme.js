(function () {
  var root = document.documentElement;
  var media = window.matchMedia("(prefers-color-scheme: dark)");

  function current() {
    var t = root.getAttribute("data-theme");
    if (t === "light" || t === "dark") return t;
    return media.matches ? "dark" : "light";
  }

  var btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.addEventListener("click", function () {
      var next = current() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("theme", next); } catch (e) {}
    });
  }

  // Ask before destructive actions.
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  // Show the chosen file name next to custom file inputs.
  document.querySelectorAll("input[type=file][data-label]").forEach(function (input) {
    input.addEventListener("change", function () {
      var label = document.getElementById(input.getAttribute("data-label"));
      if (label) label.textContent = input.files.length ? input.files[0].name : "No file selected";
    });
  });
})();
