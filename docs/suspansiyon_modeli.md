# Süspansiyon modeli: ne hesaplar, nereden gelir, nasıl doğrulandı

Çalışma masası → **Süspansiyon** sekmesinin arkasındaki model (`temsasim/suspension.py`).
19 Kasım 2026.

## 1. Model

Araç yedi serbestlik derecelidir: gövde zıplar, yunuslar ve yalpalar; dört tekerlek
istasyonu lastiği üzerinde düşey hareket eder. Gövde ile aks arasında:

| Eleman | Nasıl modellenir |
|---|---|
| Hava körüğü | Basınç yükten gelir (seviye valfleri sürüş yüksekliğini sabit tutar). Yay katsayısı tedarikçi verisinden: doğal frekans eğrisi, yay katsayısı grafiği ya da hacim (politropik, n = 1,38). Zaman simülasyonunda körük politropik bir hava sütunudur; statik noktadaki eğimi tedarikçi değeridir. |
| Amortisör | Ölçülmüş kuvvet–hız tablosu; çekme ve basma ayrı. Noktalar arası doğrusal. Frekans ve sönüm oranı için eğri, bir strok hızında aynı enerjiyi harcayan tek katsayıya indirilir. |
| Devrilme barı | Gövde ile aks arasındaki yalpa açısına bağlı burulma yayı. |
| Tampon | Körük strokunun sonunda sert yay (basma ve açılma). |
| Lastik | Doğrusal düşey yay; çekemez (yük sıfıra inince tekerlek kalkar). |

Boyuna ivme yük aktarımı olarak girer; dalma/çökme önleme geometrisi bu aktarımın yaylar
yerine kollardan geçen payıdır. Yanal ivme, gövdeyi yalpa ekseni etrafında döndüren
moment ve yalpa merkezlerinden aksa doğrudan geçen yük aktarımı olarak girer. Yol,
lastiklerin altından girer; arka aks aynı yolu bir dingil mesafesi sonra görür.

**İşaretler:** x ileri, y sola, z yukarı. Yunuslama + = burun aşağı (dalma). Yalpa + =
sağ yan aşağı (sola dönüşteki yatış). Strok + = basma. Ağırlık merkezi: x ön akstan
geriye, y orta eksenden sağa, z yerden.

## 2. Çıktılar

- **Sürüş frekansı ve sönüm oranı** (ön, arka), tekerlek zıplama frekansı, zıplama ve
  yunuslama frekansları ile salınım merkezleri, yalpa frekansı.
- **Dalma ve çökme açısı**: °/g ve seçilen manevrada en büyük ve kalıcı değer.
- **Sönüm süresi ve salınım frekansı**: manevra bittikten sonra sapmanın %5'ine inme
  süresi ve sıfır geçişlerinden okunan frekans.
- **Yalpa açısı**: °/g ve manevradaki değer; yük aktarım oranı (1 = iç tekerlekler kalktı).
- **Devrilme eşiği**: rijit araç, süspansiyon ve lastik esnemesiyle, ani manevrada;
  eğme tablası açısı (UN R107: 28°); viraj yarıçapına göre devrilme hızı.
- **Konfor**: ISO 8608 yol sınıfında, ISO 2631-1 Wk ağırlıklı düşey ivme (ön aks üstü,
  ağırlık merkezi, arka aks üstü).

Manevralar: fren, kalkış, bırakma testi (zıplama, yunuslama, yalpa), kasis, ani
direksiyon, şerit değiştirme, bozuk yol. Grafikte sürükleyerek ya da **Aralık**
kutularına zaman yazarak yalnız bir kesit incelenir.

"Devrilme" bu modelde **iki iç tekerleğin de yük taşımadığı an**dır. O andan sonrasını
model tarif etmez (büyük açılar, yan kayma, lastik yan kuvvetleri yok); eşik bu yüzden
güvenli tarafta kalır.

## 3. Denklemlerin kaynağı

| Hesap | Kaynak |
|---|---|
| Sürüş katsayısı, doğal frekans | Gillespie, *Fundamentals of Vehicle Dynamics*, Denk. 5-8, 5-9 |
| Zıplama/yunuslama frekansları ve merkezleri | Gillespie Denk. 5-21 … 5-36 |
| İki yayın yalpa direngenliği | Gillespie Denk. 6-26 |
| Yalpa gradyanı | Gillespie Denk. 6-32 |
| Viraj denklemi δ = L/R + K·a_y | Gillespie Denk. 6-16 |
| Yunuslama açısı, çökme önleme | Gillespie Denk. 7-14 |
| Devrilme eşiği, rijit ve süspansiyonlu | Gillespie Denk. 9-4, 9-6 |
| Yol pürüzlülük sınıfları | ISO 8608:2016, Tablo C.2 |
| Konfor ağırlıklandırması Wk | ISO 2631-1:1997, Ek A |
| Yedi serbestlik dereceli araç | Jazar, *Vehicle Dynamics: Theory and Application* (tam araç titreşim modeli) |

## 4. Doğrulama

### 4.1 Kitapların çözümlü örnekleri

Aynı fonksiyonlar kitaptaki sayılarla çalıştırıldı (`tests/test_suspension.py`):

| Örnek | Kitap | Model |
|---|---|---|
| Gillespie böl. 5: sürüş katsayısı ön / arka | 127 / 92,3 lb/in | 127,8 / 92,3 |
| Aynı örnek: doğal frekans ön / arka | 1,14 / 1,11 Hz | 1,142 / 1,112 |
| Gillespie böl. 5, problem 1: α, β, γ | 50,18 / −59,67 / 45,34 | 50,18 / −59,67 / 45,34 |
| Aynı problem: frekanslar | 7,10 ve 6,71 rad/s | 7,10 ve 6,71 |
| Aynı problem: yunuslama merkezi | AM'nin 11,57 in önü | 11,7 in |
| Gillespie böl. 6, problem 2: arka yalpa direngenliği | 92 000 in-lb/rad (1606 in-lb/°) | 92 000 (1606) |
| Aynı problem: yalpa gradyanı | 10,5 °/g | 10,47 |
| Gillespie böl. 9: süspansiyonlu eşik çarpanı (h_r/h = 0,5; 0,1 rad/g) | ≈ 0,95 | 0,952 |
| ISO 2631-1 Wk, 1 – 31,5 Hz arası 9 değer | tablo | ±0,001 |

Zıplama merkezi kitapta −259 in, modelde −234 in çıkar: kitap ω²'yi iki haneye
yuvarlayıp küçük bir farka böler; frekanslar aynıdır.

### 4.2 Modelin kendi içinde

| Kontrol | Sonuç |
|---|---|
| Yedi serbestlik dereceli statik çözüm, dalma °/g ↔ Denk. 7-14 | aynı (10⁻⁶) |
| Lastikler rijit alınınca yalpa °/g ↔ Denk. 6-32 | %0,1 içinde |
| Lastik ve bar rijit alınınca devrilme eşiği ↔ iz / 2h | %2 içinde |
| Lastiklerden geçen yük aktarımı = toplam devirme momenti | aynı (10⁻⁶) |
| Fren simülasyonunda kalıcı dalma ↔ statik çözüm | %6 içinde (körük ilerleyen karakterli) |
| Gövde modları ↔ Denk. 5-35 / 5-36 | %6 içinde (yaysız kütle etkisi) |
| Yol sınıfı bir kademe kötüleşince ağırlıklı ivme | ≈ 2 kat |
| Ağırlık merkezi yükselince bütün eşikler düşer; yana kaçınca o yöne dönüş kötüleşir | evet |
| Ani manevra eşiği < sabit dönüş eşiği < rijit; ivme yavaş kurulunca ikisi birleşir | evet |

### 4.3 Henüz yapılmayan

Ölçümle karşılaştırma yok. Modelin doğru olduğunu gösterecek testler: eğme tablası
(R107) açısı, sabit daire testinde yalpa açısı (ISO 4138), fren testinde yunuslama
açısı, kasis ya da bırakma testinde gövde ivmesi. Bu ölçümlerden biri geldiğinde
`kabul` yazan değerler (devrilme barı, yalpa merkezleri, ağırlık merkezi yüksekliği)
ona göre oturtulur.

## 5. Veri

Süspansiyon dosyaları `library/suspension/*.yaml` altındadır. Bir dosya, `vehicles`
listesinde adı geçen araca aittir; aracı olmayan dosya jenerik sayılır ve dosyası
olmayan araçlara ödünç verilir.

- `otobus_13m_jenerik.yaml`: yer tutucu. Körükler etkin alan ve hacimden, amortisörler
  tasarım kuralından (sönüm oranı 0,3; çekme/basma 3:1) üretilmiştir.
- Gerçek araç dosyaları (ör. Maraton) tedarikçi çizimlerinden kurulur. Çizimler gizlilik
  damgalı olduğu için bu dosyalar `.gitignore`'dadır ve açık depoya girmez.

Dosyada `measured` listesinde yazan alanlar çizim ya da test verisidir; **geri kalan her
şey kabuldür**. Bugün kabul olanlar: dingil mesafesi ve iz genişlikleri, ağırlık merkezi
(boş araç, yolcu, bagaj), yalpa merkezi yükseklikleri, dalma/çökme önleme payları, körük
ve amortisörlerin yerleşimi ve hareket oranları, devrilme barı direngenlikleri, lastik
düşey direngenliği, yaysız kütleler, atalet yarıçapları, alttan dönme gradyanı.

Sonucu en çok etkileyen kabuller, sırayla: ağırlık merkezi yüksekliği (devrilme),
devrilme barı direngenliği ve körükler arası mesafe (yalpa), yalpa merkezi
yükseklikleri.

## 6. Basitleştirmeler

- Küçük açılar; devrilme anından sonrası modellenmez.
- Sabit aks ve bağımsız süspansiyon aynı biçimde, bir yalpa merkeziyle yazılır.
- İkiz lastik, çiftin ortasında tek yay sayılır (devrilme için güvenli tarafta).
- Körükte etkin alanın strokla değişimi yoktur; seviye valfi manevra süresince hava
  alıp vermez.
- Lastik yan kuvveti, sapma açısı ve savrulma dinamiği yoktur: yanal ivme girdi olarak
  verilir. Serbest sürüş bu ivmeyi viraj denkleminden (δ = L/R + K·a_y) üretir ve
  sürücünün 0,3 g'den, yol tutuşundan ve devrilme eşiğinden fazlasını istemesine izin
  vermez.
- Koşu raporundaki yunuslama sinyali kalıcı durum değeridir (ivme × °/g); geçiş
  davranışı Süspansiyon sekmesindedir.
