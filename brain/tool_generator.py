# -*- coding: utf-8 -*-
"""
brain/tool_generator.py — Dinamik Araç Üretici
===============================================
LLM ihtiyaç duyduğu aracı kendisi yazar, validate eder ve sisteme ekler.

Akış:
  1. Kullanıcı/LLM yeni araç ihtiyacını tanımlar
  2. LLM Python fonksiyon kodu üretir
  3. Syntax + güvenlik kontrolü yapılır
  4. tools/generated_tools.py'a eklenir
  5. Dinamik olarak tool_registry'e kaydedilir
  6. KLARGUS_TOOLS'a eklenir (bu oturumda geçerli)

Kullanım:
  generate_and_register("Sistemi kapat ve 30 saniye sonra aç komutunu çalıştır")
  list_generated_tools()
  delete_generated_tool("tool_adı")
"""

import ast
import re
import time
from pathlib import Path

from core.logger import get_logger

log = get_logger("tool_generator")

PROJECT_ROOT  = Path(__file__).resolve().parents[1]
GENERATED_FILE = PROJECT_ROOT / "tools" / "generated_tools.py"

# Yasaklı kalıplar — güvenlik filtresi
# (2026-09-29) SIKILAŞTIRILDI: önceki liste "subprocess.run('...')" gibi yalnız
# string-literal komutları yakalıyordu — subprocess.run(["cmd", arg]) (liste
# argümanlı) veya requests.get(url_degiskeni) (değişkenli) gibi trivial
# varyasyonlarla atlatılabiliyordu. Artık üretilen araçlar salt-okuma/hesaplama
# mantığıyla sınırlı: süreç çalıştırma, ağ erişimi, dosya yazma/silme, dinamik
# import/exec ve düşük seviye sistem erişimi TÜMÜYLE yasak — kalıp/argüman
# formatından bağımsız (bkz. _GENERATION_PROMPT'taki izinli kütüphane listesi).
_BLOCKED_PATTERNS = [
    # Süreç / kabuk çalıştırma — HER biçimi (liste/literal argüman farketmez)
    r"\bos\.(?:system|popen|remove|unlink|rmdir|rename|replace|chmod|chown|"
    r"kill|fork|exec[lv]\w*|spawn\w*|startfile)\s*\(",
    r"\bsubprocess\.",
    r"\bshell\s*=\s*True",
    r"\bos\.environ\b",
    # Dinamik kod çalıştırma / import
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\bcompile\s*\(",
    r"__import__\s*\(",
    r"\bimportlib\.",
    r"\bgetattr\s*\([^,]+,\s*['\"]__",
    r"\bsetattr\s*\(",
    r"\bglobals\s*\(\s*\)",
    # Dosya yazma/silme — HER biçimi (proje içi/dışı farketmez)
    r"\bopen\s*\([^)]*['\"][wxa]\+?['\"]",
    r"\.write_text\s*\(|\.write_bytes\s*\(",
    r"\.unlink\s*\(|\.rmdir\s*\(|\.rename\s*\(|\.chmod\s*\(",
    r"\bshutil\.",
    # Ağ erişimi — HER biçimi (hardcoded/değişken URL farketmez)
    r"\brequests\.",
    r"\burllib\.",
    r"\bhttpx\.",
    r"\bsocket\.",
    r"\bftplib\.|\bsmtplib\.",
    # Serileştirme / düşük seviye sistem erişimi
    r"\bpickle\.",
    r"\bmarshal\.",
    r"\bctypes\.",
    r"\bwinreg\.",
]

_GENERATION_PROMPT = """Bir Python araç fonksiyonu yaz. Gereksinim:

{description}

KURALLAR (kesinlikle uy):
- Fonksiyon adı: tool_{snake_name} (snake_case)
- İmza: def tool_{snake_name}(arg: str = "") -> str:
- Her zaman str döndür
- Hataları yakala, Türkçe mesaj döndür
- Sadece: re, json, time, datetime, math, statistics, collections, pathlib (SADECE okuma — Path.read_text/exists/glob), psutil (SADECE okuma — CPU/RAM/disk bilgisi)
- YASAK: subprocess, os.system/popen, requests/urllib/httpx (ağ), dosyaya yazma/silme, eval/exec/compile, importlib, pickle, socket, ctypes, winreg
- Üretilen araç yalnız METİN/HESAPLAMA/BİÇİMLENDİRME mantığı olmalı — süreç çalıştırmaz, ağa çıkmaz, disk değiştirmez
- Docstring ekle (tek satır, Türkçe)
- Başka hiçbir şey yazma — sadece fonksiyon kodu

Örnek format:
```python
def tool_ornek(arg: str = "") -> str:
    \"\"\"Örnek araç.\"\"\"
    try:
        return f"Sonuç: {arg}"
    except Exception as e:
        return f"Hata: {e}"
```

Şimdi yaz:
```python"""


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcılar
# ─────────────────────────────────────────────────────────────────────────────

def _desc_to_snake(description: str) -> str:
    """Açıklamadan snake_case fonksiyon adı türet."""
    words = re.sub(r"[^a-zçğışöüA-ZÇĞİŞÖÜ0-9\s]", "", description).split()[:4]
    # Türkçe karakterleri karşılık gelen ASCII'ye çevir (büyük/küçük ayrı)
    _TR_SRC = "çğışöüÇĞİŞÖÜ"
    _TR_DST = "cgisou" + "CGISOU"   # Ü→U (büyük harf)
    tr_map = str.maketrans(_TR_SRC, _TR_DST)
    name = "_".join(w.lower().translate(tr_map) for w in words if w)
    name = re.sub(r"[^a-z0-9_]", "", name)
    return name or "custom_tool"


def _extract_code(raw: str) -> str:
    """LLM çıktısından Python kodunu çıkar."""
    # ```python ... ``` bloğunu bul
    match = re.search(r"```(?:python)?\n?(.*?)```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    # def ile başlayan kısmı al
    match = re.search(r"(def tool_\w+.*)", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw.strip()


def _syntax_check(code: str) -> tuple[bool, str]:
    """Python syntax kontrolü."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"Syntax hatası: {e}"


def _security_check(code: str) -> tuple[bool, str]:
    """Tehlikeli kalıpları kontrol et."""
    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, code):
            return False, f"Güvenlik ihlali: '{pattern}' tespit edildi"
    # Fonksiyon adı kontrolü
    if not re.search(r"def tool_\w+\s*\(", code):
        return False, "Fonksiyon adı 'tool_' ile başlamalı"
    return True, ""


def _extract_func_name(code: str) -> str:
    """Koddan fonksiyon adını çıkar."""
    match = re.search(r"def (tool_\w+)\s*\(", code)
    return match.group(1) if match else "tool_unknown"


def _load_generated_file() -> str:
    """Mevcut generated_tools.py içeriğini oku."""
    if GENERATED_FILE.exists():
        return GENERATED_FILE.read_text(encoding="utf-8")
    return '# -*- coding: utf-8 -*-\n"""Dinamik üretilen araçlar."""\n\n'


def _save_generated_file(content: str):
    """Atomik yazma — yarım kalırsa orijinal korunur."""
    tmp = GENERATED_FILE.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(GENERATED_FILE)


def _list_existing_tools(content: str) -> list[str]:
    """Dosyadaki mevcut (üst seviye) araç adlarını listele."""
    # re.MULTILINE ile ^ kullan — iç içe def'leri atla
    return re.findall(r"^def (tool_\w+)\s*\(", content, re.MULTILINE)


# ─────────────────────────────────────────────────────────────────────────────
# Ana API
# ─────────────────────────────────────────────────────────────────────────────

def generate_and_register(description: str) -> dict:
    """
    Yeni araç üret ve sisteme ekle.

    Args:
        description: Aracın ne yapması gerektiğinin açıklaması

    Returns:
        {"ok": bool, "tool_name": str, "message": str, "code": str}
    """
    if not description or not description.strip():
        return {"ok": False, "message": "Araç açıklaması boş."}

    description = description.strip()
    snake_name  = _desc_to_snake(description)

    # ── LLM ile kod üret ─────────────────────────────────────────────────────
    prompt = _GENERATION_PROMPT.format(
        description=description,
        snake_name=snake_name,
    )

    try:
        from brain.llm import call_ollama
        raw = call_ollama(prompt, retries=2, timeout=30, num_predict=400)
    except Exception as e:
        return {"ok": False, "message": f"LLM bağlantı hatası: {e}"}

    if not raw or raw.startswith("LLM hatası"):
        return {"ok": False, "message": "LLM kod üretemedi."}

    # ── Kodu çıkar ───────────────────────────────────────────────────────────
    code = _extract_code(raw)
    if not code:
        return {"ok": False, "message": "LLM geçerli kod üretemedi."}

    # ── Validasyon ───────────────────────────────────────────────────────────
    ok, err = _syntax_check(code)
    if not ok:
        return {"ok": False, "message": err, "code": code}

    ok, err = _security_check(code)
    if not ok:
        return {"ok": False, "message": err, "code": code}

    func_name = _extract_func_name(code)

    # ── Dosyaya kaydet ────────────────────────────────────────────────────────
    existing = _load_generated_file()
    existing_tools = _list_existing_tools(existing)

    if func_name in existing_tools:
        # Mevcut aracı güncelle — sadece üst seviye def'leri eşleştir
        new_existing = re.sub(
            rf"(?m)^def {func_name}\s*\(.*?(?=\n(?![ \t])|\Z)",
            code + "\n",
            existing,
            flags=re.DOTALL,
        )
        if new_existing == existing:
            log.warning("%s güncelleme regex'i eşleşmedi, sona ekleniyor.", func_name)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            new_existing = existing + f"\n# Güncellendi: {timestamp}\n{code}\n"
        existing = new_existing
    else:
        # Yeni araç ekle
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        existing += f"\n# Üretildi: {timestamp} — {description[:60]}\n{code}\n"

    try:
        _save_generated_file(existing)
    except Exception as e:
        return {"ok": False, "message": f"Dosyaya yazılamadı: {e}", "code": code}

    # ── Dinamik yükleme ve kayıt ──────────────────────────────────────────────
    tool_registered_name = func_name[5:] if func_name.startswith("tool_") else func_name
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("generated_tools", GENERATED_FILE)
        assert spec is not None and spec.loader is not None  # dosya var → spec/loader None olmaz
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        func = getattr(mod, func_name, None)
        if func:
            from brain.tool_registry import register_tool
            register_tool(tool_registered_name, func)

            # KLARGUS_TOOLS'a ekle (bu oturum için)
            try:
                from brain.llm import KLARGUS_TOOLS
                already = any(
                    t.get("function", {}).get("name") == tool_registered_name
                    for t in KLARGUS_TOOLS
                )
                if not already:
                    KLARGUS_TOOLS.append({
                        "type": "function",
                        "function": {
                            "name": tool_registered_name,
                            "description": description[:200],
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "arg": {"type": "string", "description": "Araç argümanı"}
                                },
                                "required": [],
                            },
                        },
                    })
            except Exception as _ke:
                log.error("KLARGUS_TOOLS güncellenemedi: %s", _ke)
    except Exception as e:
        return {
            "ok": True,  # dosyaya kaydedildi, sadece dinamik yükleme başarısız
            "tool_name": tool_registered_name,
            "message": f"Araç kaydedildi ama dinamik yükleme başarısız (restart gerekebilir): {e}",
            "code": code,
        }

    return {
        "ok": True,
        "tool_name": tool_registered_name,
        "message": (
            f"Araç oluşturuldu ve aktifleştirildi: '{tool_registered_name}'\n"
            f"Dosya: tools/generated_tools.py"
        ),
        "code": code,
    }


def list_generated_tools() -> str:
    """Üretilmiş araçları listele."""
    content = _load_generated_file()
    tools = _list_existing_tools(content)
    if not tools:
        return "Henüz üretilmiş araç yok."
    lines = [f"Üretilmiş araçlar ({len(tools)}):"]
    for t in tools:
        name = t[5:] if t.startswith("tool_") else t
        lines.append(f"  • {name}")
    return "\n".join(lines)


def delete_generated_tool(tool_name: str) -> dict:
    """Üretilmiş bir aracı sil."""
    func_name = f"tool_{tool_name}" if not tool_name.startswith("tool_") else tool_name
    content = _load_generated_file()

    if func_name not in content:
        return {"ok": False, "message": f"'{tool_name}' bulunamadı."}

    # Fonksiyonu sil
    new_content = re.sub(
        rf"# Üretildi:.*?\n{func_name}.*?(?=\n# Üretildi:|\Z)",
        "",
        content,
        flags=re.DOTALL,
    )
    _save_generated_file(new_content)
    return {"ok": True, "message": f"'{tool_name}' silindi."}


def get_generated_tool_code(tool_name: str) -> str:
    """Üretilmiş aracın kodunu göster."""
    func_name = f"tool_{tool_name}" if not tool_name.startswith("tool_") else tool_name
    content   = _load_generated_file()
    match = re.search(rf"(def {func_name}.*?)(?=\ndef |\Z)", content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return f"'{tool_name}' bulunamadı."
