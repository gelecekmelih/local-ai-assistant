from brain.tool_registry import register_tool
from vision.input_actions import (
    scroll_up,
    scroll_down,
    double_click,
    right_click,
    type_text,
    press_key,
    hotkey,
)

def tool_scroll_up(arg=None):
    return scroll_up()

def tool_scroll_down(arg=None):
    return scroll_down()

def tool_double_click(arg=None):
    return double_click()

def tool_right_click(arg=None):
    return right_click()

def tool_type_text(arg=None):
    return type_text(arg)

def tool_press_key(arg=None):
    return press_key(arg)

def tool_hotkey(arg=None):
    return hotkey(arg)

register_tool("scroll_up", tool_scroll_up)
register_tool("scroll_down", tool_scroll_down)
register_tool("double_click", tool_double_click)
register_tool("right_click", tool_right_click)
register_tool("type_text", tool_type_text)
register_tool("press_key", tool_press_key)
register_tool("hotkey", tool_hotkey)



# KLARGUS_INPUT_COMPAT_FIX
# Executor bazen arg=... keyword ile çağırır. Bu wrapperlar iki çağrı biçimini de destekler.
try:
    _orig_type_text = type_text
    def type_text(text=None, arg=None, **kwargs):  # type: ignore[misc]  # kasıtlı uyumluluk wrapper'ı (imza farklı)
        value = arg if arg is not None else text
        return _orig_type_text(value or "")

    _orig_press_key = press_key
    def press_key(key=None, arg=None, **kwargs):  # type: ignore[misc]  # kasıtlı uyumluluk wrapper'ı (imza farklı)
        value = arg if arg is not None else key
        return _orig_press_key(value or "")

    _orig_hotkey = hotkey
    def hotkey(keys=None, arg=None, **kwargs):  # type: ignore[misc]  # kasıtlı uyumluluk wrapper'ı (imza farklı)
        value = arg if arg is not None else keys
        return _orig_hotkey(value or "")
except Exception:
    pass
