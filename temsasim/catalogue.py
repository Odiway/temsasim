"""What the simulator calculates, as one list.

One source for three places: the in-app help (``/api/calculations``), the
calculation map (the block between the LISTE markers of
``app/static/hesap-haritasi.html``) and ``docs/hesaplananlar.md``. The two
files are generated from here (``python -m temsasim hesaplananlar --write``)
and a test fails when they drift, so the list people read is the list the
code is checked against.

Each item is (name, unit, how). "How" is one short sentence in plain Turkish;
the equations are in the calculation map section the group points to.
"""

from __future__ import annotations

import html
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
MAP_HTML = ROOT / "app" / "static" / "hesap-haritasi.html"
DOC_MD = ROOT.parent / "docs" / "hesaplananlar.md"
BEGIN, END = "<!-- LISTE:BEGIN (temsasim/catalogue.py üretir; elle düzenlemeyin) -->", "<!-- LISTE:END -->"

INTRO = ("Simülasyon bir aracı bir güzergâhta saniye saniye sürer ve enerjinin gittiği her yeri ayrı kalem olarak yazar. "
         "Aşağıda hesaplanan her büyüklük, birimi ve nerede görüldüğü var. Denklemler hesap haritasının ilgili bölümündedir.")

GROUPS = [
    {"key": "kosu", "title": "Koşu: yol yükü ve enerji dengesi", "where": "Çalışma masası → Koşu", "hash": "#/simulasyon", "map": "adim",
     "code": "simulator.py · diesel.py · chassis.py",
     "lede": "Elektrikli ve dizel araçta ortak olan kısım: aracı hareket ettirmek için tekerlekte ne kadar güç gerektiği.",
     "items": [
         ("Yuvarlanma direnci", "kW · kWh", "Lastik katsayısı, kütle ve yol yüzeyinden; ölçüm varsa coast-down eğrisinden."),
         ("Hava direnci", "kW · kWh", "CdxA, hava yoğunluğu (sıcaklık, basınç) ve rüzgârdan."),
         ("Eğim direnci", "kW · kWh", "Kütle ve yolun eğiminden."),
         ("Atalet", "kW · kWh", "Kütle ile dönen parçaların (tekerlek, motor, şanzıman) eşdeğer kütlesinden."),
         ("Tekerlek gücü ve torku", "kW · Nm", "Dört direncin toplamı."),
         ("Anlık kütle", "t", "Boş kütle ile o andaki yolcu sayısından; yolcular duraklarda binip iner."),
         ("Fren enerjisi", "kWh", "Motorun ya da geri kazanımın karşılayamadığı yavaşlama."),
         ("Yardımcı tüketiciler", "kW · kWh", "Kompresör, direksiyon pompası, aydınlatma, kapılar; hıza ve gece/gündüze bağlı."),
         ("Karşılanamayan talep", "kWh · s", "Aracın gücü hız izini tutmaya yetmediği yerler; koşu 'yapılabilir' mi."),
         ("Mesafe, süre, ortalama hız, durak sayısı", "km · s · km/h", "Sürülen görevin özeti."),
         ("Veri tamlığı", "", "Her bileşen için: veri ölçüm mü, kabul mü. Sonuç buna göre 'doğrulanmış' ya da 'gösterge' sayılır."),
     ]},
    {"key": "elektrik", "title": "Bataryalı elektrikli araç", "where": "Çalışma masası → Koşu", "hash": "#/simulasyon", "map": "alt",
     "code": "simulator.py · motor.py · battery.py · driveline.py",
     "lede": "Tekerlekten motora, invertöre ve bataryaya giden zincir.",
     "items": [
         ("Tüketim ve menzil", "kWh/km · km", "Bataryadan çekilen net enerji ve kullanılabilir kapasiteden."),
         ("Şarj durumu (SOC) izi", "%", "Her saniye bataryadan çekilen akımdan."),
         ("Batarya gerilimi, akımı, gücü", "V · A · kW", "Açık devre gerilimi (SOC'ye bağlı) ve iç dirençten; direnç sıcaklıkla değişir."),
         ("Motor + invertör verimi ve kaybı", "% · kW", "Verim haritasından (devir, tork); harita yoksa tepe verimden."),
         ("Aktarma kaybı", "kW · kWh", "Dişli ve diferansiyel verimi ile boşta dönme kaybından."),
         ("Batarya kaybı", "kWh", "İç dirençte ısıya giden enerji."),
         ("Geri kazanılan fren enerjisi", "kWh · %", "Motorun, bataryanın ve kontrol stratejisinin izin verdiği kadarı."),
         ("Kullanılamayan fren enerjisi", "kWh", "Batarya doluyken, hız çok düşükken ya da sınır aşıldığında frenlere giden kısım."),
         ("Batarya sıcaklığı", "°C", "Kayıp ısısı, ortam ve soğutma/ısıtma ile ısıl kütleden."),
         ("Yüksek voltaj ısıtma ve batarya soğutma enerjisi", "kWh", "Bataryayı çalışma sıcaklığında tutmak için harcanan enerji; kabinden ayrı sayılır."),
         ("C oranı ve Ah geçişi", "C · Ah", "Bataryanın ne kadar zorlandığı; yaşlanma hesabının girdisi."),
         ("En yüksek motor ve batarya gücü", "kW", "Koşu boyunca görülen tepe değerler."),
         ("Batarya yaşlanması ve ömür", "% SOH · yıl", "Takvim ve çevrim yaşlanması (sıcaklık, SOC, C oranı); State of Health'in ömür sonuna indiği yıl."),
     ]},
    {"key": "dizel", "title": "Dizel araç", "where": "Çalışma masası → Koşu", "hash": "#/simulasyon", "map": "dizel",
     "code": "diesel.py · engine.py · transmission.py",
     "lede": "Tekerlekten aksa, şanzımana ve motora giden zincir; yakıt, motorun yakıt haritasından okunur.",
     "items": [
         ("Yakıt tüketimi", "l/100 km · l · l/h", "Her saniye motor devri ve torkuyla yakıt haritasından; anlık ve ortalama."),
         ("CO₂", "g/km", "Yakılan yakıttan."),
         ("Rölanti yakıtı ve payı", "l · %", "Araç dururken ve debriyaj açıkken yakılan yakıt."),
         ("Motor devri, torku, yükü", "d/d · Nm · %", "Seçili vites ve tekerlek hızından; tam yük eğrisiyle karşılaştırılır."),
         ("Krank gücü", "kW", "Motorun krank milinde verdiği güç: çekiş, kayıplar ve yardımcılar."),
         ("Motor freni", "kWh", "Gaz kesikken motorun sürükleme torkuyla aldığı enerji."),
         ("Şanzıman, aks, retarder ve debriyaj kaybı", "kW · kWh", "Ölçülmüş kayıp haritalarından; retarder boşta dönme kaybı ayrı."),
         ("Fan ve PTO gücü", "kW · kWh", "Motor fanı ve motordan tahrik edilen yardımcılar."),
         ("Vites izi", "vites", "Her saniye hangi viteste gidildiği, vites değişim sayısı, nedenleri ve viteslerin zaman payı."),
         ("Kabine giden motor ısısı ve ısıtıcı yakıtı", "kWh · l", "Kışın kabini motorun atık ısısı ısıtır; yetmezse yakıtlı ısıtıcı."),
         ("Depo menzili", "km", "Depo hacmi ve tüketimden."),
     ]},
    {"key": "vites", "title": "Vites mantığı ve şanzıman kalibrasyonu", "where": "Çalışma masası → Vites", "hash": "#/vites", "map": "vites",
     "code": "shift.py · shiftcal.py",
     "lede": "Şanzımanın ne zaman vites değiştireceği (VECTO AMT EffShift) ve ayarların test verisine oturtulması.",
     "items": [
         ("Vites hatları", "d/d – Nm", "Her vites için küçültme ve büyütme hattı; motorun tam yük eğrisinden."),
         ("Kalkış vitesi", "vites", "Düşük hızda devri debriyaj kapanma devrinin üstünde tutan en yüksek vites."),
         ("Vites kararı ve nedeni", "", "Her saniye: acil (devir sınırı), vites hattı, verim, fren küçültmesi, talep karşılanmıyor."),
         ("Ölçümle vites uyumu", "%", "Loglanan vitesle aynı olan sürenin payı; ±1 vites içinde ve geçişlerde ayrıca."),
         ("Devir hatası", "d/d RMSE", "Model ile ölçülen motor devri arasındaki fark."),
         ("Kalibre edilmiş ayar değerleri", "", "Ölçüme en çok uyan ayarlar ve her ayarın sonuca etkisi."),
         ("Yakıt korelasyonu", "%", "Bütün test koşuları tam çözücüyle: CAN ve akış ölçere göre yakıt farkı."),
     ]},
    {"key": "kabin", "title": "Kabin içi termal yönetim", "where": "Çalışma masası → Kabin", "hash": "#/kabin", "map": "kabin",
     "code": "cabin.py · hvac.py",
     "lede": "Kabinin ne kadar ısıtma ya da soğutma istediği ve bunun kaç kW elektrik ya da yakıt ettiği.",
     "items": [
         ("Gövdenin ısı geçirgenliği", "W/K · W/m²K", "Her yüzeyin katmanlarından (malzeme, kalınlık) ve iç/dış ısı taşınımından."),
         ("Güneş yükü", "W/m² · W", "Konum, gün ve saatten açık gökyüzü ışınımı; yüzeyin yönüne göre; camdan geçen kısım."),
         ("Kabin havası ve yüzey sıcaklıkları", "°C", "Katmanlı model: hava, iç yüzeyler ve iç donanım zamanla nasıl soğur ya da ısınır."),
         ("Isı akışları", "kW", "Duvarlardan gelen, üfleme havasıyla çekilen ve yolculardan gelen ısı."),
         ("Referansla karşılaştırma", "°C RMSE", "CFD ya da test eğrisiyle fark ve kabul ölçütleri (ör. 30 dakikada baş seviyesi 31 °C altı)."),
         ("Koşuda kabin sıcaklığı ve termal güç", "°C · kW", "Ayar sıcaklığını tutmak için gereken ısıl güç ve COP ile elektrik gücü."),
         ("Kabin içi termal yönetim enerjisi", "kWh", "Koşu boyunca kabine harcanan enerji; batarya termal yönetiminden ayrı yazılır."),
     ]},
    {"key": "suspansiyon", "title": "Süspansiyon, yalpa ve devrilme", "where": "Çalışma masası → Süspansiyon", "hash": "#/suspansiyon", "map": "suspansiyon",
     "code": "suspension.py",
     "lede": "Gövdenin dört tekerlek üzerinde zıplaması, yunuslaması ve yalpalaması; körükler ve amortisörlerle.",
     "items": [
         ("Aks ve tekerlek yükleri", "kg", "Kütle ve ağırlık merkezinden (X, Y, Z); yolcu ve bagaj dahil."),
         ("Körük basıncı ve yay katsayısı", "bar · N/mm", "Basınç yükten; yay katsayısı tedarikçi eğrisinden ya da hacimden."),
         ("Sürüş frekansı ve sönüm oranı", "Hz · –", "Ön ve arka; körük, lastik ve amortisör eğrisinden."),
         ("Titreşim modları", "Hz", "Zıplama, yunuslama, yalpa ve tekerlek zıplaması; frekans, sönüm, sönüm süresi."),
         ("Dalma ve çökme açısı", "° · °/g", "Frende burnun inmesi, kalkışta arkanın çökmesi; kalıcı ve en büyük değer."),
         ("Sönüm süresi ve salınım frekansı", "s · Hz", "Manevradan sonra gövdenin durulması."),
         ("Yalpa açısı", "° · °/g", "Virajda gövdenin yatması; körük, devrilme barı ve lastik esnemesiyle."),
         ("Yük aktarım oranı ve tekerlek yükleri", "– · kN", "İç tekerleklerden dış tekerleklere geçen yük; 1 olunca iç tekerlekler kalkar."),
         ("Devrilme eşiği", "g", "Rijit araç, süspansiyon esnemesiyle ve ani manevrada."),
         ("Eğme (tipping) açısı", "°", "Eğme tablasında iç tekerleklerin kalktığı açı; UN R107 şartı 28°."),
         ("Devrilme hızı", "km/h", "Verilen viraj yarıçapında ve şerit değiştirmede iç tekerleklerin kalktığı hız."),
         ("Körük stroku ve amortisör hızı", "mm · m/s", "Zaman izi; tampona değip değmediği."),
         ("Konfor", "m/s²", "ISO 8608 yol sınıflarında ISO 2631 ağırlıklı düşey ivme; ön, orta, arka."),
         ("Direksiyon açısı", "°", "Bir viraj için gereken ön tekerlek açısı (δ = L/R + K·a_y) ve karakteristik hız."),
     ]},
    {"key": "gorev", "title": "Güzergâh ve görev üretimi", "where": "Kütüphane → Güzergâh · Senaryolar", "hash": "#/guzergah", "map": "gorev",
     "code": "cycle.py · routes.py · traffic.py · vecto_cycles.py",
     "lede": "Yolun ve sürüşün tanımından, çözücünün sürdüğü hız izinin üretilmesi.",
     "items": [
         ("Yol profili", "m · %", "Segmentlerden ya da GPX, KML, CSV dosyasından: mesafe, rakım, eğim, hız sınırı."),
         ("Hız izi", "km/h", "Sürücü modeli: aracın gücüne göre hızlanma, hız sınırında seyir, duraklara yavaşlama."),
         ("Duraklar", "s", "Bekleme süresi ve kapıların açık kaldığı süre."),
         ("Yolcu yükü", "kişi", "Durak bazında binen ve inen; doluluk yol boyunca değişir."),
         ("Trafik", "s", "Seviye (yok, orta, yoğun), sinyallerde bekleme, trafik yüzünden kaybedilen süre."),
         ("Yol yüzeyi", "–", "Yüzeye göre yuvarlanma direnci çarpanı (asfalt, parke, arnavut kaldırımı)."),
         ("Hazır görevler", "", "VECTO otobüs çevrimleri (Coach, Interurban, Suburban, Urban, Heavy Urban), standart çevrimler, sabit hız."),
         ("Senaryo", "", "İklim (sıcaklık, güneş, gece/gündüz), yük ve trafiğin bir arada tanımı."),
     ]},
    {"key": "karsilastirma", "title": "Karşılaştırma ve taramalar", "where": "Çalışma masası → Karşılaştır · Koşu", "hash": "#/karsilastir", "map": "uygulama",
     "code": "report_compare.py · server.py",
     "lede": "Aynı aracı farklı koşullarda ya da farklı araçları aynı görevde yan yana koyar.",
     "items": [
         ("Çoklu koşu", "", "2–4 koşu yan yana: tüketim, menzil, enerji kalemleri ve mesafeye göre izler."),
         ("Sıcaklık taraması", "kWh/km · km", "Tüketim ve menzilin dış sıcaklığa göre değişimi."),
         ("Trafik karşılaştırması", "", "Aynı güzergâh, farklı trafik seviyeleri."),
         ("Sinyal inceleme", "", "Bir koşunun bütün sinyalleri üst üste; sürükleyerek ya da zaman aralığı yazarak yakınlaştırma."),
     ]},
    {"key": "hat", "title": "Hat ve sefer (filo)", "where": "Çalışma masası → Hat ve sefer", "hash": "#/hat", "map": "uygulama",
     "code": "network.py",
     "lede": "Bir hattın bütün günü: saat dilimleri, sefer sıklığı, yolcu talebi.",
     "items": [
         ("Sefer ve tur süresi", "dk", "Her saat diliminde, trafik ve durak beklemeleriyle."),
         ("Gereken araç sayısı", "araç", "Tur süresi ve sefer sıklığından; saat dilimine göre."),
         ("Günlük sefer, araç-km, araç-saat", "", "Hattın günlük işi."),
         ("Yük profili ve doluluk", "kişi · %", "Durak bazında araçtaki yolcu; en yüklü kesit."),
         ("Binemeyen yolcu ve ortalama bekleme", "kişi · dk", "Kapasite yetmediğinde durakta kalanlar."),
         ("Günlük enerji ya da yakıt", "kWh · l", "Hat toplamı, araç başına ve yolcu başına."),
         ("Gün içi şarj ihtiyacı", "adet", "Araç başına günlük enerji kullanılabilir kapasiteyi aşıyorsa."),
         ("Trafikte kaybedilen süre", "dk/gün", "Serbest akışa göre."),
     ]},
    {"key": "test", "title": "Test verisi ve korelasyon", "where": "Çalışma masası → Test verisi", "hash": "#/dogrulama", "map": "test",
     "code": "validate.py · testlog.py · vecto.py · maraton.py",
     "lede": "Ölçülmüş bir sürüşü modelle yeniden sürer ve farkı gösterir.",
     "items": [
         ("Enerji ya da yakıt hatası", "%", "Aynı hız izinde model ile ölçüm; yakıtta CAN ve akış ölçer ayrı."),
         ("Aşama bazında hata", "kWh · l", "Rölanti, hızlanma, seyir, yavaşlama için ayrı ayrı."),
         ("SOC ve güç izi uyumu", "puan · kW", "SOC izinin RMSE'si, güç korelasyonu."),
         ("Vites ve seyir devri uyumu", "% · d/d", "Dizel loglarda."),
         ("VECTO ile yol yükü karşılaştırması", "%", "Aynı çevrimde yuvarlanma, hava, eğim ve atalet enerjileri."),
         ("Maraton regresyon modeli", "l/100 km · km", "Filo verisinden uydurulmuş yakıt ve balata ömrü tahmini; eğitim aralığı dışı uyarılır."),
     ]},
    {"key": "surus", "title": "Serbest sürüş", "where": "Çalışma masası → Serbest sürüş", "hash": "#/surus", "map": "surus",
     "code": "drivephys.js",
     "lede": "Aracı klavyeyle kendiniz sürersiniz; canlı değerler yaklaşık, sürüş bitince tam model hesaplar.",
     "items": [
         ("Canlı hız, ivme, güç, SOC ya da depo", "", "Aracın kendi parametreleriyle basit kuvvet dengesi."),
         ("Direksiyon ve yanal ivme", "° · g", "Viraj denklemi; sürücü 0,3 g'yi, yol tutuşunu ve devrilme eşiğini aşamaz."),
         ("Gövde yatması ve dalması", "°", "Süspansiyon dosyasındaki yalpa ve yunuslama gradyanlarından."),
         ("Sürüşün gerçek enerjisi", "kWh · l", "Kaydedilen hız, eğim ve yüzey izi tam modelle yeniden sürülür."),
     ]},
    {"key": "veri", "title": "Veri ve dijital ikiz", "where": "Kütüphane → Veri yükle · Araç kur", "hash": "#/yukle", "map": "girdi",
     "code": "vecto_import.py · importers.py · schema.py",
     "lede": "Araç, bileşen dosyalarından kurulur; elle sayı girilmez.",
     "items": [
         ("VECTO beyan dosyalarından araç", "", "Motor yakıt haritası ve tam yük eğrisi, şanzıman ve aks kayıp haritaları, lastik, kütle."),
         ("Harita ve eğri içe aktarma", "", "Verim, kayıp ve yakıt haritaları; tork ve gerilim eğrileri."),
         ("Test logu içe aktarma", "", "CAN kayıtları: hız, devir, vites, yakıt; sütunlar otomatik tanınır."),
         ("Karakteristik motor devirleri", "d/d", "Tam yük eğrisinden: anma devri, azami torkun ve gücün belirli paylarına denk gelen devirler."),
     ]},
]


def as_dict() -> dict:
    return {"intro": INTRO, "count": sum(len(g["items"]) for g in GROUPS),
            "groups": [{**{k: g[k] for k in ("key", "title", "where", "hash", "map", "code", "lede")},
                        "items": [{"name": n, "unit": u, "how": h} for n, u, h in g["items"]]} for g in GROUPS]}


def to_markdown() -> str:
    n = sum(len(g["items"]) for g in GROUPS)
    out = ["# Bu simülasyon neleri hesaplar", "",
           "<!-- temsasim/catalogue.py üretir: `python -m temsasim hesaplananlar --write`. Elle düzenlemeyin. -->", "",
           INTRO, "", f"{len(GROUPS)} başlık, {n} büyüklük. Aynı liste uygulamada sağ üstteki **Yardım** düğmesinde ve hesap haritasının başında da var.", "",
           "| # | Başlık | Nerede | Büyüklük |", "|---|---|---|---|"]
    for i, g in enumerate(GROUPS, 1):
        out.append(f"| {i} | [{g['title']}](#{i}-{_slug(g['title'])}) | {g['where']} | {len(g['items'])} |")
    for i, g in enumerate(GROUPS, 1):
        out += ["", f"## {i}. {g['title']}", "", g["lede"], "",
                f"Nerede: **{g['where']}** · Kod: `{g['code']}` · Denklemler: hesap haritası, `#{g['map']}` bölümü", "",
                "| Hesaplanan | Birim | Nasıl |", "|---|---|---|"]
        out += [f"| {n} | {u or '–'} | {h} |" for n, u, h in g["items"]]
    return "\n".join(out) + "\n"


def _slug(text: str) -> str:
    keep = "".join(c.lower() if c.isalnum() else "-" if c in " -" else "" for c in text)
    return keep.strip("-")


def to_html_block() -> str:
    e = html.escape
    n = sum(len(g["items"]) for g in GROUPS)
    out = [BEGIN, '<section id="liste">',
           f'  <div class="shead"><span class="num">0</span><h2>Neler hesaplanıyor</h2><p>{e(INTRO)} {len(GROUPS)} başlık, {n} büyüklük.</p></div>',
           '  <div class="limits">']
    for g in GROUPS:
        rows = "".join(f"<li><b>{e(nm)}</b>{' <i>' + e(u) + '</i>' if u else ''}<br>{e(h)}</li>" for nm, u, h in g["items"])
        out.append(f'    <div class="lim liste"><b>{e(g["title"])}</b><p>{e(g["lede"])}</p><ul>{rows}</ul>'
                   f'<span class="src">{e(g["where"])} · <a href="#{e(g["map"])}">denklemler</a> · {e(g["code"])}</span></div>')
    out += ["  </div>", "</section>", END]
    return "\n".join(out)


def write_files() -> list:
    """Regenerate the document and the calculation map's block; return what changed."""
    changed = []
    md = to_markdown()
    if not DOC_MD.exists() or DOC_MD.read_text(encoding="utf-8") != md:
        DOC_MD.write_text(md, encoding="utf-8", newline="\n")
        changed.append(str(DOC_MD))
    raw = MAP_HTML.read_bytes()
    crlf = b"\r\n" in raw
    page = raw.decode("utf-8").replace("\r\n", "\n")
    if BEGIN not in page or END not in page:
        raise ValueError(f"{MAP_HTML.name}: LISTE işaretleri yok")
    a, b = page.index(BEGIN), page.index(END) + len(END)
    new = page[:a] + to_html_block() + page[b:]
    if new != page:
        MAP_HTML.write_bytes((new.replace("\n", "\r\n") if crlf else new).encode("utf-8"))
        changed.append(str(MAP_HTML))
    return changed


def in_sync() -> bool:
    page = MAP_HTML.read_bytes().decode("utf-8").replace("\r\n", "\n")
    return DOC_MD.exists() and DOC_MD.read_text(encoding="utf-8") == to_markdown() and to_html_block() in page
