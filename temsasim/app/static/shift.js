/* "Vites": the gearbox's shift logic on the workbench.

   VECTO's AMT EffShift strategy with every set point on the table: the shift
   lines over the engine map, the start gear rule, the efficiency shifts, the
   timers. Change a value and the gear trace of the selected test run is
   re-planned at once (the driveline table is kept on the server, a plan costs
   milliseconds) and laid over the logged gears. "Kalibre et" searches the set
   points for the best agreement; "Kaydet" writes them into the transmission
   file, and from then on every simulation of the vehicle shifts that way. */

const REASON_TR = { launch: "kalkış", engage: "kavrama", emergency: "acil (devir sınırı)", polygon_down: "küçültme hattı", polygon_up: "büyütme hattı",
  eff_down: "verim ↓", eff_up: "verim ↑", brake_down: "fren küçültmesi", kickdown: "talep karşılanmıyor", halt: "duruşa yaklaşma" };
const LEDE = "Şanzımanın ne zaman vites değiştireceğini VECTO'nun AMT EffShift mantığı belirler: önce devir sınırları (acil), sonra vites hatları, sonra ±2 vites içinde en az yakıt yakan vites. Bütün ayar değerleri solda; VECTO'nun jenerik değerleriyle başlar. Bir değeri değiştirdiğinizde seçili test koşusunun vites izi yeniden hesaplanır ve ölçülen vitesle üst üste çizilir. Kalibre et, ölçüme en çok uyan değerleri arar. Kaydet, değerleri şanzıman dosyasına yazar; o araçla yapılan bütün simülasyonlar o andan sonra bu mantıkla vites değiştirir.";

export async function pageShift(U) {
  const { h, api, store, fmt, select, seg, toast, busy, notice, pageHead, css } = U;
  const ov = U.ov;
  const vehicles = ov.vehicles.filter(v => !v.error);
  const veh = vehicles.find(v => v.stem === store.get("vehicle")) || vehicles[0];
  const root = h("div", { class: "shf" });
  if (!veh || !veh.gears) {
    root.append(pageHead("Vites", "Vites mantığı ve şanzıman kalibrasyonu", LEDE),
      notice("warn", "Seçili araçta çok vitesli şanzıman yok.", ["Vites mantığı şanzımanlı (dizel) araçlar içindir. Üstteki araç seçicisinden şanzımanlı bir araç seçin.",
        "Elektrikli araçlarda aktarma sabit oranlıdır; çok vitesli elektrikli şanzıman modeli henüz yok."]));
    return root;
  }
  const enc = encodeURIComponent(veh.stem);
  const info = await api.get(`/api/shift/${enc}`);
  const T = info.transmission;
  let strategy = T.strategy;
  const saved = { strategy, sp: { ...info.setpoints } };
  const sp = { ...info.setpoints };
  let auto = { ...info.auto };
  let polygons = info.polygons, maxStart = info.max_start_gear;
  let campaign = (info.campaigns[0] || {}).name || null;
  let mode = campaign ? "log" : "preview";
  let run = store.get("shiftRun", null);
  let last = null, ex = null, timer = null, seq = 0;
  const dec = step => (step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : 3);

  /* ---------------- head ---------------- */
  const stratSeg = h("div", {});
  const state = h("span", { class: "muted small" });
  const saveBtn = h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, save) }, "Kaydet");
  const calBtn = h("button", { type: "button", onclick: openCalibration }, "Kalibre et");
  root.append(pageHead("Vites", "Vites mantığı ve şanzıman kalibrasyonu", LEDE, [calBtn, saveBtn],
    `${T.name} · ${T.type} ${T.n_gears} vites · aks ${fmt(T.axle_ratio, 2)}`));
  const drawStrat = () => stratSeg.replaceChildren(seg(info.strategies, strategy, v => { strategy = v; drawStrat(); dirty(); layout(); refresh(); }));
  drawStrat();

  /* ---------------- set points ---------------- */
  const inputs = {};
  function clamp(key, v) { const i = info.info[key]; if (Number.isNaN(v)) return info.defaults[key]; return Math.min(i.max, Math.max(i.min, v)); }
  function spRow(key) {
    const inf = info.info[key], def = info.defaults[key], isBool = typeof def === "boolean";
    const inp = isBool ? h("input", { type: "checkbox", checked: sp[key] ? true : null })
      : h("input", { type: "number", class: "numeric", step: inf.step, min: inf.min, max: inf.max, value: sp[key] });
    const vecto = h("td", { class: "n vd" });
    const kind = inf.kind === "temsa" ? h("span", { class: "badge plain", title: "VECTO'da yok; gerçek şanzıman davranışı için eklendi" }, "ek")
      : inf.kind === "adapt" ? h("span", { class: "badge plain", title: "Hız izini geriye doğru çözen modele uyarlama" }, "uyarlama") : null;
    const tr = h("tr", { class: "item" },
      h("td", { class: "name" }, inf.label, " ", h("span", { class: "qm", title: inf.tip + (inf.vecto ? `\nVECTO: ${inf.vecto}` : "") }, "?"), kind ? " " : null, kind),
      h("td", { class: "n" }, inp), h("td", { class: "u" }, inf.unit), vecto);
    const mark = () => {
      tr.classList.toggle("chg", sp[key] !== def);
      vecto.textContent = isBool ? (def ? "açık" : "kapalı") : (auto[key] != null && def === 0 ? `oto ${fmt(auto[key], 0)}` : fmt(def, dec(inf.step)));
      if (!isBool && auto[key] != null) inp.placeholder = `oto ${fmt(auto[key], 0)}`;
    };
    inp.addEventListener("change", () => {
      sp[key] = isBool ? inp.checked : clamp(key, parseFloat(inp.value));
      if (!isBool) inp.value = sp[key];
      mark(); dirty(); refresh();
    });
    inputs[key] = { inp, mark, isBool };
    mark();
    return tr;
  }
  const editor = h("div", { class: "stack" });
  info.groups.forEach((g, gi) => {
    const keys = Object.keys(info.info).filter(k => info.info[k].group === g);
    if (!keys.length) return;
    const body = h("tbody", {}, keys.map(spRow));
    const det = h("details", { class: "panel list shf-grp", open: gi < 3 ? true : null },
      h("summary", { class: "group-head" }, h("h3", {}, g), h("span", { class: "n" }, `${keys.length}`), h("span", { class: "spacer" }), h("span", { class: "muted small shf-nchg" })),
      h("table", { class: "data shf-sp" }, h("thead", {}, h("tr", {}, h("th", {}, "Ayar"), h("th", { class: "n" }, "Değer"), h("th", {}, ""), h("th", { class: "n" }, "VECTO"))), body));
    det.dataset.keys = keys.join(",");
    editor.append(det);
  });
  function setAll(obj) {
    Object.keys(inputs).forEach(k => {
      if (obj[k] === undefined) return;
      sp[k] = obj[k];
      if (inputs[k].isBool) inputs[k].inp.checked = !!obj[k]; else inputs[k].inp.value = obj[k];
      inputs[k].mark();
    });
    dirty(); refresh();
  }
  function dirty() {
    const chg = Object.keys(sp).filter(k => sp[k] !== saved.sp[k]).length + (strategy !== saved.strategy ? 1 : 0);
    const nd = Object.keys(sp).filter(k => sp[k] !== info.defaults[k]).length;
    state.textContent = (chg ? `${chg} kaydedilmemiş değişiklik · ` : "kayıtlı · ") + (nd ? `${nd} ayar VECTO değerinden farklı` : "bütün ayarlar VECTO değerinde");
    saveBtn.disabled = !chg;
    editor.querySelectorAll("details.shf-grp").forEach(d => {
      const n = d.dataset.keys.split(",").filter(k => sp[k] !== info.defaults[k]).length;
      d.querySelector(".shf-nchg").textContent = n ? `${n} değişik` : "";
    });
  }

  /* ---------------- engine map with the shift lines ---------------- */
  const mapBox = h("div", { class: "shf-map" });
  const gearSel = select(polygons.filter(p => p.down.length || p.up.length).map(p => [String(p.gear), `${p.gear}. vites`]), String(Math.min(6, T.n_gears)));
  gearSel.addEventListener("change", drawMap);
  function drawMap() {
    const W = Math.max(380, mapBox.clientWidth || 520), H = 300, L = 50, R = 12, Tp = 10, B = 34;
    const fl = info.engine.full_load, dr = info.engine.drag, c = info.characteristics;
    const x0 = Math.min(500, info.idle_rpm - 80), x1 = fl[fl.length - 1][0];
    const y1 = c.max_torque_nm * 1.14, y0 = Math.min(...dr.map(p => p[1])) * 2.2;
    const sx = v => L + (v - x0) / (x1 - x0) * (W - L - R), sy = v => Tp + (1 - (v - y0) / (y1 - y0)) * (H - Tp - B);
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    const s = (tag, a, parent) => { const e = document.createElementNS(NS, tag); for (const k in a) if (a[k] != null) e.setAttribute(k, a[k]); (parent || svg).appendChild(e); return e; };
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("height", H); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Motor haritası üzerinde vites hatları");
    /* brake specific fuel consumption as shading: darker = more efficient */
    const bs = info.engine.bsfc, flat = bs.g_per_kwh.flat().filter(v => v != null);
    const lo = Math.min(...flat), hi = lo * 1.35;
    const dn = bs.rpm[1] - bs.rpm[0], dt = bs.torque[1] - bs.torque[0];
    bs.torque.forEach((tq, j) => bs.rpm.forEach((n, i) => {
      const v = bs.g_per_kwh[j][i]; if (v == null) return;
      const a = Math.max(0, 1 - (v - lo) / (hi - lo));
      if (a > 0.02) s("rect", { x: sx(n - dn / 2), y: sy(tq + dt / 2), width: sx(n + dn / 2) - sx(n - dn / 2) + 0.5, height: sy(tq - dt / 2) - sy(tq + dt / 2) + 0.5, fill: css("--blue"), opacity: (a * 0.3).toFixed(3) });
    }));
    for (let t = 0; t <= y1; t += 500) { s("line", { x1: L, x2: W - R, y1: sy(t), y2: sy(t), stroke: css("--grid") }); s("text", { x: L - 6, y: sy(t) + 4, "text-anchor": "end", "font-size": 11, fill: css("--muted") }).textContent = fmt(t, 0); }
    for (let n = Math.ceil(x0 / 200) * 200; n <= x1; n += 200) { s("line", { x1: sx(n), x2: sx(n), y1: Tp, y2: H - B, stroke: css("--grid") }); s("text", { x: sx(n), y: H - B + 15, "text-anchor": "middle", "font-size": 11, fill: css("--muted") }).textContent = fmt(n, 0); }
    s("text", { x: (L + W - R) / 2, y: H - 4, "text-anchor": "middle", "font-size": 11.5, fill: css("--ink-2") }).textContent = "motor devri [d/d]";
    s("text", { x: 4, y: Tp + 2, "font-size": 10.5, fill: css("--ink-2"), "dominant-baseline": "hanging" }).textContent = "Nm";
    const path = (pts, col, w, dash) => s("path", { d: pts.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)} ${sy(p[1]).toFixed(1)}`).join(""), fill: "none", stroke: col, "stroke-width": w, "stroke-dasharray": dash || null, "stroke-linejoin": "round" });
    /* where the selected run actually worked */
    if (last && last.trace) {
      const tr = last.trace, g = s("g", { fill: css("--amber"), opacity: 0.4 });
      for (let i = 0; i < tr.time_s.length; i += 3) if (tr.gear_sim[i] > 0 && !tr.clutch_open[i]) s("circle", { cx: sx(tr.rpm_sim[i]).toFixed(1), cy: sy(tr.torque_nm[i]).toFixed(1), r: 1.5 }, g);
    }
    path(fl, css("--ink"), 2);
    path(dr, css("--muted"), 1.5);
    if (strategy === "vecto_effshift") {
      const p = polygons.find(q => String(q.gear) === gearSel.value) || polygons[1];
      if (p && p.down.length) path(p.down.map(d => [d[1], Math.max(Math.min(d[0], y1), y0)]), css("--err"), 2.2);
      if (p && p.up.length) path(p.up.map(d => [d[1], Math.max(Math.min(d[0], y1), y0)]), css("--ok"), 2.2);
      const post = sp.min_engine_speed_post_upshift_rpm;
      if (post > 0) path([[post, y0], [post, y1]], css("--ok"), 1.2, "4 4");
      const bd = sp.brake_downshift_speed_rpm;
      if (bd > 0) path([[bd, y0], [bd, 0]], css("--err"), 1.2, "4 4");
    } else {
      const w = info.window;
      path([[w.downshift_rpm, y0], [w.downshift_rpm, y1]], css("--err"), 2); path([[w.upshift_rpm, y0], [w.upshift_rpm, y1]], css("--ok"), 2);
      path([[w.eco_upshift_rpm, y0], [w.eco_upshift_rpm, y1]], css("--ok"), 1.2, "4 4"); path([[w.lug_rpm, 0], [w.lug_rpm, y1]], css("--err"), 1.2, "4 4");
    }
    s("rect", { x: L, y: Tp, width: W - L - R, height: H - Tp - B, fill: "none", stroke: css("--rule") });
    mapBox.replaceChildren(svg);
  }
  const charLine = h("div", { class: "muted small num" });
  const mapPanel = h("div", { class: "panel" },
    h("div", { class: "row" }, h("div", { class: "section-title", style: "margin:0" }, "Vites hatları"), h("span", { class: "spacer" }), gearSel),
    mapBox,
    h("div", { class: "shf-legend small" },
      h("span", {}, h("i", { style: `background:${css("--err")}` }), "küçültme hattı (solunda küçült)"), h("span", {}, h("i", { style: `background:${css("--ok")}` }), "büyütme hattı (sağında büyüt)"),
      h("span", {}, h("i", { class: "dash", style: `border-color:${css("--ok")}` }), "büyütme sonrası asgari devir"), h("span", {}, h("i", { class: "dash", style: `border-color:${css("--err")}` }), "fren küçültmesi"),
      h("span", {}, h("i", { style: `background:${css("--amber")};opacity:.6` }), "koşunun çalışma noktaları"), h("span", {}, h("i", { style: `background:${css("--blue")};opacity:.35` }), "koyu = düşük özgül tüketim")),
    charLine);

  /* ---------------- score, trace, events ---------------- */
  const kpis = h("div", { class: "cab-kpis shf-kpis" });
  const exBox = h("div", {});
  const evBody = h("tbody", {});
  const evCount = h("span", { class: "n" });
  const evPanel = h("details", { class: "panel list" }, h("summary", { class: "group-head" }, h("h3", {}, "Vites değişimleri (model)"), evCount),
    h("div", { class: "shf-ev" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", { class: "n" }, "t [s]"), h("th", {}, "Vites"), h("th", {}, "Neden"), h("th", { class: "n" }, "d/d"), h("th", { class: "n" }, "km/h"))), evBody)));
  const srcRow = h("div", { class: "row" });
  const fuelBox = h("div", {});
  const note = h("div", {});
  const kp = (k, v, d, cls) => h("div", { class: "cab-kpi" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), d ? h("div", { class: "d" + (cls ? " " + cls : "") }, d) : null);
  function drawScore() {
    kpis.replaceChildren();
    if (!last) return;
    const pct = x => `${fmt(100 * x, 1)} %`;
    if (last.agreement != null) {
      kpis.append(kp("Vites uyumu", pct(last.agreement), "bütün sürüş"), kp("Geçişlerde uyum", pct(last.transient_agreement), "kalkış, hızlanma, fren"),
        kp("±1 vites içinde", pct(last.within_one)), kp("Devir hatası", `${fmt(last.rpm_rmse, 0)} d/d`, "RMSE"),
        kp("Kalkış vitesi", pct(last.launch_agreement), `${last.launches} kalkış · en çok ${maxStart}. vites`),
        kp("Vites değişimi", `${last.shifts_sim}`, `ölçüm ${last.shifts_meas}`, last.shifts_sim > 1.25 * last.shifts_meas ? "bad" : "ok"));
    } else if (last.kpi) {
      const k = last.kpi;
      kpis.append(kp("Tüketim", `${fmt(k.l_per_100km, 1)}`, "l/100 km"), kp("Vites değişimi", `${k.shifts}`, `${fmt(k.distance_km, 1)} km`), kp("Ortalama devir", `${fmt(k.mean_rpm, 0)} d/d`),
        kp("Kalkış vitesi", `en çok ${maxStart}.`), kp("Karşılanamayan talep", `${fmt(k.unmet_s, 0)} s`, null, k.unmet_s > 0 ? "bad" : "ok"));
    }
  }
  function exData(tr) {
    const sig = [], add = (key, label, unit, values, group, extra) => { if (values) sig.push({ key, label, unit, values, group, digits: 0, ...(extra || {}) }); };
    add("speed", "Hız", "km/h", tr.speed_kmh, "Sürüş", { digits: 1 }); add("grade", "Eğim", "%", tr.grade_pct, "Sürüş", { digits: 1 });
    add("gear_meas", "Vites (ölçüm)", "vites", tr.gear_meas, "Vites", { step: true }); add("gear_sim", "Vites (model)", "vites", tr.gear_sim, "Vites", { step: true });
    add("clutch", "Debriyaj açık (model)", "", tr.clutch_open, "Vites", { step: true });
    add("rpm_meas", "Motor devri (ölçüm)", "d/d", tr.rpm_meas, "Motor"); add("rpm_sim", "Motor devri (model)", "d/d", tr.rpm_sim, "Motor");
    add("load", "Motor yükü (model)", "%", tr.load.map(x => x * 100), "Motor"); add("torque", "Şanzıman giriş torku (model)", "Nm", tr.torque_nm, "Motor");
    const presets = tr.gear_meas ? [["Vites", ["speed", "gear_meas", "gear_sim"]], ["Devir", ["rpm_meas", "rpm_sim", "gear_sim"]], ["Yük ve eğim", ["load", "grade", "gear_sim"]]]
      : [["Vites ve devir", ["speed", "gear_sim", "rpm_sim"]], ["Yük ve eğim", ["load", "grade", "gear_sim"]]];
    return { x: [{ key: "t", label: "Zaman", unit: "s", values: tr.time_s }], signals: sig, presets, selected: presets[0][1] };
  }
  function drawTrace() {
    if (!last || !last.trace) { exBox.replaceChildren(); ex = null; evBody.replaceChildren(); return; }
    const d = exData(last.trace);
    if (ex && exBox.firstChild) ex.update(d); else ex = window.TsimExplorer.mount(exBox, d, { height: 340 });
    const evs = (last.trace.events || []).filter(e => e.reason !== "engage");
    evCount.textContent = `${evs.length}` + (last.reasons ? " · " + Object.entries(last.reasons).filter(([k]) => k !== "engage").sort((a, b) => b[1] - a[1]).map(([k, n]) => `${REASON_TR[k] || k} ${n}`).join(" · ") : "");
    evBody.replaceChildren(...evs.slice(0, 600).map(e => h("tr", { class: "item", title: "Bu ana yakınlaştır", onclick: () => { if (ex) ex.zoom(e.t_s - 45, e.t_s + 45); exBox.scrollIntoView({ block: "nearest", behavior: "smooth" }); } },
      h("td", { class: "n" }, fmt(e.t_s, 0)), h("td", { class: "num" }, `${e.from || "–"} → ${e.to}`), h("td", {}, REASON_TR[e.reason] || e.reason), h("td", { class: "n" }, fmt(e.rpm, 0)), h("td", { class: "n" }, fmt(e.speed_kmh, 1)))));
  }

  /* ---------------- where the trace comes from ---------------- */
  const duty = { kind: store.get("shiftDuty", ov.routes.length ? `route:${ov.routes[0]}` : "const:80:0") };
  function drawSource() {
    const kids = [];
    const eff = strategy === "vecto_effshift";
    // the logged gears can only be compared with the EffShift plan; the old rules are shown on a route
    if (info.campaigns.length && eff) kids.push(seg([["log", "Test verisi"], ["preview", "Güzergâh önizleme"]], mode, v => { mode = v; ex = null; exBox.replaceChildren(); fuelBox.replaceChildren(); drawSource(); refresh(); }));
    if (mode === "log" && eff) {
      const c = info.campaigns.find(x => x.name === campaign) || info.campaigns[0];
      const cs = select(info.campaigns.map(x => [x.name, x.campaign]), campaign); cs.addEventListener("change", () => { campaign = cs.value; run = null; drawSource(); refresh(); });
      if (!run || !c.runs.includes(run)) run = c.runs[0];
      const rs = select(c.runs.map(r => [r, r.replaceAll("_", " ")]), run); rs.addEventListener("change", () => { run = rs.value; store.set("shiftRun", run); ex = null; exBox.replaceChildren(); refresh(); });
      kids.push(info.campaigns.length > 1 ? cs : h("span", { class: "small" }, c.campaign), h("span", { class: "muted small" }, "koşu"), rs, h("span", { class: "spacer" }),
        h("button", { type: "button", class: "ghost", title: "Kampanyayı tam çözücüyle koşar: yakıt hatası ve vites uyumu (birkaç saniye)", onclick: e => busy(e.currentTarget, fuel) }, "Yakıt korelasyonu"));
    } else {
      const opts = [...ov.routes.map(r => [`route:${r}`, `Güzergâh · ${r}`]), ...ov.cycles.map(c => [`cycle:${c}`, `Çevrim · ${c}`]),
        ["const:50:0", "Sabit 50 km/h, düz"], ["const:80:0", "Sabit 80 km/h, düz"], ["const:100:0", "Sabit 100 km/h, düz"], ["const:60:4", "Sabit 60 km/h, %4 yokuş"], ["const:40:8", "Sabit 40 km/h, %8 yokuş"]];
      const ds = select(opts, duty.kind); ds.addEventListener("change", () => { duty.kind = ds.value; store.set("shiftDuty", ds.value); ex = null; exBox.replaceChildren(); refresh(); });
      kids.push(ds, h("span", { class: "muted small" }, !eff ? "Ölçümle karşılaştırma ve kalibrasyon VECTO EffShift içindir; eski kurallar güzergâh üzerinde gösterilir."
        : info.campaigns.length ? "" : "Bu araç için vitesli test kaydı yok: kalibrasyon yapılamaz, ayarların etkisi güzergâh üzerinde görülür."));
    }
    srcRow.replaceChildren(...kids);
    calBtn.disabled = !(mode === "log" && strategy === "vecto_effshift");
  }
  function dutyBody() {
    const [k, a, b] = duty.kind.split(":");
    const pax = Math.round((veh.capacity || 40) * 0.5);
    if (k === "route") return { route: a, traffic: "orta", passengers: pax, hvac: false };
    if (k === "cycle") return { cycle: a, passengers: pax, hvac: false };
    return { constant: { speed_kmh: +a, grade_pct: +b, distance_km: 4 }, passengers: pax, hvac: false };
  }

  /* ---------------- talking to the server ---------------- */
  async function compute() {
    const my = ++seq;
    note.replaceChildren();
    try {
      const onLog = mode === "log" && strategy === "vecto_effshift";
      const r = onLog ? await api.post(`/api/shift/${enc}/evaluate`, { strategy, setpoints: sp, campaign, run })
        : await api.post(`/api/shift/${enc}/preview`, { strategy, setpoints: sp, ...dutyBody() });
      if (my !== seq) return;
      last = r; polygons = r.polygons; maxStart = r.max_start_gear; auto = r.auto || auto;
      Object.values(inputs).forEach(i => i.mark());
      drawScore(); drawMap(); drawTrace();
    } catch (e) { if (my === seq) note.replaceChildren(notice("err", "Hesaplanamadı", [e.message])); }
  }
  function refresh() { clearTimeout(timer); timer = setTimeout(compute, 180); }
  async function fuel() {
    const r = await api.post(`/api/shift/${enc}/evaluate`, { strategy, setpoints: sp, campaign, run, fuel: true });
    const f = r.fuel, pc = x => (x == null ? "—" : `${x > 0 ? "+" : ""}${fmt(100 * x, 1)} %`);
    fuelBox.replaceChildren(h("div", { class: "panel list" }, h("div", { class: "group-head" }, h("h3", {}, "Yakıt korelasyonu (tam çözücü)"), h("span", { class: "n" }, `CAN ${pc(f.err_can)} · cihaz ${pc(f.err_device)} · vites uyumu ${fmt(100 * f.gear_agreement, 1)} %`)),
      h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Koşu"), h("th", { class: "n" }, "Model l/100"), h("th", { class: "n" }, "CAN l/100"), h("th", { class: "n" }, "CAN farkı"), h("th", { class: "n" }, "Cihaz farkı"), h("th", { class: "n" }, "Vites uyumu"), h("th", { class: "n" }, "Seyir devri ölçüm / model"))),
        h("tbody", {}, f.rows.map(x => h("tr", {}, h("td", {}, x.key.replaceAll("_", " ")), h("td", { class: "n" }, fmt(x.l100_sim, 2)), h("td", { class: "n" }, fmt(x.l100_can, 2)), h("td", { class: "n" }, pc(x.err_can)), h("td", { class: "n" }, pc(x.err_device)),
          h("td", { class: "n" }, `${fmt(100 * x.gear_agreement, 1)} %`), h("td", { class: "n" }, `${fmt(x.rpm_cruise_meas, 0)} / ${fmt(x.rpm_cruise_sim, 0)}`)))))));
  }
  async function save() {
    const noteTxt = last && last.agreement != null ? `${campaign} loglarına göre; vites uyumu %${fmt(100 * last.agreement, 1)}` : "";
    const r = await api.post(`/api/shift/${enc}/save`, { strategy, setpoints: sp, note: noteTxt });
    saved.strategy = strategy; saved.sp = { ...sp };
    await U.overview(true);
    dirty();
    toast(`Kaydedildi: ${r.saved} · ${r.vehicles.length} araç bu şanzımanı kullanıyor`);
  }

  /* ---------------- calibration dialog ---------------- */
  function openCalibration() {
    const dlg = h("dialog", { class: "shf-dlg" });
    const close = () => { dlg.close(); dlg.remove(); };
    let budget = 40;
    const params = new Set(info.calibration_params);
    const bSeg = h("div", {});
    const drawB = () => bSeg.replaceChildren(seg([[15, "Hızlı · 15 s"], [40, "Normal · 40 s"], [90, "Ayrıntılı · 90 s"]], budget, v => { budget = +v; drawB(); }));
    drawB();
    const plist = h("div", { class: "shf-plist" }, Object.keys(info.info).filter(k => typeof info.defaults[k] !== "boolean").map(k => {
      const cb = h("input", { type: "checkbox", checked: params.has(k) ? true : null });
      cb.addEventListener("change", () => { if (cb.checked) params.add(k); else params.delete(k); });
      return h("label", {}, cb, info.info[k].label);
    }));
    const out = h("div", { class: "stack" });
    const nRuns = (info.campaigns.find(c => c.name === campaign) || { runs: [] }).runs.length;
    const go = h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, async () => {
      out.replaceChildren(h("p", { class: "muted" }, `Aranıyor… en çok ${budget} s. Her denemede ${nRuns} koşunun vites izi yeniden kuruluyor.`));
      const r = await api.post(`/api/shift/${enc}/calibrate`, { strategy, setpoints: sp, campaign, budget_s: budget, params: [...params] });
      const pct = x => `${fmt(100 * x, 1)} %`;
      const row = (k, a, b) => h("tr", {}, h("td", {}, k), h("td", { class: "n" }, a), h("td", { class: "n" }, b));
      const chg = Object.keys(r.setpoints).filter(k => r.setpoints[k] !== sp[k]);
      const weak = Object.entries(r.sensitivity).filter(([, v]) => v < 0.002).map(([k]) => info.info[k].label);
      out.replaceChildren(
        h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", { class: "n" }, "Önce"), h("th", { class: "n" }, "Sonra"))), h("tbody", {},
          row("Vites uyumu", pct(r.before.agreement), pct(r.after.agreement)), row("Geçişlerde uyum", pct(r.before.transient_agreement), pct(r.after.transient_agreement)),
          row("±1 vites içinde", pct(r.before.within_one), pct(r.after.within_one)), row("Devir hatası (RMSE)", `${fmt(r.before.rpm_rmse, 0)} d/d`, `${fmt(r.after.rpm_rmse, 0)} d/d`),
          row("Kalkış vitesi", pct(r.before.launch_agreement), pct(r.after.launch_agreement)), row(`Vites değişimi (ölçüm ${r.after.shifts_meas})`, `${r.before.shifts_sim}`, `${r.after.shifts_sim}`))),
        h("p", { class: "small muted", style: "margin:0" }, `${r.evaluations} deneme, ${fmt(r.seconds, 0)} s; ${r.stopped === "budget" ? "süre doldu (daha uzun arama biraz daha iyileştirebilir)" : "yakınsadı"}.`),
        chg.length ? h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Değişen ayar"), h("th", { class: "n" }, "Şimdi"), h("th", { class: "n" }, "Öneri"), h("th", { class: "n", title: "Bu ayar aralığı boyunca oynatıldığında hedefin ne kadar değiştiği (yüzde puan)" }, "Etki"))), h("tbody", {},
          chg.map(k => h("tr", {}, h("td", {}, info.info[k].label), h("td", { class: "n" }, fmt(sp[k], dec(info.info[k].step))), h("td", { class: "n" }, h("b", {}, fmt(r.setpoints[k], dec(info.info[k].step))), " ", info.info[k].unit),
            h("td", { class: "n" }, r.sensitivity[k] != null ? fmt(100 * r.sensitivity[k], 1) : "—")))))
          : notice("ok", "Bu ayarlar zaten en iyi uyumu veriyor.", []),
        weak.length ? h("p", { class: "small muted", style: "margin:0" }, `Bu loglarla belirlenemeyen (etkisi ölçülemeyen) ayarlar yerinde bırakıldı: ${weak.join(", ")}.`) : null,
        h("div", { class: "row" }, chg.length ? h("button", { class: "primary", type: "button", onclick: () => { setAll(r.setpoints); close(); toast("Öneri uygulandı; kalıcı olması için Kaydet'e basın."); } }, "Uygula") : null,
          h("button", { type: "button", class: "ghost", onclick: close }, "Kapat")));
    }) }, "Aramayı başlat");
    dlg.append(h("div", { class: "dlg-head" }, h("div", {}, h("h2", {}, "Şanzıman kalibrasyonu"), h("div", { class: "muted small" }, `${T.name} · ${campaign}`)), h("span", { class: "spacer" }), h("button", { type: "button", class: "ghost", onclick: close }, "Kapat")),
      h("div", { class: "dlg-body stack" },
        h("p", { class: "small", style: "margin:0" }, "Arama, şu anki değerlerden başlar ve işaretli ayarları tek tek oynatarak ölçülen vites ve devir izine en çok uyan birleşimi bulur. Sonuç önce burada gösterilir; siz uygulamadan hiçbir şey değişmez."),
        h("div", { class: "row" }, h("span", { class: "muted small" }, "Arama süresi"), bSeg, h("span", { class: "spacer" }), go),
        h("details", {}, h("summary", { class: "small" }, "Aranacak ayarlar"), plist),
        out));
    document.body.append(dlg);
    dlg.showModal();
  }

  /* ---------------- layout ---------------- */
  const left = h("div", { class: "stack" });
  const right = h("div", { class: "stack" });
  const windowNote = h("div", {});
  function layout() {
    const eff = strategy === "vecto_effshift";
    editor.hidden = !eff;
    const w = info.window;
    windowNote.replaceChildren(...(eff ? [] : [notice("warn", "Eski devir penceresi kuralları", [
      `Küçültme ${fmt(w.downshift_rpm, 0)} d/d · büyütme ${fmt(w.upshift_rpm, 0)} d/d · kısmi yükte erken büyütme ${fmt(w.eco_upshift_rpm, 0)} d/d · yük altında küçültme ${fmt(w.lug_rpm, 0)} d/d · kalkış ${w.launch_gear}. vites (en çok ${w.launch_gear_max}.).`,
      "Bu değerler şanzıman bileşeninin kendi sayfasından düzenlenir. VECTO EffShift'e geçmek için yukarıdan seçin."])]));
    drawSource();
  }
  left.append(h("div", { class: "panel" }, h("div", { class: "row" }, h("div", { class: "section-title", style: "margin:0" }, "Vites stratejisi"), stratSeg, h("span", { class: "spacer" })),
    h("div", { class: "row", style: "margin-top:10px" }, state, h("span", { class: "spacer" }),
      h("button", { type: "button", class: "ghost", onclick: () => setAll(saved.sp) }, "Kayıtlıya dön"), h("button", { type: "button", class: "ghost", onclick: () => setAll(info.defaults) }, "VECTO değerleri"))),
    windowNote, editor);
  right.append(h("div", { class: "panel stack" }, srcRow, note, kpis, exBox), fuelBox, mapPanel, evPanel);
  root.append(h("div", { class: "shf-grid" }, left, right));
  const c = info.characteristics;
  charLine.textContent = `rölanti ${fmt(info.idle_rpm, 0)} · n(T99↓) ${fmt(c.n_tq99l_rpm, 0)} · n(T98↑) ${fmt(c.n_tq98h_rpm, 0)} · anma ${fmt(c.rated_speed_rpm, 0)} · n(P98↑) ${fmt(c.n_p98h_rpm, 0)} · n95h ${fmt(c.n_95h_rpm, 0)} d/d · azami ${fmt(c.max_torque_nm, 0)} Nm / ${fmt(c.max_power_kw, 0)} kW`;
  layout(); dirty();
  setTimeout(() => { drawMap(); compute(); }, 0);
  return root;
}
