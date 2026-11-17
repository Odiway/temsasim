/* "Kabin termal": the cabin as surfaces with material layers, the blowers,
   the people, the sun; the soak-and-cool-down transient the CAE team runs in
   CFD, computed here in a second and laid over the CFD planes and the A/C
   test. Saving writes cabin/<ad>.yaml; "Klima bileşenine bağla" makes an HVAC
   component derive its envelope from it. */

const LAYER_SEP = ";";

export async function pageCabin(U, stem) {
  const { h, api, store, fmt, parseNum, seg, select, field, numInput, toast, busy, notice, pageHead, lineChart, css, menu, dataTable } = U;
  const cat = await api.get("/api/cabins");
  const root = h("div", {});
  const enc = encodeURIComponent;
  const isNew = stem === "yeni";
  let cur = null, origStem = null;
  if (!stem) { const first = (cat.cabins || []).find(c => !c.error); stem = first ? first.stem : ""; }
  if (!isNew && stem) {
    try { const r = await api.get(`/api/cabin/${enc(stem)}`); cur = r.cabin; origStem = r.stem; }
    catch (e) { root.append(notice("err", "Kabin modeli açılamadı", [e.message])); }
  }
  if (!cur) {
    const first = (cat.cabins || []).find(c => !c.error);
    if (!isNew && !first) { cur = blank(cat.materials); }
    else if (isNew) cur = blank(cat.materials);
    else { const r = await api.get(`/api/cabin/${enc(first.stem)}`); cur = r.cabin; origStem = r.stem; }
  }
  const M = cur;                                       // the model being edited (plain object, mirrors CabinModel)
  M.materials = M.materials || {}; M.surfaces = M.surfaces || []; M.inlets = M.inlets || []; M.location = M.location || {}; M.reference = M.reference || {};

  const listSel = select([["yeni", "— yeni kabin modeli —"], ...(cat.cabins || []).map(c => [c.stem, c.error ? `${c.name} (hata)` : c.name])], origStem || "yeni");
  listSel.addEventListener("change", () => { location.hash = `#/kabin/${enc(listSel.value)}`; });
  root.append(pageHead("Kabin termal", "Kabin termal modeli",
    "Kabin, dıştan içe malzeme katmanlarından oluşan yüzeylerdir: tavan, yan panel, cam, taban. Üfleyici debisi ve üfleme sıcaklığı, yolcular, güneş ve dış hava eklenir; 55 °C'ye ısınmış kabinin soğuması CFD ile aynı koşulda bir saniyede hesaplanır ve CFD düzlemleri ile klima testinin üstüne çizilir. Aynı katmanlar klima bileşenine UA, cam alanı, iç ısıl kütle ve SHGC olarak aktarılır.",
    [listSel, h("a", { class: "btn", href: "#/kabin/yeni" }, "Yeni"), h("a", { class: "btn ghost", href: "/api/template/cabin", download: "" }, "Şablon")]));

  /* ---------------- helpers ---------------- */
  const num = (v, d = 1) => h("input", { type: "text", inputmode: "decimal", class: "numeric", value: v == null ? "" : fmt(v, d) });
  const read = (inp, dflt = 0) => { const n = parseNum(inp.value); return Number.isNaN(n) ? dflt : n; };
  const layersText = ls => (ls || []).map(l => `${l.material} ${fmt(l.thickness_mm, 1)}`).join(`${LAYER_SEP} `);
  const parseLayers = txt => txt.split(LAYER_SEP).map(s => s.trim()).filter(Boolean).map(s => {
    const [mat, th] = s.split(/\s+/); const t = parseNum(th || "");
    if (!M.materials[mat]) throw new Error(`Malzeme tanımsız: ${mat}`);
    if (Number.isNaN(t) || t <= 0) throw new Error(`Kalınlık okunamadı: "${s}"`);
    return { material: mat, thickness_mm: t };
  });
  const uOf = s => { let r = 1 / (s.h_out_w_m2k || 30) + 1 / (s.h_in_w_m2k || 4); for (const l of s.layers || []) { const m = M.materials[l.material]; if (m) r += l.thickness_mm / 1000 / Math.max(m.k_w_mk, 1e-6); } return 1 / r; };
  const cOf = s => (s.layers || []).reduce((a, l) => { const m = M.materials[l.material]; return a + (m ? m.density_kg_m3 * m.cp_j_kgk * l.thickness_mm / 1000 * s.area_m2 : 0); }, 0);

  /* ---------------- cabin panel ---------------- */
  const nameIn = h("input", { type: "text", value: M.name || "", placeholder: "ör. NGC Maraton kabin" });
  const descIn = h("textarea", {}, M.description || "");
  const F = { air: num(M.air_volume_m3, 1), imass: num(M.interior_mass_kg, 0), icp: num(M.interior_cp_j_kgk, 0), iarea: num(M.interior_contact_area_m2, 0), ih: num(M.interior_h_w_m2k, 1),
    occ: num(M.occupants, 0), occw: num(M.occupant_w, 0), ach: num(M.infiltration_ach, 2),
    lat: num(M.location.lat ?? 37, 2), lon: num(M.location.lon ?? 35.32, 2), day: num(M.location.day_of_year ?? 200, 0), hour: num(M.location.hour_local ?? 15, 1), utc: num(M.location.utc_offset_h ?? 3, 0), head: num(M.location.heading_deg ?? 0, 0) };
  const validated = h("input", { type: "checkbox", checked: M.validated ? true : null });
  const notesIn = h("textarea", {}, M.notes || "");
  const cabinPanel = h("div", { class: "panel stack" },
    h("div", { class: "formgrid" }, field("Ad", nameIn), h("div", { class: "wide" }, field("Açıklama", descIn))),
    h("div", { class: "section-title" }, "Hava, iç donanım, insanlar"),
    h("div", { class: "formgrid" },
      field("Hava hacmi", F.air, { unit: "m³" }), field("Koltuk ve iç donanım kütlesi", F.imass, { unit: "kg", hint: "Güneşin düştüğü ve havayla ısı alışverişi yapan iç kütle; CFD'de koltuklar tek tek ağ." }),
      field("İç donanım cp", F.icp, { unit: "J/kgK" }), field("İç donanım temas alanı", F.iarea, { unit: "m²" }), field("İç donanım h", F.ih, { unit: "W/m²K", hint: "Yalnızca taşınım; yüzeyler arası ışınım bu modelde yok." }),
      field("Yolcu + sürücü + hostes", F.occ, { unit: "kişi" }), field("Kişi başına ısı", F.occw, { unit: "W" }), field("Sızıntı", F.ach, { unit: "hava değişimi/saat" })),
    h("div", { class: "section-title" }, "Güneş: konum, gün, saat, araç yönü"),
    h("div", { class: "formgrid" }, field("Enlem", F.lat, { unit: "°" }), field("Boylam", F.lon, { unit: "°" }), field("Yılın günü", F.day), field("Yerel saat", F.hour, { unit: "h" }), field("UTC farkı", F.utc, { unit: "h" }),
      field("Araç yönü", F.head, { unit: "° (0 = kuzey)", hint: "Aracın burnunun baktığı pusula yönü; yan camlara düşen güneşi belirler." })),
    h("div", { class: "formgrid" }, h("label", { class: "check" }, validated, h("span", {}, "Ölçülmüş / doğrulanmış veri")), h("div", { class: "wide" }, field("Notlar", notesIn))));

  /* ---------------- materials ---------------- */
  const matTable = dataTable([{ label: "Anahtar" }, { label: "Ad" }, { label: "ρ kg/m³", n: true }, { label: "cp J/kgK", n: true }, { label: "k W/mK", n: true }, { label: "", acts: true }]);
  matTable.table.classList.add("cab-table");
  const drawMaterials = () => {
    matTable.body.replaceChildren(...Object.entries(M.materials).map(([k, m]) => {
      const lab = h("input", { type: "text", value: m.label || k }), d = num(m.density_kg_m3, 1), cp = num(m.cp_j_kgk, 1), kk = num(m.k_w_mk, 3);
      const upd = () => { m.label = lab.value; m.density_kg_m3 = read(d, 1); m.cp_j_kgk = read(cp, 1); m.k_w_mk = read(kk, 0.1); drawSurfaces(); };
      [lab, d, cp, kk].forEach(x => x.addEventListener("change", upd));
      return h("tr", {}, h("td", {}, h("code", {}, k)), h("td", { class: "name" }, lab), h("td", { class: "n" }, d), h("td", { class: "n" }, cp), h("td", { class: "n" }, kk),
        h("td", { class: "act" }, h("button", { type: "button", class: "ghost del", title: "Sil", onclick: () => { if (M.surfaces.some(s => (s.layers || []).some(l => l.material === k))) { toast("Bu malzeme bir yüzeyde kullanılıyor.", "err"); return; } delete M.materials[k]; drawMaterials(); } }, "×")));
    }));
  };
  drawMaterials();
  const matPanel = h("div", { class: "panel list" }, h("div", { class: "group-head" }, h("h3", {}, "Malzemeler"), h("span", { class: "spacer" }),
    h("button", { type: "button", class: "btn sm", onclick: () => { const k = prompt("Malzeme anahtarı (küçük harf, ör. foam):"); if (!k) return; const key = k.trim().toLowerCase().replace(/[^a-z0-9_]/g, "_"); if (M.materials[key]) { toast("Bu anahtar var.", "err"); return; } M.materials[key] = { label: k.trim(), density_kg_m3: 1000, cp_j_kgk: 1000, k_w_mk: 0.2 }; drawMaterials(); } }, "Malzeme ekle")), matTable.table);

  /* ---------------- surfaces ---------------- */
  const sTable = dataTable([{ label: "Yüzey" }, { label: "Yön" }, { label: "Alan m²", n: true }, { label: "Katmanlar (dıştan içe: malzeme mm; …)" }, { label: "α", n: true }, { label: "τ", n: true }, { label: "Dış" }, { label: "h dış", n: true }, { label: "h iç", n: true }, { label: "U · C" }, { label: "", acts: true }]);
  sTable.table.classList.add("cab-table");
  const ORI = cat.orientations, OUTK = cat.outer_kinds;
  function drawSurfaces() {
    sTable.body.replaceChildren(...M.surfaces.map((s, i) => {
      const nm = h("input", { type: "text", value: s.name || "" }); const ori = select(ORI, s.orientation || "side_left"); const area = num(s.area_m2, 2);
      const lay = h("input", { type: "text", value: layersText(s.layers), title: "ör. frp 3; insulation 43; abs 2,5" });
      const al = num(s.absorptivity ?? 0.8, 2), tr = num(s.transmissivity ?? 0, 2);
      const outer = select(OUTK, s.outer || "ambient"); const oc = num(s.outer_c ?? 80, 0); oc.hidden = (s.outer || "ambient") !== "fixed"; oc.style.width = "54px";
      const ho = num(s.h_out_w_m2k ?? 30, 0), hi = num(s.h_in_w_m2k ?? 4, 1);
      const uc = h("td", { class: "u" }, `${fmt(uOf(s), 2)} W/m²K · ${fmt(cOf(s) / 1e3, 0)} kJ/K`);
      const upd = () => {
        s.name = nm.value; s.orientation = ori.value; s.area_m2 = read(area, 1);
        try { s.layers = parseLayers(lay.value); lay.classList.remove("invalid"); } catch (e) { lay.classList.add("invalid"); toast(e.message, "err"); }
        s.absorptivity = read(al, 0.8); s.transmissivity = read(tr, 0); s.outer = outer.value; s.outer_c = read(oc, 80); oc.hidden = outer.value !== "fixed";
        s.h_out_w_m2k = read(ho, 30); s.h_in_w_m2k = read(hi, 4);
        uc.textContent = `${fmt(uOf(s), 2)} W/m²K · ${fmt(cOf(s) / 1e3, 0)} kJ/K`;
      };
      [nm, ori, area, lay, al, tr, outer, oc, ho, hi].forEach(x => x.addEventListener("change", upd));
      return h("tr", {}, h("td", { class: "name" }, nm), h("td", { class: "sel" }, ori), h("td", { class: "n" }, area), h("td", { class: "layers" }, lay), h("td", { class: "n" }, al), h("td", { class: "n" }, tr),
        h("td", { class: "sel" }, outer, oc), h("td", { class: "n" }, ho), h("td", { class: "n" }, hi), uc,
        h("td", { class: "act" }, h("div", { class: "acts" },
          h("button", { type: "button", class: "ghost del", title: "Kopyala", onclick: () => { M.surfaces.splice(i + 1, 0, JSON.parse(JSON.stringify(s))); drawSurfaces(); } }, "⧉"),
          h("button", { type: "button", class: "ghost del", title: "Sil", onclick: () => { M.surfaces.splice(i, 1); drawSurfaces(); } }, "×"))));
    }));
    const glass = M.surfaces.filter(s => (s.transmissivity || 0) > 0), tot = M.surfaces.reduce((a, s) => a + (s.area_m2 || 0), 0);
    sumLine.textContent = `${M.surfaces.length} yüzey · toplam ${fmt(tot, 1)} m² · cam ${fmt(glass.reduce((a, s) => a + s.area_m2, 0), 1)} m² · zarf ısıl kütlesi ${fmt(M.surfaces.reduce((a, s) => a + cOf(s), 0) / 1e3, 0)} kJ/K`;
  }
  const sumLine = h("span", { class: "small muted" });
  const surfPanel = h("div", { class: "panel list" }, h("div", { class: "group-head" }, h("h3", {}, "Yüzeyler"), sumLine, h("span", { class: "spacer" }),
    h("button", { type: "button", class: "btn sm", onclick: () => { M.surfaces.push({ name: "Yeni yüzey", orientation: "side_left", area_m2: 5, layers: [{ material: Object.keys(M.materials)[0] || "abs", thickness_mm: 3 }], absorptivity: 0.8, transmissivity: 0, outer: "ambient", outer_c: 80, h_out_w_m2k: 30, h_in_w_m2k: 4 }); drawSurfaces(); } }, "Yüzey ekle")),
    h("div", { class: "scrollx" }, sTable.table),
    h("p", { class: "small muted", style: "margin:8px 14px" }, "Katman yazımı: malzeme anahtarı ve kalınlık mm, noktalı virgülle; dıştan içe. Malzeme anahtarları yukarıdaki tabloda. α: güneş yutma (dış yüzey), τ: güneş geçirgenliği (cam)."));
  drawSurfaces();

  /* ---------------- inlets ---------------- */
  const inletBox = h("div", { class: "stack" });
  const drawInlets = () => inletBox.replaceChildren(...M.inlets.map((inl, i) => {
    const nm = h("input", { type: "text", value: inl.name || "" }), cnt = num(inl.count ?? 1, 0), mf = num(inl.mass_flow_kg_s, 3);
    const prof = h("textarea", {}, (inl.supply_c || []).map(p => `${fmt(p[0], 0)}${LAYER_SEP} ${fmt(p[1], 1)}`).join("\n"));
    const upd = () => { inl.name = nm.value; inl.count = Math.max(1, Math.round(read(cnt, 1))); inl.mass_flow_kg_s = read(mf, 0.1);
      inl.supply_c = prof.value.split("\n").map(l => l.trim()).filter(Boolean).map(l => { const [t, T] = l.split(LAYER_SEP); return [parseNum(t) || 0, parseNum(T) || 20]; }).sort((a, b) => a[0] - b[0]); };
    [nm, cnt, mf, prof].forEach(x => x.addEventListener("change", upd));
    return h("div", { class: "cab-supply" }, h("div", { class: "formgrid" }, field("Ad", nm), field("Adet", cnt), field("Debi (her biri)", mf, { unit: "kg/s" }),
      h("div", { class: "wide" }, field("Üfleme sıcaklığı: saniye; °C (satır başına bir nokta)", prof), h("div", { class: "row" }, h("button", { type: "button", class: "ghost sm", onclick: () => { M.inlets.splice(i, 1); drawInlets(); } }, "Üfleyiciyi sil")))));
  }));
  drawInlets();
  const inletPanel = h("div", { class: "panel list" }, h("div", { class: "group-head" }, h("h3", {}, "Üfleyiciler (klima girişleri)"), h("span", { class: "spacer" }),
    h("button", { type: "button", class: "btn sm", onclick: () => { M.inlets.push({ name: "Üfleyici", count: 1, mass_flow_kg_s: 0.15, supply_c: [[0, 40], [600, 25], [2400, 16]] }); drawInlets(); } }, "Üfleyici ekle")),
    h("div", { style: "padding:12px 14px" }, inletBox, h("p", { class: "small muted", style: "margin:8px 0 0" }, "Üfleme sıcaklığı eğrisi Maraton A/C testinden; ilk noktadan önce ve son noktadan sonra sabit tutulur.")));

  /* ---------------- collect / save ---------------- */
  const collect = () => {
    const n = nameIn.value.trim(); if (!n) throw new Error("Kabin adı boş olamaz.");
    M.name = n; M.description = descIn.value; M.validated = validated.checked; M.notes = notesIn.value;
    M.air_volume_m3 = read(F.air, 60); M.interior_mass_kg = read(F.imass, 0); M.interior_cp_j_kgk = read(F.icp, 1000); M.interior_contact_area_m2 = read(F.iarea, 100); M.interior_h_w_m2k = read(F.ih, 4);
    M.occupants = Math.round(read(F.occ, 0)); M.occupant_w = read(F.occw, 100); M.infiltration_ach = read(F.ach, 0.3);
    M.location = { lat: read(F.lat, 37), lon: read(F.lon, 35.32), day_of_year: Math.round(read(F.day, 200)), hour_local: read(F.hour, 15), utc_offset_h: read(F.utc, 3), heading_deg: read(F.head, 0) };
    return M;
  };
  const save = async (asCopy) => {
    const body = collect();
    const stemOut = asCopy ? "" : (origStem || "");
    const overwrite = !asCopy && !!origStem;
    const r = await api.post("/api/cabin", { cabin: body, stem: stemOut, overwrite });
    toast(`Kaydedildi: ${r.saved}`); location.hash = `#/kabin/${enc(r.stem)}`;
  };
  const hvSel = select([["", "— klima bileşeni seç —"], ...(cat.hvacs || []).map(x => [x.stem, `${x.name}${x.cabin_model ? " · bağlı: " + x.cabin_model.replace("cabin/", "") : ""}`])], "");
  const linkBtn = h("button", { type: "button", disabled: origStem ? null : true, onclick: e => busy(e.currentTarget, async () => {
    if (!hvSel.value) throw new Error("Önce bir klima bileşeni seçin.");
    const r = await api.post(`/api/cabin/${enc(origStem)}/link`, { hvac: hvSel.value });
    toast(`${r.hvac} bağlandı: UA ${fmt(r.ua_w_per_k, 0)} W/K, cam ${fmt(r.glazing_area_m2, 1)} m², iç kütle ${fmt(r.interior_thermal_mass_j_per_k / 1e3, 0)} kJ/K`);
    linkNote.textContent = `Bağlı: hvac/${r.hvac}.yaml → UA ${fmt(r.ua_w_per_k, 0)} W/K · SHGC ${fmt(r.shgc, 2)}`;
  }) }, "Klima bileşenine bağla");
  const linkNote = h("span", { class: "small muted" }, origStem ? ((cat.hvacs || []).filter(x => x.cabin_model.endsWith(origStem + ".yaml")).map(x => `bağlı: hvac/${x.stem}.yaml`).join(", ") || "henüz bir klima bileşenine bağlı değil") : "önce kaydedin");
  const bar = h("div", { class: "savebar" },
    h("button", { class: "primary", type: "button", onclick: e => busy(e.currentTarget, () => save(false)) }, origStem ? "Kaydet" : "Kütüphaneye kaydet"),
    origStem ? h("button", { type: "button", onclick: e => busy(e.currentTarget, async () => { const n = prompt("Kopyanın adı:", `${nameIn.value} kopya`); if (n) { nameIn.value = n; await save(true); } }) }, "Kopya olarak kaydet") : null,
    (hvSel.style.width = "auto", hvSel.style.maxWidth = "280px", hvSel), linkBtn, linkNote, h("span", { class: "spacer" }),
    origStem ? menu("Diğer", [{ label: "Dosyayı indir", href: `/api/template/cabin`, download: "", hint: "şablon" },
      { label: "Sil", danger: true, onclick: async () => { if (!confirm(`${M.name} çöp kutusuna taşınsın mı?`)) return; await api.post(`/api/cabin/${enc(origStem)}/delete`); toast("Çöp kutusuna taşındı."); location.hash = "#/kabin"; } }], { up: true }) : null);

  /* ---------------- scenario + results ---------------- */
  const SC = { amb: num(40, 0), init: num(55, 0), dur: num(40, 0), dt: num(10, 0), occ: num(null, 0), off: num(0, 1) };
  const solarCb = h("input", { type: "checkbox", checked: true }), hvacCb = h("input", { type: "checkbox", checked: true });
  const runBtn = h("button", { class: "primary", type: "button" }, "Hesapla");
  const out = h("div", { class: "stack" });
  const scenPanel = h("div", { class: "panel stack" }, h("div", { class: "section-title" }, "Senaryo: ısınmış kabinin soğuması"),
    h("div", { class: "formgrid" }, field("Dış hava", SC.amb, { unit: "°C" }), field("Başlangıç (soak)", SC.init, { unit: "°C", hint: "Bütün yüzeyler ve hava bu sıcaklıkta başlar." }), field("Süre", SC.dur, { unit: "dk" }), field("Zaman adımı", SC.dt, { unit: "s" }),
      field("Kişi sayısı (boş = model)", SC.occ), field("Üfleme sıcaklığı kayması", SC.off, { unit: "°C", hint: "Ölçüm belirsizliği denemesi: eğriye eklenir." }),
      h("label", { class: "check" }, solarCb, h("span", {}, "Güneş yükü")), h("label", { class: "check" }, hvacCb, h("span", {}, "Klima açık"))),
    h("div", { class: "row" }, runBtn, h("span", { class: "small muted" }, "CFD: 40 °C dış, 55 °C soak, 40 dk, Adana 200. gün 15:00")));
  runBtn.addEventListener("click", () => busy(runBtn, async () => {
    const body = collect();
    const sc = { ambient_c: read(SC.amb, 40), initial_c: read(SC.init, 55), duration_min: read(SC.dur, 40), dt_s: read(SC.dt, 10), solar: solarCb.checked, hvac: hvacCb.checked,
      occupants: SC.occ.value.trim() === "" ? "" : Math.round(read(SC.occ, 0)), supply_offset_c: read(SC.off, 0) };
    const r = await api.post("/api/cabin/run", { cabin: body, scenario: sc });
    showResult(r, sc);
  }));
  function showResult(r, sc) {
    const R = r.result, tmin = R.t_s.map(t => t / 60);
    const cfd = (r.reference || {}).cfd, test = (r.reference || {}).test;
    const at = R.at_minutes;
    const cmpS = (r.compare.series || []).find(x => x.key === "cfd");
    const diffAt = tm => { const p = cmpS && cmpS.points.find(q => q.t_min === tm); return p ? p.diff_c : null; };
    const kpis = h("div", { class: "cab-kpis" }, [10, 20, 30, 40].filter(tm => at[tm] != null).map(tm => {
      const d = diffAt(tm);
      return h("div", { class: "cab-kpi" }, h("div", { class: "k" }, `${tm}. dakika`), h("div", { class: "v" }, `${fmt(at[tm], 1)} °C`),
        d == null ? null : h("div", { class: "d " + (Math.abs(d) <= 2.5 ? "ok" : "bad") }, `CFD'ye göre ${d >= 0 ? "+" : "−"}${fmt(Math.abs(d), 1)} °C`));
    }));
    const crit = h("div", { class: "cab-crit" }, (r.compare.criteria || []).map(c => h("span", { class: c.ok ? "ok" : "bad" }, `${c.label ? c.label + " " : ""}${fmt(c.at_min, 0)} dk: ${fmt(c.sim_c, 1)} °C ${c.ok ? "≤" : ">"} ${fmt(c.max_c, 0)} °C`)));
    const onGrid = (times, vals) => { const y = new Array(tmin.length).fill(null); times.forEach((tm, k) => { let j = 0; for (let i = 1; i < tmin.length; i++) if (Math.abs(tmin[i] - tm) < Math.abs(tmin[j] - tm)) j = i; y[j] = vals[k]; }); return y; };
    const series = [{ label: "Model: kabin havası", color: css("--s1"), y: R.air_c }, { label: "Üfleme", color: css("--s4"), y: R.supply_c, thin: true, dashed: true },
      { label: "İç donanım", color: css("--s3"), y: R.interior_c, thin: true }];
    if (cfd && cfd.times_min) series.push({ label: cfd.label || "CFD", color: css("--s2"), y: onGrid(cfd.times_min, cfd.avg_c), markers: true, markersOnly: true });
    if (test && test.times_min) series.push({ label: test.label || "Test", color: css("--s6"), y: onGrid(test.times_min, test.avg_c), markers: true, markersOnly: true });
    const chart1 = lineChart({ x: tmin, series, xLabel: "dakika", yLabel: "Sıcaklık (°C)", xFmt: v => fmt(v, 0), yFmt: v => `${fmt(v, 1)} °C`, height: 300 });
    const names = Object.keys(R.inner_c);
    const chart2 = lineChart({ x: tmin, series: names.map((n, i) => ({ label: n, color: [css("--s1"), css("--s2"), css("--s3"), css("--s4"), css("--s5"), css("--s6"), "#888", "#b8860b", "#2a9d8f", "#e76f51", "#6d597a", "#355070", "#8338ec", "#ff006e", "#3a86ff", "#fb5607", "#06d6a0"][i % 17], y: R.inner_c[n], thin: true })), xLabel: "dakika", yLabel: "İç yüzey sıcaklıkları (°C)", xFmt: v => fmt(v, 0), yFmt: v => `${fmt(v, 1)} °C`, height: 300 });
    const chart3 = lineChart({ x: tmin, series: [{ label: "Klima (üfleme)", color: css("--s1"), y: R.q_supply_w.map(v => v / 1e3) }, { label: "Duvarlardan havaya", color: css("--s2"), y: R.q_walls_w.map(v => v / 1e3) }, { label: "İç donanımdan havaya", color: css("--s3"), y: R.q_interior_w.map(v => v / 1e3) }],
      xLabel: "dakika", yLabel: "Isı akışı (kW, havaya +)", xFmt: v => fmt(v, 0), yFmt: v => `${fmt(v, 1)} kW`, height: 240 });
    const lump = r.hvac_fields, L2 = r.lumped;
    const lumpT = h("table", { class: "data cab-lump" }, h("tbody", {},
      [["UA (zarf)", `${fmt(L2.ua_w_per_k, 0)} W/K`], ["Cam alanı · U", `${fmt(lump.glazing_area_m2, 1)} m² · ${fmt(lump.u_glazing_w_per_m2k, 2)} W/m²K`], ["Opak alan · U", `${fmt(lump.opaque_area_m2, 1)} m² · ${fmt(lump.u_opaque_w_per_m2k, 2)} W/m²K`],
       ["İç ısıl kütle", `${fmt(lump.interior_thermal_mass_j_per_k / 1e3, 0)} kJ/K`], ["SHGC (cam)", fmt(lump.shgc, 2)], ["Hava hacmi", `${fmt(lump.cabin_volume_m3, 1)} m³`], ["Toplam üfleme debisi", `${fmt(L2.supply_mass_flow_kg_s, 2)} kg/s`],
       ["Güneş", `yükseklik ${fmt(R.sun.altitude_deg, 0)}°, azimut ${fmt(R.sun.azimuth_deg, 0)}°, DNI ${fmt(R.sun.i_dn, 0)} W/m²; cama giren ${fmt(R.sun.transmitted_w / 1e3, 1)} kW`], ["Yolcu ısısı", `${fmt(R.occupants_w / 1e3, 1)} kW`],
       ["Ağ", `${R.nodes} düğüm · ${fmt(R.capacitance_total_j_per_k / 1e3, 0)} kJ/K`]].map(([k, v]) => h("tr", {}, h("td", {}, k), h("td", { class: "n" }, v)))));
    out.replaceChildren(
      h("div", { class: "panel stack" }, h("div", { class: "section-title" }, "Sonuç"), kpis, crit.children.length ? crit : null,
        cmpS && cmpS.rmse_c != null ? h("p", { class: "small muted", style: "margin:0" }, `CFD hacim ortalamasına göre RMSE ${fmt(cmpS.rmse_c, 1)} °C. Model tek boyutlu ağdır: yüzeyler arası ışınım ve hava akışı dağılımı CFD'de çözülür, burada yoktur; alanlar ve araç yönü dosyada kabul olarak işaretlidir.`) : null,
        chart1),
      h("div", { class: "panel" }, h("div", { class: "section-title" }, "Isı akışları"), chart3),
      h("div", { class: "panel" }, h("div", { class: "section-title" }, "İç yüzeyler"), chart2),
      h("div", { class: "panel" }, h("div", { class: "section-title" }, "Klima bileşenine aktarılan değerler"), lumpT,
        h("p", { class: "small muted", style: "margin:8px 0 0" }, "Tek düğümlü klima modeli bu değerleri kullanır: katmanlar U'ya, kütleler iç ısıl kütleye, geçirgenlik SHGC'ye çökertilir (iç film taşınım + ışınım 8 W/m²K). Bağlı bileşende dosyadaki zarf alanları yerine bunlar geçerlidir.")));
  }

  root.append(h("div", { class: "cab-grid" }, h("div", { class: "stack" }, cabinPanel, surfPanel, matPanel, inletPanel, bar), h("div", { class: "stack" }, scenPanel, out)));
  if (origStem) setTimeout(() => runBtn.click(), 60);
  return root;
}

function blank(materials) {
  return { name: "", description: "", materials: JSON.parse(JSON.stringify(materials || {})), surfaces: [
    { name: "Tavan", orientation: "roof", area_m2: 30, layers: [{ material: "frp", thickness_mm: 3 }, { material: "insulation", thickness_mm: 40 }, { material: "abs", thickness_mm: 2.5 }], absorptivity: 0.8, transmissivity: 0, outer: "ambient", outer_c: 80, h_out_w_m2k: 30, h_in_w_m2k: 4 },
    { name: "Yan camlar", orientation: "side_right", area_m2: 20, layers: [{ material: "glass", thickness_mm: 6.4 }], absorptivity: 0.1, transmissivity: 0.8, outer: "ambient", outer_c: 80, h_out_w_m2k: 30, h_in_w_m2k: 4 }],
    air_volume_m3: 56, interior_mass_kg: 250, interior_cp_j_kgk: 1075, interior_contact_area_m2: 100, interior_h_w_m2k: 4, occupants: 50, occupant_w: 100,
    inlets: [{ name: "A/C üfleyicileri", count: 12, mass_flow_kg_s: 0.15, supply_c: [[0, 41], [300, 42], [600, 27.5], [1000, 22], [1500, 18.5], [2400, 16], [3600, 13.8]] }],
    infiltration_ach: 0.3, location: { lat: 37.0, lon: 35.32, day_of_year: 200, hour_local: 15, utc_offset_h: 3, heading_deg: 90 }, reference: {}, validated: false, notes: "" };
}
