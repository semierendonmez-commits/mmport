# mmport

VCV Rack eklentilerini 4ms MetaModule için `.mmplugin` dosyasına dönüştürür.

Git URL'si, bir `.zip` arşivi ya da yerel bir klasör verirsin; kaynağı tarar,
MetaModule'de çalışamayacak modülleri gerekçesiyle birlikte eler, SVG'leri
PNG'ye çevirir, gereken proje dosyalarını üretir ve derler.

**Üstkaynağa hiçbir şey yazmaz.** Port projesi ayrı bir klasöre çıkar ve
orijinal kaynağı dışarıdan referanslar.

---

## Kurulum

Ön koşul yalnızca Python 3 (macOS'ta `xcode-select --install` ile gelir) ve
bir SVG dönüştürücü:

```bash
brew install librsvg      # önerilen — küçük ve hızlı
brew install cmake ninja  # ninja yoksa Makefile'a düşer, sadece yavaşlar
```

Homebrew `/opt/homebrew/bin` ve `/usr/local/bin` yollarını uygulama kendisi
PATH'e ekliyor, ayrıca bir şey yapman gerekmiyor.

ARM toolchain'i (`arm-none-eabi-gcc` 12.3, SDK'nın istediği sürüm) ve
MetaModule SDK'sını araç kendisi `~/.mmport` altına indirir. Sistemin geri
kalanına dokunmaz; silmek istersen o klasörü silmen yeterli.

## Kullanım

**Uygulama:** `mmport.app`'i Uygulamalar klasörüne sürükle, çift tıkla.
Arayüz tarayıcıda açılır (sunucu yalnızca `127.0.0.1`'de dinler).

İmzalı olmadığı için macOS ilk açılışta engelleyecek. İki yol var:

```bash
xattr -dr com.apple.quarantine /Applications/mmport.app   # karantinayı kaldır
```

ya da Finder'da sağ tık › **Aç** › **Aç**.

Uygulamadan çıkınca (Cmd-Q) sunucu da kapanır.

**Komut satırı:**

```bash
python3 -m mmport https://github.com/gosub/forsitan-modulare
python3 -m mmport ~/Downloads/SomePlugin.zip -o ~/ports/someplugin
python3 -m mmport <kaynak> --analyze-only   # sadece rapor, dosya yazmaz
python3 -m mmport <kaynak> --no-build       # projeyi üret, derleme
python3 -m mmport <kaynak> --loose          # riskli modülleri de dahil et
python3 -m mmport --serve                   # web arayüzü
```

İlk çalıştırma toolchain indirmesi yüzünden birkaç dakika sürer; sonrakiler
sadece derleme süresi kadar.

---

## Ne yapar, ne yapmaz

**Yapar:** kaynağı getirir, taşınabilirlik analizi yapar, elenen modülleri
hem kaynak listesinden hem `addModel()` çağrılarından düşürür, SVG'leri
240 px PNG'ye çevirir, kullanılmayan asset'leri atar, `CMakeLists.txt` +
`plugin-mm.json` + eklenti girişi + uyumluluk başlığı üretir, derler,
derleyici hatalarını okunabilir önerilere eşler.

**Yapmaz:** kodu tamir etmez. `std::thread` kullanan bir modülü
`AsyncThread`'e taşımak ya da gerçek zamanlı tahsisi ön-tahsise çevirmek
hâlâ elle iştir. Araç bunların tam olarak hangi dosyanın hangi satırında
olduğunu söyler, o kadar.

Analiz **sezgiseldir** — regex ve basit çağrı grafiği, gerçek bir derleyici
ön yüzü değil. Yanlış pozitif ve yanlış negatif mümkündür. Elemeye
katılmıyorsan `--loose` ile zorlayıp derlemenin ne dediğine bakabilirsin.

---

## Eleme kuralları

Bunlar MetaModule'ün gerçek kısıtlarından geliyor: eklentiler
`arm-none-eabi-gcc` ile, işletim sistemi olmadan, Cortex-A7 üzerinde çalışıyor.

| Bulgu | Sonuç | Neden |
|---|---|---|
| `network` | eler | ağ yığını yok |
| `host-gui` | eler | çalışma anında yamaya modül eklenip çıkarılamaz |
| `file-dialog` | eler | yerel dosya seçici yok (SDK'nın file-browser API'si var) |
| `stream` | eler | libstdc++ stream desteği derlenmemiş |
| `exception` | eler | `-fno-exceptions` |
| `thread` / `sync` | eler | pthread yok |
| `rt-alloc` (korumasız) | eler | ses iş parçacığında heap yasak |
| `rt-alloc` (sr korumalı) | geçer | SDK "ilk `process()`'te bir kez kur" kalıbını tolere eder |
| `expander` | geçer, uyarır | modül "expander takılı değil" gibi davranır, çökmez |
| `minblep` | geçer, yamalar | eksik özelleşme compat başlığına üretilir |
| `host-read` | geçer, bilgi | `APP->scene` okuması yalnızca çizimi etkiler |

`rt-alloc` ayrımı önemli: araç `process()`'ten başlayan çağrı grafiğini
çıkarır ve her kenarın örnekleme hızı karşılaştırması içeren bir `if`
içinde olup olmadığına bakar. Yalnızca korumalı yollardan ulaşılan
tahsisler bilgi, en az bir korumasız yol varsa uyarı olur.

## Doğrulama

| Eklenti | Sonuç |
|---|---|
| forsitan modulare | 25 modülün 18'i portlandı, derlendi, tüm semboller çözüldü |
| VCV Fundamental | 39 modülün 39'u geçti |
| Bogaudio | 120 modülün 117'si geçti |

Bogaudio'da elenen üçü — Analyzer, AnalyzerXL, Ranalyzer — 4ms'in kendi
resmî portunda elle hariç bıraktığı modüllerin **tam olarak aynısı**
(hepsi `analyzer_base` üzerinden `std::condition_variable`'a bağlı).

forsitan'da araç, elle incelemede gözden kaçan iki şey buldu: SDK'da eksik
olan `MinBlepGenerator<16,16,float>` özelleşmesi (compat başlığına kendisi
üretti) ve **tabes**'in polifonik kayıtta ses iş parçacığında kanal başına
~5.8 MB ayırması.

## Lisans ve izinler

Port edilen kodun lisansı üstkaynağa aittir; `plugin.json`'daki `license`
alanına bak. GPL gibi bir lisans portu serbest bırakır, ama 4ms yine de
dağıtmadan önce özgün yazarla iletişime geçilmesini öneriyor:
[licensing_permissions.md](https://github.com/4ms/metamodule-plugin-sdk/blob/main/docs/licensing_permissions.md)

## Yapı

```
mmport/
  analyze.py    kaynak tarayıcı, model→dosya eşlemesi, çağrı grafiği
  assets.py     SVG→PNG, referans tabanlı temizlik
  generate.py   CMakeLists / plugin-mm.json / init() / compat başlığı
  toolchain.py  arm-none-eabi-gcc + SDK temini
  build.py      cmake çalıştırma, hata → öneri eşlemesi
  pipeline.py   uçtan uca akış
  cli.py        komut satırı
  web.py        yerel arayüz (yalnızca stdlib)
```
