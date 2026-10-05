import json
import queue
import threading
import time
import unicodedata
import warnings
import collections
from difflib import SequenceMatcher
from pathlib import Path

warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")

import sounddevice as sd
import numpy as np

from audio.device_manager import resolve_input_device, resolve_input_device_config
from brain.settings_manager import get_setting

MODEL_PATH = str(Path(__file__).resolve().parents[1] / "models" / "vosk-model-small-tr-0.3")
SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)

TRIGGERS = [
    # Temel formlar
    "klargus",
    "kılargus",
    "kilargus",
    # Boşluklu varyantlar
    "kilar gus",
    "kilar guz",
    "kıl argus",
    "klar gus",
    "kil argus",
    "k largus",
    "ki largus",
    "klar goz",
    "kilar goz",
    "kilar gozum",
    "klar gozum",
    "kilar göz",
    "kılar göz",
    "klar göz",
    # Hey ile başlayanlar
    "hey klargus",
    "hey kılargus",
    "hey kilargus",
    # Kısa/yakın formlar
    "kargus",
    "largus",
    "larkus",
    "klars",
    "klar",
    # Whisper transkripsiyon hataları
    "klargüs",
    "klarguş",
    "clargus",
    "klarkus",
    "klargos",
    "klargüş",
]
MIN_SPEECH_FRAMES = 2
COOLDOWN_SECONDS = 3.0          # 1.5 → 3.0: ard arda yanlış tetiklenmeyi önler
MAX_SILENCE_RESET_FRAMES = 22
RMS_SPEECH_THRESHOLD = 120

audio_queue = queue.Queue()

# Vosk nesneleri — import anında yüklenmiyor, ilk kullanımda lazy init
_vosk_lock = threading.Lock()
vad = None
model = None
rec = None
free_rec = None

def _ensure_vosk():
    """Vosk model ve recognizer'ları gerektiğinde yükle."""
    global vad, model, rec, free_rec
    if rec is not None:
        return True
    with _vosk_lock:
        if rec is not None:
            return True
        try:
            import webrtcvad as _wvad
            from vosk import Model as _Model, KaldiRecognizer as _KR
            vad = _wvad.Vad(1)
            model = _Model(MODEL_PATH)
            rec = _KR(model, SAMPLE_RATE,
                      json.dumps(TRIGGERS + ["[unk]"], ensure_ascii=False))
            rec.SetWords(False)
            free_rec = _KR(model, SAMPLE_RATE)
            free_rec.SetWords(False)
            return True
        except Exception as e:
            print(f"[WakeWord] Vosk yüklenemedi: {e}", flush=True)
            return False

_last_trigger_time = 0.0
_last_partial_log = 0.0
_last_level_log = 0.0
_pending_command_text = ""
_pending_command_lock = threading.Lock()  # thread-safe okuma/yazma
_trigger_time_lock   = threading.Lock()  # _last_trigger_time için ayrı lock


def _get_trigger_time() -> float:
    with _trigger_time_lock:
        return _last_trigger_time


def _set_trigger_time(t: float):
    global _last_trigger_time
    with _trigger_time_lock:
        _last_trigger_time = t


def _tts_mute_active(guard_seconds: float = 0.9) -> bool:
    try:
        guard_seconds = float(get_setting("self_listen_guard_seconds", guard_seconds) or guard_seconds)
        from audio import text_to_speech as tts_state

        if tts_state.is_speaking():
            return True
        last = tts_state.last_spoke_at()
        return bool(last and (time.time() - last) < guard_seconds)
    except Exception:
        return False

# WAKE_ALIASES kaldırıldı — TRIGGERS ile birleştirildi.
# _is_trigger() artık doğrudan TRIGGERS listesini kullanır.
WAKE_ALIASES = TRIGGERS


def _callback(indata, frames, time_info, status):
    if status:
        print("Mikrofon uyarısı:", status, flush=True)
        return
    if _tts_mute_active():
        return
    audio_queue.put(bytes(indata))


def _normalize(text: str) -> str:
    text = str(text or "").lower().strip()
    # NFKD normalizasyonu ile birleşik karakterleri ayır, sonra kaldır
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Kalan Türkçe karakterleri ASCII'ye çevir
    text = text.translate(str.maketrans("ığüşöçâîû", "igusocaiu"))

    # Boşluklu varyantlar → kilargus/klargus
    text = text.replace("kıl argus",   "kilargus")
    text = text.replace("kıl arguş",   "kilargus")
    text = text.replace("kil argus",   "kilargus")
    text = text.replace("kilar gus",   "kilargus")
    text = text.replace("kilar guz",   "kilargus")
    text = text.replace("kılar gus",   "kilargus")
    text = text.replace("killer gus",  "kilargus")
    text = text.replace("kiler gus",   "kilargus")
    text = text.replace("ki largus",   "kilargus")
    text = text.replace("k largus",    "kilargus")
    text = text.replace("kilar goz",   "kilargus")
    text = text.replace("kilar gozum", "kilargus")
    text = text.replace("klar guz",    "klargus")
    text = text.replace("klar gus",    "klargus")
    text = text.replace("klar goz",    "klargus")
    text = text.replace("klar gozum",  "klargus")
    # Harf bozulmaları
    text = text.replace("klargos",  "klargus")
    # "klargus" → "klargus" no-op kaldırıldı
    text = text.replace("klarus",   "klargus")
    text = text.replace("kargus",   "klargus")
    text = text.replace("clargus",  "klargus")
    text = text.replace("klarkus",  "klargus")
    text = text.replace("larkus",   "klargus")
    text = text.replace("largus",   "klargus")
    text = text.replace("klars",    "klargus")
    # Ön ekler
    text = text.replace("hey ",     "")
    text = text.replace("merhaba ", "")
    return " ".join(text.split())


# Önceden hesaplanmış alias kümeleri — _is_trigger saniyede birkaç kez çağrılır
# (Vosk partial + Whisper pencere); her çağrıda 60 alias'ı yeniden normalize
# etmek sıcak yolda binlerce gereksiz string işlemi demekti.
_ALIASES_NORM: frozenset = frozenset(_normalize(item) for item in TRIGGERS)
_ALIASES_COMPACT: frozenset = frozenset(a.replace(" ", "") for a in _ALIASES_NORM)
_ALIASES_BY_LEN: list = sorted(_ALIASES_NORM, key=len, reverse=True)

# Türkçe kelimeler yanlışlıkla "klargus"a yakın çıkmasın
# "kargaş" (kargas) → ratio 0.77 | "karga" → ratio 0.73 → false positive riski
_WEAK_FALSE_TRIGGERS = frozenset({"argus", "lagus", "largus", "kargas", "karga", "gargas"})


def _is_trigger(text: str) -> bool:
    normalized = _normalize(text)
    compact = normalized.replace(" ", "")
    weak_false_triggers = _WEAK_FALSE_TRIGGERS

    if normalized in weak_false_triggers or compact in weak_false_triggers:
        return False

    if normalized in _ALIASES_NORM or compact in _ALIASES_COMPACT:
        return True

    words = normalized.split()
    if any(w in {"klargus", "kilargus", "kargus", "largus"} for w in words):
        return True

    for candidate in [normalized, compact] + words:
        if candidate in weak_false_triggers:
            continue
        if len(candidate) < 4:
            continue
        if SequenceMatcher(None, candidate, "klargus").ratio() >= 0.62:
            return True
        if SequenceMatcher(None, candidate, "kilargus").ratio() >= 0.62:
            return True
        if SequenceMatcher(None, candidate, "kargus").ratio() >= 0.70:
            return True

    return False


def _reset_recognizer():
    global rec, free_rec
    if not _ensure_vosk():
        return
    from vosk import KaldiRecognizer as _KR
    with _vosk_lock:
        rec = _KR(model, SAMPLE_RATE,
                  json.dumps(TRIGGERS + ["[unk]"], ensure_ascii=False))
        rec.SetWords(False)
        free_rec = _KR(model, SAMPLE_RATE)
        free_rec.SetWords(False)


def _clear_audio_queue():
    try:
        while True:
            audio_queue.get_nowait()
    except queue.Empty:
        pass


def _extract_text(raw: str, key: str) -> str:
    try:
        return json.loads(raw).get(key, "").strip().lower()
    except Exception:
        return ""


def _rms_level(data: bytes) -> int:
    """Frame RMS — numpy ile (saf Python bayt döngüsü frame başına ~480
    iterasyondu; Vosk modunda saniyede ~33 frame = boşa CPU)."""
    if not data or len(data) < 2:
        return 0
    samples = np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16).astype(np.float64)
    if samples.size == 0:
        return 0
    return int(np.sqrt(np.mean(samples * samples)))


def _speech_rms_threshold() -> int:
    try:
        min_rms = float(get_setting("listener_min_rms", 0.002) or 0.002)
        noise_floor = float(get_setting("listener_noise_floor", 0.001) or 0.001)
        dynamic = max(min_rms * 0.50, noise_floor * 1.5) * 32767.0
        return int(max(80, min(350, dynamic)))
    except Exception:
        return RMS_SPEECH_THRESHOLD


def _trigger_ready(text: str, speech_frames: int) -> bool:
    now = time.time()
    return (
        speech_frames >= MIN_SPEECH_FRAMES
        and _is_trigger(text)
        and (now - _get_trigger_time()) >= COOLDOWN_SECONDS
    )


def _extract_command_after_wake(text: str) -> str:
    normalized = _normalize(text)
    if not normalized:
        return ""

    for alias in _ALIASES_BY_LEN:   # önceden hesaplanmış, uzundan kısaya
        if normalized == alias:
            return ""
        if normalized.startswith(alias + " "):
            return normalized[len(alias):].strip(" ,:;-")
        marker = " " + alias + " "
        if marker in " " + normalized + " ":
            return normalized.split(alias, 1)[1].strip(" ,:;-")

    words = normalized.split()
    for index, word in enumerate(words):
        if word in {"klargus", "kilargus", "kargus", "largus"}:
            return " ".join(words[index + 1:]).strip()

    return ""


def is_wake_phrase(text: str) -> bool:
    return _is_trigger(text)


def extract_command_after_wake(text: str) -> str:
    return _extract_command_after_wake(text)


def pop_pending_wake_command() -> str:
    global _pending_command_text
    with _pending_command_lock:
        command = _pending_command_text
        _pending_command_text = ""
    return command


def _set_pending_wake_command(text: str) -> None:
    global _pending_command_text
    command = _extract_command_after_wake(text)
    with _pending_command_lock:
        _pending_command_text = command


def _audio_stats(audio) -> dict:
    arr = np.asarray(audio, dtype=np.float32).flatten()
    if arr.size == 0:
        return {"duration": 0.0, "rms": 0.0, "peak": 0.0}
    return {
        "duration": float(arr.size / SAMPLE_RATE),
        "rms": float(np.sqrt(np.mean(np.square(arr)))),
        "peak": float(np.max(np.abs(arr))),
    }


def _mic_open_channels(device) -> int:
    """Mikrofonu kaç kanalla açmalı — bazı WASAPI/Intel mikrofonları MONO açılınca
    SESSİZLİK veriyor; 2 kanalla açıp callback'te mono'ya indirgemek gerekiyor."""
    try:
        info = sd.query_devices(device) if device is not None else sd.query_devices(kind="input")
        return 2 if int(info.get("max_input_channels", 1) or 1) >= 2 else 1
    except Exception:
        return 1


def _resample_audio(audio, source_rate: int, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    arr = np.asarray(audio, dtype=np.float32).flatten()
    if arr.size == 0 or int(source_rate) == int(target_rate):
        return arr.astype(np.float32)
    duration = arr.size / float(source_rate)
    target_len = max(1, int(round(duration * float(target_rate))))
    source_x = np.linspace(0.0, duration, num=arr.size, endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, arr).astype(np.float32)


def _capture_fixed_window(seconds: float = 2.2, should_stop=None) -> tuple[np.ndarray | None, dict]:
    while _tts_mute_active():
        if should_stop and should_stop():
            return None, {"status": "stopped"}
        time.sleep(0.04)

    config = resolve_input_device_config(target_samplerate=SAMPLE_RATE)
    device = config.get("device")
    actual_samplerate = int(config.get("samplerate", SAMPLE_RATE) or SAMPLE_RATE)
    frames = []

    def callback(indata, frames_count, time_info, status):
        if status:
            return
        if _tts_mute_active():
            return
        frame = np.asarray(indata, dtype=np.float32)
        if frame.ndim > 1:
            frame = frame.mean(axis=1)
        else:
            frame = frame.flatten()
        if frame.size:
            frames.append(frame.copy())

    info = {
        "device": device,
        "samplerate": actual_samplerate,
        "target_samplerate": SAMPLE_RATE,
        "resample": actual_samplerate != SAMPLE_RATE,
        "window_seconds": float(seconds),
    }

    try:
        with sd.InputStream(
            samplerate=actual_samplerate,
            blocksize=max(1, int(actual_samplerate * FRAME_MS / 1000)),
            channels=_mic_open_channels(device),
            dtype="float32",
            device=device,
            callback=callback,
        ):
            deadline = time.time() + float(seconds)
            while time.time() < deadline:
                if should_stop and should_stop():
                    info["status"] = "stopped"
                    return None, info
                time.sleep(0.03)
    except Exception as exc:
        info["status"] = "open_failed"
        info["error"] = str(exc)
        return None, info

    if not frames:
        info["status"] = "no_audio"
        return None, info

    raw = np.concatenate(frames).astype(np.float32)
    audio = _resample_audio(raw, actual_samplerate, SAMPLE_RATE)
    stats = _audio_stats(audio)
    info.update(stats)
    info["status"] = "captured"
    return audio, info


def _latest_audio_from_chunks(chunks, max_samples: int) -> np.ndarray:
    if not chunks:
        return np.asarray([], dtype=np.float32)
    audio = np.concatenate(list(chunks)).astype(np.float32)
    if audio.size > max_samples:
        audio = audio[-max_samples:]
    return audio


def _transcribe_wake_audio(audio) -> dict:
    from audio.speech_to_text import get_stt

    stt = get_stt()
    transcription = stt.transcribe_audio(audio, samplerate=SAMPLE_RATE, wake_mode=True)
    if isinstance(transcription, dict):
        text = str(transcription.get("text", "") or "").strip()
        confidence = float(transcription.get("confidence", 0.0) or 0.0)
        source = str(transcription.get("source", "") or "")
        no_speech = float(transcription.get("no_speech_probability", 0.0) or 0.0)
    else:
        text = str(transcription or "").strip()
        confidence = 0.0
        source = "unknown"
        no_speech = 0.0

    normalized = _normalize(text)
    is_wake = bool(text and _is_trigger(text))
    return {
        "text": text,
        "normalized": normalized,
        "is_wake": is_wake,
        "command": _extract_command_after_wake(text) if is_wake else "",
        "confidence": confidence,
        "source": source,
        "no_speech_probability": no_speech,
    }


def test_wake_phrase(timeout: float = 5.0) -> dict:
    audio, capture = _capture_fixed_window(seconds=float(timeout), should_stop=None)
    if audio is None or len(audio) == 0:
        return {
            "ok": False,
            "status": capture.get("status", "no_speech"),
            "text": "",
            "normalized": "",
            "is_wake": False,
            "command": "",
            "capture": capture,
            "message": f"Ses yakalanamadı: {capture.get('status', 'no_speech')}",
        }

    result = _transcribe_wake_audio(audio)
    result["ok"] = True
    result["status"] = "ok" if result.get("text") else "no_text"
    result["capture"] = capture
    result["message"] = (
        f"Duyulan: {result.get('text') or '-'} | normalize={result.get('normalized') or '-'} | "
        f"wake={'evet' if result.get('is_wake') else 'hayır'} | güven={float(result.get('confidence', 0.0)):.2f} | "
        f"seviye={float(capture.get('rms', 0.0) or 0.0):.5f}, tepe={float(capture.get('peak', 0.0) or 0.0):.5f}"
    )
    return result


def _listen_for_wake_word_whisper(should_stop=None):
    global _last_trigger_time, _pending_command_text

    from audio.speech_to_text import get_stt

    config = resolve_input_device_config(target_samplerate=SAMPLE_RATE)
    device = config.get("device")
    print(
        f"Klargus Whisper wake dinlemede... mikrofon={device if device is not None else 'varsayılan'} "
        f"{config.get('samplerate')}Hz->{config.get('target_samplerate')}Hz",
        flush=True,
    )

    stt = get_stt()
    last_log = 0.0
    window_seconds = float(get_setting("wake_window_seconds", 2.2) or 2.2)
    energy_fallback = bool(get_setting("wake_energy_fallback", True))
    energy_rms_threshold = float(get_setting("wake_energy_rms_threshold", 0.0045) or 0.0045)
    energy_peak_threshold = float(get_setting("wake_energy_peak_threshold", 0.012) or 0.012)
    energy_hits = 0
    while True:
        if should_stop and should_stop():
            print("Wake word dinleme durduruldu.", flush=True)
            return False
        if _tts_mute_active():
            time.sleep(0.05)
            continue

        audio, capture = _capture_fixed_window(seconds=window_seconds, should_stop=should_stop)
        if audio is None or len(audio) == 0:
            continue

        transcription = stt.transcribe_audio(audio, samplerate=SAMPLE_RATE, wake_mode=True)
        if isinstance(transcription, dict):
            text = str(transcription.get("text", "") or "").strip()
            confidence = float(transcription.get("confidence", 0.0) or 0.0)
            source = str(transcription.get("source", "") or "")
            no_speech = float(transcription.get("no_speech_probability", 0.0) or 0.0)
        else:
            text = str(transcription or "").strip()
            confidence = 0.0
            source = "unknown"
            no_speech = 0.0

        now = time.time()
        if now - last_log >= 0.35:
            last_log = now
            print(
                f"Wake pencere: rms={float(capture.get('rms', 0.0) or 0.0):.4f} "
                f"peak={float(capture.get('peak', 0.0) or 0.0):.4f} "
                f"aday={text or '-'} kaynak={source} güven={confidence:.3f}",
                flush=True,
            )

        if not text:
            if energy_fallback:
                energetic = (
                    float(capture.get("rms", 0.0) or 0.0) >= energy_rms_threshold * 3.0
                    and float(capture.get("peak", 0.0) or 0.0) >= energy_peak_threshold
                )
                energy_hits = energy_hits + 1 if energetic else max(0, energy_hits - 1)
                if energy_hits >= 2 and (now - _get_trigger_time()) >= COOLDOWN_SECONDS:
                    _set_trigger_time(now)
                    with _pending_command_lock:
                        _pending_command_text = ""
                    energy_hits = 0
                    print("Wake word enerjiyle algılandı.", flush=True)
                    return True
            continue
        if no_speech > 0.92 and confidence < 0.20:
            continue
        # Wake word için confidence filtresini esnet
        if not _is_trigger(text):
            if source in {"free", "grammar"} and confidence < 0.35:
                continue
        if (now - _get_trigger_time()) < COOLDOWN_SECONDS:
            continue
        if _is_trigger(text):
            _set_trigger_time(now)
            _set_pending_wake_command(text)
            print("Wake word algılandı! whisper=", _normalize(text), flush=True)
            return True
        if energy_fallback:
            energetic = (
                float(capture.get("rms", 0.0) or 0.0) >= energy_rms_threshold * 3.0
                and float(capture.get("peak", 0.0) or 0.0) >= energy_peak_threshold
            )
            energy_hits = energy_hits + 1 if energetic else max(0, energy_hits - 1)
            if energy_hits >= 2 and (now - _get_trigger_time()) >= COOLDOWN_SECONDS:
                _set_trigger_time(now)
                with _pending_command_lock:
                    _pending_command_text = ""
                energy_hits = 0
                print("Wake word enerjiyle algılandı. aday=", _normalize(text), flush=True)
                return True


def _listen_for_wake_word_streaming(should_stop=None):
    """Hibrit wake word: Vosk frame-by-frame (anlık) + Whisper pencere (doğrulama).

    Neden iki motor?
    - Vosk, grammar-constrained olduğu için "klargus"-benzeri bir şey duyunca
      hemen partial result üretiyor → çok düşük gecikme.
    - Whisper, serbest metni doğru çeviriyor ama ~0.5-1 s gecikmesi var.
    İkisi paralel çalışır, ilk doğru tanıyan kazanır.
    """
    global _last_trigger_time, _pending_command_text

    import queue as _queue
    from audio.speech_to_text import get_stt

    config = resolve_input_device_config(target_samplerate=SAMPLE_RATE)
    device = config.get("device")
    actual_samplerate = int(config.get("samplerate", SAMPLE_RATE) or SAMPLE_RATE)
    print(
        f"Klargus hibrit wake dinlemede (Vosk+Whisper)... mikrofon={device if device is not None else 'varsayılan'} "
        f"{actual_samplerate}Hz->{SAMPLE_RATE}Hz",
        flush=True,
    )

    stt = get_stt()

    # ── Vosk paralel tanıyıcı kur ─────────────────────────────────────────────
    vosk_rec_local = None
    vosk_pcm_q = _queue.Queue(maxsize=300)
    if _ensure_vosk() and model is not None:
        try:
            from vosk import KaldiRecognizer as _KR
            vosk_rec_local = _KR(
                model, SAMPLE_RATE,
                json.dumps(TRIGGERS + ["[unk]"], ensure_ascii=False),
            )
            vosk_rec_local.SetWords(False)
            print("[WakeWord] Vosk paralel tanıyıcı hazır.", flush=True)
        except Exception as _ve:
            vosk_rec_local = None
            print(f"[WakeWord] Vosk paralel kurulamadı: {_ve}", flush=True)

    # ── Ayarlar ───────────────────────────────────────────────────────────────
    chunks = collections.deque(maxlen=320)
    window_seconds      = float(get_setting("wake_window_seconds",    1.5)  or 1.5)   # 2.0→1.5
    check_interval      = float(get_setting("wake_check_interval",    0.20) or 0.20)  # 0.35→0.20
    transcribe_interval = float(get_setting("wake_transcribe_interval", 0.6) or 0.6)  # 1.2→0.6
    energy_fallback     = bool(get_setting("wake_energy_fallback", True))
    energy_immediate    = bool(get_setting("wake_energy_immediate", False))
    energy_rms_threshold  = float(get_setting("wake_energy_rms_threshold",  0.0035) or 0.0035)
    energy_peak_threshold = float(get_setting("wake_energy_peak_threshold", 0.018)  or 0.018)
    last_transcribe = 0.0
    last_log = 0.0
    last_energy_wake = 0.0

    def callback(indata, frames_count, time_info, status):
        if status:
            return
        if _tts_mute_active():
            return
        frame = np.asarray(indata, dtype=np.float32)
        if frame.ndim > 1:
            frame = frame.mean(axis=1)   # 2 kanal → mono (WASAPI mic için şart)
        else:
            frame = frame.flatten()
        if frame.size == 0:
            return
        # NATIVE frame'i sakla (Whisper penceresi bütün-buffer resample edilecek —
        # per-frame resample "Klargus"u bile bozup wake'i kaçırtıyordu)
        chunks.append(frame.astype(np.float32).copy())

        # Vosk 16kHz mono PCM ister → bu frame'i tekil resample et (Vosk gramer
        # tabanlı, per-frame resample'a toleranslı)
        if vosk_rec_local is not None:
            vframe = _resample_audio(frame, actual_samplerate, SAMPLE_RATE) if actual_samplerate != SAMPLE_RATE else frame
            pcm = (np.clip(vframe, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
            try:
                vosk_pcm_q.put_nowait(pcm)
            except _queue.Full:
                pass

    try:
        with sd.InputStream(
            samplerate=actual_samplerate,
            blocksize=max(1, int(actual_samplerate * FRAME_MS / 1000)),
            channels=_mic_open_channels(device),
            dtype="float32",
            device=device,
            callback=callback,
        ):
            while True:
                if should_stop and should_stop():
                    print("Wake word dinleme durduruldu.", flush=True)
                    return False
                if _tts_mute_active():
                    chunks.clear()
                    while not vosk_pcm_q.empty():
                        try: vosk_pcm_q.get_nowait()
                        except _queue.Empty: break
                    time.sleep(0.05)
                    continue

                time.sleep(check_interval)
                now = time.time()

                # ── 1) Vosk frame-by-frame (anlık) ────────────────────────────
                if vosk_rec_local is not None:
                    while not vosk_pcm_q.empty():
                        try:
                            pcm_chunk = vosk_pcm_q.get_nowait()
                        except _queue.Empty:
                            break
                        try:
                            accepted = vosk_rec_local.AcceptWaveform(pcm_chunk)
                            if accepted:
                                vtext = _extract_text(vosk_rec_local.Result(), "text")
                            else:
                                vtext = _extract_text(vosk_rec_local.PartialResult(), "partial")
                            if vtext and _is_trigger(vtext):
                                if (now - _get_trigger_time()) >= COOLDOWN_SECONDS:
                                    _set_trigger_time(now)
                                    _set_pending_wake_command(vtext)
                                    print(f"Wake word algılandı! vosk={_normalize(vtext)}", flush=True)
                                    return True
                        except Exception:
                            pass

                # ── 2) Whisper pencere kontrolü (daha doğru ama gecikmeli) ───
                # chunks NATIVE oranda → pencereyi al, TÜMÜNÜ tek seferde 16k'ya
                # resample et (per-frame değil — "Klargus" net çıksın)
                native_window = _latest_audio_from_chunks(chunks, int(actual_samplerate * window_seconds))
                if actual_samplerate != SAMPLE_RATE and native_window.size:
                    audio = _resample_audio(native_window, actual_samplerate, SAMPLE_RATE)
                else:
                    audio = native_window
                if audio.size < int(SAMPLE_RATE * 0.30):
                    continue

                stats = _audio_stats(audio)
                rms  = float(stats.get("rms",  0.0) or 0.0)
                peak = float(stats.get("peak", 0.0) or 0.0)
                energetic = rms >= energy_rms_threshold or peak >= energy_peak_threshold

                if now - last_log >= 1.0:
                    last_log = now
                    print(f"Wake seviye: rms={rms:.4f} peak={peak:.4f}", flush=True)

                # Enerji tabanlı anında tetikleme (isteğe bağlı, varsayılan kapalı)
                if (
                    energy_fallback and energy_immediate and energetic
                    and (now - _get_trigger_time()) >= COOLDOWN_SECONDS
                    and (now - last_energy_wake) >= COOLDOWN_SECONDS
                    and rms >= energy_rms_threshold * 3.5
                ):
                    _set_trigger_time(now)
                    last_energy_wake = now
                    with _pending_command_lock:
                        _pending_command_text = ""
                    print("Wake word enerjiyle anında algılandı.", flush=True)
                    return True

                # Gerçek sessizliği atla
                if rms < 0.0003 and peak < 0.001:
                    continue
                if now - last_transcribe < transcribe_interval:
                    continue
                last_transcribe = now

                transcription = stt.transcribe_audio(audio, samplerate=SAMPLE_RATE, wake_mode=True)
                if isinstance(transcription, dict):
                    text       = str(transcription.get("text",                  "") or "").strip()
                    confidence = float(transcription.get("confidence",         0.0) or 0.0)
                    source     = str(transcription.get("source",               "") or "")
                    no_speech  = float(transcription.get("no_speech_probability", 0.0) or 0.0)
                else:
                    text = str(transcription or "").strip()
                    confidence, source, no_speech = 0.0, "unknown", 0.0

                print(
                    f"Wake whisper: {text or '-'} | kaynak={source} "
                    f"güven={confidence:.3f} sessizlik={no_speech:.3f}",
                    flush=True,
                )

                if not text:
                    continue
                if no_speech > 0.92 and confidence < 0.20:
                    continue
                normalized_candidate = _normalize(text)
                is_candidate = _is_trigger(text)
                if not is_candidate and source in {"free", "grammar"} and confidence < 0.35:
                    continue
                if (now - _get_trigger_time()) < COOLDOWN_SECONDS:
                    continue
                if is_candidate:
                    _set_trigger_time(now)
                    _set_pending_wake_command(text)
                    print(f"Wake word algılandı! whisper={normalized_candidate}", flush=True)
                    return True

    except Exception as exc:
        print(f"Streaming wake hata verdi: {exc}", flush=True)
        return _listen_for_wake_word_whisper(should_stop=should_stop)


def _listen_for_wake_word_vosk(should_stop=None):
    global _last_trigger_time, _last_partial_log, _last_level_log

    device = resolve_input_device(samplerate=SAMPLE_RATE)
    rms_threshold = _speech_rms_threshold()
    print(
        f"Klargus dinlemede... mikrofon={device if device is not None else 'varsayılan'} "
        f"eşik={rms_threshold}",
        flush=True,
    )

    speech_frames = 0
    silence_frames = 0
    _last_partial_log = 0.0
    _clear_audio_queue()
    _reset_recognizer()
    if not _ensure_vosk():
        print("[WakeWord] Vosk modeli yüklenemedi, Vosk dinleme iptal.", flush=True)
        return False

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=FRAME_SAMPLES,
        dtype="int16",
        channels=1,
        device=device,
        callback=_callback,
    ):
        while True:
            if should_stop and should_stop():
                print("Wake word dinleme durduruldu.", flush=True)
                return False
            if _tts_mute_active():
                _clear_audio_queue()
                time.sleep(0.05)
                continue

            try:
                data = audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if len(data) < FRAME_SAMPLES * 2:
                continue

            rms = _rms_level(data)
            try:
                is_speech = vad.is_speech(data, SAMPLE_RATE)
            except Exception:
                is_speech = False

            if rms >= rms_threshold:
                is_speech = True
                now_level = time.time()
                if now_level - _last_level_log >= 1.0:
                    _last_level_log = now_level
                    print("Mikrofon ses algıladı. seviye=", rms, flush=True)

            if is_speech:
                silence_frames = 0
                speech_frames += 1
                accepted = rec.AcceptWaveform(data)
                free_accepted = free_rec.AcceptWaveform(data)

                partials = [
                    _extract_text(rec.PartialResult(), "partial"),
                    _extract_text(free_rec.PartialResult(), "partial"),
                ]
                for partial in [p for p in partials if p]:
                    normalized_partial = _normalize(partial)
                    now_log = time.time()
                    if now_log - _last_partial_log >= 0.30:
                        _last_partial_log = now_log
                        print("Duyulan partial:", normalized_partial, flush=True)
                    if _trigger_ready(normalized_partial, speech_frames):
                        _set_trigger_time(time.time())
                        _reset_recognizer()
                        _set_pending_wake_command(partial)
                        print("Wake word algılandı! partial=", normalized_partial, flush=True)
                        return True

                if accepted or free_accepted:
                    result = _extract_text(rec.Result() if accepted else free_rec.Result(), "text")
                    normalized = _normalize(result)

                    if result:
                        print("Duyulan:", normalized, flush=True)

                    now = time.time()

                    if (
                        speech_frames >= MIN_SPEECH_FRAMES
                        and _is_trigger(normalized)
                        and (now - _get_trigger_time()) >= COOLDOWN_SECONDS
                    ):
                        _set_trigger_time(now)
                        _reset_recognizer()
                        print("Wake word algılandı!", flush=True)
                        return True
            else:
                silence_frames += 1
                if speech_frames > 0:
                    accepted = rec.AcceptWaveform(b"")
                    free_accepted = free_rec.AcceptWaveform(b"")
                    if accepted or free_accepted:
                        result = _extract_text(rec.Result() if accepted else free_rec.Result(), "text")
                        normalized = _normalize(result)
                        if result:
                            print("Duyulan final:", normalized, flush=True)
                        if _trigger_ready(normalized, speech_frames):
                            _set_trigger_time(time.time())
                            _reset_recognizer()
                            print("Wake word algılandı! final=", normalized, flush=True)
                            return True

                if silence_frames >= MAX_SILENCE_RESET_FRAMES:
                    speech_frames = 0
                    silence_frames = 0
                    _reset_recognizer()


def listen_for_wake_word(should_stop=None):
    engine = str(get_setting("wake_engine", "hybrid") or "hybrid").lower().strip()
    if engine in {"stream", "streaming", "energy", "hybrid"}:
        return _listen_for_wake_word_streaming(should_stop=should_stop)
    if engine in {"vosk", "legacy"}:
        return _listen_for_wake_word_vosk(should_stop=should_stop)

    try:
        return _listen_for_wake_word_whisper(should_stop=should_stop)
    except Exception as exc:
        print(f"Whisper wake hata verdi, Vosk yedeğine düşülüyor: {exc}", flush=True)
        return _listen_for_wake_word_vosk(should_stop=should_stop)
