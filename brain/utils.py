# -*- coding: utf-8 -*-
"""brain/utils.py — Küçük yardımcı fonksiyonlar (sadece app.py kullanır)."""

import json
import time
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "data"
_DATA.mkdir(exist_ok=True)

# ── Pencere başlığı normalizasyonu ────────────────────────────────────────────

def normalize_window_title(title: str) -> str:
    text = (title or "").strip()
    if not text:
        return "unknown"
    low = text.lower()
    if "brave" in low:       return "brave - " + text
    if "chrome" in low:      return "browser - " + text
    if "discord" in low:     return "discord - " + text
    if "spotify" in low:     return "spotify - " + text
    if "explorer" in low or "dosya gezgini" in low or "file explorer" in low:
        return "explorer - " + text
    return text


# ── Görev geçmişi görünümü ────────────────────────────────────────────────────

def get_recent_tasks(limit: int = 10) -> str:
    from brain.task_history import load_history
    history = load_history()[-limit:][::-1]
    if not history:
        return "Henüz kayıtlı görev yok."
    lines = []
    for i, item in enumerate(history, start=1):
        lines.append(
            f"{i}. [{item.get('time', '-')}] {item.get('input', '-')}"
            f" -> {item.get('action', '-')} ({item.get('arg', '-')})"
        )
    return "\n".join(lines)


# ── Bağlam durumu (context.json) ──────────────────────────────────────────────

_CTX_FILE = _DATA / "context.json"


def set_context(key: str, value) -> None:
    data = {}
    if _CTX_FILE.exists():
        try:
            data = json.loads(_CTX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data[key] = value
    _CTX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_context(key: str):
    if not _CTX_FILE.exists():
        return None
    try:
        return json.loads(_CTX_FILE.read_text(encoding="utf-8")).get(key)
    except Exception:
        return None


# ── Oturum yönetimi ───────────────────────────────────────────────────────────

_last_session_time: float = 0.0


def _session_timeout() -> int:
    try:
        from brain.settings_manager import get_setting
        return int(get_setting("session_timeout", 20))
    except Exception:
        return 20


def start_session() -> None:
    global _last_session_time
    _last_session_time = time.time()


def refresh_session() -> None:
    global _last_session_time
    _last_session_time = time.time()


def session_active() -> bool:
    if _last_session_time == 0:
        return False
    return (time.time() - _last_session_time) <= _session_timeout()


def end_session() -> None:
    global _last_session_time
    _last_session_time = 0.0
