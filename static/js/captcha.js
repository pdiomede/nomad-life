// hCaptcha on Sign up and Forgot password: give the widget the page's theme, then load
// hCaptcha's script, which finds the .h-captcha box and draws the check in it.
(function () {
  var box = document.querySelector(".h-captcha");
  if (!box) return;
  var theme = document.documentElement.getAttribute("data-theme");
  if (theme !== "dark" && theme !== "light") {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  box.setAttribute("data-theme", theme);
  var script = document.createElement("script");
  script.src = "https://js.hcaptcha.com/1/api.js";
  script.async = true;
  document.head.appendChild(script);
})();
