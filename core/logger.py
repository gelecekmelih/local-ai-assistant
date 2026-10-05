# -*- coding: utf-8 -*-
"""
core/logger.py — Merkezi loglama altyapısı

Tek bir root logger ("klargus"):
  - Konsola INFO ve üstü (eski print davranışına yakın görünürlük).
  - Dosyaya (data/logs/klargus.log) DEBUG ve üstü, RotatingFileHandler ile
    (maxBytes=2MB, backupCount=3).
Türkçe uyumlu format: zaman + seviye + modül + mesaj.

Kullanım:
    from core.logger import get_logger
    log = get_logger("memory_bank")
    log.info("ChromaDB hazır")
    log.error("kayıt hatası: %s", e)

Not: Kullanıcıya gösterilen (UI/ses) çıktılar bu altyapıya taşınmaz;
yalnızca iç tanısal/hata mesajları için kullanılır.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parents[1] / "data" / "logs"
_configured = False

# ── Hassas veri maskeleme (2026-09-29) ────────────────────────────────────────
# ÖNCEDEN: hiçbir redaction yoktu — bir yerde f"Token: {TOKEN}" ya da
# f"şifre: {pwd}" gibi bir log satırı yazılırsa data/logs/klargus.log'a DÜZ
# METİN olarak işlenirdi (rotating ama diskte kalıcı, .gitignore'da olsa da
# yerelde okunabilir). Kök-neden düzeltmesi: TÜM handler'lara giden her kaydı
# tek noktadan süzen bir logging.Filter — çağıran tarafında "bunu loglama"
# disiplinine güvenmek yerine, altyapı seviyesinde savunma.
_SENSITIVE_PATTERNS = [
    # anahtar=değer / anahtar: değer biçimleri — İSİM içinde geçen (önek/sonek
    # farketmeksizin, örn. phone_shared_key, api_token) HERHANGİ bir hassas
    # kelimeyi yakalar; \b YERİNE gevşek eşleşme kasıtlı (aşırı-maskeleme,
    # sızdırmaktan daha güvenli bir hata yönüdür).
    re.compile(r"(?i)[\w-]*(?:password|passwd|pwd|token|api[_-]?key|secret|"
               r"shared[_-]?key|access[_-]?key|şifre|sifre|parola)[\w-]*"
               r"\s*[:=]\s*['\"]?[^\s'\",}]+"),
    # HTTP başlıkları — DEĞERİN TAMAMINI (boşluk içerse bile, örn. "Bearer xxx")
    # yakala; virgül/kapanan parantez/satır sonuna kadar.
    re.compile(r"(?i)\b(authorization|x-token|x-api-key)\s*:\s*[^\n,}]+"),
    re.compile(r"(?i)\bBearer\s+[^\s,}]+"),
]


class _RedactingFilter(logging.Filter):
    """Kayıt konsola/dosyaya yazılmadan ÖNCE hassas alan değerlerini maskeler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = msg
        for pat in _SENSITIVE_PATTERNS:
            redacted = pat.sub(_mask, redacted)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def _mask(m: "re.Match") -> str:
    """Eşleşen alanın ADINI koru, DEĞERİNİ maskele (hangi alanın gizlendiği
    hâlâ teşhis için görünür kalsın, ama gerçek değer diske yazılmasın)."""
    text = m.group(0)
    for sep in (":", "="):
        if sep in text:
            head = text.split(sep, 1)[0]
            return f"{head}{sep} ***REDACTED***"
    # "Bearer xxx" gibi ayraçsız kalıplar
    head = text.split(None, 1)[0]
    return f"{head} ***REDACTED***"


def _configure() -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger("klargus")
    root.setLevel(logging.DEBUG)
    root.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
                            datefmt="%H:%M:%S")

    redactor = _RedactingFilter()

    # Konsol — INFO ve üstü
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    ch.addFilter(redactor)
    root.addHandler(ch)

    # Dosya — DEBUG ve üstü, döner (rotating)
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            _LOG_DIR / "klargus.log", maxBytes=2 * 1024 * 1024,
            backupCount=3, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        fh.addFilter(redactor)
        root.addHandler(fh)
    except Exception:
        # Dosya açılamazsa yalnızca konsola devam et (loglama kritik değil)
        pass

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """'klargus.<name>' alt-logger'ı döndür (ilk çağrıda altyapıyı kurar)."""
    _configure()
    return logging.getLogger(f"klargus.{name}")
