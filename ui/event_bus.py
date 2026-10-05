import queue


event_queue = queue.Queue()


def push_event(kind, value):
    try:
        event_queue.put((kind, value))
    except Exception:
        pass


def pop_event_nowait():
    try:
        return event_queue.get_nowait()
    except Exception:
        return None
