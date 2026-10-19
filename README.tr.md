<p align="center"><a href="README.md">English</a> · <b>Türkçe</b></p>

<h1 align="center">Utterlane Live</h1>

<p align="center"><b>Asistanına kimin konuştuğunu bilen kulaklar.</b></p>

<p align="center">
Bilgisayarında çalışan, eller serbest bir sesli asistan ön yüzü. Odayı dinler, <i>kimin</i> <i>ne</i>
dediğini bilir ve sesli cevap verir: OpenAI, kendi <b>OpenClaw</b> ya da <b>Hermes Agent</b> ajanın, yerel bir
model ya da OpenAI uyumlu her şeyle. Konuşmayı yazıya dökme, konuşmacıyı tanıma ve ses üretimi tamamen senin
bilgisayarında çalışır.
</p>

<p align="center">
  <a href="https://github.com/tuncertimur/utterlane-live/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/tuncertimur/utterlane-live/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="Lisans: Apache-2.0" src="https://img.shields.io/badge/lisans-Apache--2.0-blue"></a>
  <img alt="Windows · macOS · Linux" src="https://img.shields.io/badge/%C3%A7al%C4%B1%C5%9F%C4%B1r-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-555">
  <img alt="Türkçe · İngilizce" src="https://img.shields.io/badge/diller-T%C3%BCrk%C3%A7e%20%C2%B7%20%C4%B0ngilizce-8c7cf6">
</p>

https://github.com/user-attachments/assets/18b00ef2-3b49-475d-bc4f-f764c7a55221

<p align="center">
  İki kişi, tek mikrofon, gerçek oturum ·
  <a href="https://tuncertimur.github.io/utterlane-live/?lang=tr">Web sitesi</a> ·
  <a href="https://github.com/tuncertimur/utterlane-live/releases/tag/v0.1.0">1 dakikalık sürüm</a>
</p>

<p align="center">
  <a href="#hızlı-başlangıç">Hızlı başlangıç</a> ·
  <a href="docs/INTEGRATIONS.md">Ajanını bağla</a> ·
  <a href="#nasıl-çalışır">Nasıl çalışır</a> ·
  <a href="#gizlilik-ve-güvenlik">Gizlilik</a> ·
  <a href="https://tuncertimur.github.io/utterlane-live/?lang=tr">Web sitesi</a>
</p>

---

Sesli asistanlar tek kullanıcı varsayar; gerçek odalarda birden çok kişi konuşur. Utterlane Live
konuşanları takip eder: kendini tanıtanı adıyla, tanıtmayanı "3. kişi" olarak. Böylece ajanın kimin
sorduğunu, kimin cevap verdiğini ve ne zaman susması gerektiğini bilir.

## Özellikler

- **Eller serbest konuşma**: konuşma algılama, yerel Whisper ile yazıya dökme, akışlı yerel ses
  üretimi; konuşmaya başlayınca asistanın sözünü kesebilirsin.
- **Konuşmadan isim**: "Ben Tuncer" o sesi isimlendirir. "Ahmet sen konuş" denince sıradaki yeni
  ses *Ahmet?* olur, birkaç turdan sonra kesinleşir. "Ben Ahmet değilim, Mehmet'im" geriye dönük
  düzeltir. Kendini hiç tanıtmayan sesler *3. kişi*, *4. kişi* olur.
- **Önce onay, sonra hafıza**: *"Bir dahaki sefere de seni sesinden tanımamı ister misin?"* diye
  sorar. Ses izi yalnızca sorudan hemen sonra gelen net bir *evet* ile saklanır; anahtarı işletim
  sisteminin anahtar deposunda duran AES-256-GCM ile şifrelenir. İstediğin an silinir.
- **Grup adabı**: tek kişiyle her şeye cevap verir. Grupta yalnızca adı söylenince ("Lena, …")
  konuşur; sözü başkasına verdiğinde araya girmez.
- **Beyni sen seç**: OpenAI, OpenClaw, Hermes Agent, Ollama, LM Studio, OpenAI uyumlu her sunucu
  ya da kendi webhook'un. İstersen hiçbiri (yalnızca dinleme).
- **Yerleşik MCP sunucusu**: her MCP istemcisi (OpenClaw, Hermes, Claude, Cursor, Open WebUI…)
  odayı okuyabilir (`recent_turns`, `wait_for_next_turn`, `list_speakers`) ve konuşabilir
  (`speak`).
- **Ajanına yalnızca tanıtılmış kişiler komut verir**: ayarlanabilir (`all`, `named`,
  `enrolled`); araç kullanan ajanlarda varsayılan olarak açık.
- **Sıradan bilgisayarlarda çalışır**: Windows 10/11 (x64), Apple Silicon'lu macOS 14+ ve Linux
  (x86_64). NVIDIA ekran kartı hızlandırır ama şart değil. Türkçe ve İngilizce.

## Hızlı başlangıç

**Windows**: ZIP'i indir (ya da `git clone`), sonra **`Utterlane.cmd`** dosyasına çift tıkla.
İlk açılış her şeyi o klasöre kurar ve uygulamayı açar.

```powershell
git clone https://github.com/tuncertimur/utterlane-live
cd utterlane-live
.\Utterlane.cmd
```

**macOS / Linux**

```bash
git clone https://github.com/tuncertimur/utterlane-live
cd utterlane-live
./utterlane.sh
```

Kurulum, klasörün içine kendine ait bir [uv](https://github.com/astral-sh/uv) ve Python 3.12
koyar (sisteme hiçbir şey kurulmaz), CUDA ya da CPU sürümünü kendisi seçer ve gereken modelleri
hash ile doğrulayarak indirir: GPU'suz bilgisayarda yaklaşık 1 GB, NVIDIA GPU ile yaklaşık 2 GB
(bkz. [docs/MODELS.md](docs/MODELS.md)). Sonra `http://127.0.0.1:8765` adresini aç, bir kez
**Başlat**'a bas (tarayıcılar ses için bir tıklama ister) ve konuş.

Beyin vermek için `.env.example` dosyasını `.env` olarak kopyala ve bir blok seç: örneğin
`OPENAI_API_KEY=…`, ya da tamamen ücretsiz ve yerel bir kurulum için `UTTERLANE_BRAIN=ollama` ile
`UTTERLANE_BRAIN_MODEL=qwen3:8b`. Beyin olmadan da konuşmayı yazıya döker ve konuşmacıları
etiketler. `.env` git dışında tutulur; anahtarlar istersen veri klasöründeki `config.env`
dosyasında da durabilir (`doctor` komutu yerini gösterir), böylece zipleyip paylaşabileceğin bir
klasörde durmazlar.

Seçenekler başlatıcıdan sonra yazılır (Windows'ta `.\Utterlane.cmd …`, diğerlerinde `./utterlane.sh …`):

```bash
./utterlane.sh --language en      # İngilizce (varsayılan: Türkçe)
./utterlane.sh --voice male_1     # ses seç (docs/MODELS.md)
./utterlane.sh doctor             # kurulumu kontrol et
```

## Ajanını bağla

| Ajanın | Beyin olarak (`.env`) | Odayı araç olarak ver (MCP) |
|---|---|---|
| **OpenClaw** | `UTTERLANE_BRAIN=openclaw` + gateway token'ı | `openclaw mcp add utterlane --url http://127.0.0.1:8765/mcp --transport streamable-http` |
| **Hermes Agent** | `UTTERLANE_BRAIN=hermes` + API sunucu anahtarı | `mcp_servers: {utterlane: {url: "http://127.0.0.1:8765/mcp"}}` |
| **Claude Code** | — | `claude mcp add --transport http utterlane http://127.0.0.1:8765/mcp` |
| **Ollama / LM Studio** | `UTTERLANE_BRAIN=ollama` / `lmstudio` | — |
| **Diğerleri** | `UTTERLANE_BRAIN=chat` / `responses` / `webhook` | her MCP istemcisi ya da REST API |

Bir [Agent Skill](skills/utterlane-live/SKILL.md) ajanlara odayı nasıl kullanacaklarını öğretir:
`npx skills add tuncertimur/utterlane-live -g -a openclaw hermes-agent claude-code codex`.
Ayrıntılı kurulum: **[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)** (İngilizce).

## Nasıl çalışır

```
 tarayıcı mikrofonu ──► 16 kHz PCM ──► konuşma algılama (Silero) ──► bir tur
                                                  ├─► Whisper (yerel GPU/CPU) ─────────► metin
                                                  └─► ses izi (TitaNet-L + ResNet34, yerel)
                                                        └─► kim? tanıdık / yeni "N. kişi" / belirsiz
 "Ben Tuncer", "Ahmet sen konuş", "Ben Ahmet değilim" ──► isimler + onay ──► şifreli profiller
 [Konuşan: Tuncer] metin ──► beyin (OpenAI / OpenClaw / Hermes / yerel / webhook) ──► cevap
 cevap ──► cümle cümle ──► Pocket TTS (yerel, akışlı) ──► tarayıcı hoparlörü (söz kesmeye duyarlı)
                     MCP / REST: recent_turns · wait_for_next_turn · list_speakers · speak
```

- Tek mikrofon, birden çok kişi: her tur iki ses modeliyle özetlenir ve o ana kadar duyulan
  kişilerle karşılaştırılır; aynı parça içindeki hızlı söz devri konuşmacı değişiminden bölünür.
- Emin değil mi? Tahmin etmez, *Belirsiz* der. Çok kısa turlar ("evet", "hı") işaretli bir tahmin
  olarak önceki konuşmacıya yazılır.
- Ajanın asla ses duymaz; yalnızca konuşmacı etiketli metin alır.

## Performans (ölçülmüş)

İki Türkçe konuşmacının onaylı kayıtları ve açık FLEURS sesleriyle ölçüldü (Windows 11,
i9-10900X, RTX 3080). Senin odan ve mikrofonun farklı sonuç verebilir.

| | Sonuç |
|---|---|
| Gerçek seslerle, gerçek modellerle konuşmacı etiketi | 13/13 tur doğru (iki isimli kişi + iki tanımadık ses) |
| 30 rastgele çok kişilik oturum | %93,6 doğru, %1,7 "belirsiz"; iki isimli kişi hiç karıştırılmadı |
| Türkçe yazıya dökme, Whisper large-v3-turbo (GPU) | Kelime hata oranı 0,11 · tur başına 0,34 sn |
| …aynı model CPU'da (int8, 8 iş parçacığı) | Kelime hata oranı 0,11 · gerçek zaman katsayısı 0,6 |
| …Whisper small, CPU'da | Kelime hata oranı 0,27 · gerçek zaman katsayısı 0,23 |
| Söz bitince ilk sesli cevaba kadar (GPU + gpt-6-luna) | 2,0–2,6 sn |

Model kendiliğinden seçilir: NVIDIA GPU ya da 16+ iş parçacıklı CPU'da `large-v3-turbo`, diğerlerinde
`small` (değiştirmek için `UTTERLANE_STT_MODEL`).

## Gizlilik ve güvenlik

- Ses, yazıya dökülen metin ve ses izleri bilgisayarında kalır. Beyne yalnızca konuşmacı etiketli
  metin gider.
- Ses izleri yalnızca sesli bir *evet*ten sonra saklanır, şifrelenir (AES-256-GCM; anahtar Windows
  Kimlik Bilgisi Yöneticisi / macOS Anahtar Zinciri / Secret Service'te) ve arayüzden ya da
  `./utterlane.sh profiles delete-all` (Windows'ta `.\Utterlane.cmd profiles delete-all`) ile silinir.
- **Ses eşleşmesi bir etikettir, kimlik doğrulama değildir.** Kayıtlar ya da klonlanmış sesler onu
  kandırabilir. Hassas hiçbir şeyin kilidini ona bağlama; araç kullanan ajanları kısıtlı bir
  profilde tut.
- Sunucu yalnızca `127.0.0.1`'de dinler; başka web sitelerinden gelen istekleri reddeder. Ağa
  açmadan önce `UTTERLANE_API_TOKEN` ayarla. Ayrıntılar: [SECURITY.md](SECURITY.md).

## Ayarlar

Hepsi isteğe bağlı; bkz. [`.env.example`](.env.example). Sık kullanılanlar:

| Değişken | Varsayılan | |
|---|---|---|
| `UTTERLANE_LANGUAGE` | `tr` | `tr` ya da `en` |
| `UTTERLANE_BRAIN` | `OPENAI_API_KEY` varsa `openai`, yoksa `none` | `openai`, `openclaw`, `hermes`, `ollama`, `lmstudio`, `chat`, `responses`, `webhook`, `none` |
| `UTTERLANE_BRAIN_SPEAKERS` | `all` (ajanlarda `named`) | kimin konuşması cevap tetikler |
| `UTTERLANE_MODE` | `smart` | `smart`, `always`, `wake` |
| `UTTERLANE_ASSISTANT_NAME` | `Lena` | hitap adı |
| `UTTERLANE_TTS_VOICE` | `female_1` (tr) / `alba` (en) | bkz. [docs/MODELS.md](docs/MODELS.md) |
| `UTTERLANE_STT_MODEL` | `auto` | `large-v3-turbo`, `small` |
| `UTTERLANE_PORT` | `8765` | |

## Sınırlar

- Aynı anda konuşan iki kişi tek mikrofonla ayrılamaz; böyle turlar "belirsiz" işaretlenebilir.
- Birbirine benzeyen tanımadık sesler tek bir "N. kişi"de birleşebilir.
- Eşikler temiz, onaylı kayıtlar ve açık FLEURS sesleriyle ayarlandı; gürültülü bir odada ayar
  gerekebilir (`config.SpeakerThresholds`).
- Şimdilik Türkçe ve İngilizce. Pocket TTS Almanca, Fransızca, İspanyolca, İtalyanca, Portekizce ve
  Felemenkçe de konuşur, ama isim ifadeleri yalnızca Türkçe ve İngilizce için yazıldı.

## Katkı

Hata bildirimlerine ve pull request'lere açığız: [CONTRIBUTING.md](CONTRIBUTING.md). Güvenlik
sorunlarını lütfen gizli bildir ([SECURITY.md](SECURITY.md)).

## Lisans

[Apache-2.0](LICENSE) © 2026 Tuncer Timur. Modeller ayrıca indirilir ve kendi lisanslarını korur
(MIT, CC-BY-4.0): bkz. [NOTICE](NOTICE) ve [docs/MODELS.md](docs/MODELS.md).
