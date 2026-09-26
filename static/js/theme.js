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

  // Share buttons: copy the landing page link, and use the device share sheet when available.
  document.querySelectorAll("[data-copy-link]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var url = btn.getAttribute("data-copy-link");
      var done = function (ok) {
        var label = btn.textContent;
        btn.textContent = ok ? "Link copied" : "Copy failed";
        setTimeout(function () { btn.textContent = label; }, 2000);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(url).then(function () { done(true); }, function () { done(false); });
      } else {
        var field = document.createElement("textarea");
        field.value = url; field.setAttribute("readonly", ""); field.style.position = "fixed"; field.style.opacity = "0";
        document.body.appendChild(field); field.select();
        var ok = false;
        try { ok = document.execCommand("copy"); } catch (e) {}
        document.body.removeChild(field); done(ok);
      }
    });
  });
  document.querySelectorAll("[data-native-share]").forEach(function (btn) {
    if (!navigator.share) return;
    btn.hidden = false;
    btn.addEventListener("click", function () {
      navigator.share({ title: btn.getAttribute("data-title"), text: btn.getAttribute("data-text"),
                        url: btn.getAttribute("data-url") }).catch(function () {});
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
