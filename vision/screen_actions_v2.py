import mss
import pyautogui
import pytesseract
from PIL import Image, ImageOps, ImageFilter

from brain.tool_result import ToolResult


def _ensure_ocr_ready():
    try:
        from brain.ocr_manager import ensure_pytesseract_binding
        ok, message = ensure_pytesseract_binding()
        return ok, message
    except Exception as e:
        return False, str(e)


def _grab_with_mss(region=None):
    with mss.mss() as sct:
        monitor = sct.monitors[1]

        if region:
            left, top, width, height = region
            shot = sct.grab({
                "left": int(left),
                "top": int(top),
                "width": int(width),
                "height": int(height),
            })
        else:
            shot = sct.grab(monitor)

        img = Image.frombytes("RGB", shot.size, shot.rgb)
        return img


def _is_dark_image(img) -> bool:
    """Görüntünün ortalama parlaklığına bakarak koyu tema tespiti yapar."""
    try:
        gray = ImageOps.grayscale(img)
        pixels = list(gray.getdata())
        avg = sum(pixels) / max(1, len(pixels))
        return avg < 110  # 0-255 aralığı; 110 altı koyu tema
    except Exception:
        return False


def _prepare(img):
    """
    OCR için görüntü ön işleme — hem açık hem koyu temalar için.
    Returns: variant listesi (birden fazla işlenmiş görüntü dener, en iyi seçilir)
    """
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray, cutoff=1)  # %1 siyah/beyaz kırpma ile daha iyi kontrast

    w, h = gray.size
    # 2x upscale — daha küçük font boyutlarını yakalar
    gray = gray.resize((max(1, w * 2), max(1, h * 2)), Image.LANCZOS)

    sharp = gray.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=3))
    dark = _is_dark_image(gray)

    if dark:
        # Koyu arka plan, açık metin → invert + eşik
        inv = ImageOps.invert(sharp.convert("L"))
        inv = ImageOps.autocontrast(inv, cutoff=2)
        bw_main = inv.point(lambda x: 0 if x < 128 else 255, mode="1")
        bw_alt  = inv.point(lambda x: 0 if x < 160 else 255, mode="1")
    else:
        # Açık arka plan, koyu metin → normal eşik + Otsu benzeri iki eşik
        bw_main = sharp.point(lambda x: 0 if x < 150 else 255, mode="1")
        bw_alt  = sharp.point(lambda x: 0 if x < 180 else 255, mode="1")

    return [gray, sharp, bw_main, bw_alt]


def _ocr_best(img, psms=(3, 6, 11)):
    ok, _ = _ensure_ocr_ready()
    if not ok:
        return ""

    best = ""
    best_score = -999
    variants = _prepare(img)

    # PSM 3 (tam sayfa) yalnızca gray+sharp ile dene — daha hızlı
    # PSM 6,11 tüm varyantlarla dene
    for vi, variant in enumerate(variants):
        for psm in psms:
            # PSM 3 için sadece ilk 2 variant (gray, sharp) yeterli
            if psm == 3 and vi >= 2:
                continue
            try:
                text = pytesseract.image_to_string(
                    variant,
                    lang="tur+eng",
                    config=f"--oem 3 --psm {psm}"
                ).strip()
            except Exception:
                text = ""

            score = _score_text(text)
            if score > best_score:
                best_score = score
                best = text

    return best.strip()


def _score_text(text):
    if not text:
        return -1

    letters = sum(1 for c in text if c.isalpha())
    digits = sum(1 for c in text if c.isdigit())
    spaces = text.count(" ")
    lines = len([x for x in text.splitlines() if x.strip()])
    bad = text.count("�") + text.count("|") + text.count("~")
    return (letters * 3) + digits + spaces + (lines * 4) - (bad * 6)


def _clean_ocr_text(text):
    text = str(text or "").strip()
    text = (
        text.replace("ý", "ı").replace("Ý", "İ")
        .replace("þ", "ş").replace("Þ", "Ş")
        .replace("ð", "ğ").replace("Ð", "Ğ")
    )
    text = " ".join(text.split())
    return text


def _ocr_data_image(img, scale=2):
    ok, message = _ensure_ocr_ready()
    if not ok:
        raise RuntimeError(message)

    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray)
    if scale and scale != 1:
        w, h = gray.size
        gray = gray.resize((max(1, w * scale), max(1, h * scale)))

    return pytesseract.image_to_data(
        gray,
        lang="tur+eng",
        config="--oem 3 --psm 11",
        output_type=pytesseract.Output.DICT,
    )


def find_text_elements_v2(region=None, min_conf=35, limit=220):
    """Return OCR text boxes with screen coordinates.

    UI Automation misses text in many custom-rendered apps. These OCR boxes are
    intentionally shaped like lightweight controls so the mouse layer can rank
    and click them with the same machinery it uses for real UI controls.
    """
    if region:
        offset_x, offset_y, _, _ = region
        img = _grab_with_mss(region)
    else:
        offset_x, offset_y = 0, 0
        img = _grab_with_mss()

    scale = 2
    try:
        data = _ocr_data_image(img, scale=scale)
    except Exception:
        return []

    grouped = {}
    words = []
    n = len(data.get("text", []))

    for i in range(n):
        txt = _clean_ocr_text(data["text"][i])
        if not txt:
            continue

        try:
            conf = float(data.get("conf", ["-1"])[i])
        except Exception:
            conf = -1

        if conf < min_conf:
            continue

        left = int(data["left"][i] / scale) + int(offset_x)
        top = int(data["top"][i] / scale) + int(offset_y)
        width = int(data["width"][i] / scale)
        height = int(data["height"][i] / scale)
        if width < 3 or height < 3:
            continue

        item = {
            "name": txt,
            "class_name": "OCR",
            "automation_id": "ocr_word",
            "control_type": "OCRText",
            "visible": True,
            "depth": 0,
            "left": left,
            "top": top,
            "right": left + width,
            "bottom": top + height,
            "width": width,
            "height": height,
            "center_x": left + width // 2,
            "center_y": top + height // 2,
            "source": "ocr",
            "confidence": conf,
        }
        words.append(item)

        key = (
            data.get("block_num", [0])[i],
            data.get("par_num", [0])[i],
            data.get("line_num", [0])[i],
        )
        grouped.setdefault(key, []).append(item)

    lines = []
    for parts in grouped.values():
        parts.sort(key=lambda x: x["left"])
        text = _clean_ocr_text(" ".join(p["name"] for p in parts))
        if not text:
            continue

        left = min(p["left"] for p in parts)
        top = min(p["top"] for p in parts)
        right = max(p["right"] for p in parts)
        bottom = max(p["bottom"] for p in parts)
        avg_conf = sum(float(p.get("confidence", 0)) for p in parts) / max(1, len(parts))

        lines.append({
            "name": text,
            "class_name": "OCR",
            "automation_id": "ocr_line",
            "control_type": "OCRText",
            "visible": True,
            "depth": 0,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": max(0, right - left),
            "height": max(0, bottom - top),
            "center_x": left + max(0, right - left) // 2,
            "center_y": top + max(0, bottom - top) // 2,
            "source": "ocr",
            "confidence": round(avg_conf, 2),
        })

    out = lines + words
    out.sort(key=lambda x: (x.get("top", 0), x.get("left", 0)))
    return out[:limit]


def _screen_regions():
    width, height = pyautogui.size()

    return {
        "full": (0, 0, width, height),
        "top_bar": (int(width * 0.08), int(height * 0.02), int(width * 0.84), int(height * 0.12)),
        "main_center": (int(width * 0.10), int(height * 0.12), int(width * 0.72), int(height * 0.70)),
        "right_panel": (int(width * 0.72), int(height * 0.10), int(width * 0.24), int(height * 0.80)),
        "bottom_bar": (int(width * 0.10), int(height * 0.78), int(width * 0.80), int(height * 0.18)),
    }


def read_screen_text_v2() -> ToolResult:
    """
    Ekran OCR'ı — önce tam ekranı tek pass ile okur (en hızlı ve bütünlüklü),
    ardından farklı bölgeleri ayrı ayrı okur ve birleştirir.
    """
    # 1) Tam ekran tek geçiş (PSM 3 = tam otomatik sayfa analizi)
    full_img = _grab_with_mss()
    full_text = _ocr_best(full_img, psms=(3,))

    # 2) Bölgesel OCR — tam ekranda kaçırılan detaylar için
    regions = _screen_regions()
    region_results = {}
    ordered = ["top_bar", "main_center", "right_panel", "bottom_bar"]
    labels = {
        "top_bar": "Ust Alan",
        "main_center": "Ana Icerik",
        "right_panel": "Sag Panel",
        "bottom_bar": "Alt Alan",
    }

    for name in ordered:
        x, y, w, h = regions[name]
        img = _grab_with_mss((x, y, w, h))
        txt = _ocr_best(img, psms=(6, 11))
        if txt and txt.strip():
            region_results[name] = txt

    # 3) Birleştir — tam ekran varsa önce, ardından bölgesel ekler
    parts = []

    if full_text and full_text.strip():
        parts.append(f"Tam Ekran:\n{full_text.strip()}")

    # Bölgesel metinden tam ekranda olmayan ek içerik ekle
    region_extra = []
    for key in ordered:
        if key not in region_results:
            continue
        rtxt = region_results[key].strip()
        # Tam ekran OCR'ında zaten varsa tekrar ekleme
        if full_text and len(rtxt) < 40:
            # Kısa metinleri zaten tam ekran yakalar, atlayabiliriz
            continue
        if not full_text or _score_text(rtxt) > 0:
            region_extra.append(f"{labels[key]}:\n{rtxt}")

    if region_extra and (not full_text or not full_text.strip()):
        parts.extend(region_extra)

    if not parts:
        return ToolResult(message="Vision v2: okunabilir metin bulunamadı.",
                          ok=False, error="metin_yok")

    return ToolResult(message="\n\n".join(parts), ok=True, data=parts)


def find_buttons_v2() -> ToolResult:
    regions = _screen_regions()
    target_keys = ["top_bar", "right_panel", "bottom_bar"]

    found = []
    button_words = {
        "tamam", "iptal", "kaydet", "gonder", "gönder", "sil",
        "close", "ok", "yes", "no", "next", "back", "apply",
        "search", "ara", "devam", "baslat", "başlat", "ac", "aç",
        "install", "download", "login", "sign", "ayarlar",
        "brain", "test", "panel", "durdur"
    }

    for key in target_keys:
        x, y, w, h = regions[key]
        img = _grab_with_mss((x, y, w, h))

        for variant in _prepare(img):
            try:
                data = pytesseract.image_to_data(
                    variant,
                    lang="tur+eng",
                    config="--oem 3 --psm 11",
                    output_type=pytesseract.Output.DICT
                )
            except Exception:
                continue

            n = len(data.get("text", []))
            for i in range(n):
                txt = (data["text"][i] or "").strip()
                conf = data.get("conf", ["-1"])[i]

                try:
                    conf_val = float(conf)
                except Exception:
                    conf_val = -1

                if not txt or conf_val < 35:
                    continue

                low = txt.lower()

                if low in button_words or (len(txt) <= 18 and len(txt.split()) <= 3):
                    found.append(txt)

    uniq = []
    seen = set()
    for item in found:
        k = item.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(item)

    if not uniq:
        return ToolResult(message="Vision v2: buton bulunamadı.", ok=False, error="buton_yok")

    return ToolResult(message="Vision v2 buton adaylari:\n- " + "\n- ".join(uniq[:20]),
                      ok=True, data=uniq)


def click_center() -> ToolResult:
    width, height = pyautogui.size()
    x = width // 2
    y = height // 2
    pyautogui.click(x, y)
    return ToolResult(message=f"Merkeze tıklandı: ({x}, {y})", ok=True, data=(x, y))
