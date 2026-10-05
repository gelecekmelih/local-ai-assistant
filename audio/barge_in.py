# -*- coding: utf-8 -*-
"""
audio/barge_in.py — Klargus konuşurken sözünü kesebilme (barge-in)
==================================================================
Klargus TTS ile konuşurken kullanıcı "Klargus" derse seslendirme anında durur
ve sistem dinlemeye döner. Böylece uzun cevapları beklemeden kesebilirsin.

Tasarım notları:
- Varsayılan KAPALI (settings: barge_in_enabled). Donanıma bağlı: HOPARLÖR
  kullanıyorsan Klargus kendi sesini duyabilir; kulaklıkta sorunsuz çalışır.
- Tetik yalnızca "Klargus" (+ "Klargus dur"). "dur/yeter" gibi kelimeler
  Klargus'un kendi cevabında geçebileceğinden varsayılan tetik değildir.
- TTS süresince kısa ömürlü bir Vosk tanıyıcı mikrofonu dinler; eşleşince
  text_to_speech.stop_speaking() çağrılır.
- Tüm hatalar yutulur — barge-in çökse bile normal konuşma etkilenmez.
"""

import json
import queue
import threading
import time

_BARGE_TRIGGERS = ("klargus", "kilargus", "klargus dur", "kılargus")


def is_enabled() -> bool:
    try:
        from brain.settings_manager import get_setting
        return bool(get_setting("barge_in_enabled", False))
    except Exception:
        return False


def _contains_trigger(text: str) -> bool:
    t = " ".join((text or "").lower().split())
    if not t:
        return False
    return any(trig in t for trig in _BARGE_TRIGGERS)


class BargeInMonitor:
    """TTS süresince mikrofonu dinler; 'Klargus' duyunca seslendirmeyi keser."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        self.triggered = False

    def start(self):
        if not is_enabled():
            return
        self._stop.clear()
        self.triggered = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="barge-in")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            import sounddevice as sd
            import numpy as np
            from audio.wake_word import _ensure_vosk, model as _ww_model, SAMPLE_RATE
            from audio.device_manager import get_selected_input_device
        except Exception:
            return
        try:
            if not _ensure_vosk():
                return
            from audio.wake_word import model as wm
            from vosk import KaldiRecognizer
            # Sadece tetik kelimelerine duyarlı dar gramer → TTS konuşmasına dayanıklı
            grammar = json.dumps(["klargus", "kılargus", "dur", "[unk]"], ensure_ascii=False)
            rec = KaldiRecognizer(wm, SAMPLE_RATE, grammar)
            rec.SetWords(False)
        except Exception:
            return

        q: queue.Queue = queue.Queue()

        def _cb(indata, frames, time_info, status):
            if status:
                return
            try:
                import numpy as _np
                frame = _np.asarray(indata, dtype=_np.float32)
                if frame.ndim > 1:
                    frame = frame.mean(axis=1)
                pcm = (_np.clip(frame, -1.0, 1.0) * 32767.0).astype(_np.int16).tobytes()
                q.put_nowait(pcm)
            except Exception:
                pass

        try:
            device = get_selected_input_device()
        except Exception:
            device = None

        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, blocksize=int(SAMPLE_RATE * 0.05),
                                channels=1, dtype="float32", device=device, callback=_cb):
                # İlk ~0.4s'i atla — TTS'in ilk hecesi mikrofona sızabilir
                skip_until = time.time() + 0.4
                while not self._stop.is_set():
                    try:
                        pcm = q.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if time.time() < skip_until:
                        continue
                    try:
                        if rec.AcceptWaveform(pcm):
                            txt = json.loads(rec.Result()).get("text", "")
                        else:
                            txt = json.loads(rec.PartialResult()).get("partial", "")
                    except Exception:
                        continue
                    if _contains_trigger(txt):
                        self.triggered = True
                        try:
                            from audio.text_to_speech import stop_speaking
                            stop_speaking()
                        except Exception:
                            pass
                        return
        except Exception:
            return
