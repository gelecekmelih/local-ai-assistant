# Ollama Context Kırpılması — Kök-Neden Düzeltmesi

## Sorun (kanıt)
Ollama server logunda (`%LOCALAPPDATA%\Ollama\server.log`):
```
truncating input prompt   limit=2050   prompt=6226
truncating input prompt   limit=2050   prompt=6624
slot release: ... n_tokens = 2066, truncated = 1
```
Agent döngüsünde `messages` **birikimli** büyüyor (her araç sonucu eklenir) +
54 araç tanımı ayrı `tools` param ile gider → prompt 6200+ token'a çıkıyordu ama
`num_ctx=4096` yüzünden **~2050'ye KIRPILIYORDU**. Model geçmişin çoğunu görmüyor
→ tekrar-döngüleri, bağlam kaybı. Trace bunu doğruladı: `context_tokens` sabit 2050,
`prompt_char_len` büyüyor.

## Düzeltme (kod — `brain/sovereign_brain.py`)
- `_LLM_NUM_CTX = 8192` (4096'dan) — tek kaynak (`_call_llm_step` + `_followup_llm`).
- `_LLM_NUM_PREDICT = 768` — ana döngü `num_predict` (sesli cevap kısa; 768 bol yeter).
- **Yeni giriş limiti = 8192 − 768 = 7424** → gözlenen max ~6736'yı karşılar (kırpma yok).
- Trace: istek düzeyi `input_limit_hit: bool` — `context_tokens ≥ (num_ctx − num_predict)`
  olursa true + logger'a WARNING. Kırpılma bir daha SESSİZ yaşanmaz.

## KV Cache Quantization (VRAM — MANUEL ADIM GEREKİR)
num_ctx 8192'ye çıkınca KV cache VRAM'i artar. `q8_0` quantization bunu **~yarıya**
indirir (kalite kaybı ihmal edilebilir).

**Ortam değişkeni Ollama SERVER sürecine uygulanmalı** (Klargus'a değil — Ollama
ayrı çalışır):

- `start_klargus.bat` içinde `set OLLAMA_KV_CACHE_TYPE=q8_0` eklendi; bu **yalnız
  Klargus'un başlattığı süreçleri** etkiler.
- Ollama masaüstü uygulaması/servisi olarak AYRI çalışıyorsa, kalıcı kullanıcı
  ortam değişkeni tanımlayıp Ollama'yı yeniden başlat:
  ```
  setx OLLAMA_KV_CACHE_TYPE q8_0
  ```
  Sonra Ollama'yı tamamen kapatıp yeniden aç. Doğrulama: `ollama ps` veya server.log'da
  cache tipi.

## Doğrulama
Düzeltme sonrası trace'te `context_tokens`'in ~6700'e kadar BÜYÜMESİ beklenir
(2050'de sabit kalmamalı). Hâlâ 2050'de sabitse: model Modelfile'ında `PARAMETER
num_ctx` override'ı olabilir — `ollama show <model> --modelfile` ile kontrol et.
`input_limit_hit=true` + WARNING logu kırpılmayı anında gösterir.
