# -*- coding: utf-8 -*-
"""
vision/screen_interpreter.py — Akıllı ekran anlama ve bağlamsal yardım.

Sadece "ekranda ne var" değil; "kullanıcı ne yapıyor, ne soruyor, nasıl
yardımcı olabilirim" sorusunu cevaplar.

API:
  get_screen_context()              → Yapılandırılmış ekran bağlamı (dict)
  interpret_screen(question="")     → Türkçe ekran yorumu (pasif/aktif)
  assist_from_screen(question)      → Kullanıcı sorusuna ekran bağlamında cevap
"""

from core.logger import get_logger
import time
from typing import Any, Dict, List, Tuple

from brain.tool_result import ToolResult

log = get_logger("screen_interpreter")


# ──────────────────────────────────────────────────────────────────────────────
# Uygulama profilleri
# ──────────────────────────────────────────────────────────────────────────────

# Pencere başlığında aranan anahtar kelime → profil
_APP_PROFILES: Dict[str, Dict[str, Any]] = {
    # Kod editörleri
    "code":             {"type": "code_editor",    "name": "VSCode"},
    "visual studio":    {"type": "code_editor",    "name": "Visual Studio"},
    "pycharm":          {"type": "code_editor",    "name": "PyCharm"},
    "intellij":         {"type": "code_editor",    "name": "IntelliJ IDEA"},
    "sublime":          {"type": "code_editor",    "name": "Sublime Text"},
    "notepad++":        {"type": "code_editor",    "name": "Notepad++"},
    "cursor":           {"type": "code_editor",    "name": "Cursor AI"},
    "vim":              {"type": "code_editor",    "name": "Vim"},
    "neovim":           {"type": "code_editor",    "name": "Neovim"},
    # Belgeler / yazı
    "word":             {"type": "document",       "name": "Word"},
    "notepad":          {"type": "document",       "name": "Not Defteri"},
    "wordpad":          {"type": "document",       "name": "WordPad"},
    "libreoffice":      {"type": "document",       "name": "LibreOffice"},
    "obsidian":         {"type": "document",       "name": "Obsidian"},
    "notion":           {"type": "document",       "name": "Notion"},
    # Tablolar
    "excel":            {"type": "spreadsheet",    "name": "Excel"},
    "sheets":           {"type": "spreadsheet",    "name": "Google Sheets"},
    # Sunumlar
    "powerpoint":       {"type": "presentation",   "name": "PowerPoint"},
    "impress":          {"type": "presentation",   "name": "LibreOffice Impress"},
    # Tarayıcılar
    "chrome":           {"type": "browser",        "name": "Chrome"},
    "brave":            {"type": "browser",        "name": "Brave"},
    "firefox":          {"type": "browser",        "name": "Firefox"},
    "edge":             {"type": "browser",        "name": "Edge"},
    "opera":            {"type": "browser",        "name": "Opera"},
    "safari":           {"type": "browser",        "name": "Safari"},
    # Medya
    "spotify":          {"type": "media_player",   "name": "Spotify"},
    "vlc":              {"type": "media_player",   "name": "VLC"},
    "youtube music":    {"type": "media_player",   "name": "YouTube Music"},
    "foobar":           {"type": "media_player",   "name": "Foobar2000"},
    "netflix":          {"type": "video",          "name": "Netflix"},
    "youtube":          {"type": "video",          "name": "YouTube"},
    "twitch":           {"type": "video",          "name": "Twitch"},
    # Sohbet / iletişim
    "discord":          {"type": "chat",           "name": "Discord"},
    "whatsapp":         {"type": "chat",           "name": "WhatsApp"},
    "telegram":         {"type": "chat",           "name": "Telegram"},
    "slack":            {"type": "chat",           "name": "Slack"},
    "teams":            {"type": "chat",           "name": "Microsoft Teams"},
    "outlook":          {"type": "email",          "name": "Outlook"},
    "thunderbird":      {"type": "email",          "name": "Thunderbird"},
    # Dosya yönetimi
    "explorer":         {"type": "file_manager",   "name": "Dosya Gezgini"},
    "dosya gezgini":    {"type": "file_manager",   "name": "Dosya Gezgini"},
    # Terminal / komut satırı
    "windows terminal": {"type": "terminal",       "name": "Windows Terminal"},
    "powershell":       {"type": "terminal",       "name": "PowerShell"},
    "cmd":              {"type": "terminal",       "name": "Komut İstemi"},
    "wsl":              {"type": "terminal",       "name": "WSL"},
    "bash":             {"type": "terminal",       "name": "Bash"},
    # Sistem araçları
    "task manager":     {"type": "system",         "name": "Görev Yöneticisi"},
    "görev yöneticisi": {"type": "system",         "name": "Görev Yöneticisi"},
    "settings":         {"type": "system",         "name": "Ayarlar"},
    "ayarlar":          {"type": "system",         "name": "Ayarlar"},
    "control panel":    {"type": "system",         "name": "Denetim Masası"},
    "denetim masasi":   {"type": "system",         "name": "Denetim Masası"},
    "regedit":          {"type": "system",         "name": "Kayıt Defteri"},
    # Tasarım
    "figma":            {"type": "design",         "name": "Figma"},
    "photoshop":        {"type": "design",         "name": "Photoshop"},
    "gimp":             {"type": "design",         "name": "GIMP"},
    "inkscape":         {"type": "design",         "name": "Inkscape"},
    # Oyun / eğlence
    "steam":            {"type": "gaming",         "name": "Steam"},
}

# Aktivite tipine göre LLM talimatları — ne anlaması ve söylemesi gerektiğini yönlendirir
_ACTIVITY_INSTRUCTIONS: Dict[str, str] = {
    "code_editor": (
        "Kullanıcı KOD YAZIYOR veya düzenliyor. "
        "Görünen dosya adını ve programlama dilini belirt. "
        "Ekrandaki kodu incele: ne yapıyor, hata/uyarı var mı, imleç nerede? "
        "Kullanıcının sorusu varsa o koda odaklan."
    ),
    "document": (
        "Kullanıcı METIN veya belge üzerinde çalışıyor. "
        "Belge başlığını, görünen içeriği belirt. "
        "Ne yazdığını veya okuduğunu anla."
    ),
    "spreadsheet": (
        "Kullanıcı TABLO veya veri üzerinde çalışıyor. "
        "Sayfa adını, aktif hücreyi, görünen veriyi özetle."
    ),
    "presentation": (
        "Kullanıcı SUNUM hazırlıyor. "
        "Hangi slaytta olduğunu, içeriği belirt."
    ),
    "browser": (
        "Kullanıcı WEB'DE geziyor. "
        "URL'yi, sayfa başlığını ve görünen içeriği belirt. "
        "Ne arıyor veya okuyor? Kullanıcının sorusuyla ilgili kısımları vurgula."
    ),
    "media_player": (
        "Kullanıcı MÜZİK veya medya dinliyor. "
        "Şu an ne çalıyor, sanatçı/şarkı adı nedir, duraklatılmış mı? Bunu net belirt."
    ),
    "video": (
        "Kullanıcı VİDEO izliyor. "
        "İzlenen içerik başlığını, hangi platformda olduğunu belirt."
    ),
    "chat": (
        "Kullanıcı MESAJLAŞIYOR. "
        "Hangi konuşmada olduğunu, son mesajları özetle. "
        "Kullanıcının sorusuyla ilgiliyse içeriği kullan."
    ),
    "email": (
        "Kullanıcı E-POSTA uygulamasında. "
        "Açık e-postanın konusunu, göndereni, içeriğini özetle."
    ),
    "file_manager": (
        "Kullanıcı DOSYA YÖNETİCİSİNDE. "
        "Geçerli dizin yolunu, görünen dosya ve klasörleri listele."
    ),
    "terminal": (
        "Kullanıcı TERMINALDE çalışıyor. "
        "Geçerli dizini, son çalıştırılan komutu ve çıktısını belirt. "
        "Hata varsa vurgula."
    ),
    "system": (
        "Kullanıcı SİSTEM ARAÇLARINDA. "
        "Hangi bölümde olduğunu, görünen seçenek ve değerleri belirt."
    ),
    "design": (
        "Kullanıcı TASARIM UYGULAMASINDA çalışıyor. "
        "Açık dosya/proje adını, görünen içeriği belirt."
    ),
    "gaming": (
        "Kullanıcı OYUN PLATFORMUNDA. "
        "Görünen oyun veya içeriği belirt."
    ),
    "unknown": (
        "Ekranda ne görüldüğünü anla: aktif uygulama, içerik, önemli öğeler. "
        "Kullanıcının sorusuna cevap vermek için hangi bilgi önemli?"
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# Yardımcı fonksiyonlar
# ──────────────────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    t = str(text or "").lower().strip()
    for s, d in [("ı","i"),("ğ","g"),("ü","u"),("ş","s"),("ö","o"),("ç","c")]:
        t = t.replace(s, d)
    return " ".join(t.split())


def _detect_profile(window_title: str) -> Tuple[str, Dict[str, Any]]:
    """Pencere başlığından uygulama profilini tespit et."""
    nt = _norm(window_title)
    for keyword, profile in _APP_PROFILES.items():
        if keyword in nt:
            return keyword, dict(profile)
    return "", {"type": "unknown", "name": window_title or "Bilinmiyor"}


def _extract_browser_url(controls: List[Dict[str, Any]]) -> str:
    """UIA kontrollerinden tarayıcı URL'sini çıkar."""
    url_hints = {"address", "url", "omnibox", "location", "addressbar"}
    for ctrl in controls:
        aid = _norm(ctrl.get("automation_id", ""))
        name = str(ctrl.get("name", "")).strip()
        if any(h in aid for h in url_hints) and name and len(name) > 4:
            return name
    for ctrl in controls:
        name = str(ctrl.get("name", "")).strip()
        if name.startswith(("http://", "https://", "www.")):
            return name
    return ""


def _extract_content_from_controls(controls: List[Dict[str, Any]], limit: int = 80) -> str:
    """
    UIA kontrollerinden görünen anlamlı içerik metnini çıkar.
    Buton/menü/sekme etiketlerini atlar, asıl içeriğe odaklanır.
    """
    SKIP_TYPES = {
        "buttoncontrol", "menuitemcontrol", "tabitemcontrol",
        "menucontrol", "toolbarcontrol",
    }
    SKIP_LABELS = {
        "", "unknown", "pane", "group", "window", "custom",
        "adsız öğe", "adsiz oge", "close", "minimize", "maximize",
    }

    texts: List[str] = []
    seen: set = set()

    for ctrl in controls:
        ctype = _norm(ctrl.get("control_type", ""))
        if ctype in SKIP_TYPES:
            continue
        name = str(ctrl.get("name", "")).strip()
        if not name:
            continue
        nk = _norm(name)
        if nk in SKIP_LABELS or len(nk) < 2 or len(name) > 600:
            continue
        if nk in seen:
            continue
        seen.add(nk)
        texts.append(name)
        if len(texts) >= limit:
            break

    return "\n".join(texts)


# ──────────────────────────────────────────────────────────────────────────────
# Ana API
# ──────────────────────────────────────────────────────────────────────────────

def get_screen_context() -> Dict[str, Any]:
    """
    Ekranın tam yapılandırılmış bağlamını döndür.

    Dönen dict anahtarları:
      app_type       : "code_editor" | "browser" | "chat" | vb.
      app_name       : "VSCode" | "Brave" | vb.
      window_title   : aktif pencere başlığı
      url            : tarayıcı URL (varsa)
      content_text   : OCR'den ham ekran metni
      controls_text  : UIA kontrollerinden çıkarılan metin içeriği
      inputs         : yazılabilir alanlar listesi
      clickable      : tıklanabilir hedefler listesi
      state_hints    : durum sinyalleri
      ocr_available  : OCR kullanılabildi mi
      timestamp      : float
    """
    ctx: Dict[str, Any] = {
        "app_type":     "unknown",
        "app_name":     "Bilinmiyor",
        "window_title": "",
        "url":          "",
        "content_text": "",
        "controls_text": "",
        "inputs":       [],
        "clickable":    [],
        "state_hints":  [],
        "ocr_available": False,
        "timestamp":    time.time(),
    }

    # ── Tek OCR çağrısı: snapshot + scene aynı veriden ──────────
    try:
        from vision.screen_reader import get_ui_snapshot
        from vision.screen_scene import build_screen_scene, _labels

        snap = get_ui_snapshot(include_ocr=True)   # find_text_elements_v2: ~3s (TEK çağrı)
        scene = build_screen_scene(_snap=snap)      # aynı snap'ı kullan, ikinci OCR yok

        window_title = str(snap.get("window_title", "") or "")
        controls = snap.get("controls", []) or []
        ctx["window_title"] = window_title
        ctx["ocr_available"] = bool(snap.get("ocr_count", 0))

        _, profile = _detect_profile(window_title)
        ctx["app_type"] = profile.get("type", "unknown")
        ctx["app_name"] = profile.get("name", window_title or "Bilinmiyor")

        if ctx["app_type"] == "browser":
            ctx["url"] = _extract_browser_url(controls)

        ctx["controls_text"] = _extract_content_from_controls(controls)
        ctx["inputs"]      = _labels(scene.get("inputs", []),     limit=6,  detailed=True)
        ctx["clickable"]   = _labels(scene.get("actionable", []), limit=14, detailed=False)
        ctx["state_hints"] = scene.get("state_hints", [])

        if ctx["controls_text"]:
            ctx["content_text"]  = ctx["controls_text"]
            ctx["ocr_available"] = True

    except Exception as e:
        log.warning("get_screen_context failed: %s", e)

    return ctx


def _build_screen_summary(ctx: Dict[str, Any], max_content: int = 2000) -> str:
    """Bağlam dict'inden LLM'e verilecek yapılandırılmış özet oluştur."""
    lines: List[str] = []

    lines.append(f"Aktif Pencere : {ctx['window_title'] or '(bilinmiyor)'}")
    lines.append(f"Uygulama      : {ctx['app_name']}  [{ctx['app_type']}]")

    if ctx.get("url"):
        lines.append(f"URL           : {ctx['url']}")

    if ctx.get("state_hints"):
        lines.append("Durum Sinyali : " + " | ".join(ctx["state_hints"]))

    if ctx.get("inputs"):
        lines.append("Yazı Alanları : " + " | ".join(ctx["inputs"]))

    if ctx.get("clickable"):
        lines.append("Tıklanabilir  : " + " | ".join(ctx["clickable"][:10]))

    # İçerik — en değerli kısım
    content = ctx.get("content_text", "").strip()
    if not content:
        content = ctx.get("controls_text", "").strip()

    if content:
        if len(content) > max_content:
            content = content[:max_content] + "\n...(devamı kırpıldı)"
        lines.append(f"\n── Ekran İçeriği ──\n{content}")

    return "\n".join(lines)


def interpret_screen(user_question: str = "") -> ToolResult:
    """
    Ekranı anlayıp Türkçe, bağlamsal yorum döndür.

    user_question: Kullanıcının sorusu veya isteği.
                   Boşsa genel ekran tanımı yapılır.
                   Doluysa soruya odaklanarak cevap verilir.
    """
    ctx = get_screen_context()
    screen_summary = _build_screen_summary(ctx)

    activity_instruction = _ACTIVITY_INSTRUCTIONS.get(
        ctx["app_type"],
        _ACTIVITY_INSTRUCTIONS["unknown"],
    )

    question = str(user_question or "").strip()

    if question:
        focus = (
            f"Kullanıcının sorusu/isteği: {question}\n\n"
            f"Ekrana bakarak bu soruyu cevapla. {activity_instruction}"
        )
    else:
        focus = f"Ekranda ne yapıldığını anla ve açıkla. {activity_instruction}"

    prompt = f"""Aşağıda bir Windows ekranının tam analizi var. Bunu kullanarak yardımcı ol.

{focus}

Kurallar:
- Türkçe cevap ver
- Ekranda GERÇEKTEN GÖRÜNENLERE dayan — tahmin ekleme
- Varsa hata / uyarı / önemli durumu AÇIKÇA belirt
- Kod görünüyorsa ne yaptığını anla
- Net, kısa ve doğrudan ol
- Kullanıcının sorusu varsa ona odaklan, gereksiz özet yazma

━━━ EKRAN ANALİZİ ━━━
{screen_summary}
━━━━━━━━━━━━━━━━━━━━""".strip()

    try:
        from brain.llm import call_ollama
        result = call_ollama(prompt, retries=2, timeout=35)
        if result and not result.startswith("LLM hatası"):
            return ToolResult(message=result, ok=True)
    except Exception as e:
        log.warning("interpret_screen LLM failed: %s", e)

    # LLM başarısız → yapısal fallback
    parts = [f"Aktif: {ctx['window_title'] or 'bilinmiyor'} ({ctx['app_name']})"]
    if ctx.get("url"):
        parts.append(f"URL: {ctx['url']}")
    content = ctx.get("content_text") or ctx.get("controls_text", "")
    if content:
        parts.append(f"İçerik:\n{content[:1000]}")
    return ToolResult(message="\n".join(parts) or "Ekran okunamadı.", ok=True)


def assist_from_screen(user_question: str) -> ToolResult:
    """
    Kullanıcının sorusuna ekran bağlamında yardım et.
    interpret_screen'in soru-odaklı kısayolu.
    """
    return interpret_screen(user_question=user_question)
