import collections
import threading
import time

import numpy as np
import sounddevice as sd
import webrtcvad

from audio.device_manager import resolve_input_device_config
from brain.settings_manager import get_setting


class VAD:
    def __init__(
        self,
        samplerate=16000,
        frame_duration_ms=30,
        aggressiveness=3,
        start_speech_frames=4,
        end_silence_frames=10,
        min_utterance_ms=350,
        pre_roll_frames=8,
        min_rms=None,
    ):
        self.samplerate = samplerate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(samplerate * frame_duration_ms / 1000)
        self.vad = webrtcvad.Vad(aggressiveness)

        self.start_speech_frames = start_speech_frames
        self.end_silence_frames = end_silence_frames
        self.min_utterance_frames = max(1, int(min_utterance_ms / frame_duration_ms))
        self.pre_roll_frames = pre_roll_frames
        self.min_rms = float(min_rms if min_rms is not None else get_setting("listener_min_rms", 0.0065) or 0.0065)
        self.last_rms = 0.0

    def _to_pcm16(self, frame: np.ndarray) -> bytes:
        audio = np.asarray(frame, dtype=np.float32).flatten()
        audio = np.clip(audio, -1.0, 1.0)
        pcm = (audio * 32767.0).astype(np.int16)
        return pcm.tobytes()

    def is_speech(self, frame: np.ndarray) -> bool:
        if frame is None or len(frame) == 0:
            return False

        pcm = self._to_pcm16(frame)
        expected_len = self.frame_size * 2
        if len(pcm) != expected_len:
            return False

        rms = float(np.sqrt(np.mean(np.square(frame.astype(np.float32))))) if len(frame) else 0.0
        self.last_rms = rms
        if rms < self.min_rms:
            return False

        try:
            vad_speech = self.vad.is_speech(pcm, self.samplerate)
        except Exception:
            vad_speech = False

        if vad_speech:
            return True

        return rms >= max(self.min_rms * 1.5, 0.005)


class AudioStream:
    def __init__(
        self,
        samplerate=16000,
        frame_duration_ms=30,
        channels=1,
        device=None,
    ):
        self.samplerate = samplerate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(samplerate * frame_duration_ms / 1000)
        self.channels = channels
        self.device = device
        self.actual_samplerate = samplerate
        self.tts_guard_seconds = float(get_setting("self_listen_guard_seconds", 0.9) or 0.9)

        self.stream = None
        self.buffer = collections.deque(maxlen=800)  # 400 → 800 frame (24s buffer)
        self.last_capture_info = {}
        self._start_lock = threading.Lock()
        self._stop_event = threading.Event()

    def start(self):
        with self._start_lock:  # TOCTOU race koruması
            if self.stream is not None:
                return

            if self.device is None:
                config = resolve_input_device_config(target_samplerate=self.samplerate)
                self.device = config.get("device")
                self.actual_samplerate = int(config.get("samplerate", self.samplerate) or self.samplerate)
            else:
                self.actual_samplerate = self.samplerate

            actual_frame_size = int(self.actual_samplerate * self.frame_duration_ms / 1000)
            self._stop_event.clear()

            # Bazı WASAPI/Intel Smart Sound mikrofonları MONO açılınca SESSİZLİK
            # veriyor (rms 0.0), ama native 2 kanalla açılınca düzgün ses veriyor.
            # Cihazın kanal sayısıyla aç (max 2); _callback mean(axis=1) ile mono'ya indirger.
            open_channels = self.channels
            try:
                _info = sd.query_devices(self.device) if self.device is not None else sd.query_devices(kind="input")
                _maxch = int(_info.get("max_input_channels", 1) or 1)
                if _maxch >= 2:
                    open_channels = 2
            except Exception:
                pass

            self.stream = sd.InputStream(
                samplerate=self.actual_samplerate,
                blocksize=actual_frame_size,
                channels=open_channels,
                dtype="float32",
                device=self.device,
                callback=self._callback,
            )
            self.stream.start()
            self.last_capture_info = {
                "device": self.device,
                "samplerate": self.actual_samplerate,
                "target_samplerate": self.samplerate,
                "resample": self.actual_samplerate != self.samplerate,
            }

    def stop(self):
        self._stop_event.set()  # read_frame'de bekleyenleri uyandır
        with self._start_lock:
            if self.stream is not None:
                try:
                    self.stream.stop()
                    self.stream.close()
                finally:
                    self.stream = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            return
        if self._should_mute_for_tts():
            return
        frame = np.asarray(indata, dtype=np.float32)
        if frame.ndim > 1:
            frame = frame.mean(axis=1)
        else:
            frame = frame.flatten()
        if self.actual_samplerate != self.samplerate:
            frame = _resample_frame(frame, self.actual_samplerate, self.samplerate, self.frame_size)
        self.buffer.append(frame.astype(np.float32).copy())

    def _should_mute_for_tts(self) -> bool:
        try:
            from audio import text_to_speech as tts_state

            if tts_state.is_speaking():
                return True
            last = tts_state.last_spoke_at()
            return bool(last and (time.time() - last) < self.tts_guard_seconds)
        except Exception:
            return False

    def read_frame(self, timeout=1.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.buffer:
                return self.buffer.popleft().flatten()
            time.sleep(0.005)
        return None

    def flush(self):
        self.buffer.clear()

    def capture_utterance(
        self,
        vad: VAD,
        timeout_seconds=8.0,
    ):
        """
        Sessizlikten konuşmayı ayırır.
        Dönüş: np.ndarray veya None
        """
        pre_roll = collections.deque(maxlen=vad.pre_roll_frames)
        speech_frames = []
        speech_rms_values = []
        speech_count = 0
        silence_count = 0
        started = False

        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
            frame = self.read_frame(timeout=0.5)
            if frame is None:
                continue

            pre_roll.append(frame)
            is_speech = vad.is_speech(frame)
            frame_rms = float(vad.last_rms)

            if not started:
                if is_speech:
                    speech_count += 1
                    if speech_count >= vad.start_speech_frames:
                        started = True
                        speech_frames.extend(list(pre_roll))
                        speech_rms_values.extend([_frame_rms(x) for x in list(pre_roll)])
                        silence_count = 0
                else:
                    speech_count = 0
                continue

            speech_frames.append(frame)
            speech_rms_values.append(frame_rms)

            if is_speech:
                silence_count = 0
            else:
                silence_count += 1
                if silence_count >= vad.end_silence_frames:
                    break

        if len(speech_frames) < vad.min_utterance_frames:
            self.last_capture_info = {
                "status": "too_short",
                "device": self.device,
                "samplerate": self.actual_samplerate,
                "target_samplerate": self.samplerate,
                "frames": len(speech_frames),
                "min_frames": vad.min_utterance_frames,
            }
            return None

        audio = np.concatenate(speech_frames).astype(np.float32)
        active_rms = float(np.percentile(np.asarray(speech_rms_values, dtype=np.float32), 75)) if speech_rms_values else 0.0
        peak_rms = float(np.max(np.asarray(speech_rms_values, dtype=np.float32))) if speech_rms_values else 0.0
        duration = len(audio) / float(self.samplerate)

        self.last_capture_info = {
            "status": "captured",
            "device": self.device,
            "samplerate": self.actual_samplerate,
            "target_samplerate": self.samplerate,
            "duration": duration,
            "active_rms": active_rms,
            "peak_rms": peak_rms,
            "frames": len(speech_frames),
            "min_rms": vad.min_rms,
        }

        if active_rms < 0.0001:
            self.last_capture_info["status"] = "too_quiet"
            return None

        return audio


def _frame_rms(frame: np.ndarray) -> float:
    if frame is None or len(frame) == 0:
        return 0.0
    arr = np.asarray(frame, dtype=np.float32).flatten()
    return float(np.sqrt(np.mean(np.square(arr))))


def _resample_frame(frame: np.ndarray, source_rate: int, target_rate: int, target_size: int) -> np.ndarray:
    arr = np.asarray(frame, dtype=np.float32).flatten()
    if arr.size == 0:
        return np.zeros(target_size, dtype=np.float32)
    if int(source_rate) == int(target_rate):
        if arr.size == target_size:
            return arr.astype(np.float32)
        source_x = np.linspace(0.0, 1.0, num=arr.size, endpoint=False)
        target_x = np.linspace(0.0, 1.0, num=target_size, endpoint=False)
        return np.interp(target_x, source_x, arr).astype(np.float32)

    duration = arr.size / float(source_rate)
    expected = max(1, int(round(duration * float(target_rate))))
    target_len = target_size if abs(expected - target_size) <= 2 else expected
    source_x = np.linspace(0.0, duration, num=arr.size, endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    out = np.interp(target_x, source_x, arr).astype(np.float32)
    if out.size == target_size:
        return out
    if out.size > target_size:
        return out[:target_size]
    return np.pad(out, (0, target_size - out.size), mode="constant").astype(np.float32)
