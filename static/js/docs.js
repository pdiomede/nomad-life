// Documentation page: search with the "/" shortcut, the contents that follow the reading
// position, and the contents toggle on small screens. Without JavaScript the page and its
// contents links still work; only the search stays hidden.
(function () {
  var box = document.querySelector("[data-docs-search]");
  var input = document.getElementById("docs-q");
  var list = document.getElementById("docs-results");
  var status = document.querySelector("[data-docs-status]");
  var toc = document.querySelector("[data-docs-toc]");
  if (!box || !input || !list) return;
  var MAX_RESULTS = 8;

  // Lower case without accents, keeping a map to the original positions so a match found in
  // the folded text can be marked in the original ("Côte" is found by "cote").
  function fold(text) {
    var out = "", map = [];
    for (var i = 0; i < text.length; i++) {
      var plain = text[i].normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
      for (var j = 0; j < plain.length; j++) { out += plain[j]; map.push(i); }
    }
    map.push(text.length);
    return { text: out, map: map };
  }

  // One entry per section and subsection: its title and its own text (subsections apart).
  var entries = [];
  document.querySelectorAll("[data-doc-title]").forEach(function (el) {
    var copy = el.cloneNode(true);
    copy.querySelectorAll("[data-doc-title], h2, h3, svg, .doc-shot").forEach(function (n) { n.remove(); });
    var text = copy.textContent.replace(/\s+/g, " ").trim();
    var parent = el.parentElement.closest("[data-doc-title]");
    var title = el.getAttribute("data-doc-title");
    entries.push({
      id: el.id, title: title, section: parent ? parent.getAttribute("data-doc-title") : "",
      text: text, foldedTitle: fold(title).text, folded: fold(text)
    });
  });

  var results = [], active = -1;

  function words(query) {
    return fold(query).text.split(/\s+/).filter(Boolean);
  }

  function search(query) {
    var terms = words(query);
    if (!terms.length) return [];
    var found = [];
    entries.forEach(function (e) {
      var score = 0;
      for (var i = 0; i < terms.length; i++) {
        var inTitle = e.foldedTitle.indexOf(terms[i]) !== -1;
        var inText = e.folded.text.indexOf(terms[i]) !== -1;
        if (!inTitle && !inText) return;  // every word must be there
        score += inTitle ? 10 : 1;
      }
      found.push({ entry: e, score: score });
    });
    found.sort(function (a, b) { return b.score - a.score; });
    return found.slice(0, MAX_RESULTS).map(function (f) { return f.entry; });
  }

  // About 90 characters of the text around the first word found, with that word marked.
  function snippet(entry, terms) {
    var f = entry.folded, at = -1, term = "";
    for (var i = 0; i < terms.length && at === -1; i++) { at = f.text.indexOf(terms[i]); term = terms[i]; }
    var li = document.createElement("span");
    li.className = "docs-result-text";
    if (at === -1) { li.textContent = entry.text.slice(0, 90) + (entry.text.length > 90 ? "..." : ""); return li; }
    var start = f.map[at], end = f.map[at + term.length];
    var from = Math.max(0, start - 35), to = Math.min(entry.text.length, end + 55);
    // Start and end on whole words.
    if (from > 0) { var space = entry.text.indexOf(" ", from); if (space !== -1 && space < start) from = space + 1; }
    if (to < entry.text.length) { var last = entry.text.lastIndexOf(" ", to); if (last > end) to = last; }
    li.appendChild(document.createTextNode((from > 0 ? "..." : "") + entry.text.slice(from, start)));
    var mark = document.createElement("mark");
    mark.textContent = entry.text.slice(start, end);
    li.appendChild(mark);
    li.appendChild(document.createTextNode(entry.text.slice(end, to) + (to < entry.text.length ? "..." : "")));
    return li;
  }

  function setActive(i) {
    var items = list.querySelectorAll("[role=option]");
    items.forEach(function (item, n) { item.setAttribute("aria-selected", n === i ? "true" : "false"); });
    active = i;
    if (i >= 0 && items[i]) {
      input.setAttribute("aria-activedescendant", items[i].id);
      items[i].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function close() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    setActive(-1);
  }

  function render() {
    var query = input.value.trim();
    list.textContent = "";
    if (!query) { close(); status.textContent = ""; return; }
    var terms = words(query);
    results = search(query);
    if (!results.length) {
      var none = document.createElement("li");
      none.className = "docs-result-none";
      none.textContent = "No results for “" + query + "”.";
      list.appendChild(none);
    }
    results.forEach(function (entry, n) {
      var li = document.createElement("li");
      li.id = "docs-result-" + n;
      li.setAttribute("role", "option");
      var a = document.createElement("a");
      a.href = "#" + entry.id;
      a.tabIndex = -1;
      var title = document.createElement("span");
      title.className = "docs-result-title";
      title.textContent = entry.title;
      if (entry.section) {
        var where = document.createElement("span");
        where.className = "docs-result-where";
        where.textContent = entry.section;
        title.appendChild(where);
      }
      a.appendChild(title);
      a.appendChild(snippet(entry, terms));
      a.addEventListener("click", function (e) { e.preventDefault(); go(entry.id); });
      li.appendChild(a);
      list.appendChild(li);
    });
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    setActive(results.length ? 0 : -1);
    status.textContent = results.length
      ? results.length + " result" + (results.length === 1 ? "" : "s") + ". Use the up and down arrows, then Enter."
      : "No results.";
  }

  // Open a section: its address changes (so Back returns), it scrolls into view and takes the
  // focus, so the next Tab continues from there.
  function go(id) {
    var target = document.getElementById(id);
    if (!target) return;
    close();
    if (history.pushState) history.pushState(null, "", "#" + id);
    var heading = target.querySelector("h2, h3") || target;
    heading.setAttribute("tabindex", "-1");
    target.scrollIntoView({ block: "start" });
    heading.focus({ preventScroll: true });
    flash(target);
  }

  function flash(target) {
    target.classList.remove("is-found");
    void target.offsetWidth;  // restart the highlight
    target.classList.add("is-found");
  }

  input.addEventListener("input", render);
  input.addEventListener("focus", function () { if (input.value.trim()) render(); });
  input.addEventListener("keydown", function (e) {
    if (e.isComposing) return;  // typing with an input method (Japanese, Chinese)
    var count = results.length;
    if (e.key === "ArrowDown" && count) {
      e.preventDefault();
      if (list.hidden) render(); else setActive((active + 1) % count);
    } else if (e.key === "ArrowUp" && count) {
      e.preventDefault();
      if (!list.hidden) setActive((active - 1 + count) % count);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!list.hidden && count) go(results[Math.max(active, 0)].id);
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (!list.hidden) { close(); }
      else { input.value = ""; status.textContent = ""; input.blur(); }
    }
  });
  // A click outside the search closes the results.
  document.addEventListener("click", function (e) { if (!box.contains(e.target)) close(); });
  box.addEventListener("focusout", function (e) {
    if (!box.contains(e.relatedTarget)) close();
  });

  // "/" anywhere on the page (not while typing in a field) jumps to the search.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
    var el = document.activeElement;
    var tag = el && el.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (el && el.isContentEditable)) return;
    e.preventDefault();
    input.focus();
    input.select();
  });

  box.hidden = false;

  // Contents: the section being read is marked, and on small screens the list folds away
  // behind a Contents button.
  if (!toc) return;
  var links = {};
  toc.querySelectorAll("a[href^='#']").forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
  var toggle = toc.querySelector(".docs-toc-toggle");
  var tocList = document.getElementById("docs-toc-list");
  var small = window.matchMedia("(max-width: 900px)");
  function foldToc() {
    toggle.hidden = !small.matches;
    toc.classList.toggle("is-collapsed", small.matches && toggle.getAttribute("aria-expanded") !== "true");
  }
  if (toggle && tocList) {
    toggle.addEventListener("click", function () {
      toggle.setAttribute("aria-expanded", toggle.getAttribute("aria-expanded") === "true" ? "false" : "true");
      foldToc();
    });
    toc.addEventListener("click", function (e) {
      if (e.target.closest("a") && small.matches) { toggle.setAttribute("aria-expanded", "false"); foldToc(); }
    });
    if (small.addEventListener) small.addEventListener("change", foldToc);
    foldToc();
  }

  // The section being read: the last heading (in page order) above the top quarter of the
  // window, checked once per frame while scrolling.
  var headings = Object.keys(links).map(function (id) {
    var section = document.getElementById(id);
    return { id: id, el: section && section.querySelector("h2, h3") };
  }).filter(function (h) { return h.el; });
  var current = null, queued = false;
  function mark() {
    queued = false;
    var line = window.innerHeight * 0.25, pick = headings.length ? headings[0].id : null;
    for (var i = 0; i < headings.length; i++) {
      if (headings[i].el.getBoundingClientRect().top <= line) pick = headings[i].id; else break;
    }
    if (pick === current) return;
    if (current && links[current]) links[current].removeAttribute("aria-current");
    if (pick) links[pick].setAttribute("aria-current", "location");
    current = pick;
  }
  window.addEventListener("scroll", function () {
    if (!queued) { queued = true; window.requestAnimationFrame(mark); }
  }, { passive: true });
  window.addEventListener("resize", mark);
  mark();
})();
