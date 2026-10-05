import time

import numpy as np
import sounddevice as sd

from audio.device_manager import resolve_input_device_config
from brain.settings_manager import get_setting, set_setting


SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SIZE = int(SAMPLE_RATE * FRAME_MS / 1000)


def _rms(frame) -> float:
    arr = np.asarray(frame, dtype=np.float32).flatten()
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(arr))))


def current_audio_thresholds():
    return {
        "noise_floor": float(get_setting("listener_noise_floor", 0.001) or 0.001),
        "min_rms": float(get_setting("listener_min_rms", 0.002) or 0.002),
        "confidence_threshold": float(get_setting("listener_confidence_threshold", 0.20) or 0.20),
    }


def save_audio_thresholds(noise_floor: float, min_rms: float, confidence_threshold=None):
    set_setting("listener_noise_floor", round(float(noise_floor), 6))
    set_setting("listener_min_rms", round(float(min_rms), 6))
    set_setting("command_min_rms", round(float(min_rms), 6))
    set_setting("wake_energy_rms_threshold", round(max(float(min_rms) * 0.85, float(noise_floor) * 2.0), 6))
    set_setting("wake_energy_peak_threshold", round(max(float(min_rms) * 2.2, 0.018), 6))
    if confidence_threshold is not None:
        set_setting("listener_confidence_threshold", round(float(confidence_threshold), 3))
    set_setting("listener_calibrated_at", time.strftime("%Y-%m-%d %H:%M:%S"))


def calibrate_listening(seconds=2.5):
    levels = []
    config = resolve_input_device_config(target_samplerate=SAMPLE_RATE)
    device = config.get("device")
    actual_samplerate = int(config.get("samplerate", SAMPLE_RATE) or SAMPLE_RATE)

    def callback(indata, frames, time_info, status):
        if status:
            return
        levels.append(_rms(indata))

    with sd.InputStream(
        samplerate=actual_samplerate,
        blocksize=int(actual_samplerate * FRAME_MS / 1000),
        channels=1,
        dtype="float32",
        device=device,
        callback=callback,
    ):
        time.sleep(float(seconds))

    if not levels:
        return {
            "ok": False,
            "device": device,
            "samplerate": actual_samplerate,
            "message": "Mikrofondan kalibrasyon verisi alınamadı.",
        }

    arr = np.asarray(levels, dtype=np.float32)
    median = float(np.median(arr))
    p90 = float(np.percentile(arr, 90))
    p95 = float(np.percentile(arr, 95))
    mean = float(np.mean(arr))
    std = float(np.std(arr))

    noise_floor = max(0.0012, p90)
    min_rms = max(0.0065, p95 * 2.2, mean + (std * 3.2))
    min_rms = min(0.040, min_rms)

    save_audio_thresholds(noise_floor=noise_floor, min_rms=min_rms)

    return {
        "ok": True,
        "device": device,
        "samplerate": actual_samplerate,
        "noise_floor": noise_floor,
        "min_rms": min_rms,
        "median": median,
        "p90": p90,
        "p95": p95,
        "mean": mean,
        "std": std,
        "message": (
            "Dinleme kalibrasyonu tamamlandı. "
            f"Mikrofon: {device if device is not None else 'varsayılan'} ({actual_samplerate} Hz). "
            f"Ortam seviyesi: {noise_floor:.4f}, konuşma eşiği: {min_rms:.4f}."
        ),
    }
