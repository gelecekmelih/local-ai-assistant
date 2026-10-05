import sys
import os
import re
import unicodedata

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# AppUserModelID — Tk penceresi OLUŞMADAN ÖNCE ayarlanmalı.
# Bu olmadan Windows uygulamayı python.exe ile gruplar; taskbar python'un
# ikonunu/önbelleğini kullanabilir ve Klargus ikonu bulanık/yanlış görünür.
try:
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Klargus.Jarvis.1")
except Exception:
    pass

import tkinter as tk

# KLARGUS_GLOBAL_MOUSEWHEEL_FIX
_ORIG_TEXT_INIT = tk.Text.__init__
def _klargus_text_init(self, *args, **kwargs):
    _ORIG_TEXT_INIT(self, *args, **kwargs)
    self.bind("<MouseWheel>", lambda e: self.yview_scroll(int(-1*(e.delta/120)), "units"))
tk.Text.__init__ = _klargus_text_init

_ORIG_LISTBOX_INIT = tk.Listbox.__init__
def _klargus_listbox_init(self, *args, **kwargs):
    _ORIG_LISTBOX_INIT(self, *args, **kwargs)
    self.bind("<MouseWheel>", lambda e: self.yview_scroll(int(-1*(e.delta/120)), "units"))
tk.Listbox.__init__ = _klargus_listbox_init

from tkinter import scrolledtext, messagebox
import threading
import queue
import time
from datetime import datetime
from pathlib import Path
from core.path_utils import resource_path

# Hafif TTS — senkron, hızlı (~25 ms)
try:
    from audio.text_to_speech import speak
    TTS_AVAILABLE = True
except Exception:
    TTS_AVAILABLE = False
    def speak(*args, **kwargs): return False

# Ağır ses modülleri (sounddevice / faster-whisper / vosk) —
# başlangıçta stub, _background_preload içinde arka planda yüklenir
AUDIO_AVAILABLE = False

def listen_for_wake_word(*args, **kwargs):     return None
def pop_pending_wake_command(*args, **kwargs): return ""
def is_wake_phrase(*args, **kwargs):           return False
def extract_command_after_wake(*args, **kwargs): return ""
def listen_command_detailed(*args, **kwargs):  return {"text": "", "status": "audio_unavailable"}
def enroll_voice_profile(*args, **kwargs):     return {"ok": False, "message": "Ses sistemi yükleniyor."}
def voice_profile_status(*args, **kwargs):     return {"ready": False, "samples": 0}
def input_device_status_text(*args, **kwargs): return "Ses sistemi yükleniyor..."


def _load_heavy_audio():
    """wake_word / speech_to_text / voice_profile modüllerini arka planda yükle."""
    global AUDIO_AVAILABLE
    global listen_for_wake_word, pop_pending_wake_command, is_wake_phrase
    global extract_command_after_wake, listen_command_detailed
    global enroll_voice_profile, voice_profile_status, input_device_status_text
    try:
        from audio.wake_word import (
            listen_for_wake_word       as _lww,
            pop_pending_wake_command   as _ppwc,
            is_wake_phrase             as _iwp,
            extract_command_after_wake as _ecaw,
        )
        from audio.speech_to_text import listen_command_detailed  as _lcd
        from audio.voice_profile  import (enroll_voice_profile    as _evp,
                                          voice_profile_status     as _vps)
        from audio.device_manager import input_device_status_text as _idst

        listen_for_wake_word       = _lww
        pop_pending_wake_command   = _ppwc
        is_wake_phrase             = _iwp
        extract_command_after_wake = _ecaw
        listen_command_detailed    = _lcd
        enroll_voice_profile       = _evp
        voice_profile_status       = _vps
        input_device_status_text   = _idst
        AUDIO_AVAILABLE = True
        print("[Audio] Ses sistemi hazır.", flush=True)
    except Exception as _e:
        print(f"[App] Ses sistemi yüklenemedi: {_e}", flush=True)
        AUDIO_AVAILABLE = False

from brain.llm import ask_llm
from brain.tool_registry import run_tool as _raw_run_tool
from brain.utils import start_session, refresh_session, session_active, end_session
from brain.conversation_session import (
    start_chat_session,
    refresh_chat_session,
    end_chat_session,
    chat_session_active,
    is_chat_start_request,
    is_chat_end_request,
)
from brain.approval_state import set_pending_approval as set_pending, get_pending_approval as get_pending, clear_pending_approval as clear_pending
from brain.system_settings import get_system_setting, load_system_settings
from brain.utils import set_context, get_context
from brain.settings_manager import load_settings, set_setting, get_setting
from audio.text_normalizer import save_stt_correction
from ui.tray_manager import TrayManager
from ui.event_bus import pop_event_nowait as pop_shared_event
from ui.hud_panel import HUDPanel
from ui.ui_theme import (
    BG, CARD, CARD2, TEXT, MUTED, ACCENT,
    DANGER, PANEL_BORDER, INFO,
)

try:
    from brain.llm import short_answer_streaming as _short_answer_streaming
    _FAZ1_AVAILABLE = True
except Exception:
    _FAZ1_AVAILABLE = False

try:
    from audio.streaming_tts import speak_stream as _speak_stream
    _STREAM_TTS_AVAILABLE = TTS_AVAILABLE
except Exception:
    _STREAM_TTS_AVAILABLE = False

event_queue = queue.Queue()


def _tts_clip(text: str, max_chars: int = 250) -> str:
    """Sesli yanıt için metin kısalt — UI tam metni gösterir, ses kısa tutulur.

    Uzun LLM yanıtlarını okumak 30-80s sürebilir. Bu fonksiyon ilk ~250 karakteri
    cümle sınırında keser. Kullanıcı tam metni ekranda görür.
    """
    if not text or len(text) <= max_chars:
        return text
    # Cümle bitişlerinde kes
    import re as _re
    cut = text[:max_chars]
    # Son cümle sonuna göre kes (., !, ?)
    m = list(_re.finditer(r"[.!?](?:\s|$)", cut))
    if m:
        return cut[:m[-1].end()].strip()
    # Cümle sonu yoksa son boşlukta kes
    last_space = cut.rfind(" ")
    return cut[:last_space].strip() if last_space > 50 else cut.strip()


def _background_preload():
    """
    Arka planda paralel yükleme:
    - Ağır ses modüllerini hemen yükler (UI açılırken paralel)
    - Ollama LLM modelini belleğe alır (ilk komut anında gelir)
    - STT Whisper modelini ısındırır
    - Program dizinini önbelleğe alır
    """
    import time as _t

    # 0) Ağır ses modülleri — UI açılırken paralel yükle, bekleme yok
    _load_heavy_audio()

    # 0b) Neural TTS soğuk başlatmayı yok et — açılışta bağlantıyı ısıt.
    # İlk sesli cevap 2.4s yerine ~1s gelir (edge-tts bağlantı kurma maliyeti).
    def _warm_edge_tts():
        try:
            if str(get_system_setting("tts_engine", "piper") or "").lower() in ("edge", "neural", "edge_neural"):
                import os as _os
                from audio.text_to_speech import _synth_edge_to_wav
                w = _synth_edge_to_wav("Hazırım.")
                if w and _os.path.exists(w):
                    _os.remove(w)
                    print("[Preload] Neural TTS bağlantısı ısındı.", flush=True)
        except Exception:
            pass
    threading.Thread(target=_warm_edge_tts, daemon=True, name="edge-warm").start()

    # 0b2) Piper kalıcı sürecini ısıt — varsayılan TTS motoru. İlk sesli cevap
    # ~750ms yerine ~185ms gelir (model süreçte yüklü kalır).
    def _warm_piper():
        try:
            if str(get_system_setting("tts_engine", "piper") or "").lower() in ("piper", "", "local"):
                from audio.text_to_speech import warm_piper
                if warm_piper():
                    print("[Preload] Piper kalıcı süreci ısındı.", flush=True)
        except Exception:
            pass
    threading.Thread(target=_warm_piper, daemon=True, name="piper-warm").start()

    # 1) LLM pre-warm — PARALEL thread (ses yüklemesiyle aynı anda, bloklamasın).
    # Modeli Ollama RAM'ine yükler; ses/STT yüklenirken arka planda hazırlanır.
    def _warm_llm():
        try:
            from brain.llm import _url, get_model_name, _session, OLLAMA_NUM_CTX
            _session().post(
                _url("/api/generate"),
                json={"model": get_model_name(), "prompt": ".", "stream": False,
                      "keep_alive": "30m",
                      "options": {"num_predict": 1, "num_ctx": OLLAMA_NUM_CTX}},
                timeout=35,
            )
            print("[Preload] LLM ısındı (keep_alive=30m).", flush=True)
        except Exception as _e:
            print(f"[Preload] LLM ısınma hatası: {_e}", flush=True)
    threading.Thread(target=_warm_llm, daemon=True, name="llm-warm-init").start()

    # 1b) Embedding modeli pre-warm — PARALEL thread. nomic-embed-text ilk
    # çağrıda ~13s model yükleme maliyeti öder (brain/memory_bank.py::_get_embedding).
    # Isıtılmazsa bu 13s, kullanıcının hafıza gerektiren İLK sorusunda LLM'i
    # bloklardı. keep_alive kısa (2m, VRAM bütçesi memory_bank.py'de belgeli)
    # olduğundan bu sadece açılıştan sonraki ilk birkaç dakikayı hızlandırır.
    def _warm_embed():
        try:
            from brain.memory_bank import _get_embedding
            if _get_embedding("ısınma"):
                print("[Preload] Embedding modeli ısındı.", flush=True)
        except Exception as _e:
            print(f"[Preload] Embedding ısınma hatası: {_e}", flush=True)
    threading.Thread(target=_warm_embed, daemon=True, name="embed-warm-init").start()

    # 2) STT singleton yükle — Whisper model belleğe alınsın (sesli komut için şart)
    try:
        if AUDIO_AVAILABLE:
            from audio.speech_to_text import get_stt
            get_stt()
            print("[Preload] STT hazır.", flush=True)
    except Exception as _e:
        print(f"[Preload] STT yükleme hatası: {_e}", flush=True)

    # 3) Program dizini önbelleğe al
    try:
        from control.program_index import load_program_index
        load_program_index()
        print("[Preload] Program dizini önbellekte.", flush=True)
    except Exception:
        pass

    # 4) Bilgi tabanı bağlantısını aç (SQLite cold start)
    try:
        from brain.knowledge_base import knowledge_stats
        knowledge_stats()
    except Exception:
        pass

    # 5) Ekran OCR önbelleğini başlat — screen_read anında cevap versin
    try:
        from vision.screen_understanding import start_screen_cache
        start_screen_cache()
        print("[Preload] Ekran OCR önbelleği başlatıldı.", flush=True)
    except Exception as _e:
        print(f"[Preload] Ekran önbellek hatası: {_e}", flush=True)

    # NOT: Eski "5b screen LLM ısıtma" + 5 saniyelik sleep KALDIRILDI —
    # ana LLM 1. adımda zaten ısındı, ikinci ısıtma gereksizdi (saf gecikme).

    # 6) Ana LLM ısı tutucu — model GPU'dan ASLA düşmesin.
    # keep_alive=30m sadece kullanım anında yenilenir; 30dk sessizlik sonrası
    # model boşaltılır ve ilk komut 5-15s soğuk yükleme öderdi (≤5s işleme
    # garantisini bozar). Her 10 dakikada 1-token'lık ping modeli sıcak tutar.
    def _llm_warm_keeper():
        from brain.llm import _url as _u, get_model_name as _gm, _session as _s, OLLAMA_NUM_CTX as _nctx
        while True:
            _t.sleep(600)   # 10 dk
            try:
                _s().post(
                    _u("/api/generate"),
                    json={"model": _gm(), "prompt": " ", "stream": False,
                          "keep_alive": "30m",
                          "options": {"num_predict": 1, "num_ctx": _nctx}},
                    timeout=20,
                )
            except Exception:
                pass

    threading.Thread(target=_llm_warm_keeper, daemon=True, name="llm-warm").start()

    # 7) Vision model ön-yükleme KALDIRILDI — moondream ~2GB VRAM tutuyordu.
    # RTX 4070 (8GB): llama3.1:8b (~5GB) + whisper medium (~1.5GB) + nomic (~0.5GB)
    # zaten doluydu; moondream eklenince ana LLM katmanları CPU'ya taşıyor ve
    # token üretimi 5-6x yavaşlıyordu. Vision artık ilk kullanımda yüklenir
    # (ilk screen_read ~5s, sonrası normal) ve kısa keep_alive ile boşaltılır.


threading.Thread(target=_background_preload, daemon=True).start()

app_instance = None

def run_tool(action, arg):
    return _raw_run_tool(action, arg)


DATA_DIR = Path(resource_path("data"))
BRAINPAD_FILE = DATA_DIR / "brainpad.txt"


def push_event(kind, value):
    event_queue.put((kind, value))


def ts():
    return datetime.now().strftime("%H:%M:%S")


def match_text(text: str) -> str:
    value = str(text or "").lower().strip()
    value = value.replace("ı", "i").replace("ğ", "g").replace("ü", "u")
    value = value.replace("ş", "s").replace("ö", "o").replace("ç", "c")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s:.-]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _safe_result_value(local_vars):
    try:
        return local_vars.get("result", "")
    except Exception:
        return ""


def short_answer(prompt, memory_text):
    """
    FAZ 1: Streaming TTS aktifse cevap token token gelirken TTS çalar.
    Devre dışıysa eski davranış (tek blok ask_llm).
    """
    full_prompt = prompt + "\nCevap kısa, net ve doğal olsun."

    if _FAZ1_AVAILABLE and AUDIO_AVAILABLE:
        try:
            return _short_answer_streaming(
                prompt=full_prompt,
                memory_text=memory_text,
                push_event_fn=push_event,
                speak_fn=speak,
                tts_mode="command",
            )
        except Exception as e:
            push_event("log", f"[Faz1 streaming fallback] {e}")

    # Fallback
    return ask_llm(full_prompt, memory_text=memory_text)


def handle_pending_confirmation(low):
    pending = get_pending()
    if not pending:
        return None

    if low in ["evet", "onaylıyorum", "onayliyorum", "tamam", "tıkla", "tikla", "tkla"]:
        action = pending.get("action")
        arg = pending.get("arg")
        clear_pending()
        result = run_tool(action, arg)
        return result if result else "Onaylı işlem yapılamadı."

    if low in ["hayır", "hayir", "hayr", "iptal", "vazgeç", "vazgec", "vazge"]:
        clear_pending()
        return "Tamam, işlemi iptal ettim."

    return None


def execute_single_task(text, combined_memory):
    # ── SovereignBrain: tek karar/yürütme mercii ──────────────────────────────
    # Eski AgentRunner + decide_action (rule/JSON tabanlı) yolu KALDIRILDI
    # (2026-09-29) — SovereignBrain artık istisnasız tek yürütme yoludur.
    try:
        from brain.sovereign_brain import sovereign_think
        response = sovereign_think(text)
        if response:
            return response, "sovereign", text
    except Exception as _sb_err:
        push_event("log", f"[SovereignBrain fallback] {_sb_err}")
    return short_answer(text, combined_memory), "sovereign_fallback", text


def execute_task_chain(text, combined_memory):
    """Komutu sovereign_brain ile çalıştır."""
    return execute_single_task(text, combined_memory)


class ProPanel(tk.Tk):
    def submit_command_from_dock(self, command_text):
        try:
            if not command_text:
                return
            push_event("log", f"[MANUAL] {command_text}")
            push_event("assistant", "…")
            def _run():
                try:
                    combined_memory = ""
                    result, action, arg = execute_task_chain(command_text, combined_memory)
                    try:
                        push_event("assistant", str(result))
                    except Exception:
                        pass
                    try:
                        push_event("log", f"[ACTION] {action} | {arg}")
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        push_event("log", f"[MANUAL ERROR] {e}")
                    except Exception:
                        pass
            threading.Thread(target=_run, daemon=True).start()
        except Exception as e:
            try:
                push_event("log", f"[CommandDock Submit Error] {e}")
            except Exception:
                pass

    def handle_app_close(self):
        try:
            settings = load_system_settings()
            # Tray ikonu çalışmıyorsa pencereyi gizleme — geri getirmenin yolu kalmaz
            if settings.get("close_to_tray", True) and getattr(self.tray_manager, "_running", False):
                self.tray_manager.minimize_to_tray()
                try:
                    push_event("log", "Uygulama tray moduna alındı.")
                except Exception:
                    pass
            else:
                try:
                    self.tray_manager.stop()
                except Exception:
                    pass
                self.destroy()
        except Exception:
            self.destroy()

    def quit_app(self):
        """Tray menüsünden tam çıkış."""
        try:
            self.tray_manager.stop()
        except Exception:
            pass
        self.destroy()

    def open_settings_panel(self):
        try:
            from ui.settings_panel import SettingsPanel
            SettingsPanel(self)
        except Exception as e:
            try:
                push_event("log", f"SettingsPanel Error -> {e}")
            except Exception:
                pass

    def run_manual_command_from_control_center(self, command_text):
        try:
            if not command_text:
                return
            if hasattr(self, "input_box"):
                try:
                    self.input_box.delete(0, "end")
                    self.input_box.insert(0, command_text)
                except Exception:
                    pass
            if hasattr(self, "handle_manual_command"):
                self.handle_manual_command(command_text)
        except Exception as e:
            try:
                push_event("log", f"ControlCenter Command Error -> {e}")
            except Exception:
                pass

    def __init__(self):
        super().__init__()
        # Beyaz flash önle — UI kurulumdan önce pencereyi gizle
        self.withdraw()
        self.title("Klargus — Operasyon Merkezi")
        self.geometry("2560x1440")
        self.configure(bg="#020810")
        # ── Uygulama ikonu & AppUserModelID ──────────────────────────────
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Klargus.Assistant.1")
        except Exception:
            pass
        try:
            _ico_path = Path(__file__).resolve().parent.parent / "assets" / "klargus.ico"
            if _ico_path.exists():
                # Başlık çubuğu ikonu — hızlı, PIL gerekmez
                self.iconbitmap(str(_ico_path))
                # Taskbar ikonu (PIL ile) — arka planda yükle, pencere açılışını bloklamaz
                self._ico_path_for_taskbar = str(_ico_path)
                self.after(200, self._set_taskbar_icon)
        except Exception:
            pass
        self.minsize(1400, 800)

        global app_instance
        app_instance = self
        self.tray_manager = TrayManager(self)

        self.running = False
        self.history_items = []
        settings = load_settings()

        self.status_var  = tk.StringVar(value="Hazır")
        self.command_var = tk.StringVar(value="-")
        self.action_var  = tk.StringVar(value="-")
        self.answer_var  = tk.StringVar(value="-")
        self.window_var  = tk.StringVar(value="-")
        self.pending_var = tk.StringVar(value="Bekleyen işlem yok")
        self.timeout_var = tk.StringVar(value=str(settings.get("session_timeout", 20)))

        default_voice_test = "Operasyon brifingi hazır. Sistemler çevrim içi. Sizi dinliyorum."
        self.tts_test_var = tk.StringVar(value=settings.get("tts_test_text", default_voice_test))
        if self.tts_test_var.get().strip() == "Merhaba, ben Klargus.":
            self.tts_test_var.set(default_voice_test)

        self.hud_phase = 0
        self.hud_items = {}

        self._build_ui()
        self.after(120, self.poll_events)
        self.after(4000, self.refresh_side_info)
        self.after(200, self._set_dark_titlebar)
        # Pencere HAZIR OLANA KADAR gizli kalsın — donuk/yarım yüklü görünmesin.
        # Ses/STT yüklenince (AUDIO_AVAILABLE) ya da en geç ~25s sonra göster.
        self._ready_wait_ticks = 0
        self.after(400, self._wait_ready_then_show)
        # FAZ 1 durum göstergesi — arka planda, pencere açıldıktan sonra
        self.after(3000, self._log_faz1_status)

    def _wait_ready_then_show(self):
        """Sistem hazır olana (ses/STT yüklenene) kadar bekle, sonra göster."""
        self._ready_wait_ticks += 1
        # AUDIO_AVAILABLE True = ses sistemi/STT yüklendi → hazır.
        # Hard cap: 25s (62 × 400ms) — yükleme takılırsa yine de göster.
        if AUDIO_AVAILABLE or self._ready_wait_ticks >= 62:
            self._show_window()
        else:
            self.after(400, self._wait_ready_then_show)

    def _show_window(self):
        """Sistem hazır olunca pencereyi TAM EKRAN (maximized) göster."""
        try:
            settings = load_system_settings()
            if settings.get("start_minimized", False):
                # Gizli kalacak — tray zaten çalışıyor
                pass
            else:
                try:
                    self.state("zoomed")   # Windows: tam ekran (maximized), taskbar görünür
                except Exception:
                    pass
                self.deiconify()
                self.lift()
        except Exception:
            self.deiconify()
        # Kapat butonunu tray'e gönder
        self.protocol("WM_DELETE_WINDOW", self.handle_app_close)

    def _set_taskbar_icon(self):
        """Taskbar ikonunu ayarla — pencere açıldıktan sonra çalışır.

        İki kritik nokta:
        1. iconphoto'ya TEK büyük görüntü verilirse küçültmeyi Tk/Windows
           kalitesizce yapar → bulanık taskbar ikonu. Her boyutun KENDİ
           karesi ICO'dan okunup hepsi birden verilir; sistem uygununu seçer.
        2. AppUserModelID: ayarlanmazsa Windows pencereyi python.exe ile
           gruplar ve ikon davranışı bozulabilir.
        """
        # Windows'a bağımsız uygulama kimliği bildir
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Klargus.Jarvis.1")
        except Exception:
            pass

        try:
            from PIL import Image, ImageTk
            _tk_imgs = []
            for _size in [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)]:
                try:
                    _ico = Image.open(self._ico_path_for_taskbar)
                    if _size in (_ico.info.get("sizes") or set()):
                        _ico.size = _size
                    _ico.load()
                    _frame = _ico.convert("RGBA")
                    if _frame.size != _size:
                        _frame = _frame.resize(_size, Image.LANCZOS)
                    _tk_imgs.append(ImageTk.PhotoImage(_frame))
                except Exception:
                    continue
            if _tk_imgs:
                self.iconphoto(True, *_tk_imgs)
                self._icon_ref = _tk_imgs   # GC koruması
        except Exception:
            pass

        # ── Win32 WM_SETICON: Tk'nin ikon zincirini tamamen atla ─────────────
        # Taskbar, pencerenin BIG ikonunu kullanır. LoadImage doğru boyutu
        # ICO'dan native olarak seçer — Tk/PIL ölçeklemesi devreden çıkar.
        try:
            import ctypes
            from ctypes import wintypes
            _u32 = ctypes.windll.user32
            hwnd = wintypes.HWND(int(self.winfo_id()))
            # Tk'de gerçek üst pencere: GA_ROOT (2)
            hwnd_root = _u32.GetAncestor(hwnd, 2)
            IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
            WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
            _path = str(self._ico_path_for_taskbar)
            _cx_big   = _u32.GetSystemMetrics(11)   # SM_CXICON   (genelde 32)
            _cx_small = _u32.GetSystemMetrics(49)   # SM_CXSMICON (genelde 16)
            h_big = _u32.LoadImageW(None, _path, IMAGE_ICON, _cx_big, _cx_big, LR_LOADFROMFILE)
            h_small = _u32.LoadImageW(None, _path, IMAGE_ICON, _cx_small, _cx_small, LR_LOADFROMFILE)
            if h_big:
                _u32.SendMessageW(hwnd_root, WM_SETICON, ICON_BIG, h_big)
            if h_small:
                _u32.SendMessageW(hwnd_root, WM_SETICON, ICON_SMALL, h_small)
        except Exception:
            pass

    def _set_dark_titlebar(self):
        try:
            import ctypes
            hwnd = self.winfo_id()
            # DWMWA_CAPTION_COLOR = 35, renk COLORREF (0x00BBGGRR)
            # #020810 → R=0x02, G=0x08, B=0x10 → COLORREF = 0x00100802
            color = ctypes.c_int(0x00100802)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))
            # DWMWA_TEXT_COLOR = 36 → beyaz metin
            text_color = ctypes.c_int(0x00FFFFFF)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color))
        except Exception:
            pass

    def _log_faz1_status(self):
        """Başlangıçta Faz 1 özelliklerinin durumunu logla."""
        try:
            from audio.speech_to_text import stt_engine_status
            engine = stt_engine_status()
            active = engine.get("active", "vosk")
            if engine.get("faster_whisper_ready"):
                model  = engine.get("faster_whisper_model", "?")
                device = engine.get("faster_whisper_device", "?")
                print(f"[FAZ1] ✓ Faster-Whisper aktif: {model} / {device}", flush=True)
            else:
                print(f"[FAZ1] ⚠ STT motoru: {active} (Faster-Whisper kurulu değil)", flush=True)
        except Exception:
            pass

        try:
            from brain.llm import _model_supports_tools, get_model_name
            model = get_model_name()
            if _model_supports_tools(model):
                print(f"[FAZ1] ✓ Tool-calling aktif: {model}", flush=True)
            else:
                print(f"[FAZ1] ⚠ Tool-calling: {model} desteklenmiyor, JSON-intent kullanılıyor", flush=True)
        except Exception:
            pass

        streaming = bool(get_setting("tts_streaming", True))
        print(f"[FAZ1] {'✓' if streaming else '⚠'} Streaming TTS: {'açık' if streaming else 'kapalı'}", flush=True)

    # ── Ana UI (HUDPanel tabanlı) ─────────────────────────────────────────────
    def _build_ui(self):
        HUD_BG  = "#020810"
        BAR_BG  = "#010608"
        CYAN    = "#00d4ff"
        CYAN_DIM= "#006e99"
        self.configure(bg=HUD_BG)

        # ── Üst bar — canvas tabanlı, BAŞLAT/DURDUR ortada ──────────────────
        ACCENT    = "#00aacc"
        ACCENT_DK = "#003d52"
        RED_DK    = "#3a0a10"
        RED_LT    = "#cc2233"
        SEP_COL   = "#071620"

        bar_h = 44
        topbar = tk.Canvas(self, bg=BAR_BG, height=bar_h,
                           highlightthickness=0, bd=0)
        topbar.pack(fill="x")

        # ── Kenar pixel kapatıcı: tam siyah 1px frame ──
        tk.Frame(self, bg=BAR_BG, height=1).pack(fill="x")
        tk.Frame(self, bg=SEP_COL, height=1).pack(fill="x")

        FONT_BOLD = ("Segoe UI", 9, "bold")
        FONT_SLIM = ("Segoe UI", 9)

        # Tüm butonlar aynı sabit yükseklik ve hizalama için
        # height=bar_h, width karakter cinsinden eşit
        BTN_W = 12   # karakter genişliği — tüm butonlar eşit

        def _make_btn(text, cmd, fg, bg, hbg, hfg, font=FONT_BOLD):
            b = tk.Button(topbar, text=text, command=cmd,
                          font=font, fg=fg, bg=bg,
                          activeforeground=hfg, activebackground=hbg,
                          relief="flat", bd=0, cursor="hand2",
                          padx=16, pady=0, highlightthickness=0,
                          width=BTN_W)

            def _enter(e, btn=b, h_bg=hbg, h_fg=hfg):
                btn.config(bg=h_bg, fg=h_fg)
                # Oval hover halkası canvas üzerinde
                btn.update_idletasks()
                try:
                    bx = btn.winfo_x()
                    bw2 = btn.winfo_width()
                    px, py = 6, 5
                    topbar.delete("hover_oval")
                    topbar.create_oval(bx+px, py, bx+bw2-px, bar_h-py,
                                       outline=h_fg, width=1,
                                       tags="hover_oval")
                except Exception:
                    pass

            def _leave(e, btn=b, n_bg=bg, n_fg=fg):
                btn.config(bg=n_bg, fg=n_fg)
                topbar.delete("hover_oval")

            b.bind("<Enter>", _enter)
            b.bind("<Leave>", _leave)
            return b

        # Yan butonlar (sol)
        b_tts   = _make_btn("♩  TTS",       self.test_tts,
                            CYAN_DIM, BAR_BG, "#020f18", CYAN, FONT_SLIM)
        b_brain = _make_btn("✎  BRAIN PAD",  self.open_brain_editor,
                            CYAN_DIM, BAR_BG, "#020f18", CYAN, FONT_SLIM)
        b_cfg   = _make_btn("⚙  AYARLAR",   self.open_settings,
                            CYAN_DIM, BAR_BG, "#020f18", CYAN, FONT_SLIM)

        # Orta butonlar (aynı width)
        b_start = _make_btn("▶  BAŞLAT", self.start_agent,
                            ACCENT, ACCENT_DK, "#005066", "#00ddff")
        b_stop  = _make_btn("■  DURDUR", self.stop_agent,
                            RED_LT, RED_DK,   "#4a0e16", "#ff4455")

        def _layout(event=None):
            topbar.delete("wins")
            tw = topbar.winfo_width()
            if tw < 50:
                return

            # Tüm butonlar aynı genişlikte (BTN_W karakter → reqwidth aynı)
            for b in [b_tts, b_brain, b_cfg, b_start, b_stop]:
                b.update_idletasks()
            bw = b_start.winfo_reqwidth()   # hepsi aynı width

            gap   = 1
            total_mid = bw * 2 + gap
            mid_x = (tw - total_mid) // 2

            # Sol: TTS | BRAIN PAD | AYARLAR
            x = 8
            for b in [b_tts, b_brain, b_cfg]:
                topbar.create_window(x, 0, window=b, anchor="nw",
                                     width=bw, height=bar_h, tags="wins")
                x += bw
                topbar.create_line(x, 10, x, bar_h-10, fill=SEP_COL, tags="wins")
                x += 1

            # Orta: BAŞLAT | DURDUR
            topbar.create_window(mid_x, 0, window=b_start, anchor="nw",
                                 width=bw, height=bar_h, tags="wins")
            topbar.create_line(mid_x+bw, 6, mid_x+bw, bar_h-6,
                               fill=SEP_COL, tags="wins")
            topbar.create_window(mid_x+bw+gap, 0, window=b_stop, anchor="nw",
                                 width=bw, height=bar_h, tags="wins")

            # Alt çizgi
            topbar.create_line(0, bar_h-1, tw, bar_h-1,
                               fill=SEP_COL, width=1, tags="wins")

        topbar.bind("<Configure>", _layout)
        topbar.after(200, _layout)

        # HUD paneli (ana içerik)
        self.hud = HUDPanel(self, process_callback=self.process_text)
        self.hud.pack(fill="both", expand=True)

        # manual_entry alias (agent_loop ve manual_command için)
        self.manual_entry = self.hud.command_entry

    def _make_btn(self, parent, text, cmd, bg=None, fg=None):
        """Ayarlar / Brain Pad pencerelerinde kullanılan ortak buton fabrikası."""
        bg = bg or CARD2
        fg = fg or ACCENT
        b = tk.Button(
            parent, text=text, command=cmd,
            bg=bg, fg=fg,
            activebackground=ACCENT, activeforeground=BG,
            font=("Segoe UI", 9, "bold"),
            relief="flat", bd=0, cursor="hand2",
            padx=12, pady=5, highlightthickness=0,
        )
        b.bind("<Enter>", lambda e: b.config(bg=ACCENT, fg=BG))
        b.bind("<Leave>", lambda e: b.config(bg=bg, fg=fg))
        return b

    def log(self, message):
        # process_text() ve çağırdığı yollar arka plan thread'inden burayı
        # çağırıyor; self.hud.log() canlı bir Tk Text widget'ını mutasyona
        # uğratıyor — Tk thread-safe değildir. self.after(0, ...) ile ana
        # thread'e (mainloop) yönlendiriyoruz; sıra korunur (tek thread'li
        # kuyruk), davranış aynı kalır.
        def _do():
            try:
                self.hud.log(f"[{ts()}] {message}")
            except Exception:
                pass
        self.after(0, _do)

    def add_history(self, command, action, answer):
        # Aynı thread-safety nedeniyle ana thread'e yönlendiriliyor — bkz. log().
        def _do():
            try:
                self.hud.log(f"[{ts()}] {command} → {action} | {answer[:120]}")
                item = f"[{ts()}] {command}\n  action: {action}\n  result: {answer[:180]}\n"
                self.history_items.append(item)
                self.history_items = self.history_items[-30:]
            except Exception:
                pass
        self.after(0, _do)

    def poll_events(self):
        while True:
            shared = pop_shared_event()
            if shared is None:
                break
            try:
                event_queue.put(shared)
            except Exception:
                pass

        while not event_queue.empty():
            kind, value = event_queue.get()
            if kind == "status":
                self.status_var.set(value)
                self.hud.set_status(value)
                self.log("Durum: " + value)
            elif kind == "command":
                self.command_var.set(value if value else "-")
                self.hud.set_command(str(value or ""))
                self.log("Komut: " + str(value))
            elif kind == "action":
                self.action_var.set(value if value else "-")
                self.hud.set_action(str(value or ""))
                self.log("Action: " + str(value))
            elif kind in ("answer", "assistant"):
                self.answer_var.set(value if value else "-")
                self.hud.set_answer(str(value or ""))
                self.log("Klargus: " + str(value))
            elif kind == "assistant_token":
                current = self.answer_var.get()
                new_val = str(value) if current == "-" else current + str(value)
                self.answer_var.set(new_val)
                self.hud.set_answer(new_val)
            elif kind == "user":
                self.command_var.set(value if value else "-")
                self.hud.set_command(str(value or ""))
                self.log("Kullanici: " + str(value))
            elif kind == "error":
                self.log("Hata: " + str(value))
            elif kind == "step_start":
                self.log("Adım başladı: " + str(value))
            elif kind == "step_result":
                self.log("Adım sonucu: " + str(value))
            elif kind == "window":
                self.window_var.set(value if value else "-")
                self.hud.set_window(str(value or ""))
            elif kind == "log":
                self.log(str(value))

        self.after(120, self.poll_events)

    def refresh_side_info(self):
        pending = get_pending()
        if pending:
            pending_text = f"{pending.get('action')} | {pending.get('arg')}"
            self.pending_var.set(pending_text)
            self.hud.set_pending(pending_text)
        else:
            self.pending_var.set("Bekleyen işlem yok")
            self.hud.set_pending("—")
        try:
            active = _raw_run_tool("active_window", None)
            self.window_var.set(active)
            self.hud.set_window(str(active or "—"))
        except Exception:
            pass
        self.after(4000, self.refresh_side_info)

    def start_agent(self):
        if self.running:
            return
        self.running = True
        continuous_mode = (
            str(get_setting("wake_engine", "continuous") or "continuous").lower().strip()
            in {"continuous", "always", "live", "direct"}
            or bool(get_setting("listen_without_wake", False))
        )
        if continuous_mode:
            self.status_var.set("Aktif. Sürekli komut dinleniyor")
            self.log("Klargus başlatıldı. Sürekli dinleme modu aktif.")
        else:
            self.status_var.set("Aktif. Başlangıç sesi bekleniyor")
            self.log("Klargus başlatıldı. Mikrofon wake word bekliyor.")
        if AUDIO_AVAILABLE:
            try:
                first_line = input_device_status_text().splitlines()[0]
                self.log(f"Mikrofon: {first_line}")
            except Exception:
                pass

        def _intro_then_listen():
            if continuous_mode:
                speak("Klargus aktif.", mode="command")
            else:
                speak("Klargus aktif. Başlangıç sesi bekleniyor.", mode="command")
            time.sleep(0.12)
            if self.running:
                self.agent_loop()

        threading.Thread(target=_intro_then_listen, daemon=True).start()

    def stop_agent(self):
        self.running = False
        self.status_var.set("Durduruldu")
        self.log("Klargus durduruldu.")

    def test_tts(self):
        txt = self.tts_test_var.get().strip() or "Merhaba, ben Klargus."
        threading.Thread(target=lambda: speak(txt, mode="cinematic"), daemon=True).start()

    def manual_command(self, text):
        text = (text or "").strip()
        if not text:
            return
        try:
            self.manual_entry.delete(0, "end")
        except Exception:
            pass
        self.answer_var.set("-")
        try:
            self.hud.set_answer("")
        except Exception:
            pass
        threading.Thread(target=lambda: self.process_text(text, manual=True), daemon=True).start()

    def enroll_my_voice(self):
        def prompt(idx, total, phrase=None):
            msg = f"Ses örneği {idx}/{total} — 'Klargus' deyin"
            push_event("log", msg)
            if idx == 1:
                speak(f"Ses kaydı başlıyor. {total} kez, her uyarıda bir kez Klargus deyin. Hazır olunca başlayın.")
            # Her 5 örnekte bir sesli onay
            elif idx % 5 == 0:
                speak(f"{idx}, devam edin.")
        def log_msg(msg):
            push_event("log", msg)
        # sample_count, voice_profile içinde MIN_VALID_ENROLL_SAMPLES'a (15)
        # yükseltilir; mesajlarla tutarlı olması için açıkça 15 geçiyoruz.
        result = enroll_voice_profile(sample_count=15, prompt_callback=prompt, log_callback=log_msg)
        message = result.get("message", "Ses profili işlemi tamamlandı.")

        # Kayıt başarılıysa voice_auth otomatik aç
        if result.get("ok"):
            set_setting("voice_auth_enabled", True)
            message += "\nSes doğrulama aktif edildi. Artık 'Klargus' dediğinde sesini teyit edeceğim."

        push_event("action", "voice_enroll")
        push_event("answer", message)
        self.add_history("sesimi tanı", "voice_enroll", message)
        speak(message)
        return result

    def voice_status_text(self):
        status = voice_profile_status()
        ready = bool(status.get("ready"))
        if not ready:
            return "Henüz ses profiliniz yok. Sesimi tanı diyerek başlatabilirsiniz."
        lock_state = bool(get_setting("voice_auth_enabled", False))
        return (
            f"Ses profiliniz hazir. Ornek sayisi: {status.get('samples', 0)}. "
            f"Kalite: {status.get('quality', '-')}. "
            f"Ses kilidi: {'acik' if lock_state else 'kapali'}."
        )

    def process_text(self, text, manual=False):
        # Kullanıcıya "işleniyor" göster — LLM gecikirse boş ekran kalmasın
        push_event("answer", "…")
        self.after(0, lambda: self.answer_var.set("…"))
        try:
            low = text.lower().strip()
            low_norm = match_text(text)

            confirm_result = handle_pending_confirmation(low)
            if confirm_result is not None:
                push_event("action", "pending_confirm")
                push_event("answer", confirm_result)
                self.add_history(text, "pending_confirm", confirm_result)
                speak(confirm_result)
                refresh_session()
                return

            if is_chat_start_request(text):
                start_chat_session()
                start_session()
                result = "Sohbet modunu açtım. Beni tekrar çağırmadan konuşabilirsiniz."
                push_event("action", "chat_session_start")
                push_event("answer", result)
                self.add_history(text, "chat_session_start", result)
                speak(result)
                refresh_session()
                return

            if chat_session_active() and is_chat_end_request(text):
                end_chat_session()
                end_session()
                result = "Tamam, sohbeti kapattım. Tekrar Klargus dediğinizde dinleyeceğim."
                push_event("action", "chat_session_end")
                push_event("answer", result)
                self.add_history(text, "chat_session_end", result)
                speak(result)
                return

            correction_match = re.match(
                r"^\s*(?:yanlış duydun|yanlis duydun|duyma düzelt|duyma duzelt|stt düzelt|stt duzelt)\s*:?\s*(.+?)\s*(?:=>|->|=| yerine )\s*(.+?)\s*$",
                text, flags=re.IGNORECASE,
            )
            if correction_match:
                result = save_stt_correction(correction_match.group(1), correction_match.group(2))
                push_event("action", "stt_correction_save")
                push_event("answer", result)
                self.add_history(text, "stt_correction_save", result)
                speak(result)
                refresh_session()
                return

            if any(x in low_norm for x in ["sesimi tani", "ses profilimi olustur", "ses kaydi yap"]):
                self.enroll_my_voice()
                refresh_session()
                return

            if any(x in low_norm for x in ["ses durumunu goster", "ses profilim ne durumda"]):
                result = self.voice_status_text()
                push_event("action", "voice_status")
                push_event("answer", result)
                self.add_history(text, "voice_status", result)
                speak(result)
                refresh_session()
                return

            if "oturumu bitir" in low or "sohbeti bitir" in low or "uyku moduna gec" in low:
                end_chat_session()
                end_session()
                answer = "Tamam, bekleme moduna geçiyorum."
                push_event("action", "end_session")
                push_event("answer", answer)
                self.add_history(text, "end_session", answer)
                speak(answer)
                return

            from core.perf_report import begin as _perf_begin, finish as _perf_finish, span as _perf_span
            _perf_begin(text)

            # SovereignBrain kendi hafızasını okuyor — build_memory'ye gerek yok
            combined_memory = ""
            task_result = execute_task_chain(text, combined_memory)
            if isinstance(task_result, tuple) and len(task_result) == 3:
                answer, action, arg = task_result
            else:
                answer = str(task_result or "")
                action, arg = "unknown", text

            # Eğer modern yol işleyemediyse LLM fallback'e düş
            if not answer or answer in ("", "Pasif dinleme.", "Hazırım."):
                answer = short_answer(text, combined_memory)
                action = "llm_fallback"
                arg = text

            push_event("action", f"{action} | {arg}")
            push_event("answer", str(answer)[:800])
            self.add_history(text, f"{action} | {arg}", str(answer))
            if manual:
                self.log("Manuel komut işlendi.")
            # UI tam metni gösterir; sesli yanıt sesli sohbette ~250 karakter ile sınırlanır
            full_answer  = str(answer)
            voice_answer = _tts_clip(full_answer) if AUDIO_AVAILABLE else full_answer
            with _perf_span("TTS (seslendirme)"):
                if _STREAM_TTS_AVAILABLE and AUDIO_AVAILABLE:
                    try:
                        _speak_stream(iter([voice_answer]))
                    except Exception:
                        speak(voice_answer)
                else:
                    speak(voice_answer)
            _perf_finish(log_fn=lambda s: push_event("log", s))
            refresh_session()

        except Exception as e:
            push_event("log", f"Hata: {e}")
            push_event("answer", f"Bir hata oluştu: {type(e).__name__}")

    def open_settings(self):
        if hasattr(self, "_settings_win") and self._settings_win:
            try:
                if self._settings_win.winfo_exists():
                    self._settings_win.lift()
                    self._settings_win.focus_set()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self)
        self._settings_win = win
        win.protocol("WM_DELETE_WINDOW", lambda: (setattr(self, "_settings_win", None), win.destroy()))
        win.title("Klargus — Ayarlar")
        win.geometry("540x320")
        win.configure(bg=BG)
        card = tk.Frame(win, bg=CARD, highlightthickness=1, highlightbackground=PANEL_BORDER)
        card.pack(fill="both", expand=True, padx=12, pady=12)
        hdr = tk.Frame(card, bg=CARD2)
        hdr.pack(fill="x")
        tk.Label(hdr, text="AYARLAR", bg=CARD2, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(5,3))

        for label, var in [("Session Timeout", self.timeout_var), ("TTS Test Metni", self.tts_test_var)]:
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", padx=12, pady=5)
            tk.Label(row, text=label, bg=CARD, fg=MUTED, width=16, anchor="w", font=("Segoe UI", 9)).pack(side="left")
            ef = tk.Frame(row, bg=CARD, highlightthickness=1, highlightbackground=PANEL_BORDER)
            ef.pack(side="left", fill="x", expand=True)
            tk.Entry(ef, textvariable=var, bg=CARD, fg=TEXT, insertbackground=ACCENT,
                      relief="flat", borderwidth=0, font=("Segoe UI", 9)).pack(fill="x", ipady=6, padx=4)

        def save_gui_settings():
            try:
                timeout = int(self.timeout_var.get().strip())
                if timeout < 3: timeout = 3
                set_setting("session_timeout", timeout)
                set_setting("tts_test_text", self.tts_test_var.get().strip() or "Klargus aktif.")
                self.log(f"Ayarlar kaydedildi. timeout={timeout}")
                messagebox.showinfo("Klargus", "Ayarlar kaydedildi.")
            except Exception as e:
                messagebox.showerror("Klargus", f"Hata: {e}")

        btns = tk.Frame(card, bg=CARD)
        btns.pack(fill="x", padx=12, pady=12)
        self._make_btn(btns, "Kaydet", save_gui_settings, ACCENT).pack(side="left", padx=3)
        self._make_btn(btns, "TTS Test", self.test_tts, INFO).pack(side="left", padx=3)
        self._make_btn(btns, "Tam Ayarlar", self.open_settings_panel, "#1e3a5c", fg=TEXT).pack(side="left", padx=3)
        self._make_btn(btns, "Kapat", win.destroy, DANGER, fg=TEXT).pack(side="right", padx=3)

    def open_brain_editor(self):
        if hasattr(self, "_brain_win") and self._brain_win:
            try:
                if self._brain_win.winfo_exists():
                    self._brain_win.lift()
                    self._brain_win.focus_set()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self)
        self._brain_win = win
        win.protocol("WM_DELETE_WINDOW", lambda: (setattr(self, "_brain_win", None), win.destroy()))
        win.title("Brain Pad Editor")
        win.geometry("800x640")
        win.configure(bg=BG)
        outer = tk.Frame(win, bg=CARD, highlightthickness=1, highlightbackground=PANEL_BORDER)
        outer.pack(fill="both", expand=True, padx=12, pady=12)
        hdr = tk.Frame(outer, bg=CARD2)
        hdr.pack(fill="x")
        tk.Label(hdr, text="✎  BRAIN PAD EDITOR", bg=CARD2, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(6,4))
        editor = scrolledtext.ScrolledText(
            outer, bg="#020b18", fg="#a8d8f0",
            insertbackground=ACCENT, font=("Consolas", 10),
            relief="flat", borderwidth=0, wrap="word",
        )
        editor.pack(fill="both", expand=True, padx=10, pady=(4, 4))
        content = ""
        if BRAINPAD_FILE.exists():
            try: content = BRAINPAD_FILE.read_text(encoding="utf-8")
            except Exception: content = ""
        editor.insert("1.0", content)
        btns = tk.Frame(outer, bg=CARD)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        def save_editor():
            try:
                _bp_tmp = BRAINPAD_FILE.with_suffix(".tmp")
                _bp_tmp.write_text(editor.get("1.0", "end").rstrip() + "\n", encoding="utf-8")
                _bp_tmp.replace(BRAINPAD_FILE)
                self.log("Brain Pad kaydedildi.")
                messagebox.showinfo("Klargus", "Brain Pad kaydedildi.")
            except Exception as e:
                messagebox.showerror("Klargus", f"Kaydetme hatası: {e}")
        self._make_btn(btns, "Kaydet", save_editor, ACCENT).pack(side="left", padx=3)
        self._make_btn(btns, "Kapat", win.destroy, DANGER, fg=TEXT).pack(side="right", padx=3)

    def agent_loop(self):
        # Arka planda yükleniyorsa maks 5 s bekle
        if not AUDIO_AVAILABLE:
            for _ in range(50):
                time.sleep(0.1)
                if AUDIO_AVAILABLE:
                    break
        if not AUDIO_AVAILABLE:
            push_event("status", "Ses sistemi yüklenemedi — metin modu")
            push_event("log", "Ses sistemi kullanılamıyor. Komutları metin kutusundan girin.")
            return

        empty_command_count = 0
        last_processed_text = ""
        last_processed_at = 0.0
        empty_in_session = 0
        MAX_EMPTY_IN_SESSION = 4   # ~32s sessizlik sonrası oturum biter

        # Oturumu kapatan son sözcükler — SADECE net veda ifadeleri
        # "tamam", "teşekkürler", "dur", "bekle", "sağol", "yeter", "kapat"
        # gibi kelimeler sohbet ortasında da söylenir — buraya EKLEME
        _SESSION_END_PHRASES = frozenset([
            "görüşürüz", "görüşelim", "bay bay",
            "hoşçakal", "bye", "çıkış",
            "anlaştık", "bitti",
        ])

        while self.running:
            try:
                buffered_command = ""
                continuous_mode = (
                    str(get_setting("wake_engine", "continuous") or "continuous").lower().strip()
                    in {"continuous", "always", "live", "direct"}
                    or bool(get_setting("listen_without_wake", False))
                )
                chat_mode = chat_session_active()
                if chat_mode:
                    refresh_session()
                    push_event("status", "Sohbet modu aktif")
                elif not session_active():
                    empty_command_count = 0
                    if continuous_mode:
                        start_session()
                        push_event("status", "Sürekli dinleme aktif")
                        push_event("log", "Wake word beklenmiyor. Doğrudan komut dinleniyor.")
                    else:
                        push_event("status", "Wake word bekleniyor")
                        push_event("log", "Başlangıç sesi bekleniyor.")
                        wake_ok = listen_for_wake_word(should_stop=lambda: not self.running)
                        if not wake_ok:
                            continue
                        if not self.running:
                            break

                        # ── Wake word'de ses doğrulama ────────────────────────
                        # Ses profili varsa wake word anında kimliği teyit et
                        # Oturum içinde artık ses doğrulama YAPILMAZ
                        try:
                            from audio.voice_profile import voice_profile_status as _vps, verify_voice
                            _vs = _vps()
                            if _vs.get("ready") and get_setting("voice_auth_enabled", False):
                                import sounddevice as _sd
                                import numpy as _np
                                _sr = 16000
                                _rec = _sd.rec(int(0.8 * _sr), samplerate=_sr,
                                               channels=1, dtype="float32")
                                _sd.wait()
                                _audio = (_rec.flatten() * 32767).astype(_np.int16).tobytes()
                                _vr = verify_voice(_audio)
                                if not _vr.get("verified", False):
                                    push_event("log", f"[SES] Doğrulama başarısız: benzerlik={_vr.get('similarity',0):.2f}")
                                    speak("Sizi tanıyamadım.")
                                    continue
                                push_event("log", f"[SES] Doğrulandı: benzerlik={_vr.get('similarity',0):.2f}")
                        except Exception as _ve:
                            push_event("log", f"[SES] Doğrulama atlandı: {_ve}")

                        start_session()
                        push_event("status", "Komut dinleniyor")
                        push_event("log", "Başlangıç sesi algılandı. Komut dinleniyor.")
                        buffered_command = (pop_pending_wake_command() or "").strip()
                        if not buffered_command:
                            # Chime modu: 80ms bip — mikrofon ~1.3s daha erken açılır
                            if get_setting("tts_chime_enabled", False):
                                from audio.text_to_speech import play_chime
                                play_chime()
                            else:
                                speak("Dinliyorum.")
                else:
                    push_event("status", "Sürekli dinleme aktif" if continuous_mode else "Sohbet oturumu aktif")

                active = _raw_run_tool("active_window", None)
                push_event("window", active)

                if not buffered_command:
                    buffered_command = (pop_pending_wake_command() or "").strip()
                if buffered_command:
                    command_result = {
                        "text": buffered_command,
                        "engine": "wake_whisper_buffer",
                        "source": "wake_word",
                        "status": "ok",
                    }
                    push_event("log", f"Wake cümlesinden komut alındı: {buffered_command}")
                else:
                    command_result = listen_command_detailed()

                text = (command_result.get("text") or "").strip()
                engine_name = command_result.get("engine") or command_result.get("source") or "stt"
                push_event("log", f"STT motoru: {engine_name}")

                if not text:
                    status = command_result.get("status", "empty")
                    if continuous_mode or chat_mode:
                        refresh_session()
                        empty_in_session = 0
                    elif session_active():
                        # Sohbet/oturum aktifken boş dinleme sayacını artır.
                        # MAX_EMPTY_IN_SESSION'a ulaşıldığında wake word'e dön.
                        empty_in_session += 1
                        if empty_in_session >= MAX_EMPTY_IN_SESSION:
                            end_session()
                            empty_in_session = 0
                        else:
                            refresh_session()   # sessizliği oturumu kesmez
                    else:
                        end_session()
                        empty_in_session = 0
                    push_event("status", "Sohbet modu aktif" if chat_mode else ("Sürekli dinleme aktif" if continuous_mode else "Wake word bekleniyor"))
                    push_event("log", f"Komut alınmadı: {status}.")
                    continue

                wake_command = (extract_command_after_wake(text) or "").strip()
                if wake_command:
                    push_event("log", f"Wake kelimesi temizlendi. Komut: {wake_command}")
                    text = wake_command
                elif is_wake_phrase(text):
                    start_session()
                    push_event("status", "Komut dinleniyor")
                    push_event("answer", "Dinliyorum.")
                    push_event("log", "Wake word duyuldu; komut bekleniyor.")
                    if get_setting("tts_chime_enabled", False):
                        from audio.text_to_speech import play_chime
                        play_chime()
                    else:
                        speak("Dinliyorum.", mode="command")
                    continue

                empty_command_count = 0
                normalized_text = " ".join(text.lower().split())
                now = time.time()
                if normalized_text and normalized_text == last_processed_text and (now - last_processed_at) < 18:
                    if not continuous_mode and not chat_mode:
                        end_session()
                    push_event("log", f"Tekrarlanan komut yok sayıldı: {text}")
                    continue

                # Oturum içinde ses doğrulama YOK — doğrulama sadece wake word'de yapıldı
                # Ses benzerlik verisini sadece loglama için kullan
                voice = command_result.get("voice", {}) or {}
                if voice.get("ready"):
                    similarity = float(voice.get("similarity", 0.0) or 0.0)
                    push_event("log", f"[SES] benzerlik={similarity:.2f}")

                push_event("command", text)
                self.after(0, lambda: self.answer_var.set("-"))
                last_processed_text = normalized_text
                last_processed_at = now

                # Oturum bitiş ifadesi mi?
                _low_text = text.lower().strip()
                if _low_text in _SESSION_END_PHRASES or \
                   any(_low_text.startswith(p) for p in ["görüşürüz", "bay bay", "hoşçakal"]):
                    end_chat_session()   # sohbet modu da kapat — sonraki çağrıda wake word gereksin
                    end_session()
                    push_event("log", "Oturum sona erdi — bitiş ifadesi algılandı.")
                    push_event("status", "Wake word bekleniyor")
                    continue

                self.process_text(text)
                empty_in_session = 0   # başarılı komut → sessizlik sayacını sıfırla
                chat_still_active = chat_session_active()
                if chat_still_active:
                    refresh_chat_session()
                    refresh_session()
                elif continuous_mode:
                    refresh_session()
                else:
                    # Sohbet ortasında session'ı kesmiyoruz:
                    # Kullanıcı komut verdi → oturum devam ediyor.
                    # MAX_EMPTY_IN_SESSION kez üst üste ses gelmezse wake word'e dönülür.
                    refresh_session()
                push_event("status", "Sohbet modu aktif" if chat_still_active else ("Sürekli dinleme aktif" if continuous_mode else "Wake word bekleniyor"))
                push_event("log", "Komut tamamlandı.")

            except Exception as e:
                push_event("log", f"Hata: {e}")
                push_event("status", "Hata")


def main():
    print("Klargus Faz 1 paneli başlatılıyor...", flush=True)
    clear_pending()
    app = ProPanel()
    print("Klargus hazır.", flush=True)
    app.mainloop()


if __name__ == "__main__":
    main()
