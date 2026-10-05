import json
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import sounddevice as sd

from brain.tool_result import ToolResult
from core.safe_mode import is_safe_mode


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
AUDIO_DEVICE_FILE = DATA_DIR / "audio_device.json"


BAD_INPUT_NAME_PARTS = [
    "stereo karışımı",
    "stereo karisimi",
    "speaker",
    "hoparlör",
    "hoparlor",
    "output",
]

GOOD_INPUT_NAME_PARTS = [
    "mikrofon",
    "microphone",
    "mic",
    "dijital mikrofon",
    "microfon",
]


def _load_config() -> Dict:
    try:
        if AUDIO_DEVICE_FILE.exists():
            data = json.loads(AUDIO_DEVICE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_config(data: Dict) -> None:
    AUDIO_DEVICE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm(text: str) -> str:
    value = str(text or "").lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.translate(str.maketrans("ığüşöçâîû", "igusocaiu"))
    return " ".join(value.split())


def list_input_devices() -> List[Dict]:
    devices = []
    try:
        for index, item in enumerate(sd.query_devices()):
            max_input = int(item.get("max_input_channels", 0) or 0)
            if max_input <= 0:
                continue
            devices.append({
                "index": int(item.get("index", index)),
                "name": str(item.get("name", "")),
                "hostapi": int(item.get("hostapi", -1)),
                "max_input_channels": max_input,
                "default_samplerate": float(item.get("default_samplerate", 0.0) or 0.0),
                "low_latency": float(item.get("default_low_input_latency", 0.0) or 0.0),
            })
    except Exception:
        pass
    return devices


def get_selected_input_device() -> Optional[int]:
    data = _load_config()
    value = data.get("input_device")
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def set_input_device(index: Optional[int], source: str = "manual") -> ToolResult:
    # Güvenli mod: nereden çağrılırsa çağrılsın config/mikrofon durumu DEĞİŞMEZ.
    if is_safe_mode():
        return ToolResult(message="[test modu: mikrofon değiştirilmedi]", ok=True, data=None)
    data = _load_config()
    if index is None:
        data.pop("input_device", None)
        data["source"] = source
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_config(data)
        return ToolResult(message="Mikrofon seçimi varsayılana alındı.", ok=True, data=None)

    devices = {d["index"]: d for d in list_input_devices()}
    if int(index) not in devices:
        return ToolResult(message=f"Mikrofon bulunamadı: {index}", ok=False,
                          error="mikrofon_bulunamadi")

    open_samplerate = _openable_samplerate(devices[int(index)], target_samplerate=16000)
    if not open_samplerate:
        return ToolResult(
            message=f"Mikrofon açılabilir durumda değil: {index} - {devices[int(index)]['name']}",
            ok=False, error="mikrofon_acilamiyor")

    data["input_device"] = int(index)
    data["input_device_name"] = devices[int(index)]["name"]
    data["input_samplerate"] = int(open_samplerate)
    data["source"] = source
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save_config(data)
    invalidate_device_config_cache()
    return ToolResult(
        message=f"Mikrofon seçildi: {index} - {devices[int(index)]['name']} ({int(open_samplerate)} Hz)",
        ok=True, data=int(index))


def _device_score(device: Dict) -> int:
    name = _norm(device.get("name", ""))
    score = 0

    if any(part in name for part in BAD_INPUT_NAME_PARTS):
        score -= 100
    if any(part in name for part in GOOD_INPUT_NAME_PARTS):
        score += 40

    samplerate = float(device.get("default_samplerate", 0.0) or 0.0)
    if abs(samplerate - 16000.0) < 1:
        score += 35
    elif abs(samplerate - 48000.0) < 1:
        score += 18
    elif abs(samplerate - 44100.0) < 1:
        score += 8

    hostapi = int(device.get("hostapi", -1))
    if hostapi == 3:
        score += 18
    elif hostapi == 2:
        score += 14
    elif hostapi == 1:
        score += 8

    latency = float(device.get("low_latency", 0.0) or 0.0)
    if latency:
        score += max(0, int(20 - latency * 100))

    max_channels = int(device.get("max_input_channels", 0) or 0)
    if max_channels >= 1:
        score += 6

    return score


def _can_open_device(index: Optional[int], samplerate: int = 16000) -> bool:
    levels = []

    def callback(indata, frames, time_info, status):
        arr = np.asarray(indata, dtype=np.float32).flatten()
        if arr.size:
            levels.append(float(np.sqrt(np.mean(np.square(arr)))))

    try:
        with sd.InputStream(
            samplerate=samplerate,
            blocksize=max(1, int(samplerate * 0.03)),
            channels=1,
            dtype="float32",
            device=index,
            callback=callback,
        ):
            time.sleep(0.08)
        return True
    except Exception:
        return False


def _candidate_samplerates(device: Dict, target_samplerate: int = 16000) -> List[int]:
    values = []
    for value in [
        int(float(device.get("default_samplerate", 0.0) or 0.0)),
        48000,
        44100,
        target_samplerate,
        16000,
    ]:
        if value > 0 and value not in values:
            values.append(value)
    return values


def _openable_samplerate(device: Dict, target_samplerate: int = 16000) -> Optional[int]:
    index = int(device["index"])
    for rate in _candidate_samplerates(device, target_samplerate=target_samplerate):
        if _can_open_device(index, samplerate=rate):
            return int(rate)
    return None


def _quick_peak_level(index: int, samplerate: int, seconds: float = 0.22) -> float:
    levels = []

    def callback(indata, frames, time_info, status):
        arr = np.asarray(indata, dtype=np.float32).flatten()
        if arr.size:
            levels.append(float(np.sqrt(np.mean(np.square(arr)))))

    try:
        with sd.InputStream(
            samplerate=samplerate,
            blocksize=max(1, int(samplerate * 0.03)),
            channels=1,
            dtype="float32",
            device=index,
            callback=callback,
        ):
            time.sleep(float(seconds))
    except Exception:
        return 0.0

    if not levels:
        return 0.0
    return float(np.max(np.asarray(levels, dtype=np.float32)))


def auto_select_input_device(samplerate: int = 16000) -> ToolResult:
    # Güvenli mod: mikrofon donanımına erişip seçim YAPMAZ (yan etki yok).
    if is_safe_mode():
        return ToolResult(message="[test modu: mikrofon otomatik seçilmedi]", ok=True, data=None)
    devices = list_input_devices()
    if not devices:
        return ToolResult(message="Giriş mikrofonu bulunamadı.", ok=False,
                          error="mikrofon_yok")

    scored = []
    for device in devices:
        open_samplerate = _openable_samplerate(device, target_samplerate=samplerate)
        if not open_samplerate:
            continue
        peak = _quick_peak_level(int(device["index"]), int(open_samplerate), seconds=0.22)
        level_bonus = min(120, int(peak * 90000))
        score = _device_score(device) + level_bonus
        name = _norm(device.get("name", ""))
        if any(part in name for part in BAD_INPUT_NAME_PARTS):
            score -= 200
        if peak < 0.00002 and int(device.get("hostapi", -1)) == 2:
            score -= 45
        scored.append((score, peak, open_samplerate, device))

    for _, _, _, device in sorted(scored, key=lambda item: item[0], reverse=True):
        return set_input_device(int(device["index"]), source="auto")

    return ToolResult(message="Uygun mikrofon açılabilir durumda değil.", ok=False,
                      error="uygun_mikrofon_yok")


def resolve_input_device(samplerate: int = 16000) -> Optional[int]:
    return resolve_input_device_config(target_samplerate=samplerate).get("device")


_device_config_cache: dict = {}
_device_config_cache_at: float = 0.0
_DEVICE_CONFIG_TTL: float = 60.0  # saniye — mikrofon bağlantısı sık değişmez


def resolve_input_device_config(target_samplerate: int = 16000) -> dict:
    global _device_config_cache, _device_config_cache_at
    import time as _t
    now = _t.monotonic()
    # Cache tazeyse direkt döndür
    if (_device_config_cache
            and (now - _device_config_cache_at) < _DEVICE_CONFIG_TTL
            and _device_config_cache.get("target_samplerate") == int(target_samplerate)):
        return _device_config_cache

    data = _load_config()
    selected = get_selected_input_device()
    source = str(data.get("source", "") or "")
    devices = {int(d["index"]): d for d in list_input_devices()}
    # "auto" kaynak artık her cache dolumunda (60s) YENİDEN değerlendirilir —
    # böylece kulaklık takılınca canlı sinyali olan mikrofon seçilir.
    # "manual"/"voice" kullanıcı/ses-doğrulama kararıdır → sabit kalır.
    if selected is not None and source in {"manual", "voice"} and selected in devices:
        open_samplerate = _openable_samplerate(devices[selected], target_samplerate=target_samplerate)
        if open_samplerate:
            result = {
                "device": selected,
                "samplerate": int(open_samplerate),
                "target_samplerate": int(target_samplerate),
                "resample": int(open_samplerate) != int(target_samplerate),
                "name": devices[selected]["name"],
            }
            _device_config_cache = result
            _device_config_cache_at = now
            return result

    auto_select_input_device(samplerate=target_samplerate)
    selected = get_selected_input_device()
    devices = {int(d["index"]): d for d in list_input_devices()}
    if selected is not None and selected in devices:
        open_samplerate = _openable_samplerate(devices[selected], target_samplerate=target_samplerate)
        if open_samplerate:
            result = {
                "device": selected,
                "samplerate": int(open_samplerate),
                "target_samplerate": int(target_samplerate),
                "resample": int(open_samplerate) != int(target_samplerate),
                "name": devices[selected]["name"],
            }
            _device_config_cache = result
            _device_config_cache_at = now
            return result

    result = {
        "device": None,
        "samplerate": int(target_samplerate),
        "target_samplerate": int(target_samplerate),
        "resample": False,
        "name": "varsayılan",
    }
    _device_config_cache = result
    _device_config_cache_at = now
    return result


def invalidate_device_config_cache():
    """Cihaz değişince cache'i sıfırla."""
    global _device_config_cache_at
    _device_config_cache_at = 0.0


def input_device_status_text() -> str:
    selected = get_selected_input_device()
    devices = list_input_devices()
    selected_name = "-"
    stream_config = resolve_input_device_config(target_samplerate=16000)
    for device in devices:
        if selected is not None and int(device["index"]) == int(selected):
            selected_name = device["name"]
            break

    lines = [
        f"Seçili mikrofon: {selected if selected is not None else 'varsayılan'} - {selected_name} "
        f"| açılış={stream_config.get('samplerate')} Hz -> işlem={stream_config.get('target_samplerate')} Hz"
    ]
    lines.append("Giriş aygıtları:")
    for device in devices:
        marker = "*" if selected is not None and int(device["index"]) == int(selected) else "-"
        lines.append(
            f"{marker} {device['index']}: {device['name']} "
            f"| kanal={device['max_input_channels']} | sr={int(device['default_samplerate'])}"
        )
    return "\n".join(lines)


def measure_input_level(seconds: float = 1.2, samplerate: int = 16000, device: Optional[int] = None) -> Dict:
    actual_samplerate = samplerate
    if device is None:
        config = resolve_input_device_config(target_samplerate=samplerate)
        device = config.get("device")
        actual_samplerate = int(config.get("samplerate", samplerate) or samplerate)

    levels = []

    def callback(indata, frames, time_info, status):
        arr = np.asarray(indata, dtype=np.float32).flatten()
        if arr.size:
            levels.append(float(np.sqrt(np.mean(np.square(arr)))))

    try:
        with sd.InputStream(
            samplerate=actual_samplerate,
            blocksize=int(actual_samplerate * 0.03),
            channels=1,
            dtype="float32",
            device=device,
            callback=callback,
        ):
            time.sleep(float(seconds))
    except Exception as _e:
        return {"ok": False, "device": device, "samplerate": actual_samplerate, "message": f"Mikrofon açılamadı: {_e}"}

    if not levels:
        return {"ok": False, "device": device, "samplerate": actual_samplerate, "message": "Mikrofondan seviye alınamadı."}

    arr = np.asarray(levels, dtype=np.float32)
    return {
        "ok": True,
        "device": device,
        "samplerate": actual_samplerate,
        "median": float(np.median(arr)),
        "p90": float(np.percentile(arr, 90)),
        "peak": float(np.max(arr)),
        "message": f"Mikrofon seviyesi: medyan={float(np.median(arr)):.5f}, tepe={float(np.max(arr)):.5f}",
    }
