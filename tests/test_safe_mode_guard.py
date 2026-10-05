# -*- coding: utf-8 -*-
"""
tests/test_safe_mode_guard.py — Güvenli-mod (KLARGUS_TEST_MODE) doğrudan-çağrı guard'ı

Kanıt: test modunda sistem-durumu değiştiren fonksiyonlar DOĞRUDAN çağrılsa bile
(araç dispatcher'ı olmadan) dosya/sistem durumu DEĞİŞMEZ.

Geçen turda doğrudan set_input_device çağrısı data/audio_device.json'u
değiştiriyordu (dispatcher guard'ı doğrudan çağrıyı kapsamıyordu). Bu guard
kontrolü fonksiyonların KENDİ girişine indirir → nereden çağrılırsa çağrılsın
güvenli.

Çalıştırma:  python tests/test_safe_mode_guard.py
"""

# Bu dosya BAĞIMSIZ bir betiktir (python tests/test_safe_mode_guard.py ile çalıştırılır) — pytest
# test suite'i DEĞİLDİR. __test__ = False, pytest'in bu modülü hiç
# TOPLAMAMASINI sağlar (2026-09-29 — bkz. test_klargus_suite.py'deki
# collection hatası kök-neden düzeltmesi).
__test__ = False

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["KLARGUS_TEST_MODE"] = "1"   # import ÖNCESİ ayarla

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _cases():
    from audio.device_manager import set_input_device, auto_select_input_device
    from core.system_control import apply_system_change
    from core.safe_mode import is_safe_mode
    from brain.tool_result import ToolResult

    cases = []
    cases.append(("safe_mode_acik", is_safe_mode() is True))

    DATA = Path(__file__).resolve().parents[1] / "data"
    cfg = DATA / "audio_device.json"

    # Dosya durumunu ÖNCE kaydet (varsa)
    before = cfg.read_bytes() if cfg.exists() else None

    # ── Doğrudan çağrılar — hiçbiri config/sistem durumunu değiştirmemeli ──────
    r1 = set_input_device(None, source="manual")
    r2 = set_input_device(12, source="manual")          # geçerli olsa bile yazmamalı
    r3 = auto_select_input_device()                     # mic donanımına erişmemeli
    r4 = apply_system_change("mic_default", approval_token="approved")
    r5 = apply_system_change("startup_enable", approval_token="approved")

    after = cfg.read_bytes() if cfg.exists() else None

    # 1) audio_device.json DEĞİŞMEDİ (asıl kanıt)
    cases.append(("audio_config_degismedi", before == after))
    # 2) Dosya yoktan var OLMADI / varken silinmedi
    cases.append(("audio_config_varlik_ayni", (before is None) == (after is None)))

    # 3) Her doğrudan çağrı ToolResult(ok=True) + "test modu" mesajı döndürür
    for name, r in [("set_none", r1), ("set_idx", r2), ("auto_select", r3),
                    ("apply_mic", r4), ("apply_startup", r5)]:
        cases.append((f"{name}_toolresult_ok", isinstance(r, ToolResult) and r.ok is True))
        cases.append((f"{name}_testmodu_mesaj", "test modu" in str(r)))

    # 4) Onaysız yol guard'dan ÖNCE (onay kontrolü) → gerçek mesaj korunur
    r_onaysiz = apply_system_change("mic_default", approval_token="")
    cases.append(("onaysiz_mesaj_korundu",
                  str(r_onaysiz) == "Bu sistem degisikligi once acik onay gerektirir."))
    # 5) Bilinmeyen aksiyon guard'a girmez (mutating-only) → gerçek mesaj korunur
    r_bilinmeyen = apply_system_change("yok_boyle", approval_token="approved")
    cases.append(("bilinmeyen_mesaj_korundu",
                  str(r_bilinmeyen) == "Bilinmeyen sistem degisikligi: yok_boyle"))

    return cases


def main() -> int:
    print("=" * 60)
    print("GÜVENLİ-MOD DOĞRUDAN-ÇAĞRI GUARD TESTLERİ")
    print("=" * 60)
    try:
        cases = _cases()
    except Exception as e:
        print(f"KURULUM HATASI: {e}")
        return 1
    fails = []
    for name, ok in cases:
        print(f"  {'GEÇTİ' if ok else 'BAŞARISIZ'}  {name}")
        if not ok:
            fails.append(name)
    print("-" * 60)
    if fails:
        print(f"SONUÇ: {len(fails)} başarısız — {', '.join(fails)}")
        return 1
    print(f"SONUÇ: {len(cases)} testin TAMAMI GEÇTİ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
