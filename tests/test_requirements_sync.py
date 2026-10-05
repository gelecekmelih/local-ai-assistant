# -*- coding: utf-8 -*-
"""
tests/test_requirements_sync.py — requirements.txt ↔ venv senkron kontrolü

NEDEN: dnspython / duckduckgo-search / python-whois üçü de requirements.txt'te
BEYAN EDİLMİŞTİ ama venv'e kurulmamıştı. Araçlar bu durumda sessizce ok=False
dönüp gerçek işlevi maskeliyordu (perf_dns_lookup sahte-yeşildi, whois_github
SKIP oluyordu). Bu tekrar eden bir sınıf → beyan edilen her paketin venv'de
KURULU olduğunu importlib ile doğrulayan test.

Bu test AĞDAN BAĞIMSIZDIR — yalnız find_spec ile modül var mı bakar, import
etmez (yan etki yok, ağ yok).

Çalıştırma:  python tests/test_requirements_sync.py
"""

# Bu dosya BAĞIMSIZ bir betiktir (python tests/test_requirements_sync.py ile çalıştırılır) — pytest
# test suite'i DEĞİLDİR. __test__ = False, pytest'in bu modülü hiç
# TOPLAMAMASINI sağlar (2026-09-29 — bkz. test_klargus_suite.py'deki
# collection hatası kök-neden düzeltmesi).
__test__ = False

import importlib.util
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ROOT = Path(__file__).resolve().parents[1]
_REQ = _ROOT / "requirements.txt"

# pip DAĞITIM adı  →  import (modül) adı — sadece ikisi FARKLI olanlar.
# Anahtarlar küçük harf; listede olmayanlar için varsayılan kural:
#   import_adi = pip_adi.lower().replace("-", "_)
# (ör. faster-whisper→faster_whisper, edge-tts→edge_tts varsayılanla doğru çıkar).
_PIP_TO_IMPORT = {
    "python-whois": "whois",
    "duckduckgo-search": "duckduckgo_search",
    "opencv-python": "cv2",
    "pillow": "PIL",
    "beautifulsoup4": "bs4",
    "screen-brightness-control": "screen_brightness_control",
    "faster-whisper": "faster_whisper",
    "webrtcvad-wheels": "webrtcvad",
    "pywin32": "win32api",
    "dnspython": "dns",          # dağıtım dnspython, modül dns
    "pyinstaller": "PyInstaller",  # modül adı büyük harfli
}


def _parse_requirements(text: str) -> list[str]:
    """requirements.txt satırlarından pip paket adlarını çıkar.

    Yorumları (#) ve boş satırları atlar; sürüm belirteçlerini (>=, ==, ~=, !=,
    <, >) ve extras ([...]) soyar; yalnız paket adını döndürür.
    """
    pkgs: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()  # satır içi yorumu at
        if not line:
            continue
        # Baştaki paket adı: harf/rakam/nokta/alt tire/tire
        m = re.match(r"^([A-Za-z0-9._-]+)", line)
        if m:
            pkgs.append(m.group(1))
    return pkgs


def _import_name(pip_name: str) -> str:
    key = pip_name.lower()
    return _PIP_TO_IMPORT.get(key, key.replace("-", "_"))


def _is_installed(import_name: str) -> bool:
    """Modül venv'de bulunuyor mu — import ETMEDEN (yan etkisiz) find_spec ile bak."""
    try:
        return importlib.util.find_spec(import_name) is not None
    except (ImportError, ValueError):
        # find_spec, parent paket eksikse ImportError; geçersiz adda ValueError
        return False


def _find_missing() -> tuple[list[str], list[str]]:
    """(beyan_edilen_pip_adlari, kurulu_OLMAYAN_pip_adlari) döndür."""
    pkgs = _parse_requirements(_REQ.read_text(encoding="utf-8"))
    missing = [p for p in pkgs if not _is_installed(_import_name(p))]
    return pkgs, missing


# main() eksik listesini raporlayabilsin diye modül seviyesinde sakla
_PARSED: list[str] = []
_MISSING: list[str] = []


def _cases():
    global _PARSED, _MISSING
    cases = []

    cases.append(("requirements_dosyasi_var", _REQ.is_file()))

    _PARSED, _MISSING = _find_missing()

    # Ayrıştırıcı bozulmuşsa (0 paket) sync testi yanıltıcı yeşil olmasın
    cases.append(("requirements_ayristirildi", len(_PARSED) >= 10))

    # ASIL KONTROL: beyan edilen her paket venv'de kurulu olmalı
    cases.append(("requirements_venv_sync", len(_MISSING) == 0))

    return cases


def main() -> int:
    print("=" * 60)
    print("REQUIREMENTS ↔ VENV SENKRON TESTİ")
    print("=" * 60)
    try:
        cases = _cases()
    except Exception as e:
        import traceback
        print(f"KURULUM HATASI: {e}")
        traceback.print_exc()
        return 1

    fails = []
    for name, ok in cases:
        print(f"  {'GEÇTİ' if ok else 'BAŞARISIZ'}  {name}")
        if not ok:
            fails.append(name)

    if _MISSING:
        print("-" * 60)
        print(f"KURULU OLMAYAN {len(_MISSING)} paket (requirements.txt'te beyan edilmiş):")
        for p in _MISSING:
            print(f"    - {p}  (import: {_import_name(p)})")
        print("  ÇÖZÜM: .venv\\Scripts\\python.exe -m pip install -r requirements.txt")

    print("-" * 60)
    if fails:
        print(f"SONUÇ: {len(fails)} başarısız — {', '.join(fails)}")
        return 1
    print(f"SONUÇ: {len(cases)} testin TAMAMI GEÇTİ ({len(_PARSED)} paket doğrulandı).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
