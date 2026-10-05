import pyautogui
import time

from brain.tool_result import ToolResult

pyautogui.FAILSAFE = True


def scroll_up(amount=500) -> ToolResult:
    try:
        pyautogui.scroll(amount)
        return ToolResult(message="Yukarı kaydırıldı", ok=True)
    except Exception as e:
        return ToolResult(message=f"Scroll yapılamadı: {e}", ok=False, error=str(e))


def scroll_down(amount=500) -> ToolResult:
    try:
        pyautogui.scroll(-amount)
        return ToolResult(message="Aşağı kaydırıldı", ok=True)
    except Exception as e:
        return ToolResult(message=f"Scroll yapılamadı: {e}", ok=False, error=str(e))


def double_click() -> ToolResult:
    try:
        pyautogui.doubleClick()
        return ToolResult(message="Çift tık yapıldı", ok=True)
    except Exception as e:
        return ToolResult(message=f"Çift tık yapılamadı: {e}", ok=False, error=str(e))


def right_click() -> ToolResult:
    try:
        pyautogui.rightClick()
        return ToolResult(message="Sağ tık yapıldı", ok=True)
    except Exception as e:
        return ToolResult(message=f"Sağ tık yapılamadı: {e}", ok=False, error=str(e))


def type_text(text: str):
    """Metni yaz. Türkçe karakterler için clipboard yöntemini kullan.
    pyautogui.write() yalnızca ASCII güvenlidir; ş,ğ,ı,ç,ö,ü bozulur."""
    try:
        if not text:
            return ToolResult(message="Yazılacak metin yok", ok=True)

        # Tüm karakterler ASCII ise doğrudan yaz (daha hızlı)
        if text.isascii():
            pyautogui.write(text, interval=0.02)
            return ToolResult(message=f"Yazıldı: {text}", ok=True, data=text)

        # Türkçe/özel karakter varsa → clipboard üzerinden yapıştır
        try:
            import pyperclip
            old_clip = pyperclip.paste()          # eski panoyu sakla
            pyperclip.copy(text)
            time.sleep(0.05)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.05)
            pyperclip.copy(old_clip)              # panoyu eski hâline döndür
            return ToolResult(message=f"Yazıldı (clipboard): {text}", ok=True, data=text)
        except ImportError:
            pass

        # pyperclip yoksa PowerShell ile clipboard set et
        import subprocess
        escaped = text.replace("'", "''")
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{escaped}'"],
            capture_output=True, timeout=4,
        )
        time.sleep(0.08)
        pyautogui.hotkey("ctrl", "v")
        return ToolResult(message=f"Yazıldı (PS-clipboard): {text}", ok=True, data=text)

    except Exception as e:
        return ToolResult(message=f"Yazma hatası: {e}", ok=False, error=str(e))


def press_key(key: str) -> ToolResult:
    try:
        pyautogui.press(key)
        return ToolResult(message=f"{key} tuşuna basıldı", ok=True)
    except Exception as e:
        return ToolResult(message=f"Tuş basılamadı: {e}", ok=False, error=str(e))


def hotkey(keys: str) -> ToolResult:
    try:
        parts = [k.strip() for k in keys.split("+") if k.strip()]
        if not parts:
            return ToolResult(message="Geçersiz tuş kombinasyonu", ok=True)

        pyautogui.hotkey(*parts)
        return ToolResult(message=f"Tuş kombinasyonu: {'+'.join(parts)}", ok=True)
    except Exception as e:
        return ToolResult(message=f"Kombinasyon çalışmadı: {e}", ok=False, error=str(e))
