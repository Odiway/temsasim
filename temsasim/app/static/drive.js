/* Free-drive page: edit a wide test road (length, grade and surface of each
   section), drive the selected bus on it from the keyboard, then hand the
   recorded drive to the full model.

   Live numbers come from drivephys.js and are labelled as estimates; the
   "Modelde hesapla" result comes from the same solver as every other run. */

import { buildRoad, createDriver, PRESETS } from "/static/drivephys.js";

const CAMS = [["chase", "Takip"], ["cockpit", "Sürücü"], ["side", "Yan"], ["free", "Serbest"], ["top", "Kuşbakışı"]];
const XRAY = [["off", "Kapalı"], ["cabin", "Kabin"], ["powertrain", "Güç aktarma"]];
// vehicle settings the page may change for the session: key, label, unit, decimals
const TUNE_FIELDS = [["kerb_mass_kg", "Boş ağırlık", "kg", 0], ["motor_peak_kw", "Motor tepe gücü", "kW", 0],
  ["motor_peak_nm", "Motor tepe torku", "Nm", 0], ["regen_kw", "Rejeneratif fren güç sınırı", "kW", 0],
  ["battery_kwh", "Batarya kapasitesi", "kWh", 0], ["soc_start_pct", "Başlangıç şarj durumu", "%", 0],
  ["rolling_c0", "Lastik yuvarlanma katsayısı", "", 4], ["drag_cd", "Hava direnci katsayısı (Cd)", "", 2]];
const SURF_COLOURS = { yeni_asfalt: "#2b2f33", asfalt: "#4a5056", beton: "#a3a5a1", eski_asfalt: "#6a6e72",
                       parke: "#9a8f80", arnavut: "#7c7064", stabilize: "#b5a27f" };

export async function pageDrive(U) {
  const { h, api, store, fmt, parseNum, seg, select, field, numInput, toast, busy, notice, ov, setCleanup, raster } = U;
  const root = h("div", { class: "simx drv" });
  const vehicles = ov.vehicles.filter(v => !v.error);
  if (!vehicles.length) {
    root.append(h("div", { class: "simx-empty-lib" }, notice("warn", "Önce bir araç kurun.", []), h("a", { class: "btn", href: "#/arac/yeni" }, "Araç kur")));
    return root;
  }

  /* ---------------- state ---------------- */
  let segs = store.get("driveRoad", PRESETS.tepe).map(s => ({ ...s }));
  let surfaces = [];          // from the server, with their rolling factors
  let P = null, road = null, drv = null, stage = null, raf = 0, last = 0, setupFor = null, paxNow = 0;
  const keys = { up: false, down: false, left: false, right: false };

  /* ---------------- setup panel (left) ---------------- */
  const vSel = select(vehicles.map(v => [v.stem, `${v.name} — ${v.variant}`]), store.get("vehicle", vehicles[0].stem));
  const pax = numInput(store.get("drvPax", 30), { unit: "kişi" });
  const amb = h("input", { type: "range", min: -25, max: 45, step: 1, value: store.get("drvAmb", 20), "aria-label": "Dış sıcaklık" });
  const ambOut = h("b", { class: "num" });
  amb.addEventListener("input", () => { ambOut.textContent = `${fmt(+amb.value, 0)} °C`; });
  ambOut.textContent = `${fmt(+amb.value, 0)} °C`;
  const night = h("input", { type: "checkbox", checked: store.get("drvNight", false) ? true : null });

  /* ---------------- vehicle settings: a session copy, the library file is untouched ---------------- */
  const tuneKey = () => `drvTune:${vSel.value}`;
  let tune = store.get(tuneKey(), {});
  const tuneBox = h("div", { class: "drv-tune" });
  const tuneSummary = h("span", { class: "muted small" });
  function drawTune() {
    const base = P && P.tune ? P.tune.base : null;
    if (!base) { tuneBox.replaceChildren(h("div", { class: "hint" }, "Araç hazırlanınca ayarlar burada görünür.")); return; }
    const lim = P.tune.limits;
    const rows = TUNE_FIELDS.map(([k, lab, unit, dec]) => {
      const locked = base.coastdown && (k === "rolling_c0" || k === "drag_cd");
      const inp = h("input", { type: "text", inputmode: "decimal", class: "numeric", value: fmt(tune[k] ?? base[k], dec),
        disabled: locked ? true : null, "aria-label": lab });
      const wrap = h("label", { class: "drv-tune-row" + (tune[k] != null ? " tuned" : "") },
        h("span", {}, lab, h("small", {}, locked ? "ölçülmüş yavaşlama eğrisi kullanılıyor" : `varsayılan ${fmt(base[k], dec)}${unit ? " " + unit : ""}`)),
        h("div", { class: "inputwrap" }, inp, unit ? h("span", { class: "suffix" }, unit) : null));
      inp.addEventListener("change", () => {
        const v = parseNum(inp.value), [lo, hi] = lim[k];
        if (v == null || Number.isNaN(v)) { delete tune[k]; }
        else if (v < lo || v > hi) { toast(`${lab}: ${fmt(lo, dec)}–${fmt(hi, dec)} aralığında olmalı.`, "err"); inp.value = fmt(tune[k] ?? base[k], dec); return; }
        else if (Math.abs(v - base[k]) < 1e-9) delete tune[k];
        else tune[k] = v;
        store.set(tuneKey(), tune); drawTune(); changed();
      });
      return wrap;
    });
    const n = Object.keys(tune).length;
    tuneSummary.textContent = n ? `${n} ayar değişti` : "varsayılan";
    tuneBox.replaceChildren(...rows,
      h("div", { class: "row", style: "gap:8px;margin-top:6px" },
        h("button", { type: "button", class: "ghost", disabled: n ? null : true, onclick: () => { tune = {}; store.set(tuneKey(), tune); drawTune(); changed(); } }, "Varsayılana dön"),
        h("a", { class: "small", href: `#/arac/${encodeURIComponent(vSel.value)}` }, "Aracı kalıcı olarak düzenle")),
      h("div", { class: "hint" }, "Bu ayarlar yalnızca bu sürüş sayfasında, aracın bir kopyasına uygulanır; kütüphanedeki araç değişmez. Modelde hesaplanan rapor da aynı ayarlarla çıkar."));
  }
  vSel.addEventListener("change", () => { tune = store.get(tuneKey(), {}); setupFor = null; tuneBox.replaceChildren(h("div", { class: "hint" }, "Uygulayınca yeni aracın ayarları görünür.")); changed(); });

  const segBox = h("div", { class: "drv-segs" });
  const profile = h("div", { class: "drv-profile" });
  const surfLabel = k => (surfaces.find(s => s.key === k) || {}).label || k;

  function drawSegs() {
    segBox.replaceChildren(
      h("div", { class: "drv-seg drv-seg-head" }, h("span", {}, "#"), h("span", {}, "Uzunluk (m)"), h("span", {}, "Eğim (%)"), h("span", {}, "Yüzey"), h("span", {})),
      ...segs.map((s, i) => {
        const len = h("input", { type: "text", inputmode: "decimal", class: "numeric", value: String(s.len), "aria-label": `Bölüm ${i + 1} uzunluğu` });
        const gr = h("input", { type: "text", inputmode: "decimal", class: "numeric", value: String(s.grade).replace(".", ","), "aria-label": `Bölüm ${i + 1} eğimi` });
        const sf = h("select", { "aria-label": `Bölüm ${i + 1} yüzeyi` },
          (surfaces.length ? surfaces : [{ key: s.surface, label: s.surface, factor: 1 }]).map(o =>
            h("option", { value: o.key, selected: o.key === s.surface ? true : null }, `${o.label} (×${fmt(o.factor, 2)})`)));
        len.addEventListener("change", () => { s.len = Math.max(20, Math.min(5000, parseNum(len.value) || 20)); len.value = String(s.len); changed(); });
        gr.addEventListener("change", () => { s.grade = Math.max(-15, Math.min(15, parseNum(gr.value) || 0)); gr.value = String(s.grade).replace(".", ","); changed(); });
        sf.addEventListener("change", () => { s.surface = sf.value; changed(); });
        const del = h("button", { type: "button", class: "ghost drv-del", title: "Bölümü sil", disabled: segs.length < 2 ? true : null,
          onclick: () => { segs.splice(i, 1); drawSegs(); changed(); } }, "×");
        return h("div", { class: "drv-seg" }, h("span", { class: "num muted" }, String(i + 1)), len, gr, sf, del);
      }),
      h("button", { type: "button", class: "drv-add", onclick: () => { const l = segs[segs.length - 1]; segs.push({ len: 300, grade: 0, surface: l ? l.surface : "asfalt" }); drawSegs(); changed(); } }, "+ Bölüm ekle"));
    drawProfile();
  }

  // side view of the road: elevation line over bands coloured by surface
  function drawProfile() {
    const r = buildRoad(segs, surfaces);
    const W = 268, H = 70, pad = 4;
    const span = Math.max(4, r.emax - r.emin);
    const x = d => pad + (W - 2 * pad) * d / r.L, y = e => H - 16 - (H - 26) * (e - r.emin) / span;
    let dpath = "";
    for (let d = 0; d <= r.L; d += Math.max(5, r.L / 200)) dpath += `${dpath ? "L" : "M"}${x(d).toFixed(1)},${y(r.elev(d)).toFixed(1)}`;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("class", "drv-svg");
    svg.innerHTML = `<path d="${dpath}" fill="none" stroke="currentColor" stroke-width="1.6"/>`;
    r.segs.forEach((s, k) => {
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", x(r.starts[k])); rect.setAttribute("y", H - 10);
      rect.setAttribute("width", Math.max(1, x(r.starts[k] + s.len) - x(r.starts[k]) - 0.5)); rect.setAttribute("height", 8);
      rect.setAttribute("fill", SURF_COLOURS[s.surface] || "#888"); rect.setAttribute("rx", 1);
      svg.appendChild(rect);
    });
    profile.replaceChildren(svg, h("div", { class: "drv-prof-lab" },
      h("span", {}, `${fmt(r.L / 1000, 2)} km`), h("span", {}, `rakım farkı ${fmt(r.emax - r.emin, 0)} m`)));
  }

  let dirty = false;
  const applyBtn = h("button", { class: "primary simx-run", type: "button" }, "Yolu ve aracı hazırla");
  function changed() { store.set("driveRoad", segs); drawProfile(); dirty = true; applyBtn.textContent = "Değişiklikleri uygula"; applyBtn.classList.add("drv-pulse"); }
  const presets = h("div", { class: "row drv-presets" },
    ...[["bulvar", "Düz bulvar"], ["tepe", "Yokuş ve iniş"], ["karma", "Karışık yüzey"]].map(([k, l]) =>
      h("button", { type: "button", class: "ghost", onclick: () => { segs = PRESETS[k].map(s => ({ ...s })); drawSegs(); changed(); } }, l)));

  const setup = h("aside", { class: "hud simx-setup drv-setup", "aria-label": "Yol ve araç" },
    h("div", { class: "hud-head" }, h("span", { class: "eyebrow" }, "Yol ve araç"), h("span", { class: "spacer" }),
      h("button", { type: "button", class: "ghost hud-x", onclick: () => toggleSetup(false) }, "Gizle")),
    h("div", { class: "hud-body stack" },
      field("Araç", vSel),
      h("div", { class: "grid2" }, field("Yolcu", pax),
        h("label", { class: "check", style: "align-self:end" }, night, h("span", {}, "Gece"))),
      h("div", { class: "field" }, h("div", { class: "lab" }, "Dış sıcaklık", h("span", { class: "spacer" }), ambOut), amb),
      h("details", { class: "simx-more drv-tune-box", open: Object.keys(tune).length ? true : null },
        h("summary", {}, "Araç ayarları ", tuneSummary), tuneBox),
      h("div", {}, h("div", { class: "lab simx-lab" }, "Yol"), presets, profile, segBox,
        h("div", { class: "hint" }, "Yüzey çarpanı yuvarlanma direncini ölçekler; değerler ağır taşıt lastikleri için tipik değerlerdir, ölçülmedi."))),
    h("div", { class: "hud-foot" }, applyBtn));
  const setupPill = h("button", { type: "button", class: "hud simx-pill", hidden: true, onclick: () => toggleSetup(true) }, "Yol ve araç");
  const toggleSetup = open => { setup.hidden = !open; setupPill.hidden = open; };

  /* ---------------- live panel (right) ---------------- */
  const big = (lab, unit) => { const b = h("b", {}, "—"); return { el: h("div", { class: "drv-big" }, b, h("span", { class: "unit" }, unit), h("div", { class: "lab" }, lab)), set: t => (b.textContent = t) }; };
  const row = lab => { const b = h("b", { class: "num" }, "—"), bar = h("i"); return { el: h("div", { class: "flow-row" }, h("span", {}, lab), h("div", { class: "flow-bar" }, bar), b), set(t, frac, col) { b.textContent = t; bar.style.width = `${Math.min(100, Math.abs(frac) * 100)}%`; bar.style.background = col; } }; };
  const stat = lab => { const b = h("b", {}, "—"); return { el: h("div", { class: "stat" }, h("span", {}, lab), b), set: t => (b.textContent = t) }; };
  const S = { speed: big("Hız", "km/h"), soc: big("Şarj durumu", "%"),
    trac: row("Çekiş / rejen"), hvac: row("Kabin içi termal yönetim"), aux: row("Yardımcı"), fric: row("Sürtünmeli fren"),
    dist: stat("Mesafe"), used: stat("Harcanan (tahmin)"), perkm: stat("Ortalama (tahmin)"), time: stat("Süre"),
    seg: stat("Yol bölümü"), grade: stat("Eğim"), surf: stat("Yüzey"), doors: stat("Kapılar") };
  const gearEl = h("div", { class: "drv-gear", title: "Vites (G ile değişir, otobüs dururken)" }, h("span", { "data-g": "D" }, "D"), h("span", { "data-g": "R" }, "R"));
  const msg = h("div", { class: "drv-msg", hidden: true });
  const resultBox = h("div", { class: "drv-result" });
  const calcBtn = h("button", { type: "button", class: "primary", disabled: true }, "Sürüşü modelde hesapla");
  const resetBtn = h("button", { type: "button" }, "Başa dön (R)");
  const tele = h("aside", { class: "hud simx-tele drv-tele", "aria-label": "Canlı veriler" },
    h("div", { class: "hud-head" }, h("span", { class: "eyebrow" }, "Canlı sürüş"), h("span", { class: "spacer" }), h("span", { class: "small muted" }, "tahmini değerler")),
    h("div", { class: "hud-body tele-body" },
      h("div", { class: "drv-bigs" }, S.speed.el, S.soc.el, gearEl), msg,
      h("div", { class: "tele-sec" }, h("div", { class: "sec-t" }, "Güç akışı"), S.trac.el, S.hvac.el, S.aux.el, S.fric.el),
      h("div", { class: "tele-sec" }, h("div", { class: "sec-t" }, "Yol"), h("div", { class: "stats" }, S.seg.el, S.grade.el, S.surf.el, S.doors.el)),
      h("div", { class: "tele-sec" }, h("div", { class: "sec-t" }, "Şu ana kadar"), h("div", { class: "stats" }, S.dist.el, S.time.el, S.used.el, S.perkm.el)),
      h("div", { class: "tele-sec" }, h("div", { class: "row" }, calcBtn, resetBtn), resultBox)));

  /* ---------------- camera / x-ray bars, key legend ---------------- */
  const camSeg = seg(CAMS, "chase", m => stage && stage.setCameraMode(m));
  const camBar = h("div", { class: "hud simx-cams" }, camSeg);
  const xraySeg = seg(XRAY, "off", m => stage && stage.setXray(m));
  const xrayBar = h("div", { class: "hud simx-cams simx-xray", title: "Röntgen görünümü (X tuşu)" }, h("span", { class: "simx-xray-lab" }, "Röntgen"), xraySeg);
  const legend = h("div", { class: "hud drv-keys" },
    ...[["W ↑", "gaz"], ["S ↓", "fren"], ["A D ← →", "direksiyon"], ["G", "ileri / geri vites"], ["E", "kapılar"], ["R", "başa dön"], ["1–5", "kamera"], ["X", "röntgen"]]
      .map(([k, t]) => h("span", {}, h("kbd", {}, k), " ", t)));
  const canvasHost = h("div", { class: "simx-canvas", tabindex: 0 });
  const loading = h("div", { class: "simx-loading", hidden: true }, h("div", { class: "simx-spinner" }), h("span", {}, "Hazırlanıyor…"));
  root.append(h("div", { class: "simx-stage" }, canvasHost, camBar, xrayBar, setup, setupPill, tele, legend, loading));

  /* ---------------- applying road + vehicle ---------------- */
  const params = () => {
    store.set("vehicle", vSel.value); store.set("drvPax", parseNum(pax.querySelector("input").value) || 0);
    store.set("drvAmb", +amb.value); store.set("drvNight", night.checked);
    return { vehicle: vSel.value, passengers: parseNum(pax.querySelector("input").value) || 0, ambient: +amb.value, night: night.checked, solar: 0,
             tune: { ...tune } };
  };
  async function apply() {
    const p = params();
    loading.hidden = false;
    try {
      const key = JSON.stringify(p);
      if (key !== setupFor) { P = await api.post("/api/drive/setup", p); surfaces = P.surfaces; setupFor = key; }
      road = buildRoad(segs, surfaces);
      road.surfaceLabel = surfLabel;
      drv = createDriver(P, road, { passengers: p.passengers });
      paxNow = p.passengers;
      if (stage) { stage.setEnv({ ambient: p.ambient, night: p.night }); stage.setRoad(road); }
      resultBox.replaceChildren(); calcBtn.disabled = true;
      dirty = false; applyBtn.textContent = "Yolu ve aracı hazırla"; applyBtn.classList.remove("drv-pulse");
      drawSegs(); drawTune();
      canvasHost.focus({ preventScroll: true });
    } finally { loading.hidden = true; }
  }
  applyBtn.addEventListener("click", () => busy(applyBtn, apply));
  resetBtn.addEventListener("click", () => { if (drv) { drv.reset(); resultBox.replaceChildren(); calcBtn.disabled = true; canvasHost.focus(); } });

  /* ---------------- the full model ---------------- */
  calcBtn.addEventListener("click", () => busy(calcBtn, async () => {
    const tr = drv.st.trace, p = params();
    const r = await api.post("/api/drive/simulate", { ...p, ...tr, name: "Serbest sürüş" });
    const k = r.kwh, live = drv.st.e_batt / 3.6e6;
    const line = (lab, v, note) => h("div", { class: "drv-res-row" }, h("span", {}, lab), h("b", { class: "num" }, `${fmt(v, 2)} kWh`), note ? h("span", { class: "muted small" }, note) : null);
    resultBox.replaceChildren(
      h("div", { class: "drv-res-head" },
        h("div", {}, h("div", { class: "drv-res-big" }, `${fmt(r.kpi.kwh_per_km, 2)}`, h("span", {}, " kWh/km")),
          h("div", { class: "small muted" }, `Model sonucu · ${fmt(r.kpi.distance_km, 2)} km · ${fmt(r.kpi.duration_min, 1)} dk`)),
        h("a", { class: "btn", href: `/report/${r.report_id}`, target: "_blank" }, "Tam rapor")),
      line("Bataryadan çekilen", k.battery_energy_kwh, `canlı tahmin ${fmt(live, 2)} kWh`),
      line("Çekiş", k.traction_kwh), line("Rejenle geri kazanılan", k.regen_recovered_kwh),
      line("Kabin içi termal yönetim", k.hvac_kwh), line("Yüksek voltaj ısıtma / soğutma", k.pack_thermal_kwh),
      line("Yardımcı sistemler", k.auxiliary_kwh),
      h("div", { class: "sec-t", style: "margin-top:8px" }, "Tekerlekte nereye gitti"),
      line("Yuvarlanma direnci (yüzey dahil)", k.rolling_kwh), line("Hava direnci", k.aero_kwh),
      line("Eğim (net)", k.grade_kwh), line("Sürtünmeli fren", k.friction_brake_kwh),
      r.warnings && r.warnings.length ? h("details", { class: "tele-warn" }, h("summary", {}, `${r.warnings.length} uyarı`), h("ul", {}, r.warnings.map(w => h("li", {}, w)))) : null);
  }));

  /* ---------------- keyboard ---------------- */
  const KEYMAP = { ArrowUp: "up", KeyW: "up", ArrowDown: "down", KeyS: "down", ArrowLeft: "left", KeyA: "left", ArrowRight: "right", KeyD: "right" };
  const typing = e => e.target.closest && e.target.closest("input, select, textarea");
  const onDown = e => {
    if (typing(e)) return;
    if (KEYMAP[e.code]) { keys[KEYMAP[e.code]] = true; e.preventDefault(); return; }
    if (e.repeat) return;
    if (e.code === "KeyE" && drv) { if (Math.abs(drv.st.v) > 0.5) toast("Kapılar ancak otobüs durunca açılır."); else drv.toggleDoors(); }
    else if (e.code === "KeyG" && drv) { if (drv.toggleReverse() == null) toast("Vites değiştirmek için önce durun."); }
    else if (e.code === "KeyR" && drv) resetBtn.click();
    else if (e.code === "KeyX" && stage) {
      const i = XRAY.findIndex(([k]) => k === xrayMode()), next = XRAY[(i + 1) % XRAY.length][0];
      stage.setXray(next); press(xraySeg, XRAY.findIndex(([k]) => k === next));
    } else if (/^Digit[1-5]$/.test(e.code) && stage) {
      const k = +e.code.slice(5) - 1; stage.setCameraMode(CAMS[k][0]); press(camSeg, k);
    }
  };
  const onUp = e => { if (KEYMAP[e.code]) keys[KEYMAP[e.code]] = false; };
  const onBlur = () => { for (const k in keys) keys[k] = false; };
  const press = (segEl, k) => [...segEl.querySelectorAll("button")].forEach((b, i) => b.setAttribute("aria-pressed", String(i === k)));
  const xrayMode = () => { const b = [...xraySeg.querySelectorAll("button")].findIndex(x => x.getAttribute("aria-pressed") === "true"); return XRAY[Math.max(0, b)][0]; };
  addEventListener("keydown", onDown); addEventListener("keyup", onUp); addEventListener("blur", onBlur);

  /* ---------------- loop ---------------- */
  let lastHud = 0;
  function loop(now) {
    raf = requestAnimationFrame(loop);
    const dt = Math.min(0.1, (now - last) / 1000 || 0); last = now;
    if (!drv || !stage) return;
    drv.step(keys, dt);
    const D = drv.st;
    const live = { v: Math.abs(D.v) * 3.6, p_batt: D.p_batt / 1e3, p_motor: D.p_motor / 1e3, p_hvac: P.hvac_w / 1e3, p_aux: D.p_aux / 1e3,
      p_thermal: P.pack_thermal_w / 1e3, regen_lost: D.p_fric / 1e3, doors: D.doors, accel: D.v < -0.01 ? -D.a : D.a, cabin: P.cabin_c, pack: P.pack_c,
      soc: D.soc * 100, pax: paxNow };
    stage.frame(D, live, dt);
    if (now - lastHud > 80) { hud(D); lastHud = now; }
  }
  function hud(D) {
    S.speed.set(fmt(Math.abs(D.v) * 3.6, 0)); S.soc.set(fmt(D.soc * 100, 1));
    for (const g of gearEl.children) g.classList.toggle("on", g.dataset.g === (D.dir < 0 ? "R" : "D"));
    const pm = D.p_motor / 1e3, peak = P.motor.peak_power_w / 1e3;
    S.trac.set(`${fmt(pm, 0)} kW`, pm / peak, pm < 0 ? "#2fbf71" : "#3987e5");
    S.hvac.set(`${fmt(P.hvac_w / 1e3, 1)} kW`, P.hvac_w / 30e3, "#eb6834");
    S.aux.set(`${fmt(D.p_aux / 1e3, 1)} kW`, D.p_aux / 15e3, "#8b9298");
    S.fric.set(`${fmt(D.p_fric / 1e3, 0)} kW`, D.p_fric / 1e3 / peak, "#d03b3b");
    S.dist.set(`${fmt(D.dist / 1000, 2)} km`);
    S.time.set(`${Math.floor(D.t / 60)}:${String(Math.floor(D.t % 60)).padStart(2, "0")}`);
    const kwh = D.e_batt / 3.6e6;
    S.used.set(`${fmt(kwh, 2)} kWh`);
    S.perkm.set(D.dist > 100 ? `${fmt(kwh / (D.dist / 1000), 2)} kWh/km` : "—");
    const k = road.segIndex(D.s), sg = road.segs[k];
    S.seg.set(`${k + 1} / ${road.segs.length}`);
    S.grade.set(`%${fmt(D.gradePct ?? 0, 1)}`);
    S.surf.set(`${surfLabel(sg.surface)} ×${fmt(D.kSurf ?? 1, 2)}`);
    S.doors.set(D.doors ? "açık" : "kapalı");
    const m = D.atEnd && D.s > road.L / 2 ? "Yolun sonu. R ile başa dönün ya da geri dönüş yapın."
      : D.scrape ? "Kaldırıma sürtünüyorsunuz." : D.doors ? "Kapılar açık: dış hava kabine giriyor. Kapatmak için E."
      : D.dir < 0 ? "Geri vites: en fazla 10 km/h. İleri vites için durup G'ye basın." : "";
    msg.hidden = !m; msg.textContent = m;
    calcBtn.disabled = D.trace.t.length < 20 || D.dist < 50;
  }

  /* ---------------- mount ---------------- */
  setCleanup(() => {
    cancelAnimationFrame(raf);
    removeEventListener("keydown", onDown); removeEventListener("keyup", onUp); removeEventListener("blur", onBlur);
    if (stage) stage.dispose(); stage = null;
  });
  drawSegs();
  queueMicrotask(async () => {
    try {
      const mod = await import("/static/drive3d.js");
      const sim = await import("/static/sim3d.js");
      if (!sim.webglAvailable()) throw new Error("WebGL yok");
      stage = mod.createDriveStage(canvasHost, { raster });
      await apply();
      raf = requestAnimationFrame(t => { last = t; loop(t); });
    } catch (e) {
      canvasHost.replaceChildren(h("div", { class: "simx-nogl" }, h("b", {}, "3B sürüş açılamadı."), h("span", {}, " " + e.message)));
      console.error(e);
    }
  });
  return root;
}
