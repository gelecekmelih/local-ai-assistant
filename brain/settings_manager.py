import json
import threading
import time
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parents[1] / "data" / "settings.json"

DEFAULT_SETTINGS = {
    # ── Sistem / Model ───────────────────────────────────────────────────────
    "autonomy_mode": "balanced",
    "model_name": "klargus-qwen",
    "ocr_enabled": True,
    "log_level": "INFO",
    "memory_level": "normal",
    "preferred_browser": "brave",
    "start_minimized": False,
    "close_to_tray": True,
    "windows_startup_enabled": False,
    "tesseract_path": "",
    "owner_name": "Kullanıcı",
    "sovereign_mode": True,
    "ollama_host": "127.0.0.1",
    "ollama_port": 11434,

    # ── İzleme (trace) ───────────────────────────────────────────────────────
    # Her isteğin uçtan uca kaydı (araç seçimi/self-heal/çok adımlı akış analizi).
    # data/traces/YYYY-MM-DD.jsonl. Read-only gözlem; davranışı etkilemez.
    "trace_enabled": True,

    # ── Oturum ──────────────────────────────────────────────────────────────
    "session_timeout": 20,
    "chat_session_timeout": 600,
    "tts_test_text": "Klargus aktif.",

    # ── STT / Dinleme ────────────────────────────────────────────────────────
    "listener_min_rms": 0.002,
    "listener_noise_floor": 0.001,
    "listener_confidence_threshold": 0.20,
    "stt_faster_whisper_model": "medium",
    "stt_device": "auto",
    "stt_compute_type": "auto",

    # ── TTS ──────────────────────────────────────────────────────────────────
    "tts_engine": "piper",
    "tts_voice_model": "tr_TR-dfki-medium.onnx",
    "tts_chime_enabled": False,
    "tts_streaming": True,
    # Barge-in: Klargus konuşurken "Klargus" denince seslendirmeyi kes (deneysel).
    # Hoparlörde kendi sesini duyabilir; kulaklıkta en iyi çalışır. Varsayılan kapalı.
    "barge_in_enabled": False,

    # ── Wake word ────────────────────────────────────────────────────────────
    "wake_engine": "vosk",
    "listen_without_wake": True,
    "wake_window_seconds": 2.0,
    "wake_check_interval": 0.12,
    "wake_transcribe_interval": 0.5,
    "wake_energy_fallback": True,
    "wake_energy_immediate": False,
    "wake_energy_rms_threshold": 0.002,
    "wake_energy_peak_threshold": 0.006,

    # ── Komut yakalama ───────────────────────────────────────────────────────
    "command_capture_mode": "fixed",
    "command_fixed_seconds": 4.5,
    "command_min_seconds": 0.3,
    "command_max_seconds": 6.0,
    "command_silence_seconds": 0.8,
    "command_start_speech_frames": 1,
    "command_end_silence_frames": 8,
    "command_min_rms": 0.0001,
    "command_noise_peak_limit": 0.0001,
    "self_listen_guard_seconds": 0.5,

    # ── Ses kimlik doğrulama ─────────────────────────────────────────────────
    "voice_auth_enabled": False,
    "voice_auth_threshold": 0.0,
    "voice_auth_strict_for_control": True,

    # ── LLM / Tool-calling ───────────────────────────────────────────────────
    "ai_decision_mode": "tool_calling",
    "tool_calling_timeout": 15,
    "tool_calling_enabled": True,
    "llm_stream_enabled": True,

    # ── Güvenlik ─────────────────────────────────────────────────────────────
    # Klargus kendi kaynak kodunu değiştirebilir mi? Varsayılan: HAYIR (salt-okuma).
    # Okuma/analiz her zaman açık; sadece self_repair/generate_tool yazma kapalı.
    "self_modify_enabled": False,
}

# ── Önbellek: disk'e her çağrıda gitmesin ────────────────────────────────────
_cache: dict | None = None
_cache_mtime: float = 0.0
_CACHE_TTL = 30.0  # saniye — set_setting sonrası cache anında sıfırlanır zaten
_settings_lock = threading.Lock()


def _file_mtime() -> float:
    try:
        return SETTINGS_FILE.stat().st_mtime
    except Exception:
        return 0.0


def load_settings() -> dict:
    global _cache, _cache_mtime
    now = time.monotonic()
    if _cache is not None and (now - _cache_mtime) < _CACHE_TTL:
        return _cache

    if not SETTINGS_FILE.exists():
        merged = DEFAULT_SETTINGS.copy()
        save_settings(merged)
        _cache = merged
        _cache_mtime = now
        return _cache

    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        merged = DEFAULT_SETTINGS.copy()
        merged.update(data)
        _cache = merged
        _cache_mtime = now
        return _cache
    except Exception:
        if _cache is not None:
            return _cache
        return DEFAULT_SETTINGS.copy()


def save_settings(data: dict):
    global _cache, _cache_mtime
    SETTINGS_FILE.parent.mkdir(exist_ok=True)
    with _settings_lock:
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)
        _cache = data.copy()
        _cache_mtime = time.monotonic()


def get_setting(key, default=None):
    return load_settings().get(key, default)


def set_setting(key, value):
    data = load_settings()
    data[key] = value
    save_settings(data)
