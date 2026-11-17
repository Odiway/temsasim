/* "Çoklu koşu": two to four simulations set up side by side, run at the same
   time, each with its own result card, then one comparison underneath: a
   metric table with the best value marked, overlaid curves on a shared
   distance axis, where the energy went, and a comparison report to keep.

   Slots live in localStorage so a set-up survives a page change; results do
   not (they are a click away). */

const LETTERS = ["A", "B", "C", "D"];
const KWH_PER_L = 9.92;                      // diesel LHV at 836 kg/m³: 42.7 MJ/kg → kWh per litre

export async function pageCompare(U) {
  const { h, api, store, fmt, parseNum, seg, select, field, numInput, toast, busy, notice, ov, pageHead, lineChart, css, menu, downloadTable } = U;
  const vehicles = ov.vehicles.filter(v => !v.error);
  const root = h("div", {});
  if (!vehicles.length) {
    root.append(pageHead("Çoklu koşu", "Çoklu koşu", ""), notice("warn", "Önce bir araç kurun.", []), h("a", { class: "btn", href: "#/arac/yeni" }, "Araç kur"));
    return root;
  }
  const COLORS = [css("--s1"), css("--s2"), css("--s3"), css("--s6")];
  const scenarios = (ov.scenarios || []).filter(s => !s.error);
  const blank = () => ({ vehicle: store.get("vehicle", vehicles[0].stem), dutyKind: ov.routes.length ? "route" : "cycle",
    duty: ov.routes.length ? store.get("simRoute", ov.routes[0]) : "cbdbus", traffic: "yok", ambient: 20, solar: 0,
    passengers: 40, occupancy_mode: "profile", night: false, hvac_off: false, label: "" });
  let slots = store.get("cmp.slots", null);
  if (!Array.isArray(slots) || !slots.length) slots = [blank(), { ...blank(), ambient: 35, solar: 800 }];
  slots = slots.map(s => ({ ...blank(), ...s }));
  let n = Math.min(4, Math.max(2, store.get("cmp.n", slots.length)));
  while (slots.length < n) slots.push({ ...blank() });
  const results = [];                                     // per slot: {r, p} after a run
  const saveSlots = () => { store.set("cmp.slots", slots.slice(0, n)); store.set("cmp.n", n); };

  /* ---------------- header ---------------- */
  const nSeg = seg([[2, "2"], [3, "3"], [4, "4"]], n, v => { n = v; while (slots.length < n) slots.push({ ...blank() }); saveSlots(); drawSlots(); drawCompare(); });
  const runAllBtn = h("button", { type: "button", class: "primary" }, "Hepsini koş");
  const reportBtn = h("button", { type: "button", disabled: true }, "Karşılaştırma raporu");
  const scenSel = select([["", "Senaryodan doldur…"], ...scenarios.map(s => [s.stem, s.name])], "");
  scenSel.style.width = "auto"; scenSel.style.maxWidth = "260px";
  scenSel.addEventListener("change", async () => {
    if (!scenSel.value) return;
    const sc = (await api.get(`/api/scenario/${encodeURIComponent(scenSel.value)}`)).scenario;
    const base = { dutyKind: sc.duty.route ? "route" : "cycle", duty: sc.duty.route || sc.duty.cycle || "cbdbus", traffic: sc.traffic || "yok",
      ambient: sc.conditions.ambient_c, solar: sc.conditions.solar || 0, passengers: sc.load.passengers || 0,
      occupancy_mode: sc.load.mode === "constant" ? "constant" : "profile", night: !!sc.conditions.night, hvac_off: !!sc.conditions.hvac_off };
    const vs = (sc.vehicles || []).filter(v => vehicles.some(x => x.stem === v));
    if (vs.length > 1) { n = Math.min(4, Math.max(2, vs.length)); $$(nSeg).forEach((b, k) => b.setAttribute("aria-pressed", String([2, 3, 4][k] === n))); }
    for (let i = 0; i < n; i++) slots[i] = { ...blank(), ...base, vehicle: vs[i] || vs[0] || slots[i].vehicle, label: "" };
    saveSlots(); drawSlots(); scenSel.value = "";
    toast(`Senaryo yüklendi: ${sc.name}${vs.length > 1 ? ` · ${vs.length} araç` : ""}`);
  });
  const $$ = el => [...el.querySelectorAll("button")];
  root.append(pageHead("Çoklu koşu", "Çoklu koşu ve karşılaştırma",
    "Her kart bağımsız bir simülasyondur: başka araç, başka güzergâh ya da aynı görevde başka koşul. Hepsi aynı anda koşar; altta sonuçlar tek tabloda ve aynı eksende üst üste gelir. Karşılaştırma raporu tek HTML dosyasıdır, her koşunun tam raporu ayrıca durur.",
    [h("span", { class: "small muted" }, "Koşu sayısı"), nSeg, scenSel, runAllBtn, reportBtn]));

  /* ---------------- slots ---------------- */
  const grid = h("div", { class: "cmp-grid" });
  const slotEls = [];
  const slotLabel = (s, i) => {
    if (s.label) return s.label;
    const v = vehicles.find(x => x.stem === s.vehicle) || {};
    return `${v.name || s.vehicle} · ${s.duty} · ${fmt(s.ambient, 0)} °C${s.night ? " · gece" : ""}${s.traffic !== "yok" ? ` · trafik ${s.traffic}` : ""}`;
  };
  function slotCard(i) {
    const s = slots[i];
    const col = COLORS[i];
    const vSel = select(vehicles.map(v => [v.stem, `${v.name}${v.variant ? " — " + v.variant : ""}`]), s.vehicle);
    const dutySel = h("select", {});
    const fillDuty = () => { const list = s.dutyKind === "route" ? ov.routes : ov.cycles; dutySel.replaceChildren(...list.map(x => h("option", { value: x, selected: x === s.duty ? true : null }, x))); if (!list.includes(s.duty)) s.duty = list[0]; };
    fillDuty();
    const trafficSel = select(ov.traffic_levels, s.traffic);
    const dutySeg = seg([["route", "Güzergâh"], ["cycle", "Çevrim"]], s.dutyKind, v => { s.dutyKind = v; fillDuty(); s.duty = dutySel.value; trafficSel.disabled = v !== "route"; saveSlots(); });
    trafficSel.disabled = s.dutyKind !== "route";
    const amb = numInput(s.ambient, { unit: "°C" }), sol = numInput(s.solar, { unit: "W/m²" }), pax = numInput(s.passengers, { unit: "kişi" });
    const night = h("input", { type: "checkbox", checked: s.night ? true : null }), hvacOff = h("input", { type: "checkbox", checked: s.hvac_off ? true : null });
    const labelIn = h("input", { type: "text", value: s.label || "", placeholder: slotLabel(s, i) });
    const read = () => {
      s.vehicle = vSel.value; s.duty = dutySel.value; s.traffic = trafficSel.value;
      s.ambient = parseNum(amb.querySelector("input").value) || 0; s.solar = parseNum(sol.querySelector("input").value) || 0;
      s.passengers = parseNum(pax.querySelector("input").value) || 0; s.night = night.checked; s.hvac_off = hvacOff.checked; s.label = labelIn.value.trim();
      labelIn.placeholder = slotLabel({ ...s, label: "" }, i);
      saveSlots();
    };
    [vSel, dutySel, trafficSel, night, hvacOff, labelIn].forEach(el => el.addEventListener("change", read));
    [amb, sol, pax].forEach(el => el.addEventListener("input", read));
    const res = h("div", { class: "cmp-res" }, h("p", { class: "small muted", style: "margin:0" }, "Henüz koşmadı."));
    const runBtn = h("button", { type: "button", class: "btn sm", onclick: e => busy(e.currentTarget, () => runSlot(i)) }, "Koş");
    const card = h("div", { class: "panel cmp-slot", style: `--slot:${col}` },
      h("div", { class: "cmp-head" }, h("span", { class: "cmp-letter" }, LETTERS[i]), h("span", { class: "cmp-title" }, slotLabel(s, i)), h("span", { class: "spacer" }), runBtn,
        menu("", [
          { label: "A kartından kopyala", onclick: () => { slots[i] = { ...slots[0], label: "" }; saveSlots(); drawSlots(); }, hidden: i === 0 },
          { label: "Temizle", onclick: () => { slots[i] = blank(); results[i] = null; saveSlots(); drawSlots(); drawCompare(); } },
        ].filter(x => !x.hidden), { cls: "ghost dots sm" })),
      h("div", { class: "cmp-form" },
        field("Araç", vSel),
        h("div", { class: "field" }, h("div", { class: "lab" }, "Görev"), dutySeg, h("div", { style: "margin-top:6px" }, dutySel)),
        field("Trafik", trafficSel),
        h("div", { class: "cmp-3" }, field("Sıcaklık", amb), field("Güneş", sol), field("Yolcu", pax)),
        h("div", { class: "row", style: "gap:14px" }, h("label", { class: "check" }, night, h("span", {}, "Gece")), h("label", { class: "check" }, hvacOff, h("span", {}, "Klima kapalı"))),
        field("Etiket", labelIn, { hint: "Boş bırakılırsa araç · görev · sıcaklık yazılır." })),
      res);
    card.querySelector(".cmp-title").addEventListener("click", () => card.classList.toggle("collapsed"));
    return { card, res, runBtn, title: card.querySelector(".cmp-title") };
  }
  function drawSlots() {
    slotEls.length = 0; grid.replaceChildren();
    grid.style.setProperty("--n", n);
    for (let i = 0; i < n; i++) { const el = slotCard(i); slotEls.push(el); grid.append(el.card); if (results[i]) showResult(i); }
  }
  const params = s => ({ vehicle: s.vehicle, [s.dutyKind === "route" ? "route" : "cycle"]: s.duty, traffic: s.dutyKind === "route" ? s.traffic : "yok",
    ambient: s.ambient, solar: s.solar, passengers: s.passengers, occupancy_mode: s.occupancy_mode || "profile", night: s.night, hvac_off: s.hvac_off, repeat: 1, daily_km: 200 });
  async function runSlot(i) {
    const el = slotEls[i]; const s = slots[i];
    el.res.replaceChildren(h("div", { class: "cmp-busy" }, h("span", { class: "spin" }), "Hesaplanıyor…"));
    el.card.classList.add("collapsed");
    try {
      const p = params(s);
      const r = await api.post("/api/simulate", p);
      results[i] = { r, p, label: slotLabel(s, i), color: COLORS[i] };
      showResult(i);
    } catch (e) {
      results[i] = null;
      el.res.replaceChildren(notice("err", "Koşu başarısız.", [e.message]));
    }
    drawCompare();
  }
  function showResult(i) {
    const el = slotEls[i]; const { r, label } = results[i]; const k = r.kpi, tl = r.play.timeline, dz = k.powertrain === "diesel";
    el.title.textContent = label;
    const box = (lab, v, u) => h("div", { class: "cmp-kpi" }, h("div", { class: "k" }, lab), h("div", { class: "v num" }, v, u ? h("small", {}, u) : null));
    const x = tl.d.map(d => d / 1000);
    el.res.replaceChildren(
      h("div", { class: "cmp-kpis" },
        box("Tüketim", dz ? fmt(k.l_per_100km, 2) : fmt(k.kwh_per_km, 3), dz ? "l/100 km" : "kWh/km"),
        box(dz ? "CO₂" : "Menzil", dz ? fmt(k.co2_g_per_km, 0) : fmt(k.range_km, 0), dz ? "g/km" : "km"),
        box("Süre", fmt(k.duration_min, 1), "dk"),
        box("Klima payı", `%${fmt(k.hvac_share * 100, 0)}`)),
      lineChart({ x, series: [{ label: "Hız", color: results[i].color, y: tl.v }], xLabel: "km", yLabel: "", xFmt: v => fmt(v, 1), yFmt: v => `${fmt(v, 0)} km/h`, height: 110 }),
      !r.achievable ? h("div", { class: "small", style: "color:var(--err)" }, "Araç görevi tam takip edemedi.") : null,
      h("div", { class: "row", style: "gap:6px;margin-top:8px" },
        h("a", { class: "btn sm", href: `/report/${r.report_id}`, target: "_blank", rel: "noopener" }, "Tam rapor"),
        h("button", { type: "button", class: "btn sm", onclick: () => open3d(i) }, "3B izle"),
        h("a", { class: "btn sm ghost", href: `/report/${r.report_id}/csv`, download: "" }, "CSV")));
  }
  const open3d = i => {
    const s = slots[i];
    store.set("vehicle", s.vehicle); store.set("dutyKind", s.dutyKind); store.set(s.dutyKind === "route" ? "simRoute" : "simCycle", s.duty);
    store.set("simAmb", s.ambient); store.set("simSol", s.solar); store.set("simPax", s.passengers); store.set("simTraffic", s.traffic); store.set("simHvacOff", s.hvac_off);
    store.set("autoRunSim", true);
    location.hash = "#/simulasyon";
  };
  runAllBtn.addEventListener("click", () => busy(runAllBtn, async () => {
    slotEls.forEach(el => (el.runBtn.disabled = true));
    try { await Promise.all(slots.slice(0, n).map((_, i) => runSlot(i))); }
    finally { slotEls.forEach(el => (el.runBtn.disabled = false)); }
    cmpBox.scrollIntoView({ behavior: "smooth", block: "start" });
  }));

  /* ---------------- comparison ---------------- */
  const cmpBox = h("div", { class: "cmp-compare" });
  function drawCompare() {
    const done = results.map((x, i) => x ? { ...x, i } : null).filter(Boolean).filter(x => x.i < n);
    reportBtn.disabled = done.length < 2;
    if (done.length < 2) { cmpBox.replaceChildren(done.length ? h("p", { class: "muted small" }, "Karşılaştırma için en az iki koşu gerekir.") : null); return; }
    const anyD = done.some(x => x.r.kpi.powertrain === "diesel"), anyE = done.some(x => x.r.kpi.powertrain !== "diesel");
    const last = tl => k => tl[k] ? tl[k][tl[k].length - 1] : 0;
    const F = done.map(x => {
      const k = x.r.kpi, tl = x.r.play.timeline, L = last(tl), dz = k.powertrain === "diesel";
      const total = dz ? L("e_used") * KWH_PER_L : L("e_used");
      return { ...x, dz, k, total,
        energy: dz ? k.kwh_per_km : k.kwh_per_km, cons: dz ? k.l_per_100km : k.kwh_per_km, consU: dz ? "l/100 km" : "kWh/km",
        trac: Math.max(0, L("e_trac") - L("e_regen")), hvac: L("e_hvac"), aux: L("e_aux"), brake: dz ? L("e_brake") : L("e_thermal"), regen: L("e_regen"),
        meanV: k.distance_km / Math.max(k.duration_min / 60, 1e-9) };
    });
    const bestOf = (key, lower = true, only) => {
      const c = F.filter(f => !only || only(f)); if (c.length < 2) return null;
      return c.reduce((b, f) => (lower ? f[key] < b[key] : f[key] > b[key]) ? f : b).i;
    };
    const ROWS = [
      ["Araç", f => (vehicles.find(v => v.stem === f.p.vehicle) || {}).name || f.p.vehicle, null],
      ["Görev", f => f.p.route || f.p.cycle, null],
      ["Koşul", f => `${fmt(f.p.ambient, 0)} °C · ${fmt(f.p.passengers, 0)} yolcu${f.p.night ? " · gece" : ""}${f.p.hvac_off ? " · klima kapalı" : ""}${f.p.traffic && f.p.traffic !== "yok" ? " · trafik " + f.p.traffic : ""}`, null],
      ["Mesafe", f => `${fmt(f.k.distance_km, 2)} km`, null],
      ["Süre", f => `${fmt(f.k.duration_min, 1)} dk`, "duration_min_k"],
      ["Ortalama hız", f => `${fmt(f.meanV, 1)} km/h`, null],
      ["Tüketim", f => `${fmt(f.cons, f.dz ? 2 : 3)} ${f.consU}`, anyD && anyE ? null : "cons"],
      ["Enerji (dizelde yakıt ısıl değeri)", f => `${fmt(f.energy, 3)} kWh/km`, "energy"],
      ["CO₂ (egzoz)", f => f.dz ? `${fmt(f.k.co2_g_per_km, 0)} g/km` : "0 g/km", null],
      ["Menzil", f => `${fmt(f.k.range_km, 0)} km`, ["range", false]],
      ["Klima payı", f => `%${fmt(f.k.hvac_share * 100, 0)}`, "hvacShare"],
      ["Klima enerjisi", f => `${fmt(f.hvac, 2)} kWh`, "hvac"],
      ["Yardımcı tüketiciler", f => `${fmt(f.aux, 2)} kWh`, "aux"],
      [anyD ? "Fren (retarder + balata) / paket ısıl" : "Paket ısıl yönetim", f => `${fmt(f.brake, 2)} kWh`, "brake"],
      ["Geri kazanılan", f => f.dz ? "—" : `${fmt(f.regen, 2)} kWh`, ["regen", false, f => !f.dz]],
      ["Görev tamamlandı", f => f.r.achievable ? "evet" : "hayır", null],
    ];
    F.forEach(f => { f.range = f.k.range_km; f.hvacShare = f.k.hvac_share; f.duration_min_k = f.k.duration_min; });
    const base = F[0];
    const table = h("table", { class: "data cmp-table" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), F.map(f => h("th", { style: `color:${f.color}` }, h("span", { class: "cmp-letter", style: `--slot:${f.color}` }, LETTERS[f.i]), " ", f.label)))),
      h("tbody", {}, ROWS.map(([lab, fn, best]) => {
        let bi = null;
        if (typeof best === "string") bi = bestOf(best, true);
        else if (Array.isArray(best)) bi = bestOf(best[0], best[1], best[2]);
        return h("tr", {}, h("th", {}, lab), F.map(f => h("td", { class: "n" + (bi === f.i ? " best" : "") }, fn(f))));
      })));
    const deltas = F.slice(1).map(f => {
      const de = (f.energy - base.energy) / Math.max(base.energy, 1e-9) * 100;
      const sign = v => (v >= 0 ? "+" : "−") + fmt(Math.abs(v), 1);
      return h("li", {}, h("b", { style: `color:${f.color}` }, LETTERS[f.i]), ` ${LETTERS[base.i]} koşusuna göre: enerji ${sign(de)} %, süre ${sign(f.k.duration_min - base.k.duration_min)} dk, menzil ${(f.k.range_km - base.k.range_km >= 0 ? "+" : "−")}${fmt(Math.abs(f.k.range_km - base.k.range_km), 0)} km.`);
    });
    // shared distance grid for the overlays
    const dmax = Math.max(...F.map(f => f.k.distance_km));
    const N = 400, X = Array.from({ length: N }, (_, j) => dmax * j / (N - 1));
    const onGrid = (f, key, scale = 1) => {
      const D = f.r.play.timeline.d.map(d => d / 1000), Y = f.r.play.timeline[key];
      let j = 0;
      return X.map(x => { if (x > D[D.length - 1] + 1e-6) return null; while (j < D.length - 2 && D[j + 1] < x) j++; const t = (x - D[j]) / ((D[j + 1] - D[j]) || 1); return (Y[j] + (Y[j + 1] - Y[j]) * Math.max(0, Math.min(1, t))) * scale; });
    };
    const chart = (title, key, yFmt, scaleOf) => h("div", { class: "panel" }, h("div", { class: "section-title" }, title),
      lineChart({ x: X, series: F.map(f => ({ label: LETTERS[f.i], color: f.color, y: onGrid(f, key, scaleOf ? scaleOf(f) : 1) })), xLabel: "km", yLabel: "", xFmt: v => fmt(v, 1), yFmt, height: 220 }));
    const split = h("div", { class: "panel" }, h("div", { class: "section-title" }, "Enerji nereye gitti"),
      F.map(f => {
        const parts = [[f.dz ? "Krank (net)" : "Çekiş (net)", f.trac, f.color], ["Klima", f.hvac, css("--s4")], [f.dz ? "PTO + fan" : "Yardımcı", f.aux, css("--s3")], [f.dz ? "Fren" : "Paket ısıl", f.brake, css("--err")]];
        const tot = parts.reduce((a, q) => a + q[1], 0) || 1;
        return h("div", { class: "cmp-split" }, h("div", { class: "lab" }, h("span", { class: "cmp-letter", style: `--slot:${f.color}` }, LETTERS[f.i]), " ", f.label),
          h("div", { class: "split" }, parts.map(([nm, v, c]) => h("i", { title: `${nm}: ${fmt(v, 2)} kWh`, style: `flex:${Math.max(v, 0.0001)};background:${c}` }))),
          h("div", { class: "legend split-legend" }, parts.map(([nm, v, c]) => h("span", {}, h("i", { style: `background:${c}` }), `${nm} ${fmt(v, 1)} kWh · %${fmt(v / tot * 100, 0)}`))));
      }));
    const EXPORT_COLS = [["label", "Koşu"], ["vehicle", "Araç"], ["duty", "Görev"], ["ambient", "Sıcaklık °C", 0], ["distance_km", "Mesafe km", 2], ["duration_min", "Süre dk", 1],
      ["cons", "Tüketim", 3], ["consU", "Birim"], ["energy", "Enerji kWh/km", 3], ["co2", "CO₂ g/km", 0], ["range_km", "Menzil km", 0], ["hvac_share", "Klima payı", 2],
      ["hvac", "Klima kWh", 2], ["aux", "Yardımcı kWh", 2], ["brake", anyD ? "Fren/ısıl kWh" : "Paket ısıl kWh", 2], ["regen", "Rejenerasyon kWh", 2], ["achievable", "Görev tamamlandı"]];
    const exportBody = fmtOut => ({ format: fmtOut, title: "Çoklu koşu karşılaştırması", subtitle: F.map(f => `${LETTERS[f.i]}: ${f.label}`).join(" · "),
      columns: EXPORT_COLS.map(([key, label, decimals]) => ({ key, label, decimals })),
      rows: F.map(f => ({ label: `${LETTERS[f.i]} ${f.label}`, vehicle: f.p.vehicle, duty: f.p.route || f.p.cycle, ambient: f.p.ambient, distance_km: f.k.distance_km, duration_min: f.k.duration_min,
        cons: f.cons, consU: f.consU, energy: f.energy, co2: f.dz ? f.k.co2_g_per_km : 0, range_km: f.k.range_km, hvac_share: f.k.hvac_share, hvac: f.hvac, aux: f.aux, brake: f.brake, regen: f.regen, achievable: f.r.achievable })) });
    cmpBox.replaceChildren(
      h("div", { class: "panel" },
        h("div", { class: "row" }, h("div", { class: "section-title", style: "flex:1;margin:0" }, "Karşılaştırma"), menu("Tabloyu dışa aktar", [
          { label: "Excel (.xlsx)", onclick: () => downloadTable(exportBody("xlsx")) }, { label: "CSV (; ayraçlı)", onclick: () => downloadTable(exportBody("csv")) }], { left: true })),
        anyD && anyE ? h("p", { class: "small muted", style: "margin:8px 0 0" }, "Elektrikli ve dizel aynı tabloda: Enerji satırı dizelde yakıtın alt ısıl değeridir; kıyas o satırdan yapılır.") : null,
        h("div", { class: "scrollx", style: "margin-top:10px" }, table),
        h("p", { class: "small muted", style: "margin:8px 0 0" }, "Yeşil hücre: o satırda en iyi değer."),
        h("ul", { class: "cmp-delta" }, deltas)),
      h("div", { class: "cmp-charts" },
        chart("Hız", "v", v => `${fmt(v, 0)} km/h`),
        chart(anyD ? "Güç: batarya ya da motor mili" : "Batarya gücü", "p_batt", v => `${fmt(v, 0)} kW`),
        chart("Kümülatif enerji (dizelde yakıt ısıl değeri)", "e_used", v => `${fmt(v, 1)} kWh`, f => f.dz ? KWH_PER_L : 1),
        chart(anyD ? "Şarj durumu ya da depo doluluğu" : "Şarj durumu", "soc", v => `%${fmt(v, 0)}`)),
      split);
  }
  reportBtn.addEventListener("click", () => busy(reportBtn, async () => {
    const done = results.map((x, i) => x ? { ...x, i } : null).filter(Boolean).filter(x => x.i < n);
    const title = prompt("Raporun başlığı:", "Karşılaştırma " + done.map(x => LETTERS[x.i]).join("–"));
    if (title === null) return;
    const r = await api.post("/api/compare/report", { title, runs: done.map(x => ({ report_id: x.r.report_id, label: `${LETTERS[x.i]} · ${x.label}` })) });
    window.open(`/report/${r.report_id}`, "_blank", "noopener");
    const dl = h("div", { class: "notice ok" }, h("b", {}, "Karşılaştırma raporu hazır. "), h("a", { href: `/report/${r.report_id}`, target: "_blank", rel: "noopener" }, "Aç"), " · ",
      h("a", { href: `/report/${r.report_id}/html`, download: "" }, "İndir (HTML)"));
    cmpBox.prepend(dl);
  }));

  root.append(grid, cmpBox);
  drawSlots();
  if (store.get("cmp.autoRun")) { store.set("cmp.autoRun", false); setTimeout(() => runAllBtn.click(), 80); }
  return root;
}

/* Called from the simulation page: put the current set-up into the next free
   compare slot (or replace the last one when all four are taken). */
export function addToCompare(store, p, dutyKind) {
  let slots = store.get("cmp.slots", []); if (!Array.isArray(slots)) slots = [];
  if (p.constant) { dutyKind = "cycle"; }            // the compare cards know routes and cycles; a steady-speed run keeps its vehicle and conditions
  const s = { vehicle: p.vehicle, dutyKind, duty: p.route || p.cycle || "cbdbus", traffic: p.traffic || "yok", ambient: p.ambient, solar: p.solar,
    passengers: p.passengers, occupancy_mode: p.occupancy_mode, night: !!p.night, hvac_off: !!p.hvac_off, label: "" };
  slots.push(s);
  while (slots.length > 4) slots.shift();
  const n = Math.min(4, Math.max(2, slots.length));
  store.set("cmp.slots", slots); store.set("cmp.n", n);
  return slots.length;
}
