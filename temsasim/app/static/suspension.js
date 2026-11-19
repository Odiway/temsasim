/* "Süspansiyon": ride, dive and squat, body roll and rollover on the workbench.

   One seven-degree-of-freedom model of the selected vehicle (body heave,
   pitch and roll on four wheel stations) with the air springs at the pressure
   the load asks for and the dampers' measured force-velocity curves. Left:
   the load, the centre of gravity and the chassis data. Right: what follows
   from them. Move a value, a slider or a damper node and everything is
   recomputed; "Kaydet" writes the file the vehicle uses from then on. */

const LEDE = "Seçili aracın gövdesi dört tekerlek istasyonu üzerinde zıplar, yunuslar ve yalpalar; aradaki körükler yükün istediği basınçta, amortisörler ölçülmüş kuvvet–hız eğrileriyle çalışır. Solda yük, ağırlık merkezi ve şasi verisi; sağda bunlardan çıkan sonuçlar: sürüş frekansı ve sönüm, frende dalma ve kalkışta çökme açısı, sönüm süresi, yalpa açısı, devrilme eşiği ve bir virajda devrilme hızı. Bir değeri, bir sürgüyü ya da amortisör grafiğindeki bir noktayı değiştirdiğinizde hepsi yeniden hesaplanır. Grafikte sürükleyerek ya da Aralık kutularına zaman yazarak yalnız o kısmı inceleyebilirsiniz. Formüller Gillespie (Fundamentals of Vehicle Dynamics), ISO 8608 ve ISO 2631'dendir; “kabul” yazan veriler ölçüm değil varsayımdır.";

export async function pageSuspension(U) {
  const { h, api, store, fmt, select, seg, toast, busy, notice, pageHead, css } = U;
  const vehicles = U.ov.vehicles.filter(v => !v.error);
  const veh = vehicles.find(v => v.stem === store.get("vehicle")) || vehicles[0];
  const root = h("div", { class: "shf sus" });
  if (!veh) { root.append(pageHead("Süspansiyon", "Süspansiyon", LEDE), notice("warn", "Kütüphanede araç yok.", [])); return root; }
  const enc = encodeURIComponent(veh.stem);
  const fileKey = `susFile:${veh.stem}`;
  let info;
  try { info = await api.get(`/api/suspension/${enc}` + (store.get(fileKey) ? `?file=${encodeURIComponent(store.get(fileKey))}` : "")); }
  catch (e) { if (!store.get(fileKey)) throw e; store.set(fileKey, null); info = await api.get(`/api/suspension/${enc}`); }
  const model = JSON.parse(JSON.stringify(info.model));
  let savedText = JSON.stringify(model);
  const V = info.vehicle;
  const load = { passengers: store.get(`susPax:${veh.stem}`, V.seats), luggage_kg: store.get(`susLug:${veh.stem}`, 0) };
  const man = { kind: store.get("susMan", "fren"), params: {} };
  const opt = { radius_m: store.get("susRadius", 100), rise_s: 0.5, comfort_speed_kmh: 80 };
  let base = null, sim = null, roll = null, comf = null, ex = null, exKind = null, timer = null, seq = 0, dmpAxle = "front";
  const get = path => path.split(".").reduce((o, k) => o[k], model);
  const set = (path, v) => { const p = path.split("."); p.slice(0, -1).reduce((o, k) => o[k], model)[p[p.length - 1]] = v; };
  const dec = step => (step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : 3);
  const kp = (k, v, d, cls) => h("div", { class: "cab-kpi" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), d ? h("div", { class: "d" + (cls ? " " + cls : "") }, d) : null);
  const NS = "http://www.w3.org/2000/svg";
  const svgEl = (W, H, label) => { const s = document.createElementNS(NS, "svg"); s.setAttribute("viewBox", `0 0 ${W} ${H}`); s.setAttribute("height", H); s.setAttribute("role", "img"); s.setAttribute("aria-label", label); return s; };
  const sv = (svg, tag, a, text) => { const e = document.createElementNS(NS, tag); for (const k in a) if (a[k] != null) e.setAttribute(k, a[k]); if (text != null) e.textContent = text; svg.appendChild(e); return e; };

  /* ---------------- head ---------------- */
  const state = h("span", { class: "muted small" });
  const saveBtn = h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, save) }, "Kaydet");
  const fileSel = info.files.length > 1 ? select(info.files.map(f => [f.stem, f.name]), info.stem) : null;
  if (fileSel) { fileSel.title = "Süspansiyon dosyası"; fileSel.addEventListener("change", () => { store.set(fileKey, fileSel.value); U.route(); }); }
  root.append(pageHead("Süspansiyon", "Süspansiyon", LEDE, [fileSel, saveBtn], `${model.name} · ölçüm: ${model.measured.length ? "körük ve amortisör" : "yok"} · geri kalanı kabul`));
  const top = h("div", {});
  if (!info.linked) top.append(notice("warn", "Bu araca bağlı süspansiyon dosyası yok; gösterilen dosya ödünç alındı.",
    [`“${model.name}” kullanılıyor. Kütle ve yolcu sayısı seçili araçtan, geri kalan her şey bu dosyadan gelir.`, "Değerleri araca göre düzeltip Kaydet'e basınca araca ait yeni bir dosya oluşur."]));
  root.append(top);

  /* ---------------- left: load, centre of gravity, chassis data ---------------- */
  const loadLine = h("div", { class: "muted small num sus-loadline" });
  function numRow(label, value, { min, max, step, unit, tip, slider, onInput }) {
    const inp = h("input", { type: "number", class: "numeric", step, min, max, value });
    const rng = slider ? h("input", { type: "range", class: "sus-rng", step, min, max, value, "aria-label": label }) : null;
    const apply = (v, from) => {
      if (Number.isNaN(v)) return;
      v = Math.min(max, Math.max(min, v));
      if (from !== inp) inp.value = +v.toFixed(dec(step)); if (rng && from !== rng) rng.value = v;
      onInput(v);
    };
    inp.addEventListener("change", () => { apply(parseFloat(inp.value), null); inp.value = +Math.min(max, Math.max(min, parseFloat(inp.value) || 0)).toFixed(dec(step)); });
    if (rng) rng.addEventListener("input", () => apply(parseFloat(rng.value), rng));
    const tr = h("tr", { class: "item" }, h("td", { class: "name" }, label, tip ? " " : null, tip ? h("span", { class: "qm", title: tip }, "?") : null),
      h("td", { class: "n" }, inp), h("td", { class: "u" }, unit || ""), h("td", { class: "sl" }, rng));
    return { tr, inp, rng };
  }
  const loadBody = h("tbody", {},
    numRow("Yolcu", load.passengers, { min: 0, max: Math.max(V.capacity, V.seats), step: 1, unit: "kişi", slider: true, tip: `Yolcu başına ${fmt(V.passenger_mass_kg, 0)} kg (şasi dosyası).`,
      onInput: v => { load.passengers = Math.round(v); store.set(`susPax:${veh.stem}`, load.passengers); refresh(); } }).tr,
    numRow("Bagaj", load.luggage_kg, { min: 0, max: 3000, step: 50, unit: "kg", slider: true, onInput: v => { load.luggage_kg = v; store.set(`susLug:${veh.stem}`, v); refresh(); } }).tr);
  const loadPanel = h("div", { class: "panel list" }, h("div", { class: "group-head" }, h("h3", {}, "Yük")), h("table", { class: "data shf-sp sus-sp" }, loadBody), loadLine);

  const rows = {};
  const editor = h("div", { class: "stack" }, loadPanel);
  info.groups.forEach((g, gi) => {
    const ps = info.params.filter(p => p.group === g);
    if (!ps.length) return;
    const body = h("tbody", {}, ps.map(p => {
      const r = numRow(p.label, +(get(p.path) * p.scale).toFixed(dec(p.step)), { min: p.min, max: p.max, step: p.step, unit: p.unit, tip: p.tip, slider: gi === 0,
        onInput: v => { set(p.path, p.integer ? Math.round(v) : v / p.scale); mark(); refresh(); } });
      rows[p.path] = { ...r, p };
      return r.tr;
    }));
    const det = h("details", { class: "panel list shf-grp", open: gi === 0 ? true : null },
      h("summary", { class: "group-head" }, h("h3", {}, g), h("span", { class: "n" }, `${ps.length}`), h("span", { class: "spacer" }), h("span", { class: "muted small shf-nchg" })),
      h("table", { class: "data shf-sp sus-sp" }, body));
    det.dataset.paths = ps.map(p => p.path).join(",");
    editor.append(det);
  });
  const orig = JSON.parse(savedText);
  const origGet = path => path.split(".").reduce((o, k) => o[k], orig);
  function mark() {
    Object.values(rows).forEach(r => r.tr.classList.toggle("chg", Math.abs(get(r.p.path) - origGet(r.p.path)) > 1e-9));
    editor.querySelectorAll("details.shf-grp").forEach(d => {
      const n = d.dataset.paths.split(",").filter(p => Math.abs(get(p) - origGet(p)) > 1e-9).length;
      d.querySelector(".shf-nchg").textContent = n ? `${n} değişik` : "kabul";
    });
    const dirty = JSON.stringify(model) !== savedText;
    state.textContent = dirty ? "kaydedilmemiş değişiklik var" : "kayıtlı";
    saveBtn.disabled = !dirty && info.linked;
  }

  /* ---------------- right: headline numbers ---------------- */
  const kpis = h("div", { class: "cab-kpis sus-kpis" });
  const warn = h("div", {});
  function drawKpis() {
    if (!base) return;
    const s = base.summary, r = s.roll, p = s.pitch, G = 9.80665;
    const brake = 4.0, acc = 1.2;
    const tiles = [
      kp("Sürüş frekansı · ön", `${fmt(s.ride_hz[0], 2)} Hz`, `sönüm oranı ${fmt(s.damping_ratio[0], 2)}`),
      kp("Sürüş frekansı · arka", `${fmt(s.ride_hz[1], 2)} Hz`, `sönüm oranı ${fmt(s.damping_ratio[1], 2)}`),
      kp("Dalma (fren)", `${fmt(p.dive_deg_per_g, 2)} °/g`, `${fmt(brake, 0)} m/s² frende ${fmt(p.dive_deg_per_g * brake / G, 2)}°`),
      kp("Çökme (kalkış)", `${fmt(p.squat_deg_per_g, 2)} °/g`, `${fmt(acc, 1)} m/s² ivmede ${fmt(p.squat_deg_per_g * acc / G, 2)}°`),
      kp("Yalpa", `${fmt(r.gradient_deg_per_g, 1)} °/g`, `0,4 g'de ${fmt(r.gradient_deg_per_g * 0.4, 1)}° · ${fmt(r.hz, 2)} Hz`),
    ];
    if (roll) {
      const a = roll.at_radius;
      tiles.push(kp("Devrilme eşiği", `${fmt(roll.static_g, 2)} g`, `ani manevrada ${fmt(roll.dynamic_g, 2)} g`),
        kp("Eğme açısı", `${fmt(roll.tilt_deg, 1)}°`, `R107 şartı ${fmt(roll.tilt_required_deg, 0)}°`, roll.tilt_deg >= roll.tilt_required_deg ? "ok" : "bad"),
        kp(`Devrilme hızı · R ${fmt(a.radius_m, 0)} m`, `${fmt(a.static_kmh, 0)} km/h`, `ani manevrada ${fmt(a.dynamic_kmh, 0)} km/h`));
    } else tiles.push(kp("Devrilme eşiği", "…"), kp("Eğme açısı", "…"), kp("Devrilme hızı", "…"));
    kpis.replaceChildren(...tiles);
    loadLine.textContent = `${fmt(s.mass_kg, 0)} kg · ön aks ${fmt(s.axle_load_kg[0], 0)} kg (%${fmt(100 * s.axle_share[0], 0)}) · arka aks ${fmt(s.axle_load_kg[1], 0)} kg (%${fmt(100 * s.axle_share[1], 0)}) · yüklü ağırlık merkezi X ${fmt(s.cog.x_m, 2)} m, Z ${fmt(s.cog.z_m, 2)} m`;
    const items = [];
    if (s.over_gross) items.push(`Toplam kütle ${fmt(s.mass_kg, 0)} kg, azami yüklü kütleyi (${fmt(s.gross_mass_kg, 0)} kg) aşıyor.`);
    s.springs.forEach((sp, i) => {
      if (sp.over_pressure) items.push(`${i ? "Arka" : "Ön"} körük ${fmt(Math.max(...sp.pressure_bar), 1)} bar istiyor; çalışma sınırı ${fmt(sp.max_pressure_bar, 1)} bar.`);
      if (sp.under_pressure) items.push(`${i ? "Arka" : "Ön"} körük basıncı 1 bar'ın altında; çizimdeki en düşük çalışma basıncı 1 bar.`);
    });
    warn.replaceChildren(items.length ? notice("warn", "Yük sınırları", items) : "");
  }

  /* ---------------- manoeuvre ---------------- */
  const manSeg = h("div", {}), manPar = h("div", { class: "row sus-par" }), exBox = h("div", {}), manKpis = h("div", { class: "cab-kpis sus-kpis" }), manNote = h("div", {});
  function drawManHead() {
    manSeg.replaceChildren(seg(info.manoeuvre_order.map(k => [k, info.manoeuvres[k].label]), man.kind, v => { man.kind = v; man.params = store.get(`susPar:${v}`, {}); store.set("susMan", v); drawManHead(); refresh(); }));
    const M = info.manoeuvres[man.kind];
    manPar.replaceChildren(...M.params.map(p => {
      const cur = man.params[p.key] != null ? man.params[p.key] : p.default;
      let ctl;
      if (M.choices[p.key]) { ctl = select(M.choices[p.key], cur); ctl.addEventListener("change", () => { man.params[p.key] = ctl.value; store.set(`susPar:${man.kind}`, man.params); refresh(); }); }
      else {
        ctl = h("input", { type: "number", class: "numeric", step: p.step, min: p.min, max: p.max, value: cur });
        ctl.addEventListener("change", () => { const v = Math.min(p.max, Math.max(p.min, parseFloat(ctl.value))); if (Number.isNaN(v)) return; ctl.value = v; man.params[p.key] = v; store.set(`susPar:${man.kind}`, man.params); refresh(); });
      }
      return h("label", { class: "sus-f" }, h("span", {}, p.label), ctl, p.unit ? h("em", {}, p.unit) : null);
    }), h("span", { class: "spacer" }), h("span", { class: "muted small" }, M.tip));
  }
  man.params = store.get(`susPar:${man.kind}`, {});
  function drawSim() {
    if (!sim) return;
    if (ex && exKind === sim.kind && exBox.firstChild) ex.update(sim.explorer);
    else { ex = window.TsimExplorer.mount(exBox, sim.explorer, { height: 330 }); exKind = sim.kind; }
    const k = sim.kpis, a = k.after || {}, t = [];
    const sec = v => (v == null ? "sürüyor" : `${fmt(v, 2)} s`), hz = v => (v == null ? "—" : `${fmt(v, 2)} Hz`);
    const stroke = () => kp("En büyük strok", `${fmt(Math.max(...k.max_compression_mm), 0)} mm basma`, `${fmt(Math.max(...k.max_rebound_mm), 0)} mm açılma` + (k.bump_stop.some(Boolean) || k.rebound_stop.some(Boolean) ? " · tampona değdi" : ""), k.bump_stop.some(Boolean) || k.rebound_stop.some(Boolean) ? "bad" : null);
    if (sim.kind === "fren" || sim.kind === "kalkis") {
      const dive = sim.kind === "fren";
      t.push(kp(dive ? "En büyük dalma" : "En büyük çökme", `${fmt(dive ? k.peak_dive_deg : k.peak_squat_deg, 2)}°`, `kalıcı ${fmt(Math.abs(k.steady_pitch_deg || 0), 2)}°`),
        kp(dive ? "Durunca sönüm süresi" : "Bırakınca sönüm süresi", sec(a.settle_s), "sapmanın %5'ine inene kadar"), kp("Salınım frekansı", hz(a.hz), a.overshoot != null ? `geri yaylanma %${fmt(100 * a.overshoot, 0)}` : null), stroke());
    } else if (sim.kind === "birakma") {
      t.push(kp("Salınım frekansı", hz(a.hz)), kp("Sönüm süresi", sec(a.settle_s), "sapmanın %5'ine inene kadar"), kp("İlk aşma", a.overshoot != null ? `%${fmt(100 * a.overshoot, 0)}` : "—", "başlangıç sapmasına göre"), stroke());
    } else if (sim.kind === "kasis" || sim.kind === "yol") {
      t.push(kp("Düşey ivme · ön / orta / arka", `${fmt(k.acc_rms_mps2.front, 2)} / ${fmt(k.acc_rms_mps2.cog, 2)} / ${fmt(k.acc_rms_mps2.rear, 2)}`, "m/s² RMS"),
        kp("En büyük düşey ivme", `${fmt(Math.max(k.acc_peak_mps2.front, k.acc_peak_mps2.rear), 1)} m/s²`, "aks üstünde"),
        sim.kind === "kasis" ? kp("Sönüm süresi", sec(a.settle_s), `salınım ${hz(a.hz)}`) : kp("Amortisör hızı", `${fmt(Math.max(...k.peak_damper_speed_mps), 2)} m/s`, "en büyük"), stroke());
    } else {
      t.push(kp("En büyük yalpa", `${fmt(k.peak_roll_deg, 1)}°`, k.steady_roll_deg != null && sim.kind === "direksiyon" ? `kalıcı ${fmt(Math.abs(k.steady_roll_deg), 1)}°` : null),
        kp("Yük aktarım oranı", fmt(k.ltr_max, 2), "1 = iç tekerlekler kalktı", k.ltr_max >= 0.999 ? "bad" : null),
        kp("İç tekerlekler", k.wheel_lift ? `${fmt(k.wheel_lift_s, 1)}. s'de kalktı` : "yerde", k.wheel_lift ? "araç devriliyor" : `en küçük tekerlek yükü ${fmt(k.min_wheel_load_kg, 0)} kg`, k.wheel_lift ? "bad" : "ok"),
        sim.kind === "serit" ? kp("Bu manevrada devrilme hızı", roll && roll.manoeuvre_lift ? (roll.manoeuvre_lift.speed_kmh ? `${fmt(roll.manoeuvre_lift.speed_kmh, 0)} km/h` : `> ${fmt(roll.manoeuvre_lift.above_kmh, 0)} km/h`) : "…", "iç tekerleklerin kalktığı hız")
          : kp("Sönüm süresi", sec(a.settle_s), `salınım ${hz(a.hz)}`));
    }
    manKpis.replaceChildren(...t);
    manNote.replaceChildren(k.wheel_lift ? notice("err", "Bu manevrada iki iç tekerlek de yerden kalkıyor.", ["Model bu andan sonrasını tarif etmez: araç devrilmeye başlamıştır. Grafik o noktada kesilir."]) : "");
  }
  const manPanel = h("div", { class: "panel" }, h("div", { class: "row" }, h("div", { class: "section-title", style: "margin:0" }, "Manevra"), manSeg), manPar, manNote, exBox, manKpis);

  /* ---------------- damper editor ---------------- */
  const dmpBox = h("div", { class: "sus-dmp" }), dmpTbl = h("div", {}), dmpHead = h("div", { class: "row" });
  const refOf = ax => model.dampers[model[ax].damper];
  const curOf = ax => { const r = refOf(ax), t = model[ax].damper_tune || {}; return { v: r.speeds_mps, reb: (t.rebound_n || r.rebound_n).slice(), comp: (t.compression_n || r.compression_n).slice() }; };
  function setNode(ax, which, i, val) {
    const c = curOf(ax);
    c[which][i] = Math.max(0, Math.round(val));
    model[ax].damper_tune = { rebound_n: c.reb, compression_n: c.comp };
  }
  function drawDamper(live) {
    const ax = dmpAxle, ref = refOf(ax), c = curOf(ax), tuned = !!model[ax].damper_tune;
    const W = Math.max(420, dmpBox.clientWidth || 640), H = 300, L = 56, R = 14, T = 12, B = 34;
    const vmax = Math.max(...c.v) * 1.08, fTop = Math.max(...c.reb, ...ref.rebound_n) * 1.15, fBot = Math.max(...c.comp, ...ref.compression_n, fTop * 0.25) * 1.15;
    const sx = v => L + v / vmax * (W - L - R), sy = f => T + (fTop - f) / (fTop + fBot) * (H - T - B), fy = y => fTop - (y - T) / (H - T - B) * (fTop + fBot);
    const svg = svgEl(W, H, "Amortisör kuvvet–hız grafiği");
    const stepF = fTop + fBot > 12000 ? 2000 : fTop + fBot > 5000 ? 1000 : 500;
    for (let f = -Math.floor(fBot / stepF) * stepF; f <= fTop; f += stepF) { sv(svg, "line", { x1: L, x2: W - R, y1: sy(f), y2: sy(f), stroke: f === 0 ? css("--axis") : css("--grid") }); sv(svg, "text", { x: L - 6, y: sy(f) + 4, "text-anchor": "end", "font-size": 11, fill: css("--muted") }, fmt(Math.abs(f), 0)); }
    for (let v = 0; v <= vmax + 1e-9; v += 0.1) { sv(svg, "line", { x1: sx(v), x2: sx(v), y1: T, y2: H - B, stroke: css("--grid") }); sv(svg, "text", { x: sx(v), y: H - B + 15, "text-anchor": "middle", "font-size": 11, fill: css("--muted") }, fmt(v, 1)); }
    sv(svg, "text", { x: (L + W - R) / 2, y: H - 4, "text-anchor": "middle", "font-size": 11.5, fill: css("--ink-2") }, "piston hızı [m/s]");
    sv(svg, "text", { x: L + 6, y: T + 12, "font-size": 10.5, fill: css("--ink-2") }, "çekme (açılma) kuvveti [N] ↑");
    sv(svg, "text", { x: L + 6, y: H - B - 6, "font-size": 10.5, fill: css("--ink-2") }, "basma (kapanma) kuvveti [N] ↓");
    const line = (ys, sign, col, w, dash) => sv(svg, "path", { d: [[0, 0], ...c.v.map((v, i) => [v, sign * ys[i]])].map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)} ${sy(p[1]).toFixed(1)}`).join(""), fill: "none", stroke: col, "stroke-width": w, "stroke-dasharray": dash || null });
    if (tuned) { line(ref.rebound_n, 1, css("--muted"), 1.3, "5 4"); line(ref.compression_n, -1, css("--muted"), 1.3, "5 4"); }
    line(c.reb, 1, css("--blue"), 2.2); line(c.comp, -1, css("--ink"), 2.2);
    if (base && sim) {
      /* the stroke speeds the selected manoeuvre actually reaches */
      const pk = sim.kpis.peak_damper_speed_mps[ax === "front" ? 0 : 1];
      if (pk > 0.005) sv(svg, "line", { x1: sx(Math.min(pk, vmax)), x2: sx(Math.min(pk, vmax)), y1: T, y2: H - B, stroke: css("--amber"), "stroke-width": 1.5, "stroke-dasharray": "3 3" });
    }
    [["reb", 1, css("--blue")], ["comp", -1, css("--ink")]].forEach(([which, sign, col]) => c.v.forEach((v, i) => {
      const node = sv(svg, "circle", { cx: sx(v), cy: sy(sign * c[which][i]), r: 5.5, fill: css("--panel"), stroke: col, "stroke-width": 2, class: "sus-node", tabindex: 0 });
      sv(node, "title", {}, `${fmt(v, 2)} m/s · ${which === "reb" ? "çekme" : "basma"} ${fmt(c[which][i], 0)} N (sürükleyin)`);
      node.addEventListener("pointerdown", ev => {
        ev.preventDefault(); node.setPointerCapture(ev.pointerId);
        const move = e2 => { const bb = svg.getBoundingClientRect(); const y = (e2.clientY - bb.top) / bb.height * H; setNode(ax, which, i, sign * fy(Math.min(Math.max(y, T), H - B))); const cc = curOf(ax); node.setAttribute("cy", sy(sign * cc[which][i])); syncNodeRow(i); };
        const up = () => { node.removeEventListener("pointermove", move); node.removeEventListener("pointerup", up); drawDamper(); mark(); refresh(); };
        node.addEventListener("pointermove", move); node.addEventListener("pointerup", up);
      });
      node.addEventListener("keydown", ev => { const d = ev.key === "ArrowUp" ? 1 : ev.key === "ArrowDown" ? -1 : 0; if (!d) return; ev.preventDefault(); setNode(ax, which, i, c[which][i] + d * sign * 50); drawDamper(); mark(); refresh(); });
    }));
    sv(svg, "rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "none", stroke: css("--rule") });
    dmpBox.replaceChildren(svg);
    if (!live) drawDamperTable();
  }
  const nodeRows = [];
  function syncNodeRow(i) { const c = curOf(dmpAxle), r = nodeRows[i]; if (!r) return; ["reb", "comp"].forEach(w => { r[w].inp.value = c[w][i]; r[w].rng.value = c[w][i]; }); }
  function drawDamperTable() {
    const ax = dmpAxle, ref = refOf(ax), c = curOf(ax), tuned = !!model[ax].damper_tune;
    nodeRows.length = 0;
    const cell = (which, i, refVal) => {
      const max = Math.max(1000, Math.ceil(Math.max(refVal, c[which][i]) * 2.5 / 500) * 500);
      const inp = h("input", { type: "number", class: "numeric", step: 10, min: 0, max: 60000, value: c[which][i] });
      const rng = h("input", { type: "range", class: "sus-rng", step: 10, min: 0, max, value: c[which][i], "aria-label": `${fmt(c.v[i], 2)} m/s ${which === "reb" ? "çekme" : "basma"} kuvveti` });
      inp.addEventListener("change", () => { const v = parseFloat(inp.value); if (Number.isNaN(v)) return; setNode(ax, which, i, v); drawDamper(); mark(); refresh(); });
      rng.addEventListener("input", () => { setNode(ax, which, i, parseFloat(rng.value)); inp.value = rng.value; drawDamper(true); mark(); refresh(); });
      nodeRows[i] = nodeRows[i] || {}; nodeRows[i][which] = { inp, rng };
      return [h("td", { class: "n" + (c[which][i] !== refVal ? " chg" : "") }, inp), h("td", { class: "sl" }, rng)];
    };
    const body = h("tbody", {}, c.v.map((v, i) => h("tr", { class: "item" }, h("td", { class: "n" }, fmt(v, 2)), ...cell("reb", i, ref.rebound_n[i]), ...cell("comp", i, ref.compression_n[i]))));
    const scale = (which, key, label) => {
      const rng = h("input", { type: "range", class: "sus-rng", min: 40, max: 250, step: 5, value: 100, "aria-label": label }), out = h("b", { class: "num" }, "%100");
      rng.addEventListener("input", () => { out.textContent = `%${rng.value}`; const cc = curOf(ax); cc[which] = ref[key].map(x => Math.round(x * rng.value / 100)); model[ax].damper_tune = { rebound_n: cc.reb, compression_n: cc.comp }; drawDamper(true); c.v.forEach((_, i) => syncNodeRow(i)); mark(); refresh(); });
      return h("label", { class: "sus-f" }, h("span", {}, label), rng, out);
    };
    dmpTbl.replaceChildren(
      h("div", { class: "row sus-par" }, scale("reb", "rebound_n", "Çekmenin tamamı"), scale("comp", "compression_n", "Basmanın tamamı"), h("span", { class: "spacer" }),
        h("button", { type: "button", class: "ghost", disabled: tuned ? null : true, title: "Referans eğriye dön", onclick: () => { delete model[ax].damper_tune; drawDamper(); mark(); refresh(); } }, "Referansa dön")),
      h("table", { class: "data shf-sp sus-sp sus-nodes" }, h("thead", {}, h("tr", {}, h("th", { class: "n" }, "Hız [m/s]"), h("th", { class: "n" }, "Çekme [N]"), h("th", {}, ""), h("th", { class: "n" }, "Basma [N]"), h("th", {}, ""))), body));
  }
  function drawDamperHead() {
    const ax = dmpAxle, keys = Object.keys(model.dampers);
    const refSel = select(keys.map(k => [k, model.dampers[k].name]), model[ax].damper);
    refSel.addEventListener("change", () => { model[ax].damper = refSel.value; delete model[ax].damper_tune; drawDamper(); drawDamperHead(); mark(); refresh(); });
    const d = base ? base.summary : null, i = ax === "front" ? 0 : 1;
    dmpHead.replaceChildren(h("div", { class: "section-title", style: "margin:0" }, "Amortisör"), seg([["front", "Ön"], ["rear", "Arka"]], ax, v => { dmpAxle = v; drawDamperHead(); drawDamper(); }),
      h("span", { class: "muted small" }, "referans eğri"), refSel, h("span", { class: "spacer" }),
      h("span", { class: "muted small num" }, d ? `her yanda ${model[ax].dampers_per_side} adet · sönüm oranı ${fmt(d.damping_ratio[i], 2)} · tekerlekte ${fmt(d.damping_ns_per_m[2 * i] / 1000, 1)} kNs/m` : ""));
  }
  const dmpPanel = h("div", { class: "panel" }, dmpHead, dmpBox,
    h("div", { class: "shf-legend small" }, h("span", {}, h("i", { style: `background:${css("--blue")}` }), "çekme"), h("span", {}, h("i", { style: `background:${css("--ink")}` }), "basma"),
      h("span", {}, h("i", { class: "dash", style: `border-color:${css("--muted")}` }), "referans eğri"), h("span", {}, h("i", { class: "dash", style: `border-color:${css("--amber")}` }), "seçili manevrada ulaşılan en büyük hız"),
      h("span", {}, "Noktaları sürükleyin, sayıyı yazın ya da sürgüyü çekin.")), dmpTbl);

  /* ---------------- rollover ---------------- */
  const rollBox = h("div", { class: "sus-dmp" }), rollText = h("div", { class: "small", style: "margin-top:6px" });
  const radIn = h("input", { type: "number", class: "numeric", min: 15, max: 800, step: 5, value: opt.radius_m });
  const riseIn = h("input", { type: "number", class: "numeric", min: 0.1, max: 4, step: 0.1, value: opt.rise_s });
  radIn.addEventListener("change", () => { opt.radius_m = Math.min(800, Math.max(15, parseFloat(radIn.value) || 100)); radIn.value = opt.radius_m; store.set("susRadius", opt.radius_m); refreshRoll(); });
  riseIn.addEventListener("change", () => { opt.rise_s = Math.min(4, Math.max(0.1, parseFloat(riseIn.value) || 0.5)); riseIn.value = opt.rise_s; refreshRoll(); });
  function drawRoll() {
    if (!roll) { rollText.textContent = "Hesaplanıyor…"; return; }
    const W = Math.max(420, rollBox.clientWidth || 640), H = 260, L = 50, R = 14, T = 12, B = 34;
    const pts = roll.curve, rmax = pts[pts.length - 1].radius_m, vmax = Math.min(160, Math.max(...pts.map(p => p.slide_kmh)) * 1.02);
    const sx = r => L + Math.sqrt(r / rmax) * (W - L - R), sy = v => T + (1 - v / vmax) * (H - T - B);
    const svg = svgEl(W, H, "Viraj yarıçapına göre devrilme hızı");
    sv(sv(sv(svg, "defs", {}), "clipPath", { id: "sus-clip" }), "rect", { x: L, y: T, width: W - L - R, height: H - T - B });
    for (let v = 0; v <= vmax; v += 20) { sv(svg, "line", { x1: L, x2: W - R, y1: sy(v), y2: sy(v), stroke: css("--grid") }); sv(svg, "text", { x: L - 6, y: sy(v) + 4, "text-anchor": "end", "font-size": 11, fill: css("--muted") }, fmt(v, 0)); }
    [25, 50, 100, 200, 300, 400, 500].filter(r => r <= rmax).forEach(r => { sv(svg, "line", { x1: sx(r), x2: sx(r), y1: T, y2: H - B, stroke: css("--grid") }); sv(svg, "text", { x: sx(r), y: H - B + 15, "text-anchor": "middle", "font-size": 11, fill: css("--muted") }, fmt(r, 0)); });
    sv(svg, "text", { x: (L + W - R) / 2, y: H - 4, "text-anchor": "middle", "font-size": 11.5, fill: css("--ink-2") }, "viraj yarıçapı [m]");
    sv(svg, "text", { x: 4, y: T + 2, "font-size": 10.5, fill: css("--ink-2"), "dominant-baseline": "hanging" }, "km/h");
    const line = (key, col, w, dash) => sv(svg, "path", { d: pts.map((p, i) => `${i ? "L" : "M"}${sx(p.radius_m).toFixed(1)} ${sy(p[key]).toFixed(1)}`).join(""), fill: "none", stroke: col, "stroke-width": w, "stroke-dasharray": dash || null, "clip-path": "url(#sus-clip)" });
    line("slide_kmh", css("--muted"), 1.4, "5 4"); line("static_kmh", css("--ink"), 2.2); line("dynamic_kmh", css("--err"), 2.2);
    const a = roll.at_radius;
    if (a.radius_m <= rmax) {
      sv(svg, "line", { x1: sx(a.radius_m), x2: sx(a.radius_m), y1: T, y2: H - B, stroke: css("--amber") });
      [[a.static_kmh, css("--ink")], [a.dynamic_kmh, css("--err")]].forEach(([v, col]) => { if (v <= vmax) sv(svg, "circle", { cx: sx(a.radius_m), cy: sy(v), r: 4.5, fill: col }); });
    }
    sv(svg, "rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "none", stroke: css("--rule") });
    rollBox.replaceChildren(svg);
    rollText.replaceChildren(
      h("p", {}, `Rijit araç (iz / 2 × ağırlık merkezi yüksekliği) ${fmt(roll.ssf_rigid_g, 2)} g. Gövde yalpalayıp lastikler esneyince eşik ${fmt(roll.static_g, 2)} g'ye iner (${roll.static_side}); o anda gövde ${fmt(roll.static_roll_deg, 1)}° yatmıştır, ilk iç tekerlek ${fmt(roll.first_wheel_g, 2)} g'de kalkar. Yanal ivme ${fmt(roll.rise_s, 1)} s'de kurulursa gövde aşar ve eşik ${fmt(roll.dynamic_g, 2)} g olur.`),
      h("p", {}, `R ${fmt(a.radius_m, 0)} m virajda: sabit hızla dönerken ${fmt(a.static_kmh, 0)} km/h, viraja ani girişte ${fmt(a.dynamic_kmh, 0)} km/h'te iç tekerlekler kalkar. ` +
        (roll.slides_first ? `Yol tutuşu (${fmt(roll.friction, 2)} g) eşiğin altında: araç devrilmeden önce kayar (${fmt(a.slide_kmh, 0)} km/h).` : `Yol tutuşu ${fmt(roll.friction, 2)} g: kuru yolda araç kaymadan önce devrilir.`)),
      roll.steer ? h("p", {}, `Direksiyon (δ = L/R + K·ay, K = ${fmt(roll.steer.understeer_deg_per_g, 1)} °/g): bu viraj düşük hızda ${fmt(roll.steer.ackermann_deg, 1)}° ön tekerlek açısı ister, devrilme hızında ${fmt(roll.steer.at_static_deg, 1)}°.` +
        (roll.steer.characteristic_kmh ? ` Karakteristik hız ${fmt(roll.steer.characteristic_kmh, 0)} km/h.` : "")) : null,
      h("p", { class: "muted" }, `Eğme tablası karşılığı ${fmt(roll.tilt_deg, 1)}° (rijit ${fmt(roll.tilt_rigid_deg, 1)}°); UN R107 ${fmt(roll.tilt_required_deg, 0)}° ister. Gillespie Denk. 9-6 ile kontrol: ${fmt(roll.book_suspended_g, 2)} g (lastik esnemesi ve tamponlar hariç).`));
  }
  const rollPanel = h("div", { class: "panel" },
    h("div", { class: "row sus-par" }, h("div", { class: "section-title", style: "margin:0" }, "Devrilme"), h("label", { class: "sus-f" }, h("span", {}, "Viraj yarıçapı"), radIn, h("em", {}, "m")),
      h("label", { class: "sus-f" }, h("span", {}, "Ani manevrada ivme kuruluşu"), riseIn, h("em", {}, "s"))),
    rollBox,
    h("div", { class: "shf-legend small" }, h("span", {}, h("i", { style: `background:${css("--ink")}` }), "sabit hızla dönüş"), h("span", {}, h("i", { style: `background:${css("--err")}` }), "ani manevra"),
      h("span", {}, h("i", { class: "dash", style: `border-color:${css("--muted")}` }), "kayma sınırı"), h("span", {}, "Çizginin üstündeki hızda iç tekerlekler kalkar.")),
    rollText);

  /* ---------------- air springs, comfort, modes ---------------- */
  const sprBody = h("tbody", {}), sprNote = h("div", {});
  function drawSprings() {
    if (!base) return;
    const s = base.summary, notes = [];
    sprBody.replaceChildren(...["front", "rear"].map((ax, i) => {
      const sp = s.springs[i], key = model[ax].spring, def = model.air_springs[key];
      const avail = Object.keys(sp.rates_by_basis);
      const bs = select(avail.map(b => [b, `${info.rate_bases[b]} · ${fmt(sp.rates_by_basis[b] / 1000, 0)} N/mm`]), sp.basis);
      bs.addEventListener("change", () => { def.rate_basis = bs.value; mark(); refresh(); });
      const vals = Object.values(sp.rates_by_basis);
      if (vals.length > 1 && Math.max(...vals) / Math.min(...vals) > 1.3) notes.push(`${i ? "Arka" : "Ön"} körük (${sp.name}): çizimdeki veriler aynı basınçta ${avail.map(b => `${info.rate_bases[b]} ${fmt(sp.rates_by_basis[b] / 1000, 0)} N/mm`).join(", ")} veriyor. Seçili: ${info.rate_bases[sp.basis]}.` + (def.note ? ` ${def.note}` : ""));
      return h("tr", { class: "item" }, h("td", { class: "name" }, i ? "Arka" : "Ön", h("div", { class: "muted small" }, sp.name)), h("td", { class: "n" }, `${model[ax].springs_per_side} × 2`),
        h("td", { class: "n" + (sp.over_pressure ? " bad" : "") }, `${fmt((sp.pressure_bar[0] + sp.pressure_bar[1]) / 2, 2)} / ${fmt(sp.max_pressure_bar, 1)}`), h("td", { class: "n" }, fmt(sp.force_n / 1000, 1)), h("td", {}, bs),
        h("td", { class: "n" }, `${fmt(s.ride_hz[i], 2)}`), h("td", { class: "n" }, `${fmt(s.wheel_hop_hz[i], 1)}`), h("td", { class: "n" }, `+${fmt(sp.bump_travel_mm, 0)} / −${fmt(sp.rebound_travel_mm, 0)}`));
    }));
    sprNote.replaceChildren(notes.length ? notice("warn", "Körük verisinde çelişki", notes) : "");
  }
  const sprPanel = h("div", { class: "panel list" }, h("div", { class: "group-head" }, h("h3", {}, "Körükler"), h("span", { class: "n" }, "basınç yükten gelir: seviye valfleri sürüş yüksekliğini sabit tutar")),
    h("div", { class: "tbl-x" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Aks"), h("th", { class: "n" }, "Adet"), h("th", { class: "n" }, "Basınç / sınır [bar]"), h("th", { class: "n" }, "Yük [kN]"), h("th", {}, "Yay katsayısı (körük başına)"),
      h("th", { class: "n" }, "Sürüş [Hz]"), h("th", { class: "n" }, "Tekerlek [Hz]"), h("th", { class: "n" }, "Strok [mm]"))), sprBody)), sprNote);

  const comBody = h("tbody", {});
  const comSpeed = h("input", { type: "number", class: "numeric", min: 10, max: 130, step: 5, value: opt.comfort_speed_kmh });
  comSpeed.addEventListener("change", () => { opt.comfort_speed_kmh = Math.min(130, Math.max(10, parseFloat(comSpeed.value) || 80)); comSpeed.value = opt.comfort_speed_kmh; refresh(); });
  function drawComfort() {
    if (!comf) return;
    const c = x => h("td", { class: "n", title: x.label }, fmt(x.aw_mps2, 2));
    comBody.replaceChildren(...comf.rows.map(r => h("tr", {}, h("td", {}, r.label), c(r.front), c(r.cog), c(r.rear), h("td", {}, r.cog.label), h("td", { class: "n" }, `${fmt(r.damper_rms_mps[0], 2)} / ${fmt(r.damper_rms_mps[1], 2)}`))));
  }
  const comPanel = h("div", { class: "panel list" }, h("div", { class: "group-head" }, h("h3", {}, "Konfor"), h("span", { class: "n" }, "ISO 8608 yolu, ISO 2631 ağırlıklı düşey ivme"), h("span", { class: "spacer" }), h("label", { class: "sus-f" }, h("span", {}, "Hız"), comSpeed, h("em", {}, "km/h"))),
    h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Yol sınıfı"), h("th", { class: "n" }, "Ön aks üstü [m/s²]"), h("th", { class: "n" }, "Ağırlık merkezi"), h("th", { class: "n" }, "Arka aks üstü"), h("th", {}, "Orta koltukta his"),
      h("th", { class: "n" }, "Amortisör hızı ön / arka [m/s RMS]"))), comBody));

  const modBody = h("tbody", {}), modExtra = h("div", { class: "muted small", style: "padding:6px 10px" });
  function drawModes() {
    if (!base) return;
    modBody.replaceChildren(...base.modes.map(m => h("tr", {}, h("td", {}, m.name), h("td", { class: "n" }, fmt(m.hz, 2)), h("td", { class: "n" }, m.real ? "salınımsız" : fmt(m.damped_hz, 2)), h("td", { class: "n" }, fmt(m.damping_ratio, 2)), h("td", { class: "n" }, m.settle_s == null ? "—" : fmt(m.settle_s, 2)))));
    const bp = base.summary.bounce_pitch, r = base.summary.roll;
    modExtra.textContent = `Gillespie Denk. 5-35/5-36 (sönümsüz, sürüş katsayılarıyla): ${bp.modes.map(m => `${m.kind === "bounce" ? "zıplama" : "yunuslama"} ${fmt(m.hz, 2)} Hz, merkez ağırlık merkezinin ${fmt(Math.abs(m.centre_m), 1)} m ${m.centre_m >= 0 ? "önünde" : "arkasında"}`).join(" · ")} · dinamik indeks ${fmt(bp.dynamic_index, 2)}. ` +
      `Yalpa direngenliği ön/arka ${fmt(r.stiffness_axle_nm_per_rad[0] / 1000, 0)} / ${fmt(r.stiffness_axle_nm_per_rad[1] / 1000, 0)} kNm/rad (ön pay %${fmt(100 * r.front_share, 0)}); yalpa ekseni ağırlık merkezinde ${fmt(r.h_roll_axis_m, 2)} m, kol ${fmt(r.h1_m, 2)} m; Gillespie Denk. 6-32 ile yalnız süspansiyon ${fmt(r.gradient_book_deg_per_g, 1)} °/g.`;
  }
  const modPanel = h("details", { class: "panel list shf-grp" }, h("summary", { class: "group-head" }, h("h3", {}, "Titreşim modları"), h("span", { class: "n" }, "doğrusallaştırılmış model")),
    h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", {}, "Hareket"), h("th", { class: "n" }, "Doğal frekans [Hz]"), h("th", { class: "n" }, "Sönümlü [Hz]"), h("th", { class: "n" }, "Sönüm oranı"), h("th", { class: "n" }, "%2'ye sönüm [s]"))), modBody), modExtra);

  /* ---------------- talking to the server ---------------- */
  const note = h("div", {});
  const body = parts => ({ model, passengers: load.passengers, luggage_kg: load.luggage_kg, manoeuvre: man, parts, ...opt });
  async function compute() {
    const my = ++seq;
    note.replaceChildren();
    try {
      const r = await api.post(`/api/suspension/${enc}/analyse`, body(["base", "sim", "comfort"]));
      if (my !== seq) return;
      base = r; sim = r.sim; comf = r.comfort;
      drawKpis(); drawSim(); drawSprings(); drawComfort(); drawModes(); drawDamperHead(); drawDamper();
      const q = await api.post(`/api/suspension/${enc}/analyse`, body(["roll"]));
      if (my !== seq) return;
      roll = q.rollover;
      drawKpis(); drawRoll(); drawSim();
    } catch (e) { if (my === seq) note.replaceChildren(notice("err", "Hesaplanamadı", [e.message])); }
  }
  function refresh() { clearTimeout(timer); timer = setTimeout(compute, 160); }
  async function refreshRoll() {
    const my = ++seq;
    try { const q = await api.post(`/api/suspension/${enc}/analyse`, body(["roll"])); if (my !== seq) return; roll = q.rollover; drawKpis(); drawRoll(); drawSim(); }
    catch (e) { if (my === seq) note.replaceChildren(notice("err", "Hesaplanamadı", [e.message])); }
  }
  async function save() {
    const own = info.linked;
    const r = await api.post(`/api/suspension/${enc}/save`, own ? { model, stem: info.stem } : { model: { ...model, vehicles: [] }, stem: veh.stem, name: `${veh.name} · süspansiyon` });
    store.set(fileKey, null);
    toast(`Kaydedildi: ${r.saved}`);
    if (!own) { U.route(); return; }
    savedText = JSON.stringify(model); mark();
  }

  root.append(h("div", { class: "shf-grid" }, h("div", { class: "stack" }, h("div", { class: "row" }, state), editor),
    h("div", { class: "stack" }, note, warn, kpis, manPanel, dmpPanel, rollPanel, sprPanel, comPanel, modPanel)));
  drawManHead(); drawDamperHead(); mark();
  let rz = null;
  const onResize = () => { clearTimeout(rz); rz = setTimeout(() => { drawDamper(true); drawRoll(); }, 150); };
  addEventListener("resize", onResize);
  U.setCleanup(() => { removeEventListener("resize", onResize); clearTimeout(timer); });
  setTimeout(() => { drawDamper(); compute(); }, 0);
  return root;
}
