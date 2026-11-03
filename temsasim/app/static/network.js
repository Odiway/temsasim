/* Line and timetable page: one bus line for one day.

   Left: the line, the vehicle, the conditions and the timetable (headway and
   traffic per hour band) with a what-if column. Right: the day's numbers for
   the base timetable and the what-if side by side -- fleet, occupancy,
   waiting, energy, charging -- an hour-by-hour occupancy chart, the fleet
   each hour band needs, and the load along the route at the peak.

   Every trip in these numbers was driven through the full vehicle model
   (temsasim/network.py); nothing here is a rule of thumb. */

const TRAFFIC = [["yok", "Yok"], ["akici", "Akıcı"], ["orta", "Orta"], ["yogun", "Yoğun"], ["sikisik", "Sıkışık"]];
const C = { within: "#2fbf71", above: "#d03b3b", below: "#9fb0bc", base: "#3987e5", scn: "#ffb000" };

export async function pageNetwork(U) {
  const { h, api, store, fmt, parseNum, seg, select, field, numInput, toast, busy, notice, ov, pageHead, setCleanup } = U;
  const root = h("div", {});
  const vehicles = ov.vehicles.filter(v => !v.error);
  const { lines } = await api.get("/api/network/lines");
  const good = lines.filter(l => !l.error);
  root.append(pageHead("Hat ve sefer", "Bir hattın bir günü",
    "Sefer aralığını, trafiği ve talebi değiştirin; kaç otobüs gerektiğini, doluluğu, bekleme süresini ve günlük enerjiyi aynı modelle görün."));
  if (!vehicles.length || !good.length) {
    root.append(notice("warn", !vehicles.length ? "Önce bir araç kurun." : "Kütüphanede hat yok.",
      ["library/line/ klasörüne bir hat dosyası ekleyin (örnek: adana_ornek.yaml)."]));
    return root;
  }

  /* ---------------- setup ---------------- */
  let line = good.find(l => l.stem === store.get("netLine")) || good[0];
  const lineSel = select(good.map(l => [l.stem, `${l.code} · ${l.name}`]), line.stem);
  const vSel = select(vehicles.map(v => [v.stem, `${v.name} — ${v.variant}`]), store.get("vehicle", vehicles[0].stem));
  const amb = h("input", { type: "range", min: -25, max: 45, step: 1, value: store.get("netAmb", 20), "aria-label": "Dış sıcaklık" });
  const ambOut = h("b", { class: "num" }); const updAmb = () => (ambOut.textContent = `${fmt(+amb.value, 0)} °C`); amb.addEventListener("input", updAmb); updAmb();
  const night = h("input", { type: "checkbox" });
  const demand = h("input", { type: "range", min: 30, max: 200, step: 5, value: 100, "aria-label": "Talep" });
  const demandOut = h("b", { class: "num" }); const updDem = () => (demandOut.textContent = `%${demand.value}`); demand.addEventListener("input", updDem); updDem();
  const capIn = numInput("", { unit: "kişi", placeholder: "aracın kapasitesi" });
  const bandsBox = h("div", { class: "net-bands" });
  const exampleNote = h("div", { class: "net-example", hidden: true });
  let edits = {};            // band index -> { headway, traffic }

  function drawBands() {
    edits = {};
    bandsBox.replaceChildren(
      h("div", { class: "net-band head" }, h("span", {}, "Saat"), h("span", {}, "Aralık dk"), h("span", {}, "Trafik (deneme)")),
      ...line.bands.map((b, i) => {
        const hw = h("input", { type: "text", inputmode: "decimal", class: "numeric", value: String(b.headway_min), "aria-label": `Bant ${i + 1} sefer aralığı` });
        const tr = h("select", { "aria-label": `Bant ${i + 1} trafik` }, TRAFFIC.map(([k, l]) => h("option", { value: k, selected: k === b.traffic ? true : null }, l)));
        const row = h("div", { class: "net-band" }, h("span", { class: "lbl" }, `${String(b.from).padStart(2, "0")}–${String(b.to).padStart(2, "0")}`), hw, tr);
        const upd = () => {
          const v = parseNum(hw.value);
          edits[i] = { headway: v && v !== b.headway_min ? v : null, traffic: tr.value !== b.traffic ? tr.value : null };
          row.classList.toggle("changed", !!(edits[i].headway || edits[i].traffic));
        };
        hw.addEventListener("change", upd); tr.addEventListener("change", upd);
        return row;
      }));
    exampleNote.hidden = !line.example;
    exampleNote.textContent = line.example ? "ÖRNEK VERİ: güzergâh, duraklar ve yolcu sayıları temsilidir, ölçüm değildir. Gerçek GTFS / AVL / bilet verisi geldiğinde değiştirilir." : "";
  }
  lineSel.addEventListener("change", () => { line = good.find(l => l.stem === lineSel.value); store.set("netLine", line.stem); drawBands(); });
  drawBands();

  const runBtn = h("button", { class: "primary", type: "button" }, "Günü hesapla");
  const setup = h("div", { class: "panel stack net-setup" },
    field("Hat", lineSel), exampleNote,
    field("Araç", vSel),
    h("div", { class: "field" }, h("div", { class: "lab" }, "Dış sıcaklık", h("span", { class: "spacer" }), ambOut), amb),
    h("label", { class: "check" }, night, h("span", {}, "Gece")),
    h("div", { class: "field" }, h("div", { class: "lab" }, "Talep (zirveye göre)", h("span", { class: "spacer" }), demandOut), demand),
    field("Kapasite", capIn, { hint: "Boş bırakılırsa aracın oturan + ayakta kapasitesi." }),
    h("div", {}, h("div", { class: "lab" }, "Sefer aralığı ve trafik"),
      h("div", { class: "hint" }, "Soldaki değerler hattın tarifesi. Değiştirdikleriniz “deneme” olarak aynı günde yanına hesaplanır."), bandsBox),
    runBtn);

  /* ---------------- results ---------------- */
  const results = h("div", { class: "stack" }, h("p", { class: "muted" }, "Hesaplanınca sonuçlar burada görünür."));
  root.append(h("div", { class: "net-grid" }, setup, results));

  const params = () => {
    store.set("vehicle", vSel.value); store.set("netAmb", +amb.value);
    return { line: line.stem, vehicle: vSel.value, ambient: +amb.value, night: night.checked, solar: 0,
      demand_scale: +demand.value / 100, capacity: parseNum(capIn.querySelector("input").value) || null };
  };
  runBtn.addEventListener("click", () => busy(runBtn, async () => {
    const p = params();
    const base = await api.post("/api/network/simulate", p);
    const hw = {}, tr = {};
    for (const [i, e] of Object.entries(edits)) { if (e.headway) hw[i] = e.headway; if (e.traffic) tr[i] = e.traffic; }
    const changed = Object.keys(hw).length || Object.keys(tr).length;
    const scn = changed ? await api.post("/api/network/simulate", { ...p, headways: hw, traffic: tr }) : null;
    render(base.result, scn && scn.result, base.line);
  }));

  function render(B, S, L) {
    const kpi = (lab, key, unit, d = 0, lowerIsBetter = true) => {
      const b = B[key], s = S ? S[key] : null;
      let delta = null;
      if (S && typeof b === "number") {
        const diff = s - b, cls = diff === 0 ? "" : (diff < 0) === lowerIsBetter ? "good" : "bad";
        delta = h("span", { class: "delta " + cls }, `${diff > 0 ? "+" : ""}${fmt(diff, d)} deneme`);
      }
      return h("div", { class: "kpi" + (S ? " scn" : "") }, h("span", { class: "lab" }, lab),
        h("div", {}, h("b", {}, fmt(S ? s : b, d)), h("span", { class: "unit" }, unit)),
        S ? h("div", { class: "small muted" }, `temel ${fmt(b, d)}`) : null, delta);
    };
    const dz = B.powertrain === "diesel";
    if (dz) { for (const X of [B, S].filter(Boolean)) { X.fuel_l_per_bus_day = X.fleet ? X.fuel_l_per_day / X.fleet : 0; X.fuel_l_per_pax = X.passengers_per_day ? X.fuel_l_per_day / X.passengers_per_day : 0; } }
    const tiles = h("div", { class: "kpis" },
      kpi("Filo (zirve)", "fleet", "otobüs"), kpi("Sefer / gün", "trips_per_day", "", 0, false),
      kpi("Yolcu / gün", "passengers_per_day", "", 0, false), kpi("Binemeyen / gün", "denied_per_day", "kişi"),
      kpi("Ortalama bekleme", "mean_wait_min", "dk", 1), kpi("Trafik kaybı", "traffic_loss_min_per_day", "dk/gün", 0),
      dz ? kpi("Yakıt / gün", "fuel_l_per_day", "l", 0) : kpi("Enerji / gün", "kwh_per_day", "kWh", 0),
      dz ? kpi("Otobüs başına", "fuel_l_per_bus_day", "l/gün", 0) : kpi("Otobüs başına", "kwh_per_bus_day", "kWh/gün", 0),
      dz ? kpi("Yolcu başına", "fuel_l_per_pax", "l", 3) : kpi("Yolcu başına", "kwh_per_pax", "kWh", 3),
      dz ? kpi("Ortalama tüketim", "fuel_l_per_100km", "l/100 km", 1) : kpi("Araç-km / gün", "vehicle_km", "km", 0),
      kpi("Araç-saat / gün", "vehicle_hours", "sa", 1), dz ? kpi("Yakıt ikmali", "charge_sessions_per_bus", "/otobüs-gün") : kpi("Şarj oturumu", "charge_sessions_per_bus", "/otobüs-gün"));
    const charge = (R) => dz
      ? `Otobüs başına ${fmt(R.fuel_l_per_bus_day, 0)} l/gün; ${fmt(R.vehicle_km, 0)} araç-km/gün, ${fmt(R.fuel_l_per_100km, 1)} l/100 km. Depo ${R.charge_sessions_per_bus <= 1 ? "bir günü çıkarır" : `günde ${R.charge_sessions_per_bus} dolum ister`}.`
      : R.kwh_per_bus_day <= R.usable_kwh
      ? `Otobüs başına ${fmt(R.kwh_per_bus_day, 0)} kWh/gün, kullanılabilir ${fmt(R.usable_kwh, 0)} kWh: gece depo şarjı yeter (${fmt(100 * R.kwh_per_bus_day / R.usable_kwh, 0)}% kullanım).`
      : `Otobüs başına ${fmt(R.kwh_per_bus_day, 0)} kWh/gün, kullanılabilir ${fmt(R.usable_kwh, 0)} kWh: gün içinde ${R.charge_sessions_per_bus - 1} ara şarj gerekir ya da filo büyütülür.`;
    results.replaceChildren(
      h("div", { class: "panel stack" },
        h("div", { class: "row" }, h("b", {}, `${L.code} · ${L.name}`), h("span", { class: "muted small" }, `${fmt(L.length_km, 1)} km · ${L.stops.length} durak · ${B.vehicle} · kapasite ${B.capacity}`)),
        tiles,
        h("div", { class: "net-note" }, S ? h("span", {}, h("b", {}, "Deneme: "), charge(S), " ", h("span", { class: "muted" }, `Temel: ${charge(B)}`)) : charge(B))),
      h("div", { class: "net-two" },
        h("div", { class: "panel stack" }, h("div", { class: "lab" }, "Doluluk, saat saat (sefer sayısı)"), occupancyChart(B, S),
          h("div", { class: "net-legend" }, h("span", {}, h("i", { style: `background:${C.within}` }), "hedef içinde (30–85 %)"),
            h("span", {}, h("i", { style: `background:${C.above}` }), "hedefin üstünde"), h("span", {}, h("i", { style: `background:${C.below}` }), "düşük doluluk"),
            S ? h("span", {}, h("i", { style: `background:${C.scn}` }), "deneme (sağdaki çubuk)") : null)),
        h("div", { class: "panel stack" }, h("div", { class: "lab" }, "Gereken otobüs, saat bandına göre"), fleetChart(B, S),
          h("div", { class: "net-note" }, "Otobüs sayısı = tur süresi (gidiş + dönüş + 2 mola) / sefer aralığı, yukarı yuvarlanır."))),
      h("div", { class: "panel stack" }, h("div", { class: "lab" }, `Güzergâh boyunca yolcu yükü, zirve bant (${B.bands[2 * B.peak_band_index].label})`), loadChart(B, S, L),
        h("div", { class: "net-note" }, "Çubuk: duraktan kalkışta araçtaki yolcu. Kırmızı çizgi: kapasite. Kesik çizgi: deneme.")),
      h("div", { class: "panel" }, bandTable(B, S)));
  }

  function occupancyChart(B, S) {
    const H = B.occupancy_hist, n = H.hours.length, W = 640, Hh = 190, pad = 34, bw = (W - pad - 10) / n;
    const maxN = Math.max(1, ...H.within.map((x, i) => x + H.above[i] + H.below[i]), ...(S ? S.occupancy_hist.within.map((x, i) => x + S.occupancy_hist.above[i] + S.occupancy_hist.below[i]) : [0]));
    const y = v => Hh - 24 - (Hh - 40) * v / maxN;
    const bars = [];
    const stack = (HH, i, x, w, dashed) => {
      let top = Hh - 24;
      for (const [k, col] of [["below", C.below], ["within", C.within], ["above", C.above]]) {
        const v = HH[k][i]; if (!v) continue;
        const hgt = (Hh - 40) * v / maxN; top -= hgt;
        bars.push(`<rect x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${w.toFixed(1)}" height="${hgt.toFixed(1)}" fill="${col}" rx="1.5"${dashed ? ' stroke="' + C.scn + '" stroke-width="1.5"' : ""}/>`);
      }
      bars.push(`<text x="${(x + w / 2).toFixed(1)}" y="${(top - 3).toFixed(1)}" font-size="9" text-anchor="middle" fill="currentColor">${fmt(100 * HH.load_factor[i], 0)}%</text>`);
    };
    for (let i = 0; i < n; i++) {
      const x = pad + i * bw;
      if (S) { stack(B.occupancy_hist, i, x + 1, bw / 2 - 2, false); stack(S.occupancy_hist, i, x + bw / 2 + 1, bw / 2 - 2, true); }
      else stack(B.occupancy_hist, i, x + 2, bw - 4, false);
      bars.push(`<text x="${(x + bw / 2).toFixed(1)}" y="${Hh - 8}" font-size="10" text-anchor="middle" fill="currentColor">${String(H.hours[i]).padStart(2, "0")}</text>`);
    }
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${Hh}`); svg.setAttribute("class", "net-chart"); svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Saat saat sefer sayısı ve doluluk sınıfı");
    svg.innerHTML = `<line x1="${pad}" y1="${Hh - 24}" x2="${W - 8}" y2="${Hh - 24}" stroke="currentColor" stroke-opacity=".3"/>` +
      `<text x="4" y="${y(maxN) + 4}" font-size="10" fill="currentColor">${fmt(maxN, 0)}</text><text x="4" y="${Hh - 26}" font-size="10" fill="currentColor">0</text>` + bars.join("");
    return svg;
  }

  function fleetChart(B, S) {
    const n = B.fleet_by_band.length, W = 640, Hh = 190, pad = 34, bw = (W - pad - 10) / n;
    const maxF = Math.max(1, ...B.fleet_by_band, ...(S ? S.fleet_by_band : [0]));
    const parts = [];
    for (let i = 0; i < n; i++) {
      const x = pad + i * bw, b = B.bands[2 * i];
      const bar = (v, xx, w, col) => { const hh = (Hh - 40) * v / maxF; parts.push(`<rect x="${xx.toFixed(1)}" y="${(Hh - 24 - hh).toFixed(1)}" width="${w.toFixed(1)}" height="${hh.toFixed(1)}" fill="${col}" rx="2"/><text x="${(xx + w / 2).toFixed(1)}" y="${(Hh - 28 - hh).toFixed(1)}" font-size="10" text-anchor="middle" fill="currentColor">${v}</text>`); };
      if (S) { bar(B.fleet_by_band[i], x + 2, bw / 2 - 3, C.base); bar(S.fleet_by_band[i], x + bw / 2 + 1, bw / 2 - 3, C.scn); }
      else bar(B.fleet_by_band[i], x + 3, bw - 6, C.base);
      parts.push(`<text x="${(x + bw / 2).toFixed(1)}" y="${Hh - 8}" font-size="9.5" text-anchor="middle" fill="currentColor">${b.label.replace(":00", "").replace(":00", "")}</text>`);
    }
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${Hh}`); svg.setAttribute("class", "net-chart"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Saat bandına göre gereken otobüs");
    svg.innerHTML = `<line x1="${pad}" y1="${Hh - 24}" x2="${W - 8}" y2="${Hh - 24}" stroke="currentColor" stroke-opacity=".3"/>` + parts.join("");
    return svg;
  }

  function loadChart(B, S, L) {
    const k = B.peak_band_index, out = B.bands[2 * k], back = B.bands[2 * k + 1];
    const n = out.stop_loads.length, W = 900, Hh = 230, pad = 36, bw = (W - pad - 10) / n;
    const cap = B.capacity, maxL = Math.max(cap * 1.1, ...out.stop_loads, ...back.stop_loads, ...(S ? [...S.bands[2 * k].stop_loads, ...S.bands[2 * k + 1].stop_loads] : [0]));
    const y = v => Hh - 60 - (Hh - 80) * v / maxL;
    const parts = [];
    const stopsOut = L.stops, stopsIn = [...L.stops].reverse();
    for (let i = 0; i < n; i++) {
      const x = pad + i * bw;
      const vO = out.stop_loads[i], vI = back.stop_loads[n - 1 - i];        // inbound load drawn at the same geographic stop
      parts.push(`<rect x="${(x + 1).toFixed(1)}" y="${y(vO).toFixed(1)}" width="${(bw / 2 - 2).toFixed(1)}" height="${(Hh - 60 - y(vO)).toFixed(1)}" fill="${C.base}" rx="1.5"><title>${stopsOut[i]} gidiş: ${fmt(vO, 0)} yolcu</title></rect>`);
      parts.push(`<rect x="${(x + bw / 2).toFixed(1)}" y="${y(vI).toFixed(1)}" width="${(bw / 2 - 2).toFixed(1)}" height="${(Hh - 60 - y(vI)).toFixed(1)}" fill="${C.base}" fill-opacity=".45" rx="1.5"><title>${stopsIn[n - 1 - i]} dönüş: ${fmt(vI, 0)} yolcu</title></rect>`);
      if (S) {
        const sO = S.bands[2 * k].stop_loads[i], sI = S.bands[2 * k + 1].stop_loads[n - 1 - i];
        parts.push(`<line x1="${(x + 1).toFixed(1)}" y1="${y(sO).toFixed(1)}" x2="${(x + bw / 2 - 1).toFixed(1)}" y2="${y(sO).toFixed(1)}" stroke="${C.scn}" stroke-width="2" stroke-dasharray="3 2"/>`);
        parts.push(`<line x1="${(x + bw / 2).toFixed(1)}" y1="${y(sI).toFixed(1)}" x2="${(x + bw - 2).toFixed(1)}" y2="${y(sI).toFixed(1)}" stroke="${C.scn}" stroke-width="2" stroke-dasharray="3 2"/>`);
      }
      parts.push(`<text transform="translate(${(x + bw / 2).toFixed(1)},${Hh - 54}) rotate(-55)" font-size="9" text-anchor="end" fill="currentColor">${stopsOut[i]}</text>`);
    }
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${Hh}`); svg.setAttribute("class", "net-chart"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Duraklara göre araçtaki yolcu, gidiş ve dönüş");
    svg.innerHTML = `<line x1="${pad}" y1="${y(cap)}" x2="${W - 8}" y2="${y(cap)}" stroke="#d03b3b" stroke-dasharray="4 3"/><text x="${W - 10}" y="${y(cap) - 4}" font-size="10" text-anchor="end" fill="#d03b3b">kapasite ${cap}</text>` +
      `<line x1="${pad}" y1="${Hh - 60}" x2="${W - 8}" y2="${Hh - 60}" stroke="currentColor" stroke-opacity=".3"/>` +
      `<text x="4" y="${y(maxL) + 4}" font-size="10" fill="currentColor">${fmt(maxL, 0)}</text>` +
      `<text x="${pad}" y="${Hh - 2}" font-size="10" fill="currentColor">koyu: gidiş · açık: dönüş</text>` + parts.join("");
    return svg;
  }

  function bandTable(B, S) {
    const rows = B.bands.map((b, i) => {
      const s = S ? S.bands[i] : null;
      const cell = (v, d, suffix = "") => h("td", { class: "num" }, s ? `${fmt(s[v], d)}${suffix} ` : `${fmt(b[v], d)}${suffix}`, s ? h("span", { class: "muted" }, `(${fmt(b[v], d)})`) : null);
      return h("tr", {}, h("td", {}, `${b.label} ${b.direction}`), h("td", { class: "num" }, s ? `${s.headway_min} (${b.headway_min})` : b.headway_min),
        h("td", {}, s ? `${s.traffic} (${b.traffic})` : b.traffic), cell("trips", 0), cell("runtime_min", 1), cell("free_runtime_min", 1),
        cell("peak_load", 0), cell("load_factor", 2), cell("denied", 0),
        ...(B.powertrain === "diesel" ? [cell("fuel_l", 2), h("td", { class: "num" }, s ? `${fmt(100 * s.fuel_l / s.distance_km, 1)} ` : `${fmt(100 * b.fuel_l / b.distance_km, 1)}`, s ? h("span", { class: "muted" }, `(${fmt(100 * b.fuel_l / b.distance_km, 1)})`) : null), cell("kwh", 1)]
                                      : [cell("kwh", 1), cell("kwh_per_km", 2), cell("soc_drop", 3)]));
    });
    const cols = B.powertrain === "diesel" ? ["l/sefer", "l/100 km", "kWh yakıt/sefer"] : ["kWh/sefer", "kWh/km", "SOC düşüşü"];
    return h("div", { style: "overflow-x:auto" }, h("table", { class: "net" },
      h("thead", {}, h("tr", {}, ...["Bant", "Aralık dk", "Trafik", "Sefer", "Süre dk", "Serbest dk", "Zirve yük", "Doluluk", "Binemeyen", ...cols].map(t => h("th", {}, t)))),
      h("tbody", {}, ...rows)),
      S ? h("div", { class: "hint" }, "Önce deneme, parantez içinde temel.") : null);
  }

  setCleanup(() => {});
  return root;
}
