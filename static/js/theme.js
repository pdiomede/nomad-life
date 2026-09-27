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
  var fold = function (s) {
    return (s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/’/g, "'")
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
      if (items.length) setActive(Math.max(highlight, 0));
    };
    var open = function (filter) {
      var current = exact[fold(input.value)];
      if (!filter || current) {
        // Opening on a chosen country shows the whole list with that country highlighted.
        render(entries, current ? entries.indexOf(current) : 0);
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

    input.addEventListener("input", function () { updateFlag(); render(search(input.value), 0); });
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
  document.querySelectorAll("[data-dismiss-flash]").forEach(function (close) {
    close.hidden = false;
    close.addEventListener("click", function () {
      var flash = close.closest(".flash");
      var list = flash.parentNode;
      var sibling = flash.nextElementSibling || flash.previousElementSibling;
      list.removeChild(flash);
      if (sibling) {
        sibling.querySelector("[data-dismiss-flash]").focus();
        return;
      }
      var main = list.parentNode;
      main.removeChild(list);
      main.setAttribute("tabindex", "-1");
      main.focus();
    });
  });
})();
