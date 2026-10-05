import json
import os
import re
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

# ── CUDA 12 DLL yolu — Ollama ile birlikte geliyor ───────────────────────────
# faster-whisper/ctranslate2, cublas64_12.dll'i PATH'te arar.
# Ollama kendi CUDA 12 runtime'ını getiriyor — oradan kullan.
_OLLAMA_CUDA_PATH = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "lib" / "ollama" / "cuda_v12"
if _OLLAMA_CUDA_PATH.exists():
    os.environ["PATH"] = str(_OLLAMA_CUDA_PATH) + os.pathsep + os.environ.get("PATH", "")
try:
    from vosk import Model, KaldiRecognizer
except Exception as _vosk_import_err:
    print(f"[STT] Vosk yüklenemedi: {_vosk_import_err}", flush=True)
    Model = None
    KaldiRecognizer = None

from audio.command_listener import CommandListener
from audio.device_manager import get_selected_input_device, resolve_input_device_config
from audio.text_normalizer import normalize_command_text, normalize_whisper_text
from audio import text_to_speech as tts_state
from brain.settings_manager import get_setting

try:
    from faster_whisper import WhisperModel
except Exception:
    WhisperModel = None

try:
    import ctranslate2
except Exception:
    ctranslate2 = None

try:
    from audio.voice_profile import verify_voice, voice_profile_status
except Exception:
    verify_voice = None
    voice_profile_status = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VOSK_MODEL_PATH = PROJECT_ROOT / "models" / "vosk-model-small-tr-0.3"
WHISPER_CPP_MODEL_PATH = PROJECT_ROOT / "ggml-large-v3-turbo.bin"
WHISPER_CPP_EXE_CANDIDATES = [
    PROJECT_ROOT / "whisper.cpp" / "main.exe",
    PROJECT_ROOT / "whisper.cpp" / "whisper-cli.exe",
    PROJECT_ROOT / "whisper.cpp" / "build" / "bin" / "Release" / "whisper-cli.exe",
    PROJECT_ROOT / "whisper.cpp" / "build" / "bin" / "Release" / "main.exe",
    PROJECT_ROOT / "whisper-cli.exe",
]

COMMAND_GRAMMAR = [
    "klargus",
    "kılargus",
    "ekranı oku",
    "ekranı yorumla",
    "ekrana bak",
    "ekranda ne var",
    "ekrandan ne var",
    "ekrandan ne var yorumla",
    "burada ne var",
    "ne görüyorsun",
    "spotify aç",
    "steam aç",
    "brave aç",
    "chrome aç",
    "discord aç",
    "whatsapp aç",
    "not defteri aç",
    "hesap makinesi aç",
    "internette ara",
    "internette arat",
    "google da ara",
    "arama kutusuna yaz",
    "şunu yaz",
    "enter",
    "tab",
    "kaydet",
    "kopyala",
    "yapıştır",
    "aşağı kaydır",
    "yukarı kaydır",
    "tekrar dene",
    "devam et",
    "oturumu bitir",
    "jarvis deneyimi",
    "sistem duruşu",
    "neler yapabiliyorsun",
    "beni tanıyor musun",
    "beni taniyor musun",
    "öğrenme durumun",
    "ogrenme durumun",
    "sesimi tanı",
    "ses durumunu göster",
    "dinlemeyi kalibre et",
    "mikrofonu kalibre et",
    "mikrofonları listele",
    "mikrofonlari listele",
    "mikrofon durumu",
    "mikrofon otomatik seç",
    "mikrofon otomatik sec",
    "mikrofon seç",
    "mikrofon sec",
    "mikrofon seviyesini ölç",
    "mikrofon seviyesini olc",
    "ses kilidini aç",
    "ses kilidini kapat",
    # Sistem kontrol komutları
    "ses seviyesi kaç",
    "sesi kapat",
    "sesi aç",
    "karanlık mod aç",
    "açık mod aç",
    "gece ışığını aç",
    "parlaklık kaç",
    "bilgisayarı kilitle",
    "bilgisayarı uyut",
    "bilgisayarı kapat",
    "yeniden başlat",
    "wifi durumu",
    "bluetooth aç",
    "bluetooth kapat",
    "pil durumu",
    "sistem bilgisi",
    "disk bilgisi",
    "windows ayarlarını aç",
    "monitörü kapat",
]


class SpeechToText:
    def __init__(self, samplerate=16000):
        self.samplerate = samplerate
        if Model is not None and VOSK_MODEL_PATH.exists():
            try:
                self.model = Model(str(VOSK_MODEL_PATH))
            except Exception as _e:
                print(f"[STT] Vosk modeli yüklenemedi: {_e}", flush=True)
                self.model = None
        else:
            if Model is None:
                print("[STT] Vosk kurulu değil — Whisper kullanılacak.", flush=True)
            else:
                print(f"[STT] Vosk model yolu bulunamadı: {VOSK_MODEL_PATH}", flush=True)
            self.model = None
        self.whisper_exe = self._find_whisper_cpp()
        self.fast_whisper_error = ""
        self.fast_whisper_model_name = str(get_setting("stt_faster_whisper_model", "medium") or "medium")
        self.fast_whisper_device = ""
        self.fast_whisper_compute_type = ""
        self.fast_whisper = self._load_faster_whisper()

    def _find_whisper_cpp(self):
        for candidate in WHISPER_CPP_EXE_CANDIDATES:
            try:
                if candidate.exists():
                    return candidate
            except Exception:
                pass
        return None

    def _load_faster_whisper(self):
        if WhisperModel is None:
            self.fast_whisper_error = "faster-whisper paketi bulunamadı."
            return None

        requested_device = str(get_setting("stt_device", "auto") or "auto").lower().strip()
        cuda_ready = False
        if ctranslate2 is not None:
            try:
                cuda_ready = ctranslate2.get_cuda_device_count() > 0
            except Exception:
                cuda_ready = False

        device = "cuda" if requested_device in {"auto", "cuda"} and cuda_ready else "cpu"
        requested_compute = str(get_setting("stt_compute_type", "auto") or "auto").lower().strip()
        compute_type = "float16" if requested_compute == "auto" and device == "cuda" else requested_compute
        if requested_compute == "auto" and device == "cpu":
            compute_type = "int8"

        configured_model = self.fast_whisper_model_name
        if configured_model.lower() == "auto":
            candidates = ["small", "medium", "base"]
        else:
            # NOT: Eskiden small+CUDA sessizce medium'a yükseltiliyordu.
            # KALDIRILDI — medium ~1.5GB VRAM yiyor ve 8GB kartta LLM'i
            # sistem RAM'ine taşırıyordu (LLM 10x yavaşlıyordu).
            # Ayarlardaki model neyse o kullanılır.
            candidates = [configured_model]
            for fallback in ["medium", "base", "tiny"]:
                if fallback not in candidates:
                    candidates.append(fallback)

        # i7-13700H: 6P + 8E = 14 çekirdek. CPU fallback'te max thread kullan.
        import os as _os
        _cpu_threads = min(int(_os.cpu_count() or 4), 8)   # max 8 thread yeterli

        errors = []
        for model_name in candidates:
            try:
                model = WhisperModel(
                    model_name,
                    device=device,
                    compute_type=compute_type,
                    local_files_only=True,
                    cpu_threads=_cpu_threads if device == "cpu" else 4,
                    num_workers=2,  # paralel decode worker
                )
                self.fast_whisper_model_name = model_name
                self.fast_whisper_device = device
                self.fast_whisper_compute_type = compute_type
                self.fast_whisper_error = ""
                return model
            except Exception as exc:
                errors.append(f"{model_name}/{device}/{compute_type}: {exc}")

        if device == "cuda":
            for model_name in candidates:
                try:
                    model = WhisperModel(
                        model_name,
                        device="cpu",
                        compute_type="int8",
                        local_files_only=True,
                    )
                    self.fast_whisper_model_name = model_name
                    self.fast_whisper_device = "cpu"
                    self.fast_whisper_compute_type = "int8"
                    self.fast_whisper_error = ""
                    return model
                except Exception as exc:
                    errors.append(f"{model_name}/cpu/int8: {exc}")

        self.fast_whisper_error = " | ".join(errors[-4:]) if errors else "faster-whisper yüklenemedi."
        return None

    def engine_status(self):
        whisper_ready = bool(self.whisper_exe and WHISPER_CPP_MODEL_PATH.exists())
        faster_ready = bool(self.fast_whisper)
        return {
            "active": "faster_whisper" if faster_ready else ("whisper_cpp" if whisper_ready else "vosk"),
            "input_device": get_selected_input_device(),
            "faster_whisper_ready": faster_ready,
            "faster_whisper_model": self.fast_whisper_model_name if faster_ready else "",
            "faster_whisper_device": self.fast_whisper_device,
            "faster_whisper_compute_type": self.fast_whisper_compute_type,
            "faster_whisper_error": self.fast_whisper_error,
            "whisper_cpp_ready": whisper_ready,
            "whisper_exe": str(self.whisper_exe) if self.whisper_exe else "",
            "whisper_model": str(WHISPER_CPP_MODEL_PATH) if WHISPER_CPP_MODEL_PATH.exists() else "",
            "vosk_model": str(VOSK_MODEL_PATH),
        }

    def _to_pcm16(self, audio_array):
        audio = np.array(audio_array, dtype=np.float32).flatten()
        if audio.size == 0:
            return b""

        max_abs = np.max(np.abs(audio)) if audio.size else 0.0
        if max_abs > 1.0:
            audio = audio / max_abs

        audio = np.clip(audio, -1.0, 1.0)
        return (audio * 32767.0).astype(np.int16).tobytes()

    def _recognize_with(self, pcm: bytes, samplerate: int, grammar=None):
        if self.model is None or KaldiRecognizer is None:
            return {"text": "", "confidence": 0.0, "words": 0}
        if grammar:
            recognizer = KaldiRecognizer(
                self.model,
                samplerate,
                json.dumps(grammar + ["[unk]"], ensure_ascii=False),
            )
        else:
            recognizer = KaldiRecognizer(self.model, samplerate)

        recognizer.SetWords(True)
        recognizer.AcceptWaveform(pcm)

        try:
            data = json.loads(recognizer.FinalResult())
        except Exception:
            data = {}

        text = normalize_command_text(str(data.get("text", "")).strip())
        words = data.get("result", []) if isinstance(data, dict) else []
        confidences = []
        for item in words:
            try:
                confidences.append(float(item.get("conf", 0.0)))
            except Exception:
                pass

        avg_confidence = float(np.mean(confidences)) if confidences else 0.0
        return {
            "text": " ".join(text.split()),
            "confidence": avg_confidence,
            "words": len(words),
        }

    def _write_temp_wav(self, audio_array, samplerate):
        audio = np.asarray(audio_array, dtype=np.float32).flatten()
        if audio.size == 0:
            return None

        max_abs = float(np.max(np.abs(audio))) if audio.size else 0.0
        if max_abs > 1.0:
            audio = audio / max_abs
        pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)

        tmp = tempfile.NamedTemporaryFile(prefix="klargus_cmd_", suffix=".wav", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()

        with wave.open(str(tmp_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(samplerate))
            wf.writeframes(pcm16.tobytes())

        return tmp_path

    def _clean_whisper_output(self, output):
        lines = []
        for line in str(output or "").splitlines():
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"^\[[^\]]+\]\s*", "", line).strip()
            if line and not line.lower().startswith(("whisper", "system_info", "main:")):
                lines.append(line)
        text = " ".join(lines)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _transcribe_faster_whisper(self, audio_array, samplerate, wake_mode=False):
        if self.fast_whisper is None:
            return None

        audio = np.asarray(audio_array, dtype=np.float32).flatten()
        if audio.size == 0:
            return None

        try:
            max_abs = float(np.max(np.abs(audio))) if audio.size else 0.0
            if max_abs > 1.0:
                audio = audio / max_abs
            elif 0.004 < max_abs < 0.35:
                # Düşük kazançlı mikrofon (rms ~0.001 ölçüldü) → ses Whisper için
                # çok cılız, halüsinasyona yol açıyor. Gerçek-ama-zayıf sesi
                # uygun seviyeye yükselt (saf sessizlik <0.004 dokunulmaz).
                gain = min(0.45 / max_abs, 25.0)
                audio = np.clip(audio * gain, -1.0, 1.0)

            # wake_mode=True: Klargus gibi kısa/bilinmeyen kelimeler için
            # no_speech_threshold çok düşürülür — Whisper'ın "sessizlik" diye
            # geçmesini engeller.
            no_speech_thr = 0.30 if wake_mode else 0.55
            log_prob_thr  = -2.0 if wake_mode else -1.2

            # wake_mode → temperature=0 (kısa kelime tespiti için deterministik)
            # normal mod → tek temperature fallback: 0 → 0.2 (3'lü liste her başarısız
            # decode'da tam yeniden çeviri demekti — en kötü durumda 3x süre)
            _temperature = 0.0 if wake_mode else [0.0, 0.2]

            # Hız/kalite dengesi: beam 5 + patience 1.2, beam 10 + patience 2'ye çok
            # yakın doğruluk verir ama ~2x hızlı (medium model + CUDA ölçümlerine göre).
            # wake: her 0.6s'de bir çağrılıyor — beam 3 GPU'yu boğmadan yeterli.
            _beam_size = 3  if wake_mode else 5
            _patience  = 1.0 if wake_mode else 1.2
            _best_of   = 1  if wake_mode else 2

            segments, info = self.fast_whisper.transcribe(
                audio,
                language="tr",
                task="transcribe",
                beam_size=_beam_size,
                best_of=_best_of,
                patience=_patience,
                temperature=_temperature,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={
                    "min_speech_duration_ms": 80,    # 150→80: "evet/hayır" gibi kısa yanıtlar kaybolmasın
                    "min_silence_duration_ms": 200,  # 300→200: daha hızlı segment algılama
                },
                no_speech_threshold=no_speech_thr,
                log_prob_threshold=log_prob_thr,
                compression_ratio_threshold=2.4,
                initial_prompt=("Klargus." if wake_mode else _build_initial_prompt()),
                hotwords=_build_hotwords(),
            )

            parts = []
            avg_log_probs = []
            no_speech_probs = []
            for segment in segments:
                seg_text = str(getattr(segment, "text", "") or "").strip()
                if seg_text:
                    parts.append(seg_text)
                try:
                    avg_log_probs.append(float(getattr(segment, "avg_logprob", 0.0)))
                    no_speech_probs.append(float(getattr(segment, "no_speech_prob", 0.0)))
                except Exception:
                    pass

            text = normalize_whisper_text(" ".join(parts))
            if not text:
                return None

            avg_logprob = float(np.mean(avg_log_probs)) if avg_log_probs else -0.4
            no_speech = float(np.mean(no_speech_probs)) if no_speech_probs else 0.0
            confidence = max(0.0, min(0.96, 1.0 + (avg_logprob / 2.0)))
            # wake_mode'da kısa kelime cezası uygulanmaz:
            # "Klargus" tek kelimedir ve no_speech > 0.72 olabilir ama bu
            # gerçekten sessizlik değil, bilinmeyen kelime olduğu için.
            if not wake_mode and no_speech > 0.72 and len(text.split()) <= 2:
                confidence = min(confidence, 0.12)

            return {
                "text": text,
                "confidence": confidence,
                "words": len(text.split()),
                "source": "faster_whisper",
                "language": getattr(info, "language", "tr"),
                "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
                "no_speech_probability": no_speech,
            }
        except Exception as exc:
            self.fast_whisper_error = str(exc)
            return None

    def _transcribe_whisper_cpp(self, audio_array, samplerate):
        if not self.whisper_exe or not WHISPER_CPP_MODEL_PATH.exists():
            return None

        wav_path = self._write_temp_wav(audio_array, samplerate)
        if not wav_path:
            return None

        try:
            cmd = [
                str(self.whisper_exe),
                "-m",
                str(WHISPER_CPP_MODEL_PATH),
                "-f",
                str(wav_path),
                "-l",
                "tr",
                "-nt",
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=35,
                shell=False,
            )
            combined = "\n".join([proc.stdout or "", proc.stderr or ""])
            text = self._clean_whisper_output(combined)
            if not text:
                return None
            return {
                "text": normalize_whisper_text(text),
                "confidence": 0.92,
                "words": len(text.split()),
                "source": "whisper_cpp",
            }
        except Exception:
            return None
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _normalize_audio(self, audio_array) -> np.ndarray:
        """Sesi Whisper'a göndermeden önce normalize et (çok sessiz/gürültülü ortamlara karşı)."""
        arr = np.asarray(audio_array, dtype=np.float32).flatten()
        if arr.size == 0:
            return arr
        peak = float(np.max(np.abs(arr)))
        if peak > 0.001:  # 0.01 → 0.001: sessiz ortamlarda da normalize et
            arr = arr * (0.95 / peak)
        return arr

    def transcribe_audio(self, audio_array, samplerate=16000, wake_mode=False):
        """Sesi metne dönüştür.
        wake_mode=True → Klargus gibi kısa/bilinmeyen kelimeler için
        Whisper eşikleri gevşetilir, confidence cezası kaldırılır."""
        _empty = {"text": "", "confidence": 0.0, "source": "empty", "words": 0}
        if audio_array is None or len(audio_array) == 0:
            return _empty

        audio_array = self._normalize_audio(audio_array)

        faster = self._transcribe_faster_whisper(audio_array, samplerate, wake_mode=wake_mode)
        if faster:
            return faster

        whisper = self._transcribe_whisper_cpp(audio_array, samplerate)
        if whisper:
            return whisper

        pcm = self._to_pcm16(audio_array)
        if not pcm:
            return _empty

        free = self._recognize_with(pcm, samplerate, grammar=None)
        grammar = self._recognize_with(pcm, samplerate, grammar=COMMAND_GRAMMAR)

        grammar_text = grammar.get("text", "")
        grammar_conf = float(grammar.get("confidence", 0.0) or 0.0)
        free_conf = float(free.get("confidence", 0.0) or 0.0)

        if grammar_text and grammar_text != "[unk]" and grammar_conf >= max(0.42, free_conf - 0.08):
            grammar["source"] = "grammar"
            return grammar

        free["source"] = "free"
        return free


_stt_singleton = None
_stt_singleton_lock = threading.Lock()

# ── Dinamik hotwords ──────────────────────────────────────────────────────────
_HOTWORDS_BASE = (
    "Klargus aç kapat çal dur yaz tıkla ekran kamera oku bak müzik ses "
    "Spotify Discord Chrome Steam Brave WhatsApp YouTube "
    "nasılsın nedir nasıl neden git göster listele söyle anlat "
    "ekrana bak kameraya bak ne görüyorsun evet hayır tamam dur yeter"
)
_hotwords_cache: str = ""
_hotwords_cache_at: float = 0.0
_HOTWORDS_TTL: float = 120.0  # 2 dakikada bir güncelle

# ── Dinamik initial_prompt — Whisper'ı kullanıcının sözlüğüne yönlendirir ──────
_initial_prompt_cache: str = ""
_initial_prompt_at: float = 0.0
_INITIAL_PROMPT_TTL: float = 120.0


def _build_initial_prompt() -> str:
    """Whisper için bağlam promptu: sahip adı + güncel komut/program sözlüğü.

    NOT: Ham geçmiş cümleler EKLENMEZ — Whisper sessizlik/gürültüde onları
    halüsinasyonla tekrar edebilir. Kişiselleştirme (sık kelimeler) hotwords'te.
    """
    global _initial_prompt_cache, _initial_prompt_at
    now = time.time()
    if _initial_prompt_cache and (now - _initial_prompt_at) < _INITIAL_PROMPT_TTL:
        return _initial_prompt_cache
    owner = ""
    try:
        owner = str(get_setting("owner_name", "") or "").strip()
    except Exception:
        pass
    who = owner if owner and owner.lower() != "kullanıcı" else "kullanıcının"
    prompt = (
        f"Klargus, {who} Türkçe sesli masaüstü asistanı. "
        "Hem komutları hem sohbeti anlar. "
        "Komutlar: aç, kapat, çal, dur, yaz, tıkla, ara, kaydet, geri al, "
        "ekrana bak, ekranı oku, kameraya bak, ne görüyorsun. "
        "Programlar: Spotify, Discord, Chrome, YouTube, Steam, WhatsApp, Brave. "
        "Sohbet: nasılsın, ne düşünüyorsun, anlat, nedir, neden, nasıl. "
        "Klargus müzik çal. Bugün nasılsın?"
    )
    _initial_prompt_cache = prompt
    _initial_prompt_at = now
    return prompt


def _build_hotwords() -> str:
    """Sabit hotword listesini konuşma geçmişindeki sık kelimelerle genişlet."""
    global _hotwords_cache, _hotwords_cache_at
    now = time.time()
    if _hotwords_cache and (now - _hotwords_cache_at) < _HOTWORDS_TTL:
        return _hotwords_cache

    extra: set = set()
    try:
        # Son konuşma loglarından sık geçen kelimeleri çek
        log_path = Path(__file__).resolve().parents[1] / "data" / "conversations.jsonl"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            word_freq: dict = {}
            for line in lines[-300:]:   # son 300 satır
                try:
                    entry = json.loads(line)
                    # format: {ts, user, assistant, action, ok}
                    text = str(entry.get("user", "") or "")
                    for word in text.split():
                        w = word.strip(".,!?\"'()[]{}:;").lower()
                        if len(w) >= 4 and w.isalpha():
                            word_freq[w] = word_freq.get(w, 0) + 1
                except Exception:
                    pass
            # En az 3 kez geçen kelimeleri hotword'e ekle
            for w, count in word_freq.items():
                if count >= 3:
                    extra.add(w)
    except Exception:
        pass

    combined = _HOTWORDS_BASE
    if extra:
        combined = combined + " " + " ".join(sorted(extra)[:40])  # max 40 ekstra kelime

    _hotwords_cache = combined
    _hotwords_cache_at = now
    return combined


NOISE_TRANSCRIPT_PATTERNS = {
    # Bunlar gerçek konuşma değil — YouTube/video ses artefaktı veya TTS yankısı
    "abone ol",
    "altyazi",
    "altyazi ekleyen",
    "altyazı",
    "altyazı ekleyen",
    "izlediginiz icin tesekkurler",
    "izlediğiniz için teşekkürler",
    "buna gore calisiyor",
    "buna göre çalışıyor",
    "devam edecek",
    "baslangic sesi bekleniyor",
    "başlangıç sesi bekleniyor",
    # NOT: "teşekkürler", "dinliyorum", "sizi dinliyorum" BURAYA EKLENMEZ
    # — kullanıcı sohbette bunları söyleyebilir
    "klargus aktif",
    "klargus durduruldu",
    "sohbet oturumu aktif",
}

# Pre-computed folded version — her çağrıda set comprehension çalıştırma
_NOISE_PATTERNS_FOLDED: frozenset = frozenset()  # modül yüklenince doldurulur


def _init_noise_patterns_folded():
    global _NOISE_PATTERNS_FOLDED
    _NOISE_PATTERNS_FOLDED = frozenset(_fold_for_filter(item) for item in NOISE_TRANSCRIPT_PATTERNS)


def get_stt():
    global _stt_singleton
    if _stt_singleton is None:
        with _stt_singleton_lock:
            if _stt_singleton is None:   # double-checked locking
                _stt_singleton = SpeechToText()
    return _stt_singleton


def preload_stt():
    get_stt()
    return True


def stt_engine_status():
    return get_stt().engine_status()


def _resample_audio(audio, source_rate: int, target_rate: int = 16000) -> np.ndarray:
    arr = np.asarray(audio, dtype=np.float32).flatten()
    if arr.size == 0 or int(source_rate) == int(target_rate):
        return arr.astype(np.float32)
    duration = arr.size / float(source_rate)
    target_len = max(1, int(round(duration * float(target_rate))))
    source_x = np.linspace(0.0, duration, num=arr.size, endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, arr).astype(np.float32)


def _wait_for_tts_idle():
    guard = float(get_setting("self_listen_guard_seconds", 0.9) or 0.9)
    try:
        while tts_state.is_speaking():
            time.sleep(0.04)
        last = tts_state.last_spoke_at()
        remaining = guard - (time.time() - last) if last else 0.0
        if remaining > 0:
            time.sleep(min(guard, remaining))
    except Exception:
        pass


def _fold_for_filter(text: str) -> str:
    """Türkçe → ASCII düzleştirme; gürültü filtresi için kullanılır."""
    import unicodedata
    value = str(text or "").lower().strip()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.translate(str.maketrans("ığüşöçâîûı", "igusocaiui"))
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _looks_like_noise_transcript(text: str, confidence: float, no_speech_probability: float) -> bool:
    folded = _fold_for_filter(text)
    if not folded:
        return True
    if not _NOISE_PATTERNS_FOLDED:
        _init_noise_patterns_folded()
    if folded in _NOISE_PATTERNS_FOLDED:
        return True
    if no_speech_probability > 0.68 and confidence < 0.42:
        return True
    if len(folded.split()) <= 2 and confidence < 0.22:
        return True
    return False


def _voice_auth_strict_needed(text: str) -> bool:
    folded = _fold_for_filter(text)
    if not folded:
        return False
    strict_terms = {
        "tikla",
        "tıkla",
        "bas",
        "yaz",
        "sil",
        "kapat",
        "enter",
        "tab",
        "kaydet",
        "yapistir",
        "yapıştır",
        "arama kutusu",
        "ekranin ortasina",
        "ekranın ortasına",
        "sag tikla",
        "sağ tıkla",
        "kisayol",
        "kısayol",
    }
    words = set(folded.split())
    for item in strict_terms:
        term = _fold_for_filter(item)
        if not term:
            continue
        if " " in term:
            if term in folded:
                return True
        elif term in words:
            return True
    return False


def _capture_fixed_command_audio(seconds: float = 4.0, target_samplerate: int = 16000):
    _wait_for_tts_idle()
    config = resolve_input_device_config(target_samplerate=target_samplerate)
    device = config.get("device")
    actual_samplerate = int(config.get("samplerate", target_samplerate) or target_samplerate)
    frames = []

    def callback(indata, frames_count, time_info, status):
        if status:
            return
        frame = np.asarray(indata, dtype=np.float32)
        if frame.ndim > 1:
            frame = frame.mean(axis=1)
        else:
            frame = frame.flatten()
        if frame.size:
            frames.append(frame.copy())

    capture = {
        "status": "capturing",
        "device": device,
        "samplerate": actual_samplerate,
        "target_samplerate": target_samplerate,
        "seconds": float(seconds),
        "resample": actual_samplerate != target_samplerate,
    }

    try:
        with sd.InputStream(
            samplerate=actual_samplerate,
            blocksize=max(1, int(actual_samplerate * 0.03)),
            channels=1,
            dtype="float32",
            device=device,
            callback=callback,
        ):
            time.sleep(float(seconds))
    except Exception as exc:
        capture["status"] = "open_failed"
        capture["error"] = str(exc)
        return None, capture

    if not frames:
        capture["status"] = "no_audio"
        return None, capture

    raw = np.concatenate(frames).astype(np.float32)
    audio = _resample_audio(raw, actual_samplerate, target_samplerate)
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    capture.update({
        "status": "captured",
        "duration": float(audio.size / float(target_samplerate)) if audio.size else 0.0,
        "rms": rms,
        "peak": peak,
    })
    return audio, capture


def listen_once(timeout=8.0):
    result = listen_once_detailed(timeout=timeout)
    return result.get("text", "")


def listen_once_detailed(timeout=8.0):
    listener = None
    audio = None
    capture = {}

    try:
        started_at = time.time()
        print("Komut dinleme başladı...", flush=True)
        _wait_for_tts_idle()
        capture_mode = str(get_setting("command_capture_mode", "adaptive") or "adaptive").lower().strip()
        if capture_mode == "fixed":
            seconds = float(get_setting("command_fixed_seconds", 4.0) or 4.0)
            seconds = max(1.8, min(float(timeout), seconds))
            audio, capture = _capture_fixed_command_audio(seconds=seconds, target_samplerate=16000)
        else:
            max_seconds = float(get_setting("command_max_seconds", timeout) or timeout)
            max_seconds = max(1.5, min(float(timeout), max_seconds))
            listener = CommandListener(timeout_seconds=max_seconds)
            audio = listener.listen(timeout=max_seconds)
            capture = dict(getattr(listener, "last_capture_info", {}) or {})

        if audio is None or len(audio) == 0:
            print(f"Komut sesi yakalanamadı. capture={capture}", flush=True)
            return {
                "text": "",
                "status": capture.get("status", "no_speech"),
                "duration": 0.0,
                "rms": 0.0,
                "capture": capture,
            }

        stt = get_stt()
        _stt_t0 = time.perf_counter()
        transcription = stt.transcribe_audio(audio, samplerate=16000)
        try:
            from core.perf_report import set_last_stt
            set_last_stt(time.perf_counter() - _stt_t0)
        except Exception:
            pass
        if isinstance(transcription, dict):
            text = transcription.get("text", "")
            confidence = float(transcription.get("confidence", 0.0) or 0.0)
            word_count = int(transcription.get("words", 0) or 0)
            no_speech_probability = float(transcription.get("no_speech_probability", 0.0) or 0.0)
        else:
            text = str(transcription or "")
            confidence = 0.0
            word_count = len(text.split())
            no_speech_probability = 0.0

        duration = len(audio) / 16000.0
        rms = float(np.sqrt(np.mean(np.square(np.asarray(audio, dtype=np.float32))))) if len(audio) else 0.0
        status = "ok" if text else "no_text"
        confidence_threshold = float(get_setting("listener_confidence_threshold", 0.20) or 0.20)
        source = transcription.get("source", "") if isinstance(transcription, dict) else ""
        capture_rms = float(capture.get("active_rms", capture.get("rms", rms)) or 0.0)
        capture_peak = float(capture.get("peak_rms", capture.get("peak", 0.0)) or 0.0)
        noise_floor = float(get_setting("listener_noise_floor", 0.001) or 0.001)
        min_rms = float(get_setting("command_min_rms", get_setting("listener_min_rms", 0.0001)) or 0.0001)
        noise_peak_limit = float(get_setting("command_noise_peak_limit", 0.0001) or 0.0001)
        if text and _looks_like_noise_transcript(text, confidence, no_speech_probability):
            status = "no_speech"
            text = ""

        voice = {"ready": False, "verified": True, "similarity": 0.0}
        if verify_voice is not None:
            try:
                threshold_override = get_setting("voice_auth_threshold", 0.0)
                try:
                    threshold_override = float(threshold_override or 0.0)
                except Exception:
                    threshold_override = 0.0
                strict = bool(get_setting("voice_auth_strict_for_control", True)) and _voice_auth_strict_needed(text)
                voice = verify_voice(
                    audio,
                    threshold=threshold_override if threshold_override > 0 else None,
                    strict=strict,
                )
                voice["strict_requested"] = strict
            except Exception as e:
                voice = {"ready": False, "verified": True, "similarity": 0.0, "reason": str(e)}

        print(
            "Komut sonucu: "
            f"status={status} sure={duration:.2f}s rms={rms:.4f} "
            f"guven={confidence:.3f} "
            f"ses_benzerlik={float(voice.get('similarity', 0.0)):.3f} "
            f"ses_karar={voice.get('confidence_level', '-')} "
            f"metin={text}",
            flush=True,
        )

        return {
            "text": text,
            "status": status,
            "duration": duration,
            "rms": rms,
            "confidence": confidence,
            "confidence_threshold": confidence_threshold,
            "no_speech_probability": no_speech_probability,
            "word_count": word_count,
            "source": source,
            "engine": stt.engine_status().get("active", ""),
            "elapsed": time.time() - started_at,
            "voice": voice,
            "capture": capture,
        }

    finally:
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass


def listen_command(timeout=8.0):
    return listen_once(timeout=timeout)


def listen_command_detailed(timeout=8.0):
    return listen_once_detailed(timeout=timeout)
