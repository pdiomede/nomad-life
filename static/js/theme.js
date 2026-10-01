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

  // The support button in the header glows slowly until this browser has opened the support
  // pages once, so people notice it is new. A new reply makes it glow again (set by the server).
  var support = document.querySelector(".support-btn");
  if (support) {
    var seen = false;
    try {
      if (/^\/support(\/|$)/.test(location.pathname)) localStorage.setItem("nl_support_seen", "1");
      seen = localStorage.getItem("nl_support_seen") === "1";
    } catch (e) { seen = true; }
    if (!seen) support.classList.add("is-new");
    support.addEventListener("click", function () {
      try { localStorage.setItem("nl_support_seen", "1"); } catch (e) {}
    });
  }

  // Forms marked data-submit-once ignore a second submit (a double click on "Submit ticket"
  // would otherwise send it twice; the server also refuses the copy). Coming back to the page
  // with the browser's Back button makes the form usable again.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.hasAttribute || !form.hasAttribute("data-submit-once")) return;
    if (event.defaultPrevented) return;  // another check stopped this submit: allow the next
    if (form.getAttribute("data-sent")) {
      event.preventDefault();
      return;
    }
    form.setAttribute("data-sent", "1");
  });
  window.addEventListener("pageshow", function () {
    var sent = document.querySelectorAll("form[data-sent]");
    for (var i = 0; i < sent.length; i++) sent[i].removeAttribute("data-sent");
  });

  // Deleting always asks twice. Step 1 names what will be deleted, step 2 asks "Are you sure?"
  // (and, for a year, to type it). Only then the form gets confirm_delete=2, which the server
  // requires before deleting anything.
  var dialog = document.getElementById("confirm-dialog");
  var confirmAndSubmit = function (form, typed) {
    var setHidden = function (name, value) {
      var field = form.querySelector('input[name="' + name + '"]');
      if (!field) {
        field = document.createElement("input");
        field.type = "hidden"; field.name = name; form.appendChild(field);
      }
      field.value = value;
    };
    setHidden("confirm_delete", "2");
    // The typed value goes to confirm_year (years) or the field the form names (accounts).
    if (form.getAttribute("data-confirm-type")) setHidden(form.getAttribute("data-confirm-field") || "confirm_year", typed);
    form.querySelectorAll("[type=submit]").forEach(function (b) { b.disabled = true; });
    form.submit();
  };
  if (dialog && typeof dialog.showModal !== "function") {
    // Older browsers without <dialog>: still ask twice, with the browser's own prompts.
    document.querySelectorAll("form[data-confirm-delete]").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var what = form.getAttribute("data-confirm-delete");
        var need = form.getAttribute("data-confirm-type");
        if (!window.confirm("Delete " + what + "?")) return;
        if (!window.confirm("Are you sure? This permanently deletes " + what + ". It cannot be undone.")) return;
        var typed = "";
        if (need) {
          typed = (window.prompt("Type " + need + " to confirm.") || "").trim();
          if (typed.toLowerCase() !== need.toLowerCase()) return;
        }
        confirmAndSubmit(form, typed);
      });
    });
  }
  if (dialog && typeof dialog.showModal === "function") {
    var dStep = document.getElementById("confirm-step");
    var dTitle = document.getElementById("confirm-title");
    var dText = document.getElementById("confirm-text");
    var dTypeField = document.getElementById("confirm-type-field");
    var dTypeValue = document.getElementById("confirm-type-value");
    var dTypeInput = document.getElementById("confirm-type-input");
    var dCancel = document.getElementById("confirm-cancel");
    var dNext = document.getElementById("confirm-next");
    var dLive = document.getElementById("confirm-live");
    var pending = null, opener = null, step = 1, armed = false, armTimer = null;

    var typedOk = function () {
      var need = pending && pending.getAttribute("data-confirm-type");
      return !need || dTypeInput.value.trim().toLowerCase() === need.toLowerCase();
    };
    // The step 2 button sits where the step 1 button was, so a double click would confirm
    // twice in one gesture. Keep it inactive for a moment so the second click is deliberate.
    var refreshNext = function () { dNext.disabled = step === 2 && !(armed && typedOk()); };
    var showStep = function (n) {
      step = n;
      var what = pending.getAttribute("data-confirm-delete");
      var need = pending.getAttribute("data-confirm-type");
      dStep.textContent = "Step " + n + " of 2";
      if (n === 1) {
        dTitle.textContent = "Delete " + what + "?";
        dText.textContent = "You will be asked once more before anything is deleted.";
        dNext.textContent = "Delete";
        dTypeField.hidden = true;
        dNext.disabled = false;
        if (dLive) dLive.textContent = "";
        dCancel.focus();
      } else {
        dTitle.textContent = "Are you sure?";
        dText.textContent = "This permanently deletes " + what + ". It cannot be undone.";
        dNext.textContent = "Yes, delete permanently";
        dTypeField.hidden = !need;
        dTypeValue.textContent = need || "";
        dTypeInput.value = "";
        // A year gets the number pad; an email address the normal keyboard.
        dTypeInput.setAttribute("inputmode", /^\d+$/.test(need || "") ? "numeric" : "email");
        armed = false;
        clearTimeout(armTimer);
        armTimer = setTimeout(function () { armed = true; refreshNext(); }, 700);
        refreshNext();
        (need ? dTypeInput : dCancel).focus();
        // Screen readers only say "Cancel, button" when focus moves, so read the new question.
        if (dLive) setTimeout(function () { dLive.textContent = dTitle.textContent + " " + dText.textContent; }, 50);
      }
    };

    document.querySelectorAll("form[data-confirm-delete]").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (dialog.open) return;
        pending = form;
        opener = e.submitter || form.querySelector("[type=submit]");
        dialog.showModal();
        showStep(1);
      });
    });
    dTypeInput.addEventListener("input", refreshNext);
    dTypeInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); if (!dNext.disabled) dNext.click(); }
    });
    dCancel.addEventListener("click", function () { dialog.close("cancel"); });
    dNext.addEventListener("click", function () {
      if (step === 1) { showStep(2); return; }
      if (!armed || !typedOk()) return;
      var form = pending;
      var typed = dTypeInput.value.trim();
      dialog.close("confirmed");
      confirmAndSubmit(form, typed);
    });
    dialog.addEventListener("close", function () {
      clearTimeout(armTimer);
      armed = false;
      if (dialog.returnValue !== "confirmed" && opener) opener.focus();
      pending = null;
      dialog.returnValue = "";
    });
  }

  // Country picker: searchable list with flags. Enhances inputs marked data-country-combo;
  // without JavaScript they keep the native datalist suggestions.
  var countryData = document.getElementById("country-data");
  var countries = [];
  try { countries = countryData ? JSON.parse(countryData.textContent) : []; } catch (e) { countries = []; }
  var fold = function (s) {  // the same key as fold() in app.py
    return (s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/’/g, "'")
      .replace(/[-\u2010-\u2015]/g, " ").replace(/&/g, " and ")
      .toLowerCase().replace(/\s+/g, " ").trim();
  };
  var flagOf = function (code) {
    if (!code || !/^[A-Za-z]{2}$/.test(code)) return "";
    return String.fromCodePoint(0x1F1E6 + code.toUpperCase().charCodeAt(0) - 65,
                                0x1F1E6 + code.toUpperCase().charCodeAt(1) - 65);
  };
  var entries = countries.map(function (c) {
    return { name: c[0], code: c[1], flag: flagOf(c[1]), keys: [fold(c[0])].concat((c[2] || []).map(fold)) };
  });
  var exact = {};
  [0, 1, 2].forEach(function (pass) {  // names win over codes, codes over aliases
    entries.forEach(function (e) {
      var keys = pass === 0 ? [e.keys[0]] : pass === 1 ? [e.code.toLowerCase()] : e.keys.slice(1);
      keys.forEach(function (k) { if (!(k in exact)) exact[k] = e; });
    });
  });
  var search = function (query) {
    var q = fold(query);
    if (!q) return entries.slice();
    var scored = [];
    entries.forEach(function (e) {
      var best = e.code.toLowerCase() === q ? 1 : 99;
      e.keys.forEach(function (k, i) {
        var s = 99, alias = i > 0 ? 1 : 0;
        if (k === q) s = 0 + alias;
        else if (k.indexOf(q) === 0) s = 2 + alias;
        else if ((" " + k).indexOf(" " + q) >= 0 || k.indexOf("-" + q) >= 0) s = 4 + alias;
        else if (k.indexOf(q) >= 0) s = 6 + alias;
        if (s < best) best = s;
      });
      if (best < 99) scored.push([best, e]);
    });
    scored.sort(function (a, b) { return a[0] - b[0] || a[1].name.localeCompare(b[1].name); });
    return scored.map(function (x) { return x[1]; });
  };

  document.querySelectorAll("input[data-country-combo]").forEach(function (input, n) {
    if (!entries.length) return;
    var listId = "country-list-" + n;
    var wrap = document.createElement("div");
    wrap.className = "combo";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var flagEl = document.createElement("span");
    flagEl.className = "combo-flag flag";
    flagEl.setAttribute("aria-hidden", "true");
    wrap.insertBefore(flagEl, input);
    var list = document.createElement("ul");
    list.className = "combo-list";
    list.id = listId;
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "Countries");
    list.hidden = true;
    wrap.appendChild(list);
    // Live region, always in the DOM so screen readers announce the text when it appears.
    var empty = document.createElement("div");
    empty.className = "combo-empty";
    empty.setAttribute("role", "status");
    var noMatch = "No matching country. You can keep what you typed.";
    wrap.appendChild(empty);
    // Pressing on the list (its scrollbar, padding or the empty message) must not move focus
    // away from the input, or the blur handler would close the list mid scroll.
    [list, empty].forEach(function (el) {
      el.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
    });

    input.removeAttribute("list");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", listId);

    var shown = [], active = -1;
    var updateFlag = function () {
      var e = exact[fold(input.value)];
      flagEl.textContent = e ? e.flag : "";
      wrap.classList.toggle("has-flag", !!e);
    };
    var setActive = function (i) {
      var items = list.children;
      if (active >= 0 && items[active]) items[active].setAttribute("aria-selected", "false");
      active = i;
      if (i >= 0 && items[i]) {
        items[i].setAttribute("aria-selected", "true");
        input.setAttribute("aria-activedescendant", items[i].id);
        items[i].scrollIntoView({ block: "nearest" });
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    };
    var close = function () {
      list.hidden = true; empty.textContent = ""; active = -1;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    };
    var render = function (items, highlight) {
      list.textContent = "";
      shown = items;
      items.forEach(function (e, i) {
        var li = document.createElement("li");
        li.id = listId + "-" + i;
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", "false");
        var f = document.createElement("span");
        f.className = "flag"; f.setAttribute("aria-hidden", "true"); f.textContent = e.flag;
        var label = document.createElement("span");
        label.textContent = e.name;
        li.appendChild(f); li.appendChild(label);
        li.addEventListener("mousedown", function (ev) { ev.preventDefault(); });  // keep focus in the input
        li.addEventListener("click", function () { choose(e); });
        list.appendChild(li);
      });
      list.hidden = items.length === 0;
      empty.textContent = items.length ? "" : noMatch;
      input.setAttribute("aria-expanded", items.length ? "true" : "false");
      active = -1;
      // The old options are gone: never leave the input pointing at one of them.
      input.removeAttribute("aria-activedescendant");
      if (items.length && highlight >= 0) setActive(highlight);
    };
    var open = function (filter) {
      var current = exact[fold(input.value)];
      if (!filter || current) {
        // Opening on a chosen country shows the whole list with that country highlighted;
        // otherwise nothing is, so Enter still submits a free text place as typed.
        render(entries, current ? entries.indexOf(current) : -1);
      } else {
        render(search(input.value), 0);
      }
    };
    var choose = function (e) {
      input.value = e.name;
      updateFlag();
      close();
      input.focus();
    };

    input.addEventListener("input", function () {
      updateFlag();
      // An emptied field lists every country with none highlighted: Enter then submits.
      render(search(input.value), fold(input.value) ? 0 : -1);
    });
    input.addEventListener("click", function () { if (list.hidden) open(false); });
    input.addEventListener("keydown", function (e) {
      var isOpen = !list.hidden;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!isOpen) { open(true); return; }
        var step = e.key === "ArrowDown" ? 1 : -1;
        setActive((active + step + shown.length) % shown.length);
      } else if (e.key === "Enter") {
        if (isOpen && active >= 0 && shown[active]) { e.preventDefault(); choose(shown[active]); }
      } else if (e.key === "Escape") {
        if (isOpen || empty.textContent) { e.preventDefault(); close(); }
      } else if (e.key === "Tab") {
        close();
      }
    });
    input.addEventListener("blur", function () { close(); });
    updateFlag();
  });

  // Downloads (accountant package): show a busy state and block double clicks until the
  // server answers. The response sets a cookie with the token we sent, which ends the wait.
  document.querySelectorAll("form[data-download]").forEach(function (form) {
    var btn = form.querySelector("[type=submit]");
    var label = btn ? btn.textContent : "";
    var timer = null;
    var readCookie = function () {
      var m = document.cookie.match(/(?:^|; )nl_download=([^;]*)/);
      return m ? m[1] : "";
    };
    var done = function () {
      clearInterval(timer);
      document.cookie = "nl_download=; Max-Age=0; path=/; SameSite=Lax";
      if (btn) { btn.removeAttribute("aria-disabled"); btn.textContent = label; btn.removeAttribute("aria-busy"); }
    };
    form.addEventListener("submit", function (e) {
      if (btn && btn.getAttribute("aria-disabled") === "true") { e.preventDefault(); return; }
      var token = Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
      var field = form.querySelector('input[name="dl"]');
      if (field) field.value = token;
      if (btn) {
        // Mark busy after the browser has taken the form data. aria-disabled (not disabled)
        // keeps keyboard focus on the button.
        setTimeout(function () {
          btn.setAttribute("aria-disabled", "true"); btn.textContent = "Preparing package..."; btn.setAttribute("aria-busy", "true");
        }, 0);
      }
      var started = Date.now();
      clearInterval(timer);
      timer = setInterval(function () {
        if (readCookie() === token || Date.now() - started > 60000) done();
      }, 400);
    });
  });

  // Share buttons: copy the landing page link, and use the device share sheet when available.
  document.querySelectorAll("[data-copy-link]").forEach(function (btn) {
    // Read the label once: a second click within 2 s would otherwise keep "Link copied".
    var label = btn.textContent, timer = null;
    btn.addEventListener("click", function () {
      var url = btn.getAttribute("data-copy-link");
      var done = function (ok) {
        btn.textContent = ok ? "Link copied" : "Copy failed";
        clearTimeout(timer);
        timer = setTimeout(function () { btn.textContent = label; }, 2000);
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

  // Map: zoom (+, -, whole world) and pan by dragging or with the arrow keys. Only the land is
  // scaled; pins are placed in screen pixels, keep their size and are nudged apart when they
  // would cover each other's day counts. Without JavaScript the map stays a plain, whole map.
  var ZOOMS = [1, 2, 4, 8];
  document.querySelectorAll(".map").forEach(function (map) {
    var land = map.querySelector(".map-land");
    var card = map.closest(".map-card") || map;
    var controls = card.querySelector(".map-zoom");
    var status = card.querySelector("[data-map-status]");
    var pins = [].slice.call(map.querySelectorAll(".map-pin")).map(function (pin) {
      // The server gives each pin's place as a percentage of the whole map.
      return { pin: pin, fx: parseFloat(pin.style.left) / 100, fy: parseFloat(pin.style.top) / 100 };
    });
    var z = 1, tx = 0, ty = 0;

    var bounds = function () {  // the map always fills the view: no empty edges
      var w = map.clientWidth, h = map.clientHeight;
      tx = Math.min(0, Math.max(w - w * z, tx));
      ty = Math.min(0, Math.max(h - h * z, ty));
    };

    var nudge = function (items, w, h) {
      // A pin moves at most about one pin away from its place and never leaves the view; on a
      // very small map a few pins may still touch rather than drift to the wrong place.
      var reach = Math.max(16, Math.min(56, w * 0.07));
      var clamp = function (it) {
        it.dx = Math.max(-reach, Math.min(reach, it.dx));
        it.dy = Math.max(-reach, Math.min(reach, it.dy));
        it.dx = Math.min(Math.max(it.x + it.dx, it.rx), w - it.rx) - it.x;
        it.dy = Math.min(Math.max(it.y + it.dy, it.ry), h - it.ry) - it.y;
      };
      items.forEach(clamp);
      for (var round = 0; round < 400; round++) {
        var moved = false;
        for (var i = 0; i < items.length; i++) {
          for (var j = i + 1; j < items.length; j++) {
            var a = items[i], b = items[j];
            var ox = (a.rx + b.rx) - Math.abs((b.x + b.dx) - (a.x + a.dx));
            var oy = (a.ry + b.ry) - Math.abs((b.y + b.dy) - (a.y + a.dy));
            if (ox <= 0 || oy <= 0) continue;
            moved = true;
            // Separate along the shorter overlap; identical points split sideways.
            var sx = Math.sign((b.x + b.dx) - (a.x + a.dx)) || (j % 2 ? 1 : -1);
            var sy = Math.sign((b.y + b.dy) - (a.y + a.dy)) || (j % 2 ? 1 : -1);
            if (ox < oy) { a.dx -= sx * ox / 2; b.dx += sx * ox / 2; }
            else { a.dy -= sy * oy / 2; b.dy += sy * oy / 2; }
            clamp(a); clamp(b);
          }
        }
        if (!moved) break;
      }
    };

    var render = function () {
      var w = map.clientWidth, h = map.clientHeight;
      bounds();
      land.style.transform = z === 1 ? "" : "translate(" + tx + "px, " + ty + "px) scale(" + z + ")";
      map.classList.toggle("is-zoomed", z > 1);
      var shown = [];
      pins.forEach(function (p) {
        var x = tx + p.fx * w * z, y = ty + p.fy * h * z;
        var off = x < 0 || x > w || y < 0 || y > h;
        // A pin outside the view is invisible but stays focusable (focus brings it into view);
        // it is parked inside the map so it never widens the page.
        p.pin.style.left = (off ? Math.min(Math.max(x, 0), w) : x).toFixed(1) + "px";
        p.pin.style.top = (off ? Math.min(Math.max(y, 0), h) : y).toFixed(1) + "px";
        p.pin.classList.toggle("is-off", off);
        // Tooltips open away from the edge of the view they are next to.
        p.pin.classList.toggle("tip-right", x < w * 0.18);
        p.pin.classList.toggle("tip-left", x > w * 0.82);
        p.pin.classList.toggle("tip-below", y < h * 0.22);
        if (!off) shown.push({ pin: p.pin, x: x, y: y, dx: 0, dy: 0,
                               rx: p.pin.offsetWidth / 2 + 1, ry: p.pin.offsetHeight / 2 + 1 });
        else { p.pin.style.setProperty("--nudge-x", "0px"); p.pin.style.setProperty("--nudge-y", "0px"); }
      });
      nudge(shown, w, h);
      shown.forEach(function (it) {
        it.pin.style.setProperty("--nudge-x", it.dx.toFixed(1) + "px");
        it.pin.style.setProperty("--nudge-y", it.dy.toFixed(1) + "px");
      });
      if (controls) {
        // aria-disabled, not disabled: the button keeps the focus, so pressing Enter again at a
        // limit does nothing instead of acting on the next button.
        var limit = function (what, on) {
          controls.querySelector('[data-map-zoom="' + what + '"]').setAttribute("aria-disabled", on ? "true" : "false");
        };
        limit("in", z >= ZOOMS[ZOOMS.length - 1]);
        limit("out", z <= 1);
        limit("reset", z <= 1);
      }
    };

    var zoomTo = function (next, cx, cy) {
      // Keep the point under (cx, cy), the middle of the view by default, where it is.
      var w = map.clientWidth, h = map.clientHeight;
      cx = cx === undefined ? w / 2 : cx; cy = cy === undefined ? h / 2 : cy;
      var wx = (cx - tx) / z, wy = (cy - ty) / z;
      z = next; tx = cx - wx * z; ty = cy - wy * z;
      render();
      if (status) status.textContent = z === 1 ? "Whole world" : "Zoom " + z + "x";
    };
    var step = function (dir) {
      var i = ZOOMS.indexOf(z) + dir;
      if (i >= 0 && i < ZOOMS.length) zoomTo(ZOOMS[i]);
    };

    if (controls) {
      controls.hidden = false;
      controls.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-map-zoom]");
        if (!btn || btn.getAttribute("aria-disabled") === "true") return;
        var what = btn.getAttribute("data-map-zoom");
        if (what === "in") step(1); else if (what === "out") step(-1);
        else { tx = 0; ty = 0; zoomTo(1); }
      });
    }

    // Drag to pan while zoomed. A drag is not a tap: the pin under the pointer stays closed.
    var drag = null, dragged = false;
    map.addEventListener("pointerdown", function (e) {
      dragged = false;  // every gesture starts fresh (a touch drag ends without a click)
      if (z === 1 || e.button !== 0) return;
      // With a mouse, pressing on a pin to drag must not focus it (its tooltip would ride along).
      if (e.pointerType === "mouse") e.preventDefault();
      drag = { x: e.clientX, y: e.clientY, tx: tx, ty: ty, id: e.pointerId };
    });
    map.addEventListener("pointermove", function (e) {
      if (!drag || e.pointerId !== drag.id) return;
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!dragged && Math.abs(dx) + Math.abs(dy) < 5) return;
      if (!dragged) { dragged = true; map.setPointerCapture(e.pointerId); map.classList.add("is-dragging"); }
      tx = drag.tx + dx; ty = drag.ty + dy;
      render();
    });
    var endDrag = function () {
      if (!drag) return;
      drag = null;
      map.classList.remove("is-dragging");
    };
    map.addEventListener("pointerup", endDrag);
    map.addEventListener("pointercancel", endDrag);
    map.addEventListener("click", function (e) {
      if (dragged) { e.preventDefault(); e.stopPropagation(); dragged = false; if (document.activeElement) document.activeElement.blur(); }
    }, true);

    // Keyboard: arrows pan, + and - zoom, 0 shows the whole world, from any pin or button.
    card.addEventListener("keydown", function (e) {
      if (!e.target.closest(".map, .map-zoom")) return;
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      var w = map.clientWidth, h = map.clientHeight, moved = true;
      if (e.key === "+" || e.key === "=") step(1);
      else if (e.key === "-" || e.key === "_") step(-1);
      else if (e.key === "0") { tx = 0; ty = 0; zoomTo(1); }
      else if (z > 1 && e.key === "ArrowLeft") { tx += w * 0.2; render(); }
      else if (z > 1 && e.key === "ArrowRight") { tx -= w * 0.2; render(); }
      else if (z > 1 && e.key === "ArrowUp") { ty += h * 0.2; render(); }
      else if (z > 1 && e.key === "ArrowDown") { ty -= h * 0.2; render(); }
      else moved = false;
      if (moved) {
        e.preventDefault();
        keepPinInView();
      }
    });

    // Panning with the keys never hides the pin that has the focus.
    var keepPinInView = function () {
      var el = document.activeElement, p = pins.filter(function (q) { return q.pin === el; })[0];
      if (!p || z === 1) return;
      var w = map.clientWidth, h = map.clientHeight, m = 16;
      var x = tx + p.fx * w * z, y = ty + p.fy * h * z;
      if (x < m) tx += m - x; else if (x > w - m) tx -= x - (w - m);
      if (y < m) ty += m - y; else if (y > h - m) ty -= y - (h - m);
      render();
    };

    // Tabbing to a pin outside the zoomed view brings it into view.
    pins.forEach(function (p) {
      p.pin.addEventListener("focus", function () {
        if (!p.pin.classList.contains("is-off")) return;
        var w = map.clientWidth, h = map.clientHeight;
        tx = w / 2 - p.fx * w * z; ty = h / 2 - p.fy * h * z;
        render();
      });
    });

    render();
    var timer = null, last = map.clientWidth;
    window.addEventListener("resize", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        // Keep the same middle of the view at the new size.
        var w = map.clientWidth;
        if (last && w !== last) { tx *= w / last; ty *= w / last; }
        last = w;
        render();
      }, 100);
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
      } else if (file && left >= 0 && (/\.(jpe?g|png|webp)$/i.test(file.name) ? left === 0 : file.size > left)) {
        // Big photos are resized on the server, so only the server can tell whether they fit;
        // they are refused here only when no space is left at all.
        error = "Not enough storage left for " + file.name + " (" + input.getAttribute("data-left-label") + " free).";
      }
      if (error) input.value = "";
      if (label) {
        label.textContent = error || (file ? file.name : "No file selected");
        label.classList.toggle("file-error", !!error);
      }
    });
  });

  // User menu in the header (a <details>, so it also works without JavaScript): close it on a
  // click outside, on Escape (back to the email) and when focus moves out of it.
  document.querySelectorAll("[data-user-menu]").forEach(function (menu) {
    var trigger = menu.querySelector("summary");
    document.addEventListener("click", function (e) {
      if (menu.open && !menu.contains(e.target)) menu.open = false;
    });
    menu.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menu.open) {
        menu.open = false;
        trigger.focus();
      }
    });
    menu.addEventListener("focusout", function (e) {
      if (menu.open && e.relatedTarget && !menu.contains(e.relatedTarget)) menu.open = false;
    });
  });

  // Flash messages: show the close button and remove the message when it is clicked.
  // Focus moves to the next message's button, or to the page content after the last one.
  // Overlapping movements: the red triangle next to the Movements title opens the report, which
  // can be copied. Without <dialog> support the report shows in the browser's own alert.
  var overlapOpen = document.querySelector("[data-overlap-open]");
  var overlapDialog = document.getElementById("overlap-dialog");
  if (overlapOpen && overlapDialog) {
    var overlapText = document.getElementById("overlap-text");
    var overlapStatus = overlapDialog.querySelector("[data-overlap-status]");
    var overlapCopied = overlapDialog.querySelector("[data-overlap-copied]");
    var copiedTimer = null;
    function overlapSay(text) {
      overlapStatus.textContent = text;
      overlapCopied.textContent = text;
      clearTimeout(copiedTimer);
      copiedTimer = setTimeout(function () { overlapCopied.textContent = ""; }, 2500);
    }
    function copyBySelection() {
      var range = document.createRange();
      range.selectNodeContents(overlapText);
      var selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      var done = false;
      try { done = document.execCommand("copy"); } catch (e) { done = false; }
      overlapSay(done ? "Copied" : "Select the text and copy it");
    }
    // The triangle blinks until the report of these overlaps has been opened once in this
    // browser (blinking must not go on for good); new or changed overlaps blink again.
    var seenKey = overlapOpen.getAttribute("data-overlap-key");  // "2027:<hash of the report>"
    var overlapKey = "nl_overlap_seen_" + seenKey.split(":")[0];  // one memory per year
    try { if (localStorage.getItem(overlapKey) === seenKey) overlapOpen.classList.add("is-seen"); } catch (e) {}
    overlapOpen.hidden = false;
    overlapOpen.addEventListener("click", function () {
      overlapOpen.classList.add("is-seen");
      try { localStorage.setItem(overlapKey, seenKey); } catch (e) {}
      if (typeof overlapDialog.showModal === "function") {
        overlapStatus.textContent = "";
        overlapCopied.textContent = "";
        overlapDialog.showModal();
        overlapDialog.querySelector("[data-overlap-close]").focus();
      } else {
        window.alert(overlapText.textContent);
      }
    });
    overlapDialog.querySelector("[data-overlap-close]").addEventListener("click", function () {
      overlapDialog.close();
    });
    overlapDialog.querySelector("[data-overlap-copy]").addEventListener("click", function () {
      var text = overlapText.textContent;
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function () { overlapSay("Copied"); }, copyBySelection);
      } else {
        copyBySelection();
      }
    });
    // A click on the dimmed page around the dialog closes it too.
    overlapDialog.addEventListener("click", function (e) {
      if (e.target !== overlapDialog) return;
      var r = overlapDialog.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) {
        overlapDialog.close();
      }
    });
    // Esc, Close or the backdrop: the focus goes back to the triangle.
    overlapDialog.addEventListener("close", function () { overlapOpen.focus({ preventScroll: true }); });
  }

  document.querySelectorAll("[data-dismiss-flash]").forEach(function (close) {
    close.hidden = false;
    close.addEventListener("click", function () {
      var flash = close.closest(".flash");
      if (flash.classList.contains("is-closing")) return;
      var list = flash.parentNode;
      // The next message still shown (else the one before) gets the focus afterwards.
      var open = [].filter.call(list.children, function (f) {
        return f !== flash && !f.classList.contains("is-closing");
      });
      var sibling = open.filter(function (f) {
        return flash.compareDocumentPosition(f) & Node.DOCUMENT_POSITION_FOLLOWING;
      })[0] || open[open.length - 1] || null;
      // The last message takes its whole box (and its margin) with it.
      var box = sibling ? flash : list;
      var done = false;
      function finish() {
        if (done) return;
        done = true;
        if (box.parentNode) box.parentNode.removeChild(box);
        if (box !== list && !list.children.length && list.parentNode) list.parentNode.removeChild(list);
        // Focus moves on without scrolling the page: the next message's close button, or the
        // page itself (no outline, see .main:focus).
        if (sibling) {
          sibling.querySelector("[data-dismiss-flash]").focus({ preventScroll: true });
          return;
        }
        var main = document.querySelector("main");
        if (!main) return;
        main.setAttribute("tabindex", "-1");
        main.focus({ preventScroll: true });
      }
      // The message folds away instead of vanishing, so the page below slides up smoothly
      // rather than jumping (skipped for people who ask for reduced motion).
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { finish(); return; }
      flash.classList.add("is-closing");
      box.style.height = box.offsetHeight + "px";
      box.style.overflow = "hidden";
      void box.offsetHeight;
      box.classList.add("is-collapsing");
      box.style.height = "0px";
      box.addEventListener("transitionend", function (e) { if (e.target === box && e.propertyName === "height") finish(); });
      setTimeout(finish, 400);  // in case the transition never ends (hidden tab)
    });
  });
})();
