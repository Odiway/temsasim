/* Route builder and scenario pages.

   Route builder: a road described segment by segment (length, grade, speed
   limit, surface, wind, a stop with boarding/alighting, a traffic light),
   previewed as an elevation profile, saved to the library as a spec (YAML)
   plus the 10 m grid (CSV) every vehicle drives. Templates for a city line
   and a highway run are starting points; an imported GPX route can be
   opened and becomes an editable copy.

   Scenarios: route (or standard cycle) + vehicles + conditions + load +
   traffic, saved and re-run; every vehicle of the scenario drives the same
   trace, and the table puts them side by side with their full reports. */

export async function pageRoute(U, stem) {
  const { h, api, store, fmt, parseNum, seg, select, field, numInput, toast, busy, notice, ov, pageHead, lineChart, css } = U;
  const root = h("div", {});
  root.append(pageHead("Güzergâh oluştur", "Yolu bölüm bölüm tanımlayın",
    "Her bölüm: uzunluk, eğim, hız sınırı, yüzey, rüzgâr; sonunda bir durak (biniş/iniş, bekleme) ya da trafik ışığı olabilir. Kaydedilen güzergâh her araçta, her senaryoda aynı şekilde koşar."));

  const surfaces = ov.surfaces || [];
  const KINDS = ov.route_kinds || [["sehir_ici", "Şehir içi"], ["otoyol", "Otoyol / şehirlerarası"], ["serbest", "Serbest"]];
  let spec = null, editable = true, stemIn, origStem = null;

  /* ---------------- left: the spec ---------------- */
  const nameIn = h("input", { type: "text", placeholder: "ör. Adana 19K hattı" });
  stemIn = h("input", { type: "text", placeholder: "otomatik (addan)" });
  const descIn = h("textarea", { placeholder: "Nereden nereye, hangi amaçla; varsayımlar." });
  const elev0 = numInput(50, { unit: "m" });
  let kind = "serbest";
  const kindBox = h("div", {});
  const segTable = h("div", { class: "rb-segs" });
  const totals = h("div", { class: "kpis" });
  const chartBox = h("div", { class: "panel" });
  const warnBox = h("div", {});

  const setKind = k => { kind = k; kindBox.replaceChildren(seg(KINDS, kind, v => { kind = v; })); };
  setKind(kind);

  const surfSel = (cur) => select(surfaces.map(s => [s.key, `${s.label} (×${fmt(s.factor, 2)})`]), cur || "asfalt");
  const num = (v, ph) => h("input", { type: "text", inputmode: "decimal", class: "numeric", value: v == null ? "" : String(v).replace(".", ","), placeholder: ph || "" });
  const readNum = (inp, dflt = 0) => { const n = parseNum(inp.value); return Number.isNaN(n) ? dflt : n; };

  function drawSegments() {
    segTable.replaceChildren(
      h("div", { class: "rb-seg rb-head" }, ...["#", "Ad", "Uzunluk m", "Eğim %", "Hız km/h", "Yüzey", "Rüzgâr m/s", "Durak", "Bekleme s", "Biniş", "İniş", "Işık", ""].map(x => h("span", {}, x))),
      ...spec.segments.map((s, i) => {
        const len = num(s.length_m), gr = num(s.grade_pct), vl = num(s.speed_limit_kmh), sf = surfSel(s.surface), wind = num(s.headwind_mps);
        const nm = h("input", { type: "text", value: s.name || "", placeholder: `Bölüm ${i + 1}` });
        const stopCb = h("input", { type: "checkbox", checked: s.stop ? true : null });
        const stopName = h("input", { type: "text", value: s.stop ? (s.stop.name || "") : "", placeholder: "durak adı", disabled: s.stop ? null : true });
        const dwell = num(s.stop ? s.stop.dwell_s : 20); const board = num(s.stop ? s.stop.board : 0); const alight = num(s.stop ? s.stop.alight : 0);
        for (const x of [dwell, board, alight]) x.disabled = !s.stop;
        const sig = h("input", { type: "checkbox", checked: s.signal ? true : null });
        const upd = () => {
          s.name = nm.value; s.length_m = readNum(len, 100); s.grade_pct = readNum(gr, 0); s.speed_limit_kmh = readNum(vl, 50); s.surface = sf.value; s.headwind_mps = readNum(wind, 0);
          s.signal = sig.checked;
          if (stopCb.checked) { s.stop = { name: stopName.value, dwell_s: readNum(dwell, 20), board: readNum(board, 0), alight: readNum(alight, 0) }; }
          else s.stop = null;
          for (const x of [stopName, dwell, board, alight]) x.disabled = !stopCb.checked;
          schedulePreview();
        };
        for (const x of [nm, len, gr, vl, sf, wind, stopCb, stopName, dwell, board, alight, sig]) x.addEventListener("change", upd);
        const tools = h("div", { class: "rb-tools" },
          h("button", { type: "button", class: "ghost", title: "Üste ekle", onclick: () => { spec.segments.splice(i, 0, { ...s, stop: s.stop ? { ...s.stop } : null, name: "" }); drawSegments(); schedulePreview(); } }, "⧉"),
          h("button", { type: "button", class: "ghost", title: "Yukarı", disabled: i === 0 ? true : null, onclick: () => { [spec.segments[i - 1], spec.segments[i]] = [spec.segments[i], spec.segments[i - 1]]; drawSegments(); schedulePreview(); } }, "↑"),
          h("button", { type: "button", class: "ghost", title: "Aşağı", disabled: i === spec.segments.length - 1 ? true : null, onclick: () => { [spec.segments[i + 1], spec.segments[i]] = [spec.segments[i], spec.segments[i + 1]]; drawSegments(); schedulePreview(); } }, "↓"),
          h("button", { type: "button", class: "ghost danger", title: "Sil", onclick: () => { if (spec.segments.length > 1) { spec.segments.splice(i, 1); drawSegments(); schedulePreview(); } } }, "×"));
        return h("div", { class: "rb-seg" }, h("span", { class: "muted num" }, String(i + 1)), nm, len, gr, vl, sf, wind, stopCb, h("div", { class: "rb-stopname" }, stopName), dwell, board, alight, sig, tools);
      }));
  }

  let previewTimer = null;
  const schedulePreview = () => { clearTimeout(previewTimer); previewTimer = setTimeout(preview, 250); };
  const collect = () => ({ ...spec, name: nameIn.value.trim(), stem: stemIn.value.trim(), kind, description: descIn.value, start_elevation_m: readNum($i(elev0), 50) });
  const $i = wrap => wrap.querySelector("input");

  async function preview() {
    try {
      const r = await api.post("/api/route/preview", collect());
      const p = r.preview, t = p.totals;
      totals.replaceChildren(
        kp("Uzunluk", fmt(t.length_km, 2), "km"), kp("Tırmanış / iniş", `${fmt(Math.abs(t.climb_m), 0)} / ${fmt(Math.abs(t.descent_m), 0)}`, "m"),
        kp("Eğim aralığı", `${fmt(t.min_grade_pct, 1)} … ${fmt(t.max_grade_pct, 1)}`, "%"), kp("Durak", t.stops, ""), kp("Işık", t.signals, ""),
        kp("Biniş toplamı", fmt(t.boardings, 0), "yolcu"), kp("Ort. hız sınırı", fmt(t.mean_speed_limit_kmh, 0), "km/h"));
      const x = p.distance_km;
      chartBox.replaceChildren(
        lineChart({ x, series: [{ label: "Rakım", color: css("--s1"), y: p.elevation_m }], xLabel: "mesafe (km)", yLabel: "Rakım (m)", xFmt: v => fmt(v, 1), yFmt: v => fmt(v, 0) + " m", height: 220 }),
        lineChart({ x, series: [{ label: "Hız sınırı", color: css("--s4"), y: p.speed_limit_kmh }, { label: "Rüzgâr ×10", color: css("--s2"), y: p.headwind_mps.map(w => w * 10) }, { label: "Yüzey ×50", color: css("--s3"), y: p.surface_factor.map(f => f * 50) }],
          xLabel: "mesafe (km)", yLabel: "km/h · m/s×10 · ×50", xFmt: v => fmt(v, 1), yFmt: v => fmt(v, 0), height: 160 }),
        h("p", { class: "small muted", style: "margin:6px 0 0" },
          p.stops_km.length ? `Duraklar: ${p.stops_km.map((d, i) => `${p.stop_names[i] || "durak"} ${fmt(d, 2)} km`).join(" · ")}` : "Durak yok.",
          p.signals_km.length ? ` · Işıklar: ${p.signals_km.map(d => fmt(d, 2)).join(", ")} km` : ""));
      warnBox.replaceChildren();
    } catch (e) { warnBox.replaceChildren(notice("err", "Önizleme yapılamadı", [e.message])); }
  }
  const kp = (k, v, u) => h("div", {}, h("div", { class: "k" }, k), h("div", { class: "v" }, String(v), u ? h("small", {}, " " + u) : null));

  function loadSpec(s, isEditable) {
    spec = s; editable = isEditable;
    nameIn.value = s.name || ""; stemIn.value = isEditable && s.stem ? s.stem : ""; descIn.value = s.description || "";
    $i(elev0).value = String(s.start_elevation_m ?? 50).replace(".", ","); setKind(s.kind || "serbest");
    origStem = isEditable ? s.stem : null;
    drawSegments(); preview(); drawMore();
  }

  const save = async (asCopy) => {
    const body = collect();
    if (!body.name) throw new Error("Güzergâh adı boş olamaz.");
    if (asCopy) body.stem = "";
    const overwrite = !asCopy && origStem && body.stem && body.stem === origStem;
    try {
      const r = await api.post("/api/route", { ...body, overwrite });
      await U.overview(true); store.set("simRoute", r.stem);
      toast(`Kaydedildi: ${r.csv} ve ${r.yaml}`); location.hash = `#/guzergah/${encodeURIComponent(r.stem)}`;
    } catch (e) {
      if (/zaten var/.test(e.message) && confirm(`${e.message}\n\nÜzerine yazılsın mı?`)) {
        const r = await api.post("/api/route", { ...body, overwrite: true });
        await U.overview(true); toast(`Üzerine yazıldı: ${r.csv}`); location.hash = `#/guzergah/${encodeURIComponent(r.stem)}`;
      } else throw e;
    }
  };

  const moreBox = h("span", {});
  const scenarioItem = { label: "Bu güzergâhla senaryo kur", onclick: () => { const b = collect(); store.set("newScenarioRoute", b.stem || ""); location.hash = "#/senaryo/yeni"; } };
  const drawMore = () => moreBox.replaceChildren(origStem
    ? U.itemMenu("route", origStem, nameIn.value || origStem, { label: "Diğer", up: true, extra: [scenarioItem, "-"], after: () => { location.hash = "#/kutuphane"; } })
    : U.menu("Diğer", [scenarioItem], { up: true }));
  const tplBtn = (label, kindKey, q) => h("button", { type: "button", onclick: e => busy(e.currentTarget, async () => { const r = await api.get(`/api/route/template/${kindKey}${q || ""}`); loadSpec(r.spec, true); stemIn.value = ""; }) }, label);
  const left = h("div", { class: "stack" },
    h("div", { class: "panel stack" },
      h("div", { class: "row" }, h("b", {}, "Şablondan başla:"), tplBtn("Şehir içi hat (20 durak)", "sehir_ici"), tplBtn("Şehir içi kısa (8 durak)", "sehir_ici", "?stops=8"), tplBtn("Otoyol 45 km", "otoyol"), tplBtn("Boş", "serbest")),
      h("div", { class: "formgrid" }, field("Ad", nameIn), field("Dosya adı", stemIn, { hint: "library/route/<ad>.csv + .yaml; boş bırakılırsa addan üretilir." }),
        h("div", { class: "field" }, h("div", { class: "lab" }, "Tür"), kindBox), field("Başlangıç rakımı", elev0),
        h("div", { class: "wide" }, field("Açıklama", descIn)))),
    h("div", { class: "panel" }, h("div", { class: "row" }, h("h3", {}, "Bölümler"), h("span", { class: "spacer" }),
        h("button", { type: "button", onclick: () => { const last = spec.segments[spec.segments.length - 1]; spec.segments.push({ ...last, stop: null, signal: false, name: "" }); drawSegments(); schedulePreview(); } }, "+ Bölüm ekle")),
      h("p", { class: "small muted", style: "margin:4px 0 10px" }, "Durak ve ışık bölümün sonundadır. Rüzgâr: gidiş yönünde karşıdan esiyorsa +, arkadan −. Yüzey çarpanı yuvarlanma direncini ölçekler."),
      h("div", { class: "scrollx" }, segTable)),
    h("div", { class: "savebar" },
      h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, () => save(false)) }, origStem ? "Kaydet" : "Kütüphaneye kaydet"),
      h("button", { type: "button", onclick: e => busy(e.currentTarget, () => save(true)) }, "Kopya olarak kaydet"),
      moreBox,
      h("span", { class: "spacer" }), h("a", { class: "btn ghost", href: "#/kutuphane" }, "Vazgeç")));
  const right = h("div", { class: "stack" }, totals, chartBox, warnBox,
    h("div", { class: "panel" }, h("h3", {}, "Kütüphanedeki güzergâhlar"), routeList(U, { onOpen: s => { location.hash = `#/guzergah/${encodeURIComponent(s)}`; } })));
  root.append(h("div", { class: "rb-grid" }, left, right));

  if (stem && stem !== "yeni") {
    try { const r = await api.get(`/api/route/${encodeURIComponent(stem)}`); loadSpec(r.spec, r.editable); if (r.note) warnBox.append(notice("", "Not", [r.note])); }
    catch (e) { root.append(notice("err", "Güzergâh açılamadı", [e.message])); loadSpec({ name: "", kind: "serbest", start_elevation_m: 50, segments: [{ length_m: 1000, grade_pct: 0, speed_limit_kmh: 50, surface: "asfalt", headwind_mps: 0 }] }, true); }
  } else {
    const r = await api.get("/api/route/template/serbest"); loadSpec(r.spec, true); stemIn.value = "";
  }
  return root;
}

export function routeList(U, { onOpen, compact } = {}) {
  const { h, fmt, ov, api, toast } = U;
  const items = ov.route_items || [];
  if (!items.length) return h("p", { class: "muted small" }, "Henüz güzergâh yok.");
  const list = h("ul", { class: "itemlist" });
  items.forEach(r => {
    const badges = h("div", { class: "badges" }, h("span", { class: "badge plain" }, r.kind_tr || "içe aktarılmış"),
      r.has_wind ? h("span", { class: "badge plain" }, "rüzgâr") : null, r.has_surface ? h("span", { class: "badge plain" }, "yüzey") : null,
      r.boardings ? h("span", { class: "badge plain" }, `${r.boardings} biniş`) : null, r.error ? h("span", { class: "badge err" }, "hatalı dosya") : null, U.whenEl(r.modified));
    list.append(h("li", {}, h("div", { class: "main" }, h("div", { class: "name" }, r.name),
      h("div", { class: "sum" }, r.error ? r.error : `${fmt(r.length_km, 2)} km · ${r.stops} durak · ${r.signals} ışık · tırmanış ${fmt(r.climb_m, 0)} m · ${r.elev_min}–${r.elev_max} m · ort. sınır ${r.mean_speed_limit_kmh} km/h`),
      r.description && !compact ? h("div", { class: "sum muted" }, r.description) : null, badges),
      h("div", { class: "acts" },
        h("button", { type: "button", class: "btn", onclick: () => onOpen(r.stem) }, r.editable ? "Düzenle" : "Aç / kopyala"),
        h("button", { type: "button", class: "btn", onclick: () => { U.store.set("simRoute", r.stem); U.store.set("dutyKind", "route"); location.hash = "#/simulasyon"; } }, "Simüle et"),
        U.itemMenu("route", r.stem, r.name, { extra: [{ label: "Bu güzergâhla senaryo kur", onclick: () => { U.store.set("newScenarioRoute", r.stem); location.hash = "#/senaryo/yeni"; } }] }))));
  });
  return list;
}

/* ================================================================ scenarios */
export async function pageScenario(U, stem) {
  const { h, api, store, fmt, parseNum, seg, select, field, numInput, toast, busy, notice, ov, pageHead } = U;
  const root = h("div", {});
  root.append(pageHead("Senaryolar", "Aynı soruyu her araca sorun",
    "Senaryo = güzergâh (ya da standart çevrim) + araçlar + koşullar + yük + trafik. Kaydedilir, yeniden koşar, araçları aynı iz üzerinde yan yana karşılaştırır."));
  const vehicles = ov.vehicles.filter(v => !v.error);
  const routes = ov.route_items || [];
  const KINDS = ov.scenario_kinds || [["sehir_ici", "Şehir içi"], ["otoyol", "Otoyol"], ["serbest", "Serbest"]];
  const LOADS = ov.load_modes || [["profile", "Yol boyunca değişken"], ["constant", "Sabit yolcu"], ["stops", "Duraklardan"], ["mass", "Test kütlesi"]];
  const isNew = !stem || stem === "yeni";
  let sc = { name: "", kind: "serbest", description: "", duty: { route: store.get("newScenarioRoute") || (routes[0] && routes[0].stem) || "", direction: "gidis", laps: 1 },
    vehicles: vehicles.length ? [vehicles[0].stem] : [], conditions: { ambient_c: 20, solar: 0, night: false, hvac_off: false, headwind_mps: 0, pressure_hpa: 1013.25 },
    load: { mode: "profile", passengers: 40, mass_kg: 18000 }, traffic: "yok", overrides: {}, daily_km: 200, notes: "" };
  store.set("newScenarioRoute", "");
  if (!isNew) { try { sc = (await api.get(`/api/scenario/${encodeURIComponent(stem)}`)).scenario; sc.kind = sc.scenario_kind || sc.kind || "serbest"; } catch (e) { root.append(notice("err", "Senaryo açılamadı", [e.message])); } }

  /* ---------------- form ---------------- */
  const nameIn = h("input", { type: "text", value: sc.name, placeholder: "ör. Adana yaz zirve, 40 yolcu" });
  const descIn = h("textarea", {}, sc.description || "");
  let kind = sc.kind; const kindSeg = seg(KINDS, kind, v => (kind = v));
  let dutyKind = sc.duty.cycle && !sc.duty.route ? "cycle" : "route";
  const routeSel = select(routes.map(r => [r.stem, `${r.name} (${fmt(r.length_km, 1)} km, ${r.stops} durak)`]), sc.duty.route || (routes[0] && routes[0].stem));
  const cycleSel = select(ov.cycles.map(c => [c, c]), sc.duty.cycle || "cbdbus");
  let direction = sc.duty.direction || "gidis"; const dirSeg = seg([["gidis", "Gidiş"], ["donus", "Dönüş (ters yön)"]], direction, v => (direction = v));
  const laps = numInput(sc.duty.laps || sc.duty.repeat || 1);
  const dutyBox = h("div", { class: "stack" });
  const drawDuty = () => dutyBox.replaceChildren(
    seg([["route", "Güzergâh"], ["cycle", "Standart çevrim"]], dutyKind, v => { dutyKind = v; drawDuty(); }),
    dutyKind === "route" ? h("div", { class: "formgrid" }, field("Güzergâh", routeSel), h("div", { class: "field" }, h("div", { class: "lab" }, "Yön"), dirSeg), field("Tur sayısı", laps))
                         : h("div", { class: "formgrid" }, field("Çevrim", cycleSel), field("Tekrar", laps)));
  drawDuty();
  const vehBoxes = {};
  const vehList = h("div", { class: "sc-vehicles" }, ...vehicles.map(v => { const cb = h("input", { type: "checkbox", checked: sc.vehicles.includes(v.stem) ? true : null }); vehBoxes[v.stem] = cb;
    return h("label", { class: "check" }, cb, h("span", {}, h("b", {}, v.name), h("span", { class: "muted" }, ` · ${v.variant} · ${v.powertrain_tr}`))); }));
  const amb = numInput(sc.conditions.ambient_c, { unit: "°C" }); const sol = numInput(sc.conditions.solar, { unit: "W/m²" }); const wind = numInput(sc.conditions.headwind_mps, { unit: "m/s" });
  const pres = numInput(sc.conditions.pressure_hpa || 1013.25, { unit: "hPa" });
  const night = h("input", { type: "checkbox", checked: sc.conditions.night ? true : null }); const hvacOff = h("input", { type: "checkbox", checked: sc.conditions.hvac_off ? true : null });
  let loadMode = sc.load.mode || "profile"; const pax = numInput(sc.load.passengers ?? 40, { unit: "kişi" }); const mass = numInput(sc.load.mass_kg ?? 18000, { unit: "kg" });
  const loadBox = h("div", { class: "formgrid" });
  const drawLoad = () => loadBox.replaceChildren(h("div", { class: "field wide" }, h("div", { class: "lab" }, "Yük"), seg(LOADS, loadMode, v => { loadMode = v; drawLoad(); })),
    loadMode === "mass" ? field("Test kütlesi (boş kütle yerine)", mass, { hint: "Tartıdan: ön + arka aks." }) : loadMode === "stops" ? h("div", { class: "hint" }, "Yolcu sayısı güzergâhtaki durakların biniş/iniş değerlerinden gelir.") : field(loadMode === "profile" ? "Zirve yolcu" : "Yolcu", pax));
  drawLoad();
  let traffic = sc.traffic || "yok"; const trafficSeg = seg(ov.traffic_levels, traffic, v => (traffic = v));
  const roll = numInput(sc.overrides && sc.overrides.rolling_c0 != null ? sc.overrides.rolling_c0 : "", { placeholder: "aracın kendi değeri" });
  const daily = numInput(sc.daily_km || 200, { unit: "km/gün" });

  const collect = () => {
    const vs = Object.entries(vehBoxes).filter(([, cb]) => cb.checked).map(([k]) => k);
    const n = nameIn.value.trim(); if (!n) throw new Error("Senaryo adı boş olamaz.");
    const rv = parseNum(roll.querySelector("input").value);
    return { name: n, scenario_kind: kind, description: descIn.value,
      duty: dutyKind === "route" ? { route: routeSel.value, direction, laps: parseNum(laps.querySelector("input").value) || 1 } : { cycle: cycleSel.value, repeat: parseNum(laps.querySelector("input").value) || 1 },
      vehicles: vs, conditions: { ambient_c: parseNum(amb.querySelector("input").value) || 0, solar: parseNum(sol.querySelector("input").value) || 0, night: night.checked, hvac_off: hvacOff.checked,
        headwind_mps: parseNum(wind.querySelector("input").value) || 0, pressure_hpa: parseNum(pres.querySelector("input").value) || 1013.25 },
      load: { mode: loadMode, passengers: parseNum(pax.querySelector("input").value) || 0, mass_kg: parseNum(mass.querySelector("input").value) || null },
      traffic, overrides: Number.isNaN(rv) || !rv ? {} : { rolling_c0: rv }, daily_km: parseNum(daily.querySelector("input").value) || 200 };
  };
  const results = h("div", { class: "stack" });
  const runBtn = h("button", { class: "primary", type: "button" }, "Çalıştır ve karşılaştır");
  runBtn.addEventListener("click", () => busy(runBtn, async () => {
    const body = collect(); if (!body.vehicles.length) throw new Error("En az bir araç seçin.");
    results.replaceChildren(h("p", { class: "muted" }, "Hesaplanıyor…"));
    const r = await api.post("/api/scenario/run", { scenario: body, stem: isNew ? "" : stem });
    results.replaceChildren(resultTable(U, r, body));
  }));
  const save = async (overwrite) => {
    const body = collect();
    const r = await api.post("/api/scenario", { ...body, stem: isNew ? "" : stem, overwrite });
    await U.overview(true); toast(`Kaydedildi: ${r.saved}`); location.hash = `#/senaryo/${encodeURIComponent(r.stem)}`;
  };
  const form = h("div", { class: "stack" },
    h("div", { class: "panel stack" },
      h("div", { class: "formgrid" }, field("Ad", nameIn), h("div", { class: "field" }, h("div", { class: "lab" }, "Tür"), kindSeg), h("div", { class: "wide" }, field("Açıklama", descIn))),
      h("h3", {}, "Görev"), dutyBox,
      h("h3", {}, "Araçlar"), vehList,
      h("h3", {}, "Koşullar"), h("div", { class: "formgrid" }, field("Dış sıcaklık", amb), field("Güneş ışınımı", sol), field("Rüzgâr (karşıdan +)", wind, { hint: "Güzergâhın kendi rüzgârına eklenir." }), field("Basınç", pres),
        h("label", { class: "check" }, night, h("span", {}, "Gece")), h("label", { class: "check" }, hvacOff, h("span", {}, "Klima kapalı (test koşulu)"))),
      loadBox,
      h("div", { class: "field" }, h("div", { class: "lab" }, "Trafik"), trafficSeg, h("div", { class: "hint" }, "Yalnızca güzergâhta: ışıklar, kuyruklar, dur-kalk.")),
      h("div", { class: "formgrid" }, field("Lastik yuvarlanma katsayısı", roll, { hint: "Boş = aracın kendi değeri." }), field("Günlük mesafe", daily, { hint: "Batarya ömrü projeksiyonu için." }))),
    h("div", { class: "savebar" }, runBtn,
      h("button", { type: "button", onclick: e => busy(e.currentTarget, () => save(!isNew)) }, isNew ? "Senaryoyu kaydet" : "Değişiklikleri kaydet"),
      isNew ? null : h("button", { type: "button", onclick: e => busy(e.currentTarget, async () => { const n = prompt("Kopyanın adı:", `${nameIn.value} kopya`); if (n) { nameIn.value = n; await save(false); } }) }, "Kopya olarak kaydet"),
      h("span", { class: "spacer" }), h("a", { class: "btn ghost", href: "#/senaryo" }, "Listeye dön")),
    results);
  const listPanel = h("div", { class: "panel" }, h("div", { class: "row" }, h("h3", {}, "Kayıtlı senaryolar"), h("span", { class: "spacer" }), h("a", { class: "btn", href: "#/senaryo/yeni" }, "Yeni senaryo")), scenarioList(U));
  root.append(h("div", { class: "rb-grid" }, form, listPanel));
  return root;
}

export function scenarioList(U) {
  const { h, ov, api, toast, busy } = U;
  const items = ov.scenarios || [];
  if (!items.length) return h("p", { class: "muted small" }, "Henüz senaryo yok. Bir güzergâh ve araç seçip kaydedin.");
  const list = h("ul", { class: "itemlist" });
  items.forEach(s => list.append(h("li", {}, h("div", { class: "main" }, h("div", { class: "name" }, s.name),
    h("div", { class: "sum" }, s.error ? s.error : s.summary), h("div", { class: "badges" }, h("span", { class: "badge plain" }, s.kind_tr || ""), ...(s.vehicles || []).map(v => h("span", { class: "badge plain" }, v)), U.whenEl(s.modified)),
    s.description ? h("div", { class: "sum muted" }, s.description) : null),
    h("div", { class: "acts" },
      h("a", { class: "btn", href: `#/senaryo/${encodeURIComponent(s.stem)}` }, "Aç"),
      U.itemMenu("scenario", s.stem, s.name)))));
  return list;
}

function resultTable(U, r, body) {
  const { h, fmt, notice, store } = U;
  const rows = r.rows;
  const anyD = rows.some(x => x.powertrain === "diesel"), anyE = rows.some(x => x.powertrain !== "diesel");
  const best = Math.min(...rows.map(x => x.energy_kwh_per_km));
  const cols = ["Araç", "Tüketim", "Enerji kWh/km", "CO₂ g/km", "Menzil", "Süre", "Ort. hız", "Klima payı", ""];
  const open3d = x => {
    store.set("vehicle", x.vehicle);
    if (body.duty.route) { store.set("dutyKind", "route"); store.set("simRoute", body.duty.route); }
    else { store.set("dutyKind", "cycle"); store.set("simCycle", body.duty.cycle); }
    store.set("simAmb", body.conditions.ambient_c); store.set("simSol", body.conditions.solar); store.set("simHvacOff", body.conditions.hvac_off);
    store.set("simTraffic", body.traffic); store.set("simPax", body.load.passengers || 0);
    location.hash = "#/simulasyon";
  };
  const row = x => h("tr", {},
    h("td", {}, h("b", {}, x.name), h("div", { class: "small muted" }, x.variant), !x.achievable ? h("div", { class: "small", style: "color:var(--err)" }, "görevi tam takip edemedi") : null),
    h("td", { class: "n" }, x.powertrain === "diesel" ? `${fmt(x.l_per_100km, 2)} l/100 km` : `${fmt(x.energy_kwh_per_km, 3)} kWh/km`),
    h("td", { class: "n", style: x.energy_kwh_per_km === best && rows.length > 1 ? "color:var(--ok);font-weight:700" : "" }, fmt(x.energy_kwh_per_km, 3)),
    h("td", { class: "n" }, x.co2_g_per_km != null ? fmt(x.co2_g_per_km, 0) : "0 (TTW)"),
    h("td", { class: "n" }, `${fmt(x.range_km, 0)} km`),
    h("td", { class: "n" }, `${fmt(x.duration_min, 1)} dk`),
    h("td", { class: "n" }, `${fmt(x.mean_speed_kmh, 1)} km/h`),
    h("td", { class: "n" }, `%${fmt(100 * x.hvac_share, 0)}`),
    h("td", {}, h("div", { class: "row", style: "gap:4px" },
      h("a", { class: "btn", href: `/report/${x.report_id}`, target: "_blank", rel: "noopener" }, "Tam rapor"),
      h("button", { type: "button", class: "btn", onclick: () => open3d(x) }, "3B izle"),
      U.menu("İndir", [{ label: "Rapor (HTML)", href: `/report/${x.report_id}/html`, download: "" }, { label: "Zaman serisi (CSV)", href: `/report/${x.report_id}/csv`, download: "" }]))));
  const warns = rows.flatMap(x => x.warnings.map(w => h("li", {}, h("b", {}, x.name + ": "), w)));
  return h("div", { class: "stack" },
    h("div", { class: "row" }, h("b", {}, "Sonuç"), h("span", { class: "muted small" }, `${r.cycle} · ${r.environment}`)),
    anyD && anyE ? notice("", "Not", ["Elektrikli ve dizel araç aynı tabloda: kWh/km sütunu dizelde yakıtın alt ısıl değeridir, ikisini kıyaslamak için odur."]) : null,
    h("div", { class: "scrollx" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, cols.map(c => h("th", {}, c)))), h("tbody", {}, rows.map(row)))),
    warns.length ? h("details", { class: "tele-warn" }, h("summary", {}, "Uyarılar"), h("ul", {}, warns)) : null);
}
