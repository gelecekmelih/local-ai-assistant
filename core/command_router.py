import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class SystemRoute:
    kind: str
    mode: str = "standard"


def normalize_command(text: str) -> str:
    """Türkçe metni normalize et — küçük harf, karakter dönüşümü, NFKD."""
    value = str(text or "").lower().strip()
    value = value.translate(str.maketrans(
        "ıığüşöçâîû",
        "iigusocaiu",
    ))
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s:+/.-]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


SYSTEM_CONTROL_REPORT_MODES = {
    "last": {
        "son sistem kontrol raporunu goster",
        "son sistem kontrol raporu",
        "onceki sistem kontrol raporu",
    },
    "short": {
        "kisa sistem kontrol raporu",
        "sistem ayarlari ozeti",
        "bilgisayar ayarlari ozeti",
    },
    "detailed": {
        "detayli sistem kontrol raporu",
        "detayli sistem ayarlari raporu",
        "bilgisayar ayarlarini detayli kontrol et",
    },
    "report": {
        "sistem ayarlari durumu",
        "bilgisayar ayarlari durumu",
        "sistem kontrol raporu",
        "bilgisayar kontrol raporu",
        "sistem ayarlarini kontrol et",
        "bilgisayar ayarlarini kontrol et",
        "windows ayarlari durumu",
    },
}

SYSTEM_SETTINGS_OPEN_REQUESTS = {
    "main": {
        "sistem ayarlarini ac",
        "windows ayarlarini ac",
        "ayarlar uygulamasini ac",
        "bilgisayar ayarlarini ac",
    },
    "sound": {
        "ses ayarlarini ac",
        "windows ses ayarlarini ac",
        "ses ayarini ac",
    },
    "microphone": {
        "mikrofon ayarlarini ac",
        "mikrofon gizlilik ayarlarini ac",
        "mikrofon izinlerini ac",
    },
    "bluetooth": {
        "bluetooth ayarlarini ac",
        "bluetooth ac",
    },
    "display": {
        "ekran ayarlarini ac",
        "goruntu ayarlarini ac",
        "monitor ayarlarini ac",
    },
    "network": {
        "ag ayarlarini ac",
        "internet ayarlarini ac",
        "wifi ayarlarini ac",
    },
    "startup_apps": {
        "baslangic uygulamalari ayarini ac",
        "baslangic uygulamalarini ac",
        "startup ayarlarini ac",
    },
    "power": {
        "guc ayarlarini ac",
        "uyku ayarlarini ac",
        "pil ayarlarini ac",
    },
    "default_apps": {
        "varsayilan uygulama ayarlarini ac",
        "varsayilan uygulamalari ac",
    },
    "installed_apps": {
        "yuklu uygulamalari ac",
        "uygulama kaldirma ayarlarini ac",
        "program kaldirma ayarlarini ac",
    },
    "windows_update": {
        "windows update ac",
        "guncelleme ayarlarini ac",
        "windows guncelleme ayarlarini ac",
    },
    "privacy": {
        "gizlilik ayarlarini ac",
        "izin ayarlarini ac",
    },
}

SYSTEM_CHANGE_REQUESTS = {
    "startup_enable": {
        "klargusu baslangica ekle",
        "klargusu windows baslangicina ekle",
        "klargus bilgisayar acilinca baslasin",
        "klargus baslangicta acilsin",
    },
    "startup_disable": {
        "klargusu baslangictan cikar",
        "klargusu windows baslangicindan kaldir",
        "klargus bilgisayar acilinca baslamasin",
        "klargus baslangicta acilmasin",
    },
    "mic_auto_select": {
        "mikrofonu otomatik sec",
        "klargus mikrofonu otomatik sec",
        "en iyi mikrofonu sec",
    },
    "mic_default": {
        "mikrofon secimini varsayilana al",
        "klargus mikrofonunu varsayilana al",
        "mikrofon aygitini varsayilan yap",
    },
}

SYSTEM_APPLY_REQUESTS = {
    "startup_enable": {
        "onay baslangica ekle",
        "baslangica eklemeyi onayliyorum",
    },
    "startup_disable": {
        "onay baslangictan cikar",
        "baslangictan cikarmayi onayliyorum",
    },
    "mic_auto_select": {
        "onay mikrofonu otomatik sec",
        "mikrofonu otomatik secmeyi onayliyorum",
    },
    "mic_default": {
        "onay mikrofon varsayilan",
        "mikrofonu varsayilana almayi onayliyorum",
    },
}


def system_control_mode(text: str) -> str:
    norm = normalize_command(text)

    for mode, values in SYSTEM_CONTROL_REPORT_MODES.items():
        if norm in values:
            return mode

    for target, values in SYSTEM_SETTINGS_OPEN_REQUESTS.items():
        if norm in values:
            return f"open:{target}"

    for action, values in SYSTEM_CHANGE_REQUESTS.items():
        if norm in values:
            return f"request:{action}"

    for action, values in SYSTEM_APPLY_REQUESTS.items():
        if norm in values:
            return f"apply:{action}"

    if "ayar" in norm and any(word in norm for word in {"ac", "goster"}):
        if any(word in norm for word in {"ses", "sound"}):
            return "open:sound"
        if "mikrofon" in norm:
            return "open:microphone"
        if "bluetooth" in norm:
            return "open:bluetooth"
        if any(word in norm for word in {"ekran", "goruntu", "monitor"}):
            return "open:display"
        if any(word in norm for word in {"ag", "internet", "wifi"}):
            return "open:network"
        if any(word in norm for word in {"guc", "uyku", "pil"}):
            return "open:power"
        if "guncelleme" in norm or "update" in norm:
            return "open:windows_update"
        if "varsayilan" in norm:
            return "open:default_apps"
        if "gizlilik" in norm or "izin" in norm:
            return "open:privacy"
        if "baslangic" in norm or "startup" in norm:
            return "open:startup_apps"
        return "open:main"

    return ""


def route_system_command(text: str) -> SystemRoute | None:
    norm = normalize_command(text)
    if not norm:
        return None
    system_mode = system_control_mode(norm)
    if system_mode:
        return SystemRoute("system_control", system_mode)
    return None
