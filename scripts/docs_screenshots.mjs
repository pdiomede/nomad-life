// Takes the documentation screenshots; run through scripts/docs_screenshots.py, which starts a
// demo copy of the app and passes its address. Drives the installed Google Chrome headless over
// the DevTools protocol with Node's built in WebSocket (Node 22+), so nothing is installed.
//
// Each shot opens a page, stages what the text describes (a list open, a dialog on step 2, a
// tooltip, numbered markers), then captures only the part of the page it is about, at twice the
// resolution, in the light and the dark theme.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const opts = JSON.parse(process.argv[2]);
const CHROME = [
  process.env.CHROME,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
].find((p) => p && existsSync(p));
if (!CHROME) throw new Error("Google Chrome not found; set CHROME to its path.");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Numbered markers and a ring around the control a step talks about, drawn over the page.
const CALLOUT = `window.__callout = function (sel, n, side) {
  var el = document.querySelector(sel); if (!el) throw new Error("no " + sel);
  var r = el.getBoundingClientRect();
  el.style.outline = "3px solid #E0317E"; el.style.outlineOffset = "3px";
  var b = document.createElement("span"); b.textContent = n;
  var x = side === "right" ? r.right + 12 : r.left - 15;  // "right": beside a text link
  Object.assign(b.style, { position: "absolute", left: (x + scrollX) + "px",
    top: (r.top + scrollY + (side === "right" ? r.height / 2 - 13 : -15)) + "px", width: "26px", height: "26px", borderRadius: "50%",
    background: "#E0317E", color: "#fff", font: "700 14px Inter, system-ui, sans-serif",
    display: "grid", placeItems: "center", zIndex: 99999,
    boxShadow: "0 0 0 3px #fff, 0 2px 8px rgba(0,0,0,.35)" });
  document.body.appendChild(b);
};
window.__type = function (sel, value) {
  var el = document.querySelector(sel); el.focus(); el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
};
window.__target = function (el) { el.id = el.id || "shot-target"; return "#" + el.id; };`;

const SHOTS = [
  { name: "year-new", path: "/year/new", select: [".narrow"], prep: `
      __type("#year", "2027"); __type("#base_country", "Portugal"); __type("#base_city", "Lisbon");
      document.activeElement.blur();` },
  { name: "movement-new", path: "/year/2026/movements/new", wait: 400,
    select: ["main section.card", "[role=listbox]:not([hidden])"], prep: `
      __type("#city", "Rome"); __type("#start_date", "2026-03-06"); __type("#end_date", "2026-03-08");
      __callout("#end_date", 2); __callout("main form button[type=submit]", 3);
      __callout("#country", 1); __type("#country", "ita");` },
  { name: "dashboard", path: "/year/2026", select: [".page-head", ".stats"], prep: `
      __callout(".year-tab.add", 1); __callout(".page-head .btn-primary", 2);
      var link = document.querySelector(".stat-base a.small"); link.style.display = "inline-block";
      __callout(".stat-base a.small", 3, "right");` },
  { name: "movements-table", path: "/year/2026", select: ["#movements"] },
  { name: "map", path: "/year/2026", wait: 500, select: [".map-card"], hover: '.map-pin[aria-label^="Italy"]' },
  { name: "delete-dialog", path: "/year/2026", wait: 900, pad: 28, select: ["#confirm-dialog"], prep: `
      document.querySelector("#movements form[data-confirm-delete] button").click();
      await new Promise(r => setTimeout(r, 300));
      document.getElementById("confirm-next").click();` },
  { name: "receipts", path: "/movements/1", files: { "#file-mv": "sample" }, select: ["#shot-target"], prep: `
      __target(document.getElementById("file-mv").closest("section"));
      var kind = document.getElementById("kind-mv"); kind.value = "flight";
      kind.dispatchEvent(new Event("change", { bubbles: true }));` },
  { name: "package", path: "/year/2026", select: [".storage-card", ".package-card"] },
  { name: "two-factor", path: "/settings", select: ["#two-factor"], prep: `
      // A demo account's key, blurred so nobody adds it to an app by mistake.
      document.querySelector(".tfa-qr").style.filter = "blur(2.5px)";
      document.querySelector(".tfa-key").style.filter = "blur(3px)";` },
  { name: "support-ticket", path: "/support/2026-1", select: ["#shot-target"], prep: `
      __target(document.getElementById("conversation-title").closest("section"));` },
].filter((s) => !opts.only.length || opts.only.includes(s.name));

class Cdp {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.id = 0; this.pending = new Map(); this.waiters = [];
    this.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
      } else if (msg.method) {
        this.waiters = this.waiters.filter((w) => (w.method === msg.method ? (w.resolve(msg.params), false) : true));
      }
    };
  }
  open() { return new Promise((resolve, reject) => { this.ws.onopen = resolve; this.ws.onerror = reject; }); }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  once(method) { return new Promise((resolve) => this.waiters.push({ method, resolve })); }
  async eval(expression) {
    const res = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (res.exceptionDetails) throw new Error(res.exceptionDetails.exception?.description || res.exceptionDetails.text);
    return res.result.value;
  }
  async go(url) {
    const loaded = this.once("Page.loadEventFired");
    await this.send("Page.navigate", { url });
    await loaded;
    await this.eval("document.fonts.ready.then(() => true)");
  }
}

async function launch() {
  const dir = mkdtempSync(join(tmpdir(), "nl-docs-chrome-"));
  const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${dir}`,
    "--no-first-run", "--no-default-browser-check", "--hide-scrollbars", "--window-size=1200,900",
    "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  const wsUrl = await new Promise((resolve, reject) => {
    let buf = "";
    chrome.stderr.on("data", (d) => {
      buf += d;
      const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
      if (m) resolve(m[1]);
    });
    chrome.on("exit", () => reject(new Error("Chrome exited: " + buf)));
  });
  const port = new URL(wsUrl).port;
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = new Cdp(pages.find((p) => p.type === "page").webSocketDebuggerUrl);
  await page.open();
  return { page, close: () => { chrome.kill(); setTimeout(() => rmSync(dir, { recursive: true, force: true }), 500); } };
}

async function shoot(page, shot, theme) {
  await page.go(opts.base + shot.path);
  await page.eval(`document.querySelectorAll(".flashes").forEach(e => e.remove()); ${CALLOUT} true`);
  if (shot.files) {
    const { root } = await page.send("DOM.getDocument", {});
    for (const [sel, which] of Object.entries(shot.files)) {
      const { nodeId } = await page.send("DOM.querySelector", { nodeId: root.nodeId, selector: sel });
      await page.send("DOM.setFileInputFiles", { nodeId, files: [opts[which]] });
    }
  }
  if (shot.prep) await page.eval(`(async () => { ${shot.prep} })().then(() => true)`);
  if (shot.hover) {
    // The pointer over an element, for what shows on hover (a map pin's tooltip).
    const at = await page.eval(`(() => { var el = document.querySelector(${JSON.stringify(shot.hover)});
      el.scrollIntoView({ block: "center" }); var r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
    await sleep(200);
    await page.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: at.x, y: at.y });
  }
  await sleep(shot.wait || 250);
  const pad = shot.pad ?? 16;
  const box = await page.eval(`(() => {
    var r = null;
    ${JSON.stringify(shot.select)}.forEach(function (sel) {
      document.querySelectorAll(sel).forEach(function (el) {
        var b = el.getBoundingClientRect();
        if (!b.width || !b.height) return;
        var x = b.left + scrollX, y = b.top + scrollY;
        r = r ? { x1: Math.min(r.x1, x), y1: Math.min(r.y1, y), x2: Math.max(r.x2, x + b.width), y2: Math.max(r.y2, y + b.height) }
              : { x1: x, y1: y, x2: x + b.width, y2: y + b.height };
      });
    });
    return r;
  })()`);
  if (!box) throw new Error(`${shot.name}: nothing matches ${shot.select}`);
  const clip = { x: Math.max(0, box.x1 - pad), y: Math.max(0, box.y1 - pad),
                 width: box.x2 - box.x1 + 2 * pad, height: box.y2 - box.y1 + 2 * pad, scale: 1 };
  const { data } = await page.send("Page.captureScreenshot", { format: "webp", quality: 86, clip, captureBeyondViewport: true });
  const file = join(opts.out, `${shot.name}-${theme}.webp`);
  writeFileSync(file, Buffer.from(data, "base64"));
  console.log(`  ${shot.name}-${theme}.webp  ${Math.round(clip.width)}x${Math.round(clip.height)}`);
}

const { page, close } = await launch();
try {
  await page.send("Page.enable");
  await page.send("Runtime.enable");
  await page.send("DOM.enable");
  await page.send("Emulation.setDeviceMetricsOverride", { width: 1120, height: 900, deviceScaleFactor: 2, mobile: false });
  await page.go(opts.base + "/login");
  await page.eval(`(() => {
    document.querySelector("[name=email]").value = ${JSON.stringify(opts.email)};
    document.querySelector("[name=password]").value = ${JSON.stringify(opts.password)};
    return true; })()`);
  const loaded = page.once("Page.loadEventFired");
  await page.eval(`document.querySelector("[name=password]").form.submit(); true`);
  await loaded;
  for (const theme of ["light", "dark"]) {
    // The saved theme, as the theme button stores it, and the matching system setting.
    await page.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
    await page.eval(`localStorage.setItem("theme", ${JSON.stringify(theme)}); true`);
    for (const shot of SHOTS) await shoot(page, shot, theme);
  }
} finally {
  close();
}
