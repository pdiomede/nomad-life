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

  // Show the chosen file name next to custom file inputs, and reject files over the
  // upload limit before submitting (the server would drop the whole form).
  document.querySelectorAll("input[type=file][data-label]").forEach(function (input) {
    input.addEventListener("change", function () {
      var label = document.getElementById(input.getAttribute("data-label"));
      var max = parseInt(input.getAttribute("data-max-bytes") || "0", 10);
      var left = parseInt(input.getAttribute("data-left-bytes") || "-1", 10);
      var file = input.files.length ? input.files[0] : null;
      var error = "";
      if (file && file.size === 0) {
        error = file.name + " is empty.";
      } else if (file && max && file.size > max) {
        error = file.name + " is too large (max " + input.getAttribute("data-max-label") + " per receipt).";
      } else if (file && left >= 0 && file.size > left) {
        error = "Not enough storage left for " + file.name + " (" + input.getAttribute("data-left-label") + " free).";
      }
      if (error) input.value = "";
      if (label) {
        label.textContent = error || (file ? file.name : "No file selected");
        label.classList.toggle("file-error", !!error);
      }
    });
  });
})();
