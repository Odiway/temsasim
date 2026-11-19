/* Signal explorer: any signals of a run on one plane.

   The report's fixed charts answer the usual questions. This is for the rest:
   tick the signals you want (speed, gear and engine speed; torque against
   full load; current against SOC), they are drawn over each other, each unit
   on its own scale, and a drag across the plot zooms in; the two boxes in
   the bar take the same range as numbers. Stand-alone on
   purpose (no imports, its own styles): the application loads this file and
   the HTML report embeds it, so a saved report keeps the tool. */
(function (root) {
  "use strict";
  var PALETTE = ["#d98a00", "#2f6fb0", "#c0392b", "#2e8b57", "#7b4fb3", "#00838f", "#8d6e63", "#5d6d7e"];
  var MAX = PALETTE.length;
  var CSS = [
    ".tx{font:13px/1.4 var(--body,system-ui,sans-serif);color:var(--ink,#1c1b19)}",
    ".tx-bar{display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;margin:0 0 8px}",
    ".tx-bar button,.tx-bar select{font:600 11.5px var(--cond,inherit);letter-spacing:.06em;text-transform:uppercase;padding:5px 9px;border:1px solid var(--rule,#cfc9bd);background:var(--panel,#fff);color:var(--ink,#1c1b19);border-radius:3px;cursor:pointer;width:auto;flex:none}",
    ".tx-bar button:hover{border-color:var(--amber,#ffb000)}",
    ".tx-bar button[aria-pressed=true]{border-color:var(--amber,#ffb000);box-shadow:inset 0 -2px 0 var(--amber,#ffb000)}",
    ".tx-bar .tx-sp{flex:1}",
    ".tx-rng{display:inline-flex;gap:5px;align-items:center;font-size:11.5px;color:var(--muted,#6f6a60);white-space:nowrap}",
    ".tx-rng input{width:76px;padding:4px 6px;font:12px var(--mono,ui-monospace,monospace);border:1px solid var(--rule,#cfc9bd);border-radius:3px;background:var(--panel,#fff);color:var(--ink,#1c1b19);text-align:right}",
    ".tx-rng input:focus{outline:none;border-color:var(--amber,#ffb000)}",
    ".tx-hint{font-size:11.5px;color:var(--muted,#6f6a60)}",
    ".tx-body{display:grid;grid-template-columns:220px minmax(0,1fr);gap:12px;align-items:start}",
    "@media(max-width:860px){.tx-body{grid-template-columns:1fr}}",
    ".tx-list{max-height:430px;overflow:auto;border:1px solid var(--rule,#cfc9bd);background:var(--panel,#fff);padding:6px 8px;border-radius:3px}",
    ".tx-grp{font:600 10.5px var(--cond,inherit);letter-spacing:.1em;text-transform:uppercase;color:var(--muted,#6f6a60);margin:8px 0 2px}",
    ".tx-grp:first-child{margin-top:2px}",
    ".tx-list label{display:flex;gap:6px;align-items:center;padding:2px 0;cursor:pointer;font-size:12.5px}",
    ".tx-list label small{color:var(--muted,#6f6a60);margin-left:auto;white-space:nowrap}",
    ".tx-sw{width:10px;height:10px;border-radius:2px;flex:none;border:1px solid var(--rule,#cfc9bd)}",
    ".tx-plot{position:relative;border:1px solid var(--rule,#cfc9bd);background:var(--panel,#fff);border-radius:3px;min-width:0}",
    ".tx-plot svg{display:block;width:100%;cursor:crosshair;user-select:none}",
    ".tx-read{display:flex;flex-wrap:wrap;gap:4px 16px;padding:6px 10px;border-top:1px solid var(--rule,#cfc9bd);min-height:30px;font-size:12.5px;font-variant-numeric:tabular-nums}",
    ".tx-read b{font-weight:600}",
    ".tx-read span.k{display:inline-flex;gap:5px;align-items:center}"
  ].join("\n");

  function el(tag, attrs, kids) {
    var e = document.createElement(tag), k;
    for (k in attrs || {}) {
      if (attrs[k] == null || attrs[k] === false) continue;
      if (k === "text") e.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function sv(tag, attrs, parent) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag), k;
    for (k in attrs || {}) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function num(v, d) {
    if (v == null || !isFinite(v)) return "—";
    return v.toLocaleString("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function ticks(lo, hi, n) {
    var span = hi - lo || 1, raw = span / Math.max(1, n), mag = Math.pow(10, Math.floor(Math.log10(raw))), r = raw / mag;
    var step = (r >= 5 ? 10 : r >= 2 ? 5 : r >= 1 ? 2 : 1) * mag, out = [], t = Math.ceil(lo / step) * step;
    for (; t <= hi + step * 1e-6; t += step) out.push(Math.abs(t) < step * 1e-9 ? 0 : t);
    return { ticks: out, digits: step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : 3 };
  }
  function lowerBound(a, v) { var lo = 0, hi = a.length; while (lo < hi) { var m = (lo + hi) >> 1; if (a[m] < v) lo = m + 1; else hi = m; } return lo; }

  function mount(host, data, opts) {
    opts = opts || {};
    if (!document.getElementById("tx-css")) document.head.appendChild(el("style", { id: "tx-css", text: CSS }));
    var byKey = {};
    data.signals.forEach(function (s) { byKey[s.key] = s; });
    var st = { xi: 0, sel: (data.selected || []).filter(function (k) { return byKey[k]; }), x0: null, x1: null, H: opts.height || 380 };
    var colorOf = {};
    function recolor() {
      var used = {};
      st.sel.forEach(function (k) { if (colorOf[k] != null) used[colorOf[k]] = 1; });
      Object.keys(colorOf).forEach(function (k) { if (st.sel.indexOf(k) < 0) delete colorOf[k]; });
      st.sel.forEach(function (k) {
        if (colorOf[k] != null) return;
        for (var i = 0; i < MAX; i++) if (!used[i]) { colorOf[k] = i; used[i] = 1; return; }
        colorOf[k] = 0;
      });
    }
    var host2 = el("div", { class: "tx" });
    var bar = el("div", { class: "tx-bar" });
    var list = el("div", { class: "tx-list" });
    var plot = el("div", { class: "tx-plot" });
    var read = el("div", { class: "tx-read" });
    var svgBox = el("div", {});
    plot.appendChild(svgBox); plot.appendChild(read);
    host2.appendChild(bar); host2.appendChild(el("div", { class: "tx-body" }, [list, plot]));
    host.replaceChildren(host2);

    function setSel(keys) { st.sel = keys.filter(function (k) { return byKey[k]; }).slice(0, MAX); drawList(); draw(); drawBar(); }
    function drawBar() {
      bar.replaceChildren();
      (data.presets || []).forEach(function (p) {
        var on = p[1].length === st.sel.length && p[1].every(function (k) { return st.sel.indexOf(k) >= 0; });
        bar.appendChild(el("button", { type: "button", "aria-pressed": on ? "true" : "false", text: p[0], onclick: function () { setSel(p[1]); } }));
      });
      bar.appendChild(el("span", { class: "tx-sp" }));
      if (data.x.length > 1) {
        var sel = el("select", { "aria-label": "Yatay eksen", onchange: function () { st.xi = +sel.value; st.x0 = st.x1 = null; drawBar(); draw(); } },
          data.x.map(function (x, i) { return el("option", { value: i, selected: i === st.xi ? "selected" : null, text: x.label + " [" + x.unit + "]" }); }));
        bar.appendChild(sel);
      }
      /* the range as numbers: type a start and an end to look at just that part */
      var X = data.x[st.xi], xd = X.digits != null ? X.digits : X.unit === "km" ? 2 : 0, stepX = Math.pow(10, -xd);
      function typed() {
        var a = parseFloat(st.inA.value), b = parseFloat(st.inB.value), xs = X.values, lo = xs[0], hi = xs[xs.length - 1];
        if (!isFinite(a)) a = lo; if (!isFinite(b)) b = hi;
        a = Math.min(Math.max(a, lo), hi); b = Math.min(Math.max(b, lo), hi);
        if (b - a < stepX) { st.x0 = st.x1 = null; } else { st.x0 = a <= lo ? null : a; st.x1 = b >= hi ? null : b; if (st.x0 == null && st.x1 != null) st.x0 = lo; if (st.x1 == null && st.x0 != null) st.x1 = hi; }
        draw();
      }
      st.inA = el("input", { type: "number", step: stepX, "aria-label": X.label + " başlangıcı", title: "Başlangıç", onchange: typed });
      st.inB = el("input", { type: "number", step: stepX, "aria-label": X.label + " bitişi", title: "Bitiş", onchange: typed });
      st.inA.value = (+(st.x0 == null ? X.values[0] : st.x0)).toFixed(xd);
      st.inB.value = (+(st.x1 == null ? X.values[X.values.length - 1] : st.x1)).toFixed(xd);
      bar.appendChild(el("span", { class: "tx-rng" }, [el("span", { text: "Aralık" }), st.inA, el("span", { text: "–" }), st.inB, el("span", { text: X.unit })]));
      bar.appendChild(el("button", { type: "button", text: "Tümünü göster", title: "Yakınlaştırmayı sıfırla (çift tık da olur)", onclick: function () { st.x0 = st.x1 = null; draw(); } }));
      bar.appendChild(el("span", { class: "tx-hint", text: "Sürükle: yakınlaştır · en çok " + MAX + " sinyal · aynı birim aynı ölçekte" }));
    }
    function drawList() {
      recolor();
      list.replaceChildren();
      var groups = [];
      data.signals.forEach(function (s) { if (groups.indexOf(s.group) < 0) groups.push(s.group); });
      groups.forEach(function (g) {
        list.appendChild(el("div", { class: "tx-grp", text: g }));
        data.signals.filter(function (s) { return s.group === g; }).forEach(function (s) {
          var on = st.sel.indexOf(s.key) >= 0;
          var cb = el("input", { type: "checkbox", checked: on ? "checked" : null, onchange: function () {
            if (cb.checked) { if (st.sel.length >= MAX) { cb.checked = false; return; } st.sel.push(s.key); }
            else st.sel = st.sel.filter(function (k) { return k !== s.key; });
            drawList(); draw(); drawBar();
          } });
          list.appendChild(el("label", {}, [cb, el("span", { class: "tx-sw", style: on ? "background:" + PALETTE[colorOf[s.key]] + ";border-color:transparent" : null }),
            el("span", { text: s.label }), el("small", { text: s.unit || "" })]));
        });
      });
    }

    function scales(sigs) {
      /* one scale per unit; signals without a unit (gear, door state) each get their own */
      var out = [], map = {};
      sigs.forEach(function (s) {
        var id = s.unit ? "u:" + s.unit : "k:" + s.key;
        if (!map[id]) { map[id] = { unit: s.unit, sigs: [], lo: Infinity, hi: -Infinity }; out.push(map[id]); }
        var sc = map[id]; sc.sigs.push(s);
        for (var i = 0; i < s.values.length; i++) { var v = s.values[i]; if (v == null) continue; if (v < sc.lo) sc.lo = v; if (v > sc.hi) sc.hi = v; }
      });
      out.forEach(function (sc) {
        if (!isFinite(sc.lo)) { sc.lo = 0; sc.hi = 1; }
        if (sc.sigs.every(function (s) { return s.step; })) { sc.lo = Math.min(0, sc.lo); sc.hi = sc.hi + 1; }
        else { var pad = (sc.hi - sc.lo) * 0.06 || 1; sc.lo -= pad; sc.hi += pad; if (sc.lo > 0 && sc.lo - pad * 3 < 0) sc.lo = 0; }
      });
      return out;
    }

    function draw() {
      recolor();
      var X = data.x[st.xi], xs = X.values, n = xs.length;
      var W = Math.max(420, Math.floor(svgBox.clientWidth || host.clientWidth - 240 || 900)), H = st.H;
      var T = 12, B = 34, L = 54, R = 54;
      var xa = st.x0 == null ? xs[0] : st.x0, xb = st.x1 == null ? xs[n - 1] : st.x1;
      if (!(xb > xa)) { xa = xs[0]; xb = xs[n - 1] || xa + 1; }
      var xdig = X.digits != null ? X.digits : X.unit === "km" ? 2 : 0;
      if (st.inA && document.activeElement !== st.inA) st.inA.value = (+xa).toFixed(xdig);
      if (st.inB && document.activeElement !== st.inB) st.inB.value = (+xb).toFixed(xdig);
      var i0 = Math.max(0, lowerBound(xs, xa) - 1), i1 = Math.min(n - 1, lowerBound(xs, xb) + 1);
      var sx = function (v) { return L + (v - xa) / (xb - xa) * (W - L - R); };
      var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, height: H, role: "img", "aria-label": "Sinyal grafiği" });
      sv("clipPath", { id: "tx-clip" }, sv("defs", {}, svg)).appendChild(sv("rect", { x: L, y: T, width: W - L - R, height: H - T - B }));
      var sigs = st.sel.map(function (k) { return byKey[k]; });
      var scs = scales(sigs);
      var ink = "var(--ink-2,#444)", grid = "var(--grid,#e7e2d8)", muted = "var(--muted,#6f6a60)";
      /* x axis */
      var xt = ticks(xa, xb, Math.max(3, Math.floor((W - L - R) / 110)));
      xt.ticks.forEach(function (t) {
        sv("line", { x1: sx(t), x2: sx(t), y1: T, y2: H - B, stroke: grid, "shape-rendering": "crispEdges" }, svg);
        sv("text", { x: sx(t), y: H - B + 15, "text-anchor": "middle", "font-size": 11, fill: muted }, svg).textContent = num(t, xt.digits);
      });
      sv("text", { x: (L + W - R) / 2, y: H - 4, "text-anchor": "middle", "font-size": 11.5, fill: ink }, svg).textContent = X.label + " [" + X.unit + "]";
      /* y axes: the first two scales are labelled, left and right */
      scs.forEach(function (sc, k) {
        sc.sy = function (v) { return T + (1 - (v - sc.lo) / (sc.hi - sc.lo)) * (H - T - B); };
        if (k > 1) return;
        var col = sc.sigs.length === 1 ? PALETTE[colorOf[sc.sigs[0].key]] : ink;
        var yt = ticks(sc.lo, sc.hi, 5);
        yt.ticks.forEach(function (t) {
          if (k === 0) sv("line", { x1: L, x2: W - R, y1: sc.sy(t), y2: sc.sy(t), stroke: grid, "shape-rendering": "crispEdges" }, svg);
          sv("text", { x: k === 0 ? L - 6 : W - R + 6, y: sc.sy(t) + 4, "text-anchor": k === 0 ? "end" : "start", "font-size": 11, fill: col }, svg).textContent = num(t, yt.digits);
        });
        sv("text", { x: k === 0 ? 4 : W - 4, y: T + 2, "text-anchor": k === 0 ? "start" : "end", "font-size": 10.5, fill: col, "dominant-baseline": "hanging" }, svg)
          .textContent = sc.unit || sc.sigs[0].label;
      });
      sv("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "none", stroke: "var(--rule,#cfc9bd)" }, svg);
      /* curves */
      var g = sv("g", { "clip-path": "url(#tx-clip)" }, svg);
      var cols = Math.max(1, Math.floor((W - L - R) * 1.5));
      scs.forEach(function (sc) {
        sc.sigs.forEach(function (s) {
          var d = [], pen = false, i, v, stride = Math.max(1, Math.ceil((i1 - i0 + 1) / cols));
          if (s.step || stride === 1) {
            var py = null;
            for (i = i0; i <= i1; i++) {
              v = s.values[i];
              if (v == null) { pen = false; py = null; continue; }
              var px = sx(xs[i]).toFixed(1), y = sc.sy(v).toFixed(1);
              if (!pen) { d.push("M" + px + " " + y); pen = true; }
              else if (s.step && py !== y) d.push("H" + px + "V" + y);
              else d.push("L" + px + " " + y);
              py = y;
            }
          } else {
            /* thin by buckets, keeping each bucket's extremes so spikes survive */
            for (i = i0; i <= i1; i += stride) {
              var lo = Infinity, hi = -Infinity, j, any = false;
              for (j = i; j < Math.min(i + stride, i1 + 1); j++) { v = s.values[j]; if (v == null) continue; any = true; if (v < lo) lo = v; if (v > hi) hi = v; }
              if (!any) { pen = false; continue; }
              var bx = sx(xs[i]).toFixed(1);
              d.push((pen ? "L" : "M") + bx + " " + sc.sy(lo).toFixed(1));
              if (hi !== lo) d.push("L" + bx + " " + sc.sy(hi).toFixed(1));
              pen = true;
            }
          }
          sv("path", { d: d.join(""), fill: "none", stroke: PALETTE[colorOf[s.key]], "stroke-width": 1.6, "stroke-linejoin": "round" }, g);
        });
      });
      if (!sigs.length) sv("text", { x: (L + W - R) / 2, y: H / 2, "text-anchor": "middle", "font-size": 13, fill: muted }, svg).textContent = "Soldan sinyal seçin.";
      /* cursor, read-out, drag zoom */
      var cur = sv("line", { y1: T, y2: H - B, stroke: "var(--axis,#555)", opacity: 0 }, svg);
      var band = sv("rect", { y: T, height: H - T - B, fill: "var(--amber,#ffb000)", opacity: 0 }, svg);
      var hit = sv("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent" }, svg);
      var drag = null;
      function xOf(ev) { var bb = svg.getBoundingClientRect(); return (ev.clientX - bb.left) / bb.width * W; }
      function valAt(px) { return xa + (Math.min(Math.max(px, L), W - R) - L) / (W - L - R) * (xb - xa); }
      function showAt(px) {
        var xv = valAt(px), i = lowerBound(xs, xv);
        if (i > 0 && (i >= n || xv - xs[i - 1] < xs[i] - xv)) i--;
        cur.setAttribute("x1", sx(xs[i])); cur.setAttribute("x2", sx(xs[i])); cur.setAttribute("opacity", 1);
        read.replaceChildren(el("span", { class: "k" }, [X.label + ": ", el("b", { text: num(xs[i], xdig) + " " + X.unit })]));
        sigs.forEach(function (s) {
          read.appendChild(el("span", { class: "k" }, [el("span", { class: "tx-sw", style: "background:" + PALETTE[colorOf[s.key]] + ";border-color:transparent" }),
            s.label + ": ", el("b", { text: num(s.values[i], s.digits == null ? 1 : s.digits) + (s.unit ? " " + s.unit : "") })]));
        });
      }
      hit.addEventListener("mousemove", function (ev) {
        var px = xOf(ev); showAt(px);
        if (drag != null) { var a = Math.min(drag, px), b = Math.max(drag, px); band.setAttribute("x", a); band.setAttribute("width", b - a); band.setAttribute("opacity", 0.18); }
      });
      hit.addEventListener("mousedown", function (ev) { drag = xOf(ev); ev.preventDefault(); });
      function endDrag(ev) {
        if (drag == null) return;
        var px = xOf(ev), a = Math.min(drag, px), b = Math.max(drag, px); drag = null; band.setAttribute("opacity", 0);
        if (b - a > 8) { st.x0 = valAt(a); st.x1 = valAt(b); draw(); }
      }
      hit.addEventListener("mouseup", endDrag);
      hit.addEventListener("mouseleave", function () { cur.setAttribute("opacity", 0); if (drag != null) { drag = null; band.setAttribute("opacity", 0); } });
      hit.addEventListener("dblclick", function () { st.x0 = st.x1 = null; draw(); });
      svgBox.replaceChildren(svg);
      if (!read.childNodes.length) read.replaceChildren(el("span", { class: "tx-hint", text: "İmleci grafiğin üstünde gezdirin: seçili sinyallerin o andaki değerleri burada görünür." }));
    }

    drawBar(); drawList(); draw();
    if (root.ResizeObserver) { var w0 = 0; new ResizeObserver(function () { var w = svgBox.clientWidth; if (w && Math.abs(w - w0) > 4) { w0 = w; draw(); } }).observe(svgBox); }
    return { select: setSel, zoom: function (a, b) { st.x0 = a; st.x1 = b; draw(); }, redraw: draw,
             update: function (d) { data = d; byKey = {}; d.signals.forEach(function (s) { byKey[s.key] = s; }); st.sel = st.sel.filter(function (k) { return byKey[k]; }); if (!st.sel.length) st.sel = (d.selected || []).slice(); drawBar(); drawList(); draw(); } };
  }
  root.TsimExplorer = { mount: mount };
})(typeof window !== "undefined" ? window : this);
