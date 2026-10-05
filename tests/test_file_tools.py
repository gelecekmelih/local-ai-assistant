# -*- coding: utf-8 -*-
"""
tests/test_file_tools.py — tools/file_tools.py birim testleri

Her araç için: başarı yolu + en az bir hata yolu. Ayrıca _resolve_path
semantiği ve ToolResult sözleşmesi (ok/data/error, Türkçe message).

Yan etki güvenliği:
  - Yazma/silme testleri home ALTINDA geçici bir klasöre yazar (gerçek Masaüstü'ne
    DOKUNMAZ) ve sonunda temizler.
  - "güvenlik sınırı" testleri home DIŞINDA mutlak yol kullanır; _within_home
    kontrolü YAZMADAN önce döndüğü için diske hiç dokunulmaz.

Çalıştırma:  python tests/test_file_tools.py
"""

# Bu dosya BAĞIMSIZ bir betiktir (python tests/test_file_tools.py ile çalıştırılır) — pytest
# test suite'i DEĞİLDİR. __test__ = False, pytest'in bu modülü hiç
# TOPLAMAMASINI sağlar (2026-09-29 — bkz. test_klargus_suite.py'deki
# collection hatası kök-neden düzeltmesi).
__test__ = False

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import tools.file_tools as F
from brain.tool_result import ToolResult


def _cases():
    cases = []
    home = Path.home()
    tmp = home / f".klargus_filetools_test_{os.getpid()}"
    # home DIŞI mutlak yol (home.parent altında → home'a relative_to başarısız)
    outside = home.parent / f"klargus_guvenlik_disari_{os.getpid()}.txt"

    try:
        tmp.mkdir(parents=True, exist_ok=True)

        # ── _resolve_path semantiği ──────────────────────────────────────────
        cases.append(("resolve_masaustu", F._resolve_path("masaüstü") == F._DESKTOP))
        cases.append(("resolve_desktop", F._resolve_path("desktop") == F._DESKTOP))
        cases.append(("resolve_goreli_desktop_tabanli",
                      F._resolve_path("test.txt") == F._DESKTOP / "test.txt"))
        cases.append(("resolve_mutlak_aynen",
                      F._resolve_path(str(tmp / "x.txt")) == tmp / "x.txt"))
        cases.append(("resolve_desktop_onek",
                      F._resolve_path("desktop/alt/x.txt") == F._DESKTOP / "alt" / "x.txt"))
        cases.append(("resolve_bos_desktop", F._resolve_path("") == F._DESKTOP))

        # ── create_file ──────────────────────────────────────────────────────
        f1 = tmp / "olustur.txt"
        r = F.create_file(str(f1), "merhaba")
        cases.append(("create_ok",
                      isinstance(r, ToolResult) and r.ok is True and f1.read_text(encoding="utf-8") == "merhaba"))
        cases.append(("create_msg_turkce", "oluşturuldu" in str(r) and r.error is None))
        cases.append(("create_data_yol", r.data == str(f1)))
        r = F.create_file(str(outside), "x")
        cases.append(("create_guvenlik", r.ok is False and r.error == "guvenlik_siniri"
                      and not outside.exists()))

        # ── read_file ────────────────────────────────────────────────────────
        r = F.read_file(str(f1))
        cases.append(("read_ok", r.ok is True and r.data == "merhaba"))
        r = F.read_file(str(tmp / "yok_boyle_dosya.txt"))
        cases.append(("read_bulunamadi", r.ok is False and r.error == "dosya_bulunamadi"))
        # 50KB kırpma
        big = tmp / "buyuk.txt"
        big.write_text("A" * (60 * 1024), encoding="utf-8")
        r = F.read_file(str(big))
        cases.append(("read_kirpma",
                      r.ok is True and "kırpıldı" in r.data and len(r.data) <= (F._MAX_READ + 200)))
        # ikili (binary) koruması: null byte'lı içerik → çöp döndürme, açık hata
        binf = tmp / "ikili.bin"
        binf.write_bytes(b"PK\x03\x04\x00\x00garbage\x00\xff\xfe")
        r = F.read_file(str(binf))
        cases.append(("read_ikili_dosya",
                      r.ok is False and r.error == "ikili_dosya" and "ikili" in str(r)))
        # null byte 8KB'den SONRA ise metin sayılır (sadece ilk blok koklanır)
        latebin = tmp / "gec_null.txt"
        latebin.write_bytes(b"A" * (F._BINARY_SNIFF + 100) + b"\x00son")
        r = F.read_file(str(latebin))
        cases.append(("read_gec_null_metin", r.ok is True and r.error is None))
        # okuma güvenlik sınırı (home dışı) — yazma ile simetrik
        r = F.read_file(str(outside))
        cases.append(("read_guvenlik", r.ok is False and r.error == "guvenlik_siniri"))

        # ── append_file ──────────────────────────────────────────────────────
        r = F.append_file(str(f1), " dünya")
        cases.append(("append_ok",
                      r.ok is True and f1.read_text(encoding="utf-8") == "merhaba dünya"))
        r = F.append_file(str(outside), "x")
        cases.append(("append_guvenlik", r.ok is False and r.error == "guvenlik_siniri"))

        # ── list_directory ───────────────────────────────────────────────────
        r = F.list_directory(str(tmp))
        cases.append(("list_ok",
                      r.ok is True and isinstance(r.data, list) and len(r.data) >= 2))
        cases.append(("list_data_sema",
                      all({"name", "is_dir", "size_bytes"} <= set(it.keys()) for it in r.data)))
        r = F.list_directory(str(tmp / "yok_klasor"))
        cases.append(("list_bulunamadi", r.ok is False and r.error == "dosya_bulunamadi"))
        r = F.list_directory(str(f1))  # dosya → klasör değil
        cases.append(("list_klasor_degil", r.ok is False and r.error == "klasor_degil"))
        r = F.list_directory(str(home.parent))  # home dışı (ör. C:\Users)
        cases.append(("list_guvenlik", r.ok is False and r.error == "guvenlik_siniri"))

        # ── file_info ────────────────────────────────────────────────────────
        r = F.file_info(str(f1))
        cases.append(("info_ok",
                      r.ok is True and isinstance(r.data, dict)
                      and r.data["is_dir"] is False and r.data["size_bytes"] > 0
                      and "modified" in r.data))
        r = F.file_info(str(tmp / "yok.txt"))
        cases.append(("info_bulunamadi", r.ok is False and r.error == "dosya_bulunamadi"))
        # home dışı → varlık kontrolünden ÖNCE güvenlik sınırı (var/yok sızmaz)
        r = F.file_info(str(outside))
        cases.append(("info_guvenlik", r.ok is False and r.error == "guvenlik_siniri"))

        # ── create_directory ─────────────────────────────────────────────────
        d1 = tmp / "yeni_klasor" / "alt"
        r = F.create_directory(str(d1))
        cases.append(("mkdir_ok", r.ok is True and d1.is_dir()))
        r = F.create_directory(str(outside.parent / "klargus_disari_klasor"))
        cases.append(("mkdir_guvenlik", r.ok is False and r.error == "guvenlik_siniri"))

        # ── delete_file ──────────────────────────────────────────────────────
        r = F.delete_file(str(f1))
        cases.append(("delete_ok", r.ok is True and not f1.exists() and "silindi" in str(r)))
        r = F.delete_file(str(tmp / "yok.txt"))
        cases.append(("delete_bulunamadi", r.ok is False and r.error == "dosya_bulunamadi"))
        r = F.delete_file(str(tmp / "yeni_klasor"))  # klasör → silinmez
        cases.append(("delete_klasor_silinmez",
                      r.ok is False and r.error == "klasor_silinmez" and (tmp / "yeni_klasor").is_dir()))
        r = F.delete_file(str(outside))
        cases.append(("delete_guvenlik", r.ok is False and r.error == "guvenlik_siniri"))

        # ── ToolResult sözleşmesi: message == str değeri (str alt sınıfı) ─────
        r = F.create_file(str(tmp / "sozlesme.txt"))
        cases.append(("toolresult_str_alt_sinifi",
                      isinstance(r, str) and str(r) == r.message))

    finally:
        # temizlik — home altındaki geçici test klasörünü kaldır
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        try:
            if outside.exists():
                outside.unlink()
        except Exception:
            pass

    return cases


def main() -> int:
    print("=" * 60)
    print("FILE_TOOLS BİRİM TESTLERİ")
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
    print("-" * 60)
    if fails:
        print(f"SONUÇ: {len(fails)} başarısız — {', '.join(fails)}")
        return 1
    print(f"SONUÇ: {len(cases)} testin TAMAMI GEÇTİ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
