# -*- coding: utf-8 -*-
"""
tools/codebase_tools.py — LLM Kod Erişim Araçları
===================================================
LLM'in kendi proje dosyalarını okuyup arayabilmesini sağlar.
Hedef: LLM kendi davranışını anlasın, hata ayıklasın, öneride bulunsun.
"""

import re
from pathlib import Path

from brain.tool_result import ToolResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# LLM'in okuyabileceği güvenli uzantılar
_ALLOWED_EXTS = {".py", ".json", ".txt", ".md", ".yaml", ".yml", ".toml", ".cfg", ".ini"}

# LLM'in erişemeyeceği klasörler (gizli / büyük / gereksiz)
_BLOCKED_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env",
    "node_modules", ".claude", "models", "piper",
    "whisper.cpp", ".mypy_cache", "dist", "build",
}

_MAX_FILE_CHARS = 8000   # tek seferde max okunabilir karakter
_MAX_SEARCH_RESULTS = 12


def _safe_path(rel_path: str) -> Path | None:
    """Göreceli yolu doğrula ve proje içinde olduğundan emin ol."""
    try:
        path = (PROJECT_ROOT / rel_path.strip("/\\")).resolve()
        path.relative_to(PROJECT_ROOT)  # proje dışına çıkış önlemi
        # Yasaklı klasör kontrolü
        for part in path.parts:
            if part in _BLOCKED_DIRS:
                return None
        return path
    except Exception:
        return None


def read_project_file(path: str) -> ToolResult:
    """
    Proje içindeki bir dosyayı oku.
    arg = dosya yolu (proje köküne göre, örn: 'brain/sovereign_brain.py')
    """
    if not path or not path.strip():
        return ToolResult(message="Dosya yolu belirtilmedi.", ok=True)

    safe = _safe_path(path)
    if safe is None:
        return ToolResult(message=f"Erişim reddedildi veya geçersiz yol: {path}", ok=True)

    if not safe.exists():
        return ToolResult(message=f"Dosya bulunamadı: {path}", ok=False, error="dosya_yok")

    if safe.is_dir():
        return list_project_files(path)

    if safe.suffix.lower() not in _ALLOWED_EXTS:
        return ToolResult(message=f"Bu dosya türüne erişim yok: {safe.suffix}", ok=True)

    try:
        content = safe.read_text(encoding="utf-8", errors="replace")
        if len(content) > _MAX_FILE_CHARS:
            content = content[:_MAX_FILE_CHARS] + f"\n\n... (dosya kesildi, toplam {len(content)} karakter)"
        return ToolResult(message=f"=== {path} ===\n{content}", ok=True, data=content)
    except Exception as e:
        return ToolResult(message=f"Dosya okunamadı: {e}", ok=False, error=str(e))


def list_project_files(directory: str = "") -> ToolResult:
    """
    Proje içindeki bir klasörün içeriğini listele.
    arg = klasör yolu (boş = proje kökü)
    """
    target = _safe_path(directory) if directory.strip() else PROJECT_ROOT

    if target is None:
        return ToolResult(message=f"Erişim reddedildi: {directory}", ok=True)
    if not target.exists():
        return ToolResult(message=f"Klasör bulunamadı: {directory}", ok=False, error="klasor_yok")
    if not target.is_dir():
        return read_project_file(directory)

    lines = [f"📁 {target.relative_to(PROJECT_ROOT) if target != PROJECT_ROOT else 'Proje kökü'}:"]
    try:
        items = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        for item in items:
            if item.name in _BLOCKED_DIRS or item.name.startswith("."):
                continue
            if item.is_dir():
                lines.append(f"  📂 {item.name}/")
            elif item.suffix.lower() in _ALLOWED_EXTS:
                size = item.stat().st_size
                lines.append(f"  📄 {item.name}  ({size:,} byte)")
    except Exception as e:
        lines.append(f"  Listeleme hatası: {e}")

    return ToolResult(message="\n".join(lines), ok=True, data=lines)


def search_project_code(query: str) -> ToolResult:
    """
    Proje Python dosyalarında metin/kod ara.
    arg = aranacak terim (düz metin veya regex)
    """
    if not query or not query.strip():
        return ToolResult(message="Aranacak terim belirtilmedi.", ok=True)

    query = query.strip()
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    results = []
    searched = 0

    for py_file in PROJECT_ROOT.rglob("*.py"):
        # Yasaklı klasör kontrolü
        skip = False
        for part in py_file.parts:
            if part in _BLOCKED_DIRS:
                skip = True
                break
        if skip:
            continue

        searched += 1
        try:
            lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                rel = str(py_file.relative_to(PROJECT_ROOT))
                results.append(f"{rel}:{i}  {line.strip()[:120]}")
                if len(results) >= _MAX_SEARCH_RESULTS:
                    break
        if len(results) >= _MAX_SEARCH_RESULTS:
            break

    if not results:
        return ToolResult(message=f"'{query}' için sonuç bulunamadı. ({searched} dosya tarandı)",
                          ok=False, error="sonuc_yok")

    header = f"'{query}' için {len(results)} sonuç ({searched} dosya tarandı):"
    return ToolResult(message=header + "\n" + "\n".join(results), ok=True, data=results)


def get_project_structure() -> ToolResult:
    """
    Projenin üst seviye klasör ve dosya yapısını göster.
    Büyük/gizli klasörler hariç tutulur.
    """
    lines = ["Klargus Proje Yapısı:", "=" * 36]

    def _walk(path: Path, indent: int = 0, max_depth: int = 2):
        if indent > max_depth:
            return
        try:
            items = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except Exception:
            return
        for item in items:
            if item.name in _BLOCKED_DIRS or item.name.startswith("."):
                continue
            prefix = "  " * indent
            if item.is_dir():
                lines.append(f"{prefix}📂 {item.name}/")
                _walk(item, indent + 1, max_depth)
            elif item.suffix.lower() in _ALLOWED_EXTS:
                lines.append(f"{prefix}📄 {item.name}")

    _walk(PROJECT_ROOT)
    return ToolResult(message="\n".join(lines), ok=True, data=lines)


def get_file_summary(path: str) -> ToolResult:
    """
    Bir Python dosyasındaki sınıf ve fonksiyon isimlerini özetle (tam içerik yok).
    arg = dosya yolu
    """
    safe = _safe_path(path)
    if safe is None or not safe.exists() or safe.suffix != ".py":
        return read_project_file(path)

    try:
        content = safe.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return ToolResult(message=f"Okunamadı: {e}", ok=False, error=str(e))

    lines = []
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("class ", "def ", "async def ")):
            lines.append(f"  {i:4d}: {stripped[:100]}")

    if not lines:
        return ToolResult(message=f"{path}: Sınıf/fonksiyon bulunamadı.", ok=False, error="bos")

    return ToolResult(message=f"=== {path} — Sınıf ve Fonksiyonlar ===\n" + "\n".join(lines),
                      ok=True, data=lines)
