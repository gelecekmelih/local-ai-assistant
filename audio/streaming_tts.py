# -*- coding: utf-8 -*-
"""
Klargus Streaming TTS
=====================
LLM yanıt üretirken aynı anda seslendir.

Kullanım:
    from audio.streaming_tts import speak_stream

    # Generator veren LLM ile:
    def my_llm_gen():
        for token in ollama_stream(...):
            yield token

    speak_stream(my_llm_gen())

    # Ya da normal string ile (pipeline uyumluluğu):
    speak_stream(iter(["Merhaba ", "dünya!"]))
"""

import queue
import re
import threading
from typing import Generator, Iterator

from audio.text_to_speech import speak
from audio.text_normalizer import split_tts_sentences

# Cümle bitiş işaretleri — bu karakterleri görünce TTS'e gönder
_SENTENCE_ENDS = re.compile(r"[.!?…:;\n]")

# Minimum karakter — bu dolmadan TTS'e gönderme (çok kısa parçalar kötü ses üretir)
_MIN_CHUNK = 28

# Maksimum bekleme — bu kadar token biriktikten sonra zorla gönder
_MAX_CHUNK = 220


def _split_on_sentence(buf: str) -> tuple[str, str]:
    """
    Buffer'ı cümle sınırında ikiye böl.
    (tts_ready, remainder) döner.
    """
    m = list(_SENTENCE_ENDS.finditer(buf))
    if not m:
        return "", buf

    # Son uygun bölme noktasını bul (MIN_CHUNK'tan sonra)
    for match in reversed(m):
        pos = match.end()
        candidate = buf[:pos].strip()
        if len(candidate) >= _MIN_CHUNK:
            return candidate, buf[pos:].strip()

    # Hiçbiri yeterince uzun değilse MAX_CHUNK sınırında kes
    if len(buf) >= _MAX_CHUNK:
        return buf[:_MAX_CHUNK].strip(), buf[_MAX_CHUNK:].strip()

    return "", buf


def speak_stream(
    token_iter: Iterator[str],
    mode: str = "command",
    on_chunk: callable = None,
) -> str:
    """
    Token stream'den gelen metni cümle bazlı TTS ile seslendir.

    Args:
        token_iter: String tokenları üreten iterator (Ollama stream, vs.)
        mode: TTS modu ('command', 'cinematic', 'analytic')
        on_chunk: Her TTS chunk gönderildiğinde çağrılır: on_chunk(text)

    Returns:
        Toplam üretilen tam metin
    """
    tts_queue: queue.Queue[str | None] = queue.Queue()
    full_text_parts: list[str] = []

    # ── TTS pipeline: sentez ve çalma PARALEL ──────────────────────────────
    # Eski worker her cümleyi sentezle→çal→sentezle→çal diye seri yapıyordu;
    # piper sentezi (0.3-0.8s) cümleler arasında sessiz boşluk oluşturuyordu.
    # Şimdi: synth worker sıradaki cümleyi sentezlerken play worker öncekini
    # çalıyor — boşluklar kalkar, toplam konuşma süresi kısalır.
    # _speaking tüm pipeline boyunca True kalır → mikrofon arada açılmaz.
    import time as _time
    import audio.text_to_speech as _tts_mod

    wav_queue: queue.Queue = queue.Queue(maxsize=3)   # en fazla 3 cümle önden sentez

    def _synth_worker():
        while True:
            chunk = tts_queue.get()
            if chunk is None:  # sentinel
                wav_queue.put(None)
                break
            stripped = chunk.strip()
            # LLM hata stringlerini sesli okuma — kullanıcıya karışık gelir
            if stripped and not stripped.startswith("[LLM hata"):
                try:
                    wav = _tts_mod._synth_to_wav(stripped)
                    if wav:
                        wav_queue.put(wav)
                except Exception as e:
                    print(f"[StreamingTTS] sentez hatası: {e}", flush=True)
            tts_queue.task_done()

    def _play_worker():
        monitor = None
        with _tts_mod._speak_lock:
            _tts_mod._speaking = True
            try:
                try:
                    from audio.barge_in import BargeInMonitor, is_enabled
                    if is_enabled():
                        monitor = BargeInMonitor()
                        monitor.start()
                except Exception:
                    monitor = None
                while True:
                    wav = wav_queue.get()
                    if wav is None:
                        break
                    # Barge-in tetiklendiyse kalan cümleleri çalma, dosyaları temizle
                    if monitor is not None and monitor.triggered:
                        try:
                            import os as _os
                            _os.remove(wav)
                        except Exception:
                            pass
                        continue
                    try:
                        _tts_mod._play_wav(wav)
                    except Exception as e:
                        print(f"[StreamingTTS] çalma hatası: {e}", flush=True)
            finally:
                if monitor is not None:
                    monitor.stop()
                _tts_mod._last_spoke = _time.time()
                _tts_mod._speaking = False

    synth_thread = threading.Thread(target=_synth_worker, daemon=True)
    worker = threading.Thread(target=_play_worker, daemon=True)
    synth_thread.start()
    worker.start()

    # ── Token toplama döngüsü ──────────────────────────────────────────────
    buf = ""
    try:
        for token in token_iter:
            if not isinstance(token, str):
                token = str(token)
            buf += token
            full_text_parts.append(token)

            ready, buf = _split_on_sentence(buf)
            if ready:
                if on_chunk:
                    try:
                        on_chunk(ready)
                    except Exception:
                        pass
                tts_queue.put(ready)

        # Kalan buffer'ı gönder
        remainder = buf.strip()
        if remainder:
            if on_chunk:
                try:
                    on_chunk(remainder)
                except Exception:
                    pass
            tts_queue.put(remainder)

    except Exception as e:
        print(f"[StreamingTTS] token döngüsü hatası: {e}", flush=True)
    finally:
        tts_queue.put(None)  # worker'ı durdur

    worker.join(timeout=30)
    if worker.is_alive():
        print("[StreamingTTS] TTS worker 30s içinde bitmedi, devam ediliyor.", flush=True)
    return "".join(full_text_parts)


def speak_streamed_text(text: str, mode: str = "command") -> None:
    """
    Normal string'i cümlelere bölerek sıralı TTS ile seslendir.
    speak() yerine daha granüler kontrol için.
    """
    chunks = split_tts_sentences(text, max_len=180)
    for chunk in chunks:
        if chunk.strip():
            speak(chunk, mode=mode)

