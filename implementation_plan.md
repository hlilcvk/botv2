# Confluence Detection Engine (CDE) - Architecture Plan

Bu proje, trader'ların göremediği erken aşama sinyallerini (pre-move) yakalayabilen, birbirleriyle olay odaklı (event-driven) haberleşen **bir merkezi motor ve 6 veri toplayıcı bot'tan** oluşan bir mikroservis sistemidir.

## Mimari & Teknoloji Yığını (Tech Stack)

- **Dil:** Node.js (JavaScript/ESM) — Hızlı I/O ve asenkron veri akışı için en ideali.
- **Haberleşme (Message Broker):** **Redis Pub/Sub** — Botlar arası milisaniyelik, gecikmesiz haberleşme için.
- **Dizin Yapısı:** Tüm yapı `c:\Users\HLL\Desktop\antigravity claude\cde` altında modüler olarak kurgulanacaktır.

## Bileşenler (Components)

Aşağıdaki botların her biri ayrı bir süreç (process) olarak kendi klasöründe çalışacak ve sadece tek bir göreve (layer) odaklanacaktır.

### 1. Core Engine (Merkezi Motor) - `cde/engine/`
- **Görevi:** Redis'ten gelen 6 farklı kanalın mesajlarını dinlemek (Subscribe).
- **Mantığı:** Her token (örn: SOLUSDT) için son 15 dakikayı kapsayan akıllı bir önbellek (rolling window) tutar. 
- Bir tokenin toplam `score` değeri **7/15** puan barajını aşarsa, sisteme **PRE_MOVE_SIGNAL** ateşler (ileride UI bu sinyali alacak).

### 2. Detection Workers (6 Ayrı Bot) - `cde/bots/`
Bu botların her biri bağımsız olarak piyasayı analiz eder ve anormallik bulduğunda merkeze standart bir JSON fırlatır (Publish).

| Klasör | İsim | Maks. Puan | Açıklama / Görev |
|--------|------|------------|------------------|
| `bot-lead-lag` | Lead-Lag Detector | 3 | BTC hareketine henüz tepki vermemiş tokenleri tespit eder. |
| `bot-funding-oi` | Funding/OI Div. | 3 | Fiyat yatayken artan OI ve negatif Funding (Squeeze) bulur. |
| `bot-correlation` | Correlation Break | 2 | Tarihsel korelasyonların aniden kırıldığı anları yakalar. |
| `bot-volume` | Volume Anomaly | 3 | Fiyat oynamadan belirli bir mumda oluşan olağandışı hacmi ölçer. |
| `bot-flow` | Cross-Asset Flow | 2 | Sektörler arası (Örn: AI → Gaming) hacim kaymalarını izler. |
| `bot-session` | Time Pattern | 2 | Asya birikimi, Londra açılışı gibi seanssal hacim döngülerini ölçer. |
| **Toplam Skor:** | | **15** | (7 ve üzeri puan alan tokenler sinyal üretir) |

#### Örnek İletişim Paketi (JSON)
Botlardan herhangi biri (örn. bot-funding-oi) anormallik bulduğunda şöyle bir mesaj atar:
```json
{
  "symbol": "SOLUSDT",
  "layer": "funding_oi",
  "score": 3,
  "timestamp": 1710493021000,
  "details": "Fiyat yatay, Funding -0.02, OI artışı +12% (Short Squeeze ihtimali)"
}
```

## Geliştirme Adımları (Execution Plan)

1. **Altyapı:** `cde` klasörüne gidilip ortak bir `package.json` oluşturulacak ve Redis paketi (`ioredis`) kurulacak.
2. **Core Engine Başlatma:** `engine/core.js` yazılarak Redis dinleme ve 7/15 skorlama mantığı inşa edilecek.
3. **Bot İskeletleri:** 6 botun temel çalışma döngüsü (loop) ve Redis publish fonksiyonları yazılacak.
4. **Mock Testi (Simülasyon):** Sistem henüz gerçek borsa API'sine bağlanmadan önce, botlar rastgele "fake" anormallik sinyalleri üretecek şekilde test edilecek. Engine'in bu sinyalleri yakalayıp 7 puanı geçince "ALARM" üretip üretmediği doğrulanacak.

## Verification
- Botlar ayrı terminallerde (process) çalıştırıldığında birbirlerini beklemeden asenkron çalışabilmeli.
- Engine terminalinde, farklı botlardan aynı anda gelen SOLUSDT sinyallerinin skorlarının toplanıp `[PRE-MOVE DETECTED] SOLUSDT (Skor: 8/15)` logunu vermesi test edilecektir.

> [!IMPORTANT]
> Kullanıcı Değerlendirmesi: Mikroservis mimarisi (Modüler Node.js + Redis Pub/Sub) ve 7/15 skorlama mantığı yukarıdaki yapı planına tam uygun mu? Onayınızın ardından `cde` klasörünü yaratıp kodlamaya başlayacağım.
