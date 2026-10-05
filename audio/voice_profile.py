import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from audio.command_listener import CommandListener


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
VOICE_PROFILE_FILE = DATA_DIR / "voice_profile.json"

SAMPLE_RATE = 16000
FRAME_SIZE = 400
HOP_SIZE = 160
NUM_BANDS = 24
MIN_VALID_ENROLL_SAMPLES = 15
DEFAULT_ENROLL_SAMPLES   = 30   # 30x "Klargus" — wake word ile birebir eğitim
MAX_ENROLL_SAMPLES       = 30

# Sadece "Klargus" — wake word anındaki sesi öğren
ENROLLMENT_PHRASES = [
    "Klargus",   # tek kelime, wake word ile birebir eşleşme
]


def _load_json(path: Path, default):
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return default


def _save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _as_float_audio(audio) -> np.ndarray:
    arr = np.asarray(audio, dtype=np.float32).flatten()
    if arr.size == 0:
        return arr
    max_abs = float(np.max(np.abs(arr)))
    if max_abs > 1.0:
        arr = arr / max_abs
    return np.clip(arr, -1.0, 1.0)


def _frames(audio: np.ndarray):
    if audio.size < FRAME_SIZE:
        return []
    out = []
    window = np.hanning(FRAME_SIZE).astype(np.float32)
    for start in range(0, audio.size - FRAME_SIZE + 1, HOP_SIZE):
        out.append(audio[start:start + FRAME_SIZE] * window)
    return out


def _band_edges(max_bin: int) -> List[Tuple[int, int]]:
    edges = np.geomspace(2, max(3, max_bin), NUM_BANDS + 1).astype(int)
    bands = []
    for i in range(NUM_BANDS):
        a = int(edges[i])
        b = int(edges[i + 1])
        if b <= a:
            b = a + 1
        bands.append((max(1, a), min(max_bin, b)))
    return bands


def _cosine(a, b) -> float:
    av = np.asarray(a, dtype=np.float32)
    bv = np.asarray(b, dtype=np.float32)
    if av.size == 0 or bv.size == 0 or av.size != bv.size:
        return 0.0
    denom = float(np.linalg.norm(av) * np.linalg.norm(bv))
    if denom <= 0:
        return 0.0
    return float(np.dot(av, bv) / denom)


def _normalize_vector(vector) -> np.ndarray:
    arr = np.asarray(vector, dtype=np.float32).flatten()
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    norm = float(np.linalg.norm(arr))
    if norm <= 0:
        return arr
    return arr / norm


def _quality_from_embedding(emb: Dict[str, Any]) -> Dict[str, Any]:
    duration = float(emb.get("duration", 0.0) or 0.0)
    rms = float(emb.get("rms", 0.0) or 0.0)
    peak = float(emb.get("peak", 0.0) or 0.0)
    frames = int(emb.get("frames", 0) or 0)

    score = 0
    notes = []

    if duration >= 1.2:
        score += 30
    elif duration >= 0.65:
        score += 18
    else:
        notes.append("kisa_ornek")

    if 0.010 <= rms <= 0.120:
        score += 30
    elif 0.006 <= rms < 0.010:
        score += 16
        notes.append("dusuk_seviye")
    elif rms > 0.120:
        score += 12
        notes.append("yuksek_seviye")
    else:
        notes.append("cok_sessiz")

    if peak > 0.98:
        notes.append("kirpilma_riski")
    elif peak >= 0.025:
        score += 15

    if frames >= 25:
        score += 15
    elif frames >= 10:
        score += 8
    else:
        notes.append("az_konusma_karesi")

    grade = "good" if score >= 65 else "ok" if score >= 42 else "weak"
    return {
        "score": score,
        "grade": grade,
        "notes": notes,
    }


def extract_voice_embedding(audio) -> Dict[str, object]:
    arr = _as_float_audio(audio)
    if arr.size < SAMPLE_RATE * 0.35:
        return {"ok": False, "reason": "sample_too_short"}

    rms = float(np.sqrt(np.mean(np.square(arr)))) if arr.size else 0.0
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if rms < 0.006:
        return {"ok": False, "reason": "sample_too_quiet", "rms": rms, "peak": peak}

    frame_list = _frames(arr)
    if not frame_list:
        return {"ok": False, "reason": "no_frames"}

    energies = np.array([float(np.sqrt(np.mean(np.square(f)))) for f in frame_list], dtype=np.float32)
    threshold = max(0.006, float(np.percentile(energies, 45)))
    selected = [f for f, e in zip(frame_list, energies) if e >= threshold]
    if len(selected) < 8:
        selected = frame_list

    max_bin = FRAME_SIZE // 2
    bands = _band_edges(max_bin)
    freqs = np.fft.rfftfreq(FRAME_SIZE, d=1.0 / SAMPLE_RATE)

    band_vectors = []
    zcr_values = []
    centroid_values = []
    rolloff_values = []

    for frame in selected:
        spectrum = np.abs(np.fft.rfft(frame)) + 1e-8
        power = spectrum * spectrum
        total_power = float(np.sum(power))
        if total_power <= 0:
            continue

        band_vectors.append([
            math.log(float(np.mean(power[a:b])) + 1e-8)
            for a, b in bands
        ])

        signs = np.signbit(frame)
        zcr_values.append(float(np.mean(signs[:-1] != signs[1:])))

        centroid_values.append(float(np.sum(freqs * power) / total_power) / (SAMPLE_RATE / 2))

        cumulative = np.cumsum(power)
        rolloff_idx = int(np.searchsorted(cumulative, total_power * 0.85))
        rolloff_values.append(float(freqs[min(rolloff_idx, len(freqs) - 1)]) / (SAMPLE_RATE / 2))

    if not band_vectors:
        return {"ok": False, "reason": "empty_embedding"}

    bands_np = np.asarray(band_vectors, dtype=np.float32)
    band_mean = bands_np.mean(axis=0)
    band_std = bands_np.std(axis=0)

    vector = np.concatenate([
        band_mean,
        band_std,
        np.array([
            float(np.mean(zcr_values)),
            float(np.std(zcr_values)),
            float(np.mean(centroid_values)),
            float(np.std(centroid_values)),
            float(np.mean(rolloff_values)),
            float(np.std(rolloff_values)),
            rms,
        ], dtype=np.float32),
    ])

    vector = _normalize_vector(vector)
    if vector.size == 0 or float(np.linalg.norm(vector)) <= 0:
        return {"ok": False, "reason": "zero_embedding"}

    result = {
        "ok": True,
        "vector": vector.tolist(),
        "duration": float(arr.size / SAMPLE_RATE),
        "rms": rms,
        "peak": peak,
        "frames": len(selected),
    }
    result["quality"] = _quality_from_embedding(result)
    return result


def _profile_vectors(profile: Dict[str, Any]) -> List[np.ndarray]:
    vectors = []
    for item in profile.get("sample_vectors", []) or []:
        vec = _normalize_vector(item)
        if vec.size:
            vectors.append(vec)
    if not vectors and profile.get("vector"):
        vec = _normalize_vector(profile.get("vector"))
        if vec.size:
            vectors.append(vec)
    return vectors


def _profile_center(vectors: List[np.ndarray]) -> np.ndarray:
    if not vectors:
        return np.asarray([], dtype=np.float32)
    stacked = np.vstack(vectors)
    return _normalize_vector(stacked.mean(axis=0))


def _pairwise_similarities(vectors: List[np.ndarray]) -> List[float]:
    sims = []
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            sims.append(_cosine(vectors[i], vectors[j]))
    return sims


def voice_profile_status() -> Dict[str, object]:
    data = _load_json(VOICE_PROFILE_FILE, {})
    vectors = _profile_vectors(data)
    ready = bool(vectors)
    return {
        "ready": ready,
        "version": int(data.get("version", 1) or 1) if ready else 0,
        "samples": int(data.get("samples", len(vectors)) or len(vectors)),
        "threshold": float(data.get("threshold", 0.72) or 0.72),
        "strict_threshold": float(data.get("strict_threshold", data.get("threshold", 0.72)) or 0.72),
        "avg_enroll_similarity": float(data.get("avg_enroll_similarity", 0.0) or 0.0),
        "min_enroll_similarity": float(data.get("min_enroll_similarity", 0.0) or 0.0),
        "quality": data.get("quality", "unknown" if ready else "none"),
        "updated_at": data.get("updated_at", ""),
    }


def delete_voice_profile() -> bool:
    try:
        if VOICE_PROFILE_FILE.exists():
            VOICE_PROFILE_FILE.unlink()
        return True
    except Exception:
        return False


def verify_voice(audio, threshold=None, strict: bool = False) -> Dict[str, object]:
    profile = _load_json(VOICE_PROFILE_FILE, {})
    vectors = _profile_vectors(profile)
    if not vectors:
        # (2026-09-29) ÖNCEDEN "verified": True idi — profil kaydı yokken bu
        # fonksiyonu çağıran HERHANGİ bir yer (mevcut çağıranlar ayrıca kendi
        # voice_profile_status()['ready'] kontrolünü yapıyor, ama bu fonksiyonun
        # kendisi güvenlik sınırı — çağıran disiplinine güvenmemeli) fail-open
        # davranırdı: "profil yok" → "otomatik doğrulandı" gibi yorumlanabilirdi.
        # Kök neden düzeltmesi: profil yoksa DOĞRULANAMAZ (fail-closed). "ready":
        # False zaten "henüz kurulmamış" anlamına geliyordu — artık "verified" de
        # onunla tutarlı: kurulmamış bir sistem güvenli sayılmaz, aday sayılmaz.
        return {
            "ready": False,
            "verified": False,
            "similarity": 0.0,
            "threshold": threshold or 0.0,
            "confidence_level": "none",
            "is_other_speaker": False,
            "reason": "no_profile",
        }

    emb = extract_voice_embedding(audio)
    selected_threshold = float(
        threshold
        if threshold is not None
        else profile.get("strict_threshold" if strict else "threshold", 0.72)
        or 0.72
    )
    strict_threshold = float(profile.get("strict_threshold", max(0.78, selected_threshold + 0.05)) or selected_threshold)

    if not emb.get("ok"):
        return {
            "ready": True,
            "verified": False,
            "similarity": 0.0,
            "threshold": selected_threshold,
            "strict_threshold": strict_threshold,
            "confidence_level": "failed",
            "is_other_speaker": True,
            "reason": emb.get("reason", "embedding_failed"),
        }

    sample_vec = _normalize_vector(emb.get("vector"))
    center = _profile_center(vectors)
    center_similarity = _cosine(center, sample_vec)
    sample_sims = [_cosine(vec, sample_vec) for vec in vectors]
    best_sample = float(max(sample_sims)) if sample_sims else center_similarity
    mean_top = float(np.mean(sorted(sample_sims, reverse=True)[: min(3, len(sample_sims))])) if sample_sims else center_similarity
    similarity = float((center_similarity * 0.60) + (mean_top * 0.25) + (best_sample * 0.15))

    quality = emb.get("quality", {}) or {}
    quality_grade = str(quality.get("grade", "weak"))
    low_quality = quality_grade == "weak"

    verified = similarity >= selected_threshold and not low_quality
    if similarity >= strict_threshold and not low_quality:
        confidence_level = "high"
    elif verified:
        confidence_level = "medium"
    elif similarity >= selected_threshold - 0.04:
        confidence_level = "borderline"
    else:
        confidence_level = "low"

    if low_quality:
        reason = "low_quality_sample"
    elif verified:
        reason = "voice_match"
    else:
        reason = "below_threshold"

    return {
        "ready": True,
        "verified": bool(verified),
        "similarity": similarity,
        "center_similarity": center_similarity,
        "best_sample_similarity": best_sample,
        "mean_top_similarity": mean_top,
        "threshold": selected_threshold,
        "strict_threshold": strict_threshold,
        "confidence_level": confidence_level,
        "is_other_speaker": bool(similarity < selected_threshold - 0.08 or low_quality),
        "reason": reason,
        "duration": emb.get("duration", 0.0),
        "rms": emb.get("rms", 0.0),
        "peak": emb.get("peak", 0.0),
        "quality": quality,
    }


def _capture_sample(timeout=7.0):
    listener = CommandListener(timeout_seconds=timeout)
    try:
        return listener.listen(timeout=timeout)
    finally:
        try:
            listener.stop()
        except Exception:
            pass


def _call_prompt(prompt_callback, idx: int, total: int, phrase: str) -> None:
    if not prompt_callback:
        return
    try:
        prompt_callback(idx, total, phrase)
    except TypeError:
        prompt_callback(idx, total)


def _quality_label(avg_similarity: float, min_similarity: float, valid_count: int) -> str:
    # 30 örnek için kalite eşikleri
    if valid_count >= 20 and avg_similarity >= 0.82 and min_similarity >= 0.65:
        return "good"
    if valid_count >= 12 and avg_similarity >= 0.72 and min_similarity >= 0.55:
        return "ok"
    return "weak"


def enroll_voice_profile(sample_count=DEFAULT_ENROLL_SAMPLES, prompt_callback=None, log_callback=None) -> Dict[str, object]:
    try:
        sample_count = int(sample_count or DEFAULT_ENROLL_SAMPLES)
    except Exception:
        sample_count = DEFAULT_ENROLL_SAMPLES
    sample_count = max(MIN_VALID_ENROLL_SAMPLES, min(MAX_ENROLL_SAMPLES, sample_count))

    # ── Mikrofonu YENİDEN tara — kulaklık/cihaz değişimini yakala ─────────────
    # Cache eski cihazda kalıyordu (kulaklık takınca mikrofon değişmiyordu).
    # Kayıt başında canlı sinyali olan mikrofonu seçtir.
    try:
        from audio.device_manager import (
            invalidate_device_config_cache, auto_select_input_device,
        )
        invalidate_device_config_cache()
        auto_select_input_device(samplerate=SAMPLE_RATE)
        invalidate_device_config_cache()   # auto-select sonrası taze çöz
        if log_callback:
            log_callback("Mikrofon yeniden tarandı (kulaklık/cihaz değişimi için).")
    except Exception:
        pass

    embeddings: List[np.ndarray] = []
    sample_infos = []
    _reason_counts: Dict[str, int] = {}   # tanı: neden örnekler reddedildi

    for idx in range(sample_count):
        phrase = ENROLLMENT_PHRASES[idx % len(ENROLLMENT_PHRASES)]
        _call_prompt(prompt_callback, idx + 1, sample_count, phrase)
        time.sleep(0.25)

        audio = _capture_sample(timeout=3.0)   # tek kelime için 3s yeterli
        if audio is None:
            if log_callback:
                log_callback("Ses ornegi alinamadi: timeout")
            _reason_counts["timeout"] = _reason_counts.get("timeout", 0) + 1
            continue
        emb = extract_voice_embedding(audio)
        if not emb.get("ok"):
            _r = str(emb.get("reason", "bilinmiyor"))
            _reason_counts[_r] = _reason_counts.get(_r, 0) + 1
            if log_callback:
                log_callback(f"Ses ornegi alinamadi: {_r}")
            continue

        quality = emb.get("quality", {}) or {}
        if quality.get("grade") == "weak":
            if log_callback:
                log_callback(f"Ses ornegi zayif kaliteyle alindi: {quality.get('notes', [])}")

        embeddings.append(np.asarray(emb["vector"], dtype=np.float32))
        sample_infos.append({
            "duration": emb.get("duration"),
            "rms": emb.get("rms"),
            "peak": emb.get("peak"),
            "frames": emb.get("frames"),
            "quality": quality,
            "phrase": phrase,
        })
        if log_callback:
            log_callback(f"Ses ornegi {idx + 1}/{sample_count} alindi. Kalite: {quality.get('grade', '-')}")

    if len(embeddings) < MIN_VALID_ENROLL_SAMPLES:
        # Tanısal mesaj — kullanıcı NEDEN başarısız olduğunu bilsin
        _silent = _reason_counts.get("timeout", 0) + _reason_counts.get("no_frames", 0) + \
                  _reason_counts.get("sample_too_short", 0)
        _quiet = _reason_counts.get("sample_too_quiet", 0)
        if _silent >= _quiet and _silent > 0:
            _hint = ("Mikrofon ses almıyor gibi. Doğru mikrofon seçili mi? "
                     "Kulaklık yeni taktıysan, mikrofonunun varsayılan giriş olduğundan emin ol.")
        elif _quiet > 0:
            _hint = "Ses çok kısık geldi. Mikrofona daha yakın ve net konuş."
        else:
            _hint = "Her uyarıda net şekilde 'Klargus' de ve uyarıyı bekle."
        return {
            "ok": False,
            "message": (
                f"Ses profili oluşturulamadı ({len(embeddings)}/{MIN_VALID_ENROLL_SAMPLES} temiz örnek). "
                + _hint
            ),
            "samples": len(embeddings),
            "reasons": _reason_counts,
        }

    center = _profile_center(embeddings)
    initial_sims = [_cosine(center, emb) for emb in embeddings]
    median_sim = float(np.median(initial_sims)) if initial_sims else 0.0

    kept_embeddings = []
    kept_infos = []
    for emb, info, sim in zip(embeddings, sample_infos, initial_sims):
        if sim >= max(0.55, median_sim - 0.14):
            kept_embeddings.append(emb)
            info = dict(info)
            info["center_similarity"] = sim
            kept_infos.append(info)
        elif log_callback:
            log_callback(f"Tutarsiz ses ornegi elendi. Benzerlik: {sim:.3f}")

    if len(kept_embeddings) < MIN_VALID_ENROLL_SAMPLES:
        kept_embeddings = embeddings
        kept_infos = sample_infos

    center = _profile_center(kept_embeddings)
    enroll_sims = [_cosine(center, emb) for emb in kept_embeddings]
    pairwise = _pairwise_similarities(kept_embeddings)
    avg_similarity = float(np.mean(enroll_sims)) if enroll_sims else 0.0
    min_similarity = float(np.min(enroll_sims)) if enroll_sims else 0.0
    std_similarity = float(np.std(enroll_sims)) if enroll_sims else 0.0
    avg_pairwise = float(np.mean(pairwise)) if pairwise else avg_similarity

    threshold = max(0.68, min(0.84, avg_similarity - max(0.055, std_similarity * 1.25)))
    strict_threshold = max(threshold + 0.035, min(0.90, avg_similarity - max(0.015, std_similarity * 0.55)))
    quality = _quality_label(avg_similarity, min_similarity, len(kept_embeddings))

    profile = {
        "version": 2,
        "samples": len(kept_embeddings),
        "requested_samples": sample_count,
        "vector": center.tolist(),
        "sample_vectors": [emb.tolist() for emb in kept_embeddings],
        "threshold": threshold,
        "strict_threshold": strict_threshold,
        "avg_enroll_similarity": avg_similarity,
        "min_enroll_similarity": min_similarity,
        "std_enroll_similarity": std_similarity,
        "avg_pairwise_similarity": avg_pairwise,
        "quality": quality,
        "sample_infos": kept_infos,
        "phrases": ENROLLMENT_PHRASES[:sample_count],
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_json(VOICE_PROFILE_FILE, profile)

    return {
        "ok": True,
        "message": (
            f"Ses profiliniz olusturuldu. Ornek sayisi: {len(kept_embeddings)}, "
            f"kalite: {quality}, esik: {threshold:.2f}, strict esik: {strict_threshold:.2f}."
        ),
        "samples": len(kept_embeddings),
        "threshold": threshold,
        "strict_threshold": strict_threshold,
        "avg_similarity": avg_similarity,
        "min_similarity": min_similarity,
        "quality": quality,
    }
