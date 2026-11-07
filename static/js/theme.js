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

  // The "Show 183 marker" switches of the Days in Base country and Days per Country cards, one
  // setting for both: off hides the marker and its message (.day-marker) through data-marker on
  // <html>, which theme-init.js sets again on the next page before it is drawn. Kept in this
  // browser, for every year; flipping one moves the other too.
  var markerSwitches = document.querySelectorAll("[data-marker-switch]");
  var syncMarkerSwitches = function () {
    var on = root.getAttribute("data-marker") === "off" ? "false" : "true";
    markerSwitches.forEach(function (sw) { sw.setAttribute("aria-checked", on); });
  };
  // A page brought back by the Back button (back/forward cache) keeps the theme and marker it
  // had then, although they may have been changed on another page since: apply the saved ones
  // again, as theme-init.js does on a fresh load.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    var theme = null, off = false;
    try { theme = localStorage.getItem("theme"); off = localStorage.getItem("nl_marker") !== "on"; } catch (x) {}
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme); else root.removeAttribute("data-theme");
    if (off) root.setAttribute("data-marker", "off"); else root.removeAttribute("data-marker");
    syncMarkerSwitches();
  });
  markerSwitches.forEach(function (sw) {
    sw.hidden = false;
    sw.addEventListener("click", function () {
      var off = root.getAttribute("data-marker") !== "off";
      if (off) root.setAttribute("data-marker", "off"); else root.removeAttribute("data-marker");
      try { localStorage.setItem("nl_marker", off ? "off" : "on"); } catch (e) {}
      syncMarkerSwitches();
    });
  });
  syncMarkerSwitches();

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
    if (form.hasAttribute("data-download-reload")) {
      // The answer is a file (the purged Security Activity as CSV), so the page stays: once
      // the server's cookie says the download started, reload to show the result.
      var token = Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
      setHidden("dl", token);
      var started = Date.now();
      var timer = setInterval(function () {
        var m = document.cookie.match(/(?:^|; )nl_download=([^;]*)/);
        if ((m && m[1] === token) || Date.now() - started > 60000) {
          clearInterval(timer);
          document.cookie = "nl_download=; Max-Age=0; path=/; SameSite=Lax";
          // At the top, where the message is: neither the old scroll position nor the
          // #activity anchor may hide it.
          if (window.history && history.replaceState) {
            history.replaceState(null, "", location.pathname + location.search);
            if ("scrollRestoration" in history) history.scrollRestoration = "manual";
          }
          window.scrollTo(0, 0);
          window.location.reload();
        }
      }, 400);
    }
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
      if (dialog.returnValue !== "confirmed" && opener) {
        // A button in a menu (the Security Activity gear) is hidden once the menu closed when
        // the dialog took the focus: back to the menu's trigger, not to nowhere.
        var menu = opener.closest("details:not([open])");
        (menu ? menu.querySelector("summary") : opener).focus();
      }
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
    return { name: c[0], code: c[1], flag: flagOf(c[1]), aliases: c[2] || [],
             keys: [fold(c[0])].concat((c[2] || []).map(fold)) };
  });
  var exact = {};
  [0, 1, 2].forEach(function (pass) {  // names win over codes, codes over aliases
    entries.forEach(function (e) {
      var keys = pass === 0 ? [e.keys[0]] : pass === 1 ? [e.code.toLowerCase()] : e.keys.slice(1);
      keys.forEach(function (k) { if (!(k in exact)) exact[k] = e; });
    });
  });
  // Matches the start of a name, of one of its words, or of an alias (never the middle of a
  // word: "ita" found Mauritania, and United Kingdom through "Britain"). A country found only
  // through an alias gets that alias as its hint, so "eng" shows "United Kingdom (England)".
  var search = function (query) {
    var q = fold(query);
    var all = entries.slice();
    all.hints = [];
    if (!q) return all;
    var scored = [];
    entries.forEach(function (e) {
      var best = e.code.toLowerCase() === q ? 1 : 99, via = "";
      e.keys.forEach(function (k, i) {
        var s = 99, alias = i > 0 ? 1 : 0;
        if (k === q) s = 0 + alias;
        else if (k.indexOf(q) === 0) s = 2 + alias;
        else if ((" " + k).indexOf(" " + q) >= 0) s = 4 + alias;
        if (s < best) { best = s; via = alias ? e.aliases[i - 1] : ""; }
      });
      if (best < 99) scored.push([best, e, via]);
    });
    scored.sort(function (a, b) { return a[0] - b[0] || a[1].name.localeCompare(b[1].name); });
    var found = scored.map(function (x) { return x[1]; });
    found.hints = scored.map(function (x) { return x[2]; });
    return found;
  };

  // Favorite countries (the star in the list), saved with the account (POST /favorites/countries).
  var favData = document.getElementById("country-favorites");
  var favorites = {};
  try { (favData ? JSON.parse(favData.textContent) : []).forEach(function (c) { favorites[c] = true; }); } catch (e) {}
  var favCount = function () { return Object.keys(favorites).length; };

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
    // The popup: two tabs (All countries, Favorites), the list, and a line for messages.
    var panel = document.createElement("div");
    panel.className = "combo-panel";
    panel.hidden = true;
    var tabs = document.createElement("div");
    tabs.className = "combo-tabs";
    var tabAll = document.createElement("button"), tabFav = document.createElement("button");
    [tabAll, tabFav].forEach(function (b) { b.type = "button"; b.className = "combo-tab"; b.tabIndex = -1; tabs.appendChild(b); });
    tabAll.textContent = "All countries";
    var list = document.createElement("ul");
    list.className = "combo-list";
    list.id = listId;
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "Countries");
    var note = document.createElement("p");
    note.className = "combo-note";
    note.setAttribute("role", "status");
    panel.appendChild(tabs); panel.appendChild(list); panel.appendChild(note);
    wrap.appendChild(panel);
    // Live region, always in the DOM so screen readers announce the text when it appears.
    var empty = document.createElement("div");
    empty.className = "combo-empty";
    empty.setAttribute("role", "status");
    var noMatch = "No matching country. You can keep what you typed.";
    wrap.appendChild(empty);
    // Pressing on the popup (its scrollbar, tabs, padding or the empty message) must not move
    // focus away from the input, or the blur handler would close the list mid scroll.
    [panel, empty].forEach(function (el) {
      el.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
    });

    input.removeAttribute("list");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    if (favData) {  // the keyboard ways to the favorites, read with the field
      var keysHelp = document.createElement("span");
      keysHelp.id = listId + "-keys";
      keysHelp.className = "visually-hidden";
      keysHelp.textContent = "In the list, Shift+Enter adds the highlighted country to your favorites or removes it; Alt+Right shows your favorites, Alt+Left all countries.";
      wrap.appendChild(keysHelp);
      input.setAttribute("aria-describedby", ((input.getAttribute("aria-describedby") || "") + " " + keysHelp.id).trim());
    }
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", listId);

    // The year's base country comes first in the list, with a home icon.
    var base = exact[fold(input.getAttribute("data-base-country") || "")] || null;
    var mode = "all", shown = [], active = -1;
    var filtered = false;  // filtered by the typed text, or the whole list (opened on a chosen country)
    var syncTabs = function () {
      tabFav.textContent = "Favorites (" + favCount() + ")";
      tabAll.setAttribute("aria-pressed", mode === "all" ? "true" : "false");
      tabFav.setAttribute("aria-pressed", mode === "fav" ? "true" : "false");
    };
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
      panel.hidden = true; empty.textContent = ""; note.textContent = ""; active = -1;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    };
    // What the list shows: every country or the favorites, filtered by the typed text, with
    // the base first.
    var listFor = function (filter) {
      var found = filter && fold(input.value) ? search(input.value) : entries.slice();
      var hints = found.hints || [];
      var items = [], itemHints = [];
      found.forEach(function (e, i) {
        if (mode === "fav" && !favorites[e.name]) return;
        items.push(e); itemHints.push(hints[i] || "");
      });
      var b = base ? items.indexOf(base) : -1;
      if (b > 0) {
        items.unshift(items.splice(b, 1)[0]);
        itemHints.unshift(itemHints.splice(b, 1)[0]);
      }
      items.hints = itemHints;
      return items;
    };
    var render = function (items, highlight) {
      list.textContent = "";
      shown = items;
      syncTabs();
      items.forEach(function (e, i) {
        var li = document.createElement("li");
        li.id = listId + "-" + i;
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", "false");
        var f = document.createElement("span");
        f.className = "flag"; f.setAttribute("aria-hidden", "true"); f.textContent = e.flag;
        var label = document.createElement("span");
        label.className = "combo-name";
        label.textContent = e.name;
        li.appendChild(f); li.appendChild(label);
        if (e === base) {  // the year's base: a home icon, and "base" for screen readers
          var home = document.createElement("span");
          home.className = "combo-home";
          home.title = "Your base country";
          li.appendChild(home);
          var baseText = document.createElement("span");
          baseText.className = "visually-hidden"; baseText.textContent = ", your base country";
          li.appendChild(baseText);
        }
        var hint = items.hints && items.hints[i];
        if (hint) {  // what the typed text matched: "United Kingdom (England)"
          var via = document.createElement("span");
          via.className = "combo-hint";
          via.textContent = "(" + hint + ")";
          li.appendChild(via);
        }
        if (favData && e !== base) {
          // The star (not on the base, which is always first anyway): mouse and touch;
          // Shift+Enter does the same for the highlighted country.
          var on = !!favorites[e.name];
          var star = document.createElement("button");
          star.type = "button"; star.tabIndex = -1;
          star.className = "combo-star";
          star.setAttribute("aria-hidden", "true");
          star.setAttribute("aria-pressed", on ? "true" : "false");
          star.title = on ? "Remove from favorites" : "Add to favorites";
          star.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
          star.addEventListener("click", function (ev) { ev.stopPropagation(); toggleFavorite(e); });
          li.appendChild(star);
          if (on) {
            var favText = document.createElement("span");
            favText.className = "visually-hidden"; favText.textContent = ", favorite";
            li.appendChild(favText);
          }
        }
        li.addEventListener("mousedown", function (ev) { ev.preventDefault(); });  // keep focus in the input
        li.addEventListener("click", function () { choose(e); });
        list.appendChild(li);
      });
      var favEmpty = mode === "fav" && !items.length;
      panel.hidden = items.length === 0 && !favEmpty;
      list.hidden = items.length === 0;
      note.textContent = favEmpty ? (favCount() ? "No favorite matches what you typed."
                                                : "No favorite countries yet: press the star next to a country.") : "";
      empty.textContent = panel.hidden ? noMatch : "";
      input.setAttribute("aria-expanded", panel.hidden ? "false" : "true");
      active = -1;
      // The old options are gone: never leave the input pointing at one of them.
      input.removeAttribute("aria-activedescendant");
      if (items.length && highlight >= 0) setActive(Math.min(highlight, items.length - 1));
    };
    var open = function (filter) {
      var current = exact[fold(input.value)];
      filtered = !!filter && !current;
      if (!filter || current) {
        // Opening on a chosen country shows the whole list with that country highlighted;
        // otherwise nothing is, so Enter still submits a free text place as typed.
        var items = listFor(false);
        render(items, current ? items.indexOf(current) : -1);
      } else {
        render(listFor(true), 0);
      }
    };
    var refresh = function () {  // the same view again (tab, star), keeping the highlight
      var keep = active >= 0 ? shown[active] : null;
      var items = listFor(filtered);  // a star or a tab keeps the whole list when it showed it
      render(items, keep ? items.indexOf(keep) : (filtered && fold(input.value) ? 0 : -1));
    };
    var setMode = function (m) {
      mode = m;
      refresh();
      if (!list.hidden) list.scrollTop = 0;
    };
    tabAll.addEventListener("click", function () { setMode("all"); });
    tabFav.addEventListener("click", function () { setMode("fav"); });
    var saving = 0;  // the newest save: an older answer arriving late must not undo it
    var toggleFavorite = function (e) {
      if (!favData) return;
      var adding = !favorites[e.name], mine = ++saving;
      if (adding) favorites[e.name] = true; else delete favorites[e.name];
      refresh();
      var body = new FormData();
      body.append("csrf_token", favData.getAttribute("data-csrf"));
      body.append("action", adding ? "add" : "remove");
      body.append("country", e.name);
      var undo = function (text) {
        if (mine !== saving) return;
        if (adding) delete favorites[e.name]; else favorites[e.name] = true;
        if (!panel.hidden) refresh();
        note.textContent = text;
      };
      fetch(favData.getAttribute("data-url"), { method: "POST", body: body, credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok) { undo(res.d.error || "The favorite could not be saved."); return; }
          if (mine !== saving) return;
          favorites = {};
          (res.d.favorites || []).forEach(function (c) { favorites[c] = true; });
          if (!panel.hidden) refresh();
        })
        .catch(function () { undo("The favorite could not be saved. Please try again."); });
    };
    var choose = function (e) {
      input.value = e.name;
      updateFlag();
      close();
      input.focus();
    };

    input.addEventListener("input", function () {
      updateFlag();
      filtered = true;
      // An emptied field lists every country with none highlighted: Enter then submits.
      render(listFor(true), fold(input.value) ? 0 : -1);
    });
    input.addEventListener("click", function () { if (panel.hidden) open(false); });
    input.addEventListener("keydown", function (e) {
      var isOpen = !panel.hidden;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!isOpen) { open(true); return; }
        if (!shown.length) return;
        var step = e.key === "ArrowDown" ? 1 : -1;
        setActive((active + step + shown.length) % shown.length);
      } else if (e.key === "Enter" && e.shiftKey) {
        // Shift+Enter stars or unstars the highlighted country.
        if (isOpen && active >= 0 && shown[active]) {
          e.preventDefault();
          if (shown[active] !== base) toggleFavorite(shown[active]);
        }
      } else if (e.key === "Enter") {
        if (isOpen && active >= 0 && shown[active]) { e.preventDefault(); choose(shown[active]); }
      } else if (isOpen && e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
        e.preventDefault();  // Alt+Left and Alt+Right switch between All countries and Favorites
        setMode(e.key === "ArrowRight" ? "fav" : "all");
      } else if (e.key === "Escape") {
        if (isOpen || empty.textContent) { e.preventDefault(); close(); }
      } else if (e.key === "Tab") {
        close();
      }
    });
    input.addEventListener("blur", function () { close(); });
    updateFlag();
  });

  // Receipt type lists (select[data-select-menu]): a button and a styled list with an icon per
  // type, in the country picker's look. The real <select> stays in the form, hidden and kept in
  // step, so the form posts the same value and works without JavaScript.
  document.querySelectorAll("select[data-select-menu]").forEach(function (select, n) {
    var wrap = document.createElement("div");
    wrap.className = "select-menu";
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "select-btn";
    btn.id = select.id + "-menu";
    var listId = "select-list-" + n;
    btn.setAttribute("role", "combobox");
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", listId);
    var icon = document.createElement("span"), text = document.createElement("span");
    icon.setAttribute("aria-hidden", "true");
    text.className = "select-text";
    btn.appendChild(icon); btn.appendChild(text);
    var list = document.createElement("ul");
    list.className = "combo-list select-list";
    list.id = listId;
    list.setAttribute("role", "listbox");
    list.hidden = true;
    var label = select.id ? document.querySelector('label[for="' + select.id + '"]') : null;
    if (label) {  // the label now names the button (and a click on it opens the list)
      label.htmlFor = btn.id;
      if (!label.id) label.id = btn.id + "-label";
      list.setAttribute("aria-labelledby", label.id);
    }
    select.hidden = true;
    wrap.insertBefore(btn, select);
    wrap.appendChild(list);
    var options = [].slice.call(select.options), active = -1;
    options.forEach(function (o, i) {
      var li = document.createElement("li");
      li.id = listId + "-" + i;
      li.setAttribute("role", "option");
      var oi = document.createElement("span");
      oi.className = "kind-icon kind-" + o.value;
      oi.setAttribute("aria-hidden", "true");
      var ot = document.createElement("span");
      ot.textContent = o.textContent;
      li.appendChild(oi); li.appendChild(ot);
      li.addEventListener("mousedown", function (ev) { ev.preventDefault(); });  // keep focus on the button
      li.addEventListener("click", function () { choose(i); });
      list.appendChild(li);
    });
    var sync = function () {  // the button shows the select's value
      var o = select.options[select.selectedIndex];
      icon.className = "kind-icon kind-" + (o ? o.value : "");
      text.textContent = o ? o.textContent : "";
      btn.disabled = select.disabled;
      [].forEach.call(list.children, function (li, i) { li.setAttribute("aria-selected", i === select.selectedIndex ? "true" : "false"); });
    };
    var setActive = function (i) {
      var items = list.children;
      if (active >= 0 && items[active]) items[active].classList.remove("is-active");
      active = i;
      if (i >= 0 && items[i]) {
        items[i].classList.add("is-active");
        btn.setAttribute("aria-activedescendant", items[i].id);
        items[i].scrollIntoView({ block: "nearest" });
      } else {
        btn.removeAttribute("aria-activedescendant");
      }
    };
    var open = function () {
      list.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      setActive(Math.max(select.selectedIndex, 0));
    };
    var close = function () {
      list.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      setActive(-1);
    };
    var choose = function (i) {
      if (select.selectedIndex !== i) {
        select.selectedIndex = i;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      sync();
      close();
      btn.focus();
    };
    btn.addEventListener("click", function () { if (list.hidden) open(); else close(); });
    btn.addEventListener("keydown", function (e) {
      var isOpen = !list.hidden, last = options.length - 1;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!isOpen) { open(); return; }
        setActive(e.key === "ArrowDown" ? Math.min(active + 1, last) : Math.max(active - 1, 0));
      } else if ((e.key === "Home" || e.key === "End") && isOpen) {
        e.preventDefault();
        setActive(e.key === "Home" ? 0 : last);
      } else if (e.key === "Enter" || e.key === " ") {
        if (isOpen) { e.preventDefault(); if (active >= 0) choose(active); }
        // closed: the button's own click opens it
      } else if (e.key === "Escape" && isOpen) {
        e.preventDefault();
        close();
      } else if (e.key === "Tab" && isOpen) {
        close();
      }
    });
    btn.addEventListener("blur", close);
    // Safari and Firefox do not focus a clicked button, so no blur comes: a click outside closes.
    document.addEventListener("mousedown", function (e) { if (!list.hidden && !wrap.contains(e.target)) close(); });
    select.addEventListener("change", sync);  // set from elsewhere (a reset, a script)
    sync();
  });

  // Date fields (input[data-date-picker], From and To of a movement): a button that opens a
  // month calendar in the app's look instead of the browser's own. The real input stays in the
  // form, hidden and kept in step, so the posted value (YYYY-MM-DD) is the same, and without
  // JavaScript the native date field works.
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December"];
  var DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  var pad2 = function (n) { return (n < 10 ? "0" : "") + n; };
  var isoOf = function (d) { return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()); };
  var dateOf = function (s) {
    return /^\d{4}-\d{2}-\d{2}$/.test(s || "") ? new Date(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) : null;
  };
  var weekday = function (d) { return (d.getDay() + 6) % 7; };  // Monday 0 ... Sunday 6
  var labelOf = function (d) {  // "Thu 11 Jun 2026"
    return DAYS[weekday(d)].slice(0, 3) + " " + d.getDate() + " " + MONTHS[d.getMonth()].slice(0, 3) + " " + d.getFullYear();
  };
  var addDays = function (d, n) { return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); };
  var addMonths = function (d, n) {  // the same day, or the month's last when it has fewer
    var last = new Date(d.getFullYear(), d.getMonth() + n + 1, 0).getDate();
    return new Date(d.getFullYear(), d.getMonth() + n, Math.min(d.getDate(), last));
  };
  document.querySelectorAll("input[data-date-picker]").forEach(function (input) {
    var min = dateOf(input.min), max = dateOf(input.max);
    var inRange = function (d) { return (!min || d >= min) && (!max || d <= max); };
    var clamp = function (d) { return min && d < min ? min : max && d > max ? max : d; };
    var wrap = document.createElement("div");
    wrap.className = "date-picker";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "date-btn";
    btn.id = input.id + "-picker";
    btn.setAttribute("aria-haspopup", "dialog");
    btn.setAttribute("aria-expanded", "false");
    var icon = document.createElement("span"), text = document.createElement("span");
    icon.className = "date-icon"; icon.setAttribute("aria-hidden", "true");
    text.className = "date-text"; text.id = btn.id + "-text";
    btn.appendChild(icon); btn.appendChild(text);
    var label = document.querySelector('label[for="' + input.id + '"]');
    if (label) {  // the button is named by the label and its date: "To, Thu 11 Jun 2026"
      label.htmlFor = btn.id;
      if (!label.id) label.id = btn.id + "-label";
      btn.setAttribute("aria-labelledby", label.id + " " + text.id);
    }
    var error = document.createElement("p");
    error.className = "date-error";
    error.id = btn.id + "-error";
    error.hidden = true;
    var pop = document.createElement("div");
    pop.className = "date-pop";
    pop.hidden = true;
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", "Choose a date");
    var head = document.createElement("div");
    head.className = "date-head";
    var prev = document.createElement("button"), next = document.createElement("button");
    var title = document.createElement("span");
    prev.type = next.type = "button";
    prev.className = next.className = "date-nav";
    prev.setAttribute("aria-label", "Previous month");
    next.setAttribute("aria-label", "Next month");
    prev.innerHTML = '<span aria-hidden="true">&lsaquo;</span>';
    next.innerHTML = '<span aria-hidden="true">&rsaquo;</span>';
    title.className = "date-title"; title.id = btn.id + "-title";
    title.setAttribute("aria-live", "polite");
    head.appendChild(prev); head.appendChild(title); head.appendChild(next);
    var grid = document.createElement("table");
    grid.className = "date-grid";
    grid.setAttribute("role", "grid");
    grid.setAttribute("aria-labelledby", title.id);
    var thead = document.createElement("thead"), hrow = document.createElement("tr");
    DAYS.forEach(function (name) {
      var th = document.createElement("th");
      th.scope = "col";
      th.innerHTML = '<abbr title="' + name + '">' + name.slice(0, 2) + "</abbr>";
      hrow.appendChild(th);
    });
    thead.appendChild(hrow); grid.appendChild(thead);
    var tbody = document.createElement("tbody");
    grid.appendChild(tbody);
    var foot = document.createElement("div");
    foot.className = "date-foot";
    var todayBtn = document.createElement("button");
    todayBtn.type = "button"; todayBtn.className = "date-today-btn"; todayBtn.textContent = "Today";
    foot.appendChild(todayBtn);
    pop.appendChild(head); pop.appendChild(grid); pop.appendChild(foot);
    // The field's own "required": a hidden required input would stop the form with no message.
    var required = input.required;
    input.required = false;
    input.hidden = true;
    wrap.insertBefore(btn, input);
    wrap.appendChild(pop);
    wrap.appendChild(error);

    var focusDay = null;  // the day the keyboard is on, and so the month shown
    var sync = function () {
      var d = dateOf(input.value);
      text.textContent = d ? labelOf(d) : "Choose a date";
      btn.classList.toggle("is-empty", !d);
      if (d) { error.hidden = true; btn.removeAttribute("aria-invalid"); btn.removeAttribute("aria-describedby"); }
    };
    var render = function () {
      var y = focusDay.getFullYear(), m = focusDay.getMonth();
      title.textContent = MONTHS[m] + " " + y;
      // aria-disabled, not disabled: a disabled button would drop the focus to the page.
      prev.setAttribute("aria-disabled", !!min && new Date(y, m, 0) < min ? "true" : "false");
      next.setAttribute("aria-disabled", !!max && new Date(y, m + 1, 1) > max ? "true" : "false");
      var chosen = input.value, today = isoOf(new Date());
      tbody.textContent = "";
      var first = new Date(y, m, 1), row = document.createElement("tr");
      for (var b = 0; b < weekday(first); b++) row.appendChild(document.createElement("td"));
      for (var d = first; d.getMonth() === m; d = addDays(d, 1)) {
        if (row.children.length === 7) { tbody.appendChild(row); row = document.createElement("tr"); }
        var td = document.createElement("td"), day = document.createElement("button");
        var iso = isoOf(d);
        td.setAttribute("role", "gridcell");
        day.type = "button";
        day.className = "date-day";
        day.textContent = d.getDate();
        day.setAttribute("data-date", iso);
        day.setAttribute("aria-label", DAYS[weekday(d)] + " " + d.getDate() + " " + MONTHS[m] + " " + y);
        day.tabIndex = iso === isoOf(focusDay) ? 0 : -1;
        if (iso === chosen) { td.setAttribute("aria-selected", "true"); day.classList.add("is-chosen"); }
        if (iso === today) { day.setAttribute("aria-current", "date"); day.classList.add("is-today"); }
        if (!inRange(d)) { day.setAttribute("aria-disabled", "true"); day.classList.add("is-off"); }
        td.appendChild(day);
        row.appendChild(td);
      }
      tbody.appendChild(row);
      todayBtn.hidden = !inRange(dateOf(today));
    };
    var focusOn = function (d) {
      focusDay = clamp(d);
      render();
      var day = tbody.querySelector('[data-date="' + isoOf(focusDay) + '"]');
      if (day) day.focus();
    };
    var open = function () {
      var start = null;
      if (!input.value && input.id === "end_date") start = dateOf((document.getElementById("start_date") || {}).value);
      var today = new Date(); today = new Date(today.getFullYear(), today.getMonth(), today.getDate());
      pop.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      // The chosen day, or From's day for an empty To, or today in this year, or its first day.
      focusOn(dateOf(input.value) || start || (inRange(today) ? today : min || today));
    };
    var close = function (back) {
      if (pop.hidden) return;
      pop.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      if (back) btn.focus();
    };
    var choose = function (d) {
      if (!inRange(d)) return;
      input.value = isoOf(d);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      sync();
      close(true);
    };
    btn.addEventListener("click", function () { if (pop.hidden) open(); else close(false); });
    btn.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" && pop.hidden) { e.preventDefault(); open(); }
    });
    prev.addEventListener("click", function () {
      if (prev.getAttribute("aria-disabled") === "true") return;
      focusDay = clamp(addMonths(focusDay, -1)); render();
    });
    next.addEventListener("click", function () {
      if (next.getAttribute("aria-disabled") === "true") return;
      focusDay = clamp(addMonths(focusDay, 1)); render();
    });
    // A click on the title, the weekdays or the padding keeps the focus where it is.
    pop.addEventListener("mousedown", function (e) { if (!e.target.closest("button")) e.preventDefault(); });
    todayBtn.addEventListener("click", function () { choose(new Date(new Date().setHours(0, 0, 0, 0))); });
    tbody.addEventListener("click", function (e) {
      var day = e.target.closest(".date-day");
      if (day) choose(dateOf(day.getAttribute("data-date")));
    });
    tbody.addEventListener("keydown", function (e) {
      var keys = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
      if (e.key in keys) { e.preventDefault(); focusOn(addDays(focusDay, keys[e.key])); }
      else if (e.key === "PageUp" || e.key === "PageDown") { e.preventDefault(); focusOn(addMonths(focusDay, e.key === "PageUp" ? -1 : 1)); }
      else if (e.key === "Home") { e.preventDefault(); focusOn(addDays(focusDay, -weekday(focusDay))); }
      else if (e.key === "End") { e.preventDefault(); focusOn(addDays(focusDay, 6 - weekday(focusDay))); }
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(focusDay); }
    });
    wrap.addEventListener("keydown", function (e) {  // also from the date button
      if (e.key === "Escape" && !pop.hidden) { e.preventDefault(); e.stopPropagation(); close(true); }
    });
    // Closes on a click outside, or when the focus leaves the field and its calendar.
    document.addEventListener("mousedown", function (e) { if (!wrap.contains(e.target)) close(false); });
    wrap.addEventListener("focusout", function (e) {
      if (e.relatedTarget && !wrap.contains(e.relatedTarget)) close(false);
    });
    input.addEventListener("change", sync);  // set from elsewhere (a script)
    if (required && input.form) {
      input.form.addEventListener("submit", function (e) {
        if (input.value) return;
        e.preventDefault();
        error.textContent = "Please choose a date.";
        error.hidden = false;
        btn.setAttribute("aria-invalid", "true");
        btn.setAttribute("aria-describedby", error.id);
        // Both dates empty: the focus goes to the first one (From), not the last checked.
        if (input.form.querySelector('.date-btn[aria-invalid="true"]') === btn) btn.focus();
      });
    }
    sync();
  });

  // Downloads (accountant package): show a busy state and block double clicks until the
  // server answers. The response sets a cookie with the token we sent, which ends the wait.
  document.querySelectorAll("form[data-download]").forEach(function (form) {
    var btn = form.querySelector("[type=submit]");
    var label = btn ? btn.innerHTML : "";  // with its icon, put back after "Preparing package..."
    var name = btn ? btn.getAttribute("aria-label") : null;  // its spoken name, put back too
    var timer = null;
    var readCookie = function () {
      var m = document.cookie.match(/(?:^|; )nl_download=([^;]*)/);
      return m ? m[1] : "";
    };
    var done = function () {
      clearInterval(timer);
      document.cookie = "nl_download=; Max-Age=0; path=/; SameSite=Lax";
      if (btn) { btn.removeAttribute("aria-disabled"); btn.innerHTML = label; btn.removeAttribute("aria-busy"); if (name) btn.setAttribute("aria-label", name); }
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
          if (name) btn.setAttribute("aria-label", "Preparing package...");  // the label would hide the text
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

  // Calendar card tabs (with a Google Calendar connected): Nomad Life and Google Calendar.
  // They are links (?cal=google renders the Google view on the server); here a click swaps
  // the two views in place, opens the card when it is folded, and loads the Google view once
  // from its fragment URL. The address keeps no ?cal, so a reload never waits on Google. A
  // failed load follows the link instead, if the Google tab is still the one chosen.
  document.querySelectorAll("[data-cal-tabs]").forEach(function (nav) {
    var card = document.querySelector(".cal-card");
    var local = card && card.querySelector('[data-cal-panel="local"]');
    var google = card && card.querySelector('[data-cal-panel="google"]');
    if (!local || !google) return;
    var tabs = nav.querySelectorAll("[data-cal-tab]");
    var googleTab = nav.querySelector('[data-cal-tab="google"]');
    var loaded = google.children.length > 0;
    function load() {
      loaded = true;
      google.setAttribute("aria-busy", "true");
      google.textContent = "";
      var note = document.createElement("p");
      note.className = "muted small cal-source";
      note.textContent = "Loading Google Calendar\u2026";
      google.appendChild(note);
      fetch(googleTab.getAttribute("data-src"), { credentials: "same-origin", headers: { Accept: "text/html" } })
        .then(function (r) {
          if (!r.ok || r.redirected) throw new Error("not loaded");  // a sign in page, an error
          return r.text();
        })
        .then(function (html) { google.innerHTML = html; google.removeAttribute("aria-busy"); })
        .catch(function () {
          if (googleTab.getAttribute("aria-current") === "page") {
            window.location.href = googleTab.href;
          } else {
            loaded = false;  // Nomad Life was chosen meanwhile: load again on the next click
            google.textContent = "";
            google.removeAttribute("aria-busy");
          }
        });
    }
    nav.addEventListener("click", function (e) {
      var tab = e.target.closest("[data-cal-tab]");
      if (!tab || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      var which = tab.getAttribute("data-cal-tab");
      tabs.forEach(function (t) {
        if (t === tab) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
      });
      card.open = true;
      local.hidden = which !== "local";
      google.hidden = which !== "google";
      try {
        var url = new URL(window.location.href);
        url.searchParams.delete("cal");
        history.replaceState(history.state, "", url.pathname + url.search + url.hash);
      } catch (err) { /* old browsers: the address stays as it was */ }
      if (which === "google" && !loaded) load();
    });
    // "Try again" in the Google view, after Google did not answer.
    google.addEventListener("click", function (e) {
      var retry = e.target.closest("[data-cal-retry]");
      if (!retry || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      load();
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
    // The card starts folded, where the map has no size: place the pins once it opens.
    card.addEventListener("toggle", function () {
      if (!card.open) return;
      var w = map.clientWidth;
      if (last && w !== last) { tx *= w / last; ty *= w / last; }
      last = w;
      render();
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
      } else if (file && left === 0) {
        // Receipts are stored as ZIP files (compressed, big photos resized), so only the server
        // can tell whether one fits; here a file is refused only when no space is left at all.
        error = "Not enough storage left for " + file.name + " (" + input.getAttribute("data-left-label") + " free).";
      }
      if (error) input.value = "";
      if (label) {
        label.textContent = error || (file ? file.name : "No file selected");
        label.classList.toggle("file-error", !!error);
      }
    });
  });

  // Menus made of a <details> (the user menu in the header, the Security Activity gear), so
  // they also work without JavaScript: close on a click outside, on Escape (back to the
  // trigger), when focus moves out, and after choosing a download in it.
  document.querySelectorAll("[data-user-menu], [data-menu]").forEach(function (menu) {
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
    menu.querySelectorAll("[data-menu-close]").forEach(function (link) {
      link.addEventListener("click", function () { menu.open = false; });
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
