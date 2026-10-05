import time


def read_screen_text() -> str:
    """Ham OCR ile ekran metni oku (eski yol — yeni kod read_screen_text_v2 kullanır)."""
    try:
        import mss
        import pytesseract
        from PIL import Image
        import tempfile, os
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            img_path = tmp.name
        Image.frombytes("RGB", shot.size, shot.rgb).save(img_path)
        text = pytesseract.image_to_string(Image.open(img_path), lang="eng").strip()
        try:
            os.remove(img_path)
        except Exception:
            pass
        return text or "Ekranda okunabilir metin bulunamadı."
    except Exception as e:
        return f"Ekran okunamadı: {e}"


def get_ui_snapshot(include_ocr: bool = True) -> dict:
    """
    Ekranın anlık UI snapshot'ını döndür.
    Dönen dict: ok, window_title, controls (list), ocr_count, timestamp
    """
    result = {
        "ok": False,
        "window_title": "",
        "controls": [],
        "ocr_count": 0,
        "timestamp": time.time(),
    }
    try:
        from vision.window_actions import get_active_window_title
        result["window_title"] = get_active_window_title() or ""
    except Exception:
        pass

    try:
        from vision.screen_actions_v2 import find_text_elements_v2
        elements = find_text_elements_v2() or []
        result["controls"] = elements
        result["ocr_count"] = len(elements)
        result["ok"] = True
    except Exception as e:
        result["error"] = str(e)

    return result


# Geriye dönük uyumluluk
read_screen = read_screen_text
