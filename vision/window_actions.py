import pyautogui
import pygetwindow as gw
import time

from brain.tool_result import ToolResult


def _all_titles():
    titles = []
    for w in gw.getAllWindows():
        try:
            title = (w.title or "").strip()
            if title:
                titles.append(title)
        except Exception:
            pass
    return titles


def list_windows() -> ToolResult:
    try:
        titles = _all_titles()
        if not titles:
            return ToolResult(message="Açık pencere bulunamadı.", ok=False, error="pencere_yok")
        return ToolResult(message="Açık pencereler:\n- " + "\n- ".join(titles[:30]),
                          ok=True, data=titles)
    except Exception as e:
        return ToolResult(message=f"Pencereler listelenemedi: {e}", ok=False, error=str(e))


def get_active_window_title() -> ToolResult:
    try:
        w = gw.getActiveWindow()
        if not w:
            return ToolResult(message="Aktif pencere bulunamadı.", ok=False, error="pencere_yok")
        title = (w.title or "").strip()
        return ToolResult(message=title if title else "Aktif pencerenin başlığı boş.",
                          ok=True, data=title)
    except Exception as e:
        return ToolResult(message=f"Aktif pencere okunamadı: {e}", ok=False, error=str(e))


def _find_window(target: str):
    target = (target or "").strip().lower()
    if not target:
        return None

    for w in gw.getAllWindows():
        try:
            title = (w.title or "").strip()
            if title and target in title.lower():
                return w
        except Exception:
            pass

    return None


def focus_window(target: str) -> ToolResult:
    try:
        w = _find_window(target)
        if not w:
            return ToolResult(message=f"'{target}' penceresi bulunamadı.", ok=False, error="pencere_yok")

        if w.isMinimized:
            w.restore()
            time.sleep(0.2)

        w.activate()
        return ToolResult(message=f"'{w.title}' penceresi öne alındı.", ok=True)
    except Exception as e:
        return ToolResult(message=f"Pencere odaklanamadı: {e}", ok=False, error=str(e))


def close_window(target: str) -> ToolResult:
    try:
        w = _find_window(target)
        if not w:
            return ToolResult(message=f"'{target}' penceresi bulunamadı.", ok=False, error="pencere_yok")

        w.close()
        return ToolResult(message=f"'{w.title}' penceresi kapatıldı.", ok=True)
    except Exception as e:
        return ToolResult(message=f"Pencere kapatılamadı: {e}", ok=False, error=str(e))


def minimize_window(target: str) -> ToolResult:
    try:
        w = _find_window(target)
        if not w:
            return ToolResult(message=f"'{target}' penceresi bulunamadı.", ok=False, error="pencere_yok")

        w.minimize()
        return ToolResult(message=f"'{w.title}' penceresi küçültüldü.", ok=True)
    except Exception as e:
        return ToolResult(message=f"Pencere küçültülemedi: {e}", ok=False, error=str(e))


def maximize_window(target: str) -> ToolResult:
    try:
        w = _find_window(target)
        if not w:
            return ToolResult(message=f"'{target}' penceresi bulunamadı.", ok=False, error="pencere_yok")

        w.maximize()
        return ToolResult(message=f"'{w.title}' penceresi büyütüldü.", ok=True)
    except Exception as e:
        return ToolResult(message=f"Pencere büyütülemedi: {e}", ok=False, error=str(e))


def switch_window() -> ToolResult:
    try:
        pyautogui.hotkey("alt", "tab")
        return ToolResult(message="Pencere değiştirildi.", ok=True)
    except Exception as e:
        return ToolResult(message=f"Alt tab yapılamadı: {e}", ok=False, error=str(e))


def close_active_window() -> ToolResult:
    try:
        pyautogui.hotkey("alt", "f4")
        return ToolResult(message="Aktif pencere kapatma komutu gönderildi.", ok=True)
    except Exception as e:
        return ToolResult(message=f"Aktif pencere kapatılamadı: {e}", ok=False, error=str(e))
