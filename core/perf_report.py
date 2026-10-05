# -*- coding: utf-8 -*-
"""
core/perf_report.py — Komut başına aşama süresi raporlama
=========================================================
Her sesli/yazılı komut için STT, araç çağrıları (ekran okuma, kamera vb.),
LLM adımları ve TTS sürelerini ölçer; komut bitince tek satırlık bir
toplam döküm yazar:

    [PERF] komut="ekrana bak" toplam=5.84s
           STT (ses-metin)        : 1.21s
           araç: screen_read      : 0.42s
           LLM adım 1             : 2.30s
           LLM adım 2 (yorum)     : 1.05s
           TTS (seslendirme)      : 0.86s

Düşük maliyetli: thread-local; perf kapalıysa tüm fonksiyonlar no-op.
process_text her komutu kendi thread'inde çalıştırdığı için thread-local
raporlama aynı komuta ait tüm aşamaları (LLM + araç + TTS) doğru toplar.
"""

import threading
import time
from contextlib import contextmanager

# Raporlama açık mı? settings'ten okunur ama import döngüsü olmasın diye lazy.
_enabled_cache = None
_enabled_cache_at = 0.0
_ENABLED_TTL = 30.0

_local = threading.local()

# STT ayrı thread'de (dinleme döngüsü) ölçülür → en son komutun STT süresi
# burada saklanır ve process_text raporu başlatınca rapora eklenir.
_last_stt_seconds = 0.0
_last_stt_lock = threading.Lock()


def is_enabled() -> bool:
    global _enabled_cache, _enabled_cache_at
    now = time.monotonic()
    if _enabled_cache is not None and (now - _enabled_cache_at) < _ENABLED_TTL:
        return _enabled_cache
    try:
        from brain.settings_manager import get_setting
        _enabled_cache = bool(get_setting("perf_report_enabled", True))
    except Exception:
        _enabled_cache = True
    _enabled_cache_at = now
    return _enabled_cache


def set_last_stt(seconds: float) -> None:
    """Dinleme thread'i STT süresini buraya bırakır."""
    global _last_stt_seconds
    with _last_stt_lock:
        _last_stt_seconds = max(0.0, float(seconds))


def _pop_last_stt() -> float:
    global _last_stt_seconds
    with _last_stt_lock:
        v = _last_stt_seconds
        _last_stt_seconds = 0.0
        return v


def begin(command_text: str) -> None:
    """Bu thread için yeni bir komut raporu başlat."""
    if not is_enabled():
        _local.report = None
        return
    _local.report = {
        "label": str(command_text or "")[:80],
        "t0": time.perf_counter(),
        "stages": [],   # [(name, seconds)]
    }
    stt = _pop_last_stt()
    if stt > 0.0:
        _local.report["stages"].append(("STT (ses-metin)", stt))


def record(name: str, seconds: float) -> None:
    """Bir aşama süresini geçerli rapora ekle."""
    rep = getattr(_local, "report", None)
    if rep is None:
        return
    rep["stages"].append((str(name), max(0.0, float(seconds))))


@contextmanager
def span(name: str):
    """with span("LLM adım 1"): ... — süreyi ölçüp rapora ekler."""
    rep = getattr(_local, "report", None)
    if rep is None:
        yield
        return
    t = time.perf_counter()
    try:
        yield
    finally:
        record(name, time.perf_counter() - t)


def finish(log_fn=None) -> str:
    """Raporu kapat, dökümü döndür ve yazdır. log_fn(str) verilirse oraya da gönderir."""
    rep = getattr(_local, "report", None)
    _local.report = None
    if rep is None:
        return ""
    total = time.perf_counter() - rep["t0"]
    # STT komuttan önce olduğu için toplam = işleme + STT
    stt_extra = sum(s for n, s in rep["stages"] if n.startswith("STT"))
    grand = total + stt_extra

    width = max((len(n) for n, _ in rep["stages"]), default=10)
    lines = [f'[PERF] komut="{rep["label"]}" toplam={grand:.2f}s']
    for name, sec in rep["stages"]:
        pct = (sec / grand * 100.0) if grand > 0 else 0.0
        lines.append(f"       {name.ljust(width)} : {sec:6.2f}s  (%{pct:4.1f})")
    işleme = total - sum(s for n, s in rep["stages"] if not n.startswith("STT"))
    if işleme > 0.05:
        lines.append(f"       {'(diğer/işleme)'.ljust(width)} : {işleme:6.2f}s")
    out = "\n".join(lines)
    try:
        print(out, flush=True)
    except Exception:
        pass
    if log_fn is not None:
        try:
            log_fn(out)
        except Exception:
            pass
    _append_jsonl(rep["label"], grand, rep["stages"])
    return out


def _append_jsonl(label: str, total: float, stages: list) -> None:
    """Her komutun aşama sürelerini data/perf_log.jsonl'a ekle (sonradan analiz)."""
    try:
        import json
        import os
        from pathlib import Path
        log_path = Path(__file__).resolve().parents[1] / "data" / "perf_log.jsonl"
        record = {
            "ts": time.time(),
            "komut": label,
            "toplam": round(total, 3),
            "asamalar": {n: round(s, 3) for n, s in stages},
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        # Dosya şişmesin — 2000 satırı geçerse son 1500'ü tut
        try:
            if os.path.getsize(log_path) > 600_000:
                lines_all = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if len(lines_all) > 2000:
                    log_path.write_text("\n".join(lines_all[-1500:]) + "\n", encoding="utf-8")
        except Exception:
            pass
    except Exception:
        pass
