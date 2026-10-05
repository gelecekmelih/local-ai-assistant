from brain.tool_registry import register_tool
from brain.tool_result import ToolResult
from core.logger import get_logger
from vision.screen_actions_v2 import read_screen_text_v2, find_buttons_v2, click_center

log = get_logger("vision_tools")


def tool_screen_read(arg=None):
    """
    Ekranı okur ve Türkçe, bağlamsal yorum döndür.
    Vision LLM + OCR + UIA kombinasyonu ile mükemmel anlama.

    arg: Kullanıcının isteği veya sorusu (varsa — odaklı yorum için).
         Örn: "bu kodda hata var mı" veya "şu an ne çalıyor"
    """
    user_question = str(arg or "").strip()

    # Birincil: Yeni birleşik motor (vision + OCR + UIA)
    try:
        from vision.screen_understanding import understand_screen
        return understand_screen(user_question)
    except Exception as e:
        # NOT (2026-10-03): eskiden burası tamamen sessizdi (except: pass) —
        # ~870 satırlık birincil vision motoru her başarısız olduğunda hiçbir
        # iz kalmadan en zayıf (ham OCR) fallback'e düşülüyordu, bu da gerçek
        # arızayı teşhis etmeyi imkansız kılıyordu.
        log.warning("understand_screen başarısız, fallback'e düşülüyor: %s", e)

    # 1. Fallback: text LLM tabanlı interpreter
    try:
        from vision.screen_interpreter import interpret_screen
        return interpret_screen(user_question=user_question)
    except Exception as e:
        log.debug("interpret_screen fallback başarısız: %s", e)

    # 2. Fallback: ham OCR (read_screen_text_v2 zaten ToolResult → passthrough)
    try:
        return read_screen_text_v2() or ToolResult(message="Ekran okunamadı.", ok=False, error="okunamadi")
    except Exception as e:
        log.debug("OCR fallback da başarısız: %s", e)
        return ToolResult(message="Ekran okunamadı.", ok=False, error="okunamadi")


def tool_find_buttons(arg=None):
    return find_buttons_v2()


def tool_click_center(arg=None):
    return click_center()


def tool_find_and_click(arg=None):
    """Ekranda metni bulur ve tıklar. arg = hedef metin"""
    from vision.find_and_click import find_and_click
    return find_and_click(str(arg or ""))


def tool_find_and_double_click(arg=None):
    from vision.find_and_click import find_and_double_click
    return find_and_double_click(str(arg or ""))


register_tool("screen_read",           tool_screen_read)
register_tool("find_buttons",          tool_find_buttons)
register_tool("click_center",          tool_click_center)
register_tool("find_and_click",        tool_find_and_click)
register_tool("find_and_double_click", tool_find_and_double_click)
