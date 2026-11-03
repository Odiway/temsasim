# temsasim: bus energy simulation

A route-based energy, range and battery-life simulator for battery-electric city
buses and, since November 2026, a fuel and CO₂ simulator for diesel coaches
built from their VECTO homologation files. It works the same way as NREL
ADVISOR (backward-facing, quasi-static) and adds the physics that decide
real-world e-bus range, which ADVISOR does not have:

| | ADVISOR 2002 | temsasim |
|---|---|---|
| Cabin climate | constant accessory load | lumped cabin thermal model: envelope UA, ventilation, **door-opening air exchange** (Brown–Solvason), passenger and solar gains, heat pump / resistive / fuel-fired heating with COP curves |
| Battery | Rint | Rint with Arrhenius resistance, **cold charge-acceptance derating** (winter regen loss), lumped pack thermal and conditioning power |
| Ageing | none | calendar and cycle fade (Wang et al. 2011 form) projected over service life |
| Duty | speed trace | speed trace **or real route**: elevation, speed limits, stops, occupancy profile, power-aware driver |
| Auxiliaries | constant | electrified steering, air compressor duty scaled by stops, DC-DC losses |
| Trust | none | closed energy balance with visible residual, unvalidated-data warnings, **model-vs-measurement validation tool** |

> **What is measured and what is assumed.** `library/` holds TEMSA data: the
> Maraton 13 diesel twin (`vehicle/maraton_13_dizel.yaml`) is built from the
> homologation team's VECTO certificates (engine fuel map, gearbox and axle
> loss maps, tyres), and the Maraton coast-down chassis files come from the
> 2026 test campaign. Everything else in a component file is marked
> `validated: false` and named as an assumption in its description; every
> report repeats the warning. The battery-electric placeholder buses are not
> in the library any more: they live in `examples/vehicle/` and are only
> merged in by the test suite.
>
> **Confidentiality:** the VECTO source files under `ARAÇ VERİLERİ/` are
> ignored by git. The generated library files carry supplier data (fuel maps,
> certificate numbers); do not push them to a public repository.

## Arayüz (önerilen başlangıç)

**`baslat.bat` dosyasına çift tıklayın.** İlk seferde gerekli paketleri kurar,
sonra tarayıcıda arayüzü açar (`http://127.0.0.1:8765`). Sunucu yalnızca bu
bilgisayarda çalışır, şirket ağına açılmaz. İnternet gerekmez.

| Bölüm | Ne yapar |
|---|---|
| **Kütüphane** | Tüm araçlar ve bileşenler; hangisi ölçümle doğrulandı, hangisi hâlâ varsayılan. |
| **Veri yükle** | **VECTO dosyaları:** homologasyonun motor, şanzıman, aks, lastik ve araç XML dosyalarını (klasörü olduğu gibi) bırakın; dizel dijital ikiz kurulur, her değerin hangi dosyadan geldiği gösterilir. Ayrıca motor verim haritası, güzergâh (GPX / tablo), batarya ve klima eğrileri. Dosyayı olduğu gibi bırakın: Excel ya da CSV, `;` ya da `,`, ondalık virgül, ızgara ya da uzun liste, rpm ya da rad/s, verim % / kesir ya da kayıp kW, hepsi otomatik algılanır. Önizlemede yanlış görünen bir ayarı tek tıkla değiştirirsiniz; onaylamadan hiçbir şey kaydedilmez. |
| **Araç kur** | Bileşenleri açılır listelerden seçerek araç ya da varyant oluşturma. Güç aktarma tipi seçilir: bataryalı elektrikli (şasi, aktarma, motor, batarya, klima, yardımcılar, yaşlanma) ya da dizel (şasi, şanzıman+aks+retarder, dizel motor, klima, yardımcılar). |
| **Simülasyon** | Tam ekran 3B sahne: otobüs güzergâhta ilerler; sağda canlı hız/SOC göstergeleri, güç akışı, harcanan enerji ve olay akışı; altta sürüklenebilir rakım zaman çizelgesi. Kış/yaz/gece koşula göre değişir. Ayrıca Türkçe tam rapor ve −15…+40 °C sıcaklık taraması. |
| **Doğrulama** | Gerçek otobüsün CAN/telematik logunu yükleyin; sütunlar (Türkçe başlıklar dahil) otomatik eşleşir, güç yoksa gerilim × akımdan hesaplanır. Model ile ölçüm karşılaştırılır ve fark varsa hangi bileşene bakılacağı söylenir. |

**Trafik:** Güzergâh seçildiğinde trafik seviyesi (Yok / Akıcı / Orta / Yoğun /
Sıkışık) seçilebilir. Model trafik ışıklarını gerçek faz zamanlamasıyla işler
(kırmızıda önündeki araç kuyruğunun arkasında bekler, yeşilde kuyruk sırayla
boşalır, sarıda durabiliyorsa durur), akış hızını düşürür ve dur-kalk
sıkışıklıkları ekler; bunların hepsi tüketime yansır. Güzergâh dosyasında
`signal` sütunu yoksa ışıklar temsili aralıklarla (380–720 m) yerleştirilir.
"Trafik seviyelerini karşılaştır" aynı koşuyu beş seviyede çalıştırır.
Ilıman havada trafik km başına tüketimi pek artırmayabilir (frenlerde daha az
enerji kaybedilir); soğukta ve sıcakta klima süresi nedeniyle belirgin artırır.

**3B sahne hakkında:** Yolun rakımı güzergâhın gerçek rakımıdır; yoldaki
virajlar ise temsilidir (güzergâh dosyaları harita koordinatı değil mesafe ve
yükseklik içerir). Sahne yalnızca çözücünün ürettiğini oynatır, kendi başına
hesap yapmaz. Duraklarda binen ve inen yolcular modelin kapı
açılışlarını ve yolcu sayısını izler; kaldırımdaki yayalar yalnızca görseldir,
tüketimi etkilemez. Mağazalar ve sokak eşyaları da yalnızca görseldir. Kapılar
açıkken dış hava alttan girer, kabin havası üstten çıkar; renkleri modelin dış
hava ve kabin sıcaklığından gelir, akışın şekli temsilidir. Klavye: Boşluk oynat/durdur, ←/→ 10 s ileri/geri, 1–4 kamera,
X röntgen (kabin / güç aktarma / kapalı). Kabin röntgeni modelin kabin sıcaklığını
ve yolcu sayısını gösterir; model kabini tek bölge hesapladığı için kapı
ağızlarındaki dış hava yayılımı temsilidir.
three.js (MIT lisansı) `app/static/vendor/three/` içinde gelir, internet gerekmez.
WebGL olmayan bilgisayarda canlı veriler ve zaman çizelgesi yine çalışır.

**Serbest sürüş:** Ayrı bir sayfada, güzergâhlardan bağımsız altı şeritli geniş
bir test yolunda otobüs klavyeyle sürülür (W/↑ gaz, S/↓ fren, A/D/←/→ direksiyon,
G ileri/geri vites, E kapılar, R başa dön, 1–5 kamera, X röntgen). Geri vites
yalnızca dururken seçilir ve hız 10 km/h ile sınırlıdır. "Araç ayarları"
bölümünde boş ağırlık, motor gücü ve torku, rejen sınırı, batarya kapasitesi,
başlangıç şarjı, lastik ve hava direnci katsayıları değiştirilebilir; bu ayarlar
aracın bir kopyasına uygulanır, kütüphanedeki araç değişmez. Yol bölümlerden oluşur; her
bölümün uzunluğu, eğimi ve yüzeyi (yeni asfalttan stabilize yola kadar)
düzenlenir. Yüzey, yuvarlanma direncini bir çarpanla ölçekler; çarpanlar ağır
taşıt lastikleri için tipik değerlerdir, ölçülmedi. Sürüş sırasındaki rakamlar
tarayıcıdaki basit bir modelden gelen tahminlerdir; "Sürüşü modelde hesapla"
kaydedilen hız, eğim, yüzey ve kapı izini asıl çözücüye verir ve tam rapor üretir.

**Halk otobüsü görevi:** Serbest sürüş sayfasında ikinci mod. Tepeli, ızgara
planlı bir şehirde bir hat (19K ya da 42) sürülür: dört şeritli caddeler,
her kavşakta trafik ışığı, sağdan giden, ışıkta duran ve kavşakta dönen
araçlar var. Duraklara sırayla uğranır: kaldırıma yanaşıp durulur, kapılar
açılır (E), yolcular iner ve biner, kapılar kapatılır. Yön tarifi ve mini
harita bir sonraki durağı gösterir; rotadan çıkılırsa yol yeniden bulunur.
Kırmızı ışık, çarpışma, kaçırılan durak, durakta bırakılan yolcu ve kaldırıma
çıkmak puan düşürür. Yolcu sayısı durakta değişir; hem canlı modeldeki kütle
hem de "Sürüşü modelde hesapla" ile asıl modele giden kayıt bunu izler.

**Hat ve sefer:** Bir hattın bir günü. Hat dosyası (`library/line/*.yaml`) durakları,
durak başına zirve biniş sayısını ve saat bantlarına göre sefer aralığı / trafik /
talep çarpanını verir. Her bant ve yön için bir temsilî sefer tam araç modeliyle
sürülür ve sefer sayısıyla çarpılır: gereken otobüs, doluluk, bekleme, günlük
enerji, otobüs başına enerji ve şarj ihtiyacı. Sefer aralığı değiştirilerek
"deneme" aynı günde temelle yan yana hesaplanır. `adana_ornek` hattı ÖRNEK
veridir (`tools/make_adana_example.py` üretir); gerçek GTFS / AVL / bilet verisi
geldiğinde değiştirilir.

**Dizel araç:** `powertrain: diesel` olan araç `temsasim/diesel.py` ile koşar:
tekerlekten motora geri; aks ve şanzıman kayıp haritaları (vites başına), retarder
boşta sürüklemesi, AMT vites stratejisi (devir penceresi, tork rezervi), yakıt
sertifikalı haritadan, motor freninde sıfır, duruşta rölanti; yardımcı tüketiciler
ve klima kompresörü krank miline mekanik yük; kabin ısıtması motor soğutma suyundan,
yetmezse yakıtlı ısıtıcıdan. Çıktı l/100 km, g CO₂/km, rölanti yakıtı, vites payları,
motor çalışma noktaları ve yakıttan tekerleğe kapalı enerji dengesi; rapor
`report_diesel.py`. Simülasyon, serbest sürüş, hat ve sefer, sıcaklık taraması ve
trafik karşılaştırması dizel aracı da kabul eder. Bileşenler
`library/engine/`, `library/transmission/` altındadır; kurulumu
`docs/vecto_dosyalarindan_dijital_ikiz.md` anlatır.

**VECTO köprüsü:** `temsasim/vecto.py` yerel VECTO sürümünü (depoya dahil değil,
`2026_09_03-VECTO-5.1.4/` klasöründe) başsız çalıştırır, `.vmod` / `.vsum`
çıktısını okur, güzergâhı `.vdri` olarak yazar ve VECTO'nun sürdüğü izi bizim
çözücüye verir. `tests/test_vecto.py` VECTO jenerik şehir otobüsünde yol yükü
kalemlerini karşılaştırır (VECTO yoksa atlanır). `docs/` klasöründe VECTO sürücü
ve vites mantığı analizi bulunur.

Formdaki sayılar Türkçe yazılabilir (`95,5`). Birimler mühendislik birimidir
(kW, rpm, km/h, %); dosyaya dönüştürülerek yazılır. Silinen bileşenler
`library/.cop_kutusu/` klasörüne taşınır, geri alınabilir.

## Quick start (command line)

```bash
pip install -r requirements.txt

python -m temsasim list                                  # what's in the library
python -m temsasim describe maraton_13_dizel             # the diesel twin

# one run, with the HTML report
python -m temsasim run --vehicle maraton_13_dizel --cycle hhddt65 \
    --ambient 35 --solar 800 --passengers 40 --report out/yaz.html

# the same route in heavy traffic (traffic lights, queues, stop-and-go)
python -m temsasim run --vehicle maraton_13_dizel --route demo_hilly_city --traffic yogun

# fuel from -15 to +40 degC
python -m temsasim sweep --vehicle maraton_13_dizel --cycle wvuinter --solar 900

# build (or rebuild) a diesel twin from VECTO declaration files
python -m temsasim vecto-import "ARAÇ VERİLERİ/MARATON ÖRNEK ARAÇ" --name "Maraton 13 dizel" --glazing 28.58
python tools/build_maraton_twin.py --force              # the same, with the ANSA glazing areas built in

# the placeholder e-buses live in examples/; copy one into library/vehicle/ to use it
python -m temsasim compare --route demo_hilly_city --ambient -7 --occupancy-profile 55 \
    --vehicles maraton_13_dizel,citybus_12m_be

# score the model against a real vehicle log (battery-electric vehicles)
python -m temsasim validate --vehicle citybus_12m_be --log logs/bus_1042_2026-01-14.csv \
    --overlay out/bus_1042_overlay.csv

python -m pytest tests                                   # 145 tests
python -m temsasim gui                                   # the web interface
```

Standard cycles (`--cycle cbdbus`, `manhattan`, `ukbus6`, `newyorkbus`, `wvucity`, ...)
were extracted from the ADVISOR library by `tools/import_advisor_cycles.py`.

## The report

`--report` writes one self-contained HTML file. It has no external dependencies,
so you can email it or open it on an offline PC. It contains:

- **Energy flow through the vehicle**: a side view of the bus with live power
  flow from the roof pack to the motor, HVAC and auxiliaries. Flow turns green
  and reverses under regen, and the doors light up at stops. Scrub or play
  through the run. Append `#frame=N` to the URL to link to a specific moment.
- **Along the route**: speed, battery power, SOC and cabin/pack temperature on a
  shared distance axis.
- **Where the energy went**: a closed balance from the cells to every sink. If the
  residual is not ~0, the model has a bug.
- **What the cabin load is made of**: the heat flows into the cabin, with door
  openings visible as spikes.
- **Motor operating points** on the motor's efficiency map and torque envelope.
- **Projected pack life** from this duty repeated daily.

## Adding a component

Hardware is data. You add a motor, pack or HVAC unit by adding a YAML file,
without touching Python.

```yaml
# library/motor/supplier_x_280kw.yaml
kind: motor
name: supplier_x_280kw
peak_power_kw: 280            # engineering units are converted to SI on load
continuous_power_kw: 170
peak_torque_nm: 3100
continuous_torque_nm: 1600
max_speed_rpm: 3200
efficiency_map: maps/supplier_x_280kw_eff.csv    # measured grid, see _TEMPLATE
validated: false              # true only after `validate` passes on a real log
```

Then point a vehicle at it. A variant is a copy of a vehicle file with one
reference changed:

```yaml
# library/vehicle/citybus_12m_be_x280.yaml
kind: vehicle
name: City Bus 12m BE
variant: 350 kWh / supplier X motor
motor: motor/supplier_x_280kw.yaml
# ... other references unchanged
```

Rules that protect you:

- **Unknown keys are errors.** For example, `peak_torque_Nm` fails loudly
  instead of silently leaving the default in place.
- **Unit aliases**: any SI field also accepts an engineering-unit alias:
  `_kw`, `_kwh`, `_rpm`, `_kmh`, `_t`, `_mohm`, `_kj_per_k`. Giving both the
  SI field and its alias is an error.
- **Shared references**: components are referenced, not copied. Fixing one
  motor map fixes every bus that uses it.

Map and curve formats: `Map2D` CSV grids (see
`library/motor/maps/_TEMPLATE_efficiency_grid.csv`; a corner label such as
`torque_nm / speed_rpm` makes the loader convert rpm to rad/s), `LossMap`
long-format CSVs (`gear,input_speed_rpm,input_torque_nm,loss_nm`) for gearbox
and axle stages, and `Curve1D` either inline as `[[x, y], ...]` pairs, as a
two-column CSV, or as `{path, columns: [x, y], x_unit: rpm}` picking two
named columns of a wider CSV (an engine's full-load and drag curves share one
file).

A diesel vehicle names different slots:

```yaml
kind: vehicle
name: Maraton 13 dizel
powertrain: diesel
chassis: chassis/maraton_vecto.yaml            # masses, CdxA, per-axle tyre table
transmission: transmission/zf_12_tx_2210_td_a_133_203.yaml
engine: engine/daf_mx_11_h5_330.yaml
hvac: hvac/maraton_kabin.yaml
auxiliaries: auxiliary/maraton_yardimci.yaml
seats: 55
standees: 0
fuel_tank_l: 400
```

You normally do not write these by hand: the VECTO import writes them.

## Validation: how `validated: false` becomes `true`

`temsasim validate` replays a **measured** log through the model and reports
three things:

- total battery energy error (default tolerance ±5 %)
- SOC trajectory RMSE
- mean power error **split by phase** (driving / braking / standstill), with a
  diagnosis. A near-constant offset in every phase points to auxiliary or HVAC
  loads. An error concentrated in braking points to regen limits. An error
  concentrated in driving points to road load or the motor map.

Log CSV columns: `time_s`, `speed_kmh` (or `speed_mps`), and at least one of
`battery_power_kw` (positive = discharge) or `soc_percent`. Strongly recommended:
`grade_percent`, `passengers`, `doors_open`, `ambient_c`.

Suggested order of measurement, cheapest and highest-leverage first:

1. **Auxiliary loads**: clamp meter on each HV branch for one shift.
2. **Coast-down** (SAE J1263/J2263) to replace rolling resistance and CdA.
3. **Motor + inverter efficiency map** from the supplier (under NDA).
4. **Pack OCV and resistance vs temperature** from the cell supplier.
5. **HVAC COP curves at −7 and +35 °C**, and the door discharge coefficient,
   calibrated from cabin temperature drop at a winter stop.
6. **Ageing coefficients** fitted to the cell supplier's cycle-life data.

## Layout

```
temsasim/            solver package
  components/        chassis, driveline, motor, battery, hvac, auxiliary, aging,
                     engine (diesel), transmission (gearbox + axle + retarder)
  cycle.py           drive cycles, routes, power-aware driver model
  traffic.py         traffic levels, signal timing, queues and stop-and-go jams
  simulator.py       backward-facing solver (battery-electric) and energy balance; run() dispatches
  diesel.py          backward-facing solver for the combustion powertrain
  network.py         line and timetable simulation (one day of a bus line)
  validate.py        model-vs-measurement correlation
  report.py          HTML report with 2D bus diagram (English / Turkish)
  report_diesel.py   HTML report for a diesel run
  library.py         YAML loader, unit aliases, validation
  importers.py       tolerant readers: Excel/CSV maps, curves, GPX routes, CAN logs
  vecto_xml.py       reader for VECTO declaration XML (engine, gearbox, axle, tyre, vehicle)
  vecto_import.py    VECTO bundle -> library components and vehicle, with sources
  vecto.py           bridge to a local VECTO release (runs it, reads .vmod/.vsum)
  schema.py          forms generated from the component dataclasses
  app/               local web interface (Flask + one static page, offline)
library/             TEMSA data: the Maraton twin, Maraton coast-down chassis, cycles, routes, lines
examples/            placeholder battery-electric buses (merged in by the tests only)
docs/                calculation map, VECTO analyses, how a VECTO twin is built
tests/               physics, importer, interface, line, VECTO and diesel tests
baslat.bat           double-click launcher for the interface (Windows)
tools/               ADVISOR cycle importer, demo route generator, Maraton twin builder
advisor/             original NREL ADVISOR 2002 source, for reference
```

## Known limitations

- **Backward-facing only.** Good for energy, range and sizing. Not for controller
  development, because nothing in a backward model can fail to track the trace.
  A forward-facing model with a driver loop is the next architectural step (SIL/HIL).
- **No latent (humidity) load** in the cabin model. This understates cooling
  energy in humid coastal summers.
- **No lithium-plating term** in ageing. Cold-climate life projections are
  optimistic.
- The demo route is synthetic. Replace it with DEM-based elevation and real
  stop positions.
- **Diesel:** no traction interruption during AMT shifts, no torque converter
  (an AT box is run like an AMT), constant CdxA (no VECTO cross-wind
  correction), raw certificate fuel map (no WHTC weighting), and no log-based
  validation yet. The VECTO fuel comparison for the diesel twin is still to do.

ADVISOR is © Alliance for Sustainable Energy, LLC, under a permissive license
(`advisor/license.txt`). temsasim does not contain ADVISOR code; it uses
ADVISOR's published drive-cycle data.
