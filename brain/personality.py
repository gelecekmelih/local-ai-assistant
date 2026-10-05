# -*- coding: utf-8 -*-
import json
import logging
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

PREFERENCES_FILE = DATA_DIR / "klargus_preferences.json"

DEFAULT_PREFERENCES = {
    "assistant_name": "Klargus",
    "style": "jarvis",
    "response_length": "short",
    "tone": "controlled_professional",
    "language": "tr",
    "apply_learned_preferences": True,
    "ack_style": "concise",
    "address_user_as": "efendim",
    "use_formal_you": True,
    "warmth": "balanced",
    "analytic_mode": True,
    "emotion_level": "low",
    "seriousness_adaptation": True,
}


def _load_json_safe(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logging.warning("personality load failed: %s", e)
    return default


def _save_json_safe(path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        logging.warning("personality save failed: %s", e)
        return False


def get_preferences():
    data = _load_json_safe(PREFERENCES_FILE, {})
    if not isinstance(data, dict):
        data = {}
    merged = dict(DEFAULT_PREFERENCES)
    merged.update(data)
    return merged


def save_preferences(data):
    merged = dict(DEFAULT_PREFERENCES)
    if isinstance(data, dict):
        merged.update(data)
    _save_json_safe(PREFERENCES_FILE, merged)
    return merged


def set_preference(key, value):
    prefs = get_preferences()
    prefs[key] = value
    save_preferences(prefs)
    return prefs


def _normalize(text):
    if not text:
        return ""
    text = str(text).lower().strip()
    for src, dst in [("ı","i"),("ğ","g"),("ü","u"),("ş","s"),("ö","o"),("ç","c")]:
        text = text.replace(src, dst)
    return " ".join(text.split())


def _extract_address_name(text):
    """"bana X de / diye hitap et / diye seslen" → hitap adı X (TEK kelime).
    Tek kelime şartı yanlış-pozitifi keser: "bana kahve getir de" isim yakalamaz.
    'siz'/'sen' hitap adı DEĞİL (resmiyet tercihi — çağıran önce onu ele alır).

    EŞLEŞTİRME normalize'lı metinde (ASCII-fold — "şef"/"sef" ikisi de tanınır), ama
    DÖNDÜRÜLEN isim ORİJİNAL metinden alınır: hitap kullanıcıya gösterilip seslendirilir,
    Türkçe karakter + büyük harf korunmalı ("Şef" → "sef" DEĞİL "Şef")."""
    t = _normalize(text)
    # Aynı desenler hem normalize'lı t'de (kaç kelime atlanacağını bulmak için) hem de
    # orijinal metinde çalışır; ismi orijinalden alırız.
    raw_words = text.split()
    patterns = [
        r"bana (\w+) diye (?:hitap et|seslen|cagir)",
        r"bana (\w+) de\b",
    ]
    for p in patterns:
        m = re.search(p, t)
        if m:
            # Normalize'lı t'de eşleşen ismin kelime indeksini bul; aynı indeksteki
            # ORİJİNAL kelimeyi al (fold yok, büyük harf korunur).
            prefix_words = t[:m.start(1)].split()
            idx = len(prefix_words)
            raw_name = raw_words[idx] if idx < len(raw_words) else m.group(1)
            # Yalnız harf/rakam bırak — ama Türkçe harfleri ve büyük harfi KORU.
            name = re.sub(r"[^a-zA-Z0-9çğıöşüÇĞİÖŞÜ]", "", raw_name).strip()
            if name and _normalize(name) not in ("siz", "sen"):
                return name
    return ""


# Nitelik ile tercih-fiili arası en çok 2 dolgu kelime (yakınlık şartı). Fiil ŞART:
# fiilsiz cümle ("soğuk hava") ya da eylem fiili (aç/yap/hazırla) EŞLEŞMEZ.
_PREF_GAP = r"(?:\s+\w+){0,2}\s+"
_SPEAK    = r"(?:konus|cevap ver|cevaplar ver)"   # uzunluk/üslup fiili
_MANNER   = r"(?:konus|davran|ol)"                # üslup fiili

# (regex [normalize edilmiş, ASCII], sonuç). Sıra: ilk eşleşen kazanır.
_PREF_RULES = [
    (r"\b(?:siz|sen)\b\s+diye\s+(?:hitap et|seslen)", None),  # resmiyet — özel ele alınır
    (r"\bkisa\b"      + _PREF_GAP + _SPEAK  + r"\b", ("response_length", "short")),
    (r"\bdetayli\b"   + _PREF_GAP + _SPEAK  + r"\b", ("response_length", "medium")),
    (r"\bsoguk\b"     + _PREF_GAP + _MANNER + r"\b", ("warmth", "cool")),
    (r"\bsicak\b"     + _PREF_GAP + _MANNER + r"\b", ("warmth", "warm")),
    (r"\bdengeli\b"   + _PREF_GAP + _MANNER + r"\b", ("warmth", "balanced")),
    (r"\banalitik\b"  + _PREF_GAP + r"(?:ol|davran)\b", ("analytic_mode", True)),
    (r"\bsezgisel\b"  + _PREF_GAP + r"(?:ol|davran)\b", ("analytic_mode", False)),
    (r"\bturkce\b"    + _PREF_GAP + r"konus\b", ("language", "tr")),
    (r"\bingilizce\b" + _PREF_GAP + r"konus\b", ("language", "en")),
    (r"\bbrave\b"     + _PREF_GAP + r"kullan\b", ("preferred_browser", "brave")),
    (r"\bchrome\b"    + _PREF_GAP + r"kullan\b", ("preferred_browser", "chrome")),
]


def extract_preference_update(text):
    """Doğal tercih ifadelerini yakala. "bundan sonra" öneki OPSİYONEL; nitelik + tercih-
    fiili yakınlığı ŞART. Belirsiz/fiilsiz cümlelerde None döner (dar tarafta kal —
    yanlış tercih değişikliği kaçırmaktan kötüdür). Döner: (key, value) | None."""
    t = _normalize(text)

    # 1) Resmiyet ("siz/sen diye hitap et") — hitap-adı çıkarımından ÖNCE (isim sanılmasın)
    if re.search(_PREF_RULES[0][0], t):
        m = re.search(r"\b(siz|sen)\b", t)
        return ("use_formal_you", m.group(1) == "siz")

    # 2) Hitap adı ("bana komutan de")
    address_name = _extract_address_name(text)
    if address_name:
        return ("address_user_as", address_name)

    # 3) Üslup / uzunluk / dil / tarayıcı — nitelik+fiil yakınlık kuralı
    for pat, result in _PREF_RULES[1:]:
        if re.search(pat, t):
            return result

    return None


# ── Kişilik özeti — STRING döndürür (eski dict değil) ────────────────────────

def generate_personality_summary() -> str:
    """Kullanıcı kişilik özetini metin olarak döndürür."""
    try:
        prefs = get_preferences()
        lines = [
            f"Ton: {prefs.get('tone', 'controlled_professional')}",
            f"Cevap uzunluğu: {prefs.get('response_length', 'short')}",
            f"Sıcaklık: {prefs.get('warmth', 'balanced')}",
            f"Analitik mod: {'açık' if prefs.get('analytic_mode', True) else 'kapalı'}",
            f"Dil: {prefs.get('language', 'tr')}",
            f"Hitap: {prefs.get('address_user_as', 'efendim')}",
        ]
        return "\n".join(lines)
    except Exception:
        return "Kişilik özeti alınamadı."


def get_personality_summary() -> str:
    """generate_personality_summary ile aynı — geriye dönük uyumluluk."""
    return generate_personality_summary()
