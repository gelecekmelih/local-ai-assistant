import time

import numpy as np
from audio.vad import VAD, AudioStream
from audio.listening_calibration import current_audio_thresholds
from brain.settings_manager import get_setting
from audio.device_manager import resolve_input_device_config

# webrtcvad sadece bu örnekleme oranlarını destekler
_VAD_RATES = (8000, 16000, 32000, 48000)


def _wait_for_tts_idle():
    guard = float(get_setting("self_listen_guard_seconds", 0.9) or 0.9)
    try:
        from audio import text_to_speech as tts_state

        while tts_state.is_speaking():
            time.sleep(0.04)
        last = tts_state.last_spoke_at()
        remaining = guard - (time.time() - last) if last else 0.0
        if remaining > 0:
            time.sleep(min(guard, remaining))
    except Exception:
        pass


class CommandListener:
    def __init__(
        self,
        samplerate=16000,
        frame_duration_ms=30,
        timeout_seconds=8.0,
    ):
        self.target_samplerate = 16000   # Whisper hedef oranı
        # Cihazın NATIVE oranında yakala — per-frame resample sesi bozup Whisper'ı
        # "gel gel gel" gibi halüsinasyona sokuyordu. Native yakala, VAD native
        # çalışsın, STT öncesi TÜM buffer tek seferde 16k'ya çevrilsin.
        try:
            _cfg = resolve_input_device_config(target_samplerate=self.target_samplerate)
            native_sr = int(_cfg.get("samplerate", self.target_samplerate) or self.target_samplerate)
        except Exception:
            native_sr = self.target_samplerate
        if native_sr not in _VAD_RATES:
            native_sr = self.target_samplerate   # VAD desteklemiyorsa 16k'ya düş
        self.samplerate = native_sr
        self.timeout_seconds = timeout_seconds
        thresholds = current_audio_thresholds()
        command_min_rms = float(get_setting("command_min_rms", thresholds["min_rms"]) or thresholds["min_rms"])
        min_rms = max(float(thresholds["min_rms"]), command_min_rms)
        min_seconds = float(get_setting("command_min_seconds", 0.65) or 0.65)
        silence_seconds = float(get_setting("command_silence_seconds", 0.75) or 0.75)
        start_frames = int(get_setting("command_start_speech_frames", 2) or 2)
        end_frames = max(6, int(round((silence_seconds * 1000.0) / float(frame_duration_ms))))
        self.stream = AudioStream(
            samplerate=native_sr,
            frame_duration_ms=frame_duration_ms,
            channels=1,
        )
        self.vad = VAD(
            samplerate=native_sr,
            frame_duration_ms=frame_duration_ms,
            aggressiveness=0,
            start_speech_frames=max(1, start_frames),
            end_silence_frames=end_frames,
            min_utterance_ms=max(220, int(min_seconds * 1000)),
            pre_roll_frames=10,
            min_rms=min_rms,
        )
        self.last_capture_info = {}

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()

    def listen(self, timeout=None):
        """
        Tek bir anlamlı konuşma parçasını yakalar.
        """
        if timeout is None:
            timeout = self.timeout_seconds

        _wait_for_tts_idle()
        self.start()
        try:
            audio = self.stream.capture_utterance(
                vad=self.vad,
                timeout_seconds=timeout,
            )
            self.last_capture_info = dict(getattr(self.stream, "last_capture_info", {}) or {})
        finally:
            self.stop()  # Mikrofon akışını her zaman kapat — kaynak sızıntısı önle

        if audio is None or len(audio) == 0:
            return None

        audio = np.asarray(audio, dtype=np.float32).flatten()

        # TÜM buffer'ı TEK SEFERDE hedef orana (16k) çevir — per-frame değil.
        # Bu, frame sınırı artifaktlarını ve Whisper halüsinasyonunu önler.
        if self.samplerate != self.target_samplerate and audio.size:
            n = int(round(audio.size * self.target_samplerate / self.samplerate))
            if n > 0:
                audio = np.interp(
                    np.linspace(0.0, audio.size, n, endpoint=False),
                    np.arange(audio.size),
                    audio,
                ).astype(np.float32)
        return audio
