import threading

_lock = threading.Lock()
_pending_approval = None


def set_pending_approval(step: dict):
    global _pending_approval
    with _lock:
        _pending_approval = step


def get_pending_approval():
    with _lock:
        return _pending_approval


def clear_pending_approval():
    global _pending_approval
    with _lock:
        _pending_approval = None


def get_and_clear_pending() -> dict | None:
    """Atomically get and clear pending approval — race-condition safe."""
    global _pending_approval
    with _lock:
        step = _pending_approval
        _pending_approval = None
    return step
