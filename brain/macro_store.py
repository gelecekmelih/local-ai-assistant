import json
import os
import threading
from core.path_utils import project_path

MACRO_PATH   = project_path(os.path.join("data", "replay_macros.json"))
_macro_lock  = threading.Lock()


def _ensure_file():
    os.makedirs(os.path.dirname(MACRO_PATH), exist_ok=True)
    if not os.path.exists(MACRO_PATH):
        tmp = MACRO_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MACRO_PATH)


def load_macros():
    _ensure_file()
    try:
        with open(MACRO_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_macros(data):
    """Atomik yazma — yarım kalırsa orijinal korunur."""
    os.makedirs(os.path.dirname(MACRO_PATH), exist_ok=True)
    tmp = MACRO_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MACRO_PATH)


def register_macro(trigger_text, target_text):
    with _macro_lock:
        data = load_macros()
        key   = (trigger_text or "").strip().lower()
        value = (target_text or "").strip()
        if not key or not value:
            return False
        data[key] = {"target": value}
        save_macros(data)
    return True


def get_macro(trigger_text):
    data = load_macros()
    key  = (trigger_text or "").strip().lower()
    item = data.get(key)
    if not item:
        return None
    return item.get("target")


def delete_macro(trigger_text):
    with _macro_lock:
        data = load_macros()
        key  = (trigger_text or "").strip().lower()
        if key in data:
            del data[key]
            save_macros(data)
            return True
    return False


def list_macros():
    data = load_macros()
    if not data:
        return "Henüz kayıtlı makro yok."
    lines = []
    for i, (k, v) in enumerate(data.items(), start=1):
        lines.append(f"{i}. {k} => {v.get('target', '-')}")
    return "\n".join(lines)
