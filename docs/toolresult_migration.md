# Araç Katmanı ToolResult Migrasyonu

**Durum:** ✅ TAMAMLANDI (F5 kapandı) · **Son güncelleme:** 2026-07-04

Bu iş, "Görev 8"in kalan parçası olarak değil, kendi adıyla tanımlı bir migrasyondur.
Kapsam 3 kez küçük tahmin edildikten sonra (delege fonksiyonlar → system_tools →
codebase/agent/wsl/kali + vision) tam envanter çıkarıldı ve iş gerçek boyutuyla
adlandırıldı. **SONUÇ: projede sıfır string-tabanlı hata tespiti; normalizer'da
sıfır string-yolu. Görev 8 kök-neden kapandı.**

## ✅ TAMAMLANMA ÖZETİ (F5)

| Metrik | Değer |
|---|---|
| Taşınan modül | ~20 (basic/web/system/wsl/kali/codebase/agent/vision + delege + dispatcher) |
| Taşınan fonksiyon | ~140 (araç + delege + helper) |
| Karakterizasyon testi | 84 (başlangıç ~19'dan) |
| Silinen legacy | `_LEGACY_ERROR_SIGNALS` (20 kelime) + `_legacy_looks_like_error` + string-dalı (+ sovereign_brain `_ERROR_SIGNALS`/`_is_error_result` Tur A) |
| Kalan string-tabanlı hata tespiti | **0** (grep = 0) |
| Normalizer | tip-tabanlı (ToolResult passthrough / dict map / None→ok / beklenmedik→açık hata) |

**Normalizer son hali (`brain/tool_registry._normalize_to_result`):**
```python
if isinstance(raw, ToolResult):
    return raw                                   # .ok korunur
if isinstance(raw, dict):
    err = raw.get("error")
    ok = (not err) and bool(raw.get("success", True))
    return ToolResult(message=str(raw), ok=ok, data=raw, error=(str(err) if err else None))
if raw is None:
    return ToolResult(message="", ok=True)
# Beklenmedik çıplak string/tip → taşınmamış araç: açık hata (heuristik YOK)
return ToolResult(message=str(raw), ok=False, error="taşınmamış araç: yapılandırılmamış çıktı")
```

---

## Amaç

Projedeki tüm araç çıktılarını `ToolResult` sözleşmesine taşımak; böylece
`brain/tool_registry._normalize_to_result`'daki **string-tabanlı hata tespiti
(string-yolu)** tamamen silinebilsin. Bitince: sıfır string-tabanlı hata tespiti,
normalizer'da sıfır string-yolu.

## Sözleşme

`ToolResult(message: str, ok: bool, data=None, error: str|None)` — `str` alt-sınıfı
(değer = message → mevcut string tüketicileri korunur; yeni kod `.ok` kullanır).

**ok politikası (davranış-koruyan):**
- Başarı → `ok=True` (+ `data`)
- Doğrulama / soft-fail (boş arg, "... yok.") → `ok=True` (eski legacy-sınıflandırma;
  bu tur DEĞİŞTİRİLMEZ — ayrı "semantik doğruluk turu"na ait)
- Sinyal içeren hata ("bulunamadı", "izin reddedildi" …) → `ok=False` + `error`
- Exception handler → `ok=False`
- Sistem-durumu değiştiren fonksiyonlar: "başarı VARSAYMA" — gerçek sonuçtan türet
  (denenip-başarısız → `ok=False`)

**message alanları BİREBİR korunur** (kullanıcı/ses deneyimi değişmez).

## Güvenli-mod guard (yapısal)

`core/safe_mode.is_safe_mode()` (KLARGUS_TEST_MODE=1) — sistem-durumu değiştiren
fonksiyonların KENDİ girişinde (validation SONRASI). Fonksiyon nereden çağrılırsa
çağrılsın (dispatcher VEYA doğrudan) test modunda gerçek sistem değişikliği yapmaz.

## Normalizer choke-point

Tek yer: `brain/tool_registry._normalize_to_result`.
- `ToolResult` → aynen geçir
- `dict` → yapılandırılmış ok (error/success alanı) → dict-yolu
- **`str`/`None` → GEÇİCİ string-yolu (bu migrasyonun F5'inde SİLİNİR)**

String-yolu **YALNIZCA F5'te**, aşağıdaki 13 modülün tamamı taşındıktan ve
`grep` ile tools/ genelinde sıfır çıplak string doğrulandıktan sonra silinir.
Silindiğinde: beklenmedik çıplak string → `ToolResult(ok=False, error="taşınmamış
araç: yapılandırılmamış çıktı")` (sessiz ok=True YOK).

---

## Tam Envanter (string-yoluna bağlı modüller)

| Modül | araç/fn | str-return | hata-yollu | faz | durum |
|---|---|---|---|---|---|
| basic_tools | 22 | 0 (29 ToolResult) | — | P0 | ✅ bitti |
| web_tools | 1 | 0 (8 ToolResult) | — | P0 | ✅ bitti |
| cyber_tools | 18 | 0 (94 dict) | — | P0 | ✅ bitti (dict-yolu) |
| **system_tools** | 44 | 0 (129 ToolResult+8 dict) | — | P1 | ✅ bitti (Tur C) |
| **run_command zinciri** (commands+program_index) | — | 0 | — | P2 | ✅ bitti (Tur C) |
| wsl_tools.run_kali/run_kali_bg (çekirdek) | 2 | 8 | 5 | F1 | ✅ bitti |
| kali_tools | 14 | 12 | 0 | F1 | ✅ bitti |
| ~~cyber_tools (kaçak)~~ | — | — | — | F1 | ✅ yanlış pozitif (dict döner) |
| wsl_tools (kendi araçları) | 6 | 22 | 9 | F2 | ✅ bitti |
| brain/tool_registry (10 wrapper + dispatcher) | 10 | 14 | 7 | F2 | ✅ bitti |
| codebase_tools | 5 | 12 | 6 | F3 | ✅ bitti |
| agent_tools | 5 | 13 | 5 | F3 | ✅ bitti (vision F4-wrap) |
| vision/window_actions | 8 | 21 | 10 | F4a | ✅ bitti |
| vision/input_actions | 7 | 18 | 7 | F4a | ✅ bitti |
| vision/screen_actions_v2 (araç-yüzü 3; find_text_elements→list) | 3 | 6 | 2 | F4b | ✅ bitti |
| vision/screen_understanding (understand/quick; get_active_app→dict) | 2 | — | — | F4c | ✅ bitti |
| vision/screen_interpreter (interpret/assist; get_screen_context→dict) | 2 | — | — | F4c | ✅ bitti |
| vision/find_and_click (find/double; find_text_on_screen→dict) | 2 | 5 | 3 | F4c | ✅ bitti |
| vision/screen_reader | — | — | — | — | ölü kod / dict (dokunulmadı) |
| vision_tools/input_tools/window_tools (wrapper'lar) | 20 | passthrough | — | F4 | ✅ bitti |
| wsl_tools._wsl_path/_windows_path (path utility) | — | — | — | F5 | ✅ araç kaydından çıkarıldı (`_` öneki) |

**Kalan: YOK.** Migrasyon tamamlandı.
(F1 ✅ kali; F2 ✅ wsl+registry+dispatcher; F3 ✅ codebase+agent; F4 ✅ vision;
F5 ✅ straggler + string-yolu SİLİNDİ.)

---

## Fazlar

### P0 — Sözleşme + öncü modüller · ✅ BİTTİ (Tur B + Tur C mic guard)
ToolResult sözleşmesi, normalizer choke-point, basic_tools, web_tools, cyber_tools
(dict), güvenli-mod guard (core/safe_mode), delege moduller (device_manager,
system_control, task_recipe_store).

### P1 — system_tools · ✅ BİTTİ (Tur C, commit 6be40d8/feb5502/d8789f5)
44 araç, 129 ToolResult return. 2a salt-okuma, 2b/2c mutator+guard, 2d ağ, 2e
süreç/dosya/başlangıç/terminal. WSL fonksiyonları dict döner (dict-yolu).

### P2 — run_command zinciri · ✅ BİTTİ (Tur C, commit 3315600)
control/commands.py (run_command + _learn_from_text + _run_learned) +
control/program_index.open_program. Karar-mantığı bağı: tool_open_program artık
`.ok`'a bakıyor (string-eşleştirme yok). None yolu korundu.

### F1 — Kali yürütme çekirdeği + kali_tools · ✅ BİTTİ
**Bağımlılık düzeltmesi:** kali_tools yalnız ince wrapper; asıl çıktı
`wsl_tools.run_kali` / `run_kali_bg` (str dönerdi). kali_tools'u string-yolundan
çıkarmak bu çekirdeği gerektirdi → F1 kapsamı:
- `tools/wsl_tools.py`: `run_kali`, `run_kali_bg` (2 çekirdek delege) → ToolResult ✅
- `tools/kali_tools.py`: 14 tool wrapper (validation ToolResult + run_kali passthrough) ✅
- cyber_tools "kaçak": **yanlış pozitif** — `return ''` nested `_str` helper içinde;
  whois_lookup DICT döner (dict-path). Migration gerekmedi.

wsl_tools'un KENDİ kayıtlı araçları (tool_kali_status, kali_run, kali_install …)
F2'ye kaldı (run_kali çekirdeği hazır → passthrough olacaklar).
Karakterizasyon: 7 yeni test (run_kali/kali_tools validation + güvenlik-blok).
Suite 45 PASS / 0 FAIL; karakterizasyon 47/47; mypy temiz.

### F2 — wsl_tools kendi araçları + tool_registry wrapper'ları · ✅ BİTTİ
**Adım 0:** iki grup BAĞIMSIZ (çapraz bağımlılık yok). wsl_tools → run_kali (F1);
tool_registry wrapper'ları → system_tools dict-fn + brain.* str-modülleri
(wrapper seviyesinde sarıldı, derin migration gerekmedi — hepsi başarı-veya-except).
- 2a: wsl_tools 6 kayıtlı araç → ToolResult
- 2b: tool_registry 10 wrapper (chat, memory_answer, self_improve, self_analyze,
  generate_tool, self_repair, plan_and_execute, wsl_run, wsl_check_status,
  wsl_tool_check) → ToolResult. Derin çıktı → operasyon-başarısı ok=True,
  except → ok=False, gerçek-sonuç dallanmaları (wsl_run/generate_tool) → dict/result'tan.

**DİSPATCHER DÜZELTMESİ (kök):** `_run_tool_impl` `_kw` yolu `return str(result)`
yapıyordu → taşınmış araçların ToolResult'ını str'e çevirip .ok'u YOK EDİYORDU.
`return result` (passthrough) yapıldı; "Tool bulunamadı" + "Hata: {e}" dispatcher
dönüşleri de ToolResult(ok=False). Artık _kw yolu da gerçek .ok korur.

Karakterizasyon +9 (47→56). Suite 45 PASS / 0 FAIL; mypy temiz.

### F3 — codebase_tools + agent_tools · ✅ BİTTİ
**Adım 0:** iki grup BAĞIMSIZ (cross-call yok). codebase_tools self-contained
(iç mutual-recursion). agent_tools 3/5 fonksiyon **vision (F4)** delege ediyor.
- 2a: codebase_tools 5 araç → ToolResult (self-contained)
- 2b: agent_tools 5 araç → ToolResult. vision delegasyonları (understand_screen,
  quick_screen_check, read_screen_text_v2) **wrapper seviyesinde sarıldı** (ok=True).

**⚠️ F4 REVİZYON NOTU:** take_screenshot / check_screen_for şu an vision çıktısını
`str(...)` ile sarıyor (`# F4 NOTU` yorumları kodda). F4'te vision ToolResult'a
taşınınca bunlar **passthrough**'a çevrilecek (str() yerine, iç .ok korunur).
verify_goal `run_tool("active_window")` (F2 sonrası ToolResult) → str() çıkarımı ✓.

check_screen_for VAR/YOK karar-mantığı yolu: ok=True (ikisi de geçerli sonuç);
mesaj BİREBİR (mock ile kilitlendi) → taşıma sonrası aynı davranır.
Karakterizasyon +14 (56→70). Suite 45 PASS / 0 FAIL; mypy temiz.

### F4 — vision katmanı · ✅ BİTTİ (en derin)
**Adım 0 harita:** yaprak (window/input_actions, screen_actions_v2) → orta
(screen_reader/interpreter/understanding, find_and_click) → wrapper. **KRİTİK:**
yalnız STRING araç-yüzü fn taşındı; VERİ fn (find_text_elements_v2→list,
get_ui_snapshot/get_screen_context/find_text_on_screen/get_active_app_info→dict)
DOKUNULMADI (iç veri; taşımak çağıranı bozar). İç string çağrıları str-alt-sınıfı ile güvenli.
- F4a: window_actions (8) + input_actions (7) — pyautogui/pygetwindow MOCK
- F4b: screen_actions_v2 araç-yüzü (3) — OCR/click MOCK
- F4c: screen_understanding (2) + screen_interpreter (2) + find_and_click (2) +
  vision_tools wrapper + **F4 NOTU kapanışı** (agent_tools str()→passthrough)
- screen_reader.read_screen_text: ölü kod (çağıran yok) → dokunulmadı.

**Karakterizasyon +14 (70→84):** hepsi MOCK — gerçek kamera/mouse/pencere YOK
(geçmiş kamera hatası tekrarlanmadı). grep "# F4 NOTU" = 0. Suite 45-46 PASS / 0 FAIL.

**F5 pre-check:** kalan yalnız 3 straggler — hiçbiri vision/gerçek-araç değil:
whois_lookup (FP, dict), wsl_tools.wsl_path/windows_path (path utility, auto-register).
F5'te ele alınacak (kayıttan çıkar / fallback).

### F5 — String-yolunu SİL + doğrula · ✅ BİTTİ
- **Adım 0:** stragglerlar — `wsl_path`/`windows_path` → `_` öneki ile araç
  kaydından çıkarıldı (iç yardımcı, KLARGUS_TOOLS'ta yok); whois_lookup FP=dict.
- **Adım 1:** geriye dönük `.ok` koruma KANITLANDI — dispatcher `_kw` + tek-arg
  yolları, heuristikle çelişen dummy araçlarda `.ok`'u korudu (str() kalıntısı yok).
- **Adım 2:** `_LEGACY_ERROR_SIGNALS` + `_legacy_looks_like_error` + string-dalı
  TAMAMEN SİLİNDİ. Normalizer tip-tabanlı; beklenmedik string → açık ok=False hata.
- **Adım 3:** grep `_ERROR_SIGNALS`/`_legacy_looks_like_error` = 0; taşınan
  dosyalar mypy 0; tools/ çıplak-string yalnız helper'larda (araç değil);
  suite 46 PASS / 0 FAIL; karakterizasyon 84/84.

---

## Faz çalışma kuralı
Her faz kendi turu. Sıra: **karakterizasyon testleri ÖNCE → taşıma (alt-grup grup,
her biri ayrı commit) → her adımdan sonra tam suite + tüm karakterizasyon.**
Kırılırsa DUR, geri al, raporla. message BİREBİR. Faz sonunda bu dosyadaki durumu
güncelle.
