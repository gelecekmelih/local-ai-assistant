# -*- coding: utf-8 -*-
"""
config.py — Tüm ayarlanabilir sabitler burada.

Timeout, limit ve model sabitleri dosyalara dağılmak yerine tek yerde toplanır.
Değerler ilgili modüllerdeki eski tanımlarla BİREBİR aynıdır — davranış değişmez.
"""

from __future__ import annotations

# ── LLM timeout'ları (saniye) — brain/sovereign_brain ───────────────────────
LLM_FIRST_STEP_TIMEOUT = 10   # ilk araç seçimi — takılma sigortası
LLM_TOOL_TIMEOUT       = 10   # sonraki adımlar
LLM_FOLLOWUP_TIMEOUT   = 8    # sonuç özetleme

# ── Limitler / döngü sınırları — brain/sovereign_brain ──────────────────────
MAX_HISTORY     = 40   # tutulan max mesaj (20 çift)
MAX_HEAL_DEPTH  = 2    # hata sonrası max self-heal derinliği
MAX_AGENT_STEPS = 5    # agent döngüsü max adım sayısı
ACTION_LOG_MAX  = 20   # eylem logu max kayıt

# ── Hafıza (memory) — brain/sovereign_brain, brain/memory_bank ──────────────
MEMORY_CACHE_TTL   = 30.0                     # sovereign_brain hafıza cache TTL (sn)
MEMORY_FORMAT_TTL  = 30.0                     # memory_bank format cache TTL (sn)
EMBED_MODEL        = "nomic-embed-text:latest"  # embedding modeli

# ── Hafıza/cache TTL ve throttle (saniye) — brain/sovereign_brain ───────────
SYS_PROMPT_TTL        = 60.0
MEMORY_SAVE_THROTTLE  = 60.0
FACT_EXTRACT_THROTTLE = 20.0
PROFILE_TTL           = 120.0
BRAINPAD_TTL          = 120.0
MEMORY_Q_MAX          = 20     # int — cache'de tutulan max farklı query
BG_IDLE_DELAY         = 6.0

# ── UI ─────────────────────────────────────────────────────────────────────
# UI hafıza-paneli build cache TTL. brain retrieval cache'inden (MEMORY_CACHE_TTL)
# AYRI kavram — değer tesadüfen aynı; biri değişince diğeri kaymasın diye ayrı.
UI_BUILD_MEMORY_TTL   = 30.0

# ── Cyber araç timeout'ları (saniye) — tools/cyber_tools ────────────────────
CYBER_DNS_TIMEOUT       = 10
CYBER_WHOIS_TIMEOUT     = 10
CYBER_SUBDOMAIN_TIMEOUT = 30
CYBER_IPINFO_TIMEOUT    = 10
CYBER_NMAP_TIMEOUTS     = {"fast": 60, "full": 600, "service": 180, "vuln": 900}
CYBER_TRACEROUTE_TIMEOUT = 60
CYBER_HTTP_TIMEOUT      = 15
CYBER_SSL_TIMEOUT       = 10
CYBER_ROBOTS_TIMEOUT    = 10
CYBER_GOBUSTER_TIMEOUT  = 600
CYBER_NIKTO_TIMEOUT     = 1200   # 20 dk
CYBER_SQLMAP_TIMEOUT    = 600    # 10 dk
CYBER_HASHCRACK_TIMEOUT = 1800   # 30 dk
