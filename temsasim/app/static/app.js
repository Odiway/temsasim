/* temsasim interface. No framework, no network dependencies: it must open on
   a plant PC with no internet. Everything user-supplied is inserted with
   textContent, never innerHTML. */
"use strict";

/* ================================================================ helpers */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs, parent) {
  const el = document.createElementNS(NS, tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(el);
  return el;
}
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* Numbers: shown the Turkish way (95,5), accepted either way. */
function fmt(v, d = 1) {
  if (v == null || !isFinite(v)) return "—";
  return Number(v).toLocaleString("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtInput(v) {
  if (v == null || v === "") return "";
  return String(v).replace(".", ",");
}
function parseNum(str) {
  if (str == null) return null;
  let t = String(str).trim().replace(/\s| /g, "").replace("%", "");
  if (!t) return null;
  if (t.includes(",") && t.includes(".")) {
    t = t.lastIndexOf(",") > t.lastIndexOf(".") ? t.replace(/\./g, "").replace(",", ".")
                                                 : t.replace(/,/g, "");
  } else t = t.replace(",", ".");
  const n = Number(t);
  return isFinite(n) ? n : NaN;
}

const store = {
  get(k, d) { try { const v = localStorage.getItem("tsim." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("tsim." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const api = {
  async req(method, path, body, isForm) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      if (isForm) opt.body = body;
      else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
    }
    const r = await fetch(path, opt);
    let j = null;
    try { j = await r.json(); } catch { /* non-JSON */ }
    if (!r.ok) throw new Error((j && j.error) || `Sunucu hatası (${r.status})`);
    return j;
  },
  get(p) { return this.req("GET", p); },
  post(p, b) { return this.req("POST", p, b || {}); },
  upload(p, file, fields = {}) {
    const fd = new FormData();
    fd.append("file", file);
    for (const [k, v] of Object.entries(fields)) if (v != null && v !== "") fd.append(k, v);
    return this.req("POST", p, fd, true);
  },
  uploadMany(p, files, fields = {}) {
    const fd = new FormData();
    for (const f of files) fd.append("file", f, f.name);
    for (const [k, v] of Object.entries(fields)) if (v != null && v !== "") fd.append(k, v);
    return this.req("POST", p, fd, true);
  },
};

let toastTimer;
function toast(msg, kind) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (kind === "err" ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), kind === "err" ? 7000 : 3500);
}
async function busy(btn, fn) {
  btn.disabled = true; btn.classList.add("busy");
  try { return await fn(); }
  catch (e) { toast(e.message, "err"); }
  finally { btn.disabled = false; btn.classList.remove("busy"); }
}
const tip = {
  show(ev, nodes) {
    const t = $("#tip"); t.replaceChildren(...nodes.filter(Boolean)); t.style.opacity = 1;
    const r = t.getBoundingClientRect();
    let x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - 14;
    t.style.left = x + "px"; t.style.top = y + "px";
  },
  hide() { $("#tip").style.opacity = 0; },
};

/* ================================================================ the sign
   An amber dot-matrix destination sign. Left: the vehicle in use. Right: the
   page. Text is rasterised from the system font into a coarse grid and each
   lit cell drawn as an LED, so Turkish capitals render without a bitmap font. */
const Sign = (() => {
  const cv = $("#sign");
  // 5x7 dot-matrix glyphs (HD44780-style), one 5-bit row each, top to bottom.
  const G = {
    A: [14,17,17,17,31,17,17], B: [30,17,17,30,17,17,30], C: [14,17,16,16,16,17,14],
    D: [28,18,17,17,17,18,28], E: [31,16,16,30,16,16,31], F: [31,16,16,30,16,16,16],
    G: [14,17,16,23,17,17,15], H: [17,17,17,31,17,17,17], I: [14,4,4,4,4,4,14],
    J: [7,2,2,2,2,18,12], K: [17,18,20,24,20,18,17], L: [16,16,16,16,16,16,31],
    M: [17,27,21,21,17,17,17], N: [17,17,25,21,19,17,17], O: [14,17,17,17,17,17,14],
    P: [30,17,17,30,16,16,16], Q: [14,17,17,17,21,18,13], R: [30,17,17,30,20,18,17],
    S: [15,16,16,14,1,1,30], T: [31,4,4,4,4,4,4], U: [17,17,17,17,17,17,14],
    V: [17,17,17,17,17,10,4], W: [17,17,17,21,21,21,10], X: [17,17,10,4,10,17,17],
    Y: [17,17,17,10,4,4,4], Z: [31,1,2,4,8,16,31],
    0: [14,17,19,21,25,17,14], 1: [4,12,4,4,4,4,14], 2: [14,17,1,2,4,8,31],
    3: [31,2,4,2,1,17,14], 4: [2,6,10,18,31,2,2], 5: [31,16,30,1,1,17,14],
    6: [6,8,16,30,17,17,14], 7: [31,1,2,4,8,8,8], 8: [14,17,17,14,17,17,14],
    9: [14,17,17,15,1,2,12], "-": [0,0,0,14,0,0,0], ".": [0,0,0,0,0,12,12],
    "/": [0,1,2,4,8,16,0], ">": [8,4,2,1,2,4,8],
  };
  // Turkish letters: base glyph + marks above (2 rows) or below (1 row).
  const MARK = { "Ç": ["C", null, 4], "Ş": ["S", null, 4], "Ü": ["U", [10, 0], 0],
                 "Ö": ["O", [10, 0], 0], "İ": ["I", [4, 0], 0], "Ğ": ["G", [17, 14], 0] };
  const ROWS = 12, TOP = 1;        // 1 margin, 2 mark rows, 7 glyph rows, 1 below, 1 margin
  let left = "", right = "", shift = 0, raf = null;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function raster(text) {
    const cols = [];                 // array of column bitmasks over ROWS
    for (const ch of text) {
      if (ch === " ") { cols.push(0, 0, 0); continue; }
      let base = ch, above = null, below = 0;
      if (MARK[ch]) [base, above, below] = MARK[ch];
      const g = G[base];
      if (!g) { cols.push(0, 0); continue; }
      for (let x = 0; x < 5; x++) {
        let colMask = 0;
        const bit = 1 << (4 - x);
        if (above) { if (above[0] & bit) colMask |= 1 << TOP; if (above[1] & bit) colMask |= 1 << (TOP + 1); }
        for (let y = 0; y < 7; y++) if (g[y] & bit) colMask |= 1 << (TOP + 2 + y);
        if (below & bit) colMask |= 1 << (TOP + 9);
        cols.push(colMask);
      }
      cols.push(0);
    }
    return cols;
  }

  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth, H = cv.clientHeight;
    if (!W || !H) return;
    cv.width = W * dpr; cv.height = H * dpr;
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const P = H / ROWS, n = Math.floor(W / P), r = P * 0.34;
    const lit = new Array(n).fill(0);
    const L = raster(left), R = raster(right);
    L.forEach((c, i) => { if (1 + i < n) lit[1 + i] = c; });
    const div = L.length + 3;
    const rStart = div + 4 + shift;
    R.forEach((c, i) => { const x = rStart + i; if (x >= 0 && x < n) lit[x] = c; });
    const on = css("--sign-on") || "#ffb000", off = css("--sign-off") || "#2a2318";
    for (let x = 0; x < n; x++) for (let y = 0; y < ROWS; y++) {
      const isDiv = x === div && y > 0 && y < ROWS - 1;
      ctx.fillStyle = (lit[x] >> y) & 1 ? on : isDiv ? "#5a4a2c" : off;
      ctx.beginPath();
      ctx.arc(x * P + P / 2, y * P + P / 2, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function set(l, rt) {
    const L2 = (l || "").toLocaleUpperCase("tr-TR"), R2 = (rt || "").toLocaleUpperCase("tr-TR");
    const changed = R2 !== right;
    left = L2; right = R2;
    if (!changed || reduce) { shift = 0; draw(); return; }
    // Scroll the new destination in from the right, one LED column per frame,
    // the way a real sign changes over.
    shift = 36;
    cancelAnimationFrame(raf);
    const step = () => { shift = Math.max(0, shift - 2); draw(); if (shift > 0) raf = requestAnimationFrame(step); };
    raf = requestAnimationFrame(step);
  }
  new ResizeObserver(draw).observe(cv);
  return { set, draw, raster };
})();

function vehicleCode(stem) {
  if (!stem) return "SİM";
  const v = OV && OV.vehicles ? OV.vehicles.find(x => x.stem === stem) : null;
  const words = (v && v.name ? v.name : stem.replace(/^(citybus|city_bus|bus|otobus)_/i, "").replace(/_/g, " ")).split(/\s+/);
  let out = "";
  for (const w of words) { if ((out + " " + w).trim().length > 14) break; out = (out + " " + w).trim(); }
  return out || words[0].slice(0, 14);
}

/* ================================================================ state */
let OV = null;
async function overview(force) {
  if (!OV || force) {
    OV = await api.get("/api/overview");
    $("#libroot").textContent = OV.library_root;
  }
  return OV;
}
const pending = {};   // hand-offs between pages (e.g. an imported map for a new motor)
let pageCleanup = null;   // set by pages that own long-lived resources

/* ================================================================ charts */
const RAMP = ["#e6f0fc", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];

let HM_ID = 0;
function heatmap(pv, { width = 640, height = 360, compact = false } = {}) {
  const hid = "hatch" + (++HM_ID);
  const L = compact ? 40 : 58, R = compact ? 8 : 16, T = 10, B = compact ? 26 : 46;
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": "Verim haritası önizlemesi" });
  const X = pv.speed_rpm, Y = pv.torque_nm, Z = pv.efficiency, F = pv.filled;
  const xmax = X[X.length - 1], ymax = Y[Y.length - 1];
  const sx = v => L + v / xmax * (width - L - R);
  const sy = v => T + (1 - v / ymax) * (height - T - B);
  const vals = Z.flat().filter(v => v > 0).sort((a, b) => a - b);
  const lo = Math.max(0.6, vals[Math.floor(vals.length * 0.05)] || 0.6);
  const hi = vals[vals.length - 1] || 1;
  const col = v => RAMP[Math.max(0, Math.min(RAMP.length - 1, Math.round((v - lo) / (hi - lo || 1) * (RAMP.length - 1))))];
  const edges = a => a.map((v, i) => [i === 0 ? 0 : (a[i - 1] + v) / 2, i === a.length - 1 ? v : (v + a[i + 1]) / 2]);
  const ex = edges(X), ey = edges(Y);

  const defs = s("defs", {}, svg);
  const pat = s("pattern", { id: hid, width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
  s("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: css("--muted"), "stroke-width": 1.3, opacity: .7 }, pat);

  for (let j = 0; j < Y.length; j++) for (let i = 0; i < X.length; i++) {
    const x0 = sx(ex[i][0]), x1 = sx(ex[i][1]), y0 = sy(ey[j][1]), y1 = sy(ey[j][0]);
    const filled = F && F[j][i];
    s("rect", { x: x0, y: y0, width: Math.max(x1 - x0, .5) + .4, height: Math.max(y1 - y0, .5) + .4, fill: filled ? css("--panel-2") : col(Z[j][i]) }, svg);
    if (filled) s("rect", { x: x0, y: y0, width: x1 - x0 + .4, height: y1 - y0 + .4, fill: `url(#${hid})` }, svg);
  }
  const axis = css("--muted");
  const ticks = (max, n) => { const st = niceStep(max / n); const o = []; for (let v = 0; v <= max + 1e-9; v += st) o.push(v); return o; };
  if (!compact) {
    ticks(xmax, 6).forEach(t => s("text", { x: sx(t), y: height - B + 16, "text-anchor": "middle", "font-size": 11, fill: axis }, svg).textContent = fmt(t, 0));
    ticks(ymax, 5).forEach(t => s("text", { x: L - 8, y: sy(t) + 4, "text-anchor": "end", "font-size": 11, fill: axis }, svg).textContent = fmt(t, 0));
    s("text", { x: (L + width - R) / 2, y: height - 6, "text-anchor": "middle", "font-size": 12, fill: css("--ink-2") }, svg).textContent = "devir (rpm)";
    const yl = s("text", { x: 14, y: (T + height - B) / 2, "text-anchor": "middle", "font-size": 12, fill: css("--ink-2"), transform: `rotate(-90 14 ${(T + height - B) / 2})` }, svg);
    yl.textContent = "tork (Nm)";
  }
  if (pv.peak) {
    const px = sx(pv.peak.speed_rpm), py = sy(pv.peak.torque_nm);
    s("circle", { cx: px, cy: py, r: compact ? 3.5 : 5.5, fill: "none", stroke: "#fff", "stroke-width": 3 }, svg);
    s("circle", { cx: px, cy: py, r: compact ? 3.5 : 5.5, fill: "none", stroke: css("--ink"), "stroke-width": 1.6 }, svg);
  }
  // hover
  const hit = s("rect", { x: L, y: T, width: width - L - R, height: height - T - B, fill: "transparent" }, svg);
  hit.addEventListener("mousemove", ev => {
    const b = svg.getBoundingClientRect();
    const px = (ev.clientX - b.left) / b.width * width, py = (ev.clientY - b.top) / b.height * height;
    const rpm = (px - L) / (width - L - R) * xmax, nm = (1 - (py - T) / (height - T - B)) * ymax;
    const i = nearest(X, rpm), j = nearest(Y, nm);
    tip.show(ev, [h("div", { class: "num" }, `${fmt(X[i], 0)} rpm · ${fmt(Y[j], 0)} Nm`),
      h("div", {}, h("b", {}, `%${fmt(Z[j][i] * 100, 1)}`), " verim"),
      F && F[j][i] ? h("div", { class: "muted small" }, "boş hücreydi, dolduruldu") : null]);
  });
  hit.addEventListener("mouseleave", tip.hide);

  const wrap = h("div", { class: "chart" });
  wrap.appendChild(svg);
  if (!compact) {
    const lg = h("div", { class: "legend" },
      h("span", {}, h("i", { style: `background:${RAMP[1]}` }), `%${fmt(lo * 100, 0)} ve altı`),
      h("span", {}, h("i", { style: `background:${RAMP[7]}` }), `%${fmt(hi * 100, 1)}`),
      h("span", {}, h("i", { style: `background:transparent;border:1.5px solid ${css("--ink")};border-radius:50%` }), "tepe verim"),
      F && F.flat().some(Boolean) ? h("span", {}, h("i", { style: `background:repeating-linear-gradient(45deg,${css("--muted")} 0 1px,${css("--panel-2")} 1px 4px);outline:1px solid ${css("--rule")}` }), "dosyada boştu (en yakın değerle dolduruldu)") : null);
    wrap.appendChild(lg);
  }
  return wrap;
}
function niceStep(raw) { const m = Math.pow(10, Math.floor(Math.log10(raw || 1))); const n = raw / m; return (n >= 5 ? 10 : n >= 2 ? 5 : n >= 1 ? 2 : 1) * m; }
function niceTicks(lo, hi, n) { const st = niceStep((hi - lo) / n || 1); const o = []; for (let v = Math.ceil(lo / st) * st; v <= hi + 1e-9; v += st) o.push(+v.toFixed(10)); return { ticks: o, step: st }; }
function nearest(arr, v) { let b = 0, d = Infinity; arr.forEach((a, i) => { const e = Math.abs(a - v); if (e < d) { d = e; b = i; } }); return b; }

function curveMini(points, { xLabel = "x", yLabel = "y", xPct = false } = {}) {
  const W = 300, H = 128, L = 36, R = 8, T = 8, B = 24;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}` });
  if (!points || points.length < 2) return h("div", { class: "muted small" }, "Veri yok");
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1 - y0) * .1 || .1;
  const sx = v => L + (v - x0) / (x1 - x0 || 1) * (W - L - R);
  const sy = v => T + (1 - (v - (y0 - pad)) / ((y1 + pad) - (y0 - pad))) * (H - T - B);
  s("line", { x1: L, y1: H - B, x2: W - R, y2: H - B, stroke: css("--axis") }, svg);
  s("path", { d: points.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)} ${sy(p[1]).toFixed(1)}`).join(" "), fill: "none", stroke: css("--s1"), "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  points.forEach(p => s("circle", { cx: sx(p[0]), cy: sy(p[1]), r: 2.4, fill: css("--s1") }, svg));
  const lab = (x, y, t, a = "middle") => (s("text", { x, y, "text-anchor": a, "font-size": 10.5, fill: css("--muted") }, svg).textContent = t);
  lab(L, H - 8, xPct ? fmt(x0 * 100, 0) + "%" : fmt(x0, 0), "start");
  lab(W - R, H - 8, xPct ? fmt(x1 * 100, 0) + "%" : fmt(x1, 0), "end");
  lab((L + W - R) / 2, H - 8, xLabel);
  lab(L - 4, sy(y1) + 4, fmt(y1, 2), "end");
  lab(L - 4, sy(y0) + 4, fmt(y0, 2), "end");
  return h("div", { class: "chart" }, svg);
}

/* Line chart with a shared x axis. One y scale only: two quantities with
   different units go in two charts. Drawn at the container's real pixel width
   (and redrawn when it changes) so text is never scaled up or down. */
function lineChart(opts) {
  const { series, yLabel } = opts;
  const holder = h("div");
  const wrap = h("div", { class: "chart" }, h("div", { class: "small muted", style: "margin-bottom:4px" }, yLabel), holder);
  if (series.length > 1) wrap.appendChild(h("div", { class: "legend" }, series.map(sr => h("span", {}, h("i", { style: `background:${sr.color}` }), sr.label))));
  let lastW = 0;
  const draw = () => {
    const W = Math.round(holder.clientWidth);
    if (!W || Math.abs(W - lastW) < 2) return;
    lastW = W;
    holder.replaceChildren(lineSvg(opts, Math.max(W, 320)));
  };
  new ResizeObserver(draw).observe(holder);
  return wrap;
}

function lineSvg({ x, series, xLabel, yLabel, xFmt = v => fmt(v, 0), yFmt = v => fmt(v, 1), height = 260, markers = false }, W) {
  const H = height, T = 10, B = 40, L = 52;
  const labelLen = Math.max(0, ...series.map(sr => sr.label.length));
  const R = series.length > 1 ? Math.min(150, 20 + labelLen * 6.5) : 16;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": yLabel });
  const all = series.flatMap(sr => sr.y.filter(v => v != null && isFinite(v)));
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * .08 || 1; lo -= pad; hi += pad;
  const x0 = Math.min(...x), x1 = Math.max(...x);
  const sx = v => L + (v - x0) / (x1 - x0 || 1) * (W - L - R);
  const sy = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const yt = niceTicks(lo, hi, 4), yd = yt.step >= 1 ? 0 : yt.step >= .1 ? 1 : 2;
  yt.ticks.forEach(t => {
    s("line", { x1: L, y1: sy(t), x2: W - R, y2: sy(t), stroke: css("--grid"), "shape-rendering": "crispEdges" }, svg);
    s("text", { x: L - 8, y: sy(t) + 4, "text-anchor": "end", "font-size": 11.5, fill: css("--muted") }, svg).textContent = fmt(t, yd);
  });
  if (lo < 0 && hi > 0) s("line", { x1: L, y1: sy(0), x2: W - R, y2: sy(0), stroke: css("--axis"), "stroke-width": 1.5 }, svg);
  niceTicks(x0, x1, Math.max(3, Math.floor((W - L - R) / 110))).ticks.forEach(t =>
    s("text", { x: sx(t), y: H - B + 17, "text-anchor": "middle", "font-size": 11.5, fill: css("--muted") }, svg).textContent = xFmt(t));
  s("text", { x: (L + W - R) / 2, y: H - 4, "text-anchor": "middle", "font-size": 12, fill: css("--ink-2") }, svg).textContent = xLabel;
  const labels = [];
  series.forEach(sr => {
    const pts = sr.y.map((v, i) => v == null ? null : [sx(x[i]), sy(v)]).filter(Boolean);
    s("path", { d: pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" "), fill: "none", stroke: sr.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    if (markers) pts.forEach(p => s("circle", { cx: p[0], cy: p[1], r: 4, fill: sr.color, stroke: css("--panel"), "stroke-width": 2 }, svg));
    const last = pts[pts.length - 1];
    if (last && series.length > 1) labels.push({ y: last[1] + 4, t: sr.label });
  });
  labels.sort((a, b) => a.y - b.y);
  for (let i = 1; i < labels.length; i++) if (labels[i].y - labels[i - 1].y < 15) labels[i].y = labels[i - 1].y + 15;
  labels.forEach(lb => s("text", { x: W - R + 8, y: lb.y, "font-size": 12, fill: css("--ink-2") }, svg).textContent = lb.t);

  const cross = s("line", { x1: 0, x2: 0, y1: T, y2: H - B, stroke: css("--axis"), opacity: 0 }, svg);
  const hit = s("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent" }, svg);
  hit.addEventListener("mousemove", ev => {
    const bb = svg.getBoundingClientRect();
    const xv = x0 + ((ev.clientX - bb.left) / bb.width * W - L) / (W - L - R) * (x1 - x0);
    const i = nearest(x, xv);
    cross.setAttribute("x1", sx(x[i])); cross.setAttribute("x2", sx(x[i])); cross.setAttribute("opacity", 1);
    tip.show(ev, [h("div", { class: "muted small" }, `${xLabel}: ${xFmt(x[i])}`),
      ...series.map(sr => h("div", {}, h("span", { style: `display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px;background:${sr.color}` }), `${sr.label}: `, h("b", { class: "num" }, sr.y[i] == null ? "—" : yFmt(sr.y[i]))))]);
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("opacity", 0); tip.hide(); });
  return svg;
}

/* Two variants of one bus share most of their name. Keep what tells them apart. */
function distinctLabels(labels) {
  if (labels.length < 2) return labels;
  let p = 0;
  while (labels.every(l => l[p] === labels[0][p]) && p < labels[0].length) p++;
  const cut = labels[0].lastIndexOf(" ", p);
  const out = labels.map(l => l.slice(cut + 1).trim());
  return out.every(Boolean) && new Set(out).size === labels.length ? out : labels;
}

/* ================================================================ widgets */
const ICON_SHEET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M6 2.5h8l4 4v15H6z"/><path d="M14 2.5v4h4"/><path d="M8.5 11h7M8.5 14h7M8.5 17h7M11 11v6"/></svg>';

function dropZone({ accept, title, sub, onFile, onFiles, compact, multiple, directory }) {
  const input = h("input", { type: "file", accept, hidden: true, multiple: multiple || directory ? true : null, webkitdirectory: directory ? true : null });
  const zone = h("div", { class: "drop" + (compact ? " compact" : ""), tabindex: 0, role: "button", "aria-label": title });
  if (!compact) zone.insertAdjacentHTML("afterbegin", ICON_SHEET);
  zone.append(h("div", { class: "big" }, title), h("div", { class: "sub" }, sub), input);
  const pick = () => input.click();
  const got = list => {
    const files = [...list].filter(f => !f.name.startsWith("."));
    if (!files.length) return;
    if (onFiles) onFiles(files); else onFile(files[0]);
  };
  zone.addEventListener("click", pick);
  zone.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", async e => {
    e.preventDefault(); zone.classList.remove("over");
    // dropped folders: walk them so a whole VECTO folder can be dropped at once
    const items = e.dataTransfer.items ? [...e.dataTransfer.items] : [];
    const entries = items.map(it => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
    if (onFiles && entries.some(en => en.isDirectory)) {
      const out = [];
      const walk = en => new Promise(res => {
        if (en.isFile) en.file(f => { out.push(f); res(); }, res);
        else if (en.isDirectory) { const rd = en.createReader(); const more = () => rd.readEntries(async es => { if (!es.length) return res(); for (const x of es) await walk(x); more(); }, res); more(); }
        else res();
      });
      for (const en of entries) await walk(en);
      got(out);
    } else got(e.dataTransfer.files);
  });
  input.addEventListener("change", () => { got(input.files); input.value = ""; });
  return zone;
}

function seg(options, value, onChange) {
  const el = h("div", { class: "seg", role: "group" });
  options.forEach(([v, label]) => {
    const b = h("button", { type: "button", "aria-pressed": String(v === value) }, label);
    b.addEventListener("click", () => { $$("button", el).forEach(x => x.setAttribute("aria-pressed", "false")); b.setAttribute("aria-pressed", "true"); onChange(v); });
    el.appendChild(b);
  });
  return el;
}

const CONF = { high: ["ok", "emin"], medium: ["plain", "tahmin"], low: ["warn", "kontrol edin"] };
function confBadge(c) { const [k, t] = CONF[c] || CONF.medium; return h("span", { class: "badge " + k }, t); }
function valBadge(ok) { return ok ? h("span", { class: "badge ok" }, "doğrulandı") : h("span", { class: "badge warn" }, "ölçülmedi"); }
/* "2026-11-12 09:40" -> a small muted "12.11.2026" with the full stamp as tooltip */
function whenEl(iso) {
  if (!iso) return null;
  const [d] = iso.split(" ");
  const [y, m, dd] = d.split("-");
  return h("span", { class: "when", title: `son değişiklik ${iso}` }, `${dd}.${m}.${y}`);
}
function steps(labels, now) {
  return h("ol", { class: "steps" }, labels.map((l, i) => h("li", { "data-state": i < now ? "done" : i === now ? "now" : "todo" }, h("span", {}, i + 1), l)));
}
function notice(kind, title, items) {
  return h("div", { class: "notice " + kind, role: kind === "err" ? "alert" : null },
    title ? h("b", {}, title) : null,
    items && items.length ? h("ul", {}, items.map(t => h("li", {}, t))) : null);
}
function field(label, control, { unit, hint } = {}) {
  return h("label", { class: "field" }, h("div", { class: "lab" }, label, unit ? h("span", { class: "unit" }, unit) : null), control, hint ? h("div", { class: "hint" }, hint) : null);
}
function numInput(value, { unit, placeholder } = {}) {
  const inp = h("input", { type: "text", inputmode: "decimal", class: "numeric", value: fmtInput(value), placeholder: placeholder != null ? fmtInput(placeholder) : null, autocomplete: "off" });
  inp.addEventListener("input", () => inp.classList.toggle("invalid", Number.isNaN(parseNum(inp.value))));
  // Always wrapped, so every caller can read it the same way: $("input", el).
  return h("div", { class: "inputwrap" }, inp, unit ? h("span", { class: "suffix" }, unit) : null);
}
function select(options, value) {
  return h("select", {}, options.map(([v, l]) => h("option", { value: v, selected: v === value ? true : null }, l)));
}
function pageHead(eyebrow, title, lede, actions) {
  return h("div", {},
    h("div", { class: "pagehead" }, h("div", {}, h("div", { class: "eyebrow" }, eyebrow), h("h1", {}, title)), actions ? h("div", { class: "row" }, actions) : null),
    lede ? h("p", { class: "lede" }, lede) : null);
}

/* ---- menus, copy, download, raw file ----------------------------------- */
/* One ⋯ menu on every library row: copy, download as a package, open the file,
   move to the trash. Same order everywhere so the hand learns it once. */
function menu(label, items, { cls, left, up } = {}) {
  const d = h("details", { class: "menu" + (left ? " left" : "") + (up ? " up" : "") });
  const list = h("div", { class: "menu-list", role: "menu" });
  items.filter(Boolean).forEach(it => {
    if (it === "-") { list.append(h("hr")); return; }
    const el = it.href
      ? h("a", { href: it.href, role: "menuitem", class: it.danger ? "danger" : null, download: it.download != null ? it.download : null, target: it.blank ? "_blank" : null, rel: it.blank ? "noopener" : null, onclick: () => { d.open = false; } }, it.label)
      : h("button", { type: "button", role: "menuitem", class: it.danger ? "danger" : null, onclick: async () => { d.open = false; try { await it.onclick(); } catch (e) { toast(e.message, "err"); } } }, it.label);
    if (it.hint) el.append(h("span", { class: "hint" }, it.hint));
    list.append(el);
  });
  d.append(h("summary", { class: "btn" + (cls ? " " + cls : " ghost"), "aria-haspopup": "menu", title: typeof label === "string" ? label : null }, label), list);
  d.addEventListener("toggle", () => { if (d.open) $$("details.menu[open]").forEach(o => { if (o !== d) o.open = false; }); });
  return d;
}
document.addEventListener("click", e => { if (!e.target.closest("details.menu")) $$("details.menu[open]").forEach(o => (o.open = false)); });
document.addEventListener("keydown", e => { if (e.key === "Escape") $$("details.menu[open]").forEach(o => (o.open = false)); });

async function copyItem(kind, stem, name) {
  const n = prompt("Kopyanın adı:", `${name} (kopya)`);
  if (n == null) return null;
  const r = await api.post(`/api/copy/${kind}/${encodeURIComponent(stem)}`, { name: n.trim() || null });
  await overview(true);
  toast(`Kopyalandı: ${r.files.join(", ")}`);
  return r;
}
const exportUrl = (kind, stem) => `/api/export/${kind}/${encodeURIComponent(stem)}`;
const DELETE_URL = {
  vehicle: s => `/api/vehicle/${encodeURIComponent(s)}/delete`,
  route: s => `/api/route/${encodeURIComponent(s)}/delete`,
  scenario: s => `/api/scenario/${encodeURIComponent(s)}/delete`,
};
async function deleteItem(kind, stem, name) {
  if (!confirm(`"${name}" silinsin mi?\nDosya kütüphanedeki .cop_kutusu klasörüne taşınır; geri alınabilir.`)) return false;
  const url = DELETE_URL[kind] ? DELETE_URL[kind](stem) : `/api/component/${kind}/${encodeURIComponent(stem)}/delete`;
  await api.post(url);
  await overview(true);
  toast("Çöp kutusuna taşındı.");
  return true;
}
/* The ⋯ menu for a library item. `after` runs after a copy or delete (the
   caller usually re-renders). */
function itemMenu(kind, stem, name, { after, extra, label, up } = {}) {
  const re = async () => { if (after) await after(); else route(); };
  const pkg = kind === "vehicle" ? "araç + bileşenler" : kind === "scenario" ? "senaryo + güzergâh + araçlar" : kind === "route" ? "csv + yaml" : "yaml + haritalar";
  return menu(label || "⋯", [
    ...(extra || []),
    { label: "Kopyala", onclick: async () => { if (await copyItem(kind, stem, name)) re(); } },
    { label: "İndir (zip)", href: exportUrl(kind, stem), download: "", hint: pkg },
    { label: "Dosyayı gör / düzenle", onclick: () => openFileDialog(kind, stem, name) },
    "-",
    { label: "Sil", danger: true, onclick: async () => { if (await deleteItem(kind, stem, name)) re(); } },
  ], { up });
}

/* The item's file as text: read it, copy it, hand-edit it. A save is only
   accepted if the loader takes the result; otherwise the file is untouched. */
async function openFileDialog(kind, stem, name) {
  const raw = await api.get(`/api/file/${kind}/${encodeURIComponent(stem)}`);
  const dlg = h("dialog", {});
  const close = () => { dlg.close(); dlg.remove(); };
  const ta = h("textarea", { class: "raw-editor", spellcheck: "false" }, raw.text);
  const status = h("span", { class: "muted small" }, raw.files.length > 1 ? `Diğer dosyalar: ${raw.files.filter(f => f !== raw.path).join(", ")}` : "");
  const isYaml = raw.path.endsWith(".yaml");
  dlg.append(h("div", { class: "dlg-head" }, h("div", {}, h("h2", {}, name || stem), h("div", { class: "muted small num" }, `library/${raw.path}`)), h("span", { class: "spacer" }),
      h("button", { type: "button", class: "ghost", onclick: close }, "Kapat")),
    h("div", { class: "dlg-body stack" },
      isYaml ? h("p", { class: "small muted", style: "margin:0" }, "Satır sonundaki # yorumları kaynağı belgeler; onları koruyun. Birim ekleri: _kw, _rpm, _kmh, _t… Dosya kaydedilmeden önce yükleyiciden geçer.") : null,
      ta,
      h("div", { class: "row" },
        isYaml ? h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, async () => {
          const r = await api.post(`/api/file/${kind}/${encodeURIComponent(stem)}`, { text: ta.value });
          await overview(true); toast(`Kaydedildi: ${r.saved}`); close(); route();
        }) }, "Kaydet") : null,
        h("button", { type: "button", onclick: async () => { try { await navigator.clipboard.writeText(ta.value); toast("Panoya kopyalandı."); } catch { toast("Pano erişimi yok; metni seçip kopyalayın.", "err"); } } }, "Panoya kopyala"),
        h("a", { class: "btn", href: exportUrl(kind, stem), download: "" }, "Zip indir"),
        h("span", { class: "spacer" }), status)));
  document.body.append(dlg);
  dlg.showModal();
}

/* ================================================================ router */
const PAGES = {
  baslangic: pageHome, kutuphane: pageLibrary, yukle: pageUpload, bilesen: pageComponent,
  arac: pageVehicle, simulasyon: pageSim, surus: pageDrive, hat: pageNetwork, dogrulama: pageValidate,
  guzergah: pageRouteBuilder, senaryo: pageScenarios,
};
const PAGE_SIGN = { baslangic: "Başlangıç", kutuphane: "Kütüphane", yukle: "Veri yükle", bilesen: "Bileşen", arac: "Araç kur", simulasyon: "Simülasyon", surus: "Serbest sürüş", hat: "Hat ve sefer", dogrulama: "Doğrulama",
  guzergah: "Güzergâh", senaryo: "Senaryo" };
/* The shared toolkit the page modules (network.js, drive.js, routes.js) receive. */
function pageKit() {
  return { h, api, store, fmt, parseNum, seg, select, field, numInput, toast, busy, notice, ov: OV, pageHead, lineChart, css, overview, route, kpi,
    menu, itemMenu, copyItem, exportUrl, openFileDialog, valBadge, whenEl,
    setCleanup: fn => (pageCleanup = fn), raster: Sign.raster };
}
async function pageRouteBuilder(stem) { const mod = await import("/static/routes.js"); return mod.pageRoute(pageKit(), stem); }
async function pageScenarios(stem) { const mod = await import("/static/routes.js"); return mod.pageScenario(pageKit(), stem); }

async function route() {
  const parts = (location.hash || "#/baslangic").replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  const page = PAGES[parts[0]] ? parts[0] : "baslangic";
  $$("nav.rail a").forEach(a => { if (a.dataset.page === page) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  Sign.set(vehicleCode(store.get("vehicle")), PAGE_SIGN[page]);
  if (pageCleanup) { try { pageCleanup(); } catch (e) { console.error(e); } pageCleanup = null; }
  const main = $("#main");
  main.classList.toggle("fullbleed", page === "simulasyon" || page === "surus");
  main.replaceChildren(h("div", { class: "muted" }, "Yükleniyor…"));
  try {
    await overview();
    const view = await PAGES[page](...parts.slice(1));
    main.replaceChildren(view);
  } catch (e) {
    main.replaceChildren(notice("err", "Bu sayfa açılamadı.", [e.message]));
  }
  main.focus({ preventScroll: true });
  scrollTo(0, 0);
}
addEventListener("hashchange", route);
$("#themeBtn").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const dark = cur ? cur === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
  store.set("theme", dark ? "light" : "dark");
  Sign.draw(); route();
});
{ const t = store.get("theme"); if (t) document.documentElement.setAttribute("data-theme", t); }

/* ================================================================ home */
/* One screen that says what the tool does, in the order it is used, and
   where the library stands: counts, what is still unmeasured, the latest
   test campaign. Everything here is a link into the real pages. */
async function pageHome() {
  const ov = await overview(true);
  let camps = [];
  try { camps = (await api.get("/api/campaigns")).campaigns || []; } catch { /* no test logs */ }
  const root = h("div", {});
  root.append(pageHead("Başlangıç", "Otobüs enerji simülatörü",
    "Kütüphanedeki araç dosyalarından, bir güzergâh ya da senaryo üzerinde enerji ve yakıt tüketimini hesaplar; sonucu test ekibinin ölçümleriyle karşılaştırır. Her şey bu bilgisayardaki düz dosyalardır."));
  const step = (n, title, text, links) => h("div", { class: "panel step" }, h("div", { class: "n" }, n), h("h3", {}, title), h("p", {}, text),
    h("div", { class: "links" }, links.map(([l, href]) => h("a", { href }, l))));
  root.append(h("div", { class: "flow" },
    step("1 · Veri", "Araç ve bileşenler", "VECTO dosyaları, tedarikçi haritaları, elle girilen değerler. Bir bileşen bir kez tanımlanır.", [["Kütüphane", "#/kutuphane"], ["Veri yükle", "#/yukle"], ["Araç kur", "#/arac/yeni"]]),
    step("2 · Görev", "Güzergâh ve senaryo", "Yol bölüm bölüm; durak, ışık, yüzey, rüzgâr. Senaryo = yol + araçlar + koşullar + yük.", [["Güzergâh oluştur", "#/guzergah/yeni"], ["Senaryolar", "#/senaryo"]]),
    step("3 · Koş", "Simülasyon", "Görevi araçta koş, 3B izle, raporu al. Serbest sürüşle hisset, hat ve sefer için günü hesapla.", [["Simülasyon", "#/simulasyon"], ["Serbest sürüş", "#/surus"], ["Hat ve sefer", "#/hat"]]),
    step("4 · Doğrula", "Ölçümle karşılaştır", "Test ekibinin CAN kayıtları modelden geçer; fark nerede toplanıyorsa düzeltilecek bileşen odur.", [["Doğrulama", "#/dogrulama"], ["Hesap haritası", "/static/hesap-haritasi.html"]])));
  const unval = ov.components.reduce((n, g) => n + g.items.filter(i => !i.validated && !i.error).length, 0);
  const nComp = ov.components.reduce((n, g) => n + g.items.length, 0);
  const scen = ov.scenarios || [], rts = ov.route_items || [];
  const status = h("div", { class: "panel" }, h("h2", {}, "Kütüphanenin durumu"),
    h("div", { class: "kpis", style: "margin-top:10px" },
      kpi("Araç", ov.vehicles.length), kpi("Bileşen", nComp), kpi("Ölçülmemiş bileşen", unval), kpi("Güzergâh", rts.length), kpi("Senaryo", scen.length)),
    unval ? h("p", { class: "small muted", style: "margin:10px 0 0" }, "Ölçülmemiş: dosya kabul değeri içeriyor; sonuç yol göstericidir. Kaynağı kütüphanede açıklamasında yazar.") : null,
    camps.length ? h("div", { style: "margin-top:14px" }, h("div", { class: "section-title" }, "Test kampanyaları"),
      h("ul", { class: "home-list" }, camps.map(c => h("li", {}, h("span", { class: "name", title: c.report || "" }, c.campaign || c.name), h("span", { class: "muted small" }, `${c.runs || 0} koşu · ${c.dates || ""}`), h("a", { class: "btn", href: "#/dogrulama" }, "Karşılaştır"))))) : null);
  const quick = h("div", { class: "panel" }, h("div", { class: "row" }, h("h2", {}, "Hızlı başla"), h("span", { class: "spacer" }), h("a", { class: "btn", href: "#/senaryo/yeni" }, "Yeni senaryo")),
    scen.length ? h("ul", { class: "home-list", style: "margin-top:8px" }, scen.slice(0, 6).map(s => h("li", {}, h("span", { class: "name", title: s.summary }, s.name), h("span", { class: "muted small" }, s.kind_tr || ""),
      h("a", { class: "btn", href: `#/senaryo/${encodeURIComponent(s.stem)}` }, "Aç")))) : h("p", { class: "muted small" }, "Henüz senaryo yok."),
    h("div", { class: "section-title" }, "Araçlar"),
    ov.vehicles.length ? h("ul", { class: "home-list" }, ov.vehicles.slice(0, 6).map(v => h("li", {}, h("span", { class: "name" }, v.name), h("span", { class: "muted small" }, v.powertrain_tr || ""),
      h("button", { type: "button", class: "btn", onclick: () => { store.set("vehicle", v.stem); location.hash = "#/simulasyon"; } }, "Simüle et")))) : h("p", { class: "muted small" }, "Henüz araç yok."));
  root.append(h("div", { class: "home-grid" }, status, quick));
  return root;
}

/* ================================================================ library */
async function pageLibrary() {
  const ov = await overview(true);
  const rmod = await import("/static/routes.js");
  const root = h("div", {});
  const routes = ov.route_items || [], scenarios = ov.scenarios || [];
  const nComp = ov.components.reduce((n, g) => n + g.items.length, 0);
  const newMenu = menu("Yeni", [
    { label: "Araç", href: "#/arac/yeni", hint: "bileşenlerden" },
    { label: "Güzergâh", href: "#/guzergah/yeni", hint: "bölüm bölüm" },
    { label: "Senaryo", href: "#/senaryo/yeni" }, "-",
    ...ov.components.map(g => ({ label: g.label, href: `#/bilesen/${g.kind}/yeni` })),
  ], { cls: "primary" });
  root.append(pageHead("Kütüphane", "Veri kütüphanesi",
    "Her şey bu klasördeki düz dosyalardır: bir bileşen bir kez tanımlanır, araçlar ona referans verir; güzergâh ve senaryo da aynı kütüphanede durur. Her öğe kopyalanır, zip olarak indirilir ve başka bir bilgisayardaki kütüphaneye 'Veri yükle → Paket' ile alınır.",
    [h("a", { class: "btn", href: "#/yukle" }, "Veri yükle"), h("a", { class: "btn", href: "#/yukle/paket" }, "Paket al"),
     h("a", { class: "btn", href: "/api/export/library", download: "", title: "Kütüphanenin tamamı, zip" }, "Tümünü indir"), newMenu]));

  /* sticky section nav + search over everything */
  const q = h("input", { type: "search", placeholder: "Ara: ad, özet, rozet…", "aria-label": "Kütüphanede ara" });
  const nomatch = h("span", { class: "nomatch", hidden: true }, "eşleşme yok");
  const SECS = [["arac", "Araçlar", ov.vehicles.length], ["guzergah", "Güzergâhlar", routes.length], ["senaryo", "Senaryolar", scenarios.length], ["bilesen", "Bileşenler", nComp]];
  root.append(h("div", { class: "subnav" },
    ...SECS.map(([id, l, n]) => h("a", { href: `#/kutuphane`, onclick: e => { e.preventDefault(); const el = $(`#lib-${id}`, root); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); } }, l, h("span", { class: "n" }, n))),
    h("span", { class: "spacer" }), q, nomatch));
  q.addEventListener("input", () => {
    const s = q.value.trim().toLocaleLowerCase("tr-TR");
    let shown = 0;
    $$(".itemlist li, .vehicle-card", root).forEach(el => { const hit = s === "" || el.textContent.toLocaleLowerCase("tr-TR").includes(s); el.hidden = !hit; if (hit) shown++; });
    $$(".panel.group", root).forEach(pn => { pn.hidden = s !== "" && !$$(".itemlist li", pn).some(li => !li.hidden); });
    nomatch.hidden = shown > 0 || s === "";
  });
  const section = (id, title, sub, body) => h("section", { class: "lib-sec", id: `lib-${id}` }, h("div", { class: "sec-head" }, h("h2", {}, title), sub ? h("span", { class: "muted" }, sub) : null), body);

  /* vehicles */
  const vg = h("div", { class: "grid-auto" });
  if (!ov.vehicles.length) vg.append(h("div", { class: "panel empty" }, "Henüz araç yok. ", h("a", { href: "#/arac/yeni" }, "İlk aracı kurun"), " ya da bir paket alın."));
  ov.vehicles.forEach(v => {
    vg.append(h("div", { class: "panel vehicle-card" },
      h("div", {}, h("h3", {}, v.name), h("div", { class: "muted small" }, v.variant || "")),
      v.error ? notice("err", "Dosya okunamadı", [v.error]) : h("div", { class: "small" }, h("span", { class: "badge plain", style: "margin-right:6px" }, v.powertrain_tr || ""), v.summary),
      v.error ? null : h("div", { class: "row" }, v.validated ? h("span", { class: "badge ok" }, "tüm bileşenler doğrulandı")
        : h("span", { class: "badge warn", title: v.unvalidated.join(", ") }, `${v.unvalidated.length} bileşen ölçülmedi`), h("span", { class: "spacer" }), whenEl(v.modified)),
      h("div", { class: "actions" },
        v.error ? null : h("button", { class: "primary", type: "button", onclick: () => { store.set("vehicle", v.stem); location.hash = "#/simulasyon"; } }, "Simüle et"),
        h("a", { class: "btn", href: `#/arac/${encodeURIComponent(v.stem)}` }, "Düzenle"),
        h("span", { class: "spacer" }),
        itemMenu("vehicle", v.stem, v.name))));
  });
  root.append(section("arac", "Araçlar", "bileşen seçimleri", vg));

  /* routes + scenarios */
  root.append(section("guzergah", "Güzergâhlar ve senaryolar", "kaydedilen yol her araçta koşar",
    h("div", { class: "grid-2" },
      h("div", { class: "panel", id: "lib-guzergah-p" }, h("div", { class: "group-head" }, h("h3", {}, "Güzergâhlar"), h("span", { class: "muted small" }, routes.length), h("span", { class: "spacer" }),
          h("a", { class: "btn", href: "#/guzergah/yeni" }, "Oluştur"), h("a", { class: "btn", href: "#/yukle/guzergah" }, "GPX yükle")),
        rmod.routeList(pageKit(), { onOpen: s => (location.hash = `#/guzergah/${encodeURIComponent(s)}`), compact: true })),
      h("div", { class: "panel", id: "lib-senaryo" }, h("div", { class: "group-head" }, h("h3", {}, "Senaryolar"), h("span", { class: "muted small" }, scenarios.length), h("span", { class: "spacer" }),
          h("a", { class: "btn", href: "#/senaryo/yeni" }, "Yeni senaryo")),
        rmod.scenarioList(pageKit())))));

  /* components */
  const cg = h("div", { class: "grid-auto" });
  ov.components.forEach(g => {
    const list = h("ul", { class: "itemlist" });
    if (!g.items.length) list.append(h("li", { class: "empty" }, "Henüz yok."));
    g.items.forEach(it => {
      list.append(h("li", {},
        h("div", { class: "main" },
          h("div", { class: "name" }, it.name),
          it.error ? h("div", { class: "sum", style: "color:var(--err)" }, it.error) : h("div", { class: "sum" }, it.summary),
          h("div", { class: "badges" },
            it.error ? h("span", { class: "badge err" }, "hatalı dosya") : valBadge(it.validated),
            it.map === "sentetik" ? h("span", { class: "badge plain", title: "Tedarikçi haritası yüklenene kadar kayıp modelinden üretilir" }, "sentetik harita") : null,
            it.map === "ölçülmüş" ? h("span", { class: "badge ok" }, "yüklenen harita") : null,
            whenEl(it.modified))),
        h("div", { class: "acts" },
          h("a", { class: "btn", href: `#/bilesen/${g.kind}/${encodeURIComponent(it.stem)}` }, "Düzenle"),
          itemMenu(g.kind, it.stem, it.name))));
    });
    cg.append(h("div", { class: "panel group" },
      h("div", { class: "group-head" }, h("h3", {}, g.label), h("span", { class: "muted small" }, g.items.length), h("span", { class: "spacer" }),
        h("a", { class: "btn", href: `#/bilesen/${g.kind}/yeni` }, "Ekle")),
      list));
  });
  root.append(section("bilesen", "Bileşenler", "ölçülmedi rozeti: dosya kabul değeri içeriyor, kaynağı açıklamasında", cg));
  return root;
}

/* ================================================================ upload hub */
async function pageUpload(which) {
  const root = h("div", {});
  root.append(pageHead("Veri yükle", "Dosyanızı olduğu gibi getirin",
    "Biçim dönüştürmenize gerek yok. Excel ya da CSV, noktalı virgül ya da virgül, ondalık virgül, ızgara ya da uzun liste, rpm ya da rad/s: araç ne olduğunu algılar, önizlemeyi gösterir, siz onaylayınca kaydeder."));
  const TILES = [
    ["vecto", "VECTO dosyaları (dizel araç)", "Homologasyonun motor, şanzıman, aks, lastik ve araç XML dosyaları. Klasörü olduğu gibi bırakın; dijital ikiz kurulur."],
    ["harita", "Motor verim haritası", "Tedarikçi ya da dinamometre verisi. Verim (% ya da kesir) veya kayıp gücü."],
    ["guzergah", "Güzergâh", "GPX izi ya da mesafe–rakım tablosu. Duraklar GPX noktalarından okunur."],
    ["egri", "Batarya ya da klima eğrisi", "OCV–SOC, COP–sıcaklık gibi iki sütunlu eğriler."],
    ["log", "Araç logu", "CAN / telematik kaydı. Modeli gerçek otobüsle karşılaştırır."],
    ["paket", "Paket (zip)", "Bir meslektaşın kütüphanesinden 'İndir' ile alınmış zip: araç + bileşenler, güzergâh, senaryo ya da bütün kütüphane. Var olanlara ne yapılacağını sorar."],
  ];
  const cur = which || store.get("uploadTile", "vecto");
  const body = h("div", {});
  const tiles = h("div", { class: "tiles" });
  TILES.forEach(([k, t, d]) => {
    const b = h("button", { type: "button", class: "tile", "aria-pressed": String(k === cur) }, h("span", { class: "t" }, t), h("span", { class: "d" }, d));
    b.addEventListener("click", () => {
      if (k === "log") { location.hash = "#/dogrulama"; return; }
      store.set("uploadTile", k);
      $$(".tile", tiles).forEach(x => x.setAttribute("aria-pressed", "false"));
      b.setAttribute("aria-pressed", "true");
      show(k);
    });
    tiles.append(b);
  });
  root.append(tiles, body);
  const show = k => {
    body.replaceChildren(k === "guzergah" ? routeWizard() : k === "egri" ? curveTarget() : k === "vecto" ? vectoWizard() : k === "paket" ? packageWizard() : mapWizard({ mode: "hub" }));
  };
  show(cur === "log" ? "harita" : cur);
  return root;
}

/* ---- package wizard ------------------------------------------------------- */
/* A zip made by "İndir" (or a zipped library folder). Step 1 drop it, step 2
   see every item with "new" / "exists" and choose what happens to the ones
   that exist, step 3 the result. Nothing is written before step 2's button. */
function packageWizard() {
  const root = h("div", {});
  let st = null;
  const STEPS = ["Zip", "İçerik ve karar", "Sonuç"];
  const first = () => root.replaceChildren(steps(STEPS, 0), h("div", { class: "stack" },
    dropZone({ accept: ".zip", title: "Paketi (zip) bırakın", sub: "Kütüphane sayfasındaki 'İndir' ya da 'Tümünü indir' çıktısı. Klasör yapısı kütüphaneyle aynıdır: engine/x.yaml, engine/maps/x.csv…",
      onFile: async f => {
        root.replaceChildren(steps(STEPS, 1), h("div", { class: "muted" }, `${f.name} okunuyor…`));
        try { st = await api.upload("/api/import/package", f); check(); }
        catch (e) { first(); root.append(notice("err", "Paket okunamadı.", [e.message])); }
      } })));
  const check = () => {
    const items = st.items;
    const exists = items.filter(x => x.exists).length;
    const checks = new Map();
    let mode = exists ? "skip" : "skip";
    const modeSeg = seg([["skip", "Olanı koru"], ["overwrite", "Paketle değiştir"], ["rename", "Yeni adla yan yana koy"]], mode, v => (mode = v));
    const rows = items.map(it => {
      const cb = h("input", { type: "checkbox", checked: it.error ? null : true, disabled: it.error ? true : null });
      checks.set(`${it.kind}/${it.stem}`, cb);
      const tr = h("tr", {},
        h("td", {}, cb), h("td", {}, it.kind_tr || it.kind), h("td", {}, h("b", {}, it.name), h("div", { class: "small muted" }, it.description || "")),
        h("td", { class: "num small" }, `${it.kind}/${it.stem}`),
        h("td", { class: "st" }, it.error ? h("span", { class: "badge err", title: it.error }, "hatalı") : it.exists ? h("span", { class: "badge warn" }, "kütüphanede var") : h("span", { class: "badge ok" }, "yeni"),
          it.missing ? h("div", { class: "small", style: "color:var(--err)" }, `eksik harita: ${it.missing.join(", ")}`) : null));
      cb.addEventListener("change", () => tr.classList.toggle("off", !cb.checked));
      return tr;
    });
    const man = st.manifest || {};
    root.replaceChildren(steps(STEPS, 1), h("div", { class: "stack" },
      h("div", { class: "kpis" },
        h("div", {}, h("div", { class: "k" }, "Öğe"), h("div", { class: "v" }, items.length)),
        h("div", {}, h("div", { class: "k" }, "Yeni"), h("div", { class: "v" }, items.length - exists)),
        h("div", {}, h("div", { class: "k" }, "Zaten var"), h("div", { class: "v" }, exists)),
        h("div", {}, h("div", { class: "k" }, "Paket tarihi"), h("div", { class: "v", style: "font-size:16px" }, man.created || "—"))),
      man.note ? h("p", { class: "small muted", style: "margin:0" }, man.note) : null,
      st.skipped && st.skipped.length ? notice("warn", "Pakette tanınmayan dosyalar atlandı", st.skipped.slice(0, 12)) : null,
      h("div", { class: "panel" }, h("div", { class: "scrollx" }, h("table", { class: "data pkg-table" },
        h("thead", {}, h("tr", {}, ["", "Tür", "Ad", "Dosya", "Durum"].map(c => h("th", {}, c)))), h("tbody", {}, rows)))),
      h("div", { class: "panel stack" },
        h("div", { class: "field" }, h("div", { class: "lab" }, "Kütüphanede zaten olanlar için"), modeSeg,
          h("div", { class: "hint" }, "Olanı koru: var olan dosyaya dokunulmaz. Paketle değiştir: eski dosya çöp kutusuna taşınır. Yeni adla: paketteki öğe _2 ekiyle yazılır, paket içindeki referanslar ona çevrilir.")),
        h("div", { class: "row" },
          h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, async () => {
            const select = [...checks].filter(([, cb]) => cb.checked).map(([k]) => k);
            if (!select.length) throw new Error("Hiç öğe seçilmedi.");
            const r = await api.post(`/api/import/package/${st.upload_id}/save`, { mode, select });
            await overview(true); done(r);
          }) }, "Kütüphaneye al"),
          h("button", { type: "button", class: "ghost", onclick: first }, "Başka paket seç")))));
  };
  const done = r => {
    const ren = Object.entries(r.renamed || {});
    root.replaceChildren(steps(STEPS, 2), h("div", { class: "stack" },
      r.errors.length ? notice("err", "Bazı dosyalar yükleyiciden geçmedi (yazıldı ama kütüphane onları okuyamıyor)", r.errors) : notice("ok", "Paket alındı", [`${r.written.length} dosya yazıldı`, r.replaced.length ? `${r.replaced.length} öğe değiştirildi (eskisi çöp kutusunda)` : null, r.skipped.length ? `${r.skipped.length} öğe korundu` : null, ren.length ? `yeniden adlandırıldı: ${ren.map(([a, b]) => `${a} → ${b}`).join(", ")}` : null].filter(Boolean)),
      r.written.length ? h("details", {}, h("summary", { class: "small" }, "Yazılan dosyalar"), h("ul", { class: "small num" }, r.written.map(w => h("li", {}, w)))) : null,
      h("div", { class: "row" }, h("a", { class: "btn primary", href: "#/kutuphane" }, "Kütüphaneye git"), h("button", { type: "button", class: "ghost", onclick: first }, "Başka paket"))));
  };
  first();
  return root;
}

/* ---- VECTO wizard --------------------------------------------------------- */
/* A folder of VECTO declaration files -> one diesel vehicle in the library.
   Step 1: drop the folder (or the XML files, or a zip). Step 2: the server
   says what it recognised, what it will build the vehicle from and what it
   had to assume. Step 3: name it and save; the vehicle page opens. */
function vectoWizard() {
  const root = h("div", {});
  let st = null;
  const LABELS = ["Dosyalar", "Kontrol et", "Kaydet"];
  const stepFiles = () => root.replaceChildren(steps(LABELS, 0), h("div", { class: "stack" },
    dropZone({ accept: ".xml,.vecto,.zip", title: "VECTO klasörünü ya da dosyalarını bırakın",
      sub: "Motor, şanzıman, aks, lastik, retarder, birincil araç ve tamamlanmış araç XML dosyaları; bir zip de olur. Tek tek seçmek için tıklayın.",
      multiple: true,
      onFiles: async files => {
        root.replaceChildren(steps(LABELS, 0), h("div", { class: "muted" }, `${files.length} dosya okunuyor…`));
        try { st = await api.uploadMany("/api/import/vecto", files); stepCheck(); }
        catch (e) { stepFiles(); root.append(notice("err", "Dosyalar okunamadı.", [e.message])); }
      } }),
    h("div", { class: "row" }, h("button", { type: "button", class: "ghost", onclick: () => {
      const inp = h("input", { type: "file", webkitdirectory: true, multiple: true, hidden: true });
      inp.addEventListener("change", async () => {
        const files = [...inp.files];
        if (!files.length) return;
        root.replaceChildren(steps(LABELS, 0), h("div", { class: "muted" }, `${files.length} dosya okunuyor…`));
        try { st = await api.uploadMany("/api/import/vecto", files); stepCheck(); }
        catch (e) { stepFiles(); root.append(notice("err", "Dosyalar okunamadı.", [e.message])); }
      });
      document.body.append(inp); inp.click(); inp.remove();
    } }, "Klasör seç…")),
    h("details", {}, h("summary", { class: "small muted" }, "Hangi dosyalar gerekli?"),
      h("div", { class: "doc" }, h("ul", {},
        h("li", {}, "Zorunlu: motor (yakıt haritası, tam yük eğrisi), şanzıman (vites oranları ve kayıp haritaları), aks (oran ve kayıp haritası), lastik (RRC)."),
        h("li", {}, "Araç seviyesi: tamamlanmış araç dosyası (kütle, koltuk, boyutlar, CdxA, klima donanımı) ve birincil araç dosyası (aks düzeni, retarder)."),
        h("li", {}, "Sertifikalı bileşen dosyası varsa, birincil araç dosyasının içindeki kopyaya tercih edilir."),
        h("li", {}, "Aks yük payları, lastik dinamik yarıçapı ve tekerlek ataleti VECTO'nun kendi tablolarından alınır; ikiz dosyasında kaynak olarak yazılır."))))));

  const stepCheck = () => {
    const b = st.bundle, pl = st.plan;
    const fileRows = b.files.map(f => h("tr", { class: f.kind === "unknown" ? "muted" : null },
      h("td", {}, h("b", {}, f.kind_tr), h("div", { class: "small muted" }, f.filename)),
      h("td", {}, f.label || "—", f.certification ? h("div", { class: "small muted" }, `sertifika ${f.certification}`) : null),
      h("td", { class: "small" }, f.facts.map(([k, v]) => h("div", {}, h("span", { class: "muted" }, k + ": "), v)), f.warnings.length ? h("div", { style: "color:var(--err)" }, f.warnings.join(" ")) : null)));
    const planRows = pl.rows.map(r => h("tr", {}, h("td", {}, r.label), h("td", {}, r.value), h("td", { class: "small muted" }, r.source)));
    const next = h("button", { class: "primary", type: "button", disabled: pl.missing.length ? true : null, onclick: stepSave }, "Devam: aracı adlandır ve kaydet");
    root.replaceChildren(steps(LABELS, 1), h("div", { class: "stack" },
      pl.missing.length ? notice("err", "Dijital ikiz için eksik veri var", pl.missing) : null,
      pl.warnings.length ? notice("warn", "Dikkat", pl.warnings) : null,
      h("div", { class: "panel" }, h("h3", {}, "Araç neyle kurulacak"),
        h("p", { class: "small muted", style: "margin:4px 0 8px" }, "Her satırda değerin hangi dosyadan geldiği yazar. Kaynak sütununda “örnek mimari dosyası” ya da “varsayım” görüyorsanız o değeri gerçek araçla doğrulayın."),
        h("div", { class: "scrollx" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["Veri", "Değer", "Kaynak"].map(c => h("th", {}, c)))), h("tbody", {}, planRows)))),
      h("div", { class: "panel" }, h("h3", {}, `Tanınan dosyalar (${b.files.length})`),
        h("div", { class: "scrollx" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["Tür", "Bileşen", "İçerik"].map(c => h("th", {}, c)))), h("tbody", {}, fileRows)))),
      h("div", { class: "savebar" }, next, h("button", { type: "button", class: "ghost", onclick: stepFiles }, "Başka dosyalar seç"))));
  };

  const stepSave = () => {
    const pl = st.plan;
    const nameIn = h("input", { type: "text", value: st.suggested_name });
    const stemIn = h("input", { type: "text", value: st.suggested_stem });
    const varIn = h("input", { type: "text", value: `${pl.model || ""} dizel`.trim(), placeholder: "ör. 13 m, 55 koltuk" });
    const glazIn = numInput("", { unit: "m²", placeholder: "CAD/CFD cam alanı" });
    const notesIn = h("textarea", { placeholder: "Bu dosyalar nereden geldi, hangi araç, hangi tarih?" });
    const over = h("input", { type: "checkbox" });
    root.replaceChildren(steps(LABELS, 2), h("div", { class: "panel stack" },
      h("div", { class: "formgrid" },
        field("Araç adı", nameIn), field("Dosya adı", stemIn, { hint: "library/vehicle/<ad>.yaml" }),
        field("Varyant", varIn),
        field("Toplam cam alanı", glazIn, { hint: "Boş bırakılırsa yan yüzeyin %30'u kabul edilir ve kabin dosyasında KABUL diye yazılır." }),
        h("div", { class: "wide" }, field("Not", notesIn)),
        h("label", { class: "check" }, over, h("span", {}, "Aynı adlı dosyaların üzerine yaz"))),
      h("p", { class: "small muted", style: "margin:0" }, "Kaydedince motor, şanzıman+aks, şasi, kabin ve yardımcı tüketici dosyaları ile araç dosyası yazılır; harita CSV'leri maps/ klasörüne gider. Klima ve yardımcı dosyaları kabul değerleriyle başlar, ölçümle değiştirin."),
      h("div", { class: "row" },
        h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, async () => {
          const r = await api.post(`/api/import/vecto/${st.upload_id}/save`, { name: nameIn.value.trim(), stem: stemIn.value.trim(), variant: varIn.value.trim(),
            glazing_area_m2: parseNum($("input", glazIn).value) || "", notes: notesIn.value, overwrite: over.checked });
          await overview(true); store.set("vehicle", r.stem);
          toast(`Kaydedildi: ${Object.values(r.written).length} dosya`);
          if (r.warnings && r.warnings.length) alert("Dikkat:\n\n" + r.warnings.join("\n"));
          location.hash = `#/arac/${encodeURIComponent(r.stem)}`;
        }) }, "Dijital ikizi kaydet"),
        h("button", { type: "button", class: "ghost", onclick: stepCheck }, "← Geri"))));
  };
  stepFiles();
  return root;
}

/* ---- motor map wizard ---------------------------------------------------- */
function mapWizard({ mode = "hub", onUse } = {}) {
  const root = h("div", {});
  let st = null, fileName = "";
  const render = (step, content) => root.replaceChildren(steps(["Dosya", "Kontrol et", mode === "dialog" ? "Kullan" : "Kaydet"], step), content);

  const stepFile = () => render(0, h("div", { class: "stack" },
    dropZone({ accept: ".csv,.txt,.xlsx,.xlsm,.tsv,.dat", title: "Harita dosyasını bırakın ya da seçin",
      sub: "Excel (.xlsx) ya da CSV/TXT. Başlık satırları, kapak sayfası ve boş hücreler sorun değil.",
      onFile: async f => {
        fileName = f.name;
        root.replaceChildren(steps(["Dosya", "Kontrol et", "Kaydet"], 0), h("div", { class: "muted" }, `${f.name} okunuyor…`));
        try { st = await api.upload("/api/import/map", f); stepCheck(); }
        catch (e) { stepFile(); root.append(notice("err", "Dosya harita olarak okunamadı.", [e.message])); }
      } }),
    h("details", {}, h("summary", { class: "small muted" }, "Hangi biçimler tanınıyor?"),
      h("div", { class: "doc" }, h("ul", {},
        h("li", {}, "Izgara: üst satırda devir, sol sütunda tork (ya da tersi), hücrelerde verim."),
        h("li", {}, "Uzun liste: devir, tork, verim sütunları, her satır bir çalışma noktası; sütun sırası önemsiz."),
        h("li", {}, "Verim kesir (0,95) ya da yüzde (95); veya kayıp gücü kW/W — verime çevrilir."),
        h("li", {}, "Negatif tork satırları (jeneratör bölgesi) atlanır; rejenerasyon motor haritasının aynasıdır."))))));

  const stepCheck = () => {
    const pv = st.preview;
    const redetect = async over => {
      try { st = await api.post(`/api/import/map/${st.upload_id}`, { ...st.settings, ...over }); stepCheck(); }
      catch (e) { toast(e.message, "err"); }
    };
    const setRow = (key, label, opts) => h("div", { class: "item" },
      h("div", { class: "k" }, label, st.settings[key] !== st.detected[key] ? h("span", { class: "badge plain" }, "sizin seçiminiz") : confBadge(st.confidence[key])),
      seg(opts, st.settings[key], v => redetect({ [key]: v })));
    const t = st.table;
    const left = h("div", { class: "panel" },
      h("div", { class: "row", style: "margin-bottom:8px" }, h("h3", {}, fileName), h("span", { class: "spacer" }),
        h("span", { class: "muted small num" }, `${pv.shape[1]} devir × ${pv.shape[0]} tork noktası`)),
      heatmap(pv),
      h("p", { class: "small", style: "margin:10px 0 0" }, "Tepe verim ", h("b", {}, `%${fmt(pv.peak.efficiency * 100, 1)}`),
        ` — ${fmt(pv.peak.speed_rpm, 0)} rpm, ${fmt(pv.peak.torque_nm, 0)} Nm. Aralık %${fmt(pv.range[0] * 100, 1)} – %${fmt(pv.range[1] * 100, 1)}.`));
    const right = h("div", { class: "panel detect" },
      h("div", {}, h("h3", {}, "Algılanan biçim"), h("p", { class: "small muted", style: "margin:2px 0 0" }, "Yanlış görünen bir ayarı değiştirin; önizleme hemen güncellenir.")),
      setRow("orientation", "Devir ekseni", [["speed_in_columns", "Üst satırda"], ["speed_in_rows", "Sol sütunda"]]),
      setRow("speed_unit", "Devir birimi", [["rpm", "rpm"], ["rad_s", "rad/s"]]),
      setRow("value_type", "Hücre değerleri", [["efficiency_fraction", "Verim 0–1"], ["efficiency_percent", "Verim %"], ["loss_kw", "Kayıp kW"], ["loss_w", "Kayıp W"]]),
      t.sheets && t.sheets.length > 1 ? h("div", { class: "item" }, h("div", { class: "k" }, "Excel sayfası"),
        (() => { const sel = select(t.sheets.map(n => [n, n]), t.sheet); sel.addEventListener("change", () => redetect({ sheet: sel.value })); return sel; })()) : null,
      h("dl", { class: "kv" },
        h("dt", {}, "Dosya"), h("dd", {}, t.kind === "excel" ? `Excel, sayfa “${t.sheet}”` : `Metin, ayraç ${t.delimiter}, ondalık “${t.decimal}”`),
        h("dt", {}, "Yerleşim"), h("dd", {}, st.layout === "long" ? "Uzun liste (3 sütun)" : "Izgara"),
        h("dt", {}, "Doldurulan"), h("dd", {}, `${st.filled_cells} boş hücre`)),
      st.warnings.length ? notice("warn", "Dikkat", st.warnings) : null);
    const next = h("button", { class: "primary", type: "button", onclick: () => mode === "dialog" ? onUse(st, fileName) : stepSave() },
      mode === "dialog" ? "Bu haritayı kullan" : "Devam: nereye kaydedilsin?");
    render(1, h("div", {}, h("div", { class: "grid-2", style: "grid-template-columns:minmax(0,1.5fr) minmax(0,1fr)" }, left, right),
      h("div", { class: "savebar" }, next, h("button", { type: "button", class: "ghost", onclick: stepFile }, "Başka dosya seç"))));
  };

  const stepSave = () => {
    const motors = (OV.components.find(g => g.kind === "motor") || { items: [] }).items;
    let target = motors.length ? "existing" : "new";
    const area = h("div", {});
    const draw = () => {
      if (target === "existing") {
        const sel = select(motors.map(m => [m.stem, `${m.name} — ${m.summary}`]), motors[0] && motors[0].stem);
        area.replaceChildren(h("div", { class: "stack" },
          field("Motor", sel, { hint: "Haritası bu dosyayla değiştirilir. Motor dosyasının diğer satırları ve yorumları korunur." }),
          h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, async () => {
            const r = await api.post(`/api/import/map/${st.upload_id}/attach`, { motor: sel.value });
            toast(`Kaydedildi ve bağlandı: ${r.saved}`);
            await overview(true);
            location.hash = `#/bilesen/motor/${encodeURIComponent(r.motor)}`;
          }) }, "Haritayı kaydet ve motora bağla")));
      } else {
        area.replaceChildren(h("div", { class: "stack" },
          h("p", { class: "small", style: "margin:0" }, "Motor formu açılır; harita hazır bağlı gelir. Tepe güç, tork ve azami devri veri sayfasından girin."),
          h("button", { class: "primary", type: "button", onclick: () => { pending.map = { st, fileName }; location.hash = "#/bilesen/motor/yeni"; } }, "Yeni motor formunu aç")));
      }
    };
    render(2, h("div", { class: "panel stack" },
      seg([["existing", "Mevcut motora bağla"], ["new", "Yeni motor oluştur"]], target, v => { target = v; draw(); }),
      area, h("button", { type: "button", class: "ghost", onclick: stepCheck }, "← Önizlemeye dön")));
    draw();
  };
  stepFile();
  return root;
}

/* ---- route wizard -------------------------------------------------------- */
function routeWizard() {
  const root = h("div", {});
  let file = null, st = null;
  const spacing = numInput("", { unit: "m", placeholder: 400 });
  const vdef = numInput(50, { unit: "km/h" });
  const load = async () => {
    root.replaceChildren(steps(["Dosya", "Kontrol et", "Kaydet"], 1), h("div", { class: "muted" }, `${file.name} okunuyor…`));
    try {
      st = await api.upload("/api/import/route", file, { stop_spacing_m: parseNum($("input", spacing).value) || "", default_speed_kmh: parseNum($("input", vdef).value) || 50 });
      check();
    } catch (e) { first(); root.append(notice("err", "Güzergâh okunamadı.", [e.message])); }
  };
  const first = () => root.replaceChildren(steps(["Dosya", "Kontrol et", "Kaydet"], 0), h("div", { class: "stack" },
    dropZone({ accept: ".gpx,.csv,.txt,.xlsx", title: "GPX ya da tablo dosyasını bırakın",
      sub: "GPX: iz noktaları + (varsa) durak noktaları. Tablo: mesafe (m ya da km), rakım, isteğe bağlı hız limiti ve durak sütunu.",
      onFile: f => { file = f; load(); } }),
    h("div", { class: "formgrid" },
      field("Durak aralığı", spacing, { hint: "Yalnızca dosyada durak yoksa kullanılır." }),
      field("Hız limiti", vdef, { hint: "Dosyada hız limiti yoksa bu değer kullanılır." }))));
  const check = () => {
    const nameIn = h("input", { type: "text", value: file.name.replace(/\.[^.]+$/, "") });
    const p = st.profile;
    root.replaceChildren(steps(["Dosya", "Kontrol et", "Kaydet"], 1), h("div", { class: "stack" },
      h("div", { class: "kpis" },
        h("div", {}, h("div", { class: "k" }, "Uzunluk"), h("div", { class: "v" }, fmt(st.length_km, 2), h("small", {}, "km"))),
        h("div", {}, h("div", { class: "k" }, "Durak"), h("div", { class: "v" }, st.stops)),
        h("div", {}, h("div", { class: "k" }, "Toplam tırmanış"), h("div", { class: "v" }, fmt(st.climb_m, 0), h("small", {}, "m"))),
        h("div", {}, h("div", { class: "k" }, "Rakım"), h("div", { class: "v" }, `${fmt(st.elev_range[0], 0)}–${fmt(st.elev_range[1], 0)}`, h("small", {}, "m")))),
      h("div", { class: "panel" }, lineChart({ x: p.distance_km, series: [{ label: "Rakım", color: css("--s1"), y: p.elevation_m }], xLabel: "mesafe (km)", yLabel: "Rakım (m)", xFmt: v => fmt(v, 1), yFmt: v => fmt(v, 0) + " m", height: 220 }),
        h("p", { class: "small muted", style: "margin:6px 0 0" }, st.stops_km.length ? `Duraklar: ${st.stops_km.map(v => fmt(v, 2)).join(" · ")} km` : "")),
      st.warnings.length ? notice("warn", "Dikkat", st.warnings) : null,
      h("div", { class: "panel stack" }, field("Güzergâh adı", nameIn, { hint: "Simülasyon sayfasında bu adla görünür." }),
        h("div", { class: "row" },
          h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, async () => {
            const r = await api.post(`/api/import/route/${st.upload_id}/save`, { name: nameIn.value });
            await overview(true);
            store.set("simRoute", r.stem);
            toast(`Kaydedildi: ${r.saved}`);
            location.hash = "#/simulasyon";
          }) }, "Kaydet ve simülasyona geç"),
          h("button", { type: "button", class: "ghost", onclick: first }, "Başka dosya seç")))));
  };
  first();
  return root;
}

/* ---- curve target -------------------------------------------------------- */
function curveTarget() {
  const CURVES = [["battery", "ocv_curve", "Batarya OCV eğrisi (SOC → hücre gerilimi)"],
    ["battery", "charge_accept_derate", "Soğukta şarj kabulü (sıcaklık → oran)"],
    ["hvac", "heat_pump_cop", "Isı pompası COP (sıcaklık → COP)"],
    ["hvac", "cooling_cop", "Soğutma COP (sıcaklık → COP)"]];
  const which = select(CURVES.map((c, i) => [String(i), c[2]]), "0");
  const compSel = h("select", {});
  const fill = () => {
    const [kind] = CURVES[+which.value];
    const items = (OV.components.find(g => g.kind === kind) || { items: [] }).items;
    compSel.replaceChildren(...items.map(it => h("option", { value: it.stem }, it.name)));
  };
  which.addEventListener("change", fill); fill();
  return h("div", { class: "panel stack" },
    h("p", { class: "small", style: "margin:0" }, "Eğriler bir bileşenin parçasıdır. Eğriyi ve bileşeni seçin; bileşen formu açılır ve eğri alanından dosyayı yüklersiniz."),
    h("div", { class: "formgrid" }, field("Eğri", which), field("Bileşen", compSel)),
    h("div", {}, h("button", { class: "primary", type: "button", onclick: () => {
      const [kind, fieldName] = CURVES[+which.value];
      pending.focusField = fieldName;
      location.hash = `#/bilesen/${kind}/${encodeURIComponent(compSel.value)}`;
    } }, "Bileşeni aç")));
}

/* ================================================================ component editor */
async function pageComponent(kind, stem) {
  const isNew = !stem || stem === "yeni";
  const sch = await api.get(`/api/schema/${kind}`);
  const cur = isNew ? null : await api.get(`/api/component/${kind}/${encodeURIComponent(stem)}`);
  const values = cur ? cur.values : Object.fromEntries(sch.fields.map(f => [f.name, f.default]));
  if (isNew) values.name = "";
  const controls = {};
  const rawRefs = (cur && cur.raw_refs) || {};
  const mapState = { uploadId: null, preview: cur && cur.map_preview, rawRef: rawRefs.efficiency_map || null,
    source: rawRefs.efficiency_map || null, removed: false };
  if (isNew && kind === "motor" && pending.map) {
    mapState.uploadId = pending.map.st.upload_id; mapState.preview = pending.map.st.preview;
    mapState.source = `yeni yüklenen: ${pending.map.fileName}`;
    delete pending.map;
  }
  const hasMap = () => !mapState.removed && Boolean(mapState.uploadId || mapState.rawRef);

  const root = h("div", {});
  root.append(pageHead(sch.label, isNew ? `Yeni ${sch.label.toLocaleLowerCase("tr-TR")}` : values.name,
    isNew ? "Önce ana alanlar görünür: bunlar bir veri sayfasında bulunan değerlerdir. Diğerleri makul varsayılanlarla dolu gelir, ‘Gelişmiş’ altında." : cur.summary));

  const nameIn = h("input", { type: "text", value: values.name || "", placeholder: "ör. Tedarikçi X 280 kW", required: true });
  const descIn = h("textarea", { placeholder: "Veri nereden geldi? Hangi test, hangi tarih?" }, cur ? cur.description : "");
  root.append(h("div", { class: "panel" }, h("div", { class: "formgrid" }, h("div", { class: "wide" }, field("Ad", nameIn)), h("div", { class: "wide" }, field("Açıklama ve kaynak", descIn)))));

  const renderField = f => {
    const v = values[f.name];
    const docBox = f.doc ? h("div", { class: "doc", hidden: true, lang: "en" }, f.doc) : null;
    const helpBtn = f.doc ? h("button", { type: "button", class: "help", title: "Açıklama", "aria-label": "Açıklama", onclick: e => { e.preventDefault(); docBox.hidden = !docBox.hidden; } }, "?") : null;
    const lab = h("div", { class: "lab" }, f.label, h("span", { class: "spacer" }), helpBtn);
    let ctl;
    if (f.type === "float" || f.type === "int") {
      ctl = numInput(v, { unit: f.unit, placeholder: f.default });
      controls[f.name] = () => { const r = $("input", ctl).value; if (r.trim() === "") return f.optional ? "" : null; const n = parseNum(r); if (Number.isNaN(n)) throw new Error(`${f.label}: sayı değil`); return f.type === "int" ? Math.round(n) : n; };
    } else if (f.type === "bool") {
      const cb = h("input", { type: "checkbox", checked: v ? true : null });
      controls[f.name] = () => cb.checked;
      const wrap = h("div", { class: "field" }, h("label", { class: "check" }, cb, h("span", {}, h("b", {}, f.label), f.name === "validated" ? h("div", { class: "hint" }, "Yalnızca bu bileşenin verisi gerçek ölçümden geldiyse ve Doğrulama sayfasında model–ölçüm karşılaştırması geçtiyse işaretleyin.") : null)), helpBtn, docBox);
      return wrap;
    } else if (f.type === "choice") {
      ctl = select(f.choices, v); controls[f.name] = () => ctl.value;
    } else if (f.type === "list") {
      ctl = h("input", { type: "text", class: "numeric", value: (v || []).map(fmtInput).join(" ; ") });
      controls[f.name] = () => ctl.value.split(/[;\s]+/).filter(Boolean).map(x => { const n = parseNum(x); if (Number.isNaN(n)) throw new Error(`${f.label}: sayı değil`); return n; });
      return h("div", { class: "field" }, lab, ctl, h("div", { class: "hint" }, "Birden fazla oran için noktalı virgülle ayırın: 3,2 ; 1,0"), docBox);
    } else if (f.type === "text") {
      ctl = h("textarea", {}, v || ""); controls[f.name] = () => ctl.value;
    } else if (f.type === "curve") {
      return curveField(f, v, lab, docBox, controls, () => { try { return controls.cells_series ? controls.cells_series() : values.cells_series; } catch { return values.cells_series; } });
    } else if (f.type === "map") {
      if (kind !== "motor") {
        const ref = rawRefs[f.name] || (cur && cur.values[f.name] && cur.values[f.name].source);
        const fm = f.name === "fuel_map" && cur && cur.fuel_map_preview;
        return h("div", { class: "field" }, lab, h("div", { class: "subfield small" },
          ref ? h("span", {}, h("span", { class: "badge ok" }, "yüklü"), ` ${ref} — formda değiştirilemez; kaydederken korunur. Yeni bir harita için VECTO dosyasını yeniden içe aktarın.`)
              : h("span", {}, h("span", { class: "badge plain" }, "yok"), " Sabit verim ya da sentetik eğri kullanılıyor. Bu harita VECTO dosyasından ya da YAML'dan eklenir."),
          fm ? h("div", { class: "muted", style: "margin-top:4px" }, `${fm.speed_rpm.length} devir × ${fm.torque_nm.length} tork noktası, ${fm.speed_rpm[0]}–${fm.speed_rpm[fm.speed_rpm.length - 1]} rpm`) : null), docBox);
      }
      return mapField(f, lab, docBox, mapState, () => refreshIgnored(), hasMap);
    } else {
      ctl = h("input", { type: "text", value: v || "" }); controls[f.name] = () => ctl.value;
    }
    const wrapper = h("div", { class: "field", "data-field": f.name }, lab, ctl, docBox);
    return wrapper;
  };
  const refreshIgnored = () => sch.fields.filter(f => f.ignored_if).forEach(f => {
    const el = $(`[data-field="${f.name}"]`, root); if (!el) return;
    el.classList.toggle("ignored", hasMap());
    let hint = $(".ign", el);
    if (hasMap() && !hint) el.append(h("div", { class: "hint ign" }, "Harita yüklü olduğu için kullanılmıyor."));
    if (!hasMap() && hint) hint.remove();
  });

  const bySection = list => { const m = new Map(); list.forEach(f => { if (!m.has(f.section)) m.set(f.section, []); m.get(f.section).push(f); }); return m; };
  const skip = new Set(["name", "notes", "validated"]);
  const ess = sch.fields.filter(f => f.essential && !skip.has(f.name));
  const adv = sch.fields.filter(f => !f.essential && !skip.has(f.name));
  const essBox = h("div", { class: "panel" });
  for (const [sec, fs] of bySection(ess)) essBox.append(h("div", { class: "section-title" }, sec), h("div", { class: "formgrid" }, fs.map(f => h("div", { class: f.type === "map" || f.type === "curve" ? "wide" : null }, renderField(f)))));
  root.append(essBox);
  if (adv.length) {
    const d = h("details", { class: "panel", style: "margin-top:16px" }, h("summary", {}, h("b", {}, `Gelişmiş parametreler (${adv.length})`), h("span", { class: "muted small" }, " — varsayılanlar yayımlanmış tipik değerlerdir")));
    for (const [sec, fs] of bySection(adv)) d.append(h("div", { class: "section-title" }, sec), h("div", { class: "formgrid" }, fs.map(f => h("div", { class: f.type === "curve" || f.type === "map" ? "wide" : null }, renderField(f)))));
    root.append(d);
    if (pending.focusField && adv.some(f => f.name === pending.focusField)) d.open = true;
  }
  const valF = sch.fields.find(f => f.name === "validated");
  const notesF = sch.fields.find(f => f.name === "notes");
  root.append(h("div", { class: "panel", style: "margin-top:16px" }, h("div", { class: "formgrid" },
    valF ? h("div", { class: "wide" }, renderField(valF)) : null, notesF ? h("div", { class: "wide" }, renderField(notesF)) : null)));

  const collect = () => {
    const out = {};
    for (const [k, get] of Object.entries(controls)) { const v = get(); if (v !== null && v !== undefined) out[k] = v; }
    out.name = nameIn.value.trim();
    if (!out.name) throw new Error("Ad boş olamaz.");
    // Map fields travel as file references, never as values.
    for (const f of sch.fields.filter(f => f.type === "map")) {
      delete out[f.name];
      if (kind === "motor" && f.name === "efficiency_map") {
        if (!mapState.removed && !mapState.uploadId && mapState.rawRef) out[f.name] = mapState.rawRef;
      } else if (rawRefs[f.name]) out[f.name] = rawRefs[f.name];
    }
    return out;
  };
  const save = async (stemOut, overwrite) => {
    const body = { stem: stemOut, values: collect(), description: descIn.value, overwrite, extra: (cur && cur.extra) || {} };
    if (kind === "motor" && !mapState.removed && mapState.uploadId) body.map_uploads = { efficiency_map: mapState.uploadId };
    const r = await api.post(`/api/component/${kind}`, body);
    await overview(true);
    toast(`Kaydedildi: ${r.saved}`);
    location.hash = `#/bilesen/${kind}/${encodeURIComponent(r.stem)}`;
  };
  const bar = h("div", { class: "savebar" });
  if (isNew) bar.append(h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, () => save(nameIn.value, false)) }, "Kütüphaneye kaydet"));
  else bar.append(
    h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, () => {
      if (!confirm("Dosya bu değerlerle yeniden yazılacak. Elle eklenmiş yorum satırları korunmaz. Devam edilsin mi?")) return;
      return save(stem, true);
    }) }, "Değişiklikleri kaydet"),
    h("button", { type: "button", onclick: e => busy(e.currentTarget, () => { const n = prompt("Kopyanın adı:", `${values.name} kopya`); if (n) return save(n, false); }) }, "Kopya olarak kaydet"),
    menu("Diğer", [
      { label: "İndir (zip)", href: exportUrl(kind, stem), download: "", hint: "yaml + haritalar" },
      { label: "Dosyayı gör / düzenle", onclick: () => openFileDialog(kind, stem, values.name) }, "-",
      { label: "Sil", danger: true, onclick: async () => { if (await deleteItem(kind, stem, values.name)) location.hash = "#/kutuphane"; } }], { up: true }));
  bar.append(h("span", { class: "spacer" }), h("a", { class: "btn ghost", href: "#/kutuphane" }, "Vazgeç"));
  root.append(bar);
  setTimeout(() => {
    refreshIgnored();
    if (pending.focusField) { const el = $(`[data-curve="${pending.focusField}"]`, root); if (el) { el.scrollIntoView({ block: "center" }); el.style.outline = "3px solid var(--amber)"; } delete pending.focusField; }
  });
  return root;
}

function mapField(f, lab, docBox, mapState, changed, hasMap) {
  const box = h("div", { class: "subfield" });
  const draw = () => {
    const synthetic = !hasMap();
    box.replaceChildren(
      h("div", { class: "row", style: "align-items:flex-start" },
        h("div", { style: "flex:1;min-width:240px" },
          synthetic ? h("p", { class: "small", style: "margin:0 0 8px" }, h("span", { class: "badge plain" }, "sentetik"), " Harita yok: verim, tepe verim noktasından bir kayıp modeliyle üretiliyor (sağdaki önizleme). Tedarikçi haritası gelince yükleyin.")
            : h("p", { class: "small", style: "margin:0 0 8px" }, h("span", { class: "badge ok" }, mapState.uploadId ? "yeni" : "yüklü"), " ", mapState.source || ""),
          h("div", { class: "row" },
            h("button", { type: "button", class: "primary", onclick: () => openMapDialog(mapState, () => { draw(); changed(); }) }, synthetic ? "Harita yükle" : "Haritayı değiştir"),
            !synthetic ? h("button", { type: "button", class: "ghost danger", onclick: () => { mapState.removed = true; mapState.uploadId = null; mapState.preview = null; draw(); changed(); } }, "Haritayı kaldır") : null)),
        mapState.preview ? h("div", { style: "width:280px" }, heatmap(mapState.preview, { width: 280, height: 170, compact: true })) : null));
  };
  draw();
  return h("div", { class: "field" }, lab, box, docBox);
}

function openMapDialog(mapState, done) {
  const dlg = h("dialog", {});
  const close = () => { dlg.close(); dlg.remove(); };
  dlg.append(h("div", { class: "dlg-head" }, h("h2", {}, "Verim haritası yükle"), h("span", { class: "spacer" }), h("button", { type: "button", class: "ghost", onclick: close }, "Kapat")),
    h("div", { class: "dlg-body" }, mapWizard({ mode: "dialog", onUse: (st, name) => {
      mapState.uploadId = st.upload_id; mapState.preview = st.preview; mapState.source = `yeni yüklenen: ${name}`; mapState.removed = false;
      close(); done(); toast("Harita forma eklendi. Kaydedince dosyaya yazılır.");
    } })));
  document.body.append(dlg);
  dlg.showModal();
}

function curveField(f, v, lab, docBox, controls, cellsSeries) {
  let points = v && v.points ? v.points : null, source = v && v.source, reset = false;
  const box = h("div", { class: "subfield", "data-curve": f.name });
  const xPct = f.name === "ocv_curve" || f.name === "resistance_soc_factor";
  const xLabel = xPct ? "SOC" : /load|drag|torque|speed|rpm/.test(f.name) ? "devir (rpm)" : "sıcaklık (°C)";
  const isDefault = () => f.default && JSON.stringify(points) === JSON.stringify(f.default.points);
  controls[f.name] = () => reset || !points || isDefault() ? null : { points, source };
  const draw = (warn) => box.replaceChildren(h("div", { class: "row", style: "align-items:flex-start" },
    h("div", { style: "width:300px" }, curveMini(points, { xLabel, xPct })),
    h("div", { style: "flex:1;min-width:220px" },
      h("p", { class: "small", style: "margin:0 0 8px" }, points ? `${points.length} nokta · ` : "", h("span", { class: "muted" }, reset ? "varsayılana dönecek" : (source || ""))),
      dropZone({ compact: true, accept: ".csv,.txt,.xlsx,.xlsm", title: "Dosyadan yükle", sub: "İki sütun: x ve y. Başlık, %, ondalık virgül sorun değil.",
        onFile: async file => {
          try {
            const ns = cellsSeries && cellsSeries();
            const r = await api.upload("/api/import/curve", file, { field: f.name, cells_series: ns || "" });
            points = r.points; source = `yüklendi: ${file.name}`; reset = false; draw(r.warnings);
            toast("Eğri forma eklendi. Kaydedince dosyaya yazılır.");
          } catch (e) { draw([e.message]); }
        } }),
      h("button", { type: "button", class: "ghost", style: "margin-top:6px", onclick: () => { reset = true; draw(); } }, "Varsayılana dön"),
      warn && warn.length ? notice("warn", null, warn) : null)));
  draw();
  return h("div", { class: "field" }, lab, box, docBox);
}

/* ================================================================ vehicle builder */
async function pageVehicle(stem) {
  const isNew = !stem || stem === "yeni";
  const ov = await overview(true);
  const csch = await api.get("/api/schema/control");
  const cur = isNew ? null : await api.get(`/api/vehicle/${encodeURIComponent(stem)}`);
  let powertrain = cur ? cur.powertrain : store.get("newPowertrain", "battery_electric");
  const root = h("div", {});
  root.append(pageHead("Araç kur", isNew ? "Yeni araç" : `${cur.name}`,
    "Bir araç, kütüphanedeki bileşenlerin bir seçimidir. Bir bileşeni değiştirmek için başka bir tane seçin; varyant için bu aracı kopya olarak kaydedin."));
  const nameIn = h("input", { type: "text", value: cur ? cur.name : "", placeholder: "ör. 12 m şehir içi BE" });
  const varIn = h("input", { type: "text", value: cur ? cur.variant : "", placeholder: "ör. 350 kWh / ısı pompası" });
  const seats = numInput(cur ? cur.seats : 28), stand = numInput(cur ? cur.standees : 60);
  const tankIn = numInput(cur && cur.fuel_tank_l ? cur.fuel_tank_l : 400, { unit: "l" });
  const descIn = h("textarea", {}, cur ? cur.description : "");
  const ptBox = h("div", { class: "field" }, h("div", { class: "lab" }, "Güç aktarma"));
  const tankField = field("Yakıt deposu", tankIn, { hint: "Yalnızca menzil ve oyun içi yakıt göstergesi için." });
  root.append(h("div", { class: "panel" }, h("div", { class: "formgrid" },
    field("Ad", nameIn), field("Varyant", varIn), ptBox, field("Oturan yolcu", seats), field("Ayakta yolcu", stand), tankField,
    h("div", { class: "wide" }, field("Açıklama", descIn)))));

  const slotSel = {};
  const slotPanel = h("div", { class: "panel", style: "margin-top:16px" }, h("h2", {}, "Bileşenler"));
  const grid = h("div", { class: "formgrid", style: "margin-top:10px" });
  const ctlPanel = h("div", { class: "panel", style: "margin-top:16px" });
  const drawSlots = () => {
    for (const k of Object.keys(slotSel)) delete slotSel[k];
    grid.replaceChildren();
    const SLOTS = Object.entries(ov.slots_by_powertrain[powertrain] || {});
    SLOTS.forEach(([slot, kind]) => {
      const g = ov.components.find(x => x.kind === kind);
      const items = g ? g.items.filter(i => !i.error) : [];
      const sel = select(items.map(i => [i.stem, i.name]), cur && cur.slots[slot] ? cur.slots[slot] : items[0] && items[0].stem);
      const info = h("div", { class: "hint" });
      const upd = () => { const it = items.find(i => i.stem === sel.value); info.replaceChildren(it ? h("span", {}, it.summary, " ", valBadge(it.validated)) : h("span", {}, "Bu tipte bileşen yok. ", kind === "engine" || kind === "transmission" ? h("a", { href: "#/yukle/vecto" }, "VECTO dosyalarından ekleyin") : null)); };
      sel.addEventListener("change", upd); upd();
      slotSel[slot] = sel;
      grid.append(h("div", { class: "field" }, h("div", { class: "lab" }, g ? g.label : kind, h("span", { class: "spacer" }), h("a", { href: `#/bilesen/${kind}/yeni`, class: "small" }, "yeni ekle")), sel, info));
    });
    tankField.hidden = powertrain !== "diesel";
    ctlPanel.hidden = powertrain === "diesel";
  };
  if (isNew) ptBox.append(seg(ov.powertrains, powertrain, v => { powertrain = v; store.set("newPowertrain", v); drawSlots(); }));
  else ptBox.append(h("div", { class: "small", style: "padding:8px 0" }, h("span", { class: "badge plain" }, (ov.powertrains.find(x => x[0] === powertrain) || [0, powertrain])[1]), " ", h("span", { class: "muted" }, "güç aktarma tipi sonradan değişmez; yeni araç kurun")));
  drawSlots();
  slotPanel.append(grid);
  root.append(slotPanel);

  const controls = {};
  const cvals = cur ? cur.control : Object.fromEntries(csch.fields.map(f => [f.name, f.default]));
  const ctlField = f => {
    const v = cvals[f.name];
    if (f.type === "bool") { const cb = h("input", { type: "checkbox", checked: v ? true : null }); controls[f.name] = () => cb.checked; return h("label", { class: "check" }, cb, h("span", {}, f.label)); }
    const inp = numInput(v, { unit: f.unit, placeholder: f.default });
    controls[f.name] = () => { const r = $("input", inp).value; if (!r.trim()) return f.optional ? "" : null; const n = parseNum(r); if (Number.isNaN(n)) throw new Error(`${f.label}: sayı değil`); return n; };
    return field(f.label, inp, { unit: null });
  };
  const ess = csch.fields.filter(f => f.essential), adv = csch.fields.filter(f => !f.essential);
  ctlPanel.append(h("h2", {}, "Kontrol stratejisi"),
    h("div", { class: "formgrid", style: "margin-top:10px" }, ess.map(ctlField)),
    h("details", { style: "margin-top:14px" }, h("summary", { class: "small" }, `Rejenerasyon ve ön koşullandırma ayrıntıları (${adv.length})`),
      h("div", { class: "formgrid", style: "margin-top:10px" }, adv.map(ctlField))));
  ctlPanel.hidden = powertrain === "diesel";
  root.append(ctlPanel);

  const collect = () => {
    const control = {};
    if (powertrain !== "diesel") for (const [k, g] of Object.entries(controls)) { const v = g(); if (v !== null) control[k] = v; }
    const slots = Object.fromEntries(Object.entries(slotSel).map(([k, s]) => [k, s.value]));
    const n = nameIn.value.trim(); if (!n) throw new Error("Araç adı boş olamaz.");
    return { name: n, variant: varIn.value.trim(), seats: parseNum($("input", seats).value) || 0,
      standees: parseNum($("input", stand).value) || 0, slots, control, description: descIn.value,
      powertrain, fuel_tank_l: powertrain === "diesel" ? (parseNum($("input", tankIn).value) || 400) : null };
  };
  const save = async (stemOut, overwrite) => {
    const r = await api.post("/api/vehicle", { ...collect(), stem: stemOut, overwrite });
    await overview(true); store.set("vehicle", r.stem);
    toast(`Kaydedildi: ${r.saved}`);
    location.hash = `#/arac/${encodeURIComponent(r.stem)}`;
  };
  const bar = h("div", { class: "savebar" });
  if (isNew) bar.append(h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, () => save(`${nameIn.value} ${varIn.value}`, false)) }, "Aracı kaydet"));
  else bar.append(
    h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, () => { if (confirm("Araç dosyası bu seçimlerle yeniden yazılacak. Devam edilsin mi?")) return save(stem, true); }) }, "Değişiklikleri kaydet"),
    h("button", { type: "button", onclick: e => busy(e.currentTarget, () => { const n = prompt("Yeni varyantın adı (ör. 400 kWh / ısı pompası):", varIn.value ? `${varIn.value} v2` : "varyant"); if (n) { varIn.value = n; return save(`${nameIn.value} ${n}`, false); } }) }, "Varyant olarak kaydet"),
    h("button", { type: "button", onclick: () => { store.set("vehicle", stem); location.hash = "#/simulasyon"; } }, "Simüle et"),
    menu("Diğer", [
      { label: "İndir (paket)", href: exportUrl("vehicle", stem), download: "", hint: "araç + bileşenler" },
      { label: "Dosyayı gör / düzenle", onclick: () => openFileDialog("vehicle", stem, cur.name) }, "-",
      { label: "Sil", danger: true, onclick: async () => { if (await deleteItem("vehicle", stem, cur.name)) location.hash = "#/kutuphane"; } }], { up: true }));
  bar.append(h("span", { class: "spacer" }), h("a", { class: "btn ghost", href: "#/kutuphane" }, "Kütüphane"));
  root.append(bar);
  return root;
}

/* ================================================================ simulation
   Full-bleed 3D playback: the scene fills the page, glass panels float over it.
   One animation loop owns the clock; it interpolates the solver's timeline and
   hands the same state to the 3D stage and to the telemetry panels. */
const EV_ICON = {
  stop: "M5 3h14v18H5zM12 3v18M9 12h.01M15 12h.01",
  climb: "M4 18 20 6M13 6h7v7",
  descent: "M4 6l16 12M13 18h7v-7",
  regen: "M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5",
  hvac: "M12 2v20M4.9 7l14.2 10M4.9 17 19.1 7",
  soc: "M3 7h16v10H3zM19 10h2v4h-2M6 10v4",
  signal: "M9 2h6v20H9zM12 6.5h.01M12 12h.01M12 17.5h.01",
  jam: "M2 15h8v3H2zM14 15h8v3h-8zM3 15l1.5-3h4L10 15M15 15l1.5-3h4L22 15",
};
const SPEEDS = [1, 5, 10, 30, 60];

/* Line and timetable simulation lives in network.js. */
async function pageNetwork() {
  const ov = await overview(true);
  const mod = await import("/static/network.js");
  return mod.pageNetwork({ h, api, store, fmt, parseNum, seg, select, field, numInput, toast, busy, notice, ov,
                           pageHead, setCleanup: f => { pageCleanup = f; } });
}

/* Free drive lives in its own module (drive.js); it gets the page helpers here. */
async function pageDrive() {
  const ov = await overview(true);
  const mod = await import("/static/drive.js");
  return mod.pageDrive({ h, api, store, fmt, parseNum, seg, select, field, numInput, toast, busy, notice, ov,
                         raster: Sign.raster, setCleanup: f => { pageCleanup = f; } });
}

async function pageSim() {
  const ov = await overview(true);
  const root = h("div", { class: "simx" });
  if (!ov.vehicles.length) {
    root.append(h("div", { class: "simx-empty-lib" }, notice("warn", "Önce bir araç kurun.", []), h("a", { class: "btn", href: "#/arac/yeni" }, "Araç kur")));
    return root;
  }
  const vehicles = ov.vehicles.filter(v => !v.error);

  /* ---------------- setup panel (left) ---------------- */
  const vSel = select(vehicles.map(v => [v.stem, `${v.name} — ${v.variant}`]), store.get("vehicle", vehicles[0].stem));
  let dutyKind = ov.routes.length ? store.get("dutyKind", "route") : "cycle";
  const dutySel = h("select", {});
  const fillDuty = () => {
    const list = dutyKind === "route" ? ov.routes : ov.cycles;
    const want = dutyKind === "route" ? store.get("simRoute", list[0]) : store.get("simCycle", "cbdbus");
    dutySel.replaceChildren(...list.map(n => h("option", { value: n, selected: n === want ? true : null }, n)));
  };
  fillDuty();
  let trafficLevel = store.get("simTraffic", "orta");
  const trafficBox = h("div", {});
  const drawTraffic = () => trafficBox.replaceChildren(
    h("div", { class: "lab simx-lab" }, "Trafik"),
    dutyKind === "route"
      ? h("div", {}, seg(ov.traffic_levels, trafficLevel, v => { trafficLevel = v; store.set("simTraffic", v); }),
          h("div", { class: "hint" }, "Işıklar, kuyruklar ve dur-kalk tüketime dahil edilir."))
      : h("div", { class: "hint" }, "Standart çevrimlerin hız profili, kaydedildiği andaki trafiği zaten içerir."));
  drawTraffic();
  const repeat = numInput(store.get("simRepeat", 1));
  const amb = h("input", { type: "range", min: -25, max: 45, step: 1, value: store.get("simAmb", 20), "aria-label": "Dış sıcaklık" });
  const ambOut = h("b", { class: "num" });
  const sol = h("input", { type: "range", min: 0, max: 1000, step: 50, value: store.get("simSol", 0), "aria-label": "Güneş ışınımı" });
  const solOut = h("b", { class: "num" });
  const updRanges = () => { ambOut.textContent = `${fmt(+amb.value, 0)} °C`; solOut.textContent = `${fmt(+sol.value, 0)} W/m²`; };
  amb.addEventListener("input", () => { updRanges(); if (stage && !S.play) stage.setEnv({ ambient: +amb.value }); });
  sol.addEventListener("input", updRanges); updRanges();
  const pax = numInput(store.get("simPax", 40), { unit: "kişi" });
  // tyre rolling resistance for this run only; blank = the vehicle's own value
  const roll = numInput(store.get("simRoll", ""), { placeholder: "" });
  const rollHint = h("div", { class: "hint" });
  const rollInfo = () => {
    const v = vehicles.find(x => x.stem === vSel.value) || {};
    $("input", roll).placeholder = v.rolling_c0 != null ? fmt(v.rolling_c0, 4) : "";
    rollHint.textContent = v.rolling_c0 != null ? `Boş bırakılırsa aracın kendi değeri: ${fmt(v.rolling_c0, 4)} (${v.rolling_source}). Yalnızca bu koşuya uygulanır, kütüphane değişmez. 0,003–0,03 arası.` : "";
  };
  vSel.addEventListener("change", rollInfo); rollInfo();
  let occMode = store.get("simOcc", "profile");
  const night = h("input", { type: "checkbox" });
  night.addEventListener("change", () => { if (stage && !S.play) stage.setEnv({ night: night.checked }); });
  const hvacOff = h("input", { type: "checkbox", checked: store.get("simHvacOff", false) ? true : null });
  const daily = numInput(200, { unit: "km/gün" });
  const cmpSel = select([["", "— yok —"], ...vehicles.map(v => [v.stem, `${v.name} — ${v.variant}`])], "");

  const params = () => {
    store.set("simAmb", +amb.value); store.set("simSol", +sol.value); store.set("simPax", parseNum($("input", pax).value));
    store.set("dutyKind", dutyKind); store.set(dutyKind === "route" ? "simRoute" : "simCycle", dutySel.value);
    store.set("simRepeat", parseNum($("input", repeat).value) || 1); store.set("simOcc", occMode);
    store.set("vehicle", vSel.value);
    const rollV = parseNum($("input", roll).value); store.set("simRoll", $("input", roll).value);
    return { vehicle: vSel.value, [dutyKind === "route" ? "route" : "cycle"]: dutySel.value,
      tune: Number.isNaN(rollV) || !rollV ? {} : { rolling_c0: rollV },
      repeat: parseNum($("input", repeat).value) || 1, ambient: +amb.value, solar: +sol.value,
      passengers: parseNum($("input", pax).value) || 0, occupancy_mode: occMode, night: night.checked,
      hvac_off: (store.set("simHvacOff", hvacOff.checked), hvacOff.checked),
      daily_km: parseNum($("input", daily).value) || 200,
      traffic: dutyKind === "route" ? trafficLevel : "yok" };
  };

  const scSel = select([["", "— senaryo seç —"], ...(ov.scenarios || []).filter(s => !s.error).map(s => [s.stem, s.name])], "");
  scSel.addEventListener("change", async () => {
    if (!scSel.value) return;
    const sc = (await api.get(`/api/scenario/${encodeURIComponent(scSel.value)}`)).scenario;
    if (sc.vehicles && sc.vehicles.length) { vSel.value = sc.vehicles[0]; vSel.dispatchEvent(new Event("change")); }
    if (sc.duty.route) { dutyKind = "route"; store.set("simRoute", sc.duty.route); } else { dutyKind = "cycle"; store.set("simCycle", sc.duty.cycle); }
    fillDuty(); drawTraffic(); trafficLevel = sc.traffic || "yok"; store.set("simTraffic", trafficLevel); drawTraffic();
    amb.value = sc.conditions.ambient_c; sol.value = sc.conditions.solar || 0; updRanges(); night.checked = !!sc.conditions.night; hvacOff.checked = !!sc.conditions.hvac_off;
    $("input", pax).value = String(sc.load.passengers || 0); occMode = sc.load.mode === "constant" ? "constant" : "profile";
    $("input", repeat).value = String(sc.duty.laps || sc.duty.repeat || 1);
    toast(`Senaryo yüklendi: ${sc.name}. Kütle/rüzgâr gibi ayrıntılar için Senaryolar sayfasında çalıştırın.`);
    loadPreview();
  });
  const runBtn = h("button", { class: "primary simx-run", type: "button" }, "Simülasyonu çalıştır");
  const setup = h("aside", { class: "hud simx-setup", "aria-label": "Simülasyon kurulumu" },
    h("div", { class: "hud-head" }, h("span", { class: "eyebrow" }, "Kurulum"), h("span", { class: "spacer" }),
      h("button", { type: "button", class: "ghost hud-x", title: "Paneli gizle", onclick: () => toggleSetup(false) }, "Gizle")),
    h("div", { class: "hud-body stack" },
      (ov.scenarios || []).length ? field("Kayıtlı senaryo", scSel) : null,
      field("Araç", vSel),
      h("div", {}, h("div", { class: "lab simx-lab" }, "Görev"),
        seg([["route", "Güzergâh"], ["cycle", "Standart çevrim"]], dutyKind, v => { dutyKind = v; fillDuty(); drawTraffic(); loadPreview(); }),
        h("div", { style: "margin-top:8px" }, dutySel),
        h("div", { class: "hint" }, h("a", { href: "#/yukle/guzergah" }, "Yeni güzergâh yükle"))),
      trafficBox,
      h("div", { class: "field" }, h("div", { class: "lab" }, "Dış sıcaklık", h("span", { class: "spacer" }), ambOut), amb),
      h("div", { class: "field" }, h("div", { class: "lab" }, "Güneş ışınımı", h("span", { class: "spacer" }), solOut), sol),
      h("div", {}, field("Yolcu", pax), h("div", { style: "margin-top:6px" }, seg([["profile", "Yol boyunca değişken"], ["constant", "Sabit"]], occMode, v => (occMode = v)))),
      h("div", {}, field("Lastik yuvarlanma katsayısı", roll), rollHint),
      h("label", { class: "check" }, night, h("span", {}, "Gece (aydınlatma ve farlar açık)")),
      h("label", { class: "check" }, hvacOff, h("span", {}, "Klima kapalı (yakıt testi koşulu: bütün alıcılar kapalı)")),
      h("details", { class: "simx-more" }, h("summary", {}, "Diğer ayarlar, sıcaklık taraması"),
        h("div", { class: "stack", style: "margin-top:10px" },
          field("Tekrar", repeat, { hint: "Görevi arka arkaya kaç kez koşacağı." }),
          field("Günlük mesafe", daily, { hint: "Batarya ömrü projeksiyonu için." }),
          field("Karşılaştırılacak araç", cmpSel, { hint: "İsteğe bağlı; aynı görevde −15…+40 °C." }),
          h("button", { type: "button", onclick: e => busy(e.currentTarget, () => openSweep({ ...params(), vehicles: [vSel.value, cmpSel.value].filter((x, i, a) => x && a.indexOf(x) === i) })) }, "Taramayı aç"),
          h("button", { type: "button", onclick: e => busy(e.currentTarget, () => openTrafficCompare(params())) }, "Trafik seviyelerini karşılaştır"),
          h("button", { type: "button", onclick: () => openMaraton(params()) }, "Maraton yakıt ve balata modeli")))),
    h("div", { class: "hud-foot" }, runBtn));
  const setupPill = h("button", { type: "button", class: "hud simx-pill", hidden: true, onclick: () => toggleSetup(true) }, "Kurulum");
  const toggleSetup = open => { setup.hidden = !open; setupPill.hidden = open; recenter(); };
  // Centre the bus in the space the panels leave free (desktop overlay layout only).
  const recenter = () => {
    if (!stage) return;
    const overlay = getComputedStyle(tele).position === "absolute";
    const left = overlay && !setup.hidden ? setup.offsetWidth + 16 : 0;
    const right = overlay ? tele.offsetWidth + 16 : 0;
    stage.setViewShift((right - left) / 2);
  };

  /* ---------------- camera bar (top centre) ---------------- */
  const camBar = h("div", { class: "hud simx-cams" },
    seg([["chase", "Takip"], ["side", "Yan"], ["free", "Serbest"], ["top", "Kuşbakışı"]], "chase", m => { if (stage) stage.setCameraMode(m); camHint.hidden = m !== "free"; }));
  const camHint = h("span", { class: "small muted", hidden: true }, "sürükle: döndür · tekerlek: yakınlaş");
  // X-ray: see through the shell into the cabin or the powertrain (key X cycles)
  const XRAY = ["off", "cabin", "powertrain"];
  let xrayMode = "off";
  const xraySeg = seg([["off", "Kapalı"], ["cabin", "Kabin"], ["powertrain", "Güç aktarma"]], "off", m => { xrayMode = m; if (stage) stage.setXray(m); });
  camBar.append(camHint);
  const xrayBar = h("div", { class: "hud simx-cams simx-xray", title: "Röntgen görünümü (X tuşu)" }, h("span", { class: "simx-xray-lab" }, "Röntgen"), xraySeg);

  /* ---------------- telemetry (right) ---------------- */
  const tele = h("aside", { class: "hud simx-tele", "aria-label": "Canlı veriler" });
  const idleTele = () => { tele.classList.add("idle"); tele.replaceChildren(
    h("div", { class: "hud-head" }, h("span", { class: "eyebrow" }, "Canlı veriler")),
    h("div", { class: "hud-body" }, h("p", { class: "simx-idle" }, "Aracı, güzergâhı ve koşulları soldan seçip ",
      h("b", {}, "Simülasyonu çalıştır"), "a basın. Otobüs güzergâhta ilerlerken hız, batarya, enerji akışı ve olaylar burada canlı görünür."))); };
  idleTele();

  /* ---------------- timeline (bottom) ---------------- */
  const playBtn = h("button", { type: "button", class: "simx-play", "aria-label": "Oynat", disabled: true });
  const setPlayIcon = () => { playBtn.innerHTML = S.playing ? '<svg viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>' : '<svg viewBox="0 0 24 24"><path d="M7 4l13 8-13 8z"/></svg>'; playBtn.setAttribute("aria-label", S.playing ? "Duraklat" : "Oynat"); };
  const speedSeg = seg(SPEEDS.map(s => [s, `${s}×`]), store.get("simSpeed", 10), v => { S.speed = v; store.set("simSpeed", v); });
  const clock = h("span", { class: "num simx-clock" }, "—");
  const tlHost = h("div", { class: "simx-tl", tabindex: 0, role: "slider", "aria-label": "Güzergâh üzerindeki konum" });
  const timeline = h("div", { class: "hud simx-timeline" },
    h("div", { class: "simx-tl-controls" }, playBtn, speedSeg, clock), tlHost);

  const canvasHost = h("div", { class: "simx-canvas" });
  const loading = h("div", { class: "simx-loading", hidden: true }, h("div", { class: "simx-spinner" }), h("span", {}, "Simülasyon hesaplanıyor…"));
  const stageEl = h("div", { class: "simx-stage" }, canvasHost, camBar, xrayBar, setup, setupPill, tele, timeline, loading);
  root.append(stageEl);

  /* ---------------- controller state ---------------- */
  const S = { play: null, t: 0, playing: false, speed: store.get("simSpeed", 10), cursor: 0, lastHud: 0, lastEv: -2, reportId: null, profile: null };
  let stage = null, raf = 0, last = performance.now();
  setPlayIcon();

  const mountStage = async () => {
    try {
      const mod = await import("/static/sim3d.js");
      if (!mod.webglAvailable()) throw new Error("WebGL yok");
      stage = mod.createStage(canvasHost, { raster: Sign.raster });
      stage.setEnv({ ambient: +amb.value, night: night.checked, solar: +sol.value });
      stage.setPowertrain((vehicles.find(v => v.stem === vSel.value) || {}).powertrain);
      recenter();
      addEventListener("resize", recenter);
      await loadPreview();
    } catch (e) {
      canvasHost.replaceChildren(h("div", { class: "simx-nogl" }, h("b", {}, "3B görünüm açılamadı."),
        h("span", {}, " Bu bilgisayarda WebGL kullanılamıyor. Canlı veriler ve zaman çizelgesi yine çalışır; tam rapor için ‘Tam rapor’ düğmesini kullanın.")));
      console.error(e);
    }
  };

  async function loadPreview() {
    if (!stage || S.play) return;
    const q = dutyKind === "route" ? `route=${encodeURIComponent(dutySel.value)}` : `cycle=${encodeURIComponent(dutySel.value)}`;
    try {
      const prof = await api.get(`/api/preview?${q}`);
      S.profile = prof;
      stage.setProfile(prof, { sign: prof.name.replace(/_/g, " ") });
      stage.setTraffic(null);
      stage.setPeople(null);
      drawTimeline();
    } catch (e) { toast(e.message, "err"); }
  }
  dutySel.addEventListener("change", () => { S.play = null; idleTele(); playBtn.disabled = true; loadPreview(); });
  vSel.addEventListener("change", () => { if (stage) stage.setPowertrain((vehicles.find(v => v.stem === vSel.value) || {}).powertrain); });

  /* ---------------- sampling ---------------- */
  const KEYS_BASE = ["d", "v", "grade", "elev", "soc", "p_batt", "p_motor", "p_hvac", "p_aux", "p_thermal", "regen_lost",
                "cabin", "pack", "pax", "e_used", "e_trac", "e_regen", "e_hvac", "e_aux", "e_thermal"];
  const KEYS_DIESEL = ["rpm", "gear", "fuel_lph", "l100", "e_brake", "e_engbrake", "e_heater"];
  let KEYS = KEYS_BASE;
  function sample(t) {
    const tl = S.play.timeline, T = tl.t, n = T.length;
    let i = Math.min(Math.max(S.cursor, 0), n - 2);
    while (i < n - 2 && T[i + 1] <= t) i++;
    while (i > 0 && T[i] > t) i--;
    S.cursor = i;
    const f = Math.max(0, Math.min(1, (t - T[i]) / ((T[i + 1] - T[i]) || 1)));
    const o = { t };
    for (const k of KEYS) o[k] = tl[k][i] + (tl[k][i + 1] - tl[k][i]) * f;
    o.doors = (f < 0.5 ? tl.doors[i] : tl.doors[i + 1]);
    o.accel = (tl.v[i + 1] - tl.v[i]) / 3.6 / ((T[i + 1] - T[i]) || 1);
    o.i = i;
    return o;
  }
  const idleState = { d: 0, v: 0, p_batt: 0, p_motor: 0, p_hvac: 0, doors: 0 };

  /* ---------------- loop ---------------- */
  function loop(now) {
    raf = requestAnimationFrame(loop);
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    let st = idleState;
    if (S.play) {
      const T = S.play.timeline.t, end = T[T.length - 1];
      if (S.playing) { S.t += dt * S.speed; if (S.t >= end) { S.t = end; S.playing = false; setPlayIcon(); } }
      st = sample(S.t);
      if (now - S.lastHud > 66) { updateHud(st); S.lastHud = now; }
    }
    if (stage) stage.frame(st, dt);
  }
  raf = requestAnimationFrame(loop);

  const setPlaying = on => { if (!S.play) return; const T = S.play.timeline.t; if (on && S.t >= T[T.length - 1]) { S.t = 0; S.cursor = 0; } S.playing = on; setPlayIcon(); };
  playBtn.addEventListener("click", () => setPlaying(!S.playing));
  const seekT = t => { if (!S.play) return; const T = S.play.timeline.t; S.t = Math.max(0, Math.min(T[T.length - 1], t)); S.cursor = 0; S.lastHud = 0; };
  const seekD = d => { if (!S.play) return; const D = S.play.timeline.d; let i = 0; while (i < D.length - 1 && D[i] < d) i++; seekT(S.play.timeline.t[i]); };

  const onKey = e => {
    if (e.target.closest("input, select, textarea")) return;
    if (e.code === "Space") { e.preventDefault(); setPlaying(!S.playing); }
    else if (e.key === "ArrowRight") seekT(S.t + 10);
    else if (e.key === "ArrowLeft") seekT(S.t - 10);
    else if ((e.key === "x" || e.key === "X") && stage) {
      xrayMode = XRAY[(XRAY.indexOf(xrayMode) + 1) % XRAY.length];
      stage.setXray(xrayMode);
      $$("button", xraySeg).forEach((b, k) => b.setAttribute("aria-pressed", String(XRAY[k] === xrayMode)));
    }
    else if ("1234".includes(e.key) && stage) {
      const m = ["chase", "side", "free", "top"][+e.key - 1];
      stage.setCameraMode(m);
      $$("button", camBar).forEach((b, k) => b.setAttribute("aria-pressed", String(k === +e.key - 1)));
      camHint.hidden = m !== "free";
    }
  };
  addEventListener("keydown", onKey);
  pageCleanup = () => { cancelAnimationFrame(raf); removeEventListener("keydown", onKey); removeEventListener("resize", recenter); if (stage) stage.dispose(); stage = null; };

  /* ---------------- run ---------------- */
  runBtn.addEventListener("click", () => busy(runBtn, async () => {
    loading.hidden = false;
    try {
      const p = params();
      const r = await api.post("/api/simulate", p);
      S.play = r.play; S.reportId = r.report_id; S.t = 0; S.cursor = 0; S.lastEv = -2; S.run = r;
      S.diesel = r.play.vehicle.powertrain === "diesel";
      KEYS = S.diesel ? KEYS_BASE.concat(KEYS_DIESEL.filter(k => k in r.play.timeline)) : KEYS_BASE;
      S.profile = r.play.profile;
      if (stage) {
        stage.setPowertrain(r.play.vehicle.powertrain);
        stage.setEnv({ ambient: p.ambient, solar: p.solar, night: p.night });
        stage.setProfile(r.play.profile, { sign: r.play.profile.name.replace(/_/g, " ") });
        stage.setTraffic(r.play.traffic);
        stage.setPeople(r.play.timeline);
      }
      Sign.set(vehicleCode(p.vehicle), "Simülasyon");
      buildTele(r, p);
      drawTimeline();
      playBtn.disabled = false;
      toggleSetup(false);
      setPlaying(!matchMedia("(prefers-reduced-motion: reduce)").matches);
      S.lastHud = 0;
    } finally { loading.hidden = true; }
  }));

  /* ---------------- telemetry panel ---------------- */
  let H = {};
  function gauge(label, unit, max, color) {
    const R = 44, C = 2 * Math.PI * R, sweep = 0.75;
    const svg = s("svg", { viewBox: "0 0 120 108", class: "gauge" });
    const arc = (cls, col) => s("circle", { cx: 60, cy: 60, r: R, fill: "none", stroke: col, "stroke-width": 9, "stroke-linecap": "round",
      "stroke-dasharray": `${C * sweep} ${C}`, transform: "rotate(135 60 60)", class: cls }, svg);
    arc("g-track", css("--rule"));
    const val = arc("g-val", color);
    val.setAttribute("stroke-dasharray", `0 ${C}`);
    const num = s("text", { x: 60, y: 62, "text-anchor": "middle", class: "g-num" }, svg);
    const un = s("text", { x: 60, y: 80, "text-anchor": "middle", class: "g-unit" }, svg); un.textContent = unit;
    const lb = s("text", { x: 60, y: 104, "text-anchor": "middle", class: "g-lab" }, svg); lb.textContent = label;
    return { el: svg, set(v, text, col) {
      const f = Math.max(0, Math.min(1, v / max));
      val.setAttribute("stroke-dasharray", `${C * sweep * f} ${C}`);
      if (col) val.setAttribute("stroke", col);
      num.textContent = text;
    } };
  }
  function flowRow(label, cap) {
    const bar = h("i"), val = h("b", { class: "num" });
    const row = h("div", { class: "flow-row" }, h("span", {}, label), h("span", { class: "flow-bar" }, bar), val);
    return { el: row, set(kw, col) {
      bar.style.width = `${Math.min(100, Math.abs(kw) / cap * 100)}%`;
      bar.style.background = col;
      val.textContent = `${fmt(kw, 1)} kW`;
    } };
  }
  function stat(label) { const v = h("b", { class: "num" }); return { el: h("div", { class: "stat" }, h("span", {}, label), v), set: t => (v.textContent = t) }; }

  function buildTele(r, p) {
    const pl = r.play, veh = pl.vehicle, tl = pl.timeline;
    const diesel = veh.powertrain === "diesel";
    H.diesel = diesel;
    H.speed = gauge("hız", "km/h", diesel ? 120 : 80, css("--amber"));
    H.soc = diesel ? gauge("motor devri", "rpm", Math.max(1000, (veh.rated_rpm || 2000) * 1.15), css("--s1")) : gauge("şarj durumu", "%", 100, css("--s1"));
    H.battNum = h("div", { class: "batt-num num" });
    H.battLab = h("div", { class: "batt-lab" });
    H.rows = diesel ? {
      trac: flowRow("Motor mili", veh.motor_kw), hvac: flowRow("Klima", Math.max(veh.hvac_kw, 10)),
      aux: flowRow("Yardımcı + fan", 12), therm: flowRow("Sürtünmeli fren", veh.motor_kw),
    } : {
      trac: flowRow("Çekiş", veh.motor_kw), hvac: flowRow("Klima", Math.max(veh.hvac_kw, 10)),
      aux: flowRow("Yardımcı", 12), therm: flowRow("Paket ısıl", 15),
    };
    H.stats = diesel ? { dist: stat("Mesafe"), cons: stat("Ortalama tüketim"), used: stat("Yakıt"), regen: stat("Vites"),
      cabin: stat("Kabin"), pack: stat("Anlık yakıt"), pax: stat("Yolcu"), grade: stat("Eğim"),
      red: stat("Kırmızı ışıkta"), jam: stat("Sıkışıklıkta") } : { dist: stat("Mesafe"), cons: stat("Ortalama tüketim"), used: stat("Harcanan"), regen: stat("Geri kazanılan"),
      cabin: stat("Kabin"), pack: stat("Paket"), pax: stat("Yolcu"), grade: stat("Eğim"),
      red: stat("Kırmızı ışıkta"), jam: stat("Sıkışıklıkta") };
    H.split = h("div", { class: "split" });
    H.splitLegend = h("div", { class: "legend split-legend" });
    H.powerChart = powerStrip(tl, diesel);
    H.feed = h("ol", { class: "feed" });
    const total = tl.d[tl.d.length - 1];
    H.total = total;
    pl.events.forEach((e, k) => {
      const li = h("li", { class: "ev ev-" + e.kind, "data-k": k, tabindex: 0, title: "Bu ana git" },
        h("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }),
        h("div", {}, h("div", { class: "ev-top" }, h("b", {}, e.title), h("span", { class: "num muted" }, clockText(e.t))), e.text ? h("div", { class: "ev-text" }, e.text) : null));
      li.querySelector("svg").innerHTML = `<path d="${EV_ICON[e.kind] || EV_ICON.stop}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>`;
      const go = () => { seekT(e.t); };
      li.addEventListener("click", go);
      li.addEventListener("keydown", ev => { if (ev.key === "Enter") go(); });
      H.feed.append(li);
    });
    const warn = r.warnings.length ? h("details", { class: "tele-warn" }, h("summary", {}, `${r.warnings.length} bulgu`), h("ul", {}, r.warnings.map(w => h("li", {}, w)))) : null;
    const k = r.kpi;
    tele.classList.remove("idle");
    tele.replaceChildren(
      h("div", { class: "hud-head" },
        h("div", { style: "min-width:0" }, h("div", { class: "eyebrow" }, "Canlı veriler"),
          h("div", { class: "tele-title" }, `${veh.name}`, h("span", { class: "muted" }, ` · ${veh.variant}`)),
          h("div", { class: "tele-sub" }, `${pl.profile.name} · ${fmt(p.ambient, 0)} °C · ${fmt(p.passengers, 0)} yolcu${p.night ? " · gece" : ""}` +
            (pl.traffic && pl.traffic.traffic && pl.traffic.traffic !== "yok" ? ` · trafik ${pl.traffic.traffic_label.toLocaleLowerCase("tr-TR")}` : ""))),
        h("span", { class: "spacer" }),
        h("a", { class: "btn tele-report", href: `/report/${r.report_id}`, target: "_blank", rel: "noopener" }, "Tam rapor"),
        menu("İndir", [{ label: "Rapor (HTML)", href: `/report/${r.report_id}/html`, download: "" }, { label: "Zaman serisi (CSV)", href: `/report/${r.report_id}/csv`, download: "", hint: "Excel ; ayraçlı" }], { cls: "tele-report" })),
      h("div", { class: "hud-body tele-body" },
        !r.achievable ? notice("err", "Araç bu görevi takip edemedi.", ["Bir bileşen sınırı yetersiz; bkz. tam rapor."]) : null,
        h("div", { class: "gauges" }, H.speed.el, H.soc.el),
        h("section", { class: "tele-sec" }, h("div", { class: "sec-t" }, "Güç akışı"),
          h("div", { class: "batt" }, H.battNum, H.battLab),
          Object.values(H.rows).map(x => x.el),
          h("div", { class: "hint", style: "margin-top:2px" }, "Çubuk: tüketicinin kendi kapasitesine göre kullanımı")),
        h("section", { class: "tele-sec" }, h("div", { class: "sec-t" }, "Şu ana kadar"), h("div", { class: "stats" }, Object.values(H.stats).map(x => x.el))),
        h("section", { class: "tele-sec" }, h("div", { class: "sec-t" }, "Enerji nereye gitti"), H.split, H.splitLegend),
        h("section", { class: "tele-sec" }, h("div", { class: "sec-t" }, diesel ? "Motor gücü, güzergâh boyunca" : "Batarya gücü, güzergâh boyunca"), H.powerChart.el),
        h("section", { class: "tele-sec" }, h("div", { class: "sec-t" }, `Olaylar (${pl.events.length})`), H.feed),
        h("section", { class: "tele-sec tele-final" }, h("div", { class: "sec-t" }, "Koşunun tamamı"),
          diesel ? h("div", { class: "stats" },
            h("div", { class: "stat" }, h("span", {}, "Tüketim"), h("b", { class: "num" }, `${fmt(k.l_per_100km, 2)} l/100 km`)),
            h("div", { class: "stat" }, h("span", {}, "CO₂"), h("b", { class: "num" }, `${fmt(k.co2_g_per_km, 0)} g/km`)),
            h("div", { class: "stat" }, h("span", {}, "Depoyla menzil"), h("b", { class: "num" }, `${fmt(k.range_km, 0)} km`)),
            h("div", { class: "stat" }, h("span", {}, "Rölanti yakıtı"), h("b", { class: "num" }, `${fmt(k.idle_fuel_l, 2)} l · %${fmt(k.idle_fraction * 100, 0)} süre`)),
            h("div", { class: "stat" }, h("span", {}, "Ortalama devir"), h("b", { class: "num" }, `${fmt(k.mean_rpm, 0)} rpm · ${k.shifts} vites`)),
            h("div", { class: "stat" }, h("span", {}, "Sürtünmeli fren"), h("b", { class: "num" }, `${fmt(k.friction_brake_kwh, 2)} kWh`)))
          : h("div", { class: "stats" },
            h("div", { class: "stat" }, h("span", {}, "Tüketim"), h("b", { class: "num" }, `${fmt(k.kwh_per_km, 3)} kWh/km`)),
            h("div", { class: "stat" }, h("span", {}, "Menzil"), h("b", { class: "num" }, `${fmt(k.range_km, 0)} km`)),
            h("div", { class: "stat" }, h("span", {}, "Klima payı"), h("b", { class: "num" }, `%${fmt(k.hvac_share * 100, 0)}`)),
            h("div", { class: "stat" }, h("span", {}, "Reddedilen rejen."), h("b", { class: "num" }, `${fmt(k.regen_lost_kwh, 2)} kWh`))),
          warn)));
  }

  /* power over distance: faint for the whole route, solid up to the bus */
  function powerStrip(tl, dieselStrip) {
    const W = 320, Hh = 74, pad = 2;
    const svg = s("svg", { viewBox: `0 0 ${W} ${Hh}`, class: "strip", preserveAspectRatio: "none" });
    // diesel: shaft power only; engine braking is not energy coming back
    const D = tl.d, P = dieselStrip ? tl.p_batt.map(v => Math.max(0, v)) : tl.p_batt, dmax = D[D.length - 1] || 1;
    const lo = Math.min(0, ...P), hi = Math.max(1, ...P);
    const sx = d => d / dmax * W, sy = v => pad + (1 - (v - lo) / (hi - lo)) * (Hh - 2 * pad);
    const step = Math.max(1, Math.floor(D.length / 700));
    let dpath = "";
    for (let i = 0; i < D.length; i += step) dpath += `${i ? "L" : "M"}${sx(D[i]).toFixed(1)} ${sy(P[i]).toFixed(1)}`;
    const id = "clip" + Math.random().toString(36).slice(2, 8);
    const defs = s("defs", {}, svg);
    const cp = s("clipPath", { id }, defs);
    const rect = s("rect", { x: 0, y: 0, width: 0, height: Hh }, cp);
    s("line", { x1: 0, x2: W, y1: sy(0), y2: sy(0), stroke: css("--rule"), "vector-effect": "non-scaling-stroke" }, svg);
    s("path", { d: dpath, fill: "none", stroke: css("--muted"), "stroke-width": 1, opacity: 0.35, "vector-effect": "non-scaling-stroke" }, svg);
    s("path", { d: dpath, fill: "none", stroke: css("--s1"), "stroke-width": 1.6, "clip-path": `url(#${id})`, "vector-effect": "non-scaling-stroke" }, svg);
    const head = s("line", { x1: 0, x2: 0, y1: 0, y2: Hh, stroke: css("--amber"), "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" }, svg);
    const wrap = h("div", { class: "strip-wrap" });
    wrap.appendChild(svg);
    wrap.append(h("div", { class: "strip-axis" }, h("span", {}, `${fmt(hi, 0)} kW`), h("span", {}, `${fmt(lo, 0)} kW`)));
    return { el: wrap, set(d) { const x = sx(d); rect.setAttribute("width", x); head.setAttribute("x1", x); head.setAttribute("x2", x); } };
  }

  function clockText(t) { const m = Math.floor(t / 60), s2 = Math.floor(t % 60); return `${String(m).padStart(2, "0")}:${String(s2).padStart(2, "0")}`; }

  function updateHud(st) {
    if (!H.speed) return;
    const tl = S.play.timeline, T = tl.t;
    const regen = st.p_motor < -2;
    H.speed.set(st.v, fmt(st.v, 0));
    const km = st.d / 1000;
    if (H.diesel) {
      // a diesel never recovers energy: engine braking shows as 0 kW shaft power
      // with its own label, never as a negative (green) figure
      H.soc.set(st.rpm || 0, fmt(st.rpm || 0, 0), css("--s1"));
      H.battNum.textContent = `${fmt(Math.max(0, st.p_batt), 1)} kW`;
      H.battNum.style.color = css("--s1");
      H.battLab.textContent = st.p_batt < -0.5 ? `motor freni ${fmt(-st.p_batt, 0)} kW (yakıt kesik)` : st.p_batt > 0.5 ? "motor mili gücü" : st.v < 0.5 ? "rölanti" : "boşta";
      H.rows.trac.set(Math.max(0, st.p_motor), css("--s1"));
      H.rows.hvac.set(st.p_hvac, css("--s2"));
      H.rows.aux.set(st.p_aux, css("--s3"));
      H.rows.therm.set(st.regen_lost, "#d03b3b");
      H.stats.dist.set(`${fmt(km, 2)} / ${fmt(H.total / 1000, 2)} km`);
      H.stats.cons.set(km > 0.2 ? `${fmt(st.l100 || 0, 1)} l/100 km` : "—");
      H.stats.used.set(`${fmt(st.e_used, 2)} l · depo %${fmt(st.soc, 0)}`);
      H.stats.regen.set(st.gear > 0 ? `${Math.round(st.gear)}` : "N");
      H.stats.cabin.set(`${fmt(st.cabin, 1)} °C`);
      H.stats.pack.set(`${fmt(st.fuel_lph || 0, 1)} l/h`);
      H.stats.pax.set(fmt(st.pax, 0));
      H.stats.grade.set(`%${fmt(st.grade, 1)}`);
    } else {
    H.soc.set(st.soc, fmt(st.soc, 1), st.soc < 25 ? css("--amber") : css("--s1"));
    H.battNum.textContent = `${st.p_batt >= 0 ? "" : "−"}${fmt(Math.abs(st.p_batt), 1)} kW`;
    H.battNum.style.color = st.p_batt < 0 ? css("--s6") || "#0ca30c" : css("--s1");
    H.battLab.textContent = st.p_batt < -0.5 ? "batarya şarj oluyor (rejenerasyon)" : st.p_batt > 0.5 ? "bataryadan çekilen" : "bekleme";
    H.rows.trac.set(st.p_motor, regen ? "#2fbf71" : css("--s1"));
    H.rows.hvac.set(st.p_hvac, css("--s2"));
    H.rows.aux.set(st.p_aux, css("--s3"));
    H.rows.therm.set(st.p_thermal, css("--s4"));
    H.stats.dist.set(`${fmt(km, 2)} / ${fmt(H.total / 1000, 2)} km`);
    H.stats.cons.set(km > 0.05 ? `${fmt(st.e_used / km, 2)} kWh/km` : "—");
    H.stats.used.set(`${fmt(st.e_used, 2)} kWh`);
    H.stats.regen.set(`${fmt(st.e_regen, 2)} kWh`);
    H.stats.cabin.set(`${fmt(st.cabin, 1)} °C`);
    H.stats.pack.set(`${fmt(st.pack, 1)} °C`);
    H.stats.pax.set(fmt(st.pax, 0));
    H.stats.grade.set(`%${fmt(st.grade, 1)}`);
    }
    const tr = S.play.traffic || {};
    const upTo = (a, b) => Math.max(0, Math.min(st.t, b) - a);
    const redS = (tr.signal_stops || []).reduce((acc, e) => acc + upTo(e.t0, e.t_go), 0);
    const jamS = (tr.jams || []).reduce((acc, j) => acc + upTo(j.t0, j.t1), 0);
    H.stats.red.set(`${fmt(redS, 0)} s · ${(tr.signal_stops || []).filter(e => e.t0 <= st.t).length} kez`);
    H.stats.jam.set(`${fmt(jamS, 0)} s`);
    // cumulative split: net traction, climate, auxiliaries, pack thermal (diesel: shaft work, climate, aux, brakes)
    const parts = H.diesel
      ? [["Motor mili (net)", Math.max(0, st.e_trac - st.e_hvac - st.e_aux), css("--s1")], ["Klima", st.e_hvac, css("--s2")],
         ["Yardımcı + fan", st.e_aux, css("--s3")], ["Sürtünmeli fren", st.e_brake || 0, "#d03b3b"]]
      : [["Çekiş (net)", Math.max(0, st.e_trac - st.e_regen), css("--s1")], ["Klima", st.e_hvac, css("--s2")],
         ["Yardımcı", st.e_aux, css("--s3")], ["Paket ısıl", st.e_thermal, css("--s4")]];
    const tot = parts.reduce((a, q) => a + q[1], 0) || 1;
    H.split.replaceChildren(...parts.map(([, v, c]) => h("i", { style: `flex:${Math.max(v, 0.0001)};background:${c}` })));
    H.splitLegend.replaceChildren(...parts.map(([n, v, c]) => h("span", {}, h("i", { style: `background:${c}` }), `${n} ${fmt(v, 1)} kWh · %${fmt(v / tot * 100, 0)}`)));
    H.powerChart.set(st.d);
    // events: everything up to now is "past"; the newest past one is highlighted
    const evs = S.play.events;
    let lastIdx = -1;
    for (let k = 0; k < evs.length; k++) if (evs[k].t <= st.t + 0.01) lastIdx = k; else break;
    if (lastIdx !== S.lastEv) {
      $$("li.ev", H.feed).forEach((li, k) => { li.classList.toggle("past", k <= lastIdx); li.classList.toggle("now", k === lastIdx); });
      const cur = H.feed.children[Math.max(0, lastIdx)];
      if (cur && !H.feed.matches(":hover")) H.feed.scrollTo({ top: cur.offsetTop - H.feed.offsetTop - 40, behavior: "smooth" });
      S.lastEv = lastIdx;
    }
    clock.textContent = `${clockText(st.t)} / ${clockText(T[T.length - 1])}  ·  ${fmt(km, 2)} km`;
    updateTimeline(st.d);
  }

  /* ---------------- timeline: elevation silhouette, stops, playhead ---------------- */
  let tlParts = null;
  function drawTimeline() {
    const prof = S.play ? S.play.profile : S.profile;
    if (!prof) return;
    const W = 1000, Hh = 64;
    const D = prof.d, E = prof.elev, dmax = D[D.length - 1] || 1;
    const emin = Math.min(...E), emax = Math.max(...E), span = Math.max(emax - emin, 20);
    const sx = d => d / dmax * W, sy = e => 8 + (1 - (e - emin) / span) * (Hh - 16);
    const svg = s("svg", { viewBox: `0 0 ${W} ${Hh}`, preserveAspectRatio: "none", class: "tl-svg" });
    const step = Math.max(1, Math.floor(D.length / 800));
    let area = `M0 ${Hh}`;
    for (let i = 0; i < D.length; i += step) area += ` L${sx(D[i]).toFixed(1)} ${sy(E[i]).toFixed(1)}`;
    area += ` L${W} ${sy(E[E.length - 1]).toFixed(1)} L${W} ${Hh} Z`;
    const id = "tlc" + Math.random().toString(36).slice(2, 8);
    const cp = s("clipPath", { id }, s("defs", {}, svg));
    const rect = s("rect", { x: 0, y: 0, width: 0, height: Hh }, cp);
    s("path", { d: area, fill: css("--muted"), opacity: 0.22 }, svg);
    s("path", { d: area, fill: css("--s1"), opacity: 0.55, "clip-path": `url(#${id})` }, svg);
    const trf = S.play && S.play.traffic;
    for (const j of (trf && trf.jams) || []) s("rect", { x: sx(j.d), y: 0, width: Math.max(1.5, sx(j.d_end) - sx(j.d)), height: Hh, fill: css("--s2"), opacity: 0.22 }, svg);
    for (const sd of prof.stops || []) s("line", { x1: sx(sd), x2: sx(sd), y1: Hh - 12, y2: Hh, stroke: css("--amber"), "stroke-width": 2, "vector-effect": "non-scaling-stroke" }, svg);
    for (const g of prof.signals || []) s("line", { x1: sx(g.d), x2: sx(g.d), y1: 0, y2: 9, stroke: css("--ink-2"), "stroke-width": 2, "vector-effect": "non-scaling-stroke" }, svg);
    const head = s("line", { x1: 0, x2: 0, y1: 0, y2: Hh, stroke: css("--ink"), "stroke-width": 2, "vector-effect": "non-scaling-stroke" }, svg);
    const labels = h("div", { class: "tl-labels" },
      h("span", {}, "0 km"),
      h("span", {}, `rakım ${fmt(emin, 0)}–${fmt(emax, 0)} m · ${(prof.stops || []).length} durak (alt) · ${(prof.signals || []).length} ışık (üst)` +
        (trf && trf.jams && trf.jams.length ? ` · ${trf.jams.length} sıkışıklık (turuncu)` : "")),
      h("span", {}, `${fmt(dmax / 1000, 1)} km`));
    tlHost.replaceChildren(svg, labels);
    tlParts = { rect, head, sx, dmax };
    updateTimeline(S.play ? sample(S.t).d : 0);
  }
  function updateTimeline(d) {
    if (!tlParts) return;
    const x = tlParts.sx(d);
    tlParts.rect.setAttribute("width", x);
    tlParts.head.setAttribute("x1", x); tlParts.head.setAttribute("x2", x);
    tlHost.setAttribute("aria-valuenow", Math.round(d));
    tlHost.setAttribute("aria-valuetext", `${fmt(d / 1000, 2)} km`);
  }
  const tlSeek = ev => {
    if (!S.play || !tlParts) return;
    const b = tlHost.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (ev.clientX - b.left) / b.width));
    seekD(f * tlParts.dmax);
  };
  let dragging = false;
  tlHost.addEventListener("pointerdown", e => { dragging = true; tlHost.setPointerCapture(e.pointerId); tlSeek(e); });
  tlHost.addEventListener("pointermove", e => { if (dragging) tlSeek(e); });
  tlHost.addEventListener("pointerup", () => { dragging = false; });

  setTimeout(mountStage);
  return root;
}

/* Traffic comparison: same bus, route and weather under every traffic level. */
async function openTrafficCompare(p) {
  if (!p.route) { toast("Trafik karşılaştırması için bir güzergâh seçin.", "err"); return; }
  const r = await api.post("/api/traffic_compare", p);
  const rows = r.rows, x = rows.map((_, i) => i), lab = i => rows[Math.round(i)] ? rows[Math.round(i)].label : "";
  const dlg = h("dialog", {});
  const close = () => { dlg.close(); dlg.remove(); };
  const dz = r.powertrain === "diesel", ck = dz ? "l_per_100km" : "kwh_per_km", cd = dz ? 2 : 3, cu = dz ? "l/100 km" : "kWh/km";
  const best = Math.min(...rows.map(q => q[ck])), worst = Math.max(...rows.map(q => q[ck]));
  dlg.append(h("div", { class: "dlg-head" }, h("h2", {}, "Trafik seviyelerine göre"), h("span", { class: "spacer" }), h("button", { type: "button", class: "ghost", onclick: close }, "Kapat")),
    h("div", { class: "dlg-body stack" },
      h("p", { class: "small muted", style: "margin:0" }, `${r.route} · ${fmt(r.ambient, 0)} °C · aynı araç, aynı yolcu profili.` +
        (r.synthetic_signals ? " Güzergâh dosyasında ışık konumu yok; ışıklar temsili aralıklarla yerleştirildi." : "")),
      notice("", "Nasıl okunmalı", [
        "Trafik, frenlerde kaybedilen enerjiyi azaltır (araç daha yavaş gider) ama klima ve yardımcı tüketicilerin çalıştığı süreyi uzatır.",
        "Ilıman havada bu iki etki birbirini dengeleyebilir; soğukta ya da sıcakta trafik km başına tüketimi belirgin artırır.",
        "Saatlik tüketim (kWh/h) trafikte düşer: tarifeli bir sefer bloğunda araç daha az km yapar."]),
      lineChart({ x, series: [{ label: "Tüketim", color: css("--s1"), y: rows.map(q => q[ck]) }], xLabel: "trafik seviyesi", yLabel: `Tüketim (${cu})`, xFmt: lab, yFmt: v => fmt(v, cd), markers: true, height: 220 }),
      lineChart({ x, series: [{ label: "Ortalama hız", color: css("--s2"), y: rows.map(q => q.mean_kmh) }], xLabel: "trafik seviyesi", yLabel: "Ortalama hız (km/h)", xFmt: lab, yFmt: v => fmt(v, 1), markers: true, height: 200 }),
      h("div", { class: "scrollx" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, ["Trafik", "Ort. hız", "Süre", cu, dz ? "kWh yakıt/h" : "kWh/h", "Menzil", "Klima kWh", "Yardımcı kWh", "Sürtünmeli fren kWh", "Kırmızı ışık", "Sıkışıklık"].map(c => h("th", {}, c)))),
        h("tbody", {}, rows.map(q => h("tr", {},
          h("td", {}, h("b", {}, q.label)),
          h("td", { class: "n" }, `${fmt(q.mean_kmh, 1)} km/h`), h("td", { class: "n" }, `${fmt(q.minutes, 1)} dk`),
          h("td", { class: "n", style: q[ck] === worst ? "color:var(--err);font-weight:700" : q[ck] === best ? "color:var(--ok);font-weight:700" : "" }, fmt(q[ck], cd)),
          h("td", { class: "n" }, fmt(q.kwh_per_h, 1)), h("td", { class: "n" }, `${fmt(q.range_km, 0)} km`),
          h("td", { class: "n" }, fmt(q.hvac_kwh, 2)), h("td", { class: "n" }, fmt(q.aux_kwh, 2)), h("td", { class: "n" }, fmt(q.brake_kwh, 2)),
          h("td", { class: "n" }, `${q.red_lights} · ${fmt(q.red_wait_s, 0)} s`), h("td", { class: "n" }, `${q.jams} · ${fmt(q.jam_s, 0)} s`))))))));
  document.body.append(dlg);
  dlg.showModal();
}

/* Maraton GEN3 regressions (diesel coach): fuel and brake-pad life for the chosen duty. */
function openMaraton(p) {
  const dlg = h("dialog", {});
  const close = () => { dlg.close(); dlg.remove(); };
  const f = { ac: numInput(20, { unit: "%" }), rpm1200: numInput(17, { unit: "%" }), pedal: numInput(76, { unit: "%" }),
              idle: numInput(1.0, { unit: "L/100km" }), week: numInput(30), ambient: numInput(p.ambient, { unit: "°C" }) };
  const body = select([["CD", "Mevcut tasarım (CD)"], ["ND", "Yeni tasarım (ND)"]], "CD");
  const kale = h("input", { type: "checkbox" });
  const out = h("div", { class: "stack" });
  const run = async () => {
    const val = k => parseNum($("input", f[k]).value);
    const r = await api.post("/api/maraton", { route: p.route, cycle: p.cycle, traffic: p.traffic,
      ac: val("ac"), rpm1200: val("rpm1200"), pedal: val("pedal"), idle: val("idle"), week: val("week"),
      ambient: val("ambient"), body: body.value, kale: kale.checked });
    const T = r.fuel.terms, s = r.stats, pw = r.pads;
    const names = { idle: "Rölanti", intercept: "Sabit", ac: "Klima", time_per_km: "Km başına süre", rpm1200: "≥1200 rpm",
      pedal: "Pedal", season: "Mevsim", gt102: ">102 km/h", temperature: "Sıcaklık fiziği", kale: "Kale radyatör", body_nd: "ND gövde" };
    out.replaceChildren(
      h("div", { class: "kpis" },
        kpi("Yakıt", fmt(r.fuel.fuel_l100, 2), "L/100 km"), kpi("Belirsizlik", "±" + fmt(r.fuel.rmse_l100, 2), "L/100 km"),
        kpi("Balata ömrü (ön sağ)", fmt(pw.life_km_to_20pct.front_right / 1000, 0), "bin km"), kpi("v₀₋₃₀", "%" + fmt(s.v030_pct, 1), "")),
      h("p", { class: "small muted", style: "margin:0" }, `${r.cycle}: ortalama ${fmt(s.v_mean_kmh, 1)} km/h, >80 km/h %${fmt(s.gt80_pct, 0)}, >102 km/h %${fmt(s.gt102_pct, 1)} (hız profilinden)`),
      h("table", { class: "data" }, h("tbody", {}, Object.entries(T).map(([k, v]) => h("tr", {}, h("td", {}, names[k] || k), h("td", { class: "n" }, (v >= 0 ? "+" : "") + fmt(v, 2)))))),
      h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Tekerlek"), h("th", {}, "% / 10 bin km"), h("th", {}, "%20'ye km"))),
        h("tbody", {}, [["front_right", "Ön sağ"], ["front_left", "Ön sol"], ["rear", "Arka"]].map(([k, n]) =>
          h("tr", {}, h("td", {}, n), h("td", { class: "n" }, fmt(pw.rate_pct_per_10k[k], 2)), h("td", { class: "n" }, fmt(pw.life_km_to_20pct[k], 0)))))),
      [...r.fuel.warnings, ...pw.warnings].length ? notice("warn", "Model aralığı dışında", [...r.fuel.warnings, ...pw.warnings]) : null);
  };
  dlg.append(h("div", { class: "dlg-head" }, h("h2", {}, "Maraton GEN3 · yakıt ve balata regresyonu"), h("span", { class: "spacer" }), h("button", { type: "button", class: "ghost", onclick: close }, "Kapat")),
    h("div", { class: "dlg-body stack" },
      h("p", { class: "small muted", style: "margin:0" }, "Dizel şehirlerarası otobüs filo modeli (AR-GE Test Merkezi, Eylül 2026). Hız payları seçili görevden hesaplanır; motor devri ve pedal simülatörde modellenmediği için girdidir."),
      h("div", { class: "formgrid" }, field("Klima kullanımı", f.ac), field("≥1200 rpm payı", f.rpm1200), field("Ort. pedal", f.pedal),
        field("Rölanti yakıtı", f.idle), field("Yılın haftası", f.week), field("Dış sıcaklık", f.ambient), field("Gövde", body),
        h("label", { class: "check" }, kale, h("span", {}, "Kale radyatör"))),
      h("div", {}, h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, run) }, "Hesapla")), out));
  document.body.append(dlg);
  dlg.showModal();
}

/* Temperature sweep in a dialog: the scene stays where it is underneath. */
async function openSweep(p) {
  const r = await api.post("/api/sweep", { ...p, t_min: -15, t_max: 40, t_step: 5 });
  const x = r.series[0].points.map(q => q.t);
  const colors = [css("--s1"), css("--s2")];
  const names = distinctLabels(r.series.map(sr => sr.label));
  const ser = key => r.series.map((sr, i) => ({ label: names[i], color: colors[i], y: sr.points.map(q => q[key]) }));
  const mixed = r.series.some(sr => sr.powertrain === "diesel") && r.series.some(sr => sr.powertrain !== "diesel");
  const allDiesel = r.series.every(sr => sr.powertrain === "diesel");
  const unitLab = allDiesel ? "l/100 km" : mixed ? "kWh/km (dizelde yakıt enerjisi)" : "kWh/km";
  const consKey = allDiesel ? "l_per_100km" : "kwh_per_km";
  const dlg = h("dialog", {});
  const close = () => { dlg.close(); dlg.remove(); };
  dlg.append(h("div", { class: "dlg-head" }, h("h2", {}, "Sıcaklığa göre tüketim ve menzil"), h("span", { class: "spacer" }), h("button", { type: "button", class: "ghost", onclick: close }, "Kapat")),
    h("div", { class: "dlg-body stack" }, h("p", { class: "small muted", style: "margin:0" }, r.cycle + (mixed ? " · Dizel araçta tüketim yakıtın alt ısıl değeri cinsinden kWh/km olarak gösterilir." : "")),
      lineChart({ x, series: ser(consKey), xLabel: "dış sıcaklık (°C)", yLabel: `Tüketim (${unitLab})`, yFmt: v => fmt(v, allDiesel ? 2 : 3), markers: true }),
      lineChart({ x, series: ser("range_km"), xLabel: "dış sıcaklık (°C)", yLabel: "Menzil (km)", yFmt: v => fmt(v, 0) + " km", markers: true }),
      h("div", { class: "scrollx" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Sıcaklık"), r.series.flatMap((sr, i) => [h("th", {}, `${names[i]} · ${sr.powertrain === "diesel" ? "l/100 km" : "kWh/km"}`), h("th", {}, "menzil km"), h("th", {}, "klima %")]))),
        h("tbody", {}, x.map((t, i) => h("tr", {}, h("td", { class: "n" }, `${fmt(t, 0)} °C`), r.series.flatMap(sr => [h("td", { class: "n" }, sr.powertrain === "diesel" ? fmt(sr.points[i].l_per_100km, 2) : fmt(sr.points[i].kwh_per_km, 3)), h("td", { class: "n" }, fmt(sr.points[i].range_km, 0)), h("td", { class: "n" }, fmt(sr.points[i].hvac_share * 100, 0))]))))))));
  document.body.append(dlg);
  dlg.showModal();
}
function kpi(k, v, u) { return h("div", {}, h("div", { class: "k" }, k), h("div", { class: "v" }, v, u ? h("small", {}, u) : null)); }

/* ================================================================ validation */
async function pageValidate(which) {
  const ov = await overview(true);
  const root = h("div", {});
  root.append(pageHead("Doğrulama", "Modeli gerçek otobüsle karşılaştırın",
    "Elektrikli araçta CAN/telematik kaydı batarya enerjisiyle, dizel araçta test ekibinin yakıt testi kaydı litre ve l/100 km ile karşılaştırılır. Fark varsa, hangi bileşene bakmanız gerektiğini söyler."));
  const hasDiesel = ov.vehicles.some(v => v.powertrain === "diesel");
  let mode = which || store.get("valMode", hasDiesel ? "dizel" : "elektrik");
  const modeSeg = seg([["dizel", "Dizel yakıt testi (CAN logu)"], ["elektrik", "Elektrikli araç logu (batarya)"]], mode, v => { mode = v; store.set("valMode", v); draw(); });
  root.append(h("div", { class: "panel", style: "margin-bottom:16px" }, modeSeg));
  const area = h("div", {});
  root.append(area);
  let up = null;
  const draw = () => { if (mode === "dizel") dieselHome(); else s1(); };

  /* ---------------- diesel: test campaigns and single logs ---------------- */
  const dieselVehicles = ov.vehicles.filter(v => !v.error && v.powertrain === "diesel");
  const PH_TR = { idle: "Rölanti", cruise: "Seyir", accel: "Hızlanma", decel: "Yavaşlama", other: "Düşük hız" };
  async function dieselHome() {
    const { campaigns } = await api.get("/api/campaigns");
    const list = h("div", { class: "stack" });
    if (!campaigns.length) list.append(h("p", { class: "muted" }, "Kütüphanede test kampanyası yok (library/testlog/)."));
    campaigns.forEach(c => {
      const btn = h("button", { class: "primary", type: "button" }, "Bütün koşuları karşılaştır");
      btn.addEventListener("click", () => busy(btn, async () => { const r = await api.post(`/api/campaign/${encodeURIComponent(c.name)}/run`, {}); campaignView(c, r); }));
      list.append(h("div", { class: "panel" },
        h("div", { class: "row" }, h("div", {}, h("h3", {}, c.campaign), h("div", { class: "small muted" }, `${c.report || ""} · ${c.dates} · ${c.runs} koşu · araç ${c.vehicle} · test kütlesi ${fmt(c.test_mass_kg, 0)} kg`)), h("span", { class: "spacer" }), btn),
        c.summary_l100 ? h("div", { class: "small", style: "margin-top:6px" }, "Rapordaki ortalamalar: ",
          Object.entries(c.summary_l100).map(([k, v]) => `${k}: gösterge ${fmt(v.display, 1)} · Dewesoft ${fmt(v.dewesoft, 1)} · yakıt cihazı ${fmt(v.fuel_device, 1)} l/100 km`).join(" — ")) : null));
    });
    area.replaceChildren(h("div", { class: "stack" },
      h("h2", {}, "Test kampanyaları"),
      h("p", { class: "small muted", style: "margin:0" }, "Test ekibinin CAN kayıtları ve koşulları (kütle, hava, rüzgâr, klima kapalı) kütüphanede. Her koşu aynı hız iziyle modelden geçer; litre, l/100 km, evre payları, devir ve vites yan yana gelir."),
      list,
      h("h2", { style: "margin-top:16px" }, "Tek bir CAN logu yükle"), singleLogForm()));
  }

  function singleLogForm() {
    const vSel = select(dieselVehicles.map(v => [v.stem, `${v.name} — ${v.variant}`]), store.get("vehicle", dieselVehicles[0] && dieselVehicles[0].stem));
    const mass = numInput(18000, { unit: "kg" }); const amb = numInput(25, { unit: "°C" }); const wind = numInput(0, { unit: "m/s" });
    const dist = numInput("", { unit: "km", placeholder: "logdan" }); const dev = numInput("", { unit: "l", placeholder: "varsa" });
    const routeSel = select([["", "— düz yol —"], ...ov.routes.map(r => [r, r])], "mersin_adana_otoyol_gidis");
    const dirSeg = h("div", {}); let dir = "gidis"; dirSeg.append(seg([["gidis", "Gidiş (dosya yönünde)"], ["donus", "Dönüş (ters)"]], dir, v => (dir = v)));
    const night = h("input", { type: "checkbox" }); const hvacOff = h("input", { type: "checkbox", checked: true });
    let file = null; const fname = h("span", { class: "small muted" }, "dosya seçilmedi");
    const runBtn = h("button", { class: "primary", type: "button" }, "Modelle karşılaştır");
    runBtn.addEventListener("click", () => busy(runBtn, async () => {
      if (!file) throw new Error("Önce bir CAN log dosyası seçin.");
      const r = await api.upload("/api/correlate", file, { vehicle: vSel.value, mass_kg: parseNum($("input", mass).value) || "", ambient: parseNum($("input", amb).value) || 20,
        headwind: parseNum($("input", wind).value) || 0, night: night.checked ? "1" : "", hvac_off: hvacOff.checked ? "1" : "", route: routeSel.value, direction: dir,
        distance_km: parseNum($("input", dist).value) || "", fuel_device_l: parseNum($("input", dev).value) || "" });
      singleView(r);
    }));
    return h("div", { class: "panel stack" },
      dropZone({ compact: true, accept: ".xlsx,.csv,.txt", title: "Dewesoft / CAN dışa aktarımı", sub: "Time, WheelBasedVehicleSpeed, EngSpeed, TransCurrentGear, HighResolutionEngFuelRate… (xlsx ya da CSV)",
        onFile: f => { file = f; fname.textContent = f.name; } }), fname,
      h("div", { class: "formgrid" }, field("Araç", vSel), field("Test kütlesi", mass), field("Dış sıcaklık", amb),
        field("Rüzgâr (karşıdan +)", wind, { hint: "Gidiş yönünde karşı rüzgâr pozitif." }), field("Güzergâh (eğim için)", routeSel), h("div", { class: "field" }, h("div", { class: "lab" }, "Yön"), dirSeg),
        field("Parkur uzunluğu", dist, { hint: "Verilirse CAN hızı bu mesafeye ölçeklenir." }), field("Yakıt cihazı okuması", dev),
        h("label", { class: "check" }, night, h("span", {}, "Gece")), h("label", { class: "check" }, hvacOff, h("span", {}, "Klima kapalı (test koşulu)"))),
      h("div", {}, runBtn));
  }

  const pct = (x, d = 1) => x == null ? "—" : `${x > 0 ? "+" : ""}${fmt(100 * x, d)} %`;
  function campaignView(c, r) {
    const rows = r.rows; const s = r.summary;
    let sel = rows[0] && rows[0].key;
    const detail = h("div", {});
    const drawDetail = () => {
      const row = rows.find(x => x.key === sel); if (!row) return;
      detail.replaceChildren(runDetail(row, r.overlays[sel], row.notes));
    };
    const table = h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["Koşu", "Koşul", "CAN l", "Cihaz l", "Model l", "Model vs CAN", "Model vs cihaz", "Seyir l/h ölç/model", "Rölanti l/h", "Devir ölç/model", "Vites uyumu"].map(x => h("th", {}, x)))),
      h("tbody", {}, rows.map(x => {
        const tr = h("tr", { class: x.key === sel ? "sel" : "", style: "cursor:pointer", onclick: () => { sel = x.key; $$("tr", table).forEach(e => e.classList.toggle("sel", e.dataset.k === sel)); drawDetail(); }, "data-k": x.key },
          h("td", {}, h("b", {}, x.key)), h("td", { class: "small" }, `${x.direction} · ${x.night ? "gece" : "gündüz"} · ${fmt(x.ambient_c, 1)} °C · rüzgâr ${fmt(x.wind_mps, 1)} m/s`),
          h("td", { class: "n" }, fmt(x.fuel_can_l, 3)), h("td", { class: "n" }, x.fuel_device_l ? fmt(x.fuel_device_l, 3) : "—"), h("td", { class: "n" }, fmt(x.fuel_sim_l, 3)),
          h("td", { class: "n" }, pct(x.err_can)), h("td", { class: "n", style: x.err_device != null && Math.abs(x.err_device) <= 0.05 ? "color:var(--ok);font-weight:600" : "" }, pct(x.err_device)),
          h("td", { class: "n" }, `${fmt(x.cruise_lph_meas, 1)} / ${fmt(x.cruise_lph_sim, 1)}`), h("td", { class: "n" }, `${fmt(x.idle_lph_meas, 2)} / ${fmt(x.idle_lph_sim, 2)}`),
          h("td", { class: "n" }, `${fmt(x.rpm_cruise_meas, 0)} / ${fmt(x.rpm_cruise_sim, 0)}`), h("td", { class: "n" }, x.gear_agreement != null ? `%${fmt(100 * x.gear_agreement, 0)}` : "—"));
        return tr;
      })));
    area.replaceChildren(h("div", { class: "stack" },
      h("div", { class: "row" }, h("button", { type: "button", class: "ghost", onclick: dieselHome }, "← Kampanyalar"), h("h2", { style: "margin:0" }, c.campaign), h("span", { class: "muted small" }, `${r.vehicle} · test kütlesi ${fmt(r.meta.test_mass_kg, 0)} kg · klima ${(r.meta.hvac === "off" || r.meta.hvac === false) ? "kapalı (test koşulu)" : "açık"} · eğim ${r.route_found ? "güzergâhtan" : "YOK (düz)"}`)),
      h("div", { class: "kpis" },
        kpi("Ölçülen (CAN)", fmt(s.fuel_can_l, 2), "l"), s.fuel_device_l ? kpi("Ölçülen (yakıt cihazı)", fmt(s.fuel_device_l, 2), "l") : null, kpi("Model", fmt(s.fuel_sim_l, 2), "l"),
        kpi("Model vs CAN", pct(s.err_can), ""), s.err_device != null ? kpi("Model vs cihaz", pct(s.err_device), "") : null,
        s.mean_abs_err_device != null ? kpi("Koşu başına ort. mutlak fark", `%${fmt(100 * s.mean_abs_err_device, 1)}`, "cihaza göre") : null,
        kpi("Vites uyumu", `%${fmt(100 * (s.mean_gear_agreement || 0), 0)}`, "")),
      notice("", "Nasıl okunmalı", ["Yakıt cihazı (harici debimetre) CAN'dan yaklaşık %10 yüksek okur; iki sütun da verilir, referansın hangisi olacağı test ekibiyle netleşmeli.",
        "Satıra tıklayınca o koşunun yakıt debisi, devir ve vites izleri ölçümle üst üste gelir.", "Eğim OpenStreetMap + SRTM'den; VBox yükseklik verisi gelince profil değiştirilmeli."]),
      h("div", { class: "panel scrollx" }, table),
      detail));
    drawDetail();
  }

  function singleView(r) {
    const m = r.metrics;
    area.replaceChildren(h("div", { class: "stack" },
      h("div", { class: "row" }, h("button", { type: "button", class: "ghost", onclick: dieselHome }, "← Geri"), h("h2", { style: "margin:0" }, r.log.name), h("span", { class: "muted small" }, `${fmt(r.log.duration_min, 1)} dk · ${fmt(r.log.distance_km, 2)} km (CAN) · ${Object.keys(r.log.columns).length} sinyal tanındı`)),
      h("div", { class: "kpis" }, kpi("Ölçülen (CAN)", fmt(m.fuel_meas_l, 3), "l"), m.fuel_meas_device_l ? kpi("Yakıt cihazı", fmt(m.fuel_meas_device_l, 3), "l") : null, kpi("Model", fmt(m.fuel_sim_l, 3), "l"),
        kpi("Model vs CAN", pct(m.fuel_error), ""), m.fuel_error_device != null ? kpi("Model vs cihaz", pct(m.fuel_error_device), "") : null, kpi("l/100 km", `${fmt(m.l100_meas, 2)} / ${fmt(m.l100_sim, 2)}`, "ölçüm / model")),
      runDetail({ by_phase: r.by_phase, rpm_cruise_meas: m.rpm_cruise_meas, rpm_cruise_sim: m.rpm_cruise_sim, gear_agreement: m.gear_agreement, idle_lph_meas: m.idle_lph_meas, idle_lph_sim: m.idle_lph_sim }, r.overlay, r.notes.concat(r.log.notes))));
  }

  function runDetail(row, ov2, notes) {
    const x = ov2.distance_km;
    const byPhase = row.by_phase || {};
    return h("div", { class: "stack" },
      notes && notes.length ? notice("warn", "Fark nereyi gösteriyor", notes) : null,
      ov2.meas_fuel_lph ? h("div", { class: "panel" }, lineChart({ x, series: [{ label: "Ölçülen (CAN)", color: css("--s2"), y: ov2.meas_fuel_lph }, { label: "Model", color: css("--s1"), y: ov2.sim_fuel_lph }], xLabel: "mesafe (km)", yLabel: "Yakıt debisi (l/h)", yFmt: v => fmt(v, 1), height: 220 })) : null,
      ov2.meas_fuel_cum_l ? h("div", { class: "panel" }, lineChart({ x, series: [{ label: "Ölçülen (CAN)", color: css("--s2"), y: ov2.meas_fuel_cum_l }, { label: "Model", color: css("--s1"), y: ov2.sim_fuel_cum_l }], xLabel: "mesafe (km)", yLabel: "Toplam yakıt (l)", yFmt: v => fmt(v, 2), height: 200 })) : null,
      h("div", { class: "grid-2" },
        ov2.meas_rpm ? h("div", { class: "panel" }, lineChart({ x, series: [{ label: "Ölçülen", color: css("--s2"), y: ov2.meas_rpm }, { label: "Model", color: css("--s1"), y: ov2.sim_rpm }], xLabel: "mesafe (km)", yLabel: "Motor devri (rpm)", yFmt: v => fmt(v, 0), height: 200 })) : null,
        ov2.meas_gear ? h("div", { class: "panel" }, lineChart({ x, series: [{ label: "Ölçülen", color: css("--s2"), y: ov2.meas_gear }, { label: "Model", color: css("--s1"), y: ov2.sim_gear }], xLabel: "mesafe (km)", yLabel: "Vites", yFmt: v => fmt(v, 0), height: 200 })) : null),
      h("div", { class: "panel" }, lineChart({ x, series: [{ label: "Hız", color: css("--s4"), y: ov2.speed_kmh }, { label: "Eğim ×10", color: css("--s5"), y: ov2.grade_pct.map(g => g * 10) }], xLabel: "mesafe (km)", yLabel: "km/h · eğim %×10", yFmt: v => fmt(v, 0), height: 180 })),
      Object.keys(byPhase).length ? h("div", { class: "panel" }, h("h3", {}, "Evrelere göre yakıt"),
        h("table", { class: "data", style: "margin-top:8px" }, h("thead", {}, h("tr", {}, ["Evre", "Süre payı", "Ölçülen l", "Model l", "Fark l", "Ölç. l/h", "Model l/h"].map(c => h("th", {}, c)))),
          h("tbody", {}, Object.entries(byPhase).map(([k, v]) => h("tr", {}, h("td", {}, PH_TR[k] || k), h("td", { class: "n" }, `%${fmt(100 * v.share_time, 0)}`), h("td", { class: "n" }, fmt(v.meas_l, 3)), h("td", { class: "n" }, fmt(v.sim_l, 3)),
            h("td", { class: "n", style: Math.abs(v.sim_l - v.meas_l) > 0.3 ? "color:var(--err)" : "" }, `${v.sim_l - v.meas_l > 0 ? "+" : ""}${fmt(v.sim_l - v.meas_l, 3)}`), h("td", { class: "n" }, fmt(v.mean_lph_meas, 2)), h("td", { class: "n" }, fmt(v.mean_lph_sim, 2))))))) : null);
  }

  const s1 = () => area.replaceChildren(steps(["Log dosyası", "Sütunlar", "Sonuç"], 0), h("div", { class: "stack" },
    dropZone({ accept: ".csv,.txt,.xlsx,.xlsm,.tsv", title: "Log dosyasını bırakın ya da seçin",
      sub: "Gerekli: zaman ve hız, ayrıca batarya gücü ya da gerilim+akım ya da SOC. Önerilen: eğim, yolcu, kapı, dış sıcaklık.",
      onFile: async f => {
        area.replaceChildren(steps(["Log dosyası", "Sütunlar", "Sonuç"], 0), h("div", { class: "muted" }, `${f.name} okunuyor…`));
        try { up = await api.upload("/api/import/log", f); up.fileName = f.name; s2(); }
        catch (e) { s1(); area.append(notice("err", "Log okunamadı.", [e.message])); }
      } })));

  const s2 = () => {
    const sels = {};
    const REQ = { time: true, speed: true };
    const ROLE_HINT = { power: "kW ya da W; yoksa gerilim+akım kullanılır", soc: "% ya da 0–1", grade: "yüzde (%)", doors: "0/1", ambient: "°C" };
    const opts = [["", "— yok —"], ...up.headers.map((hd, i) => [String(i), hd || `sütun ${i + 1}`])];
    const rows = up.roles.map(([role, label]) => {
      const cur = up.mapping[role] != null ? String(up.mapping[role]) : "";
      const sel = select(opts, cur); sels[role] = sel;
      const sample = h("td", { class: "n muted" });
      const upd = () => { const j = sel.value === "" ? null : +sel.value; sample.textContent = j == null ? "" : up.preview.slice(1, 4).map(r => r[j] ?? "").join(" · "); };
      sel.addEventListener("change", upd); upd();
      return h("tr", {}, h("td", {}, h("b", {}, label), REQ[role] ? h("span", { class: "badge warn", style: "margin-left:6px" }, "gerekli") : null,
        ROLE_HINT[role] ? h("div", { class: "hint" }, ROLE_HINT[role]) : null), h("td", { style: "min-width:200px" }, sel), sample);
    });
    let spUnit = "", pSign = "";
    const vSel = select(ov.vehicles.filter(v => !v.error).map(v => [v.stem, `${v.name} — ${v.variant}`]), store.get("vehicle", ov.vehicles[0] && ov.vehicles[0].stem));
    const tol = numInput(5, { unit: "%" }); const amb = numInput("", { unit: "°C", placeholder: "logdan" });
    area.replaceChildren(steps(["Log dosyası", "Sütunlar", "Sonuç"], 1), h("div", { class: "stack" },
      h("div", { class: "panel" }, h("div", { class: "row" }, h("h3", {}, up.fileName), h("span", { class: "spacer" }),
        h("span", { class: "small muted" }, up.table.kind === "excel" ? `Excel, sayfa “${up.table.sheet}”` : `ayraç ${up.table.delimiter} · ondalık “${up.table.decimal}” · ${up.table.rows} satır`)),
        h("p", { class: "small muted", style: "margin:4px 0 12px" }, "Otomatik eşleştirme aşağıda. Yanlış olanı düzeltin; eşleşmeyen sütunlar kullanılmaz."),
        h("div", { class: "scrollx" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Veri"), h("th", {}, "Dosyadaki sütun"), h("th", {}, "İlk değerler"))), h("tbody", {}, rows)))),
      h("div", { class: "panel" }, h("div", { class: "formgrid" },
        field("Karşılaştırılacak araç", vSel),
        h("div", { class: "field" }, h("div", { class: "lab" }, "Hız birimi"), seg([["", "Otomatik"], ["kmh", "km/h"], ["mps", "m/s"]], "", v => (spUnit = v))),
        h("div", { class: "field" }, h("div", { class: "lab" }, "Güç işareti"), seg([["", "Otomatik"], ["discharge_positive", "Deşarj +"], ["discharge_negative", "Deşarj −"]], "", v => (pSign = v))),
        field("Enerji toleransı", tol), field("Dış sıcaklık", amb, { hint: "Boş bırakılırsa logdaki sütun, o da yoksa 20 °C." }))),
      h("div", { class: "savebar" },
        h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, async () => {
          const mapping = Object.fromEntries(Object.entries(sels).filter(([, sl]) => sl.value !== "").map(([k, sl]) => [k, +sl.value]));
          if (mapping.time == null || mapping.speed == null) throw new Error("Zaman ve hız sütunlarını seçin.");
          store.set("vehicle", vSel.value);
          const r = await api.post("/api/validate", { upload_id: up.upload_id, mapping, header_row: up.header_row, vehicle: vSel.value,
            speed_unit: spUnit, power_sign: pSign, tolerance: parseNum($("input", tol).value) || 5, ambient: $("input", amb).value.trim() === "" ? "" : parseNum($("input", amb).value) });
          s3(r);
        }) }, "Doğrula"),
        h("button", { type: "button", class: "ghost", onclick: s1 }, "Başka dosya seç"))));
  };

  const s3 = r => {
    const m = r.metrics, ov2 = r.overlay;
    const hasP = m.energy_error != null, hasS = m.soc_rmse != null;
    area.replaceChildren(steps(["Log dosyası", "Sütunlar", "Sonuç"], 2), h("div", { class: "stack" },
      h("div", { class: "verdict " + (r.passed ? "pass" : "fail"), role: "status" },
        h("div", { class: "big" }, r.passed ? "UYUMLU" : "SAPMA VAR"),
        h("div", {}, h("b", {}, r.passed ? "Model bu sürüşü tolerans içinde açıklıyor." : "Model bu sürüşü tolerans içinde açıklayamıyor."),
          h("div", { class: "small" }, hasP ? `Enerji farkı %${fmt(m.energy_error * 100, 1)} (tolerans ±%${fmt(r.tolerance * 100, 0)}).` : "", r.passed ? " Bu sonuçla ilgili bileşenleri ‘ölçümle doğrulandı’ olarak işaretleyebilirsiniz." : ""))),
      h("div", { class: "kpis" },
        hasP ? kpi("Ölçülen enerji", fmt(m.measured_kwh, 2), "kWh") : null,
        hasP ? kpi("Model enerjisi", fmt(m.simulated_kwh, 2), "kWh") : null,
        hasP ? kpi("Enerji farkı", "%" + fmt(m.energy_error * 100, 1), "") : null,
        hasP ? kpi("Güç korelasyonu", fmt(m.power_correlation, 3), "") : null,
        hasS ? kpi("SOC sapması (RMS)", fmt(m.soc_rmse * 100, 2), "puan") : null,
        kpi("Mesafe", fmt(m.distance_km, 2), "km")),
      r.notes.length ? notice(r.passed ? "" : "warn", "Tanı ve notlar", r.notes) : null,
      hasP ? h("div", { class: "panel" }, lineChart({ x: ov2.time_s, series: [{ label: "Ölçülen", color: css("--s2"), y: ov2.meas_kw }, { label: "Model", color: css("--s1"), y: ov2.sim_kw }], xLabel: "zaman (s)", yLabel: "Batarya gücü (kW, deşarj +)", yFmt: v => fmt(v, 1) + " kW" })) : null,
      hasS && ov2.meas_soc ? h("div", { class: "panel" }, lineChart({ x: ov2.time_s, series: [{ label: "Ölçülen", color: css("--s2"), y: ov2.meas_soc }, { label: "Model", color: css("--s1"), y: ov2.sim_soc }], xLabel: "zaman (s)", yLabel: "SOC (%)", yFmt: v => "%" + fmt(v, 1) })) : null,
      Object.keys(r.by_phase).length ? h("div", { class: "panel" }, h("h3", {}, "Sürüş evresine göre enerji"),
        h("table", { class: "data", style: "margin-top:8px" }, h("thead", {}, h("tr", {}, h("th", {}, "Evre"), h("th", {}, "Model kWh"), h("th", {}, "Ölçülen kWh"), h("th", {}, "Ort. güç farkı"))),
          h("tbody", {}, Object.entries(r.by_phase).map(([k, v]) => h("tr", {}, h("td", {}, { driving: "Sürüş", braking: "Frenleme", standstill: "Duruş" }[k] || k),
            h("td", { class: "n" }, fmt(v.sim, 3)), h("td", { class: "n" }, fmt(v.meas, 3)),
            h("td", { class: "n" }, m.mean_power_error_kw && m.mean_power_error_kw[k] != null ? fmt(m.mean_power_error_kw[k], 1) + " kW" : "—"))))),
        h("p", { class: "small muted", style: "margin:8px 0 0" }, "Her evrede aynı ortalama güç farkı: sabit bir yük eksik/fazla (yardımcılar, klima). Fark frenlemede toplanıyorsa: rejenerasyon sınırları. Sürüşte toplanıyorsa: yol direnci ya da motor haritası.")) : null,
      h("div", { class: "row" }, h("button", { type: "button", onclick: s2 }, "← Sütunlara dön"), h("button", { type: "button", class: "ghost", onclick: s1 }, "Yeni log"))));
  };
  draw();
  return root;
}

/* ================================================================ go */
route();
