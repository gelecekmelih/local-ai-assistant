import subprocess
import threading
import time
import json
import webbrowser
import pyautogui
from pathlib import Path
from control.program_index import build_program_index, open_program
from brain.tool_result import ToolResult


def _start(cmd: str) -> None:
    """Windows 'start' komutunu subprocess ile güvenli çalıştır."""
    subprocess.Popen(
        ["cmd", "/c", "start", "", cmd],
        shell=False,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATA_DIR.mkdir(exist_ok=True)

LEARNED_COMMANDS_FILE = DATA_DIR / "learned_commands.json"


def _load_learned():
    if not LEARNED_COMMANDS_FILE.exists():
        LEARNED_COMMANDS_FILE.write_text("{}", encoding="utf-8")
        return {}
    try:
        return json.loads(LEARNED_COMMANDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_learned(data):
    """Atomik yazma — yarım kalırsa öğrenilen komutlar korunur."""
    tmp = LEARNED_COMMANDS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LEARNED_COMMANDS_FILE)


def _normalize(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _extract_program_name(text):
    t = text.lower().strip()

    suffixes = [" aç", " ac", " başlat", " baslat", " çalıştır", " calistir"]
    prefixes = ["program ", "uygulama ", "oyun "]

    for s in suffixes:
        if t.endswith(s):
            t = t[:-len(s)].strip()
            break

    for p in prefixes:
        if t.startswith(p):
            t = t[len(p):].strip()

    return t.strip()


def _learn_from_text(text):
    t = text.strip()

    variants = [
        ("bundan sonra ", " dediğimde ", " çalıştır"),
        ("bundan sonra ", " dedigimde ", " calistir"),
        ("bundan sonra ", " deyince ", " çalıştır"),
        ("bundan sonra ", " deyince ", " calistir"),
        ("şunu öğren ", " olunca ", " çalıştır"),
        ("sunu ogren ", " olunca ", " calistir"),
    ]

    low = t.lower()

    for a, b, c in variants:
        if a in low and b in low and c in low:
            start = low.find(a) + len(a)
            mid = low.find(b, start)
            end = low.rfind(c)

            if mid != -1 and end != -1 and mid < end:
                trigger = t[start:mid].strip()
                command = t[mid + len(b):end].strip()

                if trigger and command:
                    data = _load_learned()
                    data[_normalize(trigger)] = command
                    _save_learned(data)
                    return ToolResult(
                        message=f"Öğrendim. Artık '{trigger}' dediğinde bunu yapacağım.",
                        ok=True, data={trigger: command})

    return None


_LEARNED_BLOCKLIST = {
    "rm", "del", "format", "rd", "rmdir", "reg", "regedit",
    "shutdown", "restart", "powershell", "cmd", "wscript", "cscript",
    "curl", "wget", "bitsadmin", "certutil", "schtasks",
    "netsh", "bcdedit", "diskpart", "cipher", "mklink",
    "eval", "exec", "python", "py",
}

def _run_learned(text):
    data = _load_learned()
    key = _normalize(text)

    if key not in data:
        return None

    cmd = data[key]
    # Güvenlik: tehlikeli komutları engelle
    first_word = cmd.strip().lower().split()[0] if cmd.strip() else ""
    first_word_clean = first_word.replace(".exe", "")
    if first_word_clean in _LEARNED_BLOCKLIST:
        return ToolResult(
            message=f"Güvenlik: '{first_word}' komutu öğrenilmiş komutlarda çalıştırılamaz.",
            ok=True)
    # Shell zincirleme/yönlendirme meta-karakterlerini engelle
    _SHELL_META = ("&", "|", ";", ">", "<", "`", "$(")
    if any(c in cmd for c in _SHELL_META):
        return ToolResult(
            message="Güvenlik: Komut zinciri veya yönlendirme içeren komutlar çalıştırılamaz.",
            ok=True)

    try:
        # os.system yerine subprocess ile daha güvenli çalıştır
        subprocess.Popen(
            cmd, shell=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return ToolResult(message=f"Öğrendiğim komut çalıştırıldı: {text}", ok=True)
    except Exception as e:
        return ToolResult(message=f"Öğrendiğim komut çalıştırılamadı: {e}",
                          ok=False, error=str(e))


def run_command(text):
    t = text.lower().strip()

    learned_message = _learn_from_text(text)
    if learned_message:
        return learned_message

    learned_run = _run_learned(text)
    if learned_run:
        return learned_run

    if "programlari tara" in t or "programları tara" in t:
        programs = build_program_index()
        return ToolResult(message=f"{len(programs)} program veya kisayol tarandi",
                          ok=True, data=len(programs))

    if "spotify aç" in t or "spotify ac" in t:
        _start("spotify")
        return ToolResult(message="Spotify aciliyor", ok=True)

    if "steam aç" in t or "steam ac" in t:
        _start("steam")
        return ToolResult(message="Steam aciliyor", ok=True)

    if "chrome aç" in t or "chrome ac" in t:
        _start("chrome")
        return ToolResult(message="Chrome aciliyor", ok=True)

    if "discord aç" in t or "discord ac" in t:
        _start("discord")
        return ToolResult(message="Discord aciliyor", ok=True)

    if "dosya gezgini aç" in t or "dosya gezgini ac" in t or "klasorleri ac" in t or "klasörleri aç" in t:
        _start("explorer")
        return ToolResult(message="Dosya gezgini aciliyor", ok=True)

    if "not defteri aç" in t or "not defteri ac" in t:
        _start("notepad")
        return ToolResult(message="Not defteri aciliyor", ok=True)

    if "hesap makinesi aç" in t or "hesap makinesi ac" in t:
        _start("calc")
        return ToolResult(message="Hesap makinesi aciliyor", ok=True)

    if "görev yöneticisi aç" in t or "gorev yoneticisi ac" in t or "görev yöneticisi ac" in t:
        _start("taskmgr")
        return ToolResult(message="Gorev yoneticisi aciliyor", ok=True)

    if "cs2 başlat" in t or "cs2 baslat" in t or "counter başlat" in t or "counter baslat" in t or "counter strike başlat" in t or "counter strike baslat" in t:
        webbrowser.open("steam://rungameid/730")
        return ToolResult(message="Counter Strike baslatiliyor", ok=True)

    if "elden ring başlat" in t or "elden ring baslat" in t:
        webbrowser.open("steam://rungameid/1245620")
        return ToolResult(message="Elden Ring baslatiliyor", ok=True)

    if "spotifyda" in t and ("çal" in t or "cal" in t):
        song = t.replace("spotifyda", "").replace("çal", "").replace("cal", "").strip()
        if song:
            webbrowser.open(f"https://open.spotify.com/search/{song}")
            return ToolResult(message=f"{song} için Spotify araması açıldı", ok=True, data=song)

    if "youtube'da" in t and ("aç" in t or "ac" in t):
        q = t.replace("youtube'da", "").replace("aç", "").replace("ac", "").strip()
        if q:
            webbrowser.open(f"https://www.youtube.com/results?search_query={q}")
            return ToolResult(message=f"{q} için YouTube açıldı", ok=True, data=q)

    if "google'da" in t and ("ara" in t):
        q = t.replace("google'da", "").replace("ara", "").strip()
        if q:
            webbrowser.open(f"https://www.google.com/search?q={q}")
            return ToolResult(message=f"{q} için Google araması açıldı", ok=True, data=q)

    if "anneme mesaj yaz" in t:
        def _send_anne():
            webbrowser.open("https://web.whatsapp.com")
            time.sleep(8)
            pyautogui.write("Anne", interval=0.05)
            pyautogui.press("enter")
            time.sleep(1.5)
            pyautogui.write("Merhaba anne", interval=0.03)
            pyautogui.press("enter")
        threading.Thread(target=_send_anne, daemon=True).start()
        return ToolResult(message="WhatsApp üzerinden annene mesaj gönderiliyor...", ok=True)

    if "whatsapptan anneme" in t and "yaz" in t:
        msg = t.split("yaz", 1)[-1].strip() or "Merhaba anne"
        def _send_whatsapp(msg=msg):
            webbrowser.open("https://web.whatsapp.com")
            time.sleep(8)
            pyautogui.write("Anne", interval=0.05)
            pyautogui.press("enter")
            time.sleep(1.5)
            pyautogui.write(msg, interval=0.03)
            pyautogui.press("enter")
        threading.Thread(target=_send_whatsapp, daemon=True).start()
        return ToolResult(message="WhatsApp mesajı gönderiliyor...", ok=True)

    action_words = [" aç", " ac", " başlat", " baslat", " çalıştır", " calistir"]
    if any(t.endswith(w) for w in action_words):
        program_name = _extract_program_name(t)
        result = open_program(program_name)
        if result:
            return result

    return None


def init():
    """Komut modülünü başlat (program indeksi vb.). Geriye dönük uyumluluk."""
    try:
        build_program_index()
    except Exception:
        pass
