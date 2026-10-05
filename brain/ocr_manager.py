import os
from typing import Dict, Tuple

from brain.system_settings import get_system_setting, set_system_setting


COMMON_TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
]


def _normalize_path(path: str) -> str:
    path = (path or "").strip().strip('"')
    if not path:
        return ""
    return os.path.normpath(path)


def get_saved_tesseract_path() -> str:
    return _normalize_path(get_system_setting("tesseract_path", ""))


def detect_tesseract() -> str:
    saved = get_saved_tesseract_path()
    if saved and os.path.exists(saved):
        return saved

    for path in COMMON_TESSERACT_PATHS:
        norm = _normalize_path(path)
        if norm and os.path.exists(norm):
            return norm

    return ""


def save_tesseract_path(path: str) -> str:
    norm = _normalize_path(path)
    set_system_setting("tesseract_path", norm)
    return norm


def ocr_status() -> Dict[str, object]:
    detected = detect_tesseract()
    enabled = bool(get_system_setting("ocr_enabled", True))

    return {
        "enabled": enabled,
        "detected": bool(detected),
        "path": detected or ""
    }


def ensure_pytesseract_binding() -> Tuple[bool, str]:
    detected = detect_tesseract()
    if not detected:
        return False, "Tesseract bulunamadı."

    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = detected
        return True, detected
    except Exception as e:
        return False, f"pytesseract bağlanamadı: {e}"


def get_ocr_status_text() -> str:
    status = ocr_status()

    if not status["enabled"]:
        return "OCR ayarlardan kapalı."

    if status["detected"]:
        return f"OCR hazır. Tesseract yolu: {status['path']}"

    return "OCR açık ama Tesseract bulunamadı."