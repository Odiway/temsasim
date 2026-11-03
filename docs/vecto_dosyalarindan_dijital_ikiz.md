# VECTO dosyalarından dijital ikiz

Homologasyon ekibinin elindeki VECTO beyan dosyaları (XML), bir aracı anlatan en
güvenilir veridir: her bileşen sertifikalıdır, imzalıdır ve üreticiden geldiği
gibi durur. temsasim bu dosyaları doğrudan okur. Kimse sayı yazmaz.

## Hangi dosyalar

| Klasör / dosya | VECTO öğesi | Ne verir | Zorunlu mu |
|---|---|---|---|
| MOTOR/*.xml | `Engine` | yakıt haritası (g/h), tam yük ve sürükleme eğrisi, rölanti, anma devri ve gücü, WHTC katsayıları | evet |
| ŞANZIMAN/*.xml | `Gearbox` | vites oranları, her vites için kayıp haritası, azami giriş torku | evet |
| AKS/*.xml | `Axlegear` | aks oranı, kayıp haritası | evet |
| LASTİK/*.xml | `Tyre` | ebat, RRC, ISO test yükü | evet |
| STEP INPUT/*.xml | tamamlanmış araç | kütle, azami kütle, koltuk, boyutlar, CdxA, klima donanımı, yardımcı ısıtıcı gücü | kütle ve koltuk için |
| PRIMARY/*.xml | birincil araç | aks düzeni, retarder tipi ve oranı, yardımcı teknolojileri; bileşenlerin bir kopyası | aks düzeni ve retarder için |
| COMPLETE VEHICLE/*.vecto | iş dosyası | yalnızca diğer dosyalara işaret eder | hayır |

Sertifikalı bileşen dosyası varsa, birincil araç dosyasının içindeki kopyaya
tercih edilir. Maraton örneğinde `BENI OKU.txt` birincil ve tamamlanmış
dosyaların örnek mimari olduğunu söyler; ikiz bu yüzden bileşenleri ayrı
dosyalardan, araç seviyesi değerleri (kütle, CdxA) örnek dosyalardan alır ve
bunu şasi dosyasının açıklamasına yazar.

## Nasıl yüklenir

**Arayüzden:** Veri yükle → *VECTO dosyaları (dizel araç)*. Klasörü sürükleyin
ya da "Klasör seç" ile gösterin. İkinci adımda her dosyanın ne olarak tanındığı
ve aracın neyle kurulacağı görünür; her satırda değerin hangi dosyadan geldiği
yazar. Üçüncü adımda ada ve varsa CAD/CFD'den cam alanına girip kaydedersiniz.

**Komut satırından:**

```
python -m temsasim vecto-import "ARAÇ VERİLERİ/MARATON ÖRNEK ARAÇ" --name "Maraton 13 dizel" --glazing 28.58
python -m temsasim vecto-import klasor --name "X" --plan-only     # yalnızca ne kullanılacağını göster
```

Maraton için hazır betik: `python tools/build_maraton_twin.py` (cam alanları
ANSA CFD raporundan içinde yazılıdır).

## Ne yazılır

```
library/engine/<üretici_model>.yaml            motor; maps/<...>_yakit.csv, maps/<...>_tam_yuk.csv
library/transmission/<şanzıman_aks>.yaml       vites oranları, retarder; maps/<...>_sanziman_kayip.csv, maps/<...>_aks_kayip.csv
library/chassis/<model>_vecto.yaml             kütle, CdxA, aks tablosu (pay, RRC, FzISO, çift lastik, tekerlek ataleti), lastik yarıçapı
library/hvac/<model>_kabin.yaml                yardımcı ısıtıcı, cam/çift cam, kabin geometrisi (KABUL değerleriyle)
library/auxiliary/<model>_yardimci.yaml        yardımcı tüketiciler (KABUL değerleriyle)
library/vehicle/<ad>.yaml                      powertrain: diesel, referanslar, koltuk, depo
```

Her dosyanın `description` alanında kaynak dosya adı ve sertifika numarası
bulunur. `validated: true` yalnızca sertifikadan gelen bileşenlerde (motor,
şanzıman, aks) işaretlenir; şasi, kabin ve yardımcılar kabul içerdiği için
`false` kalır ve rapor bunu söyler.

## VECTO'dan alınan, dosyada olmayan değerler

- **Aks yük payları:** VECTO birincil otobüs segment tablosu (2 aks: %37,5 / %62,5;
  3 aks: 27,3 / 45,4 / 27,3; körüklü: 24,3 / 35,2 / 40,5; 4 aks: 21,4 / 21,4 / 35,8 / 21,4).
- **Lastik dinamik yarıçapı ve tekerlek ataleti:** VECTO `Wheels.csv`
  (295/80 R22.5: çap 1044 mm, çevre çarpanı 3,05 → r_dyn 0,507 m; 15,5 kg·m² / tekerlek).
- **Motor ataleti:** VECTO jenerik formülü, 5 l üstü motorlar: 1,3 (debriyaj) + 0,41 + 270 · V[m³].
- **Yakıt özellikleri:** VECTO `FuelTypes.csv`: dizel 836 kg/m³, 42,7 MJ/kg, 3,13 kg CO₂ / kg yakıt.
- **Vites penceresi:** vites küçültme devri = max(1,45 · rölanti, 0,5 · anma),
  vites büyütme = 0,95 · anma, tork rezervi %10. Ölçülmüş devir iziyle ayarlanmalıdır.

## Dizel çözücü nasıl hesaplar

Tekerlekten motora geri: tekerlek torku → aks (kayıp haritası, giriş tarafı) →
retarder boşta sürüklemesi → vites seçimi (AMT stratejisi) → şanzıman kayıp
haritası → motor. Yardımcı tüketiciler ve klima kompresörü krank miline mekanik
yük olarak eklenir (24 V yükleri alternatör verimi 0,70 ile). Motor torku tam
yük eğrisiyle kırpılır (açık kalan talep "karşılanamadı" olarak kaydedilir),
sürükleme eğrisinin altındaki frenleme sürtünmeli frenlere gider. Yakıt
sertifikalı haritadan okunur; motor freninde sıfırdır. Duruşta motor rölantide
yakıt yakar. Kabin ısıtması önce motor soğutma suyundan (yakıt enerjisinin
%28'i kabul), yetmezse yakıtlı ısıtıcıdan gelir ve ayrı kalem olarak yazılır.

Enerji dengesi yakıtın alt ısıl değerinden başlar: motor kayıpları, ısıtıcı
yakıtı, yuvarlanma, hava, eğim, atalet, sürtünmeli fren, motor freni, aks,
şanzıman, retarder, debriyaj, yardımcılar, fan, klima. Artık sıfıra yakın
olmalıdır; rapor bunu gösterir.

## Bilinen sınırlar

- Vites değiştirme sırasındaki çekiş kesintisi ve tork konvertörü (AT) yok;
  AT şanzıman AMT gibi hesaplanır.
- VECTO'nun hıza bağlı yan rüzgâr düzeltmesi (CdxA(v)) uygulanmaz; CdxA sabittir.
- Yakıt haritası WHTC düzeltmesi uygulanmaz (`correction_factor` 1,0; VECTO
  mühendislik modu). Beyan koşusunu taklit etmek için katsayı dosyadan
  değiştirilebilir.
- Dizel araç için log doğrulaması (yakıt ölçümüyle) henüz yok.
