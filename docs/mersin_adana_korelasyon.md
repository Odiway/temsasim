# Mersin–Adana otoyol testi ile dizel modelin karşılaştırması

Kaynak: AR-GE Test Merkezi, TRM-150-006-03, "OA20173 Maraton Karma Yakıt
Tüketim Testi (Mersin)", 17–19 Eylül 2026. Veriler `library/testlog/mersin_adana_2026_09/`.
Karşılaştırma `python -m temsasim correlate --campaign mersin_adana_2026_09`
ile ya da arayüzde *Doğrulama → Yakıt testi* ile yeniden üretilir.

## Test nasıl yapıldı

- Araç OA20173 Maraton, DAF MX-11 330, ZF TraXon 12 (otomatik), aks A133 2,00,
  Bridgestone 295/80 R22.5. Yüklü: ön 7000 + arka 11000 = **18 000 kg**.
- Parkur: Adana Batı gişeleri sonrası Avadan köprüsü → Deliçay viyadüğü,
  **44,3 km** sabit; dönüş aynı parkur ters yönde.
- Cruise control **100 km/h** (VBox GPS 98 km/h; ekonomi modunda 12. vites,
  ~1010 rpm). Yenice ve Akgedik tesislerinde 5'er dk rölanti; Öztürkler'de
  2,5 dk bekleme + kick-down'sız tam gaz 50 km/h + fren, üç kez.
- **Bütün alıcılar ve camlar kapalı**, yalnızca kısa farlar. Gündüz ve gece
  ayrı ayrı, üçer tekrar; her koşuda Akgedik'te hava ölçümü (sıcaklık, rüzgâr,
  nem, basınç).
- Ölçüm: gösterge l/100 km, Dewesoft CAN kaydı (1 Hz), harici yakıt cihazı, VBox.

## Veri hakkında iki gerçek

1. **Yakıt cihazı CAN'dan %10 yüksek okur.** Gündüz 22,9 (cihaz) / 20,7 (CAN),
   gece 24,0 / 21,8 l/100 km. CAN yakıt debisi ECU'nun enjektör süresinden
   tahminidir; cihaz fiziksel ölçümdür. Hangi değerin "gerçek" sayılacağı test
   ekibiyle netleşmeli; model iki değerle de raporlanır.
2. **Loglarda GPS/rakım yok.** Eğim profili OpenStreetMap yol geometrisi +
   SRTM 30 m yükseklik modelinden üretildi (`route/mersin_adana_otoyol_gidis.csv`,
   700 m yumuşatma). Viyadük ve yarmalarda hata olur. VBox dosyalarında
   GPS yüksekliği vardır; alınırsa profil onunla değiştirilmelidir.

## Modelde yapılan düzeltmeler (bu testten)

| Bulgu | Değişiklik |
|---|---|
| Rölantide ECU 600 rpm (sertifika 550) | motor `idle_speed_rpm: 600` |
| Rölanti yakıtı 1,1–1,2 l/h ≈ haritanın sıfır yük değeri → yardımcı yük duruşta ~0,6 kW | yardımcı tüketiciler küçültüldü (ECU 300 W, farlar 100/350 W, direksiyon 1 kW, kompresör %2 görev, fan 200 W) |
| Testte klima kapalı | `Environment.hvac_enabled` eklendi; arayüzde "Klima kapalı" kutusu, CLI `--hvac-off` |
| 12. vites 1010 rpm @ 98 km/h GPS | dinamik lastik yarıçapı 0,515 m (VECTO tablosu 0,507) |
| Gidiş/dönüş yakıt farkı rakımla açıklanamıyor | rüzgâr koşu koşu uygulanıyor; "D>B" gidişte karşı rüzgâr kabul edildi (**teyit gerekli**) |
| CAN hızı sabit parkura göre %1 fazla | hız izi 44,3 km'ye ölçeklenir |

## Sonuç (model, 9 koşu)

Yakıt cihazına göre her koşu **−6 … +1 %**, toplam **−2,9 %**; CAN'a göre
+0 … +12 %, toplam +6,9 %. Gidiş ve dönüş arasındaki ölçülen asimetri (cihaz
%7) modelde %8 olarak çıkıyor. Seyir devri modelde +10…+40 rpm; vites uyumu %84.

| Koşu | CAN l | Cihaz l | Model l | vs CAN | vs cihaz |
|---|---|---|---|---|---|
| Gündüz gidiş 1 | 9,889 | 10,546 | 9,900 | +0,1 % | −6,1 % |
| Gündüz gidiş 2 | 9,622 | 10,501 | 10,436 | +8,5 % | −0,6 % |
| Gündüz gidiş 3 | 9,294 | 10,533 | 10,250 | +10,3 % | −2,7 % |
| Gündüz dönüş 1 | 8,824 | 9,773 | 9,587 | +8,6 % | −1,9 % |
| Gündüz dönüş 2 | 8,697 | 9,693 | 9,758 | +12,2 % | +0,7 % |
| Gündüz dönüş 3 | 9,042 | 9,989 | 9,764 | +8,0 % | −2,3 % |
| Gece gidiş 1 | 9,888 | 10,940 | 10,359 | +4,8 % | −5,3 % |
| Gece gidiş 2 | 9,793 | 10,710 | 10,312 | +5,3 % | −3,7 % |
| Gece gidiş 3 | 9,790 | 10,720 | 10,329 | +5,5 % | −3,6 % |

Hiçbir "uydurma çarpanı" yok: yakıt haritası sertifikadan olduğu gibi,
CdxA CFD'den, lastik RRC sertifikadan, kütle tartıdan.

## Açık noktalar, öncelik sırasıyla

1. **Rüzgâr yönü notasyonu** ("D>B"): doğudan batıya esen mi, batıdan doğuya mı?
   Sonucu koşu başına ±1,5 l/h, yön asimetrisini tamamen değiştiriyor.
2. **Hangi yakıt ölçümü referans**: cihaz mı CAN mı? %10 fark modelin
   "doğru/yanlış" yargısını belirliyor.
3. **VBox verisi** (GPS hız ve yükseklik): gerçek eğim profili ve gerçek hız.
4. Gece koşularında model cihaza göre %4–5 düşük, gündüz dönüşte sıfıra yakın:
   gece hava yoğunluğu ve far yükü var; kalan farkın kaynağı (yakıt sıcaklığı?
   cihaz kalibrasyonu?) test ekibiyle konuşulmalı.
5. Vites stratejisi: %84 uyum; hızlanma yakıtı ±5 % içinde. VECTO EffShift'in
   yakıt tabanlı erken vites mantığı eklenirse yükselir.
6. Öztürkler'deki tam gaz kalkışlar (kick-down'sız) için pedal kaydı var; sürücü
   modeli kalibre edilebilir.

## 18 Kasım 2026: vites mantığı VECTO EffShift'e geçti

Eski devir penceresi kuralları aracı yokuşta 11. vitese indiriyor, gerçek TraXon ise
12. viteste ~1000 d/d'de kalıyordu. Şanzıman artık VECTO'nun AMT EffShift mantığıyla
(`temsasim/shift.py`) vites değiştiriyor; yedi ayar değeri bu dokuz koşunun vites ve devir
izine oturtuldu (`python -m temsasim shift mersin_adana_2026_09 --calibrate`, arayüzde
*Çalışma masası → Vites*).

| | Devir penceresi (eski) | EffShift, VECTO değerleri | EffShift, kalibre |
|---|---|---|---|
| Vites uyumu (yalnız vites planı, 9 koşu) | – | %79,5 | **%89,5** |
| ±1 vites içinde | – | %96,1 | **%98,4** |
| Devir hatası (RMSE) | – | 143 d/d | **93 d/d** |
| Kalkış vitesi (45 kalkış) | – | %100 | %100 |
| Tam çözücüde vites uyumu | %76,2 | %77,4 | **%87,2** |
| Yakıt, CAN'a göre | +7,4 % | +5,0 % | +5,9 % |
| Yakıt, cihaza göre | −2,4 % | −4,6 % | −3,9 % |

Kalibre edilen ayarlar: büyütme sonrası asgari devir 870 d/d; verim büyütmesinde tork
rezervi 0,25; mevcut vites lehine pay 0,975; iki vites arası 1 s; frenlemede 950 d/d
altında küçültme (0,4 m/s² üstü yavaşlamada); yavaşlama eşiği 0,05 m/s².

Okuma: vites doğru seçilince model yakıtı %1,5 daha az buluyor (11. vites yerine 12.).
CAN'a göre fark küçüldü, cihaza göre büyüdü; yani önceki "cihaza −%2,4" sonucunun bir
kısmı yanlış vitesin fazladan yaktığı yakıttı. Kalan fark yol yükü (eğim, rüzgâr, CdxA)
ve yardımcı yük kabullerindedir, vites seçiminde değil. Seyir devri dokuz koşunun
yedisinde ±10 d/d içinde (önce +14 … +80 d/d).
