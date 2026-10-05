"""
LLM tabanlı niyet çözümleyici.
Kullanıcının serbest dildeki komutunu analiz ederek
{action, arg, confidence, reasoning} döndürür.

Geriye dönük uyumlu — aynı (action, arg) tuple döner.
"""

import json
import re
import threading
import time
# call_ollama — ilk niyet çözümlemesinde lazy yüklenir

# Niyet çözümleyici sonuç önbelleği — aynı komutu tekrar LLM'e gönderme
_intent_cache: dict = {}     # {normalized_text: (ts, action, arg)}
_INTENT_CACHE_TTL = 30.0     # saniye — diğer bellek önbellekleriyle tutarlı
_intent_cache_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Tüm kayıtlı araçların tam listesi
# ---------------------------------------------------------------------------
KNOWN_ACTIONS = [
    # Program / dosya / klasör / site / oyun
    "open_program",
    "open_website",
    "open_folder",
    "open_file",
    "launch_game",

    # Arama
    "google_search",
    "web_search",
    "youtube_search",
    "spotify_search",
    "random_music",

    # Klavye & fare
    "type_text",
    "press_key",
    "hotkey",
    "scroll_up",
    "scroll_down",
    "double_click",
    "right_click",
    "click_center",

    # Ekran & OCR
    "screen_read",
    "find_buttons",
    "find_and_click",
    "find_and_double_click",

    # Pencere yönetimi
    "active_window",
    "list_windows",
    "focus_window",
    "close_window",
    "close_active_window",
    "minimize_window",
    "maximize_window",
    "switch_window",

    # Komut öğrenme
    "learn_command",
    "run_learned_command",
    "list_learned_commands",
    "recipe_summary",

    # Ses
    "get_volume",
    "set_volume",
    "mute_volume",
    "unmute_volume",

    # Ekran parlaklığı & tema
    "get_brightness",
    "set_brightness",
    "enable_dark_mode",
    "enable_light_mode",
    "enable_night_light",
    "disable_night_light",

    # Güç yönetimi
    "sleep_pc",
    "lock_pc",
    "shutdown_pc",
    "restart_pc",
    "cancel_shutdown",

    # Ağ
    "get_wifi_status",
    "list_wifi_networks",
    "connect_wifi",
    "disconnect_wifi",
    "enable_wifi",
    "disable_wifi",

    # Bluetooth
    "get_bluetooth_status",
    "enable_bluetooth",
    "disable_bluetooth",
    "list_bluetooth_devices",

    # Sistem bilgisi & süreçler
    "system_info",
    "battery_status",
    "disk_info",
    "list_processes",
    "kill_process",

    # Pano & ekran
    "get_clipboard",
    "set_clipboard",
    "get_resolution",
    "turn_off_monitor",

    # Başlangıç & ayarlar
    "system_startup_status",
    "enable_startup",
    "disable_startup",
    "open_windows_settings",

    # Dosya & terminal
    "delete_file",
    "run_terminal",
    "run_shell",

    # Skills
    "skill_bellek_durumu",
    "skill_hizli_hesap",
    "skill_sistem_bilgisi",

    # Genel
    "memory_answer",
    "chat",
]

# ---------------------------------------------------------------------------
# LLM sistem promptu — araçlar semantik olarak tanımlanır.
# LLM hangi cümlelerin tetiklediğini değil, aracın ne yaptığını bilir.
# ---------------------------------------------------------------------------
_SYSTEM = """Windows asistan niyet çözümleyicisi. Türkçe komutu analiz et → SADECE JSON döndür.
FORMAT: {"action":"...", "arg":"...", "confidence":0.0}

ARAÇLAR (action=arg):
open_program=program_adı | open_website=url | open_folder=yol | open_file=dosya | launch_game=oyun
google_search=sorgu | web_search=sorgu | youtube_search=sorgu | spotify_search=sorgu | random_music=
type_text=metin | press_key=tuş | hotkey=ctrl+x | scroll_up= | scroll_down= | right_click= | double_click= | click_center=
screen_read=soru | find_buttons= | find_and_click=hedef | find_and_double_click=hedef
active_window= | list_windows= | focus_window=isim | close_window=isim | close_active_window= | minimize_window=isim | maximize_window=isim | switch_window=
learn_command=cümle | run_learned_command=komut | list_learned_commands= | recipe_summary=
get_volume= | set_volume=0-100 | mute_volume= | unmute_volume=
get_brightness= | set_brightness=0-100 | enable_dark_mode= | enable_light_mode= | enable_night_light= | disable_night_light=
sleep_pc= | lock_pc= | shutdown_pc= | restart_pc= | cancel_shutdown=
get_wifi_status= | list_wifi_networks= | connect_wifi=ağ | disconnect_wifi= | enable_wifi= | disable_wifi=
get_bluetooth_status= | enable_bluetooth= | disable_bluetooth= | list_bluetooth_devices=
system_info= | battery_status= | disk_info= | list_processes= | kill_process=isim
get_clipboard= | set_clipboard=metin | get_resolution= | turn_off_monitor=
system_startup_status= | enable_startup= | disable_startup= | open_windows_settings=kategori
delete_file=dosya_yolu | run_terminal=komut | run_shell=komut
skill_hizli_hesap=hesap | skill_bellek_durumu= | skill_sistem_bilgisi=
chat=soru_veya_istek

KURAL: Eylem→araç seç. Sohbet/bilgi→chat. Emin değilsen→chat. Sadece JSON yaz."""

# ---------------------------------------------------------------------------
# Türkçe karakter normalizasyonu
# ---------------------------------------------------------------------------
_TR_NORM = str.maketrans(
    "çğıöşüÇĞİÖŞÜ",
    "cgiosuCGIOSU",
)

def _tr_norm(text: str) -> str:
    """Türkçe harfleri ASCII'ye dönüştür — yazım hatası toleransı için."""
    return text.translate(_TR_NORM)


# Wake/çağrı kelimelerini metinden soy
_WAKE_PREFIXES = re.compile(
    r"^(?:klargus|hey\s+klargus|ey\s+klargus|jarvis|hey\s+jarvis)\s*[,:]?\s*",
    re.IGNORECASE,
)

def _strip_wake(text: str) -> str:
    """'klargus spotify aç' → 'spotify aç'"""
    return _WAKE_PREFIXES.sub("", text).strip()


# ---------------------------------------------------------------------------
# Hızlı kural eşleştirici — LLM çağrısına gerek kalmadan açık komutlar
# ---------------------------------------------------------------------------
def _fast_precheck(text: str):
    """
    LLM'e gitmeden önce açık ve net komutları yakalar.
    None döndürürse LLM devreye girer.
    """
    # Wake/çağrı kelimesini soy: "klargus brave aç" → "brave aç"
    text = _strip_wake(text)
    if not text:
        return ("chat", "")

    low  = text.lower().strip()
    norm = _tr_norm(low)   # Türkçe harfsiz versiyon — toleranslı eşleştirme için

    # ── Komut öğretme ───────────────────────────────────────────────────────
    if re.search(r"bundan sonra .+ (dediğimde|deyince|dediğim zaman)", low):
        return ("learn_command", text)

    if any(x in low for x in ["öğrettiğim komutlar", "kayıtlı komutlar", "hangi komutları öğrettim"]):
        return ("list_learned_commands", "")

    if any(x in low for x in ["görev tarifleri", "gorev tarifleri", "kayıtlı görevler", "kayitli gorevler",
                               "ne öğrendin", "neler öğrendin", "öğrendiğin görevler", "tarif listesi"]):
        return ("recipe_summary", "")

    # ── Hotkey ──────────────────────────────────────────────────────────────
    # Çok kelimeli: substring eşleme | Tek kelimeli: tam eşleme (false-positive önle)
    # Örn: "bul" → substring olursa "şarkı bul" de Ctrl+F olurdu
    _HOTKEYS_MULTI = {
        "ctrl c": "ctrl+c",
        "ctrl v": "ctrl+v",
        "ctrl z": "ctrl+z",
        "ctrl y": "ctrl+y",
        "ctrl a": "ctrl+a",
        "ctrl s": "ctrl+s",
        "ctrl f": "ctrl+f",
        "ctrl p": "ctrl+p",
        "ctrl n": "ctrl+n",
        "ctrl w": "ctrl+w",
        "ctrl tab": "ctrl+tab",
        "alt tab": "alt+tab",
        "alt f4": "alt+f4",
        "win d": "win+d",
        "geri al": "ctrl+z",
        "ileri al": "ctrl+y",
        "hepsini seç": "ctrl+a", "hepsini sec": "ctrl+a",
        "masaüstüne git": "win+d",
    }
    # Tek kelimeli hotkey'ler — sadece TAM eşleme (sohbette yanlış tetiklenmesin)
    _HOTKEYS_EXACT = {
        "kopyala": "ctrl+c",
        "yapıştır": "ctrl+v", "yapistir": "ctrl+v",
        "kaydet": "ctrl+s",
        "yazdır": "ctrl+p",
    }
    for phrase, combo in _HOTKEYS_MULTI.items():
        if phrase in low:
            return ("hotkey", combo)
    if low in _HOTKEYS_EXACT:
        return ("hotkey", _HOTKEYS_EXACT[low])

    # ── Tuşa bas ────────────────────────────────────────────────────────────
    _KEYS = {
        "enter": "enter", "entere bas": "enter", "enter bas": "enter",
        "backspace": "backspace", "geri sil": "backspace",
        "escape": "escape", "esc": "escape", "esc bas": "escape",
        "tab": "tab", "tab bas": "tab",
        "delete": "delete", "sil tuşu": "delete",
        "boşluk": "space", "space bas": "space",
        "yukarı ok": "up", "aşağı ok": "down",
        "sol ok": "left", "sağ ok": "right",
        "f5": "f5", "f11": "f11",
    }
    if low in _KEYS:
        return ("press_key", _KEYS[low])

    # ── ASCII fallback — Türkçe karaktersiz yazım ────────────────────────────
    # norm (ASCII) üzerinden de kontrol et — kullanıcı ç/ş/ı yazmamış olabilir
    if any(x in norm for x in ["karanlik mod", "dark mod", "dark mode", "koyu tema"]):
        return ("enable_dark_mode", "")
    if any(x in norm for x in ["acik mod", "light mod", "light mode", "beyaz tema"]):
        return ("enable_light_mode", "")
    if any(x in norm for x in ["gece isigi ac", "night light ac", "mavi isik filtresi ac"]):
        return ("enable_night_light", "")
    if any(x in norm for x in ["gece isigi kapat", "night light kapat"]):
        return ("disable_night_light", "")
    if any(x in norm for x in ["sesi ac", "sessizi kaldir", "unmute", "sesi geri ac"]):
        return ("unmute_volume", "")
    if any(x in norm for x in ["sesi kapat", "sessize al", "sesi sustur", "mute et"]):
        return ("mute_volume", "")
    if any(x in norm for x in ["yeniden baslat", "restart", "reboot"]):
        if "iptal" not in norm:
            return ("restart_pc", "")
    if any(x in norm for x in ["bilgisayari kapat", "sistemi kapat", "shutdown"]):
        if "iptal" not in norm:
            return ("shutdown_pc", "")

    # ── Scroll ──────────────────────────────────────────────────────────────
    if any(x in norm for x in ["asagi kaydir", "scroll down", "asagi scroll",
                                "asagiya in", "asagi git"]):
        return ("scroll_down", "")
    if any(x in norm for x in ["yukari kaydir", "scroll up", "yukari scroll",
                                "yukari cik", "yukari git"]):
        return ("scroll_up", "")

    # ── Tıklama ─────────────────────────────────────────────────────────────
    if any(x in norm for x in ["sag tikla", "sag tik", "right click"]):
        return ("right_click", "")
    if any(x in norm for x in ["cift tikla", "cift tik", "double click"]):
        return ("double_click", "")
    if any(x in norm for x in ["ortaya tikla", "merkeze tikla", "ekranin ortasina tikla"]):
        return ("click_center", "")

    # find_and_click — "X butonuna tıkla", "X'e bas", "X bul ve tıkla"
    _fac = re.search(
        r"^(.+?)\s+(?:butonuna|düğmesine|linkine|seçeneğine|öğesine)?\s*"
        r"(?:tıkla|bas|tikla|bul\s+ve\s+tıkla|üzerine\s+tıkla)$",
        low,
    )
    if _fac:
        target = _fac.group(1).strip()
        _skip = {"ortaya", "merkeze", "sağa", "sola", "çift", "double", "sağ", "sol"}
        if target and target not in _skip and len(target) > 1:
            return ("find_and_click", target)

    # ── Ekran ───────────────────────────────────────────────────────────────
    _screen_q = (
        any(x in norm for x in [
            "ekrani oku", "ekranda ne var", "ne goruyorsun",
            "ekrana bak", "ekrani anlat", "ekranda ne yaziyor",
            "ekrana bakarak", "ekrana bakip", "su an ne var",
            "su an ne acik", "ne caliyor", "ne izliyorum",
            "ekrani yorumla", "bunu acikla", "bu koda bak",
            "bu sayfaya bak",
            # Ekran üzerindeki şeyler hakkında sorular
            "ekrandaki", "ekraninda", "bu ekranda", "bu pencerede",
            "neden kirmizi", "neden yesil", "neden mavi", "neden sari",
            "neden turuncu", "neden mor", "rengi ne", "ne renk",
            "bu simge", "bu ikon", "bu buton", "bu yazi", "bu sayi",
            "bu isaret", "bu sembol", "bu rakam", "bu dugme",
        ])
        or any(x in low for x in [
            "ne görüyorsun", "ekranı oku", "ekrana bak",
            "ne çalıyor", "ekranı anlat", "ekranda ne var",
            "ekrandaki", "bu ekranda", "neden kırmızı", "neden yeşil",
            "neden mavi", "rengi ne", "bu simge", "bu ikon",
        ])
    )
    if _screen_q:
        return ("screen_read", text)
    if any(x in norm for x in ["butonlari bul", "hangi butonlar var", "tiklanabilir ogeler"]):
        return ("find_buttons", "")

    # ── Pencere ─────────────────────────────────────────────────────────────
    # Spesifik kontroller önce (aktif pencereyi kapat > aktif pencere)
    if any(x in low for x in ["aktif pencereyi kapat", "bu pencereyi kapat", "şu anki pencereyi kapat"]):
        return ("close_active_window", "")
    if any(x in low for x in ["aktif pencere", "hangi pencere açık", "şu an hangi program"]):
        return ("active_window", "")
    if any(x in low for x in ["pencereleri listele", "açık pencereler", "hangi programlar açık"]):
        return ("list_windows", "")

    # minimize / maximize — belirli bir pencere adı yoksa aktif pencere
    m_min = re.search(r"^(.+?)\s+(?:penceresini\s+)?küçült$", low)
    if m_min:
        target = m_min.group(1).strip()
        return ("minimize_window", "" if target in {"onu", "bunu", "şunu", "aktif"} else target)
    m_max = re.search(r"^(.+?)\s+(?:penceresini\s+)?(?:büyüt|tam ekran yap|maximize et)$", low)
    if m_max:
        target = m_max.group(1).strip()
        return ("maximize_window", "" if target in {"onu", "bunu", "şunu", "aktif"} else target)
    if any(x in low for x in ["pencere değiştir", "pencereyi değiştir", "uygulamalar arası geç"]):
        return ("switch_window", "")

    # focus_window — "X'e geç", "X penceresine odaklan"
    m_focus = re.search(r"^(.+?)\s+(?:penceresine\s+)?(?:geç|odaklan|öne\s+getir|ön\s+plana\s+getir)$", low)
    if m_focus:
        target = m_focus.group(1).strip()
        _skip2 = {"pencere", "uygulama", "tab", "sekme"}
        if target and target not in _skip2:
            return ("focus_window", target)

    # close_window — belirli bir uygulamanın penceresini kapat
    m_cwin = re.search(r"^(.+?)\s+(?:penceresini|uygulamasını)\s+kapat$", low)
    if m_cwin:
        return ("close_window", m_cwin.group(1).strip())

    # ── Metin yazma ─────────────────────────────────────────────────────────
    m_type = re.match(r"^\s*(?:şunu|sunu|bunu)\s+yaz\s*:?\s*(.+)$", text, re.IGNORECASE)
    if m_type:
        return ("type_text", m_type.group(1).strip())
    # "yaz geldi / yaz mevsimi" gibi mevsim ifadeleri yazma komutu değil
    _YAZ_NOT_DICTATION = {
        "geldi", "geliyor", "bitti", "gidiyor", "tatili", "mevsimi",
        "zamanı", "ayında", "günleri", "sıcakları", "yağmurları",
    }
    _after_yaz = low[4:].split()[0] if len(low) > 4 else ""
    if low.startswith("yaz ") and len(low) > 5 and "?" not in low \
            and _after_yaz not in _YAZ_NOT_DICTATION:
        return ("type_text", text[4:].strip())

    # ── Ses seviyesi ────────────────────────────────────────────────────────
    if any(x in low for x in ["ses seviyesi kaç", "ses kaç", "volume kaç", "sesi sor", "ses yüzde"]):
        return ("get_volume", "")
    # "sesi 50 yap" / "ses seviyesi 70" / "ses seviyesini 30 ayarla"
    m_vol = re.search(r"\bses\S*\s+(?:yüzde\s*)?(\d{1,3})(?:\s+(?:yap|ayarla|kur|olarak))?", low)
    if not m_vol:
        m_vol = re.search(r"\bses\s+\S+\s+(?:yüzde\s*)?(\d{1,3})(?:\s+(?:yap|ayarla|kur|olarak))?", low)
    if m_vol:
        return ("set_volume", m_vol.group(1))
    m_vol2 = re.search(r"\bvolume\s+(\d{1,3})\b", low)
    if m_vol2:
        return ("set_volume", m_vol2.group(1))
    # Mute/unmute — artır/azalt'tan önce kontrol et
    if any(x in low for x in ["sesi kapat", "ses kapat", "sessize al", "sesi sustur",
                               "mute et", "mute yap", "sesi kıs", "sesi sus"]) \
       or any(x in norm for x in ["sesi kapat", "ses kapat", "sessize al", "mute et"]):
        return ("mute_volume", "")
    if any(x in low for x in ["sesi aç", "sessizi kaldır", "unmute", "sesi geri aç", "sesi ac"]) \
       or any(x in norm for x in ["sesi ac", "sessizi kaldir", "unmute", "sesi geri ac"]):
        return ("unmute_volume", "")
    # Ses artır / azalt (sayısız) — "aç" buraya dahil değil
    if any(x in low for x in ["ses", "volume", "seviye"]) and any(x in low for x in ["artır", "artir", "yükselt", "yukseltt"]):
        m_a = re.search(r"(\d+)", low)
        return ("set_volume", f"artır {m_a.group(1)}" if m_a else "artır")
    if any(x in low for x in ["ses", "volume", "seviye"]) and any(x in low for x in ["azalt", "düşür", "dusur", "küçült"]):
        m_a = re.search(r"(\d+)", low)
        return ("set_volume", f"azalt {m_a.group(1)}" if m_a else "azalt")

    # ── Parlaklık ───────────────────────────────────────────────────────────
    if any(x in low for x in ["parlaklık kaç", "parlaklık ne", "brightness kaç", "ekranın parlaklığı"]):
        return ("get_brightness", "")
    m_br = re.search(r"parlakl\S*\s+(?:yüzde\s*)?(\d{1,3})(?:\s+(?:yap|ayarla|kur|olarak))?", low)
    if m_br:
        return ("set_brightness", m_br.group(1))
    m_br2 = re.search(r"\bbrightness\s+(\d{1,3})\b", low)
    if m_br2:
        return ("set_brightness", m_br2.group(1))

    # ── Tema & gece ışığı ────────────────────────────────────────────────────
    if any(x in low for x in ["karanlık mod", "dark mod", "dark mode", "koyu tema"]):
        return ("enable_dark_mode", "")
    if any(x in low for x in ["açık mod", "light mod", "light mode", "beyaz tema", "açık tema"]):
        return ("enable_light_mode", "")
    if any(x in low for x in ["gece ışığı aç", "gece modunu aç", "night light aç", "mavi ışık filtresi aç"]):
        return ("enable_night_light", "")
    if any(x in low for x in ["gece ışığı kapat", "gece modunu kapat", "night light kapat", "mavi ışık filtresi kapat"]):
        return ("disable_night_light", "")

    # ── Güç yönetimi ────────────────────────────────────────────────────────
    if any(x in low for x in ["kilitle", "ekranı kilitle", "oturumu kilitle", "lock"]):
        if not any(x in low for x in ["kilitli mi", "kilit durumu", "unlok"]):
            return ("lock_pc", "")
    if any(x in low for x in ["uyku", "uyut", "bekleme modu", "sleep"]):
        return ("sleep_pc", "")
    if any(x in low for x in ["bilgisayarı kapat", "pc'yi kapat", "sistemi kapat", "shutdown"]):
        if "iptal" not in low:
            return ("shutdown_pc", "")
    if any(x in low for x in ["yeniden başlat", "yeniden baslat", "restart", "reboot"]):
        if "iptal" not in low:
            return ("restart_pc", "")
    if any(x in low for x in ["kapatmayı iptal", "shutdown iptal", "restart iptal", "kapatma iptal"]):
        return ("cancel_shutdown", "")

    # ── WiFi ────────────────────────────────────────────────────────────────
    if any(x in low for x in ["wifi durum", "wi-fi durum", "ağ durum", "internet bağlantısı"]):
        return ("get_wifi_status", "")
    if any(x in low for x in ["ağları listele", "wifi ağları", "yakındaki ağlar", "wifi tara"]):
        return ("list_wifi_networks", "")
    if any(x in low for x in ["wifi aç", "wi-fi aç", "wifi'yi aç", "wifii ac"]):
        return ("enable_wifi", "")
    if any(x in low for x in ["wifi kapat", "wi-fi kapat", "wifi'yi kapat"]):
        return ("disable_wifi", "")
    if any(x in low for x in ["wifi kes", "ağ bağlantısını kes", "bağlantıyı kes"]):
        return ("disconnect_wifi", "")

    # ── Bluetooth ────────────────────────────────────────────────────────────
    if any(x in low for x in ["bluetooth durum", "bt durum"]):
        return ("get_bluetooth_status", "")
    if any(x in low for x in ["bluetooth aç", "bt aç"]):
        return ("enable_bluetooth", "")
    if any(x in low for x in ["bluetooth kapat", "bt kapat"]):
        return ("disable_bluetooth", "")
    if any(x in low for x in ["bluetooth cihaz", "bt cihaz", "eşleşmiş cihaz"]):
        return ("list_bluetooth_devices", "")

    # ── Saat / tarih ────────────────────────────────────────────────────────
    if any(x in low for x in ["saat kaç", "saat kac", "saat ne", "şu an saat",
                               "tarih ne", "bugün ne", "bugun ne", "günün tarihi",
                               "hangi gün", "hangi gun", "gün ne", "gun ne",
                               "tarih ve saat", "saat ve tarih"]) \
       or any(x in norm for x in ["saat kac", "saat ne", "tarih ne", "bugun ne",
                                   "hangi gun", "gun ne", "tarih ve saat"]):
        return ("get_time", "")

    # ── Sistem bilgisi ───────────────────────────────────────────────────────
    if any(x in low for x in ["sistem bilgisi", "sistem durumu", "cpu", "ram durumu", "bellek durumu"]):
        return ("system_info", "")
    if any(x in low for x in ["pil", "batarya", "şarj kaç", "şarj durumu"]):
        return ("battery_status", "")
    if any(x in low for x in ["disk bilgisi", "disk doluluk", "depolama", "ne kadar alan var", "ne kadar yer"]):
        return ("disk_info", "")
    if any(x in low for x in ["çalışan program", "işlemleri listele", "process listesi", "hangi programlar çalışıyor"]):
        return ("list_processes", "")

    # kill_process — uygulamayı zorla sonlandır
    m_kill = re.search(
        r"^(.+?)(?:'[uüiı]|\'[yi]|yi|yı|u|ü|i|ı)?\s+(?:zorla\s+)?(?:sonlandır|öldür|kill et|zorla kapat)$",
        low,
    )
    if m_kill:
        target = m_kill.group(1).strip()
        _no_kill = {"bilgisayar", "pc", "ekran", "monitör", "wifi", "bluetooth", "uygulamayı", "süreci"}
        if target and target not in _no_kill:
            return ("kill_process", target)

    # ── Pano & çözünürlük ────────────────────────────────────────────────────
    if any(x in low for x in ["panoyu göster", "panoda ne var", "kopyalanan neydi", "clipboard"]):
        return ("get_clipboard", "")
    if any(x in low for x in ["ekran çözünürlüğü", "çözünürlük nedir", "resolution"]):
        return ("get_resolution", "")
    if any(x in low for x in ["monitörü kapat", "ekranı söndür", "ekranı kapat"]):
        return ("turn_off_monitor", "")

    # ── Windows ayarları ────────────────────────────────────────────────────
    m_settings = re.search(
        r"(bluetooth|wifi|ses|ekran|güvenlik|güncelleme|uygulama|ağ|klavye|fare|kamera|bildirim|gizlilik|tema|pil|dil)\s+ayar",
        low,
    )
    if m_settings:
        return ("open_windows_settings", m_settings.group(1))
    if re.search(r"windows ayarlar|ayarları aç\s*$", low):
        return ("open_windows_settings", "")

    # ── Başlangıç ────────────────────────────────────────────────────────────
    if any(x in low for x in ["başlangıç durumu", "startup durumu", "klargus başlangıçta mı"]):
        return ("system_startup_status", "")
    if any(x in low for x in ["başlangıca ekle", "otomatik başlat", "startup'a ekle"]):
        return ("enable_startup", "")
    if any(x in low for x in ["başlangıçtan kaldır", "otomatik başlatmayı kapat", "startup'tan kaldır"]):
        return ("disable_startup", "")

    # ── Oyun ────────────────────────────────────────────────────────────────
    if any(x in low for x in ["oyunu başlat", "steam'den aç", "oyunu aç"]):
        return None
    if "rastgele müzik" in low or "favori şarkı çal" in low:
        return ("random_music", "")

    # ── "X aç" / "X ac" genel program açma ──────────────────────────────────
    # Ses/ekran/WiFi gibi sistem sözcükleri çakışmasın diye önce kontrol et
    _NOT_PROGRAM = {
        "ses", "sesi", "ekran", "ekranı", "wifi", "wi-fi", "bluetooth",
        "monitor", "monitör", "pencere", "pencereyi", "uygulamayı",
        "gece", "gece ışığı", "karanlık", "karanlik", "night",
        # Soru kelimeleri — "neden aç / nasıl aç" gibi ifadeler program açma değil
        "neden", "niye", "nasıl", "ne", "nerede", "nereye", "kim", "hangi", "kaç",
        # Zamirler — "onu aç / bunu aç" belirsiz, LLM'e bırak
        "onu", "bunu", "şunu", "beni", "seni",
    }
    # "X aç" veya "X ac" — tek kelime veya uygulama adı + aç
    m_open = re.match(r"^(.+?)\s+(?:aç|ac)$", low)
    if m_open:
        target = m_open.group(1).strip()
        if target and target not in _NOT_PROGRAM and len(target) >= 2:
            return ("open_program", target)

    # "X'ı aç / X'i aç / X'u aç" — iyelik eki ile program açma
    m_open2 = re.match(r"^(.+?)(?:'[uüiı]|'[yi]|ı|i|u|ü)?\s+(?:aç|ac)$", low)
    if m_open2:
        target = m_open2.group(1).strip().rstrip("'")
        if target and target not in _NOT_PROGRAM and len(target) >= 2:
            return ("open_program", target)

    # Tek kelime — bilinen uygulama adları (LLM olmadan hızlı aç)
    _KNOWN_APPS = {
        "brave", "chrome", "firefox", "edge", "opera",
        "discord", "spotify", "steam", "epic",
        "notepad", "not defteri", "hesap makinesi", "calculator",
        "terminal", "cmd", "powershell", "explorer", "dosya gezgini",
        "vscode", "code", "pycharm", "intellij",
        "whatsapp", "telegram", "signal",
        "vlc", "media player",
        "obs", "twitch", "youtube",
        "word", "excel", "powerpoint",
        "task manager", "görev yöneticisi",
    }
    if low in _KNOWN_APPS:
        return ("open_program", low)

    return None


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------
def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    match = re.search(r"\{.*?\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


# ---------------------------------------------------------------------------
# Ana çözümleyici
# ---------------------------------------------------------------------------
def resolve_intent(user_text: str, memory_text: str = "") -> tuple:
    """(action, arg) döndürür."""
    text = (user_text or "").strip()
    if not text:
        return ("chat", "")

    # 1) Hızlı kural eşleşmesi — en hızlı yol
    fast = _fast_precheck(text)
    if fast:
        return fast

    # 2) Öğrenilmiş komut
    try:
        from tools.basic_tools import _load_learned, normalize
        if normalize(text) in _load_learned():
            return ("run_learned_command", text)
    except Exception:
        pass

    # 3) Niyet önbelleği — aynı metin tekrar gelirse LLM yok
    cache_key = text.lower()[:80]
    now = time.monotonic()
    with _intent_cache_lock:
        cached = _intent_cache.get(cache_key)
        if cached and (now - cached[0]) < _INTENT_CACHE_TTL:
            return (cached[1], cached[2])

    # 4) LLM — compact prompt, düşük num_predict (sadece JSON gerekli)
    prompt = _SYSTEM + f'\n\nKomut: "{text}"\nJSON:'

    try:
        from brain.llm import call_ollama
        raw = call_ollama(prompt, retries=1, timeout=14, num_predict=80)
        parsed = _parse_json_response(raw)
        if not parsed:
            return ("chat", text)
        action = str(parsed.get("action", "chat")).strip()
        arg = str(parsed.get("arg", text)).strip()
        confidence = float(parsed.get("confidence", 0.5))
        if action not in KNOWN_ACTIONS or confidence < 0.4:
            return ("chat", text)
        # Önbelleğe al
        with _intent_cache_lock:
            _intent_cache[cache_key] = (now, action, arg)
            if len(_intent_cache) > 100:
                oldest = min(_intent_cache, key=lambda k: _intent_cache[k][0])
                _intent_cache.pop(oldest, None)
        return (action, arg)
    except Exception:
        return ("chat", text)


def resolve_intent_with_details(user_text: str, memory_text: str = "") -> dict:
    """Tam detaylı sonuç — reasoning ve confidence dahil."""
    text = (user_text or "").strip()
    if not text:
        return {"action": "chat", "arg": "", "confidence": 1.0,
                "reasoning": "Boş giriş", "source": "precheck"}

    fast = _fast_precheck(text)
    if fast:
        return {"action": fast[0], "arg": fast[1], "confidence": 1.0,
                "reasoning": "Hızlı kural eşleşmesi", "source": "precheck"}

    try:
        from tools.basic_tools import _load_learned, normalize
        if normalize(text) in _load_learned():
            return {"action": "run_learned_command", "arg": text,
                    "confidence": 1.0, "reasoning": "Öğrenilmiş komut", "source": "learned"}
    except Exception:
        pass

    memory_hint = ""
    if memory_text:
        lines = [l for l in memory_text.splitlines() if l.strip()][:6]
        memory_hint = "\nKullanıcı bağlamı (kısa):\n" + "\n".join(lines)

    prompt = _SYSTEM + memory_hint + f'\n\nKullanıcı komutu: "{text}"\nJSON:'

    try:
        from brain.llm import call_ollama
        raw = call_ollama(prompt, retries=2, timeout=25)
        parsed = _parse_json_response(raw)
        if not parsed:
            return {"action": "chat", "arg": text, "confidence": 0.5,
                    "reasoning": "JSON parse hatası", "source": "llm_fallback"}
        action = str(parsed.get("action", "chat")).strip()
        arg = str(parsed.get("arg", text)).strip()
        confidence = float(parsed.get("confidence", 0.5))
        reasoning = str(parsed.get("reasoning", "")).strip()
        if action not in KNOWN_ACTIONS or confidence < 0.4:
            return {"action": "chat", "arg": text, "confidence": confidence,
                    "reasoning": f"Düşük güven / bilinmeyen araç: {action}", "source": "llm_fallback"}
        return {"action": action, "arg": arg, "confidence": confidence,
                "reasoning": reasoning, "source": "llm"}
    except Exception as e:
        return {"action": "chat", "arg": text, "confidence": 0.0,
                "reasoning": f"LLM hatası: {e}", "source": "error"}
