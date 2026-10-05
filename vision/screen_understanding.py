# -*- coding: utf-8 -*-
"""
vision/screen_understanding.py — Birleşik Ekran Anlama Motoru
=============================================================
LLM'in ekranı mükemmel biçimde anlaması için üç katmanlı yaklaşım:

  1. Vision LLM  — screenshot → llava/moondream/minicpm-v → görsel anlama
  2. OCR + UIA   — Tesseract + Windows Erişilebilirlik → hassas metin & öğeler
  3. Synthesis   — her iki kaynak birleştirilip kullanıcı sorusuna odaklı yanıt

Öncelik:
  vision model mevcut → vision + OCR kombine
  vision model yok    → OCR + UIA + text LLM (interpret_screen)

Kullanım:
  from vision.screen_understanding import understand_screen
  result = understand_screen("şu an ne izliyorum?")
"""

import base64
import io
import concurrent.futures
from core.logger import get_logger
import threading
import time
from typing import Tuple, Optional

from brain.tool_result import ToolResult

log = get_logger("screen_understanding")

# ── Arka plan OCR önbelleği ────────────────────────────────────────────────────
# understand_screen her çağrıda 3.4s OCR beklemek yerine önbellekten okur.
# Arka plan thread her 5 saniyede bir OCR'ı yeniler.
_ocr_cache: dict = {"text": "", "ctx": {}, "ts": 0.0}
_ocr_cache_lock = threading.Lock()
_OCR_CACHE_TTL   = 5.0   # saniye — maksimum eski veri yaşı
# Ekran sorgusu kullanılmıyorken arka plan OCR uyur — her tur 1-3s CPU yiyor
# ve Whisper/Vosk/ses yakalama ile yarışıyordu (sürekli %20-40 CPU israfı).
_SCREEN_USE_WINDOW = 120.0   # saniye — son ekran sorgusundan sonra aktif kalma süresi
_last_screen_use: float = 0.0
_bg_refresh_active = False
_bg_refresh_thread = None


def _refresh_ocr_cache():
    """Arka planda OCR önbelleğini güncelle.

    Sadece son _SCREEN_USE_WINDOW saniye içinde bir ekran sorgusu yapıldıysa
    çalışır — aksi halde uyur. Kullanılmayan OCR döngüsü CPU'yu boşa yakıyordu.
    """
    global _bg_refresh_active
    while _bg_refresh_active:
        if (time.monotonic() - _last_screen_use) > _SCREEN_USE_WINDOW:
            time.sleep(2.0)
            continue
        try:
            result = _get_ocr_uia_context()
            text, ctx = result if isinstance(result, tuple) else (str(result), {})
            with _ocr_cache_lock:
                _ocr_cache["text"] = text
                _ocr_cache["ctx"]  = ctx
                _ocr_cache["ts"]   = time.monotonic()
        except Exception:
            pass
        time.sleep(_OCR_CACHE_TTL)


def start_screen_cache():
    """Uygulama açılışında arka plan OCR önbelleğini başlat."""
    global _bg_refresh_active, _bg_refresh_thread
    if _bg_refresh_active:
        return
    _bg_refresh_active = True
    _bg_refresh_thread = threading.Thread(target=_refresh_ocr_cache, daemon=True, name="ocr-cache")
    _bg_refresh_thread.start()


def stop_screen_cache():
    global _bg_refresh_active
    _bg_refresh_active = False


def _get_cached_ocr() -> tuple[str, dict]:
    """Önbellekten OCR al. Eğer bayatsa senkron olarak yenile."""
    global _last_screen_use
    _last_screen_use = time.monotonic()   # arka plan OCR'ı bu pencere için uyandır
    with _ocr_cache_lock:
        age = time.monotonic() - _ocr_cache["ts"]
        if age < _OCR_CACHE_TTL * 2 and _ocr_cache["text"]:
            return _ocr_cache["text"], _ocr_cache["ctx"]
    # Önbellek yok veya çok eski → senkron yenile
    try:
        result = _get_ocr_uia_context()
        text, ctx = result if isinstance(result, tuple) else (str(result), {})
        with _ocr_cache_lock:
            _ocr_cache["text"] = text
            _ocr_cache["ctx"]  = ctx
            _ocr_cache["ts"]   = time.monotonic()
        return text, ctx
    except Exception:
        return _fast_screen_text(), {}


def _ocr_is_meaningful(text: str) -> bool:
    """OCR metni LLM'e "ekranda şu yazıyor" diye verilmeye değer mi?

    Boş veya AÇIKÇA anlamsız (çoğunlukla simge/gürültü) metni eler — ham tüketiciler
    (mission ekran notu vb.) çöp OCR'ı LLM'e verip halüsinasyona yol açmasın.

    MUHAFAZAKAR: sadece boş veya harf/rakam oranı çok düşük olanı reddeder. Kısa
    GERÇEK metin ("Tamam", "Evet", "OK") geçer — şüphede metni geçir (yanlış "okuyamadım"
    da kötü ama emin-halüsinasyondan az kötü)."""
    s = str(text or "").strip()
    if not s:
        return False
    non_space = sum(1 for c in s if not c.isspace())
    if non_space == 0:
        return False
    alnum = sum(1 for c in s if c.isalnum())
    # ≥%40 harf/rakam VE ≥2 alfasayısal: "Tamam"/"Evet"/"OK" geçer,
    # "|| ~~ □ ▯ :: --" gibi simge/gürültü elenir.
    return (alnum / non_space) >= 0.40 and alnum >= 2

# ──────────────────────────────────────────────────────────────────────────────
# Sabitler
# ──────────────────────────────────────────────────────────────────────────────

# ── Vision modeli soğukluk takibi (ekran + kamera ortak — aynı moondream/VRAM) ──
# keep_alive=2m sonrası model VRAM'den düşer; sonraki ilk sorgu ~10s yükleme
# öder. Soğuksa kullanıcıya kısa bir "bakıyorum" çalıp algılanan gecikmeyi gizle.
_VISION_KEEPALIVE = 120.0
_vision_last_use: float = 0.0
_vision_filler_lock = threading.Lock()


def mark_vision_used() -> None:
    global _vision_last_use
    _vision_last_use = time.monotonic()


def vision_probably_cold() -> bool:
    return (time.monotonic() - _vision_last_use) > _VISION_KEEPALIVE


def say_filler_if_cold() -> None:
    """Vision modeli soğuksa kısa bir ön-ses çal (bloklamaz)."""
    if not vision_probably_cold():
        return
    if not _vision_filler_lock.acquire(blocking=False):
        return
    try:
        import threading as _th
        def _say():
            try:
                from audio.text_to_speech import speak
                speak("Bakıyorum, bir saniye.", mode="command")
            except Exception:
                pass
            finally:
                try: _vision_filler_lock.release()
                except Exception: pass
        _th.Thread(target=_say, daemon=True, name="vision-filler").start()
    except Exception:
        try: _vision_filler_lock.release()
        except Exception: pass


_MAX_IMG_SIDE  = 800     # 1280→800: vision modeli daha hızlı işler, kalite yeterli
_JPEG_QUALITY  = 78
_VISION_TIMEOUT = 18     # saniye
_OCR_TIMEOUT    = 10     # saniye

# App tipine göre vision model prompt ipuçları
_APP_VISION_HINTS = {
    "code_editor":  "Bu bir kod editörü. Görünen kodu, dosya adını, satır numaralarını ve varsa hata/uyarıları (kırmızı çizgi, hata mesajı) Türkçe açıkla.",
    "browser":      "Bu bir web tarayıcısı. URL'yi, sayfa başlığını, sayfanın ana içeriğini ve önemli elementleri Türkçe açıkla.",
    "terminal":     "Bu bir terminal ekranı. Son komutları, çıktıları ve varsa hata mesajlarını Türkçe açıkla.",
    "chat":         "Bu bir mesajlaşma uygulaması. Kimin mesajlaştığını, son mesajların içeriğini Türkçe özetle.",
    "document":     "Bu bir metin/belge editörü. Belgenin başlığını ve görünen içeriği Türkçe özetle.",
    "spreadsheet":  "Bu bir tablo uygulaması. Sayfa adını, aktif hücreyi ve önemli veriyi Türkçe belirt.",
    "media_player": "Bu bir medya oynatıcı. Şu an çalan şarkı/içerik adını, sanatçıyı, durumu (oynuyor/duraklatıldı) kesin olarak belirt.",
    "video":        "Bu bir video oynatıcı. İzlenen içeriğin adını ve platformu Türkçe belirt.",
    "file_manager": "Bu bir dosya yöneticisi. Geçerli dizin yolunu ve görünen dosya/klasörleri Türkçe listele.",
    "terminal":     "Bu bir terminal. Hangi komutu çalıştırdığını, çıktıyı ve varsa hataları Türkçe göster.",
    "system":       "Bu bir sistem aracı. Hangi bölümde olduğunu, görünen ayar ve değerleri Türkçe belirt.",
    "design":       "Bu bir tasarım uygulaması. Açık proje/dosya adını ve görünen içeriği Türkçe belirt.",
    "gaming":       "Bu bir oyun veya oyun platformu. Ekranda görülen oyunu ve durumu Türkçe anlat.",
}

_DEFAULT_VISION_HINT = (
    "Ekranda tam olarak ne var? Aktif uygulama, içerik, önemli metin, "
    "butonlar, hata mesajları gibi tüm detayları kısa ve net Türkçe anlat."
)


# ──────────────────────────────────────────────────────────────────────────────
# Screenshot alma + sıkıştırma
# ──────────────────────────────────────────────────────────────────────────────

def _capture_compressed() -> Tuple[Optional[object], str]:
    """
    Ekran görüntüsü al, vision model için sıkıştır.
    Döner: (PIL Image veya None, base64 JPEG string)
    """
    # Yöntem 1: mss (en hızlı, cross-platform)
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        # Yöntem 2: PIL ImageGrab (Windows)
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
        except Exception as e:
            log.warning("Screenshot alınamadı: %s", e)
            return None, ""

    # Boyut küçültme — büyük ekranlar vision modeli yavaşlatır
    w, h = img.size
    if max(w, h) > _MAX_IMG_SIDE:
        ratio = _MAX_IMG_SIDE / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), resample=3)  # LANCZOS

    # JPEG sıkıştırma — PNG'den ~10x küçük
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return img, b64


# ──────────────────────────────────────────────────────────────────────────────
# Vision model sorgusu
# ──────────────────────────────────────────────────────────────────────────────

def _query_vision_llm(b64: str, question: str, timeout: int = _VISION_TIMEOUT) -> str:
    """
    Ollama üzerindeki vision modelini sorgula.
    Boş string döndürürse model mevcut değil veya hata var.
    """
    from brain.system_settings import get_system_setting
    # brain.llm._url → host varsayılanı 127.0.0.1 (localhost IPv6 ~2s gecikmesini
    # önler) ve _session() → paylaşımlı connection pool (keep-alive).
    from brain.llm import _url, _session

    model = get_system_setting("vision_model", "moondream")

    try:
        r = _session().post(
            _url("/api/generate"),
            json={
                "model":      model,
                "prompt":     question,
                "images":     [b64],
                "stream":     False,
                # 10m → 2m: moondream ~2GB VRAM tutuyor; uzun süre bellekte
                # kalması ana LLM'i CPU'ya taşırıyor. Ardışık ekran sorguları
                # 2dk penceresine sığar, sonrasında VRAM ana modele kalır.
                "keep_alive": "2m",
                "options": {
                    "num_predict": 150,
                    "temperature": 0.05,
                    "top_p": 0.9,
                },
            },
            timeout=timeout,
        )
        r.raise_for_status()
        mark_vision_used()   # model artık VRAM'de sıcak
        resp = r.json().get("response", "").strip()
        if not resp:
            return ""
        # "I can't see" / "I don't see" gibi model reddi → boş döndür
        low = resp.lower()
        if any(x in low for x in ["i cannot", "i can't", "unable to", "no image"]):
            return ""
        return resp
    except Exception as e:
        log.debug("Vision model sorgu hatası: %s", e)
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# OCR + UIA bağlamı
# ──────────────────────────────────────────────────────────────────────────────

def _fast_screen_text() -> str:
    """
    find_text_elements_v2 tabanlı hızlı metin çıkarım (~2.5s).
    read_screen_text_v2'nin (~23s) yerine kullanılır.
    """
    try:
        from vision.screen_actions_v2 import find_text_elements_v2
        elements = find_text_elements_v2() or []
        lines: list[str] = []
        prev_y = -999
        line_words: list[str] = []
        for el in sorted(elements, key=lambda e: (e.get("top", 0), e.get("left", 0))):
            word = str(el.get("name", "") or "").strip()
            if not word:
                continue
            y = el.get("top", 0)
            if abs(y - prev_y) > 12 and line_words:
                lines.append(" ".join(line_words))
                line_words = []
            line_words.append(word)
            prev_y = y
        if line_words:
            lines.append(" ".join(line_words))
        return "\n".join(lines)
    except Exception:
        return ""


def _get_ocr_uia_context() -> Tuple[str, dict]:
    """
    OCR + UI Automation bağlamını al.
    Döner: (özet metin, app context dict)
    """
    try:
        from vision.screen_interpreter import get_screen_context, _build_screen_summary
        ctx = get_screen_context()
        summary = _build_screen_summary(ctx, max_content=1800)
        return (summary, ctx)
    except Exception:
        pass

    # Fallback: hızlı OCR (find_text_elements_v2 tabanlı, ~2.5s)
    text = _fast_screen_text()
    return (text[:2000], {}) if text else ("", {})


# ──────────────────────────────────────────────────────────────────────────────
# Vision prompt oluşturma
# ──────────────────────────────────────────────────────────────────────────────

def _build_vision_prompt(question: str, app_type: str = "unknown") -> str:
    """App tipine ve soruya göre vision model promptu oluştur (eski Türkçe yol)."""
    hint = _APP_VISION_HINTS.get(app_type, _DEFAULT_VISION_HINT)

    if question.strip():
        return (
            f"Türkçe yanıtla. {hint}\n\n"
            f"Kullanıcının sorusu: {question}\n"
            f"Ekrana bakarak bu soruyu doğrudan ve net cevapla."
        )
    return f"Türkçe yanıtla. {hint}"


# moondream gibi küçük vision modelleri Türkçe'yi yok sayıp İngilizce'de çok
# daha doğru çalışıyor. Görsel kavrayışı İngilizce iste, sonra ana Türkçe LLM
# OCR metniyle birleştirip Türkçe nihai cevabı üretsin.
_VISION_PROMPT_EN = (
    "Look at this screenshot and describe what is on the screen: the active "
    "application, the type of content (video, image, code, text, chat, game, "
    "settings), what is happening, and any notable visual elements. Be concise "
    "and factual."
)


def _synthesize_answer(question: str, visual_en: str, ocr_text: str) -> str:
    """moondream görsel betimi (İngilizce) + OCR metnini ana Türkçe LLM ile
    birleştirip kullanıcının sorusuna odaklı, doğru Türkçe cevap üret."""
    try:
        from brain.llm import call_ollama
        ocr_block = (ocr_text or "").strip()[:1200]
        vis_block = (visual_en or "").strip()[:600]
        soru = question.strip() or "Ekranda ne var? Kısaca özetle."
        prompt = (
            "Aşağıda bir ekran görüntüsünden iki kaynak var. "
            "GÖRSEL = görüntünün genel betimi (İngilizce, yaklaşık). "
            "METİN = ekrandan OCR ile okunan kesin yazılar (doğru kaynak). "
            "Çelişki olursa METİN'e güven. Bunları kullanarak kullanıcının "
            "sorusunu kısa, net ve doğru TÜRKÇE cevapla. Uydurma.\n\n"
            f"GÖRSEL: {vis_block or 'yok'}\n\n"
            f"METİN: {ocr_block or 'yok'}\n\n"
            f"Soru: {soru}\nTürkçe cevap:"
        )
        ans = call_ollama(prompt, retries=1, timeout=15, num_predict=160)
        if ans and not ans.startswith("LLM hatası"):
            return ans.strip()
    except Exception:
        pass
    return ""


# Görsel kavrayış gerektiren soru mu? (ne izliyorum, görsel, renk, resim, video…)
_VISUAL_QUESTION_HINTS = (
    "izli", "görüyor", "goruyor", "görün", "gorun", "görsel", "gorsel",
    "resim", "video", "renk", "fotoğraf", "fotograf", "ekranda ne",
    "ne var", "neye benz", "nasıl görün", "nasil gorun", "grafik", "çiz",
)


def _needs_vision(question: str) -> bool:
    q = (question or "").lower()
    return any(h in q for h in _VISUAL_QUESTION_HINTS)


# ──────────────────────────────────────────────────────────────────────────────
# Ana API
# ──────────────────────────────────────────────────────────────────────────────

def _vision_is_enabled() -> bool:
    """settings.json'da vision_enabled=true VE vision_model dolu mu?"""
    try:
        from brain.system_settings import get_system_setting
        if not get_system_setting("vision_enabled", True):
            return False
        model = get_system_setting("vision_model", "").strip()
        return bool(model)
    except Exception:
        return True   # bilinmiyorsa dene


def understand_screen(question: str = "") -> ToolResult:
    """
    İki mod:
    - Soru varsa   → OCR (~3.5s) + kısa LLM (~2s) = ~5s
    - Genel bakış  → OCR + vision paralel = max(3.5, 7s) = ~7s
    """
    question = str(question or "").strip()
    use_vision = _vision_is_enabled()

    # ── HIZLI YOL: saf metin sorusu — görsel gerekmez (OCR + LLM, ~5s) ──────
    # "ekranda ne yazıyor", "şifre ne", "hata mesajı ne" gibi metin soruları
    # moondream'i (soğukken ~10s) çağırmadan hızlıca yanıtlanır.
    if question and not _needs_vision(question):
        ocr_context, _app_ctx = _get_cached_ocr()
        if ocr_context:
            ans = _synthesize_answer(question, "", ocr_context)
            return ToolResult(message=ans or ocr_context, ok=True)
        # OCR boş ama görsel açık → vision'a düş (aşağıdaki hibrit yol)
        if not use_vision:
            return ToolResult(message="Ekran okunamadı.", ok=True)

    # ── HİBRİT YOL: görsel + OCR paralel → ana Türkçe LLM sentezi ───────────
    # Hem genel bakış (soru yok) hem görsel sorular bu yoldan geçer.
    b64 = ""
    if use_vision:
        try:
            _, b64 = _capture_compressed()
        except Exception:
            b64 = ""

    ocr_context = ""
    visual_en = ""

    if use_vision and b64:
        say_filler_if_cold()   # moondream soğuksa "bakıyorum" çal (algılanan gecikme gizlenir)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            future_ocr = ex.submit(_get_ocr_uia_context)
            future_vis = ex.submit(_query_vision_llm, b64, _VISION_PROMPT_EN, _VISION_TIMEOUT)
            try:
                r = future_ocr.result(timeout=_OCR_TIMEOUT)
                ocr_context = r[0] if isinstance(r, tuple) else str(r)
            except Exception:
                ocr_context = ""
            try:
                visual_en = future_vis.result(timeout=_VISION_TIMEOUT + 2)
            except Exception:
                visual_en = ""
    else:
        try:
            r = _get_ocr_uia_context()
            ocr_context = r[0] if isinstance(r, tuple) else str(r)
        except Exception:
            ocr_context = ""

    # Görsel + metni ana Türkçe LLM ile birleştir → doğru, Türkçe, odaklı cevap
    if visual_en or ocr_context:
        synth = _synthesize_answer(question, visual_en, ocr_context)
        if synth:
            return ToolResult(message=synth, ok=True)

    return ToolResult(message=ocr_context or _fast_screen_text() or "Ekran okunamadı.", ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# Hafif yardımcılar
# ──────────────────────────────────────────────────────────────────────────────

def quick_screen_check(query: str) -> ToolResult:
    """
    Ekranda belirli bir şey var mı hızlıca kontrol et.
    OCR tabanlı — vision model gerekmez.
    Returns: "VAR: ..." veya "YOK"
    """
    if not query or not query.strip():
        return ToolResult(message="Aranacak metin belirtilmedi.", ok=True)
    try:
        text = _fast_screen_text().lower()
        q_lower = query.lower()
        if q_lower in text:
            idx = text.find(q_lower)
            ctx = text[max(0, idx - 50): idx + len(q_lower) + 80].strip()
            return ToolResult(message=f"VAR: '{query}' ekranda bulundu — ...{ctx}...", ok=True, data=True)
        return ToolResult(message=f"YOK: '{query}' ekranda görünmüyor.", ok=True, data=False)
    except Exception as e:
        return ToolResult(message=f"Ekran kontrolü yapılamadı: {e}", ok=False, error=str(e))


def get_active_app_info() -> dict:
    """Aktif uygulama bilgisini hızlıca döndür."""
    try:
        from vision.screen_interpreter import get_screen_context
        ctx = get_screen_context()
        return {
            "title":    ctx.get("window_title", ""),
            "app_name": ctx.get("app_name", "Bilinmiyor"),
            "app_type": ctx.get("app_type", "unknown"),
            "url":      ctx.get("url", ""),
        }
    except Exception:
        return {"title": "", "app_name": "Bilinmiyor", "app_type": "unknown", "url": ""}
