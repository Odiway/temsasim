# VECTO stable kaynak ağacı: neyi kullanıyoruz, neyi kullanmıyoruz

Kaynak: `vecto-stable/vecto-stable` (code.europa.eu/vecto/vecto, "stable" dalı; değişiklik
günlüğünün başı v5.1.3, 3 Ağustos 2026). 12 372 dosya, 770 MB. Lisans EUPL-1.2. Klasör
depoya girmez (`.gitignore`); bu belge ve ondan türetilenler girer.

Hüküm sütunu: **KOD** (algoritması alındı ya da alınacak), **VERİ** (tablosu / çevrimi
içe aktarılır), **BELGE** (okunur), **TEST VERİSİ** (karşılaştırma için), **GEREKMEZ**.

## 1. Üst klasörler

| Klasör | Boyut · dosya | Hüküm | Neden |
|---|---|---|---|
| `VectoCore/VectoCore` | 16 MB · 1 033 | KOD + VERİ | Hesap motorunun kendisi. Aşağıda ayrıntısı var. |
| `VectoCore/VectoCoreTest` | 464 MB · 6 688 | TEST VERİSİ (seçerek) | Ağacın %60'ı. Birim testleri ve onların araç dosyaları. İçinde otobüs işleri ve beklenen değerler var; VECTO'yu çalıştırmadan çoğu sonuç dosyası yok. |
| `Documentation` | 144 MB · 1 065 | BELGE | `User Manual/3-simulation-models/*.md` model başına bir sayfa (vites, sürücü, motor, yardımcılar). Geri kalanı sürüm notları, XML şemaları, görseller. |
| `Generic Vehicles` | 60 MB · 1 440 | TEST VERİSİ | Hazır örnek araçlar. Otobüs: `CompletedBus 31b2 / 34f / 39a`, `Group P31_32 … P39_40` (birincil otobüs), `SingleBus_*`, `CityBus_AT`. Kendi çözücümüzü aynı araçla koşup kıyaslamak için. |
| `VectoCommon` | 9 MB · 220 | KOD (az) | Birim tipleri, arayüzler. Bize gereken iki dosya: `InputData/EngineeringInputData.cs` (vites parametre arayüzü), `Utils/EnumerableExtensionMethods.cs` (`GetSection`). |
| `Testing` | 41 MB · 478 | GEREKMEZ | Bütünleşik test koşucuları. |
| `VECTOAux` | 3 MB · 195 | GEREKMEZ | Otobüs yardımcılarının eski Visual Basic sürümü; güncel hali `VectoCore/Models/BusAuxiliaries`. |
| `VECTO`, `VECTO3GUI`, `VECTO3GUI2020`, `*GUITest` | 9 MB · ~800 | GEREKMEZ | Masaüstü arayüzleri (VB / WPF). |
| `HashingTool`, `HashingCmd`, `XMLConverter*` | 18 MB · ~220 | GEREKMEZ | Beyan dosyası imzalama ve XML sürüm çevirme. |
| `Tools`, `Build`, `BuildTools`, `VectoConsole` | 12 MB · ~200 | GEREKMEZ | Derleme, lisans başlığı, komut satırı sarmalayıcıları. |

## 2. `VectoCore/VectoCore` içi

| Klasör | Dosya | Hüküm | Durum |
|---|---|---|---|
| `Models/SimulationComponent/Impl/Shiftstrategies` | 15 | **KOD** | **Alındı.** `AMTShiftStrategyOptimized` (EffShift) + `BaseShiftStrategy` → `temsasim/shift.py`. AT, MT ve elektrikli (PEV) stratejileri sırada. |
| `Models/Declaration/DeclarationData.cs` | 1 | **KOD** | **Alındı (vites kısmı).** `Gearbox`, `GearboxTCU` sabitleri ve `ComputeEfficiencyShiftPolygon`. Aynı dosyada sürücü, motor ataleti, tork konvertörü, PT1 sabitleri de var. |
| `Models/SimulationComponent/Data/Gearbox`, `…/Data/Engine` | 9 | **KOD** | **Alındı.** `ShiftPolygon` testleri, `EngineFullLoadCurve` karakteristik devirleri. |
| `Models/SimulationComponent/Impl/Gearbox`, `Clutch.cs` | 16 | KOD | Okundu; çekiş kesintisi, duruşta debriyaj ayırma, kalkış vitesi buradan. Debriyaj kayma modeli alınmadı. |
| `Resources/Declaration/MissionCycles` | 10 | **VERİ** | **Alındı.** Coach, Interurban, Suburban, Urban, Heavy Urban → `library/route/vecto_*.csv`. Kamyon çevrimleri gerekmez. |
| `Models/BusAuxiliaries` (HVAC, Electrics, Pneumatics, M06–M14) | 75 | KOD + VERİ | Kısmen. HVAC'ın güneş yükü kuralı alındı (aşağıda). Fan, kompresör, direksiyon pompası, alternatör tabloları sırada. |
| `Resources/Declaration/Buses`, `VAUXBus`, `VAUX` | 46 | VERİ | Sırada: kompresör haritaları (`*.acmp`), hava tüketimi (`DefaultActuationsMap.apac`), fan katsayıları, alternatör teknolojileri, HVAC güç tabloları. |
| `Models/SimulationComponent/Impl/DefaultDriverStrategy.cs`, `Driver.cs` | 2 | KOD | Sırada: ileri bakışlı coasting (LAC), overspeed, EcoRoll, PCC, dur-kalk. `docs/vecto_surucu_ve_vites_mantigi.md` ayrıntılı çözümleme içerir. |
| `Resources/Declaration/ADAS`, `LAC-DF-*.csv`, `VACC` | 5 | VERİ | Sırada: ADAS birleşimleri, coasting karar faktörü, sürücü ivme eğrisi (`Bus.vacc`). |
| `Resources/Declaration/VCDV`, `Models/…/CrossWindCorrection*` | 3 | KOD + VERİ | Sırada: hıza bağlı yan rüzgâr düzeltmesi CdxA(v). Bizde CdxA sabit. |
| `Resources/Declaration/Wheels.csv`, `FuelTypes.csv`, `WHTC-Weighting-Factors.csv`, `PT1.csv` | 4 | VERİ | İlk üçü zaten kullanılıyor; PT1 (motor tork kurulum gecikmesi) alınmadı. |
| `Resources/Declaration/*SegmentationTable.csv`, `Payloads.csv`, `CO2Standards` | 6 | VERİ | Araç grubu → görev ve yük tabloları. Beyan koşusunu taklit etmek istenirse. |
| `Models/SimulationComponent/Impl/ElectricMotor*`, `Battery*`, `Strategies` | ~40 | KOD | Sırada: elektrik motoru aşırı yük / ısıl model, batarya iç direnç tabloları, hibrit stratejiler. |
| `InputData` (JSON / XML okuyucular) | 219 | KOD (az) | XML beyan dosyalarını zaten okuyoruz. `FileIO/JSON/JSONTCUData.cs` `.vtcu` anahtarlarını verir; mühendislik modu dosyaları (`.veng`, `.vgbx`…) için okuyucu yok. |
| `OutputData` | 186 | BELGE | `.vmod` / `.vsum` sinyal adları; rapordaki sinyal listesi buna göre genişletilebilir. |
| `Ninject`, `Utils`, `Configuration` | 65 | GEREKMEZ | Bağımlılık enjeksiyonu, yardımcılar. `Configuration/Constants.cs` sabitler için okunur. |

## 3. Bulgular: bizde eksik ya da yanlış olanlar

1. **Vites mantığı** (yapıldı). Eski kural seti, aracı yokuşta 11. vitese indirip düz yolda
   12'de tutuyordu; gerçek TraXon tersini yapıyor. VECTO EffShift'e geçince ve yedi ayar
   değeri loglara oturtulunca vites uyumu %76'dan %87'ye (yalnız vites planında %89,5'e),
   seyir devri sapması dokuz koşunun yedisinde ±10 d/d içine indi (önce +14 … +80 d/d;
   kalan iki koşuda +33 ve +42 d/d).
2. **VECTO'da "Scania AMT" diye ayrı bir strateji yok.** Parametre arayüzünde
   "ACEA/Scania GS Parameters" başlığı altındaki 15 alan (`GearResidenceTime`,
   `Dn_Tq99L_high_min_1`…) dosyadan okunuyor ama hesapta hiç kullanılmıyor. Çalışan strateji
   `AMT - EffShift`; set point'leri: 2 s / 6 s / 6 s zamanlayıcılar, 0,97 pay, ±2 vites, 24
   toplam oran sınırı, 8 km/h kalkış hızı, 0,2 kalkış rezervi.
3. **Kalkış vitesi.** VECTO kuralı (8 km/h'te devir debriyaj kapanma devrinin üstünde kalan
   en yüksek vites) Maraton için 3. vitesi verir; loglardaki 45 kalkışın hepsi 3. vites.
4. **Klima.** Toplu kabin modeli güneş yükünü bütün cam alanı tam ışınım alıyormuş gibi
   hesaplıyordu. VECTO'nun otobüs HVAC modeli (`SSMRun.cs`) cam alanının 0,25'ini alır;
   bizim katmanlı kabin modelimiz Maraton için 0,26–0,41 veriyor. Düzeltildi
   (`solar_exposed_share = 0,30`): 35 °C / 800 W/m²'de klima gücü 9,4 → 6,6 kW.
5. **Frenleme.** VECTO retarder / motor freni için vites küçültmez; TraXon küçültür
   (devri 1 000 d/d üstünde tutar). Ek ayar olarak eklendi (`brake_downshift_speed_rpm`).
6. **Çekiş kesintisi.** VECTO'da AMT 1 s çekişsiz kalır ve araç hız kaybeder. Biz hız izini
   birebir izlediğimiz için bu enerjiye girmiyor; yalnız karar ve zamanlayıcılarda var.
7. **Sürücü.** VECTO 50 km/h üstünde hedef hız düşmeden önce gazı bırakır (LAC) ve yokuş
   aşağı 2,5 km/h aşıma izin verir. Bizim sürücü sabit ivmeyle frenler: ekibin "daha çok
   coasting yapabilir" gözlemi doğru, model bunu henüz yapmıyor.
8. **Yardımcılar.** Fan, hava kompresörü ve direksiyon pompası bizde sabit ortalama güç;
   VECTO'da teknolojiye ve göreve bağlı tablolar var.

## 4. Lisans

VECTO, EUPL-1.2 ile dağıtılır (`LICENSE.txt`). `temsasim/shift.py` VECTO kaynak kodundan
çevrildiği için türev eserdir ve aynı lisansla dağıtılır; `library/route/vecto_*.csv`
VECTO'nun veri dosyalarından üretilmiştir. Ayrıntı ve atıf: `THIRD_PARTY.md`. Depo herkese
açık olduğu için bu iki kalemin EUPL koşullarıyla yayımlandığı bilinmelidir; hukuki görüş
gerekiyorsa şirketin hukuk birimine danışılmalıdır.
