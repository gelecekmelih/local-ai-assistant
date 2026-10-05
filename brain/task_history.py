import json
import os
import threading
from datetime import datetime
from core.path_utils import project_path

HISTORY_PATH = project_path(os.path.join("data", "task_history.json"))
_history_lock = threading.Lock()


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_history(data):
    """Atomik yazma — yarım kalırsa orijinal korunur."""
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, HISTORY_PATH)


def log_task(user_text, action, arg, result):
    with _history_lock:
        history = load_history()
        history.append({
            "time":   datetime.now().isoformat(),
            "input":  user_text,
            "action": action,
            "arg":    arg,
            "result": str(result)[:200],
        })
        save_history(history[-100:])
