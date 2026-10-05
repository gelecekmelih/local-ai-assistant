import json
import time
from pathlib import Path
from typing import Any, Dict, List

from core.command_router import normalize_command
from vision.screen_reader import get_ui_snapshot


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LAST_SCENE_FILE = DATA_DIR / "screen_scene_last.json"

CLICKABLE_TYPES = {
    "buttoncontrol",
    "hyperlinkcontrol",
    "menuitemcontrol",
    "tabitemcontrol",
    "listitemcontrol",
    "menucontrol",
    "menuitemcontrol",
    "imagecontrol",
    "ocrtext",
}

INPUT_TYPES = {
    "editcontrol",
    "documentcontrol",
    "comboboxcontrol",
}

LOW_VALUE_LABELS = {
    "",
    "unknown",
    "pane",
    "group",
    "window",
    "custom",
    "adsiz oge",
    "adsız öğe",
}


def _norm(text: Any) -> str:
    return normalize_command(str(text or ""))


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _screen_size() -> tuple[int, int]:
    try:
        import pyautogui

        width, height = pyautogui.size()
        return int(width), int(height)
    except Exception:
        return 1920, 1080


def _label(control: Dict[str, Any]) -> str:
    return _safe_str(
        control.get("name")
        or control.get("automation_id")
        or control.get("class_name")
        or "adsiz oge"
    )


def _zone(control: Dict[str, Any], width: int, height: int) -> str:
    x = int(control.get("center_x", 0) or 0)
    y = int(control.get("center_y", 0) or 0)
    if x <= 0 or y <= 0:
        return "unknown"

    horizontal = "left" if x < width * 0.33 else "right" if x > width * 0.66 else "center"
    vertical = "top" if y < height * 0.25 else "bottom" if y > height * 0.75 else "middle"
    return f"{vertical}_{horizontal}"


def _role(control: Dict[str, Any]) -> str:
    ctype = _norm(control.get("control_type", ""))
    cls = _norm(control.get("class_name", ""))
    aid = _norm(control.get("automation_id", ""))
    name = _norm(control.get("name", ""))
    hay = f"{ctype} {cls} {aid} {name}"

    if ctype in INPUT_TYPES or "edit" in hay or "textbox" in hay or "input" in hay:
        return "input"
    if "combo" in hay:
        return "select"
    if "button" in ctype:
        return "button"
    if "tab" in ctype:
        return "tab"
    if "hyperlink" in ctype or "link" in hay:
        return "link"
    if "menu" in ctype:
        return "menu"
    if ctype == "ocrtext":
        return "text"
    if "document" in ctype:
        return "document"
    return "element"


def _is_clickable(control: Dict[str, Any], role: str) -> bool:
    ctype = _norm(control.get("control_type", ""))
    if role in {"button", "tab", "link", "menu", "select"}:
        return True
    return ctype in CLICKABLE_TYPES


def _is_text_input(control: Dict[str, Any], role: str) -> bool:
    if role in {"input", "select"}:
        return True
    ctype = _norm(control.get("control_type", ""))
    return ctype in INPUT_TYPES


def _importance(control: Dict[str, Any], role: str, clickable: bool, text_input: bool) -> int:
    label = _norm(_label(control))
    width = int(control.get("width", 0) or 0)
    height = int(control.get("height", 0) or 0)
    source = _norm(control.get("source", "uia"))

    score = 0
    if text_input:
        score += 70
    if clickable:
        score += 55
    if role == "button":
        score += 20
    if role == "tab":
        score += 15
    if role == "link":
        score += 12
    if source == "ocr":
        score += 8

    if label and label not in LOW_VALUE_LABELS:
        score += min(35, len(label) * 2)
    else:
        score -= 30

    if width >= 80 and height >= 18:
        score += 12
    if width < 8 or height < 8:
        score -= 35

    return max(0, score)


def _normalize_control(control: Dict[str, Any], width: int, height: int) -> Dict[str, Any]:
    role = _role(control)
    clickable = _is_clickable(control, role)
    text_input = _is_text_input(control, role)
    label = _label(control)

    return {
        "label": label,
        "role": role,
        "control_type": control.get("control_type", ""),
        "source": control.get("source", "uia"),
        "zone": _zone(control, width, height),
        "clickable": clickable,
        "text_input": text_input,
        "importance": _importance(control, role, clickable, text_input),
        "bounds": {
            "left": int(control.get("left", 0) or 0),
            "top": int(control.get("top", 0) or 0),
            "right": int(control.get("right", 0) or 0),
            "bottom": int(control.get("bottom", 0) or 0),
            "width": int(control.get("width", 0) or 0),
            "height": int(control.get("height", 0) or 0),
            "center_x": int(control.get("center_x", 0) or 0),
            "center_y": int(control.get("center_y", 0) or 0),
        },
        "confidence": control.get("confidence", ""),
    }


def _dedupe(items: List[Dict[str, Any]], limit: int = 180) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()

    for item in sorted(items, key=lambda x: x.get("importance", 0), reverse=True):
        label_key = _norm(item.get("label", ""))
        bounds = item.get("bounds", {})
        key = (
            label_key,
            item.get("role", ""),
            int(bounds.get("left", 0)) // 8,
            int(bounds.get("top", 0)) // 8,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break

    return out


def _state_hints(labels: List[str]) -> List[str]:
    text = " ".join(_norm(label) for label in labels)
    hints: List[str] = []

    if any(word in text for word in ["hata", "error", "failed", "basarisiz", "uyari", "warning"]):
        hints.append("Ekranda hata veya uyari sinyali var.")
    if any(word in text for word in ["giris", "login", "sign in", "password", "parola", "sifre"]):
        hints.append("Ekran giris veya hesap dogrulama akisi gibi gorunuyor.")
    if any(word in text for word in ["arama", "search", "google", "adres", "url"]):
        hints.append("Arama veya adres girisi yapilabilecek bir alan olabilir.")
    if any(word in text for word in ["kaydet", "save", "gonder", "send", "tamam", "ok"]):
        hints.append("Onay/gonderme/kaydetme gibi eylem hedefleri var.")

    return hints


def build_screen_scene(include_ocr: bool = True, limit: int = 180, _snap: dict = None) -> Dict[str, Any]:
    width, height = _screen_size()
    if _snap is not None:
        snap = _snap
    else:
        try:
            snap = get_ui_snapshot(include_ocr=include_ocr)
        except Exception as exc:
            snap = {
                "ok": False,
                "error": str(exc),
                "window_title": "",
                "controls": [],
                "ocr_count": 0,
                "timestamp": time.time(),
            }

    raw_controls = snap.get("controls", [])
    if not isinstance(raw_controls, list):
        raw_controls = []

    elements = [_normalize_control(control, width, height) for control in raw_controls]
    elements = _dedupe(elements, limit=limit)

    clickable = [e for e in elements if e.get("clickable")]
    inputs = [e for e in elements if e.get("text_input")]
    visible_text = [e for e in elements if e.get("role") in {"text", "element", "document"}]
    labels = [e.get("label", "") for e in elements]

    scene = {
        "ok": bool(snap.get("ok")),
        "error": snap.get("error", ""),
        "window_title": snap.get("window_title", ""),
        "screen": {"width": width, "height": height},
        "counts": {
            "elements": len(elements),
            "clickable": len(clickable),
            "inputs": len(inputs),
            "visible_text": len(visible_text),
            "ocr": int(snap.get("ocr_count", 0) or len([e for e in elements if e.get("source") == "ocr"])),
        },
        "state_hints": _state_hints(labels),
        "elements": elements,
        "actionable": sorted(clickable, key=lambda x: x.get("importance", 0), reverse=True)[:30],
        "inputs": sorted(inputs, key=lambda x: x.get("importance", 0), reverse=True)[:20],
        "visible_text": sorted(visible_text, key=lambda x: x.get("importance", 0), reverse=True)[:40],
        "timestamp": time.time(),
    }

    try:
        LAST_SCENE_FILE.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return scene


def _labels(items: List[Dict[str, Any]], limit: int = 10, detailed: bool = False) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        label = _safe_str(item.get("label", ""))
        key = _norm(label)
        if not key or key in LOW_VALUE_LABELS or key in seen:
            continue
        seen.add(key)
        if detailed:
            bounds = item.get("bounds", {})
            out.append(
                f"{label} [{item.get('role')}, {item.get('zone')}, "
                f"{bounds.get('center_x')},{bounds.get('center_y')}]"
            )
        else:
            out.append(label)
        if len(out) >= limit:
            break
    return out


def describe_screen_scene(mode: str = "standard", include_raw_ocr: bool = False) -> str:
    mode = _norm(mode or "standard")
    detailed = mode in {"detailed", "detayli", "full"}
    scene = build_screen_scene(include_ocr=True)

    # OCR ham metnini isteğe bağlı çek (varsayılan kapalı — build_screen_scene zaten OCR içeriyor)
    ocr_raw = ""
    if include_raw_ocr:
        try:
            from vision.screen_actions_v2 import read_screen_text_v2
            ocr_raw = read_screen_text_v2()
            if "bulunamadi" in ocr_raw.lower():
                ocr_raw = ""
        except Exception:
            pass

    if not scene.get("ok") and not scene.get("elements") and not ocr_raw:
        return f"Ekrani su an guvenilir okuyamiyorum. Hata: {scene.get('error') or '-'}"

    counts = scene.get("counts", {})
    title = _safe_str(scene.get("window_title", ""))
    lines = ["Ekran yorumu:"]
    lines.append(f"- Aktif pencere: {title or '(belirsiz)'}")
    lines.append(
        "- Algilanan: "
        f"{counts.get('elements', 0)} oge, "
        f"{counts.get('clickable', 0)} tiklanabilir, "
        f"{counts.get('inputs', 0)} yazi alani, "
        f"{counts.get('ocr', 0)} OCR metni."
    )

    hints = scene.get("state_hints") or []
    if counts.get("elements", 0) == 0 and not ocr_raw:
        lines.append("- Yorum: Ekran verisi yetersiz.")
    elif hints:
        lines.append("- Yorum: " + " ".join(hints))
    else:
        lines.append("- Yorum: Belirgin hata sinyali yok.")

    input_labels = _labels(scene.get("inputs", []), limit=8, detailed=detailed)
    action_labels = _labels(scene.get("actionable", []), limit=14, detailed=detailed)
    text_labels = _labels(scene.get("visible_text", []), limit=16, detailed=False)

    if input_labels:
        lines.append("- Yazilabilir alanlar: " + ", ".join(input_labels))
    if action_labels:
        lines.append("- Tiklanabilir hedefler: " + ", ".join(action_labels))
    if text_labels:
        lines.append("- Gorunen metinler: " + ", ".join(text_labels))

    # OCR ham metnini ekle — UIA'da bulunmayan içerikler
    if ocr_raw and ocr_raw.strip():
        # Çok uzunsa kırp
        ocr_preview = ocr_raw.strip()[:1200]
        if len(ocr_raw.strip()) > 1200:
            ocr_preview += "..."
        lines.append(f"- OCR ham metin:\n{ocr_preview}")

    if not input_labels and not action_labels and not text_labels and not ocr_raw:
        lines.append("- Not: Bu pencerede anlamli hedef yakalanamadi.")

    return "\n".join(lines)


def compact_scene_for_planner(max_chars: int = 1800) -> str:
    scene = build_screen_scene(include_ocr=True, limit=120)
    counts = scene.get("counts", {})
    lines = [
        f"window={scene.get('window_title') or '-'}",
        (
            "counts="
            f"elements:{counts.get('elements', 0)},"
            f"clickable:{counts.get('clickable', 0)},"
            f"inputs:{counts.get('inputs', 0)},"
            f"ocr:{counts.get('ocr', 0)}"
        ),
    ]

    hints = scene.get("state_hints") or []
    if hints:
        lines.append("hints=" + " ".join(hints))

    action_labels = _labels(scene.get("actionable", []), limit=14, detailed=True)
    input_labels = _labels(scene.get("inputs", []), limit=8, detailed=True)
    text_labels = _labels(scene.get("visible_text", []), limit=14, detailed=False)

    if input_labels:
        lines.append("inputs=" + " | ".join(input_labels))
    if action_labels:
        lines.append("actionable=" + " | ".join(action_labels))
    if text_labels:
        lines.append("visible_text=" + " | ".join(text_labels))

    text = "\n".join(lines)
    return text[:max_chars]


def screen_scene_json() -> str:
    return json.dumps(build_screen_scene(include_ocr=True), ensure_ascii=False, indent=2)
