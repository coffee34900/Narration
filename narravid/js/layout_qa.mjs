// Layout QA before rendering: for every scene, seek its final state in headless Chrome,
// measure every visible text box (.tx) and report
//   overlap (two text boxes intersect), outside safe area, inside caption zone,
//   overflow (content larger than its box), font smaller than the minimum,
//   missing "schematic" label where one is required.
// Also saves a screenshot of each scene's final state (thumbnails / contact sheets).
// stdin JSON: {root, chrome, out_dir, safe:{left,right,top,bottom}, caption_top, min_px,
//              chunks:[{dir, comp_id, scenes:[{id, t, schematic}]}]}
import { createRequire } from "module";
import http from "http";
import fs from "fs";
import path from "path";
const require = createRequire(process.cwd() + "/");
const puppeteer = require("puppeteer-core");

const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2",
  ".wav": "audio/wav", ".png": "image/png", ".svg": "image/svg+xml" };

let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => main(JSON.parse(input)).catch((e) => { console.error(e.stack || e); process.exit(1); }));

async function main(cfg) {
  const server = http.createServer((req, res) => {
    const p = path.join(cfg.root, decodeURIComponent(req.url.split("?")[0]));
    if (!p.startsWith(cfg.root) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end(); }
    if (p.endsWith(".wav")) { res.writeHead(204); return res.end(); }   // audio not needed for layout
    res.writeHead(200, { "Content-Type": MIME[path.extname(p)] || "application/octet-stream" });
    fs.createReadStream(p).pipe(res);
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const port = server.address().port;
  const browser = await puppeteer.launch({ executablePath: cfg.chrome, headless: true,
    args: ["--no-sandbox", "--disable-gpu", "--font-render-hinting=none"] });
  const out = { scenes: {} };
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
    fs.mkdirSync(cfg.out_dir, { recursive: true });
    for (const ch of cfg.chunks) {
      await page.goto(`http://127.0.0.1:${port}/${ch.dir}/index.html`, { waitUntil: "load", timeout: 120000 });
      await page.evaluate(() => document.fonts.ready);
      for (const sc of ch.scenes) {
        const res = await page.evaluate(measure, ch.comp_id, sc, cfg);
        await page.screenshot({ path: path.join(cfg.out_dir, `${sc.id}.jpg`), type: "jpeg", quality: 78 });
        out.scenes[sc.id] = res;
      }
    }
  } finally {
    await browser.close();
    server.close();
  }
  process.stdout.write(JSON.stringify(out));
}

// Runs inside the page.
function measure(compId, sc, cfg) {
  const t = sc.t;
  document.querySelectorAll(".clip").forEach((el) => {
    const s = parseFloat(el.dataset.start), d = parseFloat(el.dataset.duration);
    el.style.display = t >= s && t < s + d ? "" : "none";
  });
  const tl = window.__timelines && window.__timelines[compId];
  if (!tl) return { errors: [{ kind: "timeline", message: "timeline not registered" }], boxes: [] };
  tl.seek(t, false);
  const root = document.getElementById("root").getBoundingClientRect();
  const effOpacity = (el) => { let o = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity); return o; };
  const visible = (el) => { const cs = getComputedStyle(el); return cs.display !== "none" && cs.visibility !== "hidden" && el.offsetParent !== null || el.closest("svg"); };
  const boxes = [];
  document.querySelectorAll(".tx").forEach((el) => {
    if (!visible(el)) return;
    if (el.closest(".clip") && el.closest(".clip").style.display === "none") return;
    if (effOpacity(el) < 0.05) return;
    if (!el.textContent.trim()) return;
    // tight box of the actual text (range rects), not the full block width
    const range = document.createRange(); range.selectNodeContents(el);
    const rs = Array.from(range.getClientRects()).filter((r) => r.width > 0.5 && r.height > 0.5);
    if (!rs.length) return;
    const x0 = Math.min(...rs.map((r) => r.left)) - root.left, y0 = Math.min(...rs.map((r) => r.top)) - root.top;
    const x1 = Math.max(...rs.map((r) => r.right)) - root.left, y1 = Math.max(...rs.map((r) => r.bottom)) - root.top;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    const fe = el.closest("[data-field]");
    boxes.push({ el, field: fe ? fe.dataset.field : "", text: el.textContent.trim().slice(0, 60), x0, y0, x1, y1, fs,
      overflowX: el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflow !== "visible",
      clipped: el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).textOverflow === "ellipsis" });
  });
  const errors = [];
  const S = cfg.safe;
  for (const b of boxes) {
    if (b.x0 < S.left - 0.5 || b.x1 > S.right + 0.5 || b.y0 < S.top - 0.5)
      errors.push({ kind: "safe_area", fields: [b.field], text: b.text, box: [b.x0, b.y0, b.x1, b.y1].map(Math.round) });
    if (b.y1 > cfg.caption_top + 0.5)
      errors.push({ kind: "caption_zone", fields: [b.field], text: b.text, box: [b.x0, b.y0, b.x1, b.y1].map(Math.round) });
    if (b.fs < cfg.min_px - 0.01 && !b.el.closest(".katex"))
      errors.push({ kind: "font_size", fields: [b.field], text: b.text, size: b.fs });
    if (b.overflowX || b.clipped) errors.push({ kind: "overflow", fields: [b.field], text: b.text });
  }
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
    const ix = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0), iy = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0);
    if (ix > 2 && iy > 2) errors.push({ kind: "overlap", fields: [a.field, b.field], text: `${a.text} ⟷ ${b.text}`, area: Math.round(ix * iy) });
  }
  // diagram graphics (node discs) must not cover any text that is not their own label
  document.querySelectorAll(".dg-disc").forEach((disc) => {
    const clip = disc.closest(".clip");
    if (clip && clip.style.display === "none") return;
    const node = disc.closest(".dg-node");
    if (effOpacity(disc) < 0.05) return;
    const r = disc.getBoundingClientRect();
    const d = { x0: r.left - root.left, y0: r.top - root.top, x1: r.right - root.left, y1: r.bottom - root.top };
    for (const b of boxes) {
      if (node && node.contains(b.el)) continue;
      const ix = Math.min(d.x1, b.x1) - Math.max(d.x0, b.x0), iy = Math.min(d.y1, b.y1) - Math.max(d.y0, b.y0);
      if (ix > 2 && iy > 2) errors.push({ kind: "overlap", fields: [b.field], text: `node graphic ⟷ ${b.text}`, area: Math.round(ix * iy) });
    }
  });
  // any element (not just text) overflowing the content box downwards into captions
  document.querySelectorAll(".scene .content *, .scene .dg *").forEach((el) => {
    if (el.closest(".clip") && el.closest(".clip").style.display === "none") return;
    const r = el.getBoundingClientRect();
    if (r.height > 0 && r.bottom - root.top > cfg.caption_top + 0.5 && effOpacity(el) > 0.05)
      errors.push({ kind: "caption_zone", text: (el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className) || el.tagName, box: [r.left, r.top, r.right, r.bottom].map(Math.round) });
  });
  if (sc.schematic) {
    const lab = Array.from(document.querySelectorAll(".schematic")).find((e) => e.closest(".clip").style.display !== "none" && effOpacity(e) > 0.5);
    if (!lab) errors.push({ kind: "schematic_label", text: "schematic label missing" });
  }
  const seen = new Set();
  const uniq = errors.filter((e) => { const k = e.kind + e.text; if (seen.has(k)) return false; seen.add(k); return true; });
  return { errors: uniq, boxes: boxes.length };
}
