/* "Veri biçimleri": every file the tool accepts, what its columns mean, where
   it is uploaded, and a template to download. The catalogue comes from the
   server (templates.py) so the page and the importers cannot drift apart. */

export async function pageFormats(U, group) {
  const { h, api, store, pageHead, notice, menu, dataTable } = U;
  const cat = await api.get("/api/templates");
  const root = h("div", {});
  root.append(pageHead("Veri biçimleri", "Veri biçimleri ve şablonlar",
    "Simülatöre veri vermenin iki yolu var: Veri yükle sayfasındaki içe aktarıcılar (biçimi kendileri algılar: Excel ya da CSV, noktalı virgül, ondalık virgül, rpm ya da rad/s) ve kütüphane klasörüne doğrudan konan dosyalar (çözücünün kendi biçimi). Her satırdaki şablon, o biçimin geçerli bir örneğidir; sayılar sentetiktir.",
    [h("a", { class: "btn primary", href: "/api/templates.zip", download: "" }, "Bütün şablonlar (zip)"),
     h("a", { class: "btn", href: "#/yukle" }, "Veri yükle")]));

  const groups = cat.groups;
  const cur = group && groups.some(g => g[0] === group) ? group : store.get("fmtGroup", groups[0][0]);
  const tabs = h("div", { class: "tabs", role: "tablist" });
  const body = h("div", {});
  const count = key => cat.items.filter(t => t.group === key).length;
  const draw = key => {
    store.set("fmtGroup", key);
    tabs.querySelectorAll(".tab").forEach(b => b.setAttribute("aria-selected", String(b.dataset.key === key)));
    const items = cat.items.filter(t => t.group === key);
    const t = dataTable([{ label: "Veri" }, { label: "Dosya ve sütunlar" }, { label: "Nereye yüklenir" }, { label: "", acts: true }]);
    items.forEach(it => {
      const cols = h("dl", { class: "fmt-cols" }, it.columns.map(([c, m]) => [h("dt", {}, c), h("dd", {}, m)]));
      const more = it.notes && it.notes.length ? h("details", { class: "fmt-notes" }, h("summary", {}, "Notlar"), h("ul", {}, it.notes.map(n => h("li", {}, n)))) : null;
      const where = it.family === "library"
        ? h("div", {}, h("span", { class: "badge plain" }, "kütüphane klasörü"), h("div", { class: "small", style: "margin-top:4px" }, h("code", {}, it.upload)),
            h("div", { class: "small muted" }, "Paket (zip) ile de alınır."))
        : h("div", {}, h("span", { class: "badge ok" }, "içe aktarıcı"), h("div", { class: "small", style: "margin-top:4px" }, h("a", { href: it.upload }, it.upload_label)));
      t.body.append(h("tr", { class: "item" },
        h("td", { class: "name fmt-name" }, h("b", {}, it.label), h("span", { class: "sub" }, it.description)),
        h("td", { class: "txt" }, h("div", { class: "fmt-file" }, h("code", {}, it.filename)), cols, more),
        h("td", { class: "txt" }, where),
        h("td", { class: "act" }, h("div", { class: "acts" },
          h("a", { class: "btn", href: `/api/template/${encodeURIComponent(it.key)}`, download: "", title: "Şablon dosyasını indir" }, "Şablon"),
          it.family === "library" ? null : h("a", { class: "btn ghost", href: it.upload }, "Yükle")))));
    });
    body.replaceChildren(h("div", { class: "panel list" }, t.table));
  };
  groups.forEach(([key, label]) => tabs.append(h("button", { type: "button", class: "tab", role: "tab", "data-key": key, "aria-selected": "false", onclick: () => draw(key) }, label, h("span", { class: "n" }, count(key)))));
  root.append(tabs, body);
  draw(cur);
  root.append(h("div", { class: "panel", style: "margin-top:16px" },
    h("div", { class: "section-title" }, "Genel kurallar"),
    h("ul", { class: "fmt-rules" },
      h("li", {}, h("b", {}, "Ayraç ve ondalık."), " İçe aktarıcılar virgül, noktalı virgül, sekme ve ondalık virgülü kendileri algılar. Kütüphane biçimleri virgül ayraç ve ondalık nokta kullanır."),
      h("li", {}, h("b", {}, "Başlıklar."), " Türkçe ya da İngilizce olabilir: mesafe/distance, rakım/elevation, hız/speed. Tanınmayan başlık sayfada elle eşlenir."),
      h("li", {}, h("b", {}, "Birimler."), " YAML alan adının sonundaki ek birimi söyler: _kw, _rpm, _kmh, _mohm, _t (ton). Harita eksenlerinde rpm yazılmazsa rad/s kabul edilir; içe aktarıcı rpm'i tahmin eder ve sorar."),
      h("li", {}, h("b", {}, "Ölçüm mü, kabul mü?"), " Her bileşen dosyasında validated alanı yalnızca ölçülmüş veri için true olur. Ölçülmemiş bileşenle çalışan sonuç raporda yol gösterici diye işaretlenir."),
      h("li", {}, h("b", {}, "Yorum satırı."), " CSV'de # ile başlayan satırlar atlanır; kaynağı ve tarihi oraya yazın."),
      h("li", {}, h("b", {}, "Paket."), " Bir kütüphaneden İndir ile alınan zip, başka bilgisayarda Veri yükle → Paket ile açılır; klasör yapısı kütüphaneyle aynıdır."))));
  return root;
}

/* Single YAML item upload (scenario, vehicle, component, route spec, line):
   drop the file, see what it is, save. Used by the upload hub and the home
   roadmap's "Senaryo yükle". */
export function yamlWizard(U, { accept = "scenario,vehicle,route_spec,line,component", onSaved, title } = {}) {
  const { h, api, notice, toast, steps, dropZone, busy } = U;
  const root = h("div", {});
  const LABELS = ["Dosya", "Kaydet"];
  const over = h("input", { type: "checkbox" });
  const first = (err) => root.replaceChildren(steps(LABELS, 0), h("div", { class: "stack" },
    err ? notice("err", "Dosya kaydedilemedi.", [err]) : null,
    dropZone({ accept: ".yaml,.yml", title: title || "YAML dosyasını bırakın",
      sub: "Senaryo, araç, bileşen, güzergâh tanımı ya da hat dosyası. Dosyanın kind alanı neyin ne olduğunu söyler; yükleyici kabul etmezse hiçbir şey yazılmaz. Şablonlar: Veri biçimleri sayfası.",
      onFile: async f => {
        root.replaceChildren(steps(LABELS, 1), h("div", { class: "muted" }, `${f.name} kaydediliyor…`));
        try {
          const r = await api.upload("/api/import/yaml", f, { overwrite: over.checked ? "1" : "" });
          await U.overview(true);
          toast(`Kaydedildi: ${r.saved}`);
          if (onSaved) onSaved(r); else done(r);
        } catch (e) { first(e.message); }
      } }),
    h("label", { class: "check" }, over, h("span", {}, "Aynı adlı dosyanın üzerine yaz")),
    h("p", { class: "small muted", style: "margin:0" }, h("a", { href: "#/bicimler" }, "Veri biçimleri ve şablonlar"))));
  const done = r => {
    const links = { scenario: `#/senaryo/${encodeURIComponent(r.stem)}`, vehicle: `#/arac/${encodeURIComponent(r.stem)}`, route: `#/guzergah/${encodeURIComponent(r.stem)}` };
    const href = links[r.kind] || (r.kind === "line" ? "#/hat" : `#/bilesen/${r.kind}/${encodeURIComponent(r.stem)}`);
    root.replaceChildren(steps(LABELS, 2), notice("ok", `${r.label}: ${r.name}`, [`${r.saved} yazıldı.`]),
      h("div", { class: "row", style: "margin-top:12px" }, h("a", { class: "btn primary", href }, "Aç"), h("button", { type: "button", class: "ghost", onclick: () => first() }, "Başka dosya")));
  };
  first();
  return root;
}
