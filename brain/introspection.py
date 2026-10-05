# -*- coding: utf-8 -*-
"""
brain/introspection.py — Mimari Öz-Analiz
==========================================
Klargus kendi yapısını, yeteneklerini ve performansını analiz eder.

API:
  capability_map()       → Tüm araçlar kategorize edilmiş
  performance_report()   → Başarı/başarısızlık istatistikleri
  architecture_overview()→ Sistem mimarisinin özeti
  full_self_analysis()   → Hepsini birleştir, LLM ile yorumla
"""

import json
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR     = PROJECT_ROOT / "data"


# ─────────────────────────────────────────────────────────────────────────────
# 1) Araç Haritası
# ─────────────────────────────────────────────────────────────────────────────

_TOOL_CATEGORIES = {
    "Uygulama & Dosya":   ["open_program", "open_website", "open_folder", "open_file",
                           "launch_game", "rebuild_program_index"],
    "Arama & Medya":      ["google_search", "youtube_search", "spotify_search",
                           "spotify_liked_songs", "random_music", "recipe_summary"],
    "Klavye & Fare":      ["type_text", "press_key", "hotkey", "scroll_up", "scroll_down",
                           "double_click", "right_click", "click_center"],
    "Ekran Okuma & Görüntü": ["screen_read", "take_screenshot", "check_screen_for",
                           "find_and_click", "find_and_double_click", "find_buttons"],
    "Pencere Yönetimi":   ["active_window", "list_windows", "focus_window", "close_window",
                           "close_active_window", "minimize_window", "maximize_window", "switch_window"],
    "Ses & Ekran Ayarı":  ["get_volume", "set_volume", "mute_volume", "unmute_volume",
                           "get_brightness", "set_brightness", "enable_dark_mode", "enable_light_mode",
                           "enable_night_light", "disable_night_light", "turn_off_monitor"],
    "Güç Yönetimi":       ["sleep_pc", "lock_pc", "shutdown_pc", "restart_pc", "cancel_shutdown"],
    "Ağ & Bluetooth":     ["get_wifi_status", "list_wifi_networks", "enable_wifi", "disable_wifi",
                           "enable_bluetooth", "disable_bluetooth", "list_bluetooth_devices"],
    "Sistem Bilgisi":     ["system_info", "battery_status", "disk_info", "list_processes",
                           "kill_process", "get_resolution", "get_clipboard", "set_clipboard",
                           "open_windows_settings", "run_terminal", "run_shell"],
    "Başlangıç & Mikrofon": ["system_startup_enable", "system_startup_disable", "system_startup_status",
                           "system_mic_auto_select", "system_mic_default"],
    "Web":                ["web_search", "web_fetch"],
    "Siber Güvenlik":     ["nmap_scan", "nikto_scan", "sqlmap_test", "dns_lookup", "whois_lookup",
                           "reverse_dns", "subdomain_enum", "ssl_check", "robots_txt", "traceroute",
                           "ip_info", "hash_identify", "hash_calculate", "hash_crack",
                           "password_strength", "encode_decode", "gobuster_dir", "http_analysis"],
    "Kali / WSL":         ["kali_run", "kali_nmap", "kali_nmap_quick", "kali_whois", "kali_whatweb",
                           "kali_traceroute", "kali_sqlmap", "kali_shell", "kali_terminal",
                           "kali_status", "kali_results", "kali_tools_list", "run_kali", "run_kali_bg",
                           "wsl_run", "run_wsl_command", "wsl_status", "wsl_check_status",
                           "wsl_tool_check", "wsl_path", "windows_path"],
    "Kod & Proje":        ["read_project_file", "search_project_code", "get_project_structure",
                           "get_file_summary", "list_project_files"],
    "Agent & Öz-geliştirme": ["plan_and_execute", "self_improve", "self_analyze", "self_repair",
                           "generate_tool", "verify_goal", "wait_seconds"],
    "Öğrenme & Hafıza":   ["learn_command", "run_learned_command", "list_learned_commands"],
    "Sohbet":             ["chat", "memory_answer"],
}


def capability_map() -> str:
    """Tüm araçları kategorize edilmiş listele."""
    try:
        from brain.tool_registry import list_tools
        registered = set(list_tools())
    except Exception:
        registered = set()

    lines = ["═══ KLARGUS YETENEKLERİ ═══\n"]
    total = 0

    for category, tools in _TOOL_CATEGORIES.items():
        active = [t for t in tools if t in registered]
        inactive = [t for t in tools if t not in registered]
        lines.append(f"▸ {category} ({len(active)}/{len(tools)})")
        for t in active:
            lines.append(f"   ✓ {t}")
        for t in inactive:
            lines.append(f"   ✗ {t}  [kayıtsız]")
        total += len(active)

    # Kategoride olmayan ek araçlar
    categorized = {t for tools in _TOOL_CATEGORIES.values() for t in tools}
    extra = registered - categorized
    if extra:
        lines.append(f"▸ Diğer ({len(extra)})")
        for t in sorted(extra):
            lines.append(f"   ✓ {t}")
        total += len(extra)

    lines.append(f"\nToplam aktif araç: {total}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 2) Performans Raporu
# ─────────────────────────────────────────────────────────────────────────────

def performance_report(last_n: int = 200) -> str:
    """Konuşma logunu analiz et — başarı oranı, en çok kullanılan araçlar, hatalar."""
    log_file = DATA_DIR / "conversations.jsonl"
    if not log_file.exists():
        return "Henüz konuşma logu yok."

    entries = []
    try:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    except Exception as e:
        return f"Log okunamadı: {e}"

    entries = entries[-last_n:]
    if not entries:
        return "Log boş."

    total = len(entries)
    ok_count = sum(1 for e in entries if e.get("ok", True))
    fail_count = total - ok_count

    # Aksiyon dağılımı
    action_counts: dict = defaultdict(int)
    action_fails: dict = defaultdict(int)
    for entry in entries:
        action = entry.get("action", "unknown")
        action_counts[action] += 1
        if not entry.get("ok", True):
            action_fails[action] += 1

    top_actions = sorted(action_counts.items(), key=lambda x: -x[1])[:10]
    top_fails   = sorted(action_fails.items(), key=lambda x: -x[1])[:5]

    lines = [
        f"═══ PERFORMANS RAPORU (Son {total} etkileşim) ═══\n",
        f"Başarılı  : {ok_count}  ({ok_count/total*100:.0f}%)",
        f"Başarısız : {fail_count}  ({fail_count/total*100:.0f}%)\n",
        "En çok kullanılan araçlar:",
    ]
    for action, count in top_actions:
        fail = action_fails.get(action, 0)
        lines.append(f"  {action:<30} {count:>4}x  (hata: {fail})")

    if top_fails:
        lines.append("\nEn çok hata veren araçlar:")
        for action, count in top_fails:
            lines.append(f"  {action:<30} {count:>4}x hata")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 3) Mimari Genel Bakış
# ─────────────────────────────────────────────────────────────────────────────

def architecture_overview() -> str:
    """Sistemin katmanlarını ve bileşenlerini özetle."""
    lines = [
        "═══ KLARGUS MİMARİSİ ═══\n",
        "KATMAN 1 — Ses & Giriş",
        "  wake_word.py     → Uyandırma kelimesi dinleme (Vosk + Whisper)",
        "  speech_to_text.py→ faster-whisper STT (CUDA) + Vosk yedek",
        "  text_to_speech.py→ Piper TTS (kalıcı süreç) + streaming",
        "",
        "KATMAN 2 — Beyin (Bu ben)",
        "  sovereign_brain.py → Agent döngüsü (8 adım max)",
        "  llm.py             → Ollama /api/chat + tool-calling",
        "  llm_intent.py      → Hızlı kural eşleştirici + LLM niyet",
        "  memory_bank.py     → Uzun süreli hafıza (SQLite)",
        "  knowledge_base.py  → Bilgi tabanı",
        "",
        "KATMAN 3 — Araçlar",
        "  basic_tools.py     → Program/site/dosya açma",
        "  system_tools.py    → Ses, parlaklık, güç, WiFi",
        "  input_tools.py     → Klavye & fare",
        "  window_tools.py    → Pencere yönetimi",
        "  vision_tools.py    → Ekran okuma (OCR + moondream + Türkçe sentez)",
        "  cyber_tools.py     → Siber güvenlik (nmap, dns, hash, ssl)",
        "  kali_tools/wsl     → Kali/WSL araç köprüsü",
        "  codebase_tools.py  → Kendi kodunu okuma/arama",
        "  agent_tools.py     → Screenshot, shell, bekleme",
        "  generated_tools.py → Dinamik üretilen araçlar",
        "",
        "KATMAN 4 — Öğrenme",
        "  training_manager.py  → Konuşma logu, fine-tune dataset",
        "  self_improvement.py  → Otomatik öz-değerlendirme",
        "  tool_generator.py    → Yeni araç üretimi",
        "  self_repair.py       → Hata tespiti ve kod düzeltme",
        "  autonomous_planner.py→ Çok adımlı otonom planlama",
        "",
        "KATMAN 5 — UI",
        "  app.py              → Tkinter arayüz",
        "  llm.py              → LLM katmanı + streaming TTS",
    ]

    # Mevcut model bilgisi
    try:
        from brain.llm import get_model_name
        from brain.system_settings import get_system_setting
        main_model   = get_model_name()
        vision_model = get_system_setting("vision_model", "yok")
        lines.append(f"\nAktif modeller:")
        lines.append(f"  Ana LLM    : {main_model}")
        lines.append(f"  Vision     : {vision_model}")
    except Exception:
        pass

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 4) Tam Öz-Analiz
# ─────────────────────────────────────────────────────────────────────────────

def full_self_analysis() -> str:
    """Mimari + performans + yetenekleri birleştir, LLM ile yorumla."""
    arch    = architecture_overview()
    perf    = performance_report(last_n=100)
    caps    = capability_map()

    combined = f"{arch}\n\n{perf}\n\n{caps}"

    # LLM ile kısa yorum ekle
    try:
        from brain.llm import call_ollama
        prompt = (
            f"Aşağıda bir AI asistanın kendine ait sistem analizi var.\n"
            f"Bu analizi oku ve şunları belirt:\n"
            f"1. Güçlü yönler\n2. Zayıf yönler\n3. Önerilen iyileştirme\n"
            f"Kısa ve net Türkçe yaz.\n\n{combined[:2000]}"
        )
        llm_comment = call_ollama(prompt, retries=1, timeout=20, num_predict=200)
        if llm_comment and not llm_comment.startswith("LLM"):
            combined += f"\n\n═══ LLM DEĞERLENDİRMESİ ═══\n{llm_comment}"
    except Exception:
        pass

    return combined
