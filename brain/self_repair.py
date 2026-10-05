# -*- coding: utf-8 -*-
"""
brain/self_repair.py — Otomatik Hata Tespiti ve Kod Düzeltme
=============================================================
Klargus kendi hatalarını tespit eder, ilgili kaynak kodu okur,
düzeltme önerir ve onaylanırsa uygular.

Akış:
  1. analyze_errors()     → Hangi araçlar en çok hata veriyor?
  2. diagnose(tool_name)  → O aracın kodunu oku, hatayı anla
  3. propose_fix(...)     → LLM düzeltme kodu yazar
  4. apply_fix(...)       → Backup al, fix uygula
  5. verify_fix(...)      → Syntax kontrolü yap

Kullanım:
  "kendini düzelt"
  "hata raporunu göster"
  "spotify aracını düzelt"
"""

import ast
import json
import re
import shutil
import time
from collections import defaultdict
from pathlib import Path

from core.logger import get_logger

log = get_logger("self_repair")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR     = PROJECT_ROOT / "data"
BACKUP_DIR   = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 1) Hata Analizi
# ─────────────────────────────────────────────────────────────────────────────

def analyze_errors(last_n: int = 300, min_fail_rate: float = 0.3) -> list[dict]:
    """
    Konuşma logunu analiz et, hata oranı yüksek araçları bul.

    Returns:
        [{"tool": str, "total": int, "fails": int, "rate": float}]
    """
    log_file = DATA_DIR / "conversations.jsonl"
    if not log_file.exists():
        return []

    entries = []
    try:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return []

    entries = entries[-last_n:]
    tool_stats: dict = defaultdict(lambda: {"total": 0, "fails": 0})

    for e in entries:
        action = e.get("action", "")
        if not action or action in ("chat", "fast_route", "llm", "sovereign", "unknown"):
            continue
        tool_stats[action]["total"] += 1
        if not e.get("ok", True):
            tool_stats[action]["fails"] += 1

    problems = []
    for tool, stats in tool_stats.items():
        total = stats["total"]
        fails = stats["fails"]
        if total < 2:
            continue
        rate = fails / total
        if rate >= min_fail_rate:
            problems.append({
                "tool":  tool,
                "total": total,
                "fails": fails,
                "rate":  round(rate, 2),
            })

    return sorted(problems, key=lambda x: -x["rate"])


def error_report() -> str:
    """İnsan okunabilir hata raporu."""
    problems = analyze_errors()
    if not problems:
        return "Tespit edilmiş sistematik hata yok — sistem sağlıklı görünüyor."

    lines = [f"Hata Raporu — {len(problems)} sorunlu araç:\n"]
    for p in problems:
        bar = "█" * int(p["rate"] * 10) + "░" * (10 - int(p["rate"] * 10))
        lines.append(
            f"  {p['tool']:<30}  {bar}  %{p['rate']*100:.0f} hata  ({p['fails']}/{p['total']})"
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 2) Araç Kaynak Dosyası Bulma
# ─────────────────────────────────────────────────────────────────────────────

def _find_tool_source(tool_name: str) -> tuple[Path | None, str]:
    """
    Araç adından kaynak dosyasını ve fonksiyon kodunu bul.
    Returns: (file_path, function_code)
    """
    search_dirs = [
        PROJECT_ROOT / "tools",
        PROJECT_ROOT / "brain",
        PROJECT_ROOT / "audio",
        PROJECT_ROOT / "vision",
    ]

    # tool_ önekiyle ara
    func_names = [tool_name, f"tool_{tool_name}"]

    for d in search_dirs:
        if not d.exists():
            continue
        for py_file in d.glob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for fn in func_names:
                match = re.search(
                    rf"(def {fn}\s*\(.*?)(?=\ndef |\Z)",
                    content,
                    re.DOTALL,
                )
                if match:
                    return py_file, match.group(1).strip()

    return None, ""


# ─────────────────────────────────────────────────────────────────────────────
# 3) Tanı ve Düzeltme Önerisi
# ─────────────────────────────────────────────────────────────────────────────

def diagnose(tool_name: str) -> str:
    """Aracın kaynak kodunu oku ve sorununu tespit et."""
    file_path, func_code = _find_tool_source(tool_name)

    if not func_code:
        return f"'{tool_name}' aracının kaynak kodu bulunamadı."

    # Son hata mesajlarını bul
    log_file = DATA_DIR / "conversations.jsonl"
    error_examples = []
    if log_file.exists():
        try:
            for line in log_file.read_text(encoding="utf-8").splitlines()[-100:]:
                try:
                    e = json.loads(line)
                    if e.get("action") == tool_name and not e.get("ok", True):
                        err_text = e.get("assistant", "")[:200]
                        if err_text:
                            error_examples.append(err_text)
                except Exception:
                    pass
        except Exception:
            pass

    lines = [
        f"TANI: '{tool_name}'",
        f"Dosya: {file_path.relative_to(PROJECT_ROOT) if file_path else 'bilinmiyor'}",
        "",
        "Kaynak Kod:",
        func_code[:1500],
    ]

    if error_examples:
        lines.append(f"\nSon hata örnekleri:")
        for ex in error_examples[-3:]:
            lines.append(f"  • {ex}")

    return "\n".join(lines)


# ── Güvenlik taraması (2026-09-29) ────────────────────────────────────────────
# Önceden propose_fix() sadece ast.parse() ile SÖZDİZİMİ kontrolü yapıyordu —
# LLM'in ürettiği "düzeltme" kodu içerik güvenliği hiç taranmadan doğrudan proje
# dosyasına yazılıyordu. tool_generator.py'nin tam kilidi burada UYGULANAMAZ
# (self_repair var olan sistem araçlarını onarıyor — subprocess/requests/os gibi
# kütüphaneler bu araçların MEŞRU işlevidir); bunun yerine, hangi bağlamda olursa
# olsun asla meşru olmayan ilkel yapıları (dinamik kod çalıştırma, serileştirme,
# düşük seviye sistem erişimi) engelleyen dar bir kara liste kullanılır.
_REPAIR_BLOCKED_PATTERNS = [
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\bcompile\s*\(",
    r"__import__\s*\(",
    r"\bimportlib\.",
    r"\bpickle\.",
    r"\bmarshal\.",
    r"\bctypes\.",
    r"\bwinreg\.",
    r"\bos\.system\s*\(",
    r"\bshell\s*=\s*True",
    r"\bsetattr\s*\(",
    r"\bglobals\s*\(\s*\)",
]


def _repair_security_check(code: str) -> tuple[bool, str]:
    """Onarım kodunda kesinlikle meşru olmayan tehlikeli ilkel yapıları engelle."""
    for pattern in _REPAIR_BLOCKED_PATTERNS:
        if re.search(pattern, code):
            return False, f"Güvenlik ihlali: '{pattern}' tespit edildi — onarım reddedildi."
    return True, ""


def propose_fix(tool_name: str, error_description: str = "") -> dict:
    """
    LLM ile araç için düzeltme kodu öner.

    Returns:
        {"ok": bool, "original": str, "fixed": str, "file": str, "message": str}
    """
    file_path, original_code = _find_tool_source(tool_name)

    if not original_code:
        return {"ok": False, "message": f"'{tool_name}' kaynağı bulunamadı."}

    # Son hatalar
    log_file = DATA_DIR / "conversations.jsonl"
    recent_errors = []
    if log_file.exists():
        try:
            for line in log_file.read_text(encoding="utf-8").splitlines()[-200:]:
                try:
                    e = json.loads(line)
                    if e.get("action") == tool_name and not e.get("ok", True):
                        recent_errors.append(e.get("assistant", "")[:150])
                except Exception:
                    pass
        except Exception:
            pass

    error_ctx = ""
    if error_description:
        error_ctx = f"Hata açıklaması: {error_description}\n"
    if recent_errors:
        error_ctx += "Son hata mesajları:\n" + "\n".join(f"  - {e}" for e in recent_errors[-3:])

    prompt = (
        f"Aşağıdaki Python aracı hata veriyor. Düzeltilmiş versiyonunu yaz.\n\n"
        f"ARAÇ ADI: {tool_name}\n"
        f"{error_ctx}\n\n"
        f"MEVCUT KOD:\n```python\n{original_code}\n```\n\n"
        f"KURALLAR:\n"
        f"- Aynı fonksiyon adını koru\n"
        f"- Aynı imzayı koru (arg: str = '')\n"
        f"- Hatayı düzelt\n"
        f"- Türkçe hata mesajları\n"
        f"- Sadece Python kodu yaz, açıklama ekleme\n\n"
        f"Düzeltilmiş kod:\n```python"
    )

    try:
        from brain.llm import call_ollama
        raw = call_ollama(prompt, retries=2, timeout=30, num_predict=500)
    except Exception as e:
        return {"ok": False, "message": f"LLM hatası: {e}"}

    if not raw or raw.startswith("LLM hatası"):
        return {"ok": False, "message": "LLM düzeltme üretemedi."}

    # Kodu çıkar
    match = re.search(r"```(?:python)?\n?(.*?)```", raw, re.DOTALL)
    fixed_code = match.group(1).strip() if match else raw.strip()

    # Syntax kontrolü
    try:
        ast.parse(fixed_code)
    except SyntaxError as e:
        return {"ok": False, "message": f"Üretilen kodda syntax hatası: {e}", "code": fixed_code}

    # Güvenlik taraması — dinamik kod çalıştırma/serileştirme/düşük seviye erişim yasak
    ok_sec, err_sec = _repair_security_check(fixed_code)
    if not ok_sec:
        return {"ok": False, "message": err_sec, "code": fixed_code}

    # Fonksiyon adı tutarlılığı — "düzeltme" orijinal fonksiyonun yerine
    # BAŞKA/EK bir fonksiyon geçirmemeli (isim değişmeden kalmalı)
    orig_name_m = re.search(r"def (\w+)\s*\(", original_code)
    fixed_name_m = re.search(r"def (\w+)\s*\(", fixed_code)
    if orig_name_m and (not fixed_name_m or fixed_name_m.group(1) != orig_name_m.group(1)):
        return {"ok": False, "message": "Üretilen kodda fonksiyon adı orijinalle uyuşmuyor — reddedildi.",
                "code": fixed_code}

    return {
        "ok":       True,
        "original": original_code,
        "fixed":    fixed_code,
        "file":     str(file_path) if file_path else "",
        "message":  f"Düzeltme hazır. apply_fix() ile uygula.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4) Düzeltme Uygulama
# ─────────────────────────────────────────────────────────────────────────────

def apply_fix(file_path: str, original_code: str, fixed_code: str) -> dict:
    """
    Düzeltmeyi dosyaya uygula. Öncesinde backup alır.

    Returns:
        {"ok": bool, "backup": str, "message": str}
    """
    path = Path(file_path)
    if not path.exists():
        return {"ok": False, "message": f"Dosya bulunamadı: {file_path}"}

    # Backup al — milisaniye hassasiyetiyle çakışma önle
    ts = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time()*1000) % 1000:03d}"
    backup_path = BACKUP_DIR / f"{path.stem}_{ts}.py.bak"
    try:
        shutil.copy2(path, backup_path)
    except Exception as e:
        return {"ok": False, "message": f"Backup alınamadı: {e}"}

    # Eski kodu yeni kodla değiştir
    try:
        content = path.read_text(encoding="utf-8")
        # Eski fonksiyonu bul ve değiştir
        func_name_match = re.search(r"def (\w+)\s*\(", original_code)
        if not func_name_match:
            return {"ok": False, "message": "Orijinal kodda fonksiyon adı bulunamadı."}

        func_name = func_name_match.group(1)
        # Fonksiyon bloğunu bul (bir sonraki def'e kadar)
        pattern = rf"(def {func_name}\s*\(.*?)(?=\ndef |\Z)"
        if not re.search(pattern, content, re.DOTALL):
            return {"ok": False, "message": f"'{func_name}' dosyada bulunamadı."}

        new_content = re.sub(pattern, fixed_code, content, flags=re.DOTALL)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(new_content, encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        # Başarısız → backup'tan geri yükle
        shutil.copy2(backup_path, path)
        return {"ok": False, "message": f"Uygulama başarısız, geri yüklendi: {e}"}

    # Şeffaflık: ne değiştiğini kısa bir diff olarak göster (sessiz kara kutu olmasın —
    # onay akışı henüz burada yok, ama en azından NE yazıldığı görünür olmalı).
    try:
        import difflib
        diff_lines = list(difflib.unified_diff(
            original_code.splitlines(), fixed_code.splitlines(),
            fromfile="öncesi", tofile="sonrası", lineterm="", n=1,
        ))[:40]
        diff_txt = "\n".join(diff_lines) if diff_lines else "(fark yok)"
    except Exception:
        diff_txt = "(diff üretilemedi)"

    return {
        "ok":     True,
        "backup": str(backup_path),
        "message": (
            f"Düzeltme uygulandı: {path.name}\n"
            f"Backup: {backup_path.name}\n"
            f"Değişiklik:\n```diff\n{diff_txt}\n```\n"
            f"Değişikliklerin tam etkisi için uygulamayı yeniden başlat."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5) Tek Adım Otomatik Onarım
# ─────────────────────────────────────────────────────────────────────────────

def auto_repair(tool_name: str = "") -> str:
    """
    En çok hata veren aracı otomatik onar.
    tool_name boşsa en kötü aracı seç.
    """
    if not tool_name:
        problems = analyze_errors()
        if not problems:
            return "Düzeltilecek sistematik hata bulunamadı."
        tool_name = problems[0]["tool"]

    log.info("'%s' aracı onarılıyor...", tool_name)

    fix = propose_fix(tool_name)
    if not fix["ok"]:
        return f"Düzeltme üretilemedi: {fix['message']}"

    result = apply_fix(fix["file"], fix["original"], fix["fixed"])
    if result["ok"]:
        return f"'{tool_name}' onarıldı. {result['message']}"
    return f"Onarım başarısız: {result['message']}"
