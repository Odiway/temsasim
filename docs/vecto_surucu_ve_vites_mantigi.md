# VECTO 5.1.4 – Sürücü, Frenleme ve Vites Değiştirme Mantığı Kaynak Kod Analizi

**Kaynak:** `vecto-source/` (https://code.europa.eu/vecto/vecto, commit `0ccd8fe9`, "prepare 5.1.4-RC", 3 Eylül 2026).
Tüm dosya yolları `vecto-source/VectoCore/VectoCore/` altına göredir; satır numaraları bu commit için geçerlidir.

---

## İçindekiler

1. Dosya haritası
2. Simülasyon mimarisi: istek-yanıt zinciri
3. Sürücü modeli (`Driver.cs`): temel sürüş aksiyonları
4. Sürücü stratejisi (`DefaultDriverStrategy.cs`): ne zaman gaz, ne zaman fren
5. Sürüş modları (`DriverModes.cs`): DRIVE ve BRAKE
6. Frenleme fiziği ve fren gücü arama
7. Şanzıman modelleri (AMT/MT, AT, APTN)
8. Vites polygon'ları (shift polygon)
9. Vites değiştirme stratejileri
10. Sabitler ve varsayılan parametreler
11. Kod notları ve gözlemler

---

## 1. Dosya haritası

| Konu | Dosya |
|---|---|
| Sürücü aksiyonları (gaz, fren, coasting, roll, durma) | `Models/SimulationComponent/Impl/Driver/Driver.cs` (1519 satır) |
| Sürücü stratejisi (ileri bakış, fren tetiği, EcoRoll, PCC, Stop/Start) | `Models/SimulationComponent/Impl/Driver/DefaultDriverStrategy.cs` |
| DRIVE / BRAKE modları ve cevap zincirleri | `Models/SimulationComponent/Impl/Driver/DriverModes.cs` |
| Sürücü ivme/yavaşlama eğrisi ve fren mesafesi | `Models/SimulationComponent/Data/AccelerationCurve.cs` |
| Fren bileşeni | `Models/SimulationComponent/Impl/Brakes.cs` |
| AMT/MT şanzıman modeli | `Models/SimulationComponent/Impl/Gearbox/AbstractAMTGearbox.cs` |
| AT (tork konvertörlü) şanzıman modeli | `Models/SimulationComponent/Impl/Gearbox/APTGearbox.cs` |
| Ortak şanzıman tabanı | `Models/SimulationComponent/Impl/AbstractGearbox.cs` |
| Vites stratejisi tabanı | `Models/SimulationComponent/Impl/Shiftstrategies/BaseShiftStrategy.cs` |
| AMT "EffShift" stratejisi | `.../Shiftstrategies/AMTShiftStrategyOptimized.cs` |
| MT stratejisi | `.../Shiftstrategies/MTShiftStrategy.cs` |
| AT stratejisi | `.../Shiftstrategies/ATShiftStrategyOptimized.cs` |
| Elektrikli araç (BEV/IEPC) stratejisi | `.../Shiftstrategies/PEVAMTShiftStrategy.cs` |
| Hibrit kontrolcü tarafından yönetilen strateji | `.../Shiftstrategies/HybridCtlShiftStrategy.cs` |
| Strateji seçimi (fabrika) | `Models/SimulationComponent/Impl/ShiftStrategyFactory.cs` |
| Vites polygon geometrisi | `Models/SimulationComponent/Data/Gearbox/ShiftPolygon.cs` |
| Deklarasyon modu sabitleri ve polygon hesabı | `Models/Declaration/DeclarationData.cs` |
| Simülasyon sabitleri | `Configuration/Constants.cs` |
| Çekiş kesintisinde hız düşüşü ön-işlemcisi | `Models/SimulationComponent/Impl/VelocitySpeedGearshiftPreprocessor.cs` |

**Dikkat:** `Models/SimulationComponent/Impl/DefaultDriverStrategy.cs` (kök dizindeki) dosyasının tamamı yorum satırıdır, ölü kopyadır. Canlı sınıf `Impl/Driver/DefaultDriverStrategy.cs` içindedir.

---

## 2. Simülasyon mimarisi: istek-yanıt zinciri

VECTO ileri yönde değil, **geri yönde (tekerlekten motora)** hesap yapan, **mesafe tabanlı** bir simülatördür. Aktarma organları bir zincir olarak bağlanır:

```
DrivingCycle → Driver → Vehicle → Wheels → Brakes → AxleGear → (Angledrive) → Gearbox → (TorqueConverter) → Clutch → Engine
```

Her simülasyon adımında sürüş çevrimi sürücüye "şu `ds` mesafeyi, şu hedef hız ve eğimle git" ister (`DistanceBasedDrivingCycle.DriveDistance`, satır 337). Sürücü bir **ivme** seçer; bu ivme zincir boyunca her bileşende tork ve devire çevrilir ve sonunda motor bir **yanıt** döndürür. Yanıt tipleri (`VectoCommon/Models/Response*.cs`):

| Yanıt | Anlamı |
|---|---|
| `ResponseSuccess` | Çalışma noktası geçerli, adım kabul edildi. |
| `ResponseOverload` | Motor/EM istenen gücü veremiyor (tam yük eğrisinin üstü). |
| `ResponseUnderload` | Çalışma noktası motor sürükleme (drag) eğrisinin altında, yani motor freni yetmiyor, fren gerekli. |
| `ResponseEngineSpeedTooHigh` | Motor devri sınırı aşıldı. |
| `ResponseSpeedLimitExceeded` | Coasting sırasında araç hedef hızı aşacak. |
| `ResponseGearShift` | Şanzıman vites değiştirmek istiyor (AMT'de çekiş kesintisi). |
| `ResponseFailTimeInterval` | Zaman adımı uygunsuz (AT'de powershift süresi vb.), `DeltaT` ile tekrar. |
| `ResponseDrivingCycleDistanceExceeded` | Adım mesafesi kısaltılmalı (`MaxDistance`). |
| `ResponseDryRun` | "Deneme" isteği; `DeltaFullLoad`, `DeltaDragLoad`, `DeltaEngineSpeed` gibi sapmaları taşır. |

Bu yanıt tipleri sürücünün **karar ağacının temelidir**: sürücü bir aksiyon dener, gelen yanıta göre başka bir aksiyona geçer.

**Dry-run ve arama:** Sürücü çoğu zaman doğrudan geçerli bir ivme bulamaz. Bu durumda `dryRun = true` ile deneme istekleri atar ve `SearchAlgorithm.Search` (`VectoCommon/Utils/SearchAlgorithm.cs`) ile ivmeyi veya fren gücünü iteratif olarak arar (secant/line-search karışımı). Kriter genellikle `DeltaFullLoad` (hızlanmada) veya `DeltaDragLoad` (coasting/frende) sıfırlanmasıdır.

**Kinematik:** Mesafe `ds` ve ivme `a` verildiğinde zaman adımı `VectoMath.ComputeTimeInterval` ile ikinci derece denklemden bulunur: `ds = a/2·dt² + v·dt`. Çözüm yoksa (araç `ds`'e varamadan duruyorsa) `ds` durma mesafesine kısaltılır (`VectoCommon/Utils/VectoMath.cs:314`).

---

## 3. Sürücü modeli: `Driver.cs`

### 3.1 Sürüş aksiyonları

`DrivingAction` enum'u (satır 19): `Halt=0, Roll=2, Coast=4, Accelerate=6, Brake=-5`. Bu değerler `.vmod` çıktısında `DriverAction` sütunu olarak yazılır.

| Aksiyon | Ne yapar | Ana fonksiyon |
|---|---|---|
| **Accelerate** | Hedef hıza ulaşmak için gereken ivmeyi hesaplar, sürücü eğrisiyle sınırlar, motor veremezse tam yük eğrisinde çalışma noktası arar. Hız tutma da bu aksiyonla olur (hedef = mevcut hız). | `DrivingActionAccelerate` (119) |
| **Coast** | Gaz bırakılmış, debriyaj kapalı, vites takılı. Motor sürükleme eğrisinde çalışma noktası aranır (motor freni). | `DrivingActionCoast` (324) |
| **Roll** | Vites boşta / debriyaj açık. Aktarma organı güç talebi sıfırlanacak şekilde ivme aranır. | `DrivingActionRoll` (349) |
| **Brake** | Hedef hıza yavaşlamak için gereken ivme hesaplanır, motor freni yetmezse servis freni gücü aranır. | `DrivingActionBrake` (628) |
| **Halt** | Araç durmuş, zaman tabanlı adım (hedef hız = 0 olmalı). | `DrivingActionHalt` (1266) |

### 3.2 İvme hesabı: `ComputeAcceleration` (satır 1208)

Ortalama hız yaklaşımıyla:

```
dt = 2·ds / (v0 + v_hedef)
a  = (v_hedef − v0) / dt
```

Ardından `DriverData.AccelerationCurve.Lookup(v0)` ile sürücünün izin verdiği maksimum ivme ve yavaşlama arasına **kırpılır**. `limitByDriverModel=false` ile çağrılırsa (fren hedef mesafesine tam oturma için) kırpma yapılmaz.

### 3.3 Sürücü ivme/yavaşlama eğrisi (`.vacc`)

`AccelerationCurve.cs`. Hıza bağlı `acc` ve `dec` değerleri doğrusal interpolasyonla okunur. Deklarasyon modunda tüm araçlar için sabit `Truck.vacc`:

| v [km/h] | acc [m/s²] | dec [m/s²] |
|---|---|---|
| 0 | 1.0 | -1.0 |
| 25 | 1.0 | -1.0 |
| 50 | 0.643 | -1.0 |
| 60 | 0.5 | -0.5 |
| 120 | 0.5 | -0.5 |

**Fren mesafesi** (`ComputeDecelerationDistance`, satır 103): eğri parça parça doğrusal olduğundan her segmentte `a(v) = k·v + d` çözülür; sabit segmentlerde `s = (v2²−v1²)/(2a)`, değişkenlerde üstel çözüm (`t = ln((v2k+d)/(v1k+d))/k`). Bu, sürücü stratejisinin "frene ne zaman basmalıyım" sorusunun fizik temelidir.

### 3.4 Çalışma noktası arama: `SearchOperatingPoint` (satır 1039)

Verilen `ds` ve başlangıç ivmesiyle iteratif arama. Kriter seçimi:

- `actionRoll` (debriyaj açık): `Gearbox.PowerRequest` → 0 (şanzıman girişinde güç sıfır).
- `coastingOrRoll`: `DeltaDragLoad` → 0 (motor sürükleme eğrisi üstünde).
- Normal: `DeltaFullLoad` → 0 (motor tam yük eğrisi üstünde).
- `ResponseEngineSpeedTooHigh` geldiyse: `DeltaEngineSpeed` → 0.

AT şanzımanlarda tork konvertörü nedeniyle arama başarısız olursa `SetTCOperatingPointATGbxCoastOrRoll` (satır 484) yarı-ileri yönlü bir yedek çözüm dener: önce TC giriş devrini rölantinin %1 üstüne sabitler, motor torku aralıkta ise ivmeyi bu TC çıkış torkuna göre arar; olmazsa uygun motor devri aranır.

### 3.5 Sürücü sınırı: `LimitAccelerationByDriverModel` (satır 881)

Bulunan ivme `AccelerationCurve` sınırları dışındaysa kırpılır. `LimitationMode.NoLimitation` sadece Roll aksiyonunda ve TC yedek çözümünde kullanılır (yokuş aşağı boşta araç doğal olarak hızlanabilir).

---

## 4. Sürücü stratejisi: `DefaultDriverStrategy.cs`

Strateji iki moddan birindedir: **`DrivingModeDrive`** ve **`DrivingModeBrake`**. Ana giriş `DoHandleRequest` (satır 146).

### 4.1 Her adımda yapılanlar (sıra önemli)

1. PCC (tahmini hız sabitleyici) aktifse `HandlePCC`.
2. EcoRoll aktifse ve PCC segment dışındaysa `HandleEcoRoll`.
3. EcoRoll/PCC motoru kapatmamışsa motor açık kabul edilir (`CombustionEngineOn = true`).
4. **BRAKE modundaysa:** `GetNextDrivingAction(ds)` ile yeni, daha yakın bir fren hedefi var mı diye bakılır ve `BrakeTrigger` güncellenir. Araç `BrakeTrigger.TriggerDistance`'a ulaştıysa DRIVE'a dönülür.
5. **DRIVE modundaysa:** `UpdateDrivingAction` ile `NextDrivingAction` güncellenir. Sonra:
   - Kalan mesafe sıfıra yakınsa (`DriverActionDistanceTolerance` = 0.25 m) ya da tahmini süre `LowerBoundTimeInterval` (0.25 s) altındaysa ya da fren aralığı çok kısaysa → **BRAKE moduna geç**, `BrakeTrigger = NextDrivingAction`.
   - Mevcut adım aksiyon noktasını aşacaksa → `ResponseDrivingCycleDistanceExceeded` ile adım aksiyon noktasında kesilir.
6. Seçili modun `Request`'i çağrılır; hedef hız `min(MaxVehicleSpeed, targetVelocity)` ile sınırlanır.

### 4.2 İleri bakış: `GetNextDrivingAction` (satır 559)

```
lookahead = max(2·ds, v[km/h] · LookAheadDistanceFactor, 1.2 · durma_mesafesi + ds)
```

`LookAheadDistanceFactor` deklarasyonda 10, yani 80 km/h'de 800 m ileriye bakılır. Çevrimden bu mesafedeki tüm hedef hız girişleri alınır; mevcut hızdan **düşük** olanlar için `GetDrivingBehaviorEntry` ile bir aday üretilir. Adaylar arasından:

- `BrakingStartDistance` en küçük olan (en erken frenlenmesi gereken) ve
- `CoastingStartDistance` en küçük olan seçilir; ikisi aynı tetik noktasına aitse coasting adayı, değilse fren adayı döner.

### 4.3 Aday üretimi: `GetDrivingBehaviorEntry` (satır 589)

```
brakingDistance  = ComputeDecelerationDistance(hedefHız) + BrakingSafetyMargin (0.1 m)
coastingDistance = ComputeCoastingDistance(v, girdi)
```

- Look-ahead coasting (LAC) kapalıysa ya da coasting mesafesi negatifse → aksiyon **Braking**, `coastingDistance = brakingDistance`.
- Aksi halde mevcut hız `LookAheadCoasting.MinSpeed` (deklarasyon: 50 km/h) üstündeyse aksiyon **Coasting**; değilse coasting devre dışı (−1).

`ActionDistance = min(CoastingStartDistance, BrakingStartDistance)`.

### 4.4 Coasting mesafesi: `ComputeCoastingDistance` (satır 622)

Enerji dengesi ile:

```
E_şimdi  = m·g·h_şimdi  + ½·m·v²
E_hedef  = m·g·h_hedef  + ½·m·v_hedef²          (overspeed izinliyse v_hedef += overspeed)
ΔE       = E_şimdi − E_hedef
F_direnç = F_hava(v, v_hedef) + F_yuvarlanma(ort. eğim) + (P_şanzıman + P_aks + P_EM_sürükleme − P_ICE_sürükleme) / v
coastingDistance = ΔE / (DF · F_direnç)
```

`m` toplam kütle + tekerlek indirgenmiş kütlesi. **Karar faktörü DF** (`LACDecisionFactor.Lookup`):

```
DF = 2.5 − 1.5 · f(v_hedef) · g(v_düşüş)
f(v_hedef): 0 @ ≤48 km/h → 1 @ ≥52 km/h   (LAC-DF-Vtarget.csv)
g(v_düşüş): 1 @ ≤9 km/h  → 0 @ ≥11 km/h   (LAC-DF-Vdrop.csv)
```

Yani hedef hız yüksek ve hız düşüşü küçükse DF = 1 (tam coasting mesafesi); büyük hız düşüşünde veya düşük hedef hızda DF = 2.5 (coasting mesafesi 2.5 kat kısaltılır, yani sürücü daha geç gaz bırakır). ICE sürükleme kaybı seri hibrit/FCHV/IEPC-S için 0 alınır.

### 4.5 Overspeed (hız aşımı)

`IsOverspeedAllowed` (satır 662): sürücü modelinde etkinse veya PCC seçenek 3 otoyolda aktifse, hedef hız `OverSpeed.MinSpeed` (50 km/h) üstündeyse ve aşım sonucu araç maksimum hızını geçmiyorsa izin verilir. Deklarasyonda aşım 2.5 km/h (PCC seçenek 3 otoyolda 5 km/h). `ApplyOverspeed` otobüslerde `MaxBusSpeed` ile kırpar.

### 4.6 EcoRoll: `HandleEcoRoll` (satır 401)

Durum makinesi `EcoRollOff → PreActivation → EcoRollOn`. Tüm koşullar (`AllConditionsMet`):

- hız ≥ `EcoRoll.MinSpeed` (60 km/h),
- hız ≤ hedef+overspeed − 2 km/h,
- eğim negatif,
- direnç kuvvetlerinden hesaplanan doğal ivme `[AccelerationLowerLimit, AccelerationUpperLimit]` = [0, 0.1 m/s²] aralığında,
- gaz pedalı boşta (önceki adımda motor tam sürükleme noktasında),
- fren basılı değil.

Koşullar `ActivationPhaseDuration` (2 s) boyunca sürerse EcoRoll açılır: şanzıman ayrılır (`DisengageGearbox = true`) ve `WithEngineStop` tipindeyse motor kapatılır. Hız hedefin `UnderspeedThreshold` kadar altına inerse ya da fren basılırsa kapanır. AT şanzımanda `ATEcoRollReleaseLockupClutch` ise şanzıman ayrılmaz, sadece kilit debriyajı açılır ve ICE sürükleme kaybı hesaba katılır.

### 4.7 PCC (Predictive Cruise Control): `HandlePCC` / `UpdatePCCState` (satır 225-342)

Ön-işlemci (`PCCSegmentPreprocessor`) çevrimde "tepe-çukur" segmentlerini bulur. İki kullanım durumu:

- **UseCase1** (tepeye yaklaşırken erken coasting): en düşük hız noktasından önce, hız ≥ hedef − `Underspeed` (8 km/h), mevcut enerji hem segment sonu/`PreviewDistanceUseCase1` (1500 m) enerjisini hem de en düşük hız noktası enerjisini coasting kayıpları dahil karşılıyorsa.
- **UseCase2** (inişte hedefin altında coasting ile toparlanma): en düşük hız noktasından sonra, hız hedef − 1 km/h altında ama ≥ max(hedef − Underspeed, `MinSpeed` 50 km/h), enerji `PreviewDistanceUseCase2` (1000 m) için yeterliyse.

Aktif durumlarda şanzıman ayrılır, EcoRoll tipine göre motor kapatılır; hız hedef + 1 km/h'i geçerse segment içine dönülür. `PCCinterrupt`: UseCase1'de hız hedef − 1.05·Underspeed'in altına düşerse.

### 4.8 Motor Stop/Start (durakta)

`HandleEngineStopStartDuringVehicleStop` (satır 474): PTO aktif değilse, araç `ActivationDelay` (2 s) boyunca durduysa motor kapatılır; `MaxEngineOffTimespan` (120 s) sonra tekrar açılır.

---

## 5. Sürüş modları: `DriverModes.cs`

Her modun `Request`'i (`AbstractDriverMode.Request`, satır 8) önce `DoHandleRequest` çağırır, sonra **bu adımda hızlanıldıysa** adım sonundaki hız sonraki hedef hızı geçecek mi diye kontrol eder (`CheckRequestDoesNotExceedNextAction`). Geçecekse adım mesafesi kısaltılır (`ds/2` veya tam fren noktasına kadar) ve gerekiyorsa sabit hızla sürülür.

### 5.1 `DriverModeDrive`

`DoHandleRequest` (satır 71): overspeed uygulanır; PTO yol süpürme modunda hız `PTODriveMinSpeed` altına düşürülmez.

**Vites takılı/debriyaj kapalı (ya da AT) ise** `HandleRequestEngaged` en fazla 3 kez denenir. Akış:

1. `FirstAccelerateOrCoast`:
   - PCC aktifse önce **Coast**.
   - Overspeed izinli ve araç hedef hızın üstündeyse **Coast**; coast sonucu hız hedefin altına inecekse **Accelerate**; Overload gelirse (motor kapalı / BEV) **Accelerate**.
   - Aksi halde: AT ve şanzıman ayrık ise **Coast**, değilse **Accelerate**.
2. İlk cevaba göre ikinci aksiyon:
   - `Underload` → hız > 0 ve overspeed izinliyse **Coast** (olmazsa Brake); değilse **Brake** (AT + konvertör vitesinde `overrideAction = Accelerate`: motor itiyor, kalan güç frende harcanıyor, "creeping").
   - `EngineSpeedTooHigh` → **Brake** (ivmeyi düşürerek).
   - `SpeedLimitExceeded` → **Brake** (EcoRoll AT'de kilit debriyajı kapatılır).
3. İkinci cevaba göre üçüncü aksiyon:
   - `GearShift` → **Roll** (çekiş kesintisinde boşta sürükleme); Roll Overload→Coast, Underload→Brake, SpeedLimitExceeded→Brake(→Accelerate).
   - `Overload` (hız>0) → **Coast**; Coast GearShift→Coast, SpeedLimitExceeded→ AT konvertör vitesinde +1 km/h ile Coast, aksi halde Brake; APTN/IEPC'de Overload→Accelerate.

**Vites boşta/debriyaj açık ise** `HandleRequestDisengaged`: araç duruyorsa debriyaj kapanana kadar `ResponseFailTimeInterval`; değilse **Roll**. Roll Underload → debriyaj kapalıysa engaged akışı, değilse Brake; SpeedLimitExceeded → Brake.

### 5.2 `DriverModeBrake`

İki faz: **Coast** ve **Brake**. `ResetMode` ile Coast'tan başlar.

`DoHandleRequest` (satır 430):

1. Araç hızı ≤ `BrakeTrigger.NextTargetSpeed` ise hedefe varılmış demektir → `HandleTargetspeedReached` (hedef hızda sürmeye devam; Accelerate/Roll ve yanıt zincirleri).
2. Her adımda fren mesafesi **yeniden hesaplanır**: `BrakingStartDistance = TriggerDistance − (ComputeDecelerationDistance(v_hedef) + 0.1 m)`.
3. Tetik aksiyonu `Braking` ise faz doğrudan **Brake**.
4. Coast fazında `CheckSwitchingToBraking`: daha yakın başka bir fren tetiği varsa `BrakeTrigger` değiştirilir. Fren başlangıcına kalan süre < 0.25 s ya da mesafe < 0.25 m ise **Brake** fazına geçilir. Değilse adım fren başlangıç noktasında kesilir. Hız < `MinVelocityForCoast` (5 km/h) ise doğrudan Brake fazı.
5. Coast fazı → `DoCoast`: debriyaj kapalıysa **Coast**, açıksa **Roll** (`maxVelocity = max(hedef, mevcut)`). Yanıtlar:
   - Underload → **Brake** (coasting sürücünün max yavaşlamasından fazla yavaşlatıyor), fren noktasına geldiyse faz Brake.
   - Overload → Accelerate/Roll (yavaşlama sınırı nedeniyle).
   - DistanceExceeded (araç duracak) → **Brake** ile tam durma noktasına.
   - EngineSpeedTooHigh → Brake.
   - Sonrasında GearShift / SpeedLimitExceeded / vites değiştirmiş MT-AMT-IHPC-APTN-ATSerial için Overload/Underload üç tura kadar Roll/Brake/Accelerate ile çözülür.
6. Brake fazı → `DoBrake`:
   - `targetDistance`: hız < 5 km/h ise tetik mesafesi; hedef hız 0 ise tetik − 0.1 m. Bu, aracın **tam durma noktasına oturmasını** sağlar (`AdaptDecelerationToTargetDistance`).
   - Araç durmuş ve hedef 0 ise (ör. yokuşta duruyor) küçük bir hızla Accelerate denenir, Underload'da Brake.
   - Normalde `DrivingActionBrake(nextTargetSpeed, targetDistance)`.
   - AT/hibritte `null` dönerse (vites değişti) fren gücü sıfırlanıp 3 kez tekrarlanır.
   - `Overload` (istenen yavaşlama sağlanamıyor, ör. yokuş yukarı): debriyaj açıksa Roll → gerekirse Brake/Accelerate/Coast; kapalıysa fren sıfırlanıp tekrar Brake, hâlâ Overload ise Accelerate/Roll, vites değiştiyse tekrar dene.
   - `GearShift`: fren sıfırlanıp tekrar Brake, Overload'da Roll.

**Özet frenleme stratejisi:** Sürücü önce gaz bırakır (coasting, motor freni), fren mesafesine gelince servis frenini kullanır. Fren gücü her adımda, motor sürükleme noktasında kalacak şekilde aranır; yani **servis freni ancak motor freni yetmediğinde** devreye girer.

---

## 6. Frenleme fiziği ve fren gücü arama

### 6.1 `DrivingActionBrake` (Driver.cs:628)

1. `op1 = ComputeAcceleration(ds, v_hedef)` → sürücü eğrisiyle sınırlı yavaşlama.
2. `IncreaseDecelerationToMaxWithinSpeedRange`: yavaşlama sürücü maksimumuna eşitse ve adım sonu hızında eğri daha büyük yavaşlama veriyorsa büyük olanı al.
3. `AdaptDecelerationToTargetDistance`: hedef mesafe verilmişse tam o noktada hedef hıza varacak ivmeyi hesapla, daha yumuşaksa onu kullan.
4. Zincire istek: `Success` ve `Overload` doğrudan döner; `Underload`, `GearShift`, `EngineSpeedTooHigh` → `SearchBrakingPower`.
5. Fren gücü bulununca gerçek istek; vites değiştiyse (AT/hibrit) `null` döner (üst katman tekrar dener). Negatif fren gücü çıkarsa `ResponseOverload`.

### 6.2 `SearchBrakingPower` (Driver.cs:908)

Başlangıç sapması `deltaPower`: debriyaj kapalı ve vites takılıysa `DeltaDragLoad` (motor sürükleme eğrisine uzaklık), değilse `Gearbox.PowerRequest`. `DataBus.Brakes.BrakePower` değişkeni aranır; her iterasyonda dry-run istek atılır. AT konvertör vitesinde `forceLineSearch`, kriter 1.1 ile ağırlıklandırılır; Accelerate override'da motorun çalışabildiği ilk geçerli nokta alınır. Arama başarısız olursa AT'de %1'lik adımla ikinci deneme.

### 6.3 `Brakes.cs`

Basit bileşen: `BrakePower / ortalama açısal hız` kadar tork eklenir (`brakeTorque`), negatif fren gücü hata fırlatır, her adım sonunda `BrakePower = 0`. Modal çıktıda `P_brake_loss` olarak yazılır.

### 6.4 Debriyaj

`Clutch.ClutchClosed(absTime)` = şanzımanın `GearEngaged(absTime)` değeri (Clutch.cs:339). Kayma kayıpları 1-2. viteste sürüşte ve 1. viteste fren olmadan frenlemede eklenir.

---

## 7. Şanzıman modelleri

### 7.1 `AbstractGearbox` (ortak)

- `GearboxLoss()`: önceki adımın iletim + atalet tork kaybı × giriş devri.
- `ComputeShiftLosses`: AT powershift kaybı = `T_gbx_in · Δn_debriyaj/2 · PowershiftShiftTime`.
- `ConsiderShiftLosses`: sadece otomatik, pozitif tork, kilitli vites için.

### 7.2 `AbstractAMTGearbox` (AMT ve MT)

Temel kavramlar:

- **`EngageTime`**: yeni vitesin takılı olacağı zaman. Vites değişimi tetiklenince `EngageTime = absTime + TractionInterruption`.
- **`GearEngaged(absTime)`** (satır 26): `!DisengageGearbox && EngageTime ≤ absTime (±TractionInterruption/20) && !postponeEngage`.
- `DisengageGearbox`: sürücü (EcoRoll/PCC) tarafından kontrol edilir.

`Request` akışı (satır 89):

1. Araç duruyorsa `EngageTime = absTime`, shift zamanları sıfırlanır. Halted ise `EngageTime = absTime + dt`.
2. Vites takılacaksa ve `Disengaged` ise `ReEngageGear`: araç duruyorsa `InitGear`, değilse `Engage` ile vites seçilir; `LastUpshift`/`LastDownshift` güncellenir. Çok kısa adımda (`dt < TargetTimeInterval/10`) takma ertelenir.
3. Giriş torku: `T_in = T_out · (n_out_ort / n_in_ort) + kayıp + atalet/ratio`.
4. **Durma öncesi ayırma** (satır 149-168): sürücü `Braking`+`Brake` aksiyonunda, (eğim negatif veya giriş devri rölanti altı) ve (fren basılı veya giriş torku negatif) ve hız < `DisengageWhenHaltingSpeed` → vites ayrılır (`RequestGearDisengaged`). `DisengageWhenHaltingSpeed` = 1. vitesin sıfır torkta downshift devri × 1.05'in araç hızı karşılığı (`EngineeringDataAdapter.cs:733`).
5. Vites takılıysa `RequestGearEngaged`: zincire istek; `Success` ve vites değişimi izinliyse `_strategy.ShiftRequired(...)` sorulur. Gerekiyorsa `Disengaged = true`, `EngageTime = absTime + TractionInterruption` ve **`ResponseGearShift`** döndürülür. Sürücü bu yanıtı alınca Roll aksiyonuna geçer.
6. Vites boştaysa `RequestGearDisengaged`: `EngageTime`'ı aşan adımda `ResponseFailTimeInterval`; kalan kesinti süresi < 0.25 s ve < %10 ise kesinti kısaltılır. Giriş torku sıfır değilse yine zincire iletilir (motor rölantiye gidiyor).

`DoCommitSimulationStep`: sürüş davranışı Driving/Braking'e değiştiğinde shift zamanlayıcıları sıfırlanır (yeni fazda hemen vites değiştirilebilsin).

### 7.3 `APTGearbox` (AT, tork konvertörlü)

- Vites pozisyonu `GearshiftPosition(gear, TorqueConverterLocked)`: aynı vites numarası "C" (konvertör) veya "L" (kilitli) olabilir.
- Kalkış: araç duruyor ve `n_out > 0` ya da pozitif tork → 1. vites, `Disengaged = false`.
- `RequestEngaged`: etkin oran/kayıp haritası kilit durumuna göre seçilir. Powershift kaybı `_powershiftLossEnergy` `PowershiftShiftTime` boyunca eşit dağıtılarak giriş torkuna eklenir. Konvertör vitesinde `TorqueConverter.Request` çağrılır; motor kapalıysa disengaged istek.
- Vites değişimi: strateji `ShiftRequired` derse `ResponseGearShift`; `ConsiderShiftLosses` ise ilk kez `ResponseFailTimeInterval(DeltaT = PowershiftShiftTime)` döner (sürücü adımı buna göre keser), ardından `RequestAfterGearshift=true` ile yeni vites `Engage` edilir. Çekiş kesintisi yok.
- `RequestDisengaged`: pozitif güç → `ResponseOverload`, negatif → `ResponseUnderload` (sürücüye "fren yap"), sıfır → rölanti kontrolcüsü.
- `GearEngaged`: Halted değil, Disengaged değil, EcoRoll ile ayrılmıyor.

### 7.4 APTN / IEPC / PEV şanzımanlar

AT-N (hibrit/elektrikli, konvertörsüz otomatik) ve IEPC `APTGearbox`/`AbstractAMTGearbox` türevleridir; `APTNShiftStrategy` ve `PEVAMTShiftStrategy` ile çalışır. `PEVAMTShiftStrategy` `IPEVGearbox`, `IIEPCGearbox`, `IAPTNGearbox` kabul eder.

---

## 8. Vites polygon'ları (shift polygon)

Her vites için bir **downshift** ve **upshift** çizgisi vardır (tork–devir düzleminde). `ShiftPolygon.cs`:

- `IsBelowDownshiftCurve(T, n)`: çalışma noktası downshift çizgisinin **solunda** → küçült.
- `IsAboveUpshiftCurve(T, n)`: upshift çizgisinin **sağında** → büyüt.
- Test, çizgi segmenti ile noktanın vektörel çarpımının işaretiyle yapılır (`IsLeftOf` / `IsRightOf`).
- `InterpolateDownshift(n)`: downshift çizgisinde o devirdeki tork (üst strateji "tork rezervi" hesabında maksimum olarak kullanır).

Deklarasyon modunda hesaplama (`DeclarationData.Gearbox.ComputeShiftPolygon`, satır 1105):

| Şanzıman | Yöntem |
|---|---|
| AMT, APTN, IHPC | `ComputeEfficiencyShiftPolygon` ("whitebook 2016"), tam yük eğrisi × 0.98 marjı, %7 devir marjı |
| MT | `ComputeManualTransmissionShiftPolygon` |
| AT (seri/powersplit) | `TorqueConverter.ComputeShiftPolygon` (satır 1731): downshift dikey çizgi 700 rpm; upshift 900 rpm'den 1150 rpm'de tam yük torkuna doğru eğik çizgi; ilk viteste downshift, son viteste upshift yok |
| Elektrikli (E2/IEPC) | `ComputeElectricMotorShiftPolygon`: downshift NP80low ve rated×0.1 arası tam yük/sürükleme çizgileri; upshift max devir × 0.9 |

Mühendislik modunda kullanıcı `.vgbs` dosyası verebilir; verilmezse aynı fonksiyonlar kullanılır.

---

## 9. Vites değiştirme stratejileri

### 9.1 Strateji seçimi (`ShiftStrategyFactory`)

| Şanzıman | İş tipi | Strateji |
|---|---|---|
| AMT | konvansiyonel, paralel hibrit (ICE modunda) | `AMTShiftStrategyOptimized` ("EffShift") |
| AMT | paralel hibrit sadece batarya | `ParallelHybridBatteryOnlyModeShiftStrategy` |
| AMT | BEV, seri hibrit | `PEVAMTShiftStrategy` |
| AMT | FCHV | `FCHVAMTShiftStrategy` |
| MT | tümü | `MTShiftStrategy` |
| AT seri/powersplit | konvansiyonel, paralel hibrit | `ATShiftStrategyOptimized` |
| AT seri/powersplit | BEV, seri hibrit, batarya-only | `APTNShiftStrategy` |
| APTN | hibrit/BEV/IEPC | `APTNShiftStrategy` |
| IHPC | IHPC | `AMTShiftStrategyOptimized` |
| Hibrit kontrolcü vites seçimi yapıyorsa | | `HybridCtlShiftStrategy` ailesi |

### 9.2 `BaseShiftStrategy` (ortak yardımcılar)

- Polygon testleri (yukarıda).
- `SpeedTooLowForEngine(gear, n_out)`: `n_out · ratio < rölanti`.
- `SpeedTooHighForEngine(gear, n_out)`: `n_out · ratio ≥ min(vites maks devri, N95h − 1 rpm)`.
- `EstimateAccelerationForGear(gear, n_out)`: 100 m ileri bakışla ortalama eğim; `a = (P_maks(n) − P_şanz − P_aks − P_hava − P_yuv − P_eğim) / v / (m + m_tekerlek)`.

### 9.3 AMT: `AMTShiftStrategyOptimized` ("EffShift")

**Kurulum:** test aktarma organı (`TestPowertrain`) oluşturulur; `VelocitySpeedGearshiftPreprocessor` çekiş kesintisi sırasında hız düşüşünü (5-120 km/h × eğim aralığı) önceden simüle eder. `MaxStartGear`: `StartSpeed` (8 km/h) ile motor devri `rölanti + 0.065·(nominal−rölanti)` üstünde kalan en yüksek vites.

**Kalkış vitesi** (`InitStartGear`): `MaxStartGear`'dan 1'e doğru, motor devri > rölanti ve tork rezervi ≥ `StartTorqueReserve` (0.2) olan ilk vites.

**Hareket halinde başlangıç vitesi** (`InitGear`): en yüksekten aşağı, polygon içinde kalan ve rezerv ≥ 0.2 olan vites; devir normalize < 0.065 ise bir alt vites.

**`DoCheckShiftRequired` sırası** (satır 144):

1. Araç duruyorsa → hayır.
2. **Acil**: 1. viteste devir rölanti altı → evet; devir rölanti altı/üstü oldukça bir alt/üst vites.
3. **PTO yol süpürme**: `PTODriveRoadsweepingGear`'a doğru.
4. `TimeBetweenGearshifts` (2 s) geçmemişse → hayır.
5. `CheckDownshift` → değiştiyse evet.
6. `CheckUpshift` → değiştiyse evet.

**Upshift** (`CheckUpshift`, satır 204):

- Sürücü `Accelerating`/`Driving` değilse (coasting/frenleme) büyütme yok.
- Son küçültmeden `UpshiftAfterDownshiftDelay` (6 s) geçmediyse yok.
- `DoCheckUpshift`: upshift çizgisinin sağındaysa bir üst vites; **vites atlama**: daha üst vitesler dry-run ile denenir, downshift çizgisinin sağında ve `T_in ≤ min(dinamik tam yük, downshift çizgisi)` kaldıkça devam.
- Ardından **erken büyütme** (`CheckEarlyUpshift`, satır 272): yakıt tüketimine göre:
  - Motor torku sürükleme × 0.7'nin (`DragMarginFactor`) altındaysa yok.
  - Kesinti sonrası tahmini hız > 5 km/h olmalı.
  - 1..`AllowedGearRangeFC` (2) üst vites için: oran < `RatioEarlyUpshiftFC`, downshift çizgisinin sağında, kesinti sonrası motor devri ≥ `MinEngineSpeedPostUpshift`, güç < maksimum, rezerv ≥ `TorqueReserve` (0; rezerv yetmezse NTq98h-NP98h arasında `AccelerationFactor` 0.5 ile azaltılmış ivme torkuyla tekrar denenir).
  - Adayın yakıt tüketimi `fc_next < fc_mevcut · RatingFactorCurrentGear (0.97)` ve en düşükse seçilir. Yani **en az %3 daha verimli** olmalı.
- Seçilen vites için `EstimateAccelerationForGear < UpshiftMinAcceleration` (0.1 m/s²) ise büyütme iptal (atlanan viteste bir alt vites denenir).

**Downshift** (`CheckDownshift`, satır 264):

- Son büyütmeden `DownshiftAfterUpshiftDelay` (6 s) geçmediyse yok.
- `DoCheckDownshiftBase`: downshift çizgisinin solundaysa bir alt vites.
- Değilse **erken küçültme** (`CheckEarlyDownshift`): aynı FC mantığı, upshift çizgisinin solunda kalan alt vitesler arasında %3 daha verimli olan.

### 9.4 MT: `MTShiftStrategy`

AMT'nin sadeleştirilmiş hali, FC tabanlı erken vites yok. Farklar:

- Üst vitese zorunlu geçiş (devir çok yüksek) sadece üst vites downshift çizgisinin sağındaysa (hız düşüş oranı dikkate alınarak) yapılır.
- `DoCheckUpshift`: atlama için rezerv ≥ `TorqueReserve`.
- `DoCheckDownshift`: downshift çizgisi (hız düşüş oranı ≤ 2) veya **genişletilmiş** downshift polygon'u (`ExtendedShiftPolygon`); yokuş yukarı düşük hızda frenleme istisnası; devir rölanti altı özel durumu.

### 9.5 AT: `ATShiftStrategyOptimized`

Vites pozisyonu C/L ayrımı taşır. `DoCheckShiftRequired` (satır 130) sırası:

1. **Kalkış**: ayrıkken `n_out > 0` → 1C.
2. **Ayırma**: frenleme + negatif tork + hız < `DisengageWhenHaltingSpeed`; kilitliyken `n_in = 0`; 1C'de `T_out<0`, `T_in>0`; frenlemede 1C'de devir ≤ rölanti. Motor itmesi gerekiyorsa (`T_out>0`) ayırma yapılmaz.
3. **Acil**: devir < rölanti → küçült; devir ≥ min(vites maks, N95h) → üst vites ivme > 0 veya kilitliyse büyüt.
4. **Upshift** (`CheckUpshift`): `TimeBetweenGearshifts` sonrası:
   - Kilitli veya kilitli varyantı olan viteste `CheckUpshiftToLocked` (L→L+1, C→L): üst viteste eşdeğer tork upshift çizgisinin sağındaysa ve ulaşılabilir ivme ≥ min(`UpshiftMinAcceleration`/`CLUpshiftMinAcceleration` 0.1, sürücü ivmesi).
   - C→C+1 (`CheckUpshiftTcTc`): konvertör çalışma noktası hesaplanır; motor devri > min(700 rpm, oran·(N80h−150)), tork ≤ maks − atalet, ivme ≥ min(0.1, sürücü ivmesi).
   - Erken büyütme: C→L için **yük kademesi tablosu** (`CheckUpshiftFromTC`): motor yükü yüzdesi Schmitt-trigger eşikleriyle (`LoadStageThresholdsUp/Down`) 1-6 yük kademesine ayrılır; `ShiftSpeedsTCToLocked` tablosundan (iniş/düz/yokuş × a_min/a_max, ör. 90/120/165 rpm) eğim ve ivmeye göre geçiş devri interpolasyonla bulunur; `n_out` eşiği geçince kilitle. L→L+1/2 için `CheckEarlyUpshiftFromLocked`: AMT'deki FC mantığı, `ATLookAheadTime` (1.5 s) ile hız tahmini, `AllowedGearRangeFC` AT için 1, N95h üstü devir ve negatif maks tork elenir.
5. **Downshift** (`CheckDownshift`): downshift çizgisinin solunda ve alt vites kilitli değilse ya da alt viteste upshift çizgisinin sağında değilse küçült; **hızlanma sırasında** hız hedefin 10 km/h altında ve ivme negatifse alt vites daha fazla rezerv veriyorsa küçült; erken küçültme FC tabanlı (sadece kilitli alt vitesler, C→L atlama yasak).

### 9.6 Elektrikli: `PEVAMTShiftStrategy` (BEV, seri hibrit, IEPC, batarya-only paralel hibrit)

Yakıt yerine **elektriksel güç** (`GetECRating`, pozitif = reküperasyon, negatif = tahrik) ile değerlendirir:

- Derate aktifse `DeRatedEmShiftPolygon` kullanılır.
- Kalkış: `DriveOffStandstill` bayrağı; hedef hıza ulaşana, EM NP80low devrini aşana veya tam yüke gelene kadar vites değişimi yok. Kalkış vitesi: rezerv ≥ `StartTorqueReserve` sağlayan en yüksek vites.
- Upshift: polygon + atlama; atalet torku reküperasyon sınırını aşmamalı. Erken büyütme: `SelectEffshiftGear` – tahrikte `ec_next > ec_mevcut · 0.97` (daha az negatif), reküperasyonda `ec_next > ec_mevcut / 0.97`.
- **Frenlemede özel vites seçimi** (`SelectBrakingGear`, satır 377): downshift çizgisinin solunda ve sürücü frenliyorsa, EM devrini `PEV_TargetSpeedBrakeNorm · (n_max/n_rated − 1) + 1) · n_rated` hedef devrine en yakın (üstünde) tutan alt vites seçilir. Amaç reküperasyon verimini maksimize etmek.
- Maksimum reküperasyon çizgisine %10 yakınken erken küçültme yapılmaz.

### 9.7 Hibrit kontrolcü stratejileri

`HybridCtlShiftStrategy`, `HybridCtlATShiftStrategy`, `HybridCtlIHPCShiftStrategy`: `ShiftRequired` kararı doğrudan hibrit kontrolcüsünden (`Controller.ShiftRequired`, `Controller.NextGear`) alınır. Hibrit kontrolcü, ICE/EM güç dağılımını ve vitesi birlikte optimize eder (bu raporun kapsamı dışında, `HybridStrategy.cs`).

### 9.8 `APTNShiftStrategy`

`PEVAMTShiftStrategy` türevidir; tek fark çok kısa adımlarda (`dt < TargetTimeInterval/10`) vites değişimine izin vermez.

---

## 10. Sabitler ve varsayılan parametreler

### Simülasyon (`Configuration/Constants.cs`)

| Sabit | Değer | Kullanım |
|---|---|---|
| `TargetTimeInterval` | 0.5 s | hedef adım süresi |
| `LowerBoundTimeInterval` | 0.25 s | fren moduna geçiş eşiği, min adım |
| `MinVelocityForCoast` | 5 km/h | altında coasting yok, direkt fren |
| `DriverActionDistanceTolerance` | 0.25 m | aksiyon noktası toleransı |
| `BrakingSafetyMargin` (DefaultDriverStrategy) | 0.1 m | fren mesafesine eklenen pay |
| `ClutchClosingSpeedNorm` | 0.065 | debriyaj kapanma devri normalize |
| `GearboxLookaheadForAccelerationEstimation` | 100 m | vites için ivme tahmininde eğim |
| `LineSearchTolerance` | 0.5 W | |

### Sürücü (deklarasyon, `DeclarationData.Driver`)

| Parametre | Değer |
|---|---|
| LAC etkin / `LookAheadDistanceFactor` / min hız | evet / 10 / 50 km/h |
| LAC karar faktörü offset / scaling | 2.5 / 1.5 |
| Overspeed min hız / izin | 50 km/h / 2.5 km/h |
| EcoRoll min hız / aktivasyon / ivme aralığı | 60 km/h / 2 s / 0–0.1 m/s² |
| PCC enable / min hız / preview UC1 / UC2 / underspeed / overspeed UC3 | 80 / 50 km/h / 1500 m / 1000 m / 8 km/h / 5 km/h |
| Stop/Start gecikme / maks kapalı süre | 2 s / 120 s |

### Vites (`DeclarationData.Gearbox` ve `GearboxTCU`)

| Parametre | Değer |
|---|---|
| `MinTimeBetweenGearshifts` | 2 s |
| `UpshiftAfterDownshiftDelay` / `DownshiftAfterUpshiftDelay` | 6 s / 6 s |
| `UpshiftMinAcceleration` | 0.1 m/s² |
| `TorqueReserve` / `TorqueReserveStart` | 0 / 0.2 |
| `StartSpeed` / `StartAcceleration` | 8 km/h / 0.8 m/s² |
| `RatingFactorCurrentGear` | 0.97 |
| `RatioEarlyUpshiftFC` / `RatioEarlyDownshiftFC` | 24 (aks oranına bölünür) |
| `AllowedGearRangeFC` AMT / AT | 2 / 1 |
| `AccelerationFactor` / `VelocityDropFactor` | 0.5 / 1.0 |
| `ATLookAheadTime` | 1.5 s |
| `PowershiftShiftTime` | 0.8 s |
| `DragMarginFactor` | 0.7 |
| `MIN_SPEED_AFTER_TRACTION_INTERRUPTION` | 5 km/h |
| AT downshift / upshift düşük / yüksek devir | 700 / 900 / 1150 rpm |
| Yük kademesi eşikleri yukarı / aşağı [%] | 19.7, 36.3, 53.0, 69.7, 86.4 / 13.7, 30.3, 47.0, 63.7, 80.4 |

`GearboxTCU` statik yapıcısı `Declaration\EffShiftParameters.vtcu` dosyası varsa bu değerleri oradan ezer (satır 1024-1066).

---

## 11. Kod notları ve gözlemler

- **Ölü dosya:** `Impl/DefaultDriverStrategy.cs` tamamı yorumda; canlı sınıf `Impl/Driver/` altında. Kodu okurken karıştırmamak gerekir.
- **Yanıt tabanlı karar ağacı:** Sürücü mantığı "if-else" yerine deneme-yanıt zincirleriyle yazılmıştır. `DriverModes.cs`'teki `debug.Add("[DMD.HRE-7] ...")` etiketleri hangi dalın çalıştığını izlemek için en iyi referanstır (`DebugData`).
- **Fren = motor freni + servis freni:** `SearchBrakingPower` her zaman önce motoru sürükleme eğrisinde tutmaya çalışır; servis freni kalan farkı kapatır. Bu nedenle modal çıktıda `P_brake_loss` sıfırken araç yine de yavaşlıyor olabilir.
- **AMT çekiş kesintisi** gerçek bir fiziksel kesinti olarak modellenir (`ResponseGearShift` → Roll aksiyonu, `EngageTime`). AT'de kesinti yoktur, yerine `PowershiftShiftTime` boyunca kayıp enerjisi eklenir.
- **Vites stratejileri gerçek aktarma organına dokunmaz:** tüm "ne olurdu" soruları ayrı bir `TestPowertrain` kopyası üzerinde dry-run ile cevaplanır (`RequestDryRunWithGear`).
- **EffShift "en az %3 iyileşme" kuralı** (`RatingFactorCurrentGear = 0.97`) hem AMT hem AT hem elektrikli stratejide ortaktır; sürekli vites gezinmesini (hunting) engeller. Ek olarak 6 s'lik yön değişim gecikmeleri vardır.
- `CheckEarlyUpshift` içindeki `fcUpshiftPossible` değişkeni her zaman `true` (kodda TODO notu var), `CheckEarlyUpshiftBase` dolayısıyla hiç çağrılmıyor.
- `PCC` enerji hesabında yuvarlanma direnci için mevcut eğim kullanılıyor; `ComputeCoastingDistance`'ta ise hedefe ortalama eğim. Kodda `mk20211008` notuyla bu tutarsızlık işaretlenmiş.
