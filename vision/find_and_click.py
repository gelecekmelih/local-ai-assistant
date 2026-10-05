# -*- coding: utf-8 -*-
"""
vision/find_and_click.py — Ekranda metin/buton bul ve tıkla
Koordinat tabanlı: OCR ile metin bulur, fare ile tıklar.
"""

import time
import pyautogui

from brain.tool_result import ToolResult

# mss + OCR tabanlı element bulucu
from vision.screen_actions_v2 import find_text_elements_v2


# ── Metin benzerlik skoru ──────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    import unicodedata
    t = str(text or "").lower().strip()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.translate(str.maketrans("ığüşöçâîûı", "igusocaiui"))
    return " ".join(t.split())


def _score(target: str, candidate: str) -> float:
    t = _normalize(target)
    c = _normalize(candidate)

    if not t or not c:
        return 0.0

    # Tam eşleşme
    if t == c:
        return 1.0

    # Hedef, aday içinde tam kelime olarak geçiyor mu?
    if t in c:
        # Uzunluk oranına göre ağırlık — kısa target geniş cümlede bulunabilir
        ratio = len(t) / max(len(c), 1)
        return max(0.80, 0.90 * ratio)

    # Aday, hedef içinde geçiyor mu? (kısaltma eşleşmesi)
    if c in t:
        ratio = len(c) / max(len(t), 1)
        return 0.70 * ratio

    # Kelime bazlı F1 eşleşmesi
    t_words = set(t.split())
    c_words = set(c.split())
    if not t_words or not c_words:
        return 0.0

    common = t_words & c_words
    if common:
        precision = len(common) / max(len(c_words), 1)
        recall    = len(common) / max(len(t_words), 1)
        f1        = 2 * precision * recall / max(precision + recall, 1e-9)
        # Tek kelime ortak olan ama biri çok uzunsa ceza ver
        if len(common) == 1 and max(len(t_words), len(c_words)) > 3:
            f1 *= 0.6
        return f1 * 0.75

    # Karakter örtüşmesi (son çare)
    t_chars = set(t)
    c_chars = set(c)
    char_overlap = len(t_chars & c_chars) / max(len(t_chars | c_chars), 1)
    if char_overlap > 0.80:
        return 0.40

    return 0.0


def _find_best(target: str, elements: list, min_score: float = 0.40):
    """Listeden en iyi eşleşen elementi döndürür."""
    best_el    = None
    best_score = 0.0

    for el in elements:
        name = el.get("name", "")
        s = _score(target, name)
        if s > best_score:
            best_score = s
            best_el    = el

    return (best_el, best_score) if best_score >= min_score else (None, 0.0)


# ── Ana API ───────────────────────────────────────────────────────────────────

def _screen_size():
    try:
        return pyautogui.size()
    except Exception:
        return (1920, 1080)


def _validate_coords(x: int, y: int) -> bool:
    """Koordinatların ekran içinde olduğunu doğrula."""
    w, h = _screen_size()
    return 0 <= x < w and 0 <= y < h


def find_and_click(target: str, region=None, timeout: float = 8.0,
                   min_score: float = 0.45, double: bool = False) -> ToolResult:
    """
    Ekranda `target` metnini bulur ve tıklar.

    Parametreler
    ────────────
    target      : Aranacak metin / buton adı
    region      : (x, y, w, h) sınırlı alan, None=tam ekran
    timeout     : Max bekleme süresi (saniye)
    min_score   : Kabul edilebilir minimum benzerlik skoru (0.45)
    double      : True → çift tık

    Döner
    ─────
    Başarı/hata açıklaması (str)
    """
    if not target:
        return ToolResult(message="Hedef metin belirtilmedi.", ok=True)

    deadline = time.time() + timeout
    attempt  = 0
    best_seen_score = 0.0
    best_seen_name  = ""

    while time.time() < deadline:
        attempt += 1
        try:
            elements = find_text_elements_v2(region=region)
        except Exception as e:
            return ToolResult(message=f"OCR hatası: {e}", ok=False, error=str(e))

        el, score = _find_best(target, elements, min_score)

        # En iyi görülen skoru kaydet (debug için)
        if elements:
            _, top_score = _find_best(target, elements, 0.0)
            if top_score > best_seen_score:
                best_seen_score = top_score
                top_el, _ = _find_best(target, elements, 0.0)
                if top_el:
                    best_seen_name = top_el.get("name", "")

        if el:
            x = el["center_x"]
            y = el["center_y"]

            # Koordinat doğrulama
            if not _validate_coords(x, y):
                time.sleep(0.5)
                continue

            try:
                if double:
                    pyautogui.doubleClick(x, y)
                else:
                    pyautogui.click(x, y)
                return ToolResult(message=(
                    f"Tıklandı: '{el['name']}' "
                    f"(benzerlik: {score:.2f}, konum: {x},{y})"
                ), ok=True, data={"name": el["name"], "x": x, "y": y})
            except Exception as e:
                return ToolResult(message=f"Tıklama hatası: {e}", ok=False, error=str(e))

        # İlk denemeden sonra biraz daha uzun bekle (UI yüklenmiş olabilir)
        wait = 0.5 if attempt == 1 else 0.8
        time.sleep(wait)

    hint = f" En yakın: '{best_seen_name}' ({best_seen_score:.2f})" if best_seen_name else ""
    return ToolResult(
        message=f"'{target}' ekranda bulunamadı ({attempt} deneme, {timeout:.0f}s).{hint}",
        ok=False, error="bulunamadi")


def find_and_double_click(target: str, **kwargs) -> ToolResult:
    return find_and_click(target, double=True, **kwargs)


def find_text_on_screen(target: str, region=None) -> dict | None:
    """
    Koordinatlarıyla birlikte element döndürür, tıklamaz.
    {'name', 'center_x', 'center_y', 'score'} veya None
    """
    try:
        elements = find_text_elements_v2(region=region)
        el, score = _find_best(target, elements)
        if el:
            el["score"] = score
            return el
    except Exception:
        pass
    return None
