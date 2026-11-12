# Veri kütüphanesi

Simülatörün bütün verisi bu klasördedir. Python kodu hiçbir aracı ya da sayıyı
içermez; her şey buradaki dosyalardan okunur. Kural: **bir bileşen bir kez
tanımlanır, onu kullanan her araç ona referans verir.** Bir motor haritası
düzeltildiğinde o motoru kullanan bütün araçlar düzelir.

## Klasörler

| Klasör | İçerik | Dosya biçimi |
|---|---|---|
| `vehicle/` | Araçlar: bileşen referansları, koltuk, güç aktarma tipi | YAML, `kind: vehicle` |
| `chassis/` | Kütle, yol yükü (Cd·A ya da ölçülmüş CdxA, yuvarlanma ya da aks/lastik tablosu, coast-down), lastik yarıçapı | YAML |
| `engine/` | Dizel motor: anma değerleri, yakıt haritası, tam yük ve sürükleme eğrisi (VECTO sertifikasından) | YAML + `maps/*.csv` |
| `transmission/` | Dizel: vites oranları, vites başına kayıp haritası, aks oranı ve kayıp haritası, retarder, vites stratejisi | YAML + `maps/*.csv` |
| `motor/` | Elektrikli: çekiş motoru + invertör, verim haritası | YAML + `maps/*.csv` |
| `battery/` | Elektrikli: hücre, paket, OCV ve direnç eğrileri, ısıl parametreler | YAML |
| `driveline/` | Elektrikli: aktarma oranı ve verimi | YAML |
| `hvac/` | Kabin geometrisi, yalıtım, ısıtma/soğutma sistemi, COP eğrileri | YAML |
| `auxiliary/` | Yardımcı tüketiciler (24 V yükleri, direksiyon, kompresör) | YAML |
| `aging/` | Elektrikli: batarya yaşlanma katsayıları | YAML |
| `cycle/` | Standart hız çevrimleri (ADVISOR'dan) | CSV: `time_s, speed_mps` |
| `route/` | Güzergâhlar: mesafe, rakım, hız sınırı, durak, ışık, yüzey, rüzgâr, durak adı/bekleme/biniş/iniş | CSV (çözücü okur) + aynı adlı YAML (güzergâh oluşturucunun bölüm tanımı; varsa düzenlenebilir) |
| `scenario/` | Senaryolar: güzergâh/çevrim + araçlar + koşullar + yük + trafik | YAML |
| `line/` | Hat ve tarife: duraklar, biniş, saat bantları | YAML |
| `testlog/<kampanya>/` | Test ekibinin ölçüm kayıtları ve `meta.yaml` (koşullar, ölçülen yakıt) | CSV + YAML |
| `.cop_kutusu/` | Arayüzden silinen dosyalar; geri alınabilir | |

Bir aracın hangi bileşenlerden oluştuğunu güç aktarma tipi belirler:

```
battery_electric : chassis, driveline, motor, battery, hvac, auxiliaries, aging
diesel           : chassis, transmission, engine, hvac, auxiliaries
```

## Her dosyada bulunması gerekenler

```yaml
kind: engine                     # klasörle aynı
name: DAF TRUCKS N.V. MX-11 H5 330
description: >                   # NEREDEN geldi: dosya, sertifika no, test, tarih
  VECTO motor sertifikasından içe aktarıldı (...xml). Sertifika e4*2017/2400*..., tarih 2022-04-29.
validated: true                  # yalnızca ÖLÇÜLMÜŞ veri ise; kabul/varsayım içeriyorsa false
notes: ...                       # kabuller, sınırlar, nelerin doğrulanması gerektiği
```

`validated: false` olan bileşen raporlarda ve arayüzde "ölçülmedi" diye
işaretlenir; araç bir tane bile böyle bileşen içeriyorsa sonuç "yol gösterici"
sayılır. Sayıların yanındaki yorumlar (`# KABUL`, `# ölçüldü: ...`) kaynağı
satır satır verir; bunlar silinmemelidir.

## Birimler

Çözücü SI çalışır; dosyalar mühendislik birimi kullanabilir. Alan adının
sonundaki ek birimi belirler ve okunurken çevrilir:

`_kw` (kW) · `_kwh` · `_rpm` · `_kmh` · `_t` (ton) · `_mohm` · `_kj_per_k`
· `_nm`, `_kg`, `_m`, `_m2`, `_s`, `_c` (°C) olduğu gibi.

Bilinmeyen bir alan adı hatadır (sessizce atlanmaz): `peak_torque_Nm` gibi bir
yazım hatası varsayılanı yerinde bırakmak yerine yüklemeyi durdurur.

## Haritalar ve eğriler

- **Izgara (Map2D)** `maps/*.csv`: köşe hücresi eksenleri adlandırır
  (`torque_nm / speed_rpm`), ilk satır hız, ilk sütun tork, içi değer.
  `rpm` yazan eksen okunurken rad/s'ye çevrilir.
- **Kayıp haritası (LossMap)** uzun biçim: `gear, input_speed_rpm, input_torque_nm, loss_nm`
  (VECTO şanzıman ve aks sertifikalarının biçimi).
- **Eğri (Curve1D)**: dosya içinde `points: [[x, y], ...]`, iki sütunlu CSV ya da
  `{path, columns: [x, y], x_unit: rpm}` ile geniş bir CSV'nin iki sütunu.

## Veri nereden gelir

| Veri | Kaynak | Nasıl girer |
|---|---|---|
| Dizel motor, şanzıman, aks, lastik | Homologasyonun VECTO XML dosyaları | *Veri yükle → VECTO dosyaları* ya da `python -m temsasim vecto-import` |
| Kütle, CdxA, koltuk, boyut | Tamamlanmış araç dosyası, CFD raporu, tartı | VECTO içe aktarma; elle düzeltme formdan |
| Elektrik motoru verim haritası | Tedarikçi / dinamometre (Excel, CSV) | *Veri yükle → Motor verim haritası* |
| Güzergâh | GPX, mesafe–rakım tablosu, KML (planda) | *Veri yükle → Güzergâh* |
| Test kayıtları | Test ekibi CAN/Dewesoft (xlsx) | `testlog/<kampanya>/` + `meta.yaml`; *Doğrulama* sayfası |
| Başka bir kütüphaneden öğe | Meslektaşın "İndir" ile aldığı zip paketi | *Veri yükle → Paket* ya da `python -m temsasim import paket.zip` |

## Kopyalama, indirme, paylaşma

Kütüphane sayfasında her öğenin (araç, bileşen, güzergâh, senaryo) ⋯ menüsü
aynı dört işi yapar:

- **Kopyala**: yeni adla çoğaltır; bileşenin harita CSV'leri de kopyalanır ve
  kopya onlara bağlanır, böylece birini düzenlemek ötekini bozmaz.
- **İndir (zip)**: öğeyi bağımlılıklarıyla paketler. Araç paketi = araç YAML +
  bütün bileşenleri + haritaları; senaryo paketi = senaryo + güzergâh + araçlar.
  Zip'in klasör yapısı kütüphaneyle birebir aynıdır (`engine/x.yaml`,
  `engine/maps/x.csv`), içinde `PAKET.yaml` neyin olduğunu yazar.
- **Dosyayı gör / düzenle**: YAML'ı olduğu gibi gösterir; kaydetmeden önce
  yükleyiciden geçirir, geçmezse dosyaya dokunmaz.
- **Sil**: `.cop_kutusu/` klasörüne taşır; başka bir öğe onu kullanıyorsa
  reddeder. Kütüphane sayfasının altındaki **Çöp kutusu** paneli silinenleri
  listeler; "Geri al" dosyayı eski adıyla yerine koyar, "Boşalt" kalıcı siler.

"Tümünü indir" bütün kütüphaneyi zip yapar; başka bir bilgisayarda *Veri
yükle → Paket* ile açılır. Paket alırken var olan öğeler için üç seçenek:
olanı koru, paketle değiştir (eskisi çöp kutusuna), yeni adla yan yana koy
(`_2` eki, paket içindeki referanslar da çevrilir).

Komut satırı: `python -m temsasim export vehicle/maraton_13_dizel`,
`export library`, `import paket.zip --mode rename`, `copy engine/daf_mx_11_h5_330 --name "Deneme"`.

## Bu kütüphanede ne var

- `vehicle/maraton_13_dizel.yaml`: Maraton 13 dizel ikizi. Motor, şanzıman, aks,
  lastik VECTO sertifikalarından (ölçülmüş); kütle ve koltuk tamamlanmış araç
  dosyasından; CdxA 3,528 m² CFD'den; kabin ve yardımcılar kabul değerleriyle
  (Mersin testinin rölanti yakıtıyla sınırlandı).
- `chassis/maraton_cd.yaml`, `maraton_nd.yaml`: 2026 coast-down kampanyasının
  yol yükü (mevcut ve yeni tasarım gövde).
- `route/mersin_adana_otoyol_gidis.csv`: Adana Batı gişeleri → Deliçay viyadüğü,
  44,3 km test parkuru; rakım SRTM 30 m, yol OpenStreetMap; duraklar CAN
  loglarından. Dönüş yönü için ters okunur.
- `testlog/mersin_adana_2026_09/`: 9 koşu (gündüz gidiş/dönüş, gece gidiş), test
  raporu TRM-150-006-03'ün koşul ve sonuç tablosu `meta.yaml` içinde.
- `route/sehir_ici_ornek_20_durak`, `route/otoyol_ornek_45_km`: güzergâh oluşturucu şablonları (düzenlenebilir).
- `scenario/`: Mersin–Adana gündüz gidiş 18 t (test tekrarı), şehir içi yaz zirvesi, otoyol kış gecesi.
- Yer tutucu elektrikli otobüsler burada değil, `examples/vehicle/` altındadır.
