# temsasim: bus energy simulation

A route-based energy, range and battery-life simulator for battery-electric city
buses. It works the same way as NREL ADVISOR (backward-facing, quasi-static) and
adds the physics that decide real-world e-bus range, which ADVISOR does not have:

| | ADVISOR 2002 | temsasim |
|---|---|---|
| Cabin climate | constant accessory load | lumped cabin thermal model: envelope UA, ventilation, **door-opening air exchange** (Brown–Solvason), passenger and solar gains, heat pump / resistive / fuel-fired heating with COP curves |
| Battery | Rint | Rint with Arrhenius resistance, **cold charge-acceptance derating** (winter regen loss), lumped pack thermal and conditioning power |
| Ageing | none | calendar and cycle fade (Wang et al. 2011 form) projected over service life |
| Duty | speed trace | speed trace **or real route**: elevation, speed limits, stops, occupancy profile, power-aware driver |
| Auxiliaries | constant | electrified steering, air compressor duty scaled by stops, DC-DC losses |
| Trust | none | closed energy balance with visible residual, unvalidated-data warnings, **model-vs-measurement validation tool** |

> **Every component in `library/` is placeholder data.** The numbers are
> plausible industry values, not TEMSA measurements. Each file is marked
> `validated: false`, and every report says so on its face until real data
> replaces it.

## Arayüz (önerilen başlangıç)

**`baslat.bat` dosyasına çift tıklayın.** İlk seferde gerekli paketleri kurar,
sonra tarayıcıda arayüzü açar (`http://127.0.0.1:8765`). Sunucu yalnızca bu
bilgisayarda çalışır, şirket ağına açılmaz. İnternet gerekmez.

| Bölüm | Ne yapar |
|---|---|
| **Kütüphane** | Tüm araçlar ve bileşenler; hangisi ölçümle doğrulandı, hangisi hâlâ varsayılan. |
| **Veri yükle** | Motor verim haritası, güzergâh (GPX / tablo), batarya ve klima eğrileri. Dosyayı olduğu gibi bırakın: Excel ya da CSV, `;` ya da `,`, ondalık virgül, ızgara ya da uzun liste, rpm ya da rad/s, verim % / kesir ya da kayıp kW, hepsi otomatik algılanır. Önizlemede yanlış görünen bir ayarı tek tıkla değiştirirsiniz; onaylamadan hiçbir şey kaydedilmez. |
| **Araç kur** | Bileşenleri açılır listelerden seçerek araç ya da varyant oluşturma. |
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
python -m temsasim describe citybus_12m_be               # an assembled bus

# one run, with the HTML report (2D bus diagram + charts)
python -m temsasim run --vehicle citybus_12m_be --route demo_hilly_city \
    --ambient -7 --occupancy-profile 55 --report out/winter.html

# the same run in heavy traffic (traffic lights, queues, stop-and-go)
python -m temsasim run --vehicle citybus_12m_be --route demo_hilly_city --traffic yogun

# consumption and range from -15 to +40 degC
python -m temsasim sweep --vehicle citybus_12m_be --route demo_hilly_city \
    --occupancy-profile 55 --solar 900

# two variants on an identical duty
python -m temsasim compare --route demo_hilly_city --ambient -7 --occupancy-profile 55 \
    --vehicles citybus_12m_be,citybus_12m_be_resistive

# score the model against a real vehicle log
python -m temsasim validate --vehicle citybus_12m_be --log logs/bus_1042_2026-01-14.csv \
    --overlay out/bus_1042_overlay.csv

python -m pytest tests                                   # 107 tests
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
`library/motor/maps/_TEMPLATE_efficiency_grid.csv`), and `Curve1D` either inline
as `[[x, y], ...]` pairs or as a two-column CSV.

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
  components/        chassis, driveline, motor, battery, hvac, auxiliary, aging
  cycle.py           drive cycles, routes, power-aware driver model
  traffic.py         traffic levels, signal timing, queues and stop-and-go jams
  simulator.py       backward-facing solver and energy balance
  validate.py        model-vs-measurement correlation
  report.py          HTML report with 2D bus diagram (English / Turkish)
  library.py         YAML loader, unit aliases, validation
  importers.py       tolerant readers: Excel/CSV maps, curves, GPX routes, CAN logs
  schema.py          forms generated from the component dataclasses
  app/               local web interface (Flask + one static page, offline)
library/             component data (all PLACEHOLDER), cycles, routes
tests/               physics, importer and interface tests
baslat.bat           double-click launcher for the interface (Windows)
tools/               ADVISOR cycle importer, demo route generator
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

ADVISOR is © Alliance for Sustainable Energy, LLC, under a permissive license
(`advisor/license.txt`). temsasim does not contain ADVISOR code; it uses
ADVISOR's published drive-cycle data.
