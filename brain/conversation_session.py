import re
import threading
import time

from brain.settings_manager import get_setting


_chat_active = False
_chat_started_at = 0.0
_chat_last_turn_at = 0.0
_chat_turn_count = 0
_session_lock = threading.Lock()


def _norm(text: str) -> str:
    value = str(text or "").lower().strip()
    value = value.translate(str.maketrans({  # type: ignore[arg-type]  # tek-karakter str->str dict runtime'da geçerli
        "ı": "i",
        "ğ": "g",
        "ü": "u",
        "ş": "s",
        "ö": "o",
        "ç": "c",
        "â": "a",
        "î": "i",
        "û": "u",
    }))
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _idle_timeout() -> float:
    try:
        return float(get_setting("chat_session_timeout", 600) or 600)
    except Exception:
        return 600.0


def start_chat_session() -> None:
    global _chat_active, _chat_started_at, _chat_last_turn_at, _chat_turn_count
    now = time.time()
    with _session_lock:
        _chat_active = True
        _chat_started_at = now
        _chat_last_turn_at = now
        _chat_turn_count = 0


def refresh_chat_session() -> None:
    global _chat_last_turn_at, _chat_turn_count
    with _session_lock:
        if _chat_active:
            _chat_last_turn_at = time.time()
            _chat_turn_count += 1


def end_chat_session() -> None:
    global _chat_active, _chat_started_at, _chat_last_turn_at, _chat_turn_count
    with _session_lock:
        _chat_active = False
        _chat_started_at = 0.0
        _chat_last_turn_at = 0.0
        _chat_turn_count = 0


def chat_session_active() -> bool:
    with _session_lock:
        if not _chat_active:
            return False
        if _chat_last_turn_at and (time.time() - _chat_last_turn_at) > _idle_timeout():
            pass  # will end below
        else:
            return True
    # idle timeout detected — end session outside lock to avoid reentrance
    end_chat_session()
    return False


def chat_status() -> dict:
    return {
        "active": chat_session_active(),
        "started_at": _chat_started_at,
        "last_turn_at": _chat_last_turn_at,
        "turn_count": _chat_turn_count,
        "idle_timeout": _idle_timeout(),
    }


def is_chat_start_request(text: str) -> bool:
    norm = _norm(text)
    start_phrases = [
        "benimle biraz sohbet et",
        "benimle sohbet et",
        "sohbet edelim",
        "biraz sohbet edelim",
        "konusalim",
        "benimle konus",
        "sohbet modu ac",
        "sohbet modunu ac",
    ]
    return any(phrase in norm for phrase in start_phrases)


def is_chat_end_request(text: str) -> bool:
    norm = _norm(text)
    if not norm:
        return False

    direct_end_phrases = [
        "sohbeti bitir",
        "sohbeti kapat",
        "sohbet modunu kapat",
        "konusmayi bitir",
        "konusmayi kapat",
        "oturumu bitir",
        "uyku moduna gec",
        "bekleme moduna gec",
        "dinlemeyi bitir",
    ]
    if any(phrase in norm for phrase in direct_end_phrases):
        return True

    closing_phrases = [
        "tamam bu kadar",
        "bu kadar yeter",
        "simdilik bu kadar",
        "hadi gorusuruz",
        "gorusuruz",
        "sonra konusuruz",
        "tesekkurler bu kadar",
        "sag ol bu kadar",
        "sagol bu kadar",
        "tamamdir gorusuruz",
    ]
    if any(phrase in norm for phrase in closing_phrases):
        return True

    words = set(norm.split())
    if {"gorusuruz", "hadi"} & words and len(words) <= 4:
        return True
    if "hoscakal" in words or "hosca" in words:
        return True
    return False
