# Bu simülasyon neleri hesaplar

<!-- temsasim/catalogue.py üretir: `python -m temsasim hesaplananlar --write`. Elle düzenlemeyin. -->

Simülasyon bir aracı bir güzergâhta saniye saniye sürer ve enerjinin gittiği her yeri ayrı kalem olarak yazar. Aşağıda hesaplanan her büyüklük, birimi ve nerede görüldüğü var. Denklemler hesap haritasının ilgili bölümündedir.

12 başlık, 97 büyüklük. Aynı liste uygulamada sağ üstteki **Yardım** düğmesinde ve hesap haritasının başında da var.

| # | Başlık | Nerede | Büyüklük |
|---|---|---|---|
| 1 | [Koşu: yol yükü ve enerji dengesi](#1-koşu-yol-yükü-ve-enerji-dengesi) | Çalışma masası → Koşu | 11 |
| 2 | [Bataryalı elektrikli araç](#2-bataryalı-elektrikli-araç) | Çalışma masası → Koşu | 13 |
| 3 | [Dizel araç](#3-dizel-araç) | Çalışma masası → Koşu | 11 |
| 4 | [Vites mantığı ve şanzıman kalibrasyonu](#4-vites-mantığı-ve-şanzıman-kalibrasyonu) | Çalışma masası → Vites | 7 |
| 5 | [Kabin içi termal yönetim](#5-kabin-içi-termal-yönetim) | Çalışma masası → Kabin | 7 |
| 6 | [Süspansiyon, yalpa ve devrilme](#6-süspansiyon-yalpa-ve-devrilme) | Çalışma masası → Süspansiyon | 14 |
| 7 | [Güzergâh ve görev üretimi](#7-güzergâh-ve-görev-üretimi) | Kütüphane → Güzergâh · Senaryolar | 8 |
| 8 | [Karşılaştırma ve taramalar](#8-karşılaştırma-ve-taramalar) | Çalışma masası → Karşılaştır · Koşu | 4 |
| 9 | [Hat ve sefer (filo)](#9-hat-ve-sefer-filo) | Çalışma masası → Hat ve sefer | 8 |
| 10 | [Test verisi ve korelasyon](#10-test-verisi-ve-korelasyon) | Çalışma masası → Test verisi | 6 |
| 11 | [Serbest sürüş](#11-serbest-sürüş) | Çalışma masası → Serbest sürüş | 4 |
| 12 | [Veri ve dijital ikiz](#12-veri-ve-dijital-ikiz) | Kütüphane → Veri yükle · Araç kur | 4 |

## 1. Koşu: yol yükü ve enerji dengesi

Elektrikli ve dizel araçta ortak olan kısım: aracı hareket ettirmek için tekerlekte ne kadar güç gerektiği.

Nerede: **Çalışma masası → Koşu** · Kod: `simulator.py · diesel.py · chassis.py` · Denklemler: hesap haritası, `#adim` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| Yuvarlanma direnci | kW · kWh | Lastik katsayısı, kütle ve yol yüzeyinden; ölçüm varsa coast-down eğrisinden. |
| Hava direnci | kW · kWh | CdxA, hava yoğunluğu (sıcaklık, basınç) ve rüzgârdan. |
| Eğim direnci | kW · kWh | Kütle ve yolun eğiminden. |
| Atalet | kW · kWh | Kütle ile dönen parçaların (tekerlek, motor, şanzıman) eşdeğer kütlesinden. |
| Tekerlek gücü ve torku | kW · Nm | Dört direncin toplamı. |
| Anlık kütle | t | Boş kütle ile o andaki yolcu sayısından; yolcular duraklarda binip iner. |
| Fren enerjisi | kWh | Motorun ya da geri kazanımın karşılayamadığı yavaşlama. |
| Yardımcı tüketiciler | kW · kWh | Kompresör, direksiyon pompası, aydınlatma, kapılar; hıza ve gece/gündüze bağlı. |
| Karşılanamayan talep | kWh · s | Aracın gücü hız izini tutmaya yetmediği yerler; koşu 'yapılabilir' mi. |
| Mesafe, süre, ortalama hız, durak sayısı | km · s · km/h | Sürülen görevin özeti. |
| Veri tamlığı | – | Her bileşen için: veri ölçüm mü, kabul mü. Sonuç buna göre 'doğrulanmış' ya da 'gösterge' sayılır. |

## 2. Bataryalı elektrikli araç

Tekerlekten motora, invertöre ve bataryaya giden zincir.

Nerede: **Çalışma masası → Koşu** · Kod: `simulator.py · motor.py · battery.py · driveline.py` · Denklemler: hesap haritası, `#alt` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| Tüketim ve menzil | kWh/km · km | Bataryadan çekilen net enerji ve kullanılabilir kapasiteden. |
| Şarj durumu (SOC) izi | % | Her saniye bataryadan çekilen akımdan. |
| Batarya gerilimi, akımı, gücü | V · A · kW | Açık devre gerilimi (SOC'ye bağlı) ve iç dirençten; direnç sıcaklıkla değişir. |
| Motor + invertör verimi ve kaybı | % · kW | Verim haritasından (devir, tork); harita yoksa tepe verimden. |
| Aktarma kaybı | kW · kWh | Dişli ve diferansiyel verimi ile boşta dönme kaybından. |
| Batarya kaybı | kWh | İç dirençte ısıya giden enerji. |
| Geri kazanılan fren enerjisi | kWh · % | Motorun, bataryanın ve kontrol stratejisinin izin verdiği kadarı. |
| Kullanılamayan fren enerjisi | kWh | Batarya doluyken, hız çok düşükken ya da sınır aşıldığında frenlere giden kısım. |
| Batarya sıcaklığı | °C | Kayıp ısısı, ortam ve soğutma/ısıtma ile ısıl kütleden. |
| Yüksek voltaj ısıtma ve batarya soğutma enerjisi | kWh | Bataryayı çalışma sıcaklığında tutmak için harcanan enerji; kabinden ayrı sayılır. |
| C oranı ve Ah geçişi | C · Ah | Bataryanın ne kadar zorlandığı; yaşlanma hesabının girdisi. |
| En yüksek motor ve batarya gücü | kW | Koşu boyunca görülen tepe değerler. |
| Batarya yaşlanması ve ömür | % SOH · yıl | Takvim ve çevrim yaşlanması (sıcaklık, SOC, C oranı); State of Health'in ömür sonuna indiği yıl. |

## 3. Dizel araç

Tekerlekten aksa, şanzımana ve motora giden zincir; yakıt, motorun yakıt haritasından okunur.

Nerede: **Çalışma masası → Koşu** · Kod: `diesel.py · engine.py · transmission.py` · Denklemler: hesap haritası, `#dizel` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| Yakıt tüketimi | l/100 km · l · l/h | Her saniye motor devri ve torkuyla yakıt haritasından; anlık ve ortalama. |
| CO₂ | g/km | Yakılan yakıttan. |
| Rölanti yakıtı ve payı | l · % | Araç dururken ve debriyaj açıkken yakılan yakıt. |
| Motor devri, torku, yükü | d/d · Nm · % | Seçili vites ve tekerlek hızından; tam yük eğrisiyle karşılaştırılır. |
| Krank gücü | kW | Motorun krank milinde verdiği güç: çekiş, kayıplar ve yardımcılar. |
| Motor freni | kWh | Gaz kesikken motorun sürükleme torkuyla aldığı enerji. |
| Şanzıman, aks, retarder ve debriyaj kaybı | kW · kWh | Ölçülmüş kayıp haritalarından; retarder boşta dönme kaybı ayrı. |
| Fan ve PTO gücü | kW · kWh | Motor fanı ve motordan tahrik edilen yardımcılar. |
| Vites izi | vites | Her saniye hangi viteste gidildiği, vites değişim sayısı, nedenleri ve viteslerin zaman payı. |
| Kabine giden motor ısısı ve ısıtıcı yakıtı | kWh · l | Kışın kabini motorun atık ısısı ısıtır; yetmezse yakıtlı ısıtıcı. |
| Depo menzili | km | Depo hacmi ve tüketimden. |

## 4. Vites mantığı ve şanzıman kalibrasyonu

Şanzımanın ne zaman vites değiştireceği (VECTO AMT EffShift) ve ayarların test verisine oturtulması.

Nerede: **Çalışma masası → Vites** · Kod: `shift.py · shiftcal.py` · Denklemler: hesap haritası, `#vites` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| Vites hatları | d/d – Nm | Her vites için küçültme ve büyütme hattı; motorun tam yük eğrisinden. |
| Kalkış vitesi | vites | Düşük hızda devri debriyaj kapanma devrinin üstünde tutan en yüksek vites. |
| Vites kararı ve nedeni | – | Her saniye: acil (devir sınırı), vites hattı, verim, fren küçültmesi, talep karşılanmıyor. |
| Ölçümle vites uyumu | % | Loglanan vitesle aynı olan sürenin payı; ±1 vites içinde ve geçişlerde ayrıca. |
| Devir hatası | d/d RMSE | Model ile ölçülen motor devri arasındaki fark. |
| Kalibre edilmiş ayar değerleri | – | Ölçüme en çok uyan ayarlar ve her ayarın sonuca etkisi. |
| Yakıt korelasyonu | % | Bütün test koşuları tam çözücüyle: CAN ve akış ölçere göre yakıt farkı. |

## 5. Kabin içi termal yönetim

Kabinin ne kadar ısıtma ya da soğutma istediği ve bunun kaç kW elektrik ya da yakıt ettiği.

Nerede: **Çalışma masası → Kabin** · Kod: `cabin.py · hvac.py` · Denklemler: hesap haritası, `#kabin` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| Gövdenin ısı geçirgenliği | W/K · W/m²K | Her yüzeyin katmanlarından (malzeme, kalınlık) ve iç/dış ısı taşınımından. |
| Güneş yükü | W/m² · W | Konum, gün ve saatten açık gökyüzü ışınımı; yüzeyin yönüne göre; camdan geçen kısım. |
| Kabin havası ve yüzey sıcaklıkları | °C | Katmanlı model: hava, iç yüzeyler ve iç donanım zamanla nasıl soğur ya da ısınır. |
| Isı akışları | kW | Duvarlardan gelen, üfleme havasıyla çekilen ve yolculardan gelen ısı. |
| Referansla karşılaştırma | °C RMSE | CFD ya da test eğrisiyle fark ve kabul ölçütleri (ör. 30 dakikada baş seviyesi 31 °C altı). |
| Koşuda kabin sıcaklığı ve termal güç | °C · kW | Ayar sıcaklığını tutmak için gereken ısıl güç ve COP ile elektrik gücü. |
| Kabin içi termal yönetim enerjisi | kWh | Koşu boyunca kabine harcanan enerji; batarya termal yönetiminden ayrı yazılır. |

## 6. Süspansiyon, yalpa ve devrilme

Gövdenin dört tekerlek üzerinde zıplaması, yunuslaması ve yalpalaması; körükler ve amortisörlerle.

Nerede: **Çalışma masası → Süspansiyon** · Kod: `suspension.py` · Denklemler: hesap haritası, `#suspansiyon` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| Aks ve tekerlek yükleri | kg | Kütle ve ağırlık merkezinden (X, Y, Z); yolcu ve bagaj dahil. |
| Körük basıncı ve yay katsayısı | bar · N/mm | Basınç yükten; yay katsayısı tedarikçi eğrisinden ya da hacimden. |
| Sürüş frekansı ve sönüm oranı | Hz · – | Ön ve arka; körük, lastik ve amortisör eğrisinden. |
| Titreşim modları | Hz | Zıplama, yunuslama, yalpa ve tekerlek zıplaması; frekans, sönüm, sönüm süresi. |
| Dalma ve çökme açısı | ° · °/g | Frende burnun inmesi, kalkışta arkanın çökmesi; kalıcı ve en büyük değer. |
| Sönüm süresi ve salınım frekansı | s · Hz | Manevradan sonra gövdenin durulması. |
| Yalpa açısı | ° · °/g | Virajda gövdenin yatması; körük, devrilme barı ve lastik esnemesiyle. |
| Yük aktarım oranı ve tekerlek yükleri | – · kN | İç tekerleklerden dış tekerleklere geçen yük; 1 olunca iç tekerlekler kalkar. |
| Devrilme eşiği | g | Rijit araç, süspansiyon esnemesiyle ve ani manevrada. |
| Eğme (tipping) açısı | ° | Eğme tablasında iç tekerleklerin kalktığı açı; UN R107 şartı 28°. |
| Devrilme hızı | km/h | Verilen viraj yarıçapında ve şerit değiştirmede iç tekerleklerin kalktığı hız. |
| Körük stroku ve amortisör hızı | mm · m/s | Zaman izi; tampona değip değmediği. |
| Konfor | m/s² | ISO 8608 yol sınıflarında ISO 2631 ağırlıklı düşey ivme; ön, orta, arka. |
| Direksiyon açısı | ° | Bir viraj için gereken ön tekerlek açısı (δ = L/R + K·a_y) ve karakteristik hız. |

## 7. Güzergâh ve görev üretimi

Yolun ve sürüşün tanımından, çözücünün sürdüğü hız izinin üretilmesi.

Nerede: **Kütüphane → Güzergâh · Senaryolar** · Kod: `cycle.py · routes.py · traffic.py · vecto_cycles.py` · Denklemler: hesap haritası, `#gorev` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| Yol profili | m · % | Segmentlerden ya da GPX, KML, CSV dosyasından: mesafe, rakım, eğim, hız sınırı. |
| Hız izi | km/h | Sürücü modeli: aracın gücüne göre hızlanma, hız sınırında seyir, duraklara yavaşlama. |
| Duraklar | s | Bekleme süresi ve kapıların açık kaldığı süre. |
| Yolcu yükü | kişi | Durak bazında binen ve inen; doluluk yol boyunca değişir. |
| Trafik | s | Seviye (yok, orta, yoğun), sinyallerde bekleme, trafik yüzünden kaybedilen süre. |
| Yol yüzeyi | – | Yüzeye göre yuvarlanma direnci çarpanı (asfalt, parke, arnavut kaldırımı). |
| Hazır görevler | – | VECTO otobüs çevrimleri (Coach, Interurban, Suburban, Urban, Heavy Urban), standart çevrimler, sabit hız. |
| Senaryo | – | İklim (sıcaklık, güneş, gece/gündüz), yük ve trafiğin bir arada tanımı. |

## 8. Karşılaştırma ve taramalar

Aynı aracı farklı koşullarda ya da farklı araçları aynı görevde yan yana koyar.

Nerede: **Çalışma masası → Karşılaştır · Koşu** · Kod: `report_compare.py · server.py` · Denklemler: hesap haritası, `#uygulama` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| Çoklu koşu | – | 2–4 koşu yan yana: tüketim, menzil, enerji kalemleri ve mesafeye göre izler. |
| Sıcaklık taraması | kWh/km · km | Tüketim ve menzilin dış sıcaklığa göre değişimi. |
| Trafik karşılaştırması | – | Aynı güzergâh, farklı trafik seviyeleri. |
| Sinyal inceleme | – | Bir koşunun bütün sinyalleri üst üste; sürükleyerek ya da zaman aralığı yazarak yakınlaştırma. |

## 9. Hat ve sefer (filo)

Bir hattın bütün günü: saat dilimleri, sefer sıklığı, yolcu talebi.

Nerede: **Çalışma masası → Hat ve sefer** · Kod: `network.py` · Denklemler: hesap haritası, `#uygulama` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| Sefer ve tur süresi | dk | Her saat diliminde, trafik ve durak beklemeleriyle. |
| Gereken araç sayısı | araç | Tur süresi ve sefer sıklığından; saat dilimine göre. |
| Günlük sefer, araç-km, araç-saat | – | Hattın günlük işi. |
| Yük profili ve doluluk | kişi · % | Durak bazında araçtaki yolcu; en yüklü kesit. |
| Binemeyen yolcu ve ortalama bekleme | kişi · dk | Kapasite yetmediğinde durakta kalanlar. |
| Günlük enerji ya da yakıt | kWh · l | Hat toplamı, araç başına ve yolcu başına. |
| Gün içi şarj ihtiyacı | adet | Araç başına günlük enerji kullanılabilir kapasiteyi aşıyorsa. |
| Trafikte kaybedilen süre | dk/gün | Serbest akışa göre. |

## 10. Test verisi ve korelasyon

Ölçülmüş bir sürüşü modelle yeniden sürer ve farkı gösterir.

Nerede: **Çalışma masası → Test verisi** · Kod: `validate.py · testlog.py · vecto.py · maraton.py` · Denklemler: hesap haritası, `#test` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| Enerji ya da yakıt hatası | % | Aynı hız izinde model ile ölçüm; yakıtta CAN ve akış ölçer ayrı. |
| Aşama bazında hata | kWh · l | Rölanti, hızlanma, seyir, yavaşlama için ayrı ayrı. |
| SOC ve güç izi uyumu | puan · kW | SOC izinin RMSE'si, güç korelasyonu. |
| Vites ve seyir devri uyumu | % · d/d | Dizel loglarda. |
| VECTO ile yol yükü karşılaştırması | % | Aynı çevrimde yuvarlanma, hava, eğim ve atalet enerjileri. |
| Maraton regresyon modeli | l/100 km · km | Filo verisinden uydurulmuş yakıt ve balata ömrü tahmini; eğitim aralığı dışı uyarılır. |

## 11. Serbest sürüş

Aracı klavyeyle kendiniz sürersiniz; canlı değerler yaklaşık, sürüş bitince tam model hesaplar.

Nerede: **Çalışma masası → Serbest sürüş** · Kod: `drivephys.js` · Denklemler: hesap haritası, `#surus` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| Canlı hız, ivme, güç, SOC ya da depo | – | Aracın kendi parametreleriyle basit kuvvet dengesi. |
| Direksiyon ve yanal ivme | ° · g | Viraj denklemi; sürücü 0,3 g'yi, yol tutuşunu ve devrilme eşiğini aşamaz. |
| Gövde yatması ve dalması | ° | Süspansiyon dosyasındaki yalpa ve yunuslama gradyanlarından. |
| Sürüşün gerçek enerjisi | kWh · l | Kaydedilen hız, eğim ve yüzey izi tam modelle yeniden sürülür. |

## 12. Veri ve dijital ikiz

Araç, bileşen dosyalarından kurulur; elle sayı girilmez.

Nerede: **Kütüphane → Veri yükle · Araç kur** · Kod: `vecto_import.py · importers.py · schema.py` · Denklemler: hesap haritası, `#girdi` bölümü

| Hesaplanan | Birim | Nasıl |
|---|---|---|
| VECTO beyan dosyalarından araç | – | Motor yakıt haritası ve tam yük eğrisi, şanzıman ve aks kayıp haritaları, lastik, kütle. |
| Harita ve eğri içe aktarma | – | Verim, kayıp ve yakıt haritaları; tork ve gerilim eğrileri. |
| Test logu içe aktarma | – | CAN kayıtları: hız, devir, vites, yakıt; sütunlar otomatik tanınır. |
| Karakteristik motor devirleri | d/d | Tam yük eğrisinden: anma devri, azami torkun ve gücün belirli paylarına denk gelen devirler. |
