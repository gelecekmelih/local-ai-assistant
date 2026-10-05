# -*- coding: utf-8 -*-
"""
audio/text_to_speech.py - Klargus TTS katmani
Oncelik: Piper (yerel, Turkce) → sessiz
"""

import threading
from pathlib import Path

# os / tempfile / subprocess / winsound — ilk speak() çağrısında lazy yüklenir

_PROJECT_ROOT        = Path(__file__).resolve().parents[1]
PIPER_EXE            = str(_PROJECT_ROOT / "piper" / "piper.exe")
_PIPER_MODEL_DEFAULT = str(_PROJECT_ROOT / "tr_TR-dfki-medium.onnx")

_speak_lock = threading.Lock()
_speaking   = False
_last_spoke = 0.0


def _perf_log(msg: str) -> None:
    """[TTS_PERF] ölçüm logu — mevcut logger akışına yazar. Yalnız gözlem;
    hiçbir mantığı/davranışı etkilemez, hata olursa sessizce yutulur."""
    try:
        from core.logger import get_logger
        get_logger("tts").info(msg)
    except Exception:
        pass


def _get_piper_model() -> str:
    try:
        from brain.settings_manager import get_setting
        val = get_setting("tts_voice_model", "")
        if val:
            p = _PROJECT_ROOT / val
            return str(p) if p.exists() else _PIPER_MODEL_DEFAULT
    except Exception:
        pass
    return _PIPER_MODEL_DEFAULT


def is_speaking() -> bool:
    return _speaking


def last_spoke_at() -> float:
    return _last_spoke


def _piper_params() -> tuple:
    """Sentez kalite parametrelerini ayardan oku (length_scale, sentence_silence,
    noise_scale, noise_w)."""
    try:
        from brain.settings_manager import get_setting as _gs
        return (
            float(_gs("tts_length_scale", 1.05) or 1.05),
            float(_gs("tts_sentence_silence", 0.25) or 0.25),
            float(_gs("tts_noise_scale", 0.667) or 0.667),
            float(_gs("tts_noise_w", 0.8) or 0.8),
        )
    except Exception:
        return (1.05, 0.25, 0.667, 0.8)


# ── Kalıcı Piper süreci ───────────────────────────────────────────────────────
# Eskiden her seslendirme piper.exe'yi yeniden başlatıp ONNX modelini baştan
# yüklerdi (~630ms/cümle). Süreci açık tutunca model bellekte kalır → ~185ms.
# Takılma/çökme durumunda otomatik olarak tek-seferlik subprocess'e düşülür.
import queue as _queue

_piper_worker = None
_piper_worker_lock = threading.Lock()
_PIPER_SYNTH_TIMEOUT = 20.0   # bir cümle bu sürede gelmezse worker'ı bırak


class _PiperWorker:
    def __init__(self, model: str, params: tuple):
        import subprocess, tempfile
        self.model = model
        self.params = params
        self.outdir = tempfile.mkdtemp(prefix="klargus_piper_")
        ls, sil, ns, nw = params
        self.proc = subprocess.Popen(
            [PIPER_EXE, "--model", model, "--output_dir", self.outdir,
             "--length_scale", str(ls), "--sentence_silence", str(sil),
             "--noise_scale", str(ns), "--noise_w", str(nw)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.lock = threading.Lock()
        self.q: _queue.Queue = _queue.Queue()
        self.reader = threading.Thread(target=self._read_loop, daemon=True,
                                       name="piper-reader")
        self.reader.start()

    def _read_loop(self):
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                self.q.put(raw.decode("utf-8", errors="replace").strip())
        except Exception:
            pass

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def synth(self, text: str) -> str | None:
        import os, time
        _t0 = time.perf_counter()   # yalnız ölçüm — mantığı etkilemez
        line = " ".join(str(text or "").split())   # tek satır — newline protokolü bozar
        _n = len(line)
        if not line:
            return None
        with self.lock:
            if not self.alive():
                _perf_log(f"[TTS_PERF] synth chars={_n} ms={(time.perf_counter()-_t0)*1000:.0f} result=worker_dead")
                return None
            # Eski/bayat çıktıları temizle (önceki timeout'tan kalmış olabilir)
            while not self.q.empty():
                try: self.q.get_nowait()
                except _queue.Empty: break
            try:
                self.proc.stdin.write((line + "\n").encode("utf-8"))
                self.proc.stdin.flush()
            except Exception:
                _perf_log(f"[TTS_PERF] synth chars={_n} ms={(time.perf_counter()-_t0)*1000:.0f} result=write_failed")
                return None
            try:
                path = self.q.get(timeout=_PIPER_SYNTH_TIMEOUT)
            except _queue.Empty:
                _perf_log(f"[TTS_PERF] synth chars={_n} ms={(time.perf_counter()-_t0)*1000:.0f} result=timeout limit_ms={_PIPER_SYNTH_TIMEOUT*1000:.0f}")
                return None   # takıldı → çağıran subprocess'e düşer, worker bırakılır
        if path and os.path.exists(path) and os.path.getsize(path) > 100:
            _perf_log(f"[TTS_PERF] synth chars={_n} ms={(time.perf_counter()-_t0)*1000:.0f} result=ok")
            return path
        _perf_log(f"[TTS_PERF] synth chars={_n} ms={(time.perf_counter()-_t0)*1000:.0f} result=bad_output")
        return None

    def stop(self):
        try:
            if self.proc:
                self.proc.stdin.close()
                self.proc.terminate()
        except Exception:
            pass


def _stop_piper_worker():
    """Uygulama kapanırken kalıcı piper sürecini sonlandır (orphan kalmasın)."""
    global _piper_worker
    with _piper_worker_lock:
        if _piper_worker is not None:
            _piper_worker.stop()
            _piper_worker = None


import atexit as _atexit
_atexit.register(_stop_piper_worker)


def warm_piper() -> bool:
    """Açılışta kalıcı Piper sürecini başlat + modeli yükle (ilk cevap hızlı gelsin)."""
    try:
        piper_model = _get_piper_model()
        if not (Path(PIPER_EXE).exists() and Path(piper_model).exists()):
            return False
        w = _get_piper_worker(piper_model, _piper_params())
        if w is None:
            return False
        wav = w.synth("Hazırım.")
        if wav:
            try:
                import os as _os
                _os.remove(wav)
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


def _get_piper_worker(model: str, params: tuple):
    """Geçerli model/parametrelerle çalışan worker'ı döndür; yoksa/eskiyse kur."""
    global _piper_worker
    with _piper_worker_lock:
        w = _piper_worker
        if w is not None and w.alive() and w.model == model and w.params == params:
            return w
        # Eski worker'ı kapat (model/param değişti ya da öldü)
        if w is not None:
            w.stop()
            _piper_worker = None
        try:
            _piper_worker = _PiperWorker(model, params)
            return _piper_worker
        except Exception as e:
            print(f"[TTS] Kalıcı Piper başlatılamadı (subprocess'e düşülüyor): {e}", flush=True)
            _piper_worker = None
            return None


def _synth_piper_subprocess(text: str, piper_model: str, params: tuple) -> str | None:
    """Tek-seferlik piper.exe çağrısı (yedek yol — worker yoksa/takılırsa)."""
    import os
    import tempfile
    import subprocess
    fd, wav = tempfile.mkstemp(suffix=".wav")
    ls, sil, ns, nw = params
    try:
        os.close(fd)  # piper kendi açar, fd'yi kapat
        r = subprocess.run(
            [PIPER_EXE, "--model", piper_model, "--output_file", wav,
             "--length_scale", str(ls), "--sentence_silence", str(sil),
             "--noise_scale", str(ns), "--noise_w", str(nw)],
            input=text.encode("utf-8"),
            capture_output=True, text=False, timeout=30,
        )
        if r.returncode == 0 and os.path.exists(wav) and os.path.getsize(wav) > 100:
            return wav
    except Exception as e:
        print(f"[TTS] Piper sentez hatasi: {e}", flush=True)
    try:
        os.remove(wav)
    except Exception:
        pass
    return None


def _synth_piper_to_wav(text: str) -> str | None:
    """Metni piper ile wav dosyasına sentezler. Çalmaz — dosya yolunu döndürür.

    Önce kalıcı Piper sürecini dener (model bellekte → hızlı); başarısızsa
    tek-seferlik subprocess'e düşer. Streaming TTS bu sayede bir cümle ÇALARKEN
    sıradakini sentezleyebilir (pipeline).
    """
    # TTS normalizasyonu — ham metin Piper'a gidince Türkçe bozuluyordu:
    #   markdown (**, •, *), semboller (->, =, /), İngilizce marka adları
    #   (Spotify→spotify, Chrome→krom), kısaltmalar (LLM→el el em) düzeltilir.
    try:
        from audio.text_normalizer import normalize_tts_text
        normalized = normalize_tts_text(text)
        if normalized:
            text = normalized
    except Exception:
        pass

    piper_model = _get_piper_model()
    if not (Path(PIPER_EXE).exists() and Path(piper_model).exists()):
        print(f"[TTS] (sessiz) {text[:80]}", flush=True)
        return None

    params = _piper_params()

    # 1) Kalıcı süreç (hızlı yol)
    worker = _get_piper_worker(piper_model, params)
    if worker is not None:
        wav = worker.synth(text)
        if wav:
            return wav
        # worker başarısız/takıldı → güvenlik için bırak, subprocess'e düş
        with _piper_worker_lock:
            global _piper_worker
            if _piper_worker is worker:
                worker.stop()
                _piper_worker = None

    # 2) Tek-seferlik subprocess (yedek)
    import time as _t
    _t0 = _t.perf_counter()   # yalnız ölçüm — mantığı etkilemez
    _wav = _synth_piper_subprocess(text, piper_model, params)
    _perf_log(f"[TTS_PERF] fallback=subprocess chars={len(text or '')} ms={(_t.perf_counter()-_t0)*1000:.0f} result={'ok' if _wav else 'fail'}")
    return _wav


def _synth_edge_to_wav(text: str) -> str | None:
    """edge-tts (Microsoft neural, insansı ses) ile sentez → WAV.

    İnternet gerektirir. Başarısız olursa (çevrimdışı vb.) None döner ve
    çağıran taraf otomatik Piper'a düşer. MP3 üretir → miniaudio ile PCM'e
    çözülüp WAV yazılır (winsound çalabilsin).
    """
    import os
    import tempfile
    import asyncio
    import wave
    try:
        import edge_tts
        import miniaudio
    except Exception:
        return None

    # Neural ses İngilizce kelimeleri doğru okur → SADECE sembol temizliği
    try:
        from audio.text_normalizer import normalize_tts_text
        text = normalize_tts_text(text, light=True) or text
    except Exception:
        pass

    try:
        from brain.settings_manager import get_setting as _gs
        voice = str(_gs("tts_neural_voice", "tr-TR-AhmetNeural") or "tr-TR-AhmetNeural")
        rate = str(_gs("tts_neural_rate", "+0%") or "+0%")
    except Exception:
        voice, rate = "tr-TR-AhmetNeural", "+0%"

    fd, mp3 = tempfile.mkstemp(suffix=".mp3"); os.close(fd)
    fd2, wav = tempfile.mkstemp(suffix=".wav"); os.close(fd2)
    try:
        async def _gen():
            await edge_tts.Communicate(text, voice, rate=rate).save(mp3)
        asyncio.run(_gen())
        if not (os.path.exists(mp3) and os.path.getsize(mp3) > 100):
            return None
        dec = miniaudio.decode_file(
            mp3, output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1, sample_rate=22050,
        )
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(dec.sample_rate)
            w.writeframes(dec.samples.tobytes())
        if os.path.getsize(wav) > 100:
            return wav
    except Exception as e:
        print(f"[TTS] edge-tts hatasi (Piper'a düşülüyor): {e}", flush=True)
    finally:
        try:
            os.remove(mp3)
        except Exception:
            pass
    try:
        if os.path.exists(wav) and os.path.getsize(wav) <= 100:
            os.remove(wav)
    except Exception:
        pass
    return None


def _synth_to_wav(text: str) -> str | None:
    """TTS motoru seçici: tts_engine ayarına göre.

    'edge'/'neural' → Microsoft neural (insansı), başarısızsa Piper yedek.
    'piper' (varsayılan) → yerel Piper.
    """
    try:
        from brain.settings_manager import get_setting as _gs
        engine = str(_gs("tts_engine", "piper") or "piper").lower()
    except Exception:
        engine = "piper"

    if engine in ("edge", "neural", "edge_neural", "edge-tts"):
        wav = _synth_edge_to_wav(text)
        if wav:
            return wav
        # edge başarısız (çevrimdışı?) → Piper'a düş
    return _synth_piper_to_wav(text)


# Devam eden seslendirme bu event ile anında durdurulabilir (barge-in / "dur").
_stop_playback = threading.Event()


def stop_speaking() -> None:
    """Çalmakta olan TTS'i hemen durdur (kullanıcı sözünü kesince / 'dur')."""
    _stop_playback.set()
    try:
        import winsound
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def _play_wav(wav: str) -> None:
    """Wav dosyasını çalar ve siler.

    Bloklayan çalma (winsound) — davranış eskisiyle birebir aynı. Başka bir
    thread stop_speaking() çağırırsa PlaySound(None, SND_PURGE) bu bloklayan
    çalmayı da anında durdurur ve fonksiyon döner (barge-in / "dur").
    """
    import os
    import winsound
    _stop_playback.clear()
    try:
        winsound.PlaySound(wav, winsound.SND_FILENAME)
    except Exception as e:
        print(f"[TTS] Çalma hatasi: {e}", flush=True)
    finally:
        try:
            os.remove(wav)
        except Exception:
            pass


def _speak_piper(text: str) -> None:
    # İsim geriye dönük uyumluluk için _speak_piper; gerçekte motor seçici
    # üzerinden gider (edge-tts neural → Piper yedek).
    wav = _synth_to_wav(text)
    if wav:
        _play_wav(wav)


def speak(text: str, mode: str = "command") -> None:
    text = (text or "").strip()
    if not text:
        return

    def _run():
        global _speaking, _last_spoke
        import time
        monitor = None
        with _speak_lock:
            _speaking = True
            try:
                # Barge-in: Klargus konuşurken "Klargus" denince kesilebilsin (opsiyonel)
                try:
                    from audio.barge_in import BargeInMonitor, is_enabled
                    if is_enabled():
                        monitor = BargeInMonitor()
                        monitor.start()
                except Exception:
                    monitor = None
                _speak_piper(text)
            finally:
                if monitor is not None:
                    monitor.stop()
                _last_spoke = time.time()
                _speaking = False

    threading.Thread(target=_run, daemon=True).start()


def play_chime() -> None:
    """Kısa onay bipi — 'Dinliyorum.' TTS'inden (~1.3s) çok daha hızlı (~80ms).

    tts_chime_enabled=true iken wake onayı olarak kullanılır. _speaking
    durumunu DEĞİŞTİRMEZ; bip o kadar kısa ki mikrofon guard'ı gerekmez.
    """
    def _run():
        try:
            import winsound
            winsound.Beep(880, 80)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def tts_voice_status() -> dict:
    piper_ok = Path(PIPER_EXE).exists() and Path(_PIPER_MODEL_DEFAULT).exists()
    return {
        "engine":        "piper" if piper_ok else "silent",
        "voice":         "tr_TR-dfki-medium",
        "model_exists":  piper_ok,
        "config_exists": piper_ok,
        "model":         _PIPER_MODEL_DEFAULT,
    }
