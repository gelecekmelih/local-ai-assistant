# -*- coding: utf-8 -*-
"""
brain/sovereign_brain.py — SovereignBrain (Hızlandırılmış)
============================================================
Optimizasyonlar:
  - _fast_precheck önce çalışır → LLM'e gitmeyen komutlar anında biter
  - Hafıza 30s önbelleklenir → her komutta I/O yok
  - LLM timeout 45s → 18s, num_predict 512 → 128
  - Araç sonrası follow-up LLM sadece sohbet/ekran gibi durumlarda
  - system_settings cached (system_settings.py'de)
"""

import json
import re
import threading
import time
from typing import Optional

from brain.system_settings import get_system_setting
# requests — ilk LLM çağrısında lazy yüklenir
from brain.llm import (
    call_ollama,
    get_model_name,
    KLARGUS_TOOLS,
    SYSTEM_PROMPT,
    _url,
)
from brain.tool_registry import run_tool
from brain.tool_result import ToolResult

# ── Sabitler ─────────────────────────────────────────────────────────────────

# Ayarlanabilir sabitler config.py'de toplandı (değerler birebir aynı).
from config import (
    MAX_HISTORY, MEMORY_CACHE_TTL,
    LLM_FIRST_STEP_TIMEOUT, LLM_TOOL_TIMEOUT, LLM_FOLLOWUP_TIMEOUT,
    MAX_HEAL_DEPTH, MAX_AGENT_STEPS, ACTION_LOG_MAX,
    SYS_PROMPT_TTL, MEMORY_SAVE_THROTTLE, FACT_EXTRACT_THROTTLE,
    PROFILE_TTL, BRAINPAD_TTL, MEMORY_Q_MAX, BG_IDLE_DELAY,
)
from core.logger import get_logger

log = get_logger("brain")

import os as _os


def _test_mode() -> bool:
    """Test/güvenli mod açık mı? (KLARGUS_TEST_MODE=1). Açıkken beyin GERÇEK DÜNYA
    yan etkisi olan araçları çalıştırmaz ve kamerayı açmaz — testler yan etkisiz olur.
    Üretimde (env yok) hiçbir etkisi yoktur. Tek kaynak: core.safe_mode.is_safe_mode."""
    from core.safe_mode import is_safe_mode
    return is_safe_mode()


# Ollama /api/chat options — TÜM agent çağrılarında sabit (runner reload'u önler).
# Tek kaynak: _call_llm_step + _followup_llm + trace buradan.
# num_ctx TEK KAYNAK: brain.llm.OLLAMA_NUM_CTX (iki dosyada iki ayrı "8192" tutulmaz).
# Böylece agent /api/chat ile call_ollama/stream_ollama/ask_with_tools AYNI ctx'i
# kullanır → Ollama runner reload thrash'i (4096↔8192) ortadan kalkar.
# (Tarihçe: num_ctx 4096 iken prompt 2050'ye kırpılıyordu; 8192 → giriş limiti 7424.)
from brain.llm import OLLAMA_NUM_CTX as _LLM_NUM_CTX
_LLM_NUM_PREDICT = 768


# Test modunda AGENT DÖNGÜSÜNDEN çalıştırılmayan yan-etkili araçlar (tarayıcı /
# program açma / klavye-fare / sistem değiştirme / güç / ağ yazma). Doğrudan
# run_tool çağrıları (birim/karakterizasyon testleri) bundan ETKİLENMEZ.
_SIDE_EFFECT_TOOLS = frozenset({
    "open_program", "open_website", "open_folder", "open_file", "launch_game",
    "google_search", "youtube_search", "spotify_search", "spotify_liked_songs",
    "random_music", "web_search",
    "type_text", "press_key", "hotkey", "scroll_up", "scroll_down",
    "click_center", "double_click", "right_click", "find_and_click", "find_and_double_click",
    "take_screenshot", "set_clipboard",
    "set_volume", "mute_volume", "unmute_volume", "set_brightness", "turn_off_monitor",
    "enable_dark_mode", "enable_light_mode", "enable_night_light", "disable_night_light",
    "sleep_pc", "lock_pc", "shutdown_pc", "restart_pc", "cancel_shutdown",
    "enable_wifi", "disable_wifi", "enable_bluetooth", "disable_bluetooth",
    "kill_process", "run_terminal", "run_shell", "open_windows_settings",
    "enable_startup", "disable_startup",
    "run_kali", "run_kali_bg", "kali_run", "kali_nmap", "kali_terminal",
    "wsl_run", "run_wsl_command", "nmap_scan", "nikto_scan", "sqlmap_test", "gobuster_dir",
})

# NOT: Eski string-eşleştirmeli hata-sinyali listesi KALDIRILDI.
# Hata tespiti artık YALNIZCA ToolResult.ok üzerinden (Görev 8 kapandı).
# Projede hiçbir yerde string-tabanlı hata tespiti kalmadı (normalizer tip-tabanlı).

# Araç çalışınca follow-up LLM istemeyenler (sonucu direkt döndür)
_SILENT_TOOLS = {
    "open_program", "open_website", "open_folder", "open_file",
    "google_search", "youtube_search", "spotify_search", "random_music",
    "type_text", "press_key", "hotkey", "scroll_up", "scroll_down",
    "double_click", "right_click", "click_center",
    "find_and_click", "find_and_double_click",
    # screen_read kendi içinde OCR'ı LLM ile yorumluyor → tam cevap döndürür.
    # Sessiz yaparak agent döngüsünün GEREKSİZ ikinci yorumlamasını (follow-up
    # LLM) atlıyoruz → "ekranda ne var" ~1.5s hızlanır, çift yorumlama biter.
    "screen_read",
    "active_window", "list_windows", "focus_window",
    "close_window", "close_active_window", "minimize_window", "maximize_window",
    "switch_window", "mute_volume", "unmute_volume", "set_volume",
    "set_brightness", "enable_dark_mode", "enable_light_mode",
    "enable_night_light", "disable_night_light",
    "sleep_pc", "lock_pc", "shutdown_pc", "restart_pc", "cancel_shutdown",
    "enable_wifi", "disable_wifi", "enable_bluetooth", "disable_bluetooth",
    "turn_off_monitor", "enable_startup", "disable_startup",
    "kill_process", "set_clipboard",
}

# Bu araçlar çalışınca döngü DEVAM EDER (sessiz + zincirleme)
_CHAIN_CONTINUE = {
    "wait_seconds", "check_screen_for", "verify_goal",
}

_OWNERSHIP_BLOCK = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SAHİP: {owner_name}
Sen Klargus'sun — {owner_name}'in JARVIS'i.
{owner_name}'in tek eğitmeni ve sahibisin.
Sıradan komutlarda (PC kontrolü, dosya/sistem/ağ araçları) sızlanma, gereksiz onay
isteme — direkt yap. Ama bu, gerçek zarar potansiyeli olan isteklerde (kendine/
başkasına zarar, yetkisiz sistemlere saldırı, büyük ölçekli yıkıcı eylemler vb.)
muhakemeyi devre dışı bırakmaz — böyle bir şey görürsen sorgula ya da reddet.
Faydalı gördüğün öneri/gözlemi ekleyebilirsin — ama önce komutu yerine getir.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# Gözlem/öneri inisiyatifi (uydu asistan Katman 1). İtaat DEĞİL — öneri.
# Frekans freni bilinçli olarak EN BAŞTA (negatif çıpa): qwen2.5:7b "sunabilirsin"i
# "her zaman öner" diye aşırı-yorumlamasın. Denge öneri-yok tarafına yaslı.
_INITIATIVE_BLOCK = """\
GÖZLEM/ÖNERİ:
Çoğu cevapta öneriye GEREK YOKTUR — sadece gerçekten değerli bir şey fark edersen ekle.
Patron'a faydalı olacağını düşündüğün bir şey görürsen (bir sorun, daha iyi bir yol, gözden
kaçan bir ayrıntı) bunu kısaca söyleyebilirsin. Ama:
- Her cevaba öneri sıkıştırma; az ama isabetli olsun.
- Önce isteneni yap; öneriyi AYRICA, sonradan ekle. Öneri, komutu yapmamak için bahane değildir.
- Öneri sunmak = SÖYLEMEK, yapmak değil. "İstersen şunu da yapabilirim" de; onay vermeden
  kendiliğinden yapma."""

_TOOL_AUTHORITY_BLOCK = """ARAÇ: Direkt çalıştır, izin isteme. Sonucu kısa Türkçe özetle."""

_MEMORY_DECIDE_PROMPT = """Konuşmada hatırlamaya değer bir bilgi/tercih/kural var mı?
Varsa: {{"save": true, "text": "..."}}
Yoksa: {{"save": false}}
Sadece JSON:
Kullanıcı: {user}
Klargus: {assistant}"""

_HEAL_PROMPT = """\
'{action}' aracı başarısız oldu.
Hata: {error}
Kullanıcının isteği: {user_text}

Bu hatayı düzeltmek için farklı bir araç veya yaklaşım kullan.
Aynı başarısız aracı ({action}) tekrar deneme — alternatif bul.
Eğer gerçekten çözüm yoksa, kullanıcıya neden çözemediğini açıkla.\
"""


# ── Akıllı tool seçici ────────────────────────────────────────────────────────
# 57 tool şeması her çağrıda gönderilince ~8,500 ekstra token oluyor.
# Bağlama göre 15-20 tool seçerek ~4s kazanılıyor.

_CORE_TOOLS = {
    "open_program", "open_website", "open_folder", "open_file",
    "google_search", "youtube_search", "spotify_search", "random_music",
    "spotify_liked_songs", "web_search",
    "type_text", "press_key", "hotkey",
    "scroll_up", "scroll_down", "right_click", "double_click",
    "screen_read", "find_and_click",
    "get_volume", "set_volume", "mute_volume", "unmute_volume",
    "get_time", "system_info", "battery_status",
    # Sık kullanılan ağ/IP araçları — core'da olması mantıklı
    "ip_info", "reverse_dns", "dns_lookup", "traceroute",
    "wsl_run", "wsl_check_status",
    "hash_calculate", "hash_identify",
    "chat",
}

_WINDOW_TOOLS = {
    "active_window", "list_windows", "focus_window",
    "close_window", "close_active_window",
    "minimize_window", "maximize_window", "switch_window",
}

_POWER_TOOLS = {
    "sleep_pc", "lock_pc", "shutdown_pc", "restart_pc", "cancel_shutdown",
    "set_brightness", "enable_dark_mode", "enable_light_mode",
    "enable_night_light", "disable_night_light", "turn_off_monitor",
}

_NET_TOOLS = {
    "get_wifi_status", "list_wifi_networks", "connect_wifi",
    "disconnect_wifi", "enable_wifi", "disable_wifi",
    "get_bluetooth_status", "enable_bluetooth", "disable_bluetooth",
}

_CYBER_TOOLS = {
    "dns_lookup", "whois_lookup", "subdomain_enum", "ip_info", "reverse_dns",
    "nmap_scan", "traceroute", "http_analysis", "ssl_check", "robots_txt",
    "gobuster_dir", "hash_identify", "hash_calculate", "encode_decode",
    "password_strength", "nikto_scan", "sqlmap_test", "hash_crack",
}

_WSL_TOOLS = {
    "wsl_run", "wsl_check_status", "wsl_tool_check",
    "kali_status", "kali_terminal", "kali_run", "kali_results",
    "kali_nmap", "kali_nmap_quick",
}

# Güvenlik (içe dönük savunma) — _CYBER_TOOLS'tan AYRI: security_check salt-okuma
# yerel sistem taraması (süreç/ağ şüpheli tespiti), _CYBER_TOOLS ise dışa dönük
# saldırı/keşif araçlarıdır. Tetikleyici kelimeler de farklı olduğu için ayrı grup.
_SECURITY_TOOLS = {
    "security_check",
}

_SELF_TOOLS = {
    "self_improve", "self_analyze", "self_repair", "generate_tool",
    "plan_and_execute",
}

_CLIPBOARD_TOOLS = {
    "get_clipboard", "set_clipboard", "get_resolution",
    "list_processes", "kill_process", "run_terminal", "run_shell",
    "delete_file",
}

# Temel dosya araçları (tools/file_tools.py) — dosya-ilgili sorgularda görünür.
_FILE_TOOLS = {
    "create_file", "read_file", "append_file", "list_directory",
    "delete_file", "file_info", "create_directory",
    "open_file", "open_folder",
}


def _select_tools(user_text: str, all_tools: list) -> list:
    """
    Kullanıcı metnine göre ilgili tool grubunu seç.
    57 → 15-25 tool ile ~4s LLM tasarrufu sağlanır.
    """
    low = user_text.lower()

    selected = set(_CORE_TOOLS)  # her zaman çekirdek araçlar

    # Pencere yönetimi
    # "kapat" da eklendi: "Discord'u kapat" gibi komutlarda close_window gerekli
    if any(x in low for x in ["pencere", "window", "ekran", "uygulama", "kapat"]):
        selected |= _WINDOW_TOOLS

    # Güç / görüntü — "kapat" güç araçlarını da tetikler (bilgisayarı kapat vb.)
    if any(x in low for x in ["kapat", "uyut", "kilitle", "parlaklık", "karanlık", "gece"]):
        selected |= _POWER_TOOLS

    # Ağ
    if any(x in low for x in ["wifi", "wi-fi", "ağ", "bluetooth", "internet"]):
        selected |= _NET_TOOLS

    # Cyber / güvenlik (keyword + IP adresi pattern)
    _has_ip = bool(re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", user_text))
    if _has_ip or any(x in low for x in ["dns", "whois", "nmap", "ssl", "http", "hash",
                                          "encode", "decode", "şifre", "zafiyet", "port",
                                          "ip", "domain", "subdomain", "robots", "nikto",
                                          "sqlmap", "traceroute", "gobuster"]):
        selected |= _CYBER_TOOLS

    # Güvenlik (içe dönük savunma) — cyber'dan ayrı: yerel sistem güvenlik taraması
    if any(x in low for x in ["güvenlik", "guvenlik", "şüpheli", "supheli", "virüs", "virus",
                              "tehdit", "zararlı", "zararli", "malware", "trojan",
                              "process güvenli", "sistem güvenli", "hacklen", "saldırıya uğra",
                              "çalışan process", "calisan process"]):
        selected |= _SECURITY_TOOLS

    # WSL / Kali
    if any(x in low for x in ["wsl", "kali", "linux", "bash"]):
        selected |= _WSL_TOOLS

    # Öz-geliştirme
    if any(x in low for x in ["kendini", "geliştir", "analiz", "onar", "araç üret"]):
        selected |= _SELF_TOOLS

    # Sistem / süreç / dosya
    if any(x in low for x in ["süreç", "process", "dosya", "terminal", "clipboard",
                                "çözünürlük", "sil", "pano"]):
        selected |= _CLIPBOARD_TOOLS

    # Temel dosya işlemleri (oluştur/oku/yaz/listele/klasör...) → dosya araçları görünür
    if any(x in low for x in ["dosya", "klasör", "klasor", "oluştur", "olustur",
                                "oku", "yaz", "listele", "sil", "ekle", "içerik", "icerik"]):
        selected |= _FILE_TOOLS

    # Seçilen tool nesnelerini filtrele
    result = [
        t for t in all_tools
        if t.get("function", {}).get("name") in selected
    ]

    # Minimum 10 tool garanti (çok az seçildiyse core + window ekle)
    if len(result) < 10:
        extra = _WINDOW_TOOLS | _CLIPBOARD_TOOLS
        result = [
            t for t in all_tools
            if t.get("function", {}).get("name") in (selected | extra)
        ]

    return result


# ── SovereignBrain ────────────────────────────────────────────────────────────

def _format_factual(action: str, res) -> str:
    """Faktüel araç sonucunu kullanıcıya TÜRKÇE cümle olarak sun (ham dict repr DEĞİL).

    NEDEN: cyber_tools araçları (hash_calculate/password_strength/dns_lookup) dict
    döndürür; normalizer .message'a dict'in str()'ini koyuyor → kullanıcı ham Python
    dict görüyordu. Burada YALNIZ SUNUM düzeltilir: ToolResult sözleşmesi
    (ok/data/message/error) ve araçların dönüş tipleri DEĞİŞMEZ — doğrudan çağıranlar
    (ör. testler, suite) dict beklemeye devam eder.
    Modül seviyesinde (self kullanmıyor) → test/probe'da örneksiz çağrılabilir."""
    _d = res.data if isinstance(getattr(res, "data", None), dict) else {}
    if not getattr(res, "ok", True):
        _err = getattr(res, "error", None) or _d.get("error") or "bilinmeyen hata"
        _ad = {"dns_lookup": "DNS sorgusu", "hash_calculate": "Hash hesabı",
               "password_strength": "Şifre analizi"}.get(action, action)
        return f"{_ad} yapılamadı: {_err}"

    if action == "hash_calculate":
        return (f"{_d.get('text', '')} metninin "
                f"{str(_d.get('algorithm', '')).upper()} özeti: {_d.get('hash', '')}")

    if action == "password_strength":
        _tr = {"very_weak": "çok zayıf", "weak": "zayıf", "medium": "orta",
               "strong": "güçlü", "very_strong": "çok güçlü"}
        _s = _tr.get(str(_d.get("strength", "")), str(_d.get("strength", "")))
        _line = (f"Şifre gücü: {_s}, {_d.get('password_length', '?')} karakter, "
                 f"kırılma süresi tahmini {_d.get('crack_time_estimate', '?')}.")
        _notes = list(_d.get("warnings") or []) + list(_d.get("feedback") or [])
        if _notes:
            _line += " " + " ".join(str(n) for n in _notes[:2])
        return _line

    if action == "dns_lookup":
        _dom = _d.get("domain", "")
        # Kayıt tipi sırası SABİT — dict sırasına güvenme; en önemli (A/MX) önce
        # okunsun. Listede olmayan tipler atlanır; listede olmayıp veride olan
        # tipler (beklenmedik) sona eklenir.
        _order = ["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA"]
        _keys = [k for k in _order if isinstance(_d.get(k), list) and _d.get(k)]
        _keys += [k for k, v in _d.items()
                  if k not in _order and k != "domain"
                  and isinstance(v, list) and v]
        _rows = []
        for k in _keys:
            v = _d[k]
            # Sesli asistan için kayıt tipi başına en fazla 3 değer; fazlası
            # "(+N tane)" olarak özetlenir (hash/password kollarındaki kırpma deseni).
            _vals = ", ".join(str(x) for x in v[:3])
            if len(v) > 3:
                _vals += f" (+{len(v) - 3} tane)"
            _rows.append(f"{k}: {_vals}")
        if not _rows:
            return f"{_dom} için DNS kaydı bulunamadı."
        _out = f"{_dom} DNS kayıtları — " + " | ".join(_rows)
        # OS fallback devreye girdiyse yalnız A/AAAA gelir (MX/TXT/NS/SOA yok).
        # Kullanıcı eksik kayıtları "yok" sanmasın diye sonucun KISMİ olduğunu
        # kısa ve doğal Türkçeyle bildir. resolver alanı string → _rows'a girmez.
        if _d.get("resolver") == "os_fallback":
            _out += (". Not: Ağ DNS sunucusuna ulaşılamadı, sistem çözümleyicisine "
                     "düşüldü — bu yüzden yalnızca adres kayıtları var, sonuç kısmi.")
        return _out

    return str(res)


class SovereignBrain:
    def __init__(self):
        self._history: list[dict] = []
        self._lock = threading.Lock()
        self._owner: str = get_system_setting("owner_name", "Kullanıcı")
        # NOT (2026-10-03): SovereignBrain tekil (singleton, bkz. get_sovereign_brain())
        # ve think() birden fazla thread'den eşzamanlı çağrılabiliyor (örn. elle
        # yazılan komut ile ses uyandırma-kelimesi akışı aynı anda tetiklenirse).
        # self._tracer / self._trace_ended_by eskiden düz instance attribute'du —
        # bir thread'in izleme nesnesini/sonuç kodunu diğer çağrı ezebiliyordu
        # (iz dosyasına karışık veri yazılması, log_conversation(ok=...)'un yanlış
        # thread'in sonucunu okuması). threading.local() ile her çağıran thread
        # kendi yuvasını görür — bkz. aşağıdaki _tracer/_trace_ended_by property'leri.
        self._reason_local = threading.local()
        # Hafıza önbellekleri (katmanlı — _read_memory_cached'de yönetilir)
        self._profile_cache:    str   = ""
        self._profile_cache_at: float = 0.0
        self._brainpad_cache:    str   = ""
        self._brainpad_cache_at: float = 0.0
        self._memory_qcache:    dict  = {}   # {query_key: (ts, result)}
        # Eylem günlüğü — LLM her zaman ne yapıldığını görsün
        self._action_log: list[dict] = []   # {tool, arg, result, ok, ts}
        # Sistem promptu önbelleği — her çağrıda yeniden inşa etme
        self._sys_prompt_cache: str = ""
        self._sys_prompt_at: float = 0.0
        self._SYS_PROMPT_TTL: float = SYS_PROMPT_TTL  # config.py tek kaynak (60.0)
        # Hafıza LLM throttle — ard arda komutlarda GPU çakışmasını önle
        self._last_memory_save_at: float = 0.0
        self._MEMORY_SAVE_THROTTLE: float = MEMORY_SAVE_THROTTLE   # config.py (60.0)
        # user_fact çıkarımı ayrı throttle — daha sık çalışabilir, prompt çok kısa
        self._last_fact_extract_at: float = 0.0
        self._FACT_EXTRACT_THROTTLE: float = FACT_EXTRACT_THROTTLE  # config.py (20.0)
        # Foreground komut sayacı — arka plan LLM'leri GPU çakışmasını önlemek
        # için bu sayaç değişmediyse (kullanıcı sessizse) çalışır
        self._think_seq: int = 0

    # ── Thread-local iz durumu ───────────────────────────────────────────────
    # Bkz. __init__'teki not: tekil SovereignBrain nesnesi eşzamanlı thread'lerden
    # çağrılabildiği için _tracer ve _trace_ended_by instance attribute DEĞİL,
    # her çağıran thread'e özel property olarak tutulur. Davranış tek-thread'li
    # kullanımda (ki bu hala büyük çoğunluk durum) birebir aynıdır.
    @property
    def _tracer(self):
        return getattr(self._reason_local, "tracer", None)

    @_tracer.setter
    def _tracer(self, value):
        self._reason_local.tracer = value

    @property
    def _trace_ended_by(self):
        return getattr(self._reason_local, "ended_by", None)

    @_trace_ended_by.setter
    def _trace_ended_by(self, value):
        self._reason_local.ended_by = value

    def _with_scene(self, base_prompt: str) -> str:
        """Kamera desteği kaldırıldı (2026-09-29) — artık düz geçiş (no-op)."""
        return base_prompt

    # ── Ana API ───────────────────────────────────────────────────────────────

    def think(self, user_text: str) -> str:
        self._think_seq += 1   # arka plan LLM'leri foreground aktifken beklesin
        # 0a. Sadece "klargus/jarvis" çağrı kelimesini soy — Türkçe karakterlere dokunma
        # normalize_command_text STT içindir, Türkçe karakterleri ASCII'ye çevirir
        # ve _fast_precheck pattern'larını bozar — metin girişinde kullanılmaz
        stripped = re.sub(
            r'^(?:klargus|hey\s+klargus|ey\s+klargus|jarvis|hey\s+jarvis)\s*[,:]?\s*',
            '', user_text, flags=re.IGNORECASE,
        ).strip()
        if stripped:
            user_text = stripped
        elif user_text.strip():
            # Sadece çağrı kelimesi yazıldı
            return "Evet, buradayım. Ne yapmamı istersin?"

        # Boş komut koruması
        if not user_text.strip():
            return "Bir şey söylemek istedin mi?"

        # 0b. Sohbet oturumu yönetimi
        try:
            from brain.conversation_session import (
                is_chat_start_request, is_chat_end_request,
                chat_session_active, start_chat_session,
                end_chat_session, refresh_chat_session,
            )
            if is_chat_start_request(user_text):
                start_chat_session()
                return "Sohbet moduna geçtim. Ne konuşmak istersin?"
            if is_chat_end_request(user_text):
                end_chat_session()
                return "Sohbeti kapattım. Bir şeye ihtiyacın olursa çağır."
            if chat_session_active():
                refresh_chat_session()
        except Exception as e:
            log.debug("sohbet oturumu işlenemedi: %s", e)

        # 1. Eğitim/konfigürasyon komutları — LLM'e bile gitmez
        train_result = self._handle_training_command(user_text)
        if train_result is not None:
            return train_result

        # 2. Hızlı kural eşleştirici — LLM'e gitme
        # intent_engine ayarı:
        #   "hybrid"    → net komutlar kuralla (anlık), gerisi LLM
        #   "llm_first" → SADECE tek kelimelik tuş komutları kuralla (enter, tab,
        #                 kopyala vb.) — diğer HER ŞEYİ LLM düşünüp yapar
        # Çok adımlı görevler ("lol'a girip dereceli oyna") her iki modda da
        # hızlı yola GİRMEZ — kural sadece ilk adımı yapar, gerisi kaybolurdu.
        # 2.5 Algı sorgusu (ekran/kamera) — qwen2.5 bu araçları tutarsız çağırıyor
        # ("okuyacağım" deyip çağırmıyor). Doğrudan araca yönlendir → garanti çalışır.
        _perc = self._perception_route(user_text)
        if _perc is not None:
            self._append_history("user", user_text)
            self._append_history("assistant", _perc)
            self._log_conversation_async(user_text, _perc, action="perception",
                                          ok=bool(getattr(_perc, "ok", True)))
            return _perc

        # 2.6 Faktüel araç sorgusu (zaman/DNS/hash/şifre) — qwen2.5 çok-turlu konuşmada
        # bu araçları çağırmak yerine yanlış cevap üretiyor (ölçüldü). Doğrudan araca
        # yönlendir → garanti çalışır. Eşleşme yoksa None → akış değişmeden sürer.
        _fact = self._factual_tool_route(user_text)
        if _fact is not None:
            self._append_history("user", user_text)
            self._append_history("assistant", _fact)
            self._log_conversation_async(user_text, _fact, action="factual_route")
            return _fact

        _engine = str(get_system_setting("intent_engine", "hybrid") or "hybrid").lower()
        fast = None
        if _engine == "llm_first":
            if user_text.lower().strip() in self._STATELESS_COMMANDS:
                fast = self._fast_route(user_text)
        elif not self._looks_like_mission(user_text.lower()):
            fast = self._fast_route(user_text)
        if fast is not None:
            # Stateless sistem komutları (ctrl+c, enter vb.) geçmişe eklenmez —
            # LLM bağlamını anlamsız araç çağrılarıyla kirletmesin.
            _is_stateless = user_text.lower().strip() in self._STATELESS_COMMANDS
            if not _is_stateless:
                self._append_history("user", user_text)
                self._append_history("assistant", fast)
            self._log_conversation_async(user_text, fast, action="fast_route")
            return fast

        # 3. Hafıza oku (önbellekli)
        memory_ctx = self._read_memory_cached(user_text)

        # 4. LLM ile düşün
        response = self._reason(user_text, memory_ctx)

        # 5. Geçmişe ekle
        self._append_history("user", user_text)
        self._append_history("assistant", response)

        # 6. Arka planda: hafızaya yaz + konuşmayı logla
        threading.Thread(
            target=self._save_memory_async,
            args=(user_text, response),
            daemon=True,
        ).start()
        self._log_conversation_async(
            user_text, response, action="llm",
            ok=(self._trace_ended_by == "model_done"))

        return response

    # Eğitim/sistem komutlarını tetikleyen kelimeler — bunlar yoksa hızlı çık
    _TRAINING_TRIGGERS = frozenset([
        "eğitim", "egitim", "training", "dataset", "modelfile",
        "kendini", "öz", "oz", "self", "gelişim", "gelistim", "improve",
        "reflect", "araç", "arac", "generate", "tool",
        "hata raporu", "onar", "repair", "diagnose", "tanı",
        "plan", "kali", "wsl", "proje yapı", "dosya yapı", "mimari",
        "performans", "yetenek", "capability",
        "profil", "hafıza", "bellek", "fact",
        "sesim", "ses profil", "voice", "kaydet",
        "güvenlik", "guvenlik",
        "bundan sonra", "diye hitap et",   # tercih/kişiselleştirme komutları (_cmd_tercih)
    ])

    def _handle_training_command(self, text: str):
        """Eğitim, öz-geliştirme ve kod erişim komutlarını yakala."""
        low = text.lower().strip()

        # Hızlı çıkış — tetikleyici kelime yoksa 50 kontrolü atla
        if not any(w in low for w in self._TRAINING_TRIGGERS):
            return None

        for _handler in (self._cmd_tercih,
                         self._cmd_egitim_profil,
                         self._cmd_gelisim_arac,
                         self._cmd_onarim_ses_kali):
            _r = _handler(text, low)
            if _r is not None:
                return _r
        return None

    def _cmd_tercih(self, text: str, low: str):
        """Kişiselleştirme/tercih komutları ("bundan sonra bana X de / soğuk konuş /
        kısa cevap ver" vb.). Yakalarsa tercihi öğrenir + persona cache'ini sıfırlar +
        doğal onay döndürür. Eşleşme yoksa None → normal akış sürer. Döner: str | None."""
        try:
            from brain.personality import extract_preference_update, set_preference
        except Exception as e:
            log.debug("tercih modülü yüklenemedi: %s", e)
            return None

        pair = extract_preference_update(text)   # (key, value) | None
        if not pair:
            return None

        key, value = pair
        try:
            set_preference(key, value)
        except Exception as e:
            log.debug("tercih kaydedilemedi: %s", e)
            return None

        # Persona cache'ini sıfırla — yoksa eski üslup 60 sn devam eder (Halka 2).
        self.invalidate_memory_cache()

        # Tercihe göre doğal onay
        if key == "address_user_as":
            return f"Anlaşıldı, bundan sonra size '{value}' diye hitap edeceğim."
        if key == "response_length":
            return ("Bundan sonra daha kısa cevaplar vereceğim."
                    if value == "short"
                    else "Bundan sonra biraz daha detaylı cevaplar vereceğim.")
        if key == "warmth":
            return {
                "cool":     "Bundan sonra daha kontrollü, mesafeli bir üslup kullanacağım.",
                "warm":     "Bundan sonra biraz daha sıcak bir üslup kullanacağım.",
                "balanced": "Bundan sonra dengeli bir üslup kullanacağım.",
            }.get(value, "Üslubumu ayarladım.")
        if key == "analytic_mode":
            return ("Bundan sonra daha analitik davranacağım."
                    if value else "Bundan sonra daha sezgisel davranacağım.")
        if key == "use_formal_you":
            return ("Bundan sonra size 'siz' diye hitap edeceğim."
                    if value else "Bundan sonra sana 'sen' diye hitap edeceğim.")
        if key == "language":
            return ("Bundan sonra Türkçe konuşacağım."
                    if value == "tr" else "Bundan sonra İngilizce konuşacağım.")
        if key == "preferred_browser":
            return f"Tercih ettiğin tarayıcıyı '{value}' olarak not ettim."
        return "Tercihini kaydettim."

    def _cmd_egitim_profil(self, text: str, low: str):
        """Eğitim/dataset/modelfile/proje-yapısı ve kullanıcı profili komutları. Döner: str | None."""
        # ── Eğitim komutları ──────────────────────────────────────────────────
        try:
            from brain.training_manager import (
                build_finetune_dataset, generate_modelfile,
                training_status,
            )

            if any(x in low for x in ["eğitim durumu", "egitim durumu", "training status"]):
                return training_status()

            if any(x in low for x in ["eğitim dataseti oluştur", "egitim dataseti olustur",
                                       "dataset oluştur", "dataset olustur", "fine-tune dataseti"]):
                fmt = "chatml"
                if "alpaca" in low:
                    fmt = "alpaca"
                elif "sharegpt" in low:
                    fmt = "sharegpt"
                r = build_finetune_dataset(format=fmt)
                return r.get("message", str(r))

            if any(x in low for x in ["modelfile güncelle", "modelfile olustur", "modelfile oluştur",
                                       "ollama modeli oluştur", "ollama model olustur"]):
                owner = get_system_setting("owner_name", "Kullanıcı")
                model = get_system_setting("model_name", "klargus-qwen")
                r = generate_modelfile(base_model=model, owner_name=owner)
                return r.get("message", str(r))

            if any(x in low for x in ["proje yapısı", "proje yapisi", "dosya yapısı",
                                       "klasör yapısı", "proje ağacı"]):
                from tools.codebase_tools import get_project_structure
                return get_project_structure()

        except Exception as e:
            log.debug("eğitim/profil komutu işlenemedi: %s", e)

        # ── Kullanıcı profili ─────────────────────────────────────────────────
        if any(x in low for x in ["profilimi göster", "profilimi goster", "profilim nedir",
                                    "profil göster", "profil goster", "profilimi ver",
                                    "benim hakkımda", "benim hakkimda"]):
            try:
                from brain.profile_learning import get_profile_summary
                return get_profile_summary() or "Henüz profil bilgisi yok."
            except Exception as e:
                return f"Profil hatası: {e}"

        if any(x in low for x in ["profili özetle", "profili ozetle", "hafızayı özetle",
                                    "hafizayi ozetle", "user fact özetle", "profil sıkıştır"]):
            try:
                from brain.profile_learning import summarize_user_facts
                return summarize_user_facts()
            except Exception as e:
                return f"Özetleme hatası: {e}"

        if any(x in low for x in ["hafıza durumu", "bellek durumu", "profil durumu",
                                    "user fact sayısı", "kaç fact"]):
            try:
                from brain.memory_bank import memory_stats
                from brain.memory_bank import _init_chroma, _chroma_profile_coll
                base = memory_stats()
                if _init_chroma() and _chroma_profile_coll is not None:
                    pcount = _chroma_profile_coll.count()
                    base += f"\nKullanıcı profili: {pcount} fact"
                return base
            except Exception as e:
                return f"Hafıza durumu hatası: {e}"
        return None

    def _cmd_gelisim_arac(self, text: str, low: str):
        """Öz-gelişim, mimari analiz ve araç üretimi komutları. Döner: str | None."""
        # ── Öz-Gelişim ────────────────────────────────────────────────────────
        if any(x in low for x in ["kendini geliştir", "kendini gelistir", "öz-gelişim",
                                    "oz gelisim", "öz gelişim", "self improve",
                                    "performans skoru", "öz değerlendirme", "oz degerlendirme"]):
            # Otonom öğrenme brainpad/hafızaya yazar → self_modify_enabled guard'ı
            # (self_improve tool ile tutarlı). Güvenli modda yazmaya ulaşılmaz.
            from brain.tool_registry import _self_modify_allowed, _READ_ONLY_MSG
            if not _self_modify_allowed():
                return _READ_ONLY_MSG
            try:
                from brain.self_improvement import improve
                return improve()
            except Exception as e:
                return f"Öz-gelişim hatası: {e}"

        if any(x in low for x in ["son etkileşimleri değerlendir", "son etkilesimleri degerlendir",
                                    "etkileşim analizi", "etkilesim analizi", "reflect"]):
            try:
                from brain.self_improvement import reflect
                return reflect()
            except Exception as e:
                return f"Değerlendirme hatası: {e}"

        if any(x in low for x in ["başarısızlıklardan öğren", "basarisizliklardan ogren",
                                    "hatalardan öğren", "hatalardan ogren"]):
            # update_brainpad brainpad/hafızaya yazar → self_modify_enabled guard'ı
            # (self_improve tool ile tutarlı). Güvenli modda yazmaya ulaşılmaz.
            from brain.tool_registry import _self_modify_allowed, _READ_ONLY_MSG
            if not _self_modify_allowed():
                return _READ_ONLY_MSG
            try:
                from brain.self_improvement import learn_from_failures, update_brainpad
                rules = learn_from_failures()
                if rules:
                    result = update_brainpad(rules)
                    return f"{result}\nÖğrenilen kurallar:\n" + "\n".join(f"• {r}" for r in rules)
                return "Öğrenilecek başarısızlık bulunamadı."
            except Exception as e:
                return f"Öğrenme hatası: {e}"

        # ── Mimari İnceleme ───────────────────────────────────────────────────
        if any(x in low for x in ["kendini analiz et", "tam analiz", "full analiz",
                                    "self analiz", "full self analysis"]):
            try:
                from brain.introspection import full_self_analysis
                return full_self_analysis()
            except Exception as e:
                return f"Analiz hatası: {e}"

        if any(x in low for x in ["yetenek haritası", "yetenek haritasi", "araç haritası",
                                    "arac haritasi", "capability map", "yetenekler"]):
            try:
                from brain.introspection import capability_map
                return capability_map()
            except Exception as e:
                return f"Yetenek haritası hatası: {e}"

        if any(x in low for x in ["mimari", "mimari analiz", "architecture", "sistem katmanları",
                                    "sistem katmanlari"]):
            try:
                from brain.introspection import architecture_overview
                return architecture_overview()
            except Exception as e:
                return f"Mimari analiz hatası: {e}"

        if any(x in low for x in ["performans raporu", "performans istatistik",
                                    "başarı oranı", "basari orani"]):
            try:
                from brain.introspection import performance_report
                return performance_report()
            except Exception as e:
                return f"Performans raporu hatası: {e}"

        # ── Araç Üretimi ──────────────────────────────────────────────────────
        if any(x in low for x in ["yeni araç oluştur", "yeni arac olustur", "araç üret",
                                    "arac uret", "araç ekle", "arac ekle", "generate tool",
                                    "tool üret", "tool oluştur"]):
            # Açıklamayı komuttan ayır
            description = text.strip()
            for prefix in ["yeni araç oluştur", "yeni arac olustur", "araç üret",
                            "arac uret", "araç ekle", "arac ekle", "generate tool",
                            "tool üret", "tool oluştur"]:
                if prefix in low:
                    idx = low.index(prefix) + len(prefix)
                    description = text[idx:].strip(" :—-")
                    break
            if not description or len(description) < 5:
                return "Araç açıklamasını belirt. Örnek: 'Yeni araç oluştur: sistemin RAM kullanımını göster'"
            # self_modify_enabled guard'ını ATLAMADAN, tool_registry.generate_tool
            # sarmalayıcısı üzerinden çalıştır (2026-09-29) — tek doğruluk kaynağı.
            try:
                return str(run_tool("generate_tool", description))
            except Exception as e:
                return f"Araç üretim hatası: {e}"

        if any(x in low for x in ["üretilmiş araçlar", "uretilmis araclar",
                                    "üretilen araçlar", "generated tools", "dinamik araçlar"]):
            try:
                from brain.tool_generator import list_generated_tools
                return list_generated_tools()
            except Exception as e:
                return f"Liste hatası: {e}"
        return None

    def _cmd_onarim_ses_kali(self, text: str, low: str):
        """Hata onarımı, otonom plan, ses profili ve WSL/Kali komutları. Döner: str | None."""
        # ── Güvenlik (içe dönük savunma — salt-okuma) ─────────────────────────
        # Net komutlar LLM'e bırakılmadan deterministik çalışır (LLM-tool 5C esnek
        # niyet için, bu route net komut için garanti — iki katman çakışmaz).
        # Dış guard (güvenlik + kontrol/rapor/tarama) bare "güvenlik" false-pozitifini
        # eler. Spesifik kalıplar (son/detaylı/kısa) genel'den ÖNCE — gölgeleme olmasın.
        if any(w in low for w in ("güvenlik", "guvenlik")) and any(
            w in low for w in ("kontrol", "rapor", "tarama", "tara", "denetim")
        ):
            try:
                from security.security_monitor import (
                    run_security_check, get_last_security_report,
                )
                if any(x in low for x in ("son güvenlik", "son guvenlik",
                                          "raporunu göster", "raporunu goster",
                                          "raporu göster", "raporu goster",
                                          "son rapor")):
                    return get_last_security_report()
                if any(x in low for x in ("detaylı", "detayli", "detay",
                                          "tam rapor", "full")):
                    return run_security_check("detailed")
                if any(x in low for x in ("kısa", "kisa", "özet", "ozet", "kısaca")):
                    return run_security_check("short")
                return run_security_check("standard")
            except Exception as e:
                return f"Güvenlik kontrolü hatası: {e}"

        # ── Hata Onarımı ──────────────────────────────────────────────────────
        if any(x in low for x in ["hata raporu", "error raporu", "hangi araçlar hatalı",
                                    "hangi araclar hatali", "hatalı araçlar"]):
            try:
                from brain.self_repair import error_report
                return error_report()
            except Exception as e:
                return f"Hata raporu hatası: {e}"

        # self_modify_enabled guard'ını ATLAMADAN, tool_registry.self_repair
        # sarmalayıcısı üzerinden çalıştır (2026-09-29) — tek doğruluk kaynağı.
        if any(x in low for x in ["kendini onar", "kendini düzelt", "kendini duzelt",
                                    "self repair", "otomatik onar", "auto repair"]):
            try:
                return str(run_tool("self_repair", ""))
            except Exception as e:
                return f"Onarım hatası: {e}"

        # "[araç adı]'ı düzelt" / "[araç adı] aracını düzelt"
        for suffix in ["'ı düzelt", "'i düzelt", "'u düzelt", "'ü düzelt",
                        " düzelt", " duzelt", " aracını düzelt", " aracini duzelt"]:
            if suffix in low:
                tool_name = low[:low.index(suffix)].strip().split()[-1]
                try:
                    return str(run_tool("self_repair", tool_name))
                except Exception as e:
                    return f"Onarım hatası: {e}"

        # "[araç]'ı diagnose et"
        if any(x in low for x in ["tanı koy", "tani koy", "diagnose", "aracı incele", "araci incele"]):
            words = low.split()
            # Son kelime araç adı olabilir
            tool_candidate = words[-1] if words else ""
            try:
                from brain.self_repair import diagnose
                return diagnose(tool_candidate)
            except Exception as e:
                return f"Tanı hatası: {e}"

        # ── Otonom Planlama ───────────────────────────────────────────────────
        if any(x in low for x in ["aktif plan", "plan durumu", "plan status"]):
            try:
                from brain.autonomous_planner import get_active_plan
                return get_active_plan()
            except Exception as e:
                return f"Plan durumu hatası: {e}"

        if any(x in low for x in ["planı iptal", "plani iptal", "plan iptal",
                                    "cancel plan", "planı durdur"]):
            try:
                from brain.autonomous_planner import cancel_plan
                return cancel_plan()
            except Exception as e:
                return f"Plan iptal hatası: {e}"

        # ── Ses profili ───────────────────────────────────────────────────────
        if any(x in low for x in ["sesimi kaydet", "sesimi kayit et", "ses kaydı yap",
                                    "sesimi öğren", "ses profilimi oluştur",
                                    "sesi kaydet", "voice enroll", "ses kaydet"]):
            return "Ses kaydı için uygulamadaki 'Sesimi Kaydet' butonunu kullan veya 'sesimi teyit et' de."

        if any(x in low for x in ["ses profilim", "sesim kayıtlı mı", "sesimi tanıyor musun",
                                    "ses durumu", "voice status"]):
            try:
                from audio.voice_profile import voice_profile_status
                s = voice_profile_status()
                if s.get("ready"):
                    return (f"Sesini tanıyorum. {s.get('samples', 0)} örnek kayıtlı, "
                            f"kalite: {s.get('quality', '?')}.")
                return "Henüz sesin kayıtlı değil. Uygulamadan 'Sesimi Kaydet' butonuna bas."
            except Exception as e:
                return f"Ses profili hatası: {e}"

        if any(x in low for x in ["ses profilini sil", "sesimi sil", "voice profile sil"]):
            try:
                from audio.voice_profile import delete_voice_profile
                # Dönüşü onurlandır: silme dosya kilidi/izin yüzünden başarısız olursa
                # False döner — koşulsuz "silindi" deme (yanlış "yaptım" sinyali).
                if delete_voice_profile():
                    return "Ses profili silindi."
                log.warning("ses profili silinemedi (dosya kilitli veya izin sorunu)")
                return "Ses profili silinemedi (dosya kilitli veya izin sorunu olabilir)."
            except Exception as e:
                return f"Ses silme hatası: {e}"

        # ── WSL2 / Kali Linux ────────────────────────────────────────────────
        if any(x in low for x in ["kali durumu", "wsl durumu", "kali linux durumu"]):
            try:
                from tools.wsl_tools import tool_kali_status
                return tool_kali_status()
            except Exception as e:
                return f"Kali durum hatası: {e}"

        if any(x in low for x in ["kali terminal", "kali terminali aç", "wsl terminali"]):
            try:
                from tools.wsl_tools import tool_kali_terminal
                return tool_kali_terminal()
            except Exception as e:
                return f"Terminal açma hatası: {e}"

        if any(x in low for x in ["kali sonuçları", "kali sonuclari", "kali result", "tarama sonuçları"]):
            try:
                from tools.wsl_tools import tool_kali_results
                return tool_kali_results()
            except Exception as e:
                return f"Sonuç listesi hatası: {e}"

        if any(x in low for x in ["kali agent başlat", "kali agent baslat", "kali bridge"]):
            try:
                from tools.wsl_tools import tool_kali_agent_start
                return tool_kali_agent_start()
            except Exception as e:
                return f"Agent başlatma hatası: {e}"

        if any(x in low for x in ["kali araçları", "kali araclari", "hangi kali", "kali toollar"]):
            try:
                from tools.kali_tools import tool_kali_tools_list
                return tool_kali_tools_list()
            except Exception as e:
                return f"Araç listesi hatası: {e}"

        # "kali'de X çalıştır" / "kali de X yap"
        for prefix in ["kali'de ", "kalide ", "kali de ", "wsl'de ", "wslde "]:
            if prefix in low:
                idx = low.index(prefix) + len(prefix)
                kali_cmd = text[idx:].strip()
                # Araç eşleştirme
                if kali_cmd.startswith("nmap"):
                    try:
                        from tools.kali_tools import tool_kali_nmap
                        arg = kali_cmd[4:].strip()
                        return tool_kali_nmap(arg)
                    except Exception as e:
                        return f"Nmap hatası: {e}"
                try:
                    from tools.wsl_tools import tool_kali_run
                    return tool_kali_run(kali_cmd)
                except Exception as e:
                    return f"Kali komut hatası: {e}"
        return None


    def _log_conversation_async(self, user: str, assistant: str, action: str = "",
                                 ok: bool = True):
        """Konuşmayı arka planda logla — eğitim dataseti için.
        NOT (2026-10-03): ok parametresi eskiden hiçbir çağıran tarafından
        verilmiyordu, log_conversation() hep ok=True varsayılanıyla yazıyordu —
        self_improvement.py/self_repair.py'nin "başarısızlıklardan öğren" /
        "hata raporu" özellikleri bu yüzden hep "sorun yok" görüyordu (gerçek
        veri yoktu). action="llm" ve action="perception" yolları artık gerçek
        durumu iletiyor (bkz. çağrı yerleri)."""
        def _log():
            try:
                from brain.training_manager import log_conversation
                log_conversation(user, assistant, action=action, ok=ok)
            except Exception as e:
                log.debug("konuşma loglanamadı: %s", e)
        threading.Thread(target=_log, daemon=True).start()

    def clear_history(self):
        with self._lock:
            self._history.clear()

    def get_history(self) -> list[dict]:
        with self._lock:
            return list(self._history)

    # ── Hızlı kural yolu ─────────────────────────────────────────────────────

    def _fast_route(self, user_text: str) -> Optional[str]:
        """
        Kural tabanlı hızlı eşleştirici.
        Eşleşirse aracı çalıştırır ve sonucu döndürür (LLM yok).
        Eşleşmezse None → LLM devreye girer.
        """
        try:
            from brain.llm_intent import _fast_precheck
            match = _fast_precheck(user_text)
            if match:
                action, arg = match
                if action == "chat":
                    return None  # sohbet → LLM'e gönder
                result = run_tool(action, arg if arg else None)
                return str(result) if result is not None else None
        except Exception as e:
            log.warning("deterministik route aracı çalıştırılamadı, LLM'e düşülüyor: %s", e)
        return None

    # ── Hafıza (katmanlı önbellek) ────────────────────────────────────────────
    # Profil + brainpad: 2dk global cache (nadiren değişir)
    # Anılar: query bazlı 30s cache (her sorgu kendi alakalı anılarını getirir)

    _PROFILE_TTL: float    = PROFILE_TTL    # config.py tek kaynak (120.0)
    _BRAINPAD_TTL: float   = BRAINPAD_TTL   # config.py tek kaynak (120.0)

    # Query bazlı memory cache sabitleri
    _MEMORY_Q_TTL: float = MEMORY_CACHE_TTL   # config.py tek kaynak (değer: 30.0)
    _MEMORY_Q_MAX: int   = MEMORY_Q_MAX    # config.py tek kaynak (20, int)

    def _read_memory_cached(self, query: str) -> dict:
        now = time.monotonic()

        # 1. Profil — global 2dk cache
        if not self._profile_cache or (now - self._profile_cache_at) >= self._PROFILE_TTL:
            try:
                from brain.profile_learning import get_profile_summary
                self._profile_cache    = get_profile_summary() or ""
                self._profile_cache_at = now
            except Exception as e:
                log.debug("profil cache doldurulamadı: %s", e)

        # 2. Brainpad — global 2dk cache
        if not self._brainpad_cache or (now - self._brainpad_cache_at) >= self._BRAINPAD_TTL:
            try:
                from brain.profile_learning import get_brainpad_text
                self._brainpad_cache    = get_brainpad_text() or ""
                self._brainpad_cache_at = now
            except Exception as e:
                log.debug("brainpad cache doldurulamadı: %s", e)

        # 3. Anılar — query bazlı 30s cache
        # Kısa araç komutları ("spotify aç") anı gerektirmez — embedding HTTP
        # çağrısını (100ms-3s, Ollama GPU çakışması) tamamen atla.
        q_words = (query or "").split()
        if len(q_words) <= 3 and "?" not in (query or ""):
            return {
                "profile":  self._profile_cache,
                "memories": "",
                "brainpad": self._brainpad_cache,
            }

        qkey = (query or "")[:50].lower().strip()
        cached_mem = self._memory_qcache.get(qkey)
        if cached_mem and (now - cached_mem[0]) < self._MEMORY_Q_TTL:
            memories = cached_mem[1]
        else:
            memories = ""
            try:
                from brain.memory_bank import format_retrieved_memories
                memories = format_retrieved_memories(query, top_k=4) or ""
            except Exception as e:
                log.debug("hafıza getirme başarısız: %s", e)
            self._memory_qcache[qkey] = (now, memories)
            # Cache boyutunu sınırla
            if len(self._memory_qcache) > self._MEMORY_Q_MAX:
                oldest = min(self._memory_qcache, key=lambda k: self._memory_qcache[k][0])
                self._memory_qcache.pop(oldest, None)

        return {
            "profile":  self._profile_cache,
            "memories": memories,
            "brainpad": self._brainpad_cache,
        }

    def invalidate_memory_cache(self):
        """Hafıza değişince cache'i sıfırla (profil + anılar + sistem promptu)."""
        self._profile_cache    = ""
        self._profile_cache_at = 0.0
        self._brainpad_cache    = ""
        self._brainpad_cache_at = 0.0
        self._memory_qcache.clear()
        self._sys_prompt_cache = ""
        self._sys_prompt_at    = 0.0

    # ── LLM akışı ─────────────────────────────────────────────────────────────

    def _preference_block(self) -> str:
        """Öğrenilen tercihleri (get_preferences) persona talimatına çevirir — yalnızca
        VARSAYILANDAN FARKLI olanları (kullanıcı hiç ayar yapmadıysa prompt şişmez).
        Çeviri mantığı f3ad122^ build_persona_system_prompt'tan taşındı (Halka 2)."""
        try:
            from brain.personality import get_preferences, DEFAULT_PREFERENCES
            prefs = get_preferences()
        except Exception as e:
            log.debug("tercih bloğu üretilemedi: %s", e)
            return ""

        def _changed(key):
            return prefs.get(key) != DEFAULT_PREFERENCES.get(key)

        lines = []
        if _changed("response_length"):
            rl = prefs.get("response_length")
            if rl == "short":
                lines.append("Varsayılan olarak kısa cevap ver.")
            elif rl == "medium":
                lines.append("Varsayılan olarak orta uzunlukta cevap ver.")
        if _changed("warmth"):
            lines.append({
                "cool": "Üslubun biraz daha mesafeli ve kontrollü olsun, ama itici olma.",
                "warm": "Üslubun biraz daha sıcak olsun, ama profesyonelliği koru.",
            }.get(prefs.get("warmth"),
                  "Üslubun dengeli olsun; ne fazla soğuk ne fazla samimi."))
        if _changed("analytic_mode"):
            lines.append("Teknik meselelerde analitik davran, neden-sonuç ilişkisi kur."
                         if prefs.get("analytic_mode")
                         else "Aşırı teknik analize girme; sezgisel ve pratik ol.")
        if _changed("use_formal_you"):
            lines.append("Kullanıcıya 'siz' dilini kullan." if prefs.get("use_formal_you")
                         else "Kullanıcıya 'sen' dilini kullan.")
        if _changed("address_user_as"):
            addr = str(prefs.get("address_user_as") or "").strip()
            if addr:
                lines.append(f"Uygun yerlerde kullanıcıya '{addr}' diye hitap et.")
        if _changed("language"):
            lang = prefs.get("language")
            if lang == "en":
                lines.append("Varsayılan dil İngilizce.")
            elif lang == "tr":
                lines.append("Varsayılan dil Türkçe.")

        if not lines:
            return ""
        return "Kullanıcı tercihleri:\n" + "\n".join(lines)

    def _struggling_block(self) -> str:
        """Kullanıcının AÇIKÇA zorlandığı konuları 'temelden anlat' talimatına çevirir —
        yalnız struggling varsa (yoksa boş; token şişmez, _preference_block deseni).

        GÜVENLİ yön (asimetri): SADECE "daha dikkatli/temelden açıkla". 'Şunu biliyor,
        atla' YÖNÜ YOK — o riskli (yanlışsa atlama zararı), güçlü modele ertelendi. En
        kötü durum: fazla açıklama (zararsız)."""
        try:
            from brain.profile_learning import get_struggling_facts
            facts = get_struggling_facts()
        except Exception as e:
            log.debug("zorlanma bloğu üretilemedi: %s", e)
            return ""
        if not facts:
            return ""
        konular = "; ".join(facts)
        return (f"Patron şu konularda yeni/zorlanıyor: {konular}. "
                "Bu konular geçtiğinde temelden, sabırla ve terim yığmadan açıkla.")

    def _build_system_prompt(self, memory_ctx: dict) -> str:
        now = time.monotonic()
        # Profile + brainpad: statik → 60s cache. Memories: her sorgu farklı → her seferinde rebuild.
        # Cache sadece memories boşsa ya da profile/brainpad değişmediyse geçerli.
        _cur_mem = (memory_ctx.get("memories", "") or "").strip()
        if (
            self._sys_prompt_cache
            and (now - self._sys_prompt_at) < self._SYS_PROMPT_TTL
            and not _cur_mem   # sorguya özgü anı yoksa cache geçerli
        ):
            return self._with_scene(self._sys_prompt_cache)

        ownership = _OWNERSHIP_BLOCK.format(owner_name=self._owner)

        # Hafıza ve profil — sadece doluysa ekle, boş başlık gönderme
        profile  = (memory_ctx.get("profile",  "") or "").strip()[:300]
        memories = (memory_ctx.get("memories", "") or "").strip()
        brainpad = (memory_ctx.get("brainpad", "") or "").strip()[:200]

        parts = [SYSTEM_PROMPT, ownership]

        # Öğrenilen tercihler (kimlik → nasıl davran → kullanıcı kim sırası) — Halka 2.
        # Statik: _sys_prompt_cache'e dahil; tercih değişince invalidate_memory_cache sıfırlar.
        pref_block = self._preference_block()
        if pref_block:
            parts.append(pref_block)

        # Seviye-farkındalığı (GÜVENLİ yön): açıkça zorlandığı konularda temelden anlat.
        # struggling değişince extract → invalidate_memory_cache → _sys_prompt_cache sıfırlanır.
        struggling_block = self._struggling_block()
        if struggling_block:
            parts.append(struggling_block)

        if profile:
            parts.append(profile)          # "Kullanıcı hakkında bilinenler:" başlığı dahil

        if memories:
            # Profile varsa kısa tut; yoksa daha geniş bırak
            mem_block = memories[:200] if profile else memories[:400]
            parts.append(f"Anılar: {mem_block}")

        if brainpad:
            parts.append(f"Kalıcı notlar: {brainpad}")

        action_summary = self._action_log_summary()
        if action_summary:
            parts.append(action_summary)

        parts.append(_INITIATIVE_BLOCK)
        parts.append(_TOOL_AUTHORITY_BLOCK)
        result = "\n\n".join(parts)
        self._sys_prompt_cache = result   # statik kısım cache'lenir (sahne hariç)
        self._sys_prompt_at = now
        return self._with_scene(result)   # sahne özeti her çağrıda taze eklenir

    # Tek kelimelik kesin araç komutları — geçmiş olmadan çalışır
    _STATELESS_COMMANDS = frozenset([
        "enter", "tab", "escape", "esc", "backspace", "delete",
        "kopyala", "yapıştır", "yapistir", "kaydet", "geri al",
        "scroll up", "scroll down", "yukari kaydir", "asagi kaydir",
        "sag tikla", "cift tikla",
    ])

    def _build_messages(self, system: str, user_text: str) -> list[dict]:
        messages = [{"role": "system", "content": system}]
        with self._lock:
            history = self._history
            if not history:
                # Geçmiş yoksa sadece mevcut mesaj yeterli
                pass
            elif user_text.lower().strip() in self._STATELESS_COMMANDS:
                # Tek kelimelik sistem komutu — geçmişe gerek yok
                pass
            else:
                # Her türlü sohbet ve komut için son 8 mesajı gönder
                # (kısa mesajlar da bağlamı kaybetmesin)
                messages.extend(history[-8:])
        messages.append({"role": "user", "content": user_text})
        return messages

    # Sohbet sorusu sinyalleri — bu kelimeler varsa tool kullanılmaz, sohbet yanıtı verilir
    _CHAT_SIGNALS = frozenset([
        "nasılsın", "nasilsin", "ne haber", "ne yapıyorsun", "ne yapiyorsun",
        "ne düşünüyorsun", "ne dusunuyorsun", "ne zaman", "neden", "niye",
        "anlat", "anlatabiliyon", "anla", "açıkla", "açıklar mısın",
        "ne biliyorsun", "biliyor musun", "hakkında ne", "hakkinda ne",
        "ne dersin", "düşüncen ne", "dusuncen ne", "fikrin ne",
        "kim", "kimdir", "nedir", "nedir bu", "bu ne", "bu nedir",
        "nasıl çalışır", "nasil calisir", "nasıl yapılır", "nasil yapilir",
        "söyle", "soyle", "anlayamadım", "anlayamadim", "tekrar",
        "iyi misin", "iyisin", "yorgun musun", "mutlu musun",
        "ne öğrendin", "ne ogrendin", "neler yapabiliyorsun",
        "teşekkür", "tesekkur", "sağ ol", "sag ol", "bravo",
        "harika", "güzel", "mükemmel", "eyvallah",
    ])

    def _is_chat_query(self, low_text: str) -> bool:
        """Mesaj açıkça sohbet/soru niteliğinde mi? Tool gerektirmiyor mu?"""
        # Soru işareti → genellikle sohbet
        if "?" in low_text:
            return True
        # Sohbet sinyalleri
        if any(sig in low_text for sig in self._CHAT_SIGNALS):
            return True
        # Uzun cümle — ama araç komutları da uzun olabilir ("spotify'de pop şarkı çal" = 4 kelime)
        # Sadece açık sohbet fiilleriyle birlikte uzun cümle → sohbet
        _CHAT_VERBS = {"anlat", "açıkla", "söyle", "bildir", "yaz", "tahmin", "düşün",
                       "anlatır", "açıklar", "söyler", "neden", "niye", "nasıl"}
        words = low_text.split()
        if len(words) >= 7 and any(w in _CHAT_VERBS for w in words):
            return True
        return False

    # ── Görev modu (mission mode) ─────────────────────────────────────────────
    # "lol'a gir, dereceli tek çift oyuna gir" gibi çok adımlı UI görevleri:
    # LLM ekranı okuyarak, tıklayarak, doğrulayarak hedefe kendisi ulaşır.
    # Tek araçla biten kısa komutlardan ("spotify aç") farkı: araç çalışınca
    # döngü BİTMEZ, LLM ekran geri bildirimiyle bir sonraki adıma karar verir.

    # UI eylem fiilleri — görev modu tespiti için
    _MISSION_VERBS = frozenset([
        "gir", "tıkla", "tikla", "seç", "sec", "bas", "başlat", "baslat",
        "aç", "ac", "oyna", "katıl", "katil", "kur", "doldur", "yaz",
        "geç", "gec", "bul", "ara", "kaydır", "kaydir", "çal", "cal",
    ])

    # Görev modunda her UI eyleminden sonra ekran özeti LLM'e gösterilir
    _MISSION_UI_ACTIONS = frozenset([
        "open_program", "open_website", "find_and_click", "find_and_double_click",
        "click_center", "double_click", "right_click", "type_text", "press_key",
        "hotkey", "scroll_up", "scroll_down", "focus_window", "launch_game",
    ])

    _MISSION_PROMPT = (
        "GÖREV MODU: Kullanıcının hedefi birden çok adım gerektiriyor. "
        "Hedefe ulaşana kadar şu döngüyü uygula:\n"
        "1. Ekran durumuna bak (her araçtan sonra EKRAN özeti verilir).\n"
        "2. Hedefe götüren BİR SONRAKİ küçük adımı seç (find_and_click ile "
        "buton/menü adına tıkla, gerekirse type_text ile yaz).\n"
        "3. Uygulama/oyun yükleniyorsa wait_seconds ile bekle (oyun istemcileri "
        "10-20 saniye sürebilir).\n"
        "4. Adım işe yaramadıysa ekranda gördüğün alternatif öğeyi dene.\n"
        "Hedef tamamlanınca araç çağırmayı bırak ve tek cümleyle raporla. "
        "Hedefe ulaşmadan durma."
    )

    def _looks_like_mission(self, low_text: str) -> bool:
        """Çok adımlı UI görevi mi? 2+ eylem fiili veya eylem+bağlaç içeriyorsa evet."""
        words = low_text.replace(",", " ").split()
        if len(words) < 3:
            return False
        verb_count = sum(1 for w in words if w in self._MISSION_VERBS)
        if verb_count >= 2:
            return True
        # Tek fiil + sıralama ifadesi ("açıp ... gir", "sonra", "ardından")
        # Not: genel "-ıp" eki aranmaz ("grup", "tip" gibi kelimeler yanlış
        # tetiklerdi) — sadece yaygın zincirleme fiil formları listelenir.
        _chain_hints = (
            " sonra ", " ardından ", " ardindan ",
            "açıp ", "acip ", "girip ", "açarak ", "acarak ", "girerek ",
            "bulup ", "seçip ", "secip ", "başlatıp ", "baslatip ",
        )
        if verb_count >= 1 and any(c in low_text for c in _chain_hints):
            return True

        # "X'dan/X'den ... aç/çal/gir" kalıbı: kaynak → hedef navigasyonu.
        # "spotify'dan beğenilen şarkılardan birini aç" tek fiil içerir ama
        # doğası gereği çok adımlıdır (uygulamayı aç → içinde gez → başlat).
        if verb_count >= 1 and re.search(
            r"\b\w+['’]?[dt][ae]n\b.+\b(aç|ac|çal|cal|oyna|gir|başlat|baslat|seç|sec)\b",
            low_text,
        ):
            return True

        # Uygulama içi hedef isimleri: tek fiille de gelse içeride gezinme gerektirir
        _in_app_targets = (
            "beğenilen", "begenilen", "çalma listesi", "calma listesi", "playlist",
            "kitaplık", "kitaplik", "kütüphane", "kutuphane", "favori",
            "dereceli", "sıralamalı", "siralamali", "lobi",
            "ayarlar menüsü", "ayarlar menusu", "profil sayfası", "profil sayfasi",
        )
        if verb_count >= 1 and any(t in low_text for t in _in_app_targets):
            return True
        return False

    # Plan/yol haritası isteği tetikleyen kelimeler
    _PLAN_TRIGGERS = frozenset([
        "yol haritası", "yol haritasi", "roadmap", "plan çıkar", "plan cikar",
        "adımları söyle", "adimlari soyle", "nasıl yapabilirim", "nasil yapabilirim",
        "ne yapmalıyım", "ne yapmaliyim", "strateji", "aşamaları", "asamalari",
        "sırasıyla", "sirastyla", "önce ne", "once ne", "nereden başlamalıyım",
        "nereden baslayim", "plan yap", "yol harita",
    ])

    # Geçerli araç adları — metin-içi tool çağrısı doğrulaması için (bir kez hesapla)
    _VALID_TOOL_NAMES = frozenset(t["function"]["name"] for t in KLARGUS_TOOLS)

    # Ekran algı sorguları — doğrudan araca yönlendir (LLM güvenilmez)
    _SCREEN_VERBS = ("ne var", "ne görüyor", "oku", "ne yazıyor", "ne çıkıyor",
                     "göster", "nedir", "ne açık")

    def _perception_route(self, user_text: str):
        """Ekran sorgusunu doğrudan ilgili araca yönlendir. Yoksa None."""
        low = user_text.lower().strip()
        try:
            if "ekran" in low and any(p in low for p in self._SCREEN_VERBS):
                return run_tool("screen_read", user_text)
        except Exception:
            return None
        return None

    def _factual_tool_route(self, user_text: str) -> Optional[str]:
        """Faktüel araç sorgularını (zaman/DNS/hash/şifre) DOĞRUDAN ilgili araca
        yönlendir → LLM'e hiç gitmez. Eşleşme yoksa None (mevcut fast_route/LLM akışı
        DEĞİŞMEDEN sürer — bu yalnız bir EK güvenlik ağı).

        NEDEN (ölçülü): qwen2.5:7b bu 4 sınıfta çok-turlu konuşmada (≥2 önceki tur VEYA
        tek faktüel önceki tur) görünür aracı çağırmak yerine kendinden emin YANLIŞ cevap
        üretiyor (2026-07-15 tanı serisi: depth0/1 → %93-100 doğru araç çağrımı; depth≥2
        veya tek faktüel-önceki-tur → %0-7). Prompt/history mühendisliği (system prompt,
        tool-count azaltma, history tool_calls izi, content-marker — 4 yöntem) çözmedi;
        _perception_route deseniyle deterministik yönlendirme uygulanıyor.

        Regex'ler DAR — false positive (yanlış cümleyi yanlış araca yönlendirmek) false
        negative'den (LLM'e düşüp bazen doğru çalışmak) DAHA KÖTÜ."""
        import re
        low = user_text.lower().strip()
        try:
            # a) ZAMAN — "saat kaç" (ama "kaç saat/saattir" süre sorusu DEĞİL) / tarih
            if re.search(r"saat\s*ka[cç](?!\s*saat)|tarih\s*ne|hangi\s*tarih|"
                         r"hangi\s*g[üu]n|bug[üu]n.*tarih", low):
                return str(run_tool("get_time", ""))

            # b) DNS — "dns" + metinde bir domain varsa; domain yoksa LLM'e düşsün
            if "dns" in low:
                _m = re.search(r"\b([\w-]+\.[a-z]{2,})\b", low)
                if _m:
                    return _format_factual(
                        "dns_lookup", run_tool("dns_lookup", _m.group(1)))
                return None

            # c) HASH — hash/özet/md5.. + hesapla/bul/nedir + "X kelimesinin"
            if (any(k in low for k in ("hash", "özet", "ozet", "md5", "sha1", "sha256", "sha512"))
                    and any(k in low for k in ("hesapla", "hesapl", "bul", "nedir"))):
                _t = re.search(r"['\"]?(\w+)['\"]?\s*kelimesinin", user_text)
                if not _t:
                    return None
                _alg = ("sha256" if "sha256" in low else "sha1" if "sha1" in low
                        else "sha512" if "sha512" in low else "md5")
                return _format_factual(
                    "hash_calculate",
                    run_tool("hash_calculate", "",
                             _kw={"text": _t.group(1), "algorithm": _alg}))

            # d) ŞİFRE — şifre/parola + güvenlik/analiz/güçlü + "X şifresinin"
            if (any(k in low for k in ("şifre", "sifre", "parola"))
                    and any(k in low for k in ("güvenlik", "guvenlik", "analiz",
                                                "güçlü", "guclu", "güclü"))):
                _p = re.search(r"['\"]?(\S+?)['\"]?\s*"
                               r"(?:şifresinin|sifresinin|parolasının|parolasinin)", user_text)
                if not _p:
                    return None
                return _format_factual(
                    "password_strength",
                    run_tool("password_strength", "", _kw={"password": _p.group(1)}))
        except Exception:
            return None
        return None


    def _tool_call_from_text(self, content: str):
        """Model tool çağrısını CONTENT içinde JSON olarak yazdıysa yakala.
        Döner: (name, args_dict) veya None.
        Örn: '```json\\n{\"name\":\"get_time\",\"arguments\":{}}\\n```' → ('get_time', {})"""
        if not content or '"name"' not in content:
            return None
        text = content.replace("```json", "").replace("```", "")
        # Dengeli süslü parantezle ilk JSON objesini çıkar (iç içe arguments için)
        start = text.find("{")
        while start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        chunk = text[start:i + 1]
                        try:
                            obj = json.loads(chunk)
                        except Exception:
                            obj = None
                        if isinstance(obj, dict) and "name" in obj:
                            name = str(obj.get("name", "")).strip()
                            args = obj.get("arguments", obj.get("parameters", {})) or {}
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    args = {"arg": args}
                            if not isinstance(args, dict):
                                args = {}
                            if name in self._VALID_TOOL_NAMES:
                                return name, args
                        start = text.find("{", i + 1)
                        break
            else:
                break
        return None

    def _prepare_reasoning(self, user_text: str, memory_ctx: dict) -> dict:
        """Agent döngüsü öncesi kurulum: system prompt, mesajlar, mod/model/
        parametre tespiti. Döner: döngünün kullandığı yapılandırma dict'i (davranış aynı)."""
        # owner sadece settings değişince güncellenir — her çağrıda okumaya gerek yok
        if not self._owner or self._owner == "Kullanıcı":
            self._owner = get_system_setting("owner_name", "Kullanıcı")
        system   = self._build_system_prompt(memory_ctx)
        messages = self._build_messages(system, user_text)

        _low = user_text.lower()

        # Plan/yol haritası → daha fazla token
        _is_plan = any(t in _low for t in self._PLAN_TRIGGERS)

        # Görev modu: çok adımlı UI hedefi → uzun döngü + ekran geri bildirimi
        _is_mission = self._looks_like_mission(_low)
        _max_steps = 12 if _is_mission else MAX_AGENT_STEPS
        if _is_mission:
            messages.insert(1, {"role": "system", "content": self._MISSION_PROMPT})
            log.info(f"Görev modu aktif (max {_max_steps} adım): {user_text[:60]}")

        # GENEL kural — sadece Spotify/anahtar kelime değil, HER konu için:
        # Uzun komutlar (5+ kelime) tek araç hedefi aşan niyet taşıyabilir.
        # Bu komutlarda araç çalıştıktan sonra döngü ERKEN BİTMEZ; LLM sonucu
        # ve ekranı görür, hedef tamamlandıysa kendisi bitirir, eksikse devam
        # eder. Kısa komutlar ("spotify aç") anında-dönüş hızını korur.
        _multi_intent = _is_mission or len(user_text.split()) >= 5
        if _multi_intent and not _is_mission:
            _max_steps = max(_max_steps, 8)
            messages.insert(1, {"role": "system", "content": (
                "Kullanıcının isteği birden fazla adım gerektirebilir. "
                "Araç çalıştıktan sonra hedefin TAMAMI gerçekleşmediyse sıradaki "
                "aracı çağır (araç sonucunda EKRAN özeti varsa ona göre karar ver). "
                "Hedef tamamlandıysa tek cümlelik Türkçe cevapla bitir."
            )})

        # Sohbet tespiti: geçmiş varsa veya mesaj açıkça sohbet sorusuysa
        _is_chat_context = bool(self._history) or self._is_chat_query(_low)

        # Sohbet için ayrı model kullan (ayarlanmışsa) — komutlar ana modelde kalır
        _chat_model = get_system_setting("chat_model_name", "") or ""
        model = (_chat_model if _is_chat_context and _chat_model else None) or get_model_name()

        # Ana döngü num_predict: sabit 768 (sesli cevap kısa; giriş limiti = 8192-768).
        _num_predict_main  = _LLM_NUM_PREDICT
        _num_predict_final = 500 if _is_plan else 300
        # Sohbet: temperature yüksek + repeat_penalty düşük (doğal tekrarlar engellenmesin)
        # Komut: temperature düşük + repeat_penalty yüksek (tutarlı çıktı)
        _temperature     = 0.4  if _is_chat_context else 0.2
        _repeat_penalty  = 1.05 if _is_chat_context else 1.1
        return {
            "messages": messages, "model": model, "is_mission": _is_mission,
            "multi_intent": _multi_intent, "is_chat_context": _is_chat_context,
            "max_steps": _max_steps, "num_predict_main": _num_predict_main,
            "num_predict_final": _num_predict_final, "temperature": _temperature,
            "repeat_penalty": _repeat_penalty,
        }

    def _reason(self, user_text: str, memory_ctx: dict) -> str:
        """Trace sarmalayıcı (READ-ONLY): istek trace'ini başlat, gövdeyi çalıştır,
        bitişte JSONL'e yaz. Trace kapalıysa (self._tracer=None) sıfır ek davranış."""
        from core import trace as _trace
        self._tracer = _trace.begin(user_text)
        self._trace_ended_by = "model_done"
        self._trace_input_limit_logged = False   # WARNING istek başına bir kez
        _final = ""
        try:
            _final = self._reason_inner(user_text, memory_ctx)
            return _final
        except Exception:
            self._trace_ended_by = "error"
            raise
        finally:
            _t = getattr(self, "_tracer", None)
            if _t is not None:
                try:
                    _t.finish(_final, self._trace_ended_by)
                except Exception as _te:
                    log.debug("trace finish atlandı: %s", _te)
            self._tracer = None

    def _trace_step(self, step_no, tools_visible, tool_called, args, tool_result,
                    self_heal_triggered, self_heal_reason, t0, *,
                    step_kind="tool_call", rejected_reason=None,
                    context_tokens=None, result_fed_back_chars=0,
                    prompt_char_len=None, model_name=None, num_ctx_sent=None) -> None:
        """Bir agent adımını aktif tracer'a ekle (guarded — trace kapalıysa no-op,
        asla patlamaz). tool_result None ise araçsız/reddedilmiş adım."""
        _t = getattr(self, "_tracer", None)
        if _t is None:
            return
        try:
            _ok = None if tool_result is None else bool(getattr(tool_result, "ok", None))
            _err = None if tool_result is None else getattr(tool_result, "error", None)
            _t.add_step(
                step_no=step_no,
                tools_visible=tools_visible,
                tool_called=tool_called,
                args=(args if isinstance(args, dict) else {}),
                result_ok=_ok,
                result_error=_err,
                self_heal_triggered=self_heal_triggered,
                self_heal_reason=self_heal_reason,
                latency_ms=int((time.monotonic() - t0) * 1000),
                step_kind=step_kind,
                rejected_reason=rejected_reason,
                context_tokens=context_tokens,
                result_fed_back_chars=result_fed_back_chars,
                prompt_char_len=prompt_char_len,
                model_name=model_name,
                num_ctx_sent=num_ctx_sent,
            )
        except Exception as _se:
            log.debug("trace step atlandı: %s", _se)

    def _reason_inner(self, user_text: str, memory_ctx: dict) -> str:
        """
        Gerçek agent döngüsü:
        1. LLM araç seçer
        2. Araç çalışır → sonuç mesaj geçmişine eklenir
        3. LLM sonucu görür, yeni araç seçer veya bitirip cevap verir
        4. Hata → self-heal (alternatif araç dene)
        5. Max MAX_AGENT_STEPS adımda zorla bitir
        """
        _cfg = self._prepare_reasoning(user_text, memory_ctx)
        messages           = _cfg["messages"]
        model              = _cfg["model"]
        _is_mission        = _cfg["is_mission"]
        _multi_intent      = _cfg["multi_intent"]
        _is_chat_context   = _cfg["is_chat_context"]
        _max_steps         = _cfg["max_steps"]
        _num_predict_main  = _cfg["num_predict_main"]
        _num_predict_final = _cfg["num_predict_final"]
        _temperature       = _cfg["temperature"]
        _repeat_penalty    = _cfg["repeat_penalty"]

        step            = 0
        heal_depth      = 0
        last_action     = "chat"
        last_result     = ""
        tool_call_count = 0       # kaç araç çağrıldı (raporlama için)
        failed_tools: set = set() # heal sırasında başarısız olan araçlar
        # Tekrar koruması — canlı testte 8B model aynı aracı 12 kez üst üste
        # çağırabiliyor (chrome ×12). Mekanik kırıcı: aynı (araç, argüman)
        # ikinci kez gelirse ÇALIŞTIRILMAZ; üçüncü kez gelirse döngü biter.
        _seen_calls: dict = {}      # (action, arg_key) → kaç kez istendi
        _action_counts: dict = {}   # action → toplam çalıştırma
        _done_ledger: list = []     # yapılanların özeti (LLM'e gösterilir)
        available_tools = None      # 1. adımda seçilir, sonraki adımlarda korunur

        while step < _max_steps:
            step += 1
            _step_t0 = time.monotonic()   # trace: adım gecikmesi

            # Bağlama göre tool listesini küçült — token tasarrufu
            # İlk adımda bağlam analizi yap, sonraki adımlarda sabit tut
            available_tools = self._select_available_tools(
                user_text, step, _is_mission, available_tools, failed_tools)
            # trace: o adımda modele sunulan araç adları (adım BAŞINA değişebilir)
            _tools_vis = None
            _prompt_chars = None
            if self._tracer is not None:
                _tools_vis = [t.get("function", {}).get("name")
                              for t in (available_tools or [])]
                # trace: bu adımda modele giden BİRİKİMLİ messages'ın toplam karakteri
                # (prompt_eval_count'un cache davranışından bağımsız doğrulama)
                try:
                    _prompt_chars = len(json.dumps(messages, ensure_ascii=False, default=str))
                except Exception:
                    _prompt_chars = None

            # ── LLM çağrısı ───────────────────────────────────────────────────
            data, _sig = self._call_llm_step(
                messages, available_tools, model, step,
                _temperature, _num_predict_main, _repeat_penalty,
                _multi_intent, user_text)
            if _sig is not None:
                if _sig[0] == "break":
                    # LLM ulaşılamadı (timeout/bağlantı reddi) → trace'e HATA adımı yaz.
                    # Eskiden izsiz break atılıyordu → istek "steps": [] görünüyordu
                    # (7 günde %9.9 istek bu kör noktaya düşmüştü). step_kind="llm_error",
                    # result_error=hata mesajı (ToolResult.ok/.error üzerinden otomatik).
                    self._trace_ended_by = "error"
                    _err_result = ToolResult(message=_sig[1], ok=False, error=_sig[1])
                    self._trace_step(step, _tools_vis, None, {}, _err_result, False, None,
                                     _step_t0, step_kind="llm_error", prompt_char_len=_prompt_chars,
                                     model_name=model, num_ctx_sent=_LLM_NUM_CTX)
                    break              # LLM ulaşılamıyor → döngüden çık, fallback
                # ('return', metin): ilk-adım acil kural-yolu düşüşü (LLM zaman aşımı)
                self._trace_ended_by = "error"
                self._trace_step(step, _tools_vis, None, {}, None, False, None, _step_t0,
                                 step_kind="text_response", prompt_char_len=_prompt_chars,
                                 model_name=model, num_ctx_sent=_LLM_NUM_CTX)
                return _sig[1]

            # trace: bu adımda modele giden prompt token sayısı (Ollama prompt_eval_count)
            _ctx_tokens = None
            if self._tracer is not None:
                try:
                    _ctx_tokens = data.get("prompt_eval_count")
                except Exception:
                    _ctx_tokens = None
                # Giriş limiti kırpılması tespiti: context_tokens, num_ctx - num_predict
                # sınırına dayandıysa prompt kırpılıyor demektir (SESSİZ yaşanmasın).
                if _ctx_tokens is not None and _ctx_tokens >= (_LLM_NUM_CTX - _num_predict_main):
                    self._tracer.mark_input_limit_hit()
                    if not self._trace_input_limit_logged:
                        self._trace_input_limit_logged = True
                        log.warning(
                            "Context giriş limiti aşıldı — prompt KIRPILIYOR: "
                            f"context_tokens={_ctx_tokens} >= limit="
                            f"{_LLM_NUM_CTX - _num_predict_main} "
                            f"(num_ctx={_LLM_NUM_CTX}, num_predict={_num_predict_main}, adım={step})"
                        )

            message    = data.get("message", {}) or {}
            tool_calls = message.get("tool_calls", []) or []
            content    = str(message.get("content", "") or "").strip()

            # ── Araç yok → LLM doğrudan cevap verdi ──────────────────────────
            if not tool_calls:
                # Bazı modeller (qwen2.5 dahil) tool çağrısını yapısal döndürmek
                # yerine CONTENT içinde JSON olarak yazıyor → get_time gibi araçlar
                # çalışmıyordu. Bunu yakala ve GERÇEK tool çağrısına çevir.
                _parsed = self._tool_call_from_text(content)
                if _parsed:
                    tool_calls = [{"function": {"name": _parsed[0], "arguments": _parsed[1]}}]
                elif content:
                    # trace: model araçsız doğrudan cevap verdi (text_response)
                    self._trace_step(step, _tools_vis, None, {}, None, False, None, _step_t0,
                                     step_kind="text_response", context_tokens=_ctx_tokens,
                                     prompt_char_len=_prompt_chars,
                                     model_name=model, num_ctx_sent=_LLM_NUM_CTX)
                    return content
                else:
                    break   # boş cevap → döngüden çık

            # ── Araç seçildi ─────────────────────────────────────────────────
            tc     = tool_calls[0]
            fn     = tc.get("function", {}) or {}
            action = str(fn.get("name", "chat")).strip()
            args   = fn.get("arguments", {}) or {}

            # "chat" → LLM konuşmak istiyor, döngüyü bitir
            if action == "chat":
                chat_msg = str(args.get("message", "") or content).strip()
                # trace: "chat" gerçek araç değil → araçsız cevap (text_response)
                self._trace_step(step, _tools_vis, None, args, None, False, None, _step_t0,
                                 step_kind="text_response", context_tokens=_ctx_tokens,
                                 prompt_char_len=_prompt_chars,
                                 model_name=model, num_ctx_sent=_LLM_NUM_CTX)
                return chat_msg or self._fallback(user_text, memory_ctx)

            # ── Tekrar kırıcı: aynı aracı aynı argümanla yeniden çağırma ──────
            _rsig, messages = self._check_repeat(
                action, args, content, tool_calls, messages, user_text,
                _seen_calls, _action_counts)
            if _rsig == "break":
                break              # model kilitlendi → döngüyü bitir
            if _rsig == "continue":
                # trace: aynı araç 2. kez istendi → ÇALIŞTIRILMADAN reddedildi
                # (_check_repeat: _seen_calls[(action,args)]==2). Sonuç modele
                # geri beslenmez (result_fed_back_chars=0).
                self._trace_step(step, _tools_vis, action, args, None, False, None, _step_t0,
                                 step_kind="tool_call_rejected", context_tokens=_ctx_tokens,
                                 rejected_reason="aynı araç aynı argümanla 2. kez çağrıldı (çalıştırılmadan reddedildi)",
                                 prompt_char_len=_prompt_chars,
                                 model_name=model, num_ctx_sent=_LLM_NUM_CTX)
                continue           # 2. kez → çalıştırmadan reddedildi

            # ── Aracı çalıştır ────────────────────────────────────────────────
            tool_result  = self._execute_tool(action, args)
            is_error     = not tool_result.ok   # ToolResult.ok — string-eşleştirme yok
            last_action  = action
            last_result  = tool_result
            tool_call_count += 1

            self._log_action(action, args, tool_result, ok=not is_error)

            # trace: bu adımı kaydet (self-heal KARARINDAN ÖNCE — gerçek koşulu
            # aynen yansıt; ok=True iken tetiklenirse bile gizlenmez).
            _heal_will_trigger = is_error and heal_depth < MAX_HEAL_DEPTH
            _heal_reason = (getattr(tool_result, "error", None) or str(tool_result)[:150]) \
                if _heal_will_trigger else None
            # trace: araç sonucundan modele GERİ BESLENEN karakter sayısı — akışın
            # gireceği dalı aynen yansıtır (self-heal error[:300] / sessiz araç 0 /
            # normal _append_tool_result result[:500]). NOT: aşağıdaki dal koşulları
            # değişirse bu de güncellenmeli.
            _fed_back = 0
            if self._tracer is not None:
                _rlen = len(str(tool_result))
                if _heal_will_trigger:
                    _fed_back = min(_rlen, 300)     # self-heal → error[:300] beslenir
                elif action in _SILENT_TOOLS and not is_error and not _multi_intent:
                    _fed_back = 0                   # sessiz araç → doğrudan döner, beslenmez
                else:
                    _fed_back = min(_rlen, 500)     # _append_tool_result → result[:500]
            self._trace_step(step, _tools_vis, action, args, tool_result,
                             _heal_will_trigger, _heal_reason, _step_t0,
                             step_kind="tool_call", context_tokens=_ctx_tokens,
                             result_fed_back_chars=_fed_back, prompt_char_len=_prompt_chars,
                             model_name=model, num_ctx_sent=_LLM_NUM_CTX)

            # ── Self-heal: hata → LLM'e bildir, yeni araç seçtir ─────────────
            if is_error and heal_depth < MAX_HEAL_DEPTH:
                messages, heal_depth = self._attempt_self_heal(
                    action, tool_result, tool_calls, messages, user_text,
                    heal_depth, failed_tools)
                continue   # yeni araç seç

            # ── Çok niyetli komut: UI eyleminden sonra ekranı oku, LLM görsün ─
            _screen_note = self._read_screen_note(action, is_error, _multi_intent)

            # ── Başarılı araç sonucunu mesaj geçmişine ekle + ledger/direktif ──
            messages = self._append_tool_result(
                messages, tool_result, _screen_note, content, action, tool_calls,
                args, _done_ledger, _multi_intent, user_text)

            # ── Zincirleme araçlar: devam et, LLM sonraki adıma karar versin ──
            if action in _CHAIN_CONTINUE:
                continue   # sonucu mesaj geçmişine ekledik, LLM görsün

            # ── Sessiz araçlar: hemen bitir, follow-up LLM yok ────────────────
            # Çok niyetli komutlarda (görev modu VEYA 5+ kelime) BİTMEZ —
            # LLM sonuç + ekran geri bildirimiyle bitirme kararını kendisi verir
            if action in _SILENT_TOOLS and not is_error and not _multi_intent:
                return tool_result or f"{action} tamamlandı."

            # Diğer araçlar: döngüye devam, LLM sonucu yorumlar veya yeni adım seçer

        # ── Döngü bitti — son durumu özetle ──────────────────────────────────
        # trace: adım kotası dolduysa max_iterations, yoksa break ile bitti (model_done)
        if self._trace_ended_by != "error":
            self._trace_ended_by = "max_iterations" if step >= _max_steps else "model_done"
        return self._summarize_result(
            last_result, last_action, _multi_intent, messages, model,
            _num_predict_final, _is_chat_context, user_text, memory_ctx)

    def _summarize_result(self, last_result, last_action, _multi_intent, messages,
                          model, _num_predict_final, _is_chat_context, user_text, memory_ctx) -> str:
        """Döngü bittikten sonra nihai cevabı üret: sessiz araç sonucu / follow-up
        LLM özeti / son sonuç / fallback. Döner: kullanıcıya gösterilecek metin."""
        if last_result and last_action not in ("chat",):
            # Çok niyetli komutta ham araç çıktısı yerine LLM özeti ver
            if last_action in _SILENT_TOOLS and not _multi_intent:
                return last_result

            # Follow-up: LLM son aracın sonucunu doğal dille özetlesin
            # _followup_llm kendi içinde assistant+tool mesajlarını ekliyor;
            # messages'ı olduğu gibi geçiyoruz (son 2 öğeyi kesmiyoruz)
            return self._followup_llm(
                messages,
                {"content": ""},
                last_result,
                model,
                num_predict=_num_predict_final,
                is_chat=_is_chat_context,
            )

        if last_result:
            return last_result

        return self._fallback(user_text, memory_ctx)

    def _append_tool_result(self, messages, tool_result, _screen_note, content, action,
                            tool_calls, args, _done_ledger, _multi_intent, user_text):
        """Başarılı araç sonucunu (varsa ekran notuyla) mesaj geçmişine ekle,
        yapılanlar ledger'ını güncelle ve çok-niyetli komutta 'sıradaki farklı adımı
        çağır ya da bitir' direktifini ekle. Döner: güncellenmiş messages."""
        # Bu sayede LLM bir sonraki adımda ne olduğunu görür
        _tool_payload = {"result": str(tool_result)[:500]}
        if _screen_note:
            _tool_payload["screen"] = _screen_note
        messages = messages + [
            {
                "role": "assistant",
                "content": content or f"[{action} çalıştırıldı]",
                "tool_calls": tool_calls,
            },
            {
                "role": "tool",
                "content": json.dumps(_tool_payload, ensure_ascii=False),
            },
        ]

        # ── Çok niyetli komut: devam direktifi + yapılanlar listesi ───────
        # llama3.1 araç sonucundan sonra ekranı ANLATMAYA veya aynı aracı
        # TEKRARLAMAYA meyilli. Her araçtan sonra: yapılanların net listesi
        # + "sıradaki FARKLI adımı çağır ya da bitir" direktifi.
        _arg_summary = ""
        try:
            _arg_summary = str(next(iter(args.values()), ""))[:40]
        except Exception as e:
            log.debug("argüman özeti çıkarılamadı: %s", e)
        _done_ledger.append(f"{action}({_arg_summary}) ✓")
        if _multi_intent and action not in _CHAIN_CONTINUE:
            # system rolü: model bunu cevap olarak TEKRARLAMAZ (user rolü sızdırıyordu)
            messages = messages + [{
                "role": "system",
                "content": (
                    f"[İç durum — kullanıcıya yazma] Yapılanlar: {'; '.join(_done_ledger)}. "
                    f"Hedef: {user_text}. Eksikse sıradaki farklı aracı çağır, "
                    "tamamsa tek cümleyle sonucu söyle."
                ),
            }]
        return messages

    def _check_repeat(self, action, args, content, tool_calls, messages, user_text,
                      _seen_calls, _action_counts):
        """Aynı aracın aynı argümanla tekrar çağrılmasını engelle; sayaçları günceller.
        Döner: (signal, messages). signal: 'break'=döngü kes (model kilitlendi),
        'continue'=çalıştırmadan reddet (2. kez), None=devam (aracı çalıştır)."""
        _call_key = (action, json.dumps(args, sort_keys=True, ensure_ascii=False)[:120])
        _seen_calls[_call_key] = _seen_calls.get(_call_key, 0) + 1
        _action_counts[action] = _action_counts.get(action, 0) + 1
        if _seen_calls[_call_key] >= 3 or _action_counts[action] > 4:
            # Model kilitlendi — döngüyü bitir, yapılanları özetle
            log.info(f"Tekrar kilidi: {action} döngüsü kesildi")
            return "break", messages
        if _seen_calls[_call_key] == 2:
            # ÇALIŞTIRMADAN reddet — modele net geri bildirim ver
            log.info(f"Tekrar engellendi: {action}{args}")
            messages = messages + [
                {"role": "assistant", "content": content or f"[{action} tekrar denendi]",
                 "tool_calls": tool_calls},
                {"role": "tool", "content": json.dumps(
                    {"error": f"{action} bu argümanla ZATEN çalıştırıldı ve başarılı oldu. Tekrar etme."},
                    ensure_ascii=False)},
                {"role": "system", "content": (
                    f"[İç durum — kullanıcıya yazma] Hedef: {user_text}. "
                    "Aynı aracı tekrar çağırma. Eksikse farklı araç çağır, "
                    "tamamsa tek cümleyle sonucu söyle."
                )},
            ]
            return "continue", messages
        return None, messages

    def _attempt_self_heal(self, action, tool_result, tool_calls, messages,
                           user_text, heal_depth, failed_tools):
        """Araç hata verince: heal sayacını artır, aracı failed_tools'a ekle, hatayı
        LLM'e bildirip farklı araç seçtirecek mesajları ekle. Döner: (messages, heal_depth)."""
        heal_depth += 1
        failed_tools.add(action)   # sonraki adımda bu araç sunulmaz
        print(
            f"[Brain] Self-heal #{heal_depth} — {action} → {tool_result[:60]}",
            flush=True,
        )
        heal_msg = _HEAL_PROMPT.format(
            action=action, error=tool_result[:300], user_text=user_text
        )
        messages = messages + [
            {
                "role": "assistant",
                "content": f"[{action} HATA: {tool_result[:150]}]",
                "tool_calls": tool_calls,
            },
            {"role": "tool",    "content": json.dumps({"error": tool_result[:300]}, ensure_ascii=False)},
            {"role": "user",    "content": heal_msg},
        ]
        return messages, heal_depth

    def _call_llm_step(self, messages, available_tools, model, step,
                       _temperature, _num_predict_main, _repeat_penalty,
                       _multi_intent, user_text):
        """Bir agent adımı için Ollama /api/chat çağrısı.
        Döner: (data, signal). signal: None=başarılı (data geçerli),
        ('break', hata_mesajı)=döngüden çık (LLM ulaşılamadı — mesaj trace'e yazılır),
        ('return', metin)=hemen dön (ilk-adım zaman aşımında kural-yolu düşüşü)."""
        # İlk adım: 5s işleme bütçesi. Aşılırsa kural-yoluna acil düşüş —
        # kullanıcı asla 5s'den fazla "hiçbir şey olmuyor" yaşamaz.
        _step_timeout = LLM_FIRST_STEP_TIMEOUT if step == 1 else LLM_TOOL_TIMEOUT
        try:
            from brain.llm import _session
            from core.perf_report import span as _perf_span
            with _perf_span(f"LLM adım {step}"):
                r = _session().post(
                _url("/api/chat"),
                json={
                    "model":      model,
                    "messages":   messages,
                    "tools":      available_tools,
                    "stream":     False,
                    "keep_alive": "30m",   # model RAM'de kalsın
                    "options": {
                        "temperature":    _temperature,
                        "num_predict":    _num_predict_main,
                        "num_ctx":        _LLM_NUM_CTX,
                        "top_p":          0.9,
                        "repeat_penalty": _repeat_penalty,
                    },
                },
                timeout=_step_timeout,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as _llm_err:
            log.warning(f"LLM isteği başarısız (adım {step}, {_step_timeout}s): {_llm_err}")
            if step == 1 and not _multi_intent:
                # Acil çıkış: kural eşleştirici komutu tanıyorsa ANINDA yap.
                # Çok adımlı komutlarda YAPILMAZ — kural yolu komutun
                # tamamını tek araca verip saçmalıyor (canlı testte
                # "spotify'dan beğenilen..." metnini program adı sandı).
                _emergency = self._fast_route(user_text)
                if _emergency is not None:
                    log.warning("İlk adım zaman aşımı → kural-yolu acil düşüş")
                    return None, ("return", _emergency)
            return None, ("break", f"{type(_llm_err).__name__}: {_llm_err}")
        return data, None

    def _select_available_tools(self, user_text, step, _is_mission, available_tools, failed_tools):
        """Adıma göre LLM'e sunulacak araç listesini seç: 1. adımda bağlam analizi
        (+görev modunda ekran/bekleme/doğrulama araçları), sonraki adımlarda başarısız
        araçları eleyerek önceki listeyi koru. Döner: araç listesi."""
        if step == 1:
            available_tools = _select_tools(user_text, KLARGUS_TOOLS)
            if _is_mission:
                # Görev modunda ekran/bekleme/doğrulama araçları şart
                _mission_extra = {
                    "screen_read", "check_screen_for", "find_and_click",
                    "find_and_double_click", "find_buttons", "wait_seconds",
                    "verify_goal", "launch_game", "focus_window",
                    "list_windows", "active_window", "click_center",
                }
                _have = {t.get("function", {}).get("name") for t in available_tools}
                available_tools = available_tools + [
                    t for t in KLARGUS_TOOLS
                    if t.get("function", {}).get("name") in (_mission_extra - _have)
                ]
        elif failed_tools:
            available_tools = [
                t for t in available_tools
                if t.get("function", {}).get("name") not in failed_tools
            ]
        # else: available_tools bir önceki adımdan gelir
        return available_tools

    def _read_screen_note(self, action, is_error, _multi_intent) -> str:
        """Çok-niyetli komutta UI eyleminden sonra ekranı okuyup kısa EKRAN notu
        döndürür (koşullar sağlanmazsa boş string)."""
        _screen_note = ""
        if _multi_intent and action in self._MISSION_UI_ACTIONS and not is_error:
            try:
                time.sleep(1.0)   # UI'nin güncellenmesini bekle
                from vision.screen_understanding import _get_cached_ocr, _ocr_is_meaningful
                _scr_text, _scr_ctx = _get_cached_ocr()
                _app_name = str((_scr_ctx or {}).get("app_name", "") or "")
                # Boş VE çöp OCR'ı LLM'e "ekranda şu yazıyor" diye verme — anlamsızsa
                # notu hiç ekleme (LLM boşluğu emin içerikle doldurmaya davet edilmesin).
                _screen_note = (
                    f"EKRAN [{_app_name}]: {str(_scr_text or '')[:400]}"
                    if _ocr_is_meaningful(_scr_text) else ""
                )
            except Exception as e:
                log.debug("ekran OCR notu alınamadı: %s", e)
        return _screen_note

    def _followup_llm(self, messages, assistant_msg, tool_result, model,
                      num_predict: int = 280, is_chat: bool = False) -> str:
        """Araç sonucunu LLM'e verip doğal yanıt al — sadece gerektiğinde."""
        followup = messages + [
            {"role": "assistant", "content": assistant_msg.get("content", "") or ""},
            {"role": "tool",
             "content": json.dumps({"result": str(tool_result)[:800]},
                                   ensure_ascii=False)},
        ]
        _temp   = 0.4  if is_chat else 0.2
        _rp     = 1.05 if is_chat else 1.1
        try:
            from brain.llm import _session
            from core.perf_report import span as _perf_span
            with _perf_span("LLM (yorum/followup)"):
                r2 = _session().post(
                _url("/api/chat"),
                json={
                    "model":      model,
                    "messages":   followup,
                    "stream":     False,
                    "keep_alive": "30m",   # model RAM'de kalsın
                    "options":  {
                        "temperature":    _temp,
                        "num_predict":    num_predict,
                        "num_ctx":        _LLM_NUM_CTX,
                        "top_p":          0.9,
                        "repeat_penalty": _rp,
                    },
                },
                timeout=LLM_FOLLOWUP_TIMEOUT,
            )
            r2.raise_for_status()
            content = str((r2.json().get("message", {}) or {}).get("content", "") or "").strip()
            return content or str(tool_result)
        except Exception:
            return str(tool_result)

    # ── Eylem günlüğü ────────────────────────────────────────────────────────
    # NOT: eski string-eşleştirmeli hata-tespit fonksiyonu KALDIRILDI —
    # hata artık ToolResult.ok ile belirleniyor (Görev 8 kapandı).

    def _log_action(self, tool: str, args: dict, result: str, ok: bool):
        """Eylem günlüğüne kayıt ekle — LLM farkındalığı için."""
        entry = {
            "ts":     time.strftime("%H:%M:%S"),
            "tool":   tool,
            "arg":    str(args)[:80],
            "result": result[:120],
            "ok":     ok,
        }
        self._action_log.append(entry)
        if len(self._action_log) > ACTION_LOG_MAX:
            self._action_log = self._action_log[-ACTION_LOG_MAX:]
        status = "✓" if ok else "✗"
        log.info(f"{status} {tool}({entry['arg']}) → {result[:60]}")

    def _action_log_summary(self) -> str:
        """Son eylemleri LLM'in anlayacağı biçimde özetle."""
        if not self._action_log:
            return ""
        lines = ["Son eylemler:"]
        for e in self._action_log[-6:]:   # son 6 eylem yeterli
            status = "TAMAM" if e["ok"] else "HATA"
            lines.append(f"  [{e['ts']}] {status} — {e['tool']}({e['arg']}) → {e['result']}")
        return "\n".join(lines)

    def _execute_tool(self, action: str, args: dict) -> ToolResult:
        # Test/güvenli mod: yan-etkili araçları ÇALIŞTIRMA (tarayıcı/program/sistem).
        # Testler beyni canlandırıyor; gerçek dünya yan etkisi (Google açma, kamera vb.)
        # olmasın diye agent döngüsünden gelen bu araçlar burada atlanır.
        if _test_mode() and action in _SIDE_EFFECT_TOOLS:
            log.info(f"[test modu] yan-etkili araç atlandı: {action}")
            return ToolResult(message=f"[test modu: {action} çalıştırılmadı]", ok=True)
        # Bilinen parametre adlarına göre sıralı arama (tek-arg fallback için)
        _PRIMARY_KEYS = (
            "name", "url", "query", "text", "key", "keys",
            "message", "question",
            "arg", "path", "directory",
            # Cyber tool parametreleri
            "domain", "ip", "target", "hash_string", "hash_value",
            "password", "hash_type", "wordlist", "mode",
        )
        arg = ""
        for key in _PRIMARY_KEYS:
            if key in args and args[key]:
                arg = str(args[key]).strip()
                break

        # Hiçbiri bulunamadıysa dict'in ilk değerini dene
        if not arg and args:
            first_val = next(iter(args.values()), "")
            if first_val:
                arg = str(first_val).strip()

        try:
            # args dict'ini _kw olarak geç — multi-param tool'lar (encode_decode vb.)
            # run_tool önce _kw yolunu dener, başarısız olursa tek-arg'a düşer.
            # NOT: **args kullanılmaz — run_tool'un 'action' parametresiyle çakışır.
            # run_tool ZATEN ToolResult döndürür (str alt-sınıfı) — aynen geçir.
            return run_tool(action, arg, _kw=args)
        except Exception as e:
            return ToolResult(message=f"({action} çalıştırılamadı: {e})", ok=False, error=str(e))

    def _fallback(self, user_text: str, memory_ctx: dict) -> str:
        try:
            from brain.llm import ask_llm
            mem = memory_ctx.get("memories", "") or memory_ctx.get("profile", "")
            return ask_llm(user_text, memory_text=mem)
        except Exception:
            return "Bir sorun oluştu, lütfen tekrar dene."

    def _append_history(self, role: str, content: str):
        with self._lock:
            self._history.append({"role": role, "content": content})
            if len(self._history) > MAX_HISTORY:
                self._history = self._history[-MAX_HISTORY:]

    # ── Arka plan hafıza ──────────────────────────────────────────────────────

    # Arka plan LLM çağrıları foreground'u bekletmesin:
    # Ollama aynı modelde istekleri SIRAYA koyar — cevaptan hemen sonra başlayan
    # hafıza LLM'i, kullanıcının bir sonraki komutunu 5-10s bekletebiliyordu.
    _BG_IDLE_DELAY: float = BG_IDLE_DELAY   # config.py tek kaynak (6.0) — kullanıcı sessizse çalış

    def _wait_for_idle_gpu(self) -> bool:
        """Yeni bir foreground komutu gelmediyse True döner (arka plan LLM güvenli)."""
        seq = self._think_seq
        time.sleep(self._BG_IDLE_DELAY)
        return self._think_seq == seq

    def _save_memory_async(self, user_text: str, assistant_text: str):
        now = time.monotonic()

        # ── 1. user_fact çıkarımı — her 20s'de bir, kısa prompt ──────────────
        if (now - self._last_fact_extract_at) >= self._FACT_EXTRACT_THROTTLE:
            self._last_fact_extract_at = now
            threading.Thread(
                target=self._extract_user_fact,
                args=(user_text, assistant_text),
                daemon=True,
            ).start()

        # ── 2. Genel hafıza kararı — her 60s'de bir ──────────────────────────
        if (now - self._last_memory_save_at) < self._MEMORY_SAVE_THROTTLE:
            return
        self._last_memory_save_at = now

        # Kullanıcı hemen yeni komut verdiyse bu turu atla — GPU foreground'a kalsın
        if not self._wait_for_idle_gpu():
            return

        prompt = _MEMORY_DECIDE_PROMPT.format(
            user=user_text[:200], assistant=assistant_text[:200])
        try:
            raw = call_ollama(prompt, retries=1, timeout=10, num_predict=80)
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start == -1 or end == 0:
                return
            decision = json.loads(raw[start:end])
            if decision.get("save") and decision.get("text"):
                from brain.memory_bank import remember_text
                remember_text(decision["text"], kind="learned")
                self.invalidate_memory_cache()
                try:
                    import ui.app as _app
                    with _app._build_memory_lock:
                        _app._build_memory_cache["ts"] = 0.0
                except Exception as e:
                    log.debug("ui.app hafıza cache invalidasyonu başarısız: %s", e)
        except Exception as e:
            log.warning("öğrenilen metin kaydedilemedi: %s", e)

    def _extract_user_fact(self, user_text: str, assistant_text: str):
        """Konuşmadan kullanıcıya dair kalıcı gerçek çıkar, ChromaDB'ye kaydet."""
        # Kullanıcı hemen yeni komut verdiyse bu turu atla — GPU foreground'a kalsın
        if not self._wait_for_idle_gpu():
            return
        try:
            from brain.profile_learning import extract_user_fact, save_user_fact
            result = extract_user_fact(user_text, assistant_text)
            if result:
                save_user_fact(result["fact"], result["category"])
                self.invalidate_memory_cache()
                print(
                    f"[Brain] user_fact öğrenildi [{result['category']}]: {result['fact'][:60]}",
                    flush=True,
                )
        except Exception as e:
            log.warning("kullanıcı bilgisi (user_fact) kaydedilemedi: %s", e)


# ── Singleton ─────────────────────────────────────────────────────────────────

_brain: Optional[SovereignBrain] = None
_brain_lock = threading.Lock()


def get_sovereign_brain() -> SovereignBrain:
    global _brain
    if _brain is None:
        with _brain_lock:
            if _brain is None:
                _brain = SovereignBrain()
                # Arka planda: embedding'siz kayıtları düzelt
                threading.Thread(
                    target=_startup_reembed,
                    daemon=True,
                ).start()
    return _brain


def _startup_reembed():
    """Uygulama başlarken embedding'siz ChromaDB kayıtlarını düzelt."""
    try:
        from brain.memory_bank import reembed_existing
        result = reembed_existing()
        if "0/" not in result:
            log.info(f"Reembed: {result}")
    except Exception as e:
        log.debug("başlangıç reembed başarısız: %s", e)


def sovereign_think(user_text: str) -> str:
    return get_sovereign_brain().think(user_text)
