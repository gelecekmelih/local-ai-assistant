from brain.tool_registry import register_tool
from vision.window_actions import (
    list_windows,
    get_active_window_title,
    focus_window,
    close_window,
    minimize_window,
    maximize_window,
    switch_window,
    close_active_window,
)

def tool_list_windows(arg=None):
    return list_windows()

def tool_active_window(arg=None):
    return get_active_window_title()

def tool_focus_window(arg=None):
    return focus_window(arg)

def tool_close_window(arg=None):
    return close_window(arg)

def tool_minimize_window(arg=None):
    return minimize_window(arg)

def tool_maximize_window(arg=None):
    return maximize_window(arg)

def tool_switch_window(arg=None):
    return switch_window()

def tool_close_active_window(arg=None):
    return close_active_window()

register_tool("list_windows", tool_list_windows)
register_tool("active_window", tool_active_window)
register_tool("focus_window", tool_focus_window)
register_tool("close_window", tool_close_window)
register_tool("minimize_window", tool_minimize_window)
register_tool("maximize_window", tool_maximize_window)
register_tool("switch_window", tool_switch_window)
register_tool("close_active_window", tool_close_active_window)

