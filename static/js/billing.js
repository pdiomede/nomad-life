// After paying on Stripe's checkout the plan page opens with ?paid=1. The plan changes when
// Stripe's webhook reaches the server, usually within seconds: ask for it every 2 seconds for
// up to 30, and reload (without ?paid=1) once it changed.
(function () {
  var box = document.querySelector("[data-billing-wait]");
  if (!box || !window.fetch) return;
  var url = box.getAttribute("data-status-url");
  var before = box.getAttribute("data-plan");
  var started = Date.now();
  var tick = function () {
    fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.plan && data.plan !== before) {
          window.location.replace(window.location.pathname);
        } else if (Date.now() - started < 30000) {
          setTimeout(tick, 2000);
        } else {
          box.textContent = "Payment received. Your plan has not updated yet; it will within a few minutes. Reload this page later, or write to support if it does not.";
        }
      })
      .catch(function () { if (Date.now() - started < 30000) setTimeout(tick, 2000); });
  };
  setTimeout(tick, 1500);
})();
