import json
import re
import time
import unicodedata
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STT_CORRECTIONS_FILE = DATA_DIR / "stt_corrections.json"

# STT corrections in-process cache — her transcription'da disk okumaz
_stt_corrections_cache: dict = {}
_stt_corrections_mtime: float = 0.0


def _fold_text(text: str) -> str:
    """Metni küçük harf + NFKD normalize edilmiş karşılaştırma formuna çevirir."""
    value = str(text or "").lower().strip()
    # NFKD ile birleşik Türkçe karakterleri ASCII'ye indir
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    # Kalan özel karakterleri temizle
    value = re.sub(r"[^\w\s:+/.-]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _load_json(path: Path, default):
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, type(default)):
                return data
    except Exception:
        pass
    return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_stt_corrections() -> dict:
    global _stt_corrections_cache, _stt_corrections_mtime
    try:
        mtime = STT_CORRECTIONS_FILE.stat().st_mtime if STT_CORRECTIONS_FILE.exists() else 0.0
    except Exception:
        mtime = 0.0
    if _stt_corrections_cache and mtime == _stt_corrections_mtime:
        return _stt_corrections_cache  # disk değişmediyse cache döndür

    data = _load_json(STT_CORRECTIONS_FILE, {})
    clean = {}
    for key, value in data.items():
        if isinstance(value, dict):
            wrong = str(value.get("wrong", key) or "").strip()
            correct = str(value.get("correct", "") or "").strip()
            updated_at = str(value.get("updated_at", "") or "")
        else:
            wrong = str(key or "").strip()
            correct = str(value or "").strip()
            updated_at = ""
        norm_key = _fold_text(wrong)
        if norm_key and correct:
            clean[norm_key] = {
                "wrong": wrong,
                "correct": correct,
                "updated_at": updated_at,
            }
    _stt_corrections_cache = clean
    _stt_corrections_mtime = mtime
    return clean


def save_stt_correction(wrong: str, correct: str) -> str:
    wrong = " ".join(str(wrong or "").strip().split())
    correct = " ".join(str(correct or "").strip().split())
    key = _fold_text(wrong)
    if not key or not correct:
        return "Düzeltme için yanlış ve doğru metni birlikte söylemelisiniz."

    data = load_stt_corrections()
    data[key] = {
        "wrong": wrong,
        "correct": correct,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_json(STT_CORRECTIONS_FILE, data)
    return f"Duyma düzeltmesi kaydedildi: {wrong} -> {correct}"


def delete_stt_correction(wrong: str) -> str:
    key = _fold_text(wrong)
    data = load_stt_corrections()
    if key in data:
        removed = data.pop(key)
        _save_json(STT_CORRECTIONS_FILE, data)
        return f"Duyma düzeltmesi silindi: {removed.get('wrong', wrong)}"
    return "Bu duyma düzeltmesini bulamadım."


def list_stt_corrections() -> str:
    data = load_stt_corrections()
    if not data:
        return "Henüz kayıtlı duyma düzeltmesi yok."
    lines = ["Kayıtlı duyma düzeltmeleri:"]
    for item in sorted(data.values(), key=lambda x: str(x.get("wrong", ""))):
        lines.append(f"- {item.get('wrong')} -> {item.get('correct')}")
    return "\n".join(lines)


def apply_stt_corrections(text: str) -> str:
    t = " ".join(str(text or "").strip().split())
    if not t:
        return ""

    data = load_stt_corrections()
    if not data:
        return t

    out = t
    for key, item in sorted(data.items(), key=lambda kv: len(kv[0]), reverse=True):
        wrong = str(item.get("wrong", "") or "").strip()
        correct = str(item.get("correct", "") or "").strip()
        if not key or not correct:
            continue

        if wrong:
            direct = re.compile(rf"(?<!\w){re.escape(wrong)}(?!\w)", flags=re.IGNORECASE)
            if direct.search(out):
                out = direct.sub(correct, out)
                continue

        folded = _fold_text(out)
        if key in folded:
            folded = re.sub(rf"(?<!\w){re.escape(key)}(?!\w)", correct.lower(), folded)
            out = folded

    return " ".join(out.split())


# Whisper bazen wake word/program adlarını bozuk tanır — modül seviyesinde
# tanımlı: normalize_whisper_text wake döngüsünde ~0.6s'de bir çağrılıyor,
# dict'in her çağrıda yeniden kurulması gereksizdi.
_WHISPER_REPLACEMENTS = {
    "kıl argus": "klargus",
    "kılar gus": "klargus",
    "kılar göz": "klargus",
    "klar gus": "klargus",
    "klar göz": "klargus",
    "kargus": "klargus",
    "klargos": "klargus",
    "klarus": "klargus",
    "spotifay": "spotify",
    "spotifai": "spotify",
    "spotifi": "spotify",
    "diskort": "discord",
    "diskord": "discord",
    "vatsap": "whatsapp",
    "vat sap": "whatsapp",
    "you tube": "youtube",
    "yutub": "youtube",
    "gugıl": "google",
    "krom": "chrome",
    "korom": "chrome",
}


def normalize_whisper_text(text: str) -> str:
    """Whisper (faster-whisper) çıktısı için hafif normalizasyon.

    Whisper Türkçe modeli diacritic'li metin üretir (ü, ş, ç, ı, ğ, ö).
    Bu karakterleri SİLMEZ — LLM'e tam Türkçe metin gönderilir.
    Sadece: küçük harf, wake word temizleme, yaygın yanlış tanıma düzeltmeleri.
    """
    t = str(text or "").lower().strip()

    for wrong, correct in _WHISPER_REPLACEMENTS.items():
        t = t.replace(wrong, correct)

    t = re.sub(r"\blargus\b", "klargus", t)
    t = " ".join(t.split())

    # Kullanıcı tarafından öğretilen STT düzeltmelerini uygula
    t = apply_stt_corrections(t)

    # "klargus" çağrı kelimesini soy — "klargus spotify aç" → "spotify aç"
    t = re.sub(r"^(?:klargus|jarvis|hey\s+klargus|ey\s+klargus|hey\s+jarvis)\s*[,:]?\s*", "", t).strip()
    return t


def normalize_command_text(text: str) -> str:
    t = str(text or "").lower().strip()
    # NFKD normalizasyonu — Türkçe özel karakterleri ASCII'ye indir
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))

    replacements = {
        "kıl argus": "klargus",
        "kil argus": "klargus",
        "kılar gus": "klargus",
        "kilar gus": "klargus",
        "killer gus": "klargus",
        "kiler gus": "klargus",
        "klar gus": "klargus",
        "k largus": "klargus",
        "ki largus": "klargus",
        "kılar göz": "klargus",
        "kilar göz": "klargus",
        "kilar goz": "klargus",
        "klar göz": "klargus",
        "klar goz": "klargus",
        "kargus": "klargus",
        "klargos": "klargus",
        "klarus": "klargus",
        "spotifay": "spotify",
        "spotifai": "spotify",
        "spotifi": "spotify",
        "diskort": "discord",
        "diskord": "discord",
        "vatsap": "whatsapp",
        "vat sap": "whatsapp",
        "you tube": "youtube",
        "yutub": "youtube",
        "gugıl": "google",
        "gugil": "google",
        "krom": "chrome",
        "korom": "chrome",
        "çakır": "cakir",
    }

    for wrong, correct in replacements.items():
        t = t.replace(wrong, correct)

    t = re.sub(r"\blargus\b", "klargus", t)
    t = apply_stt_corrections(t)
    t = " ".join(t.split())

    # "klargus" çağrı kelimesini soy — "klargus spotify ac" → "spotify ac"
    t = re.sub(r"^(?:klargus|jarvis|hey\s+klargus|ey\s+klargus|hey\s+jarvis)\s*[,:]?\s*", "", t).strip()
    return t


def detect_tts_mode(text: str) -> str:
    if not text:
        return "conversation"

    t = text.lower().strip()

    command_markers = [
        "açıldı",
        "kapatıldı",
        "tıklandı",
        "yazıldı",
        "hazırım",
        "tamam",
        "not edildi",
        "anlaşıldı",
        "tuş basıldı",
        "bağlantı açıldı",
    ]

    analytic_markers = [
        "sorun",
        "problem",
        "neden",
        "çünkü",
        "analiz",
        "muhtemelen",
        "sebep",
        "çözüm",
        "adım",
        "kontrol",
        "hata",
        "karşılaştır",
    ]

    cinematic_markers = [
        "operasyon brifingi",
        "klargus çekirdeği aktif",
        "sistem duruşu",
        "jarvis deneyimi",
        "aktif çalışma alanı",
        "hazırım. hedefi söyleyin",
    ]

    if any(x in t for x in cinematic_markers):
        return "cinematic"

    if any(x in t for x in command_markers):
        return "command"

    if any(x in t for x in analytic_markers):
        return "analytic"

    return "conversation"


def _soften_symbols(text: str) -> str:
    t = text
    t = t.replace("```", " ")
    t = t.replace("**", " ")
    t = t.replace("__", " ")
    t = t.replace("##", " ")
    t = t.replace("*", " ")
    t = t.replace("•", ", ")
    t = t.replace("->", ", sonra ")
    t = t.replace("=>", ", sonuç olarak ")
    t = t.replace("=", " eşittir ")
    # Yüzde işareti: "%50" → "yüzde 50" (Piper Türkçesi % işaretini okuyamıyor)
    t = re.sub(r"%\s*(\d)", r"yüzde \1", t)
    t = t.replace("%", " yüzde ")
    t = t.replace("&", " ve ")
    t = t.replace("+", " artı ")
    t = t.replace("@", " et ")
    t = t.replace("/", " ")
    t = t.replace("\\", " ")
    t = t.replace("_", " ")
    return t


def _speakable_replacements(text: str) -> str:
    t = text
    replacements = {
        "LLM": "el el em",
        "UI": "yu ay",
        "TTS": "ti ti es",
        "STT": "es ti ti",
        "OCR": "o si ar",
        "GPU": "ci pi yu",
        "CPU": "si pi yu",
        "CMD": "si em di",
        "JSON": "cey son",
        "ONNX": "oniks",
        "VS Code": "vi es kod",
        "WhatsApp": "vatsap",
        "Whatsapp": "vatsap",
        "Spotify": "ispotifay",
        "Steam": "stim",
        "Brave": "breyv",
        "Chrome": "krom",
        "Discord": "diskord",
        "YouTube": "yutub",
        "Youtube": "yutub",
        "Google": "gugıl",
        "Telegram": "telegram",
        "Twitch": "tviç",
        "Netflix": "netfliks",
        "Firefox": "fayrfoks",
        "Edge": "ec",
        "GitHub": "githab",
        "Windows": "vindovs",
    }
    for src, dst in replacements.items():
        t = t.replace(src, dst)
    return t


def normalize_tts_text(text: str, mode: str | None = None, light: bool = False) -> str:
    """TTS öncesi metin normalizasyonu.

    light=True (neural sesler için): sadece sembol/markdown temizliği yapılır.
    Marka adı fonetik düzeltmeleri (Spotify→ispotifay) UYGULANMAZ — neural
    sesler İngilizce kelimeleri zaten doğru telaffuz eder, fonetik mangling
    onları bozar. light=False (Piper için): tam normalizasyon.
    """
    if not text:
        return ""

    if mode is None:
        mode = detect_tts_mode(text)

    t = str(text).strip()
    t = _soften_symbols(t)
    if not light:
        t = _speakable_replacements(t)

    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\.{2,}", ".", t)
    t = re.sub(r"\?{2,}", "?", t)
    t = re.sub(r"!{2,}", "!", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)

    if mode == "command":
        t = re.sub(r"\bşu an\b", "", t, flags=re.IGNORECASE)

    elif mode == "analytic":
        t = t.replace(" çünkü ", ". Çünkü ")
        t = t.replace(" ancak ", ". Ancak ")
        t = t.replace(" bu yüzden ", ". Bu yüzden ")
        t = t.replace(" ardından ", ". Ardından ")
        t = t.replace(" sonra ", ". Sonra ")

    elif mode == "conversation":
        t = t.replace(" efendim,", " efendim.")
        t = t.replace(" efendim.", " efendim.")

    elif mode == "cinematic":
        t = t.replace("Klargus operasyon brifingi:", "Operasyon brifingi hazır.")
        t = t.replace("Sistem:", "Sistem durumu.")
        t = t.replace("Öğrenme:", "Öğrenme durumu.")
        t = t.replace("Sizi tanıma:", "Kullanıcı profili.")
        t = t.replace("Açık pencereler:", "Açık pencereler.")
        t = t.replace("Aktif çalışma alanı:", "Aktif çalışma alanı.")

    t = re.sub(r"\b(\d{4,})\b", r" \1 ", t)
    t = re.sub(r"\s+", " ", t).strip()

    if t and t[-1] not in ".!?":
        t += "."

    return t


def split_tts_sentences(text: str, max_len: int = 180) -> list[str]:
    if not text:
        return []

    parts = re.split(r"(?<=[.!?])\s+", text)
    parts = [p.strip() for p in parts if p.strip()]

    chunks = []
    current = ""

    for part in parts:
        if not current:
            current = part
            continue

        if len(current) + 1 + len(part) <= max_len:
            current += " " + part
        else:
            chunks.append(current.strip())
            current = part

    if current.strip():
        chunks.append(current.strip())

    return chunks
