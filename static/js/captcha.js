// hCaptcha on Sign up and Forgot password. Drawn by hand (render=explicit) in the page's theme,
// and drawn again when the theme changes (the header button, or the system setting while no
// theme is saved), unless the check is already done: drawing again would undo it.
(function () {
  var box = document.querySelector(".h-captcha");
  if (!box) return;
  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var widget = null;
  var drawn = null;

  function theme() {
    var t = root.getAttribute("data-theme");
    if (t === "dark" || t === "light") return t;
    return media && media.matches ? "dark" : "light";
  }

  function draw() {
    var api = window.hcaptcha;
    var t = theme();
    if (!api || t === drawn) return;
    if (widget !== null) {
      if (api.getResponse(widget)) return;
      api.remove(widget);
    }
    drawn = t;
    widget = api.render(box, { sitekey: box.getAttribute("data-sitekey"), theme: t });
  }

  window.nlCaptchaReady = draw;
  if (window.MutationObserver) {
    new MutationObserver(draw).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  }
  if (media && media.addEventListener) media.addEventListener("change", draw);

  var script = document.createElement("script");
  script.src = "https://js.hcaptcha.com/1/api.js?render=explicit&onload=nlCaptchaReady";
  script.async = true;
  document.head.appendChild(script);
})();
