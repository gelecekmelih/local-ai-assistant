# -*- coding: utf-8 -*-
"""
tests/test_security_monitor.py — Güvenlik monitörü karakterizasyon testleri (Faz 5E)

Amaç: 18a4e0c ile silinip 5B'de restore+modernize edilen security_monitor'ün
davranışını KİLİTLEMEK. Test "geçsin diye" kod değiştirilmez — davranış 5B-5D'de
bitti; bu testler onu dondurur.

Kapsam:
  1) find_suspicious_processes — mimikatz/temp-exe işaretlenir, normal süreç işaretlenmez.
  2) analyze_network — dış/established sayımı + şüpheli port tespiti.
  3) run_security_check(short/standard/detailed) — mock'lu tam pipeline, mod uzunlukları.
  4) SAFE-MODE DİSK KORUMASI — güvenli modda state/last_report diske YAZILMAZ (guard kilidi).
  5) security_check_tool — ToolResult (.ok/.message/.data).
  6) Deterministik route (5D) — net komut yakalanır, belirsiz sohbet LLM'e düşer.

Yan etki YOK: psutil/socket sahtelenir (gerçek süreç/ağ okunmaz, DNS sorgulanmaz),
disk yazımı safe_mode + tmp yönlendirme ile gerçek data/'ya DOKUNMAZ.

Çalıştırma:  python tests/test_security_monitor.py
"""

# Bu dosya BAĞIMSIZ bir betiktir (python tests/test_security_monitor.py ile çalıştırılır) — pytest
# test suite'i DEĞİLDİR. __test__ = False, pytest'in bu modülü hiç
# TOPLAMAMASINI sağlar (2026-09-29 — bkz. test_klargus_suite.py'deki
# collection hatası kök-neden düzeltmesi).
__test__ = False

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Testler güvenli modda çalışır — gerçek data/ dosyaları yazılmaz (self-contained).
os.environ["KLARGUS_TEST_MODE"] = "1"


def _cases():
    import security.security_monitor as SM
    from tests._mock_helpers import (
        FakeProc, FakeConn, make_fake_psutil, make_fake_socket, patch_attrs,
    )
    from brain.tool_result import ToolResult
    from core.safe_mode import is_safe_mode

    cases = []

    # ── Kurgu süreçler (list_processes çıktısı biçiminde dict'ler) ─────────────
    normal_chrome = {
        "name": "chrome.exe",
        "exe": "c:\\program files\\google\\chrome\\application\\chrome.exe",
        "cmdline": "chrome --type=renderer",
    }
    evil_mimikatz = {
        "name": "mimikatz.exe",
        "exe": "c:\\users\\x\\downloads\\mimikatz.exe",
        "cmdline": "mimikatz sekurlsa::logonpasswords",
    }
    temp_exe = {
        "name": "payload.exe",
        "exe": "c:\\users\\x\\appdata\\local\\temp\\payload.exe",
        "cmdline": "payload.exe",
    }
    procs = [normal_chrome, evil_mimikatz, temp_exe]

    # ── 1) find_suspicious_processes ──────────────────────────────────────────
    findings = SM.find_suspicious_processes(procs)
    names = [f["name"] for f in findings]
    cases.append(("susp_mimikatz_isaretli",   "mimikatz.exe" in names))
    cases.append(("susp_temp_exe_isaretli",   "payload.exe" in names))
    cases.append(("susp_normal_isaretsiz",    "chrome.exe" not in names))
    # skor sıralaması deterministik: yüksek-riskli mimikatz ilk sırada + "yüksek" seviye
    cases.append(("susp_siralama_det",        len(findings) >= 1 and findings[0]["name"] == "mimikatz.exe"))
    cases.append(("susp_mimikatz_yuksek",     findings[0]["severity"] == "yüksek"))

    # ── 2) analyze_network (list_network_connections çıktısı biçimi) ──────────
    conns = [
        {"remote_ip": "8.8.8.8", "remote_scope": "public", "status": "ESTABLISHED",
         "remote_port": 443, "process": "chrome.exe", "pid": 1000},
        {"remote_ip": "9.9.9.9", "remote_scope": "public", "status": "ESTABLISHED",
         "remote_port": 4444, "process": "payload.exe", "pid": 6666},
        {"remote_ip": "", "remote_scope": "", "status": "LISTEN",
         "remote_port": 0, "process": "svchost.exe", "pid": 4},
    ]
    net = SM.analyze_network(conns)
    cases.append(("net_dis_sayimi",       net["external"] == 2))
    cases.append(("net_established",      net["established"] == 2))
    cases.append(("net_toplam",           net["total"] == 3))
    cases.append(("net_supheli_port",     len(net["suspicious_ports"]) == 1))
    cases.append(("net_supheli_port_4444",
                  net["suspicious_ports"][0]["remote_port"] == 4444))

    # ── 3) run_security_check — mock'lu tam pipeline + mod uzunlukları ────────
    fake_procs = [
        FakeProc({"pid": 1000, "ppid": 1, "name": "chrome.exe",
                  "exe": "c:\\program files\\google\\chrome\\application\\chrome.exe",
                  "cmdline": ["chrome"], "username": "u", "create_time": 1.0, "status": "running"}),
        FakeProc({"pid": 6666, "ppid": 1, "name": "mimikatz.exe",
                  "exe": "c:\\users\\x\\downloads\\mimikatz.exe",
                  "cmdline": ["mimikatz"], "username": "u", "create_time": 2.0, "status": "running"}),
    ]
    fake_conns = [
        FakeConn(laddr=("127.0.0.1", 5000), raddr=("8.8.8.8", 443), status="ESTABLISHED", pid=1000),
        FakeConn(laddr=("127.0.0.1", 5001), raddr=("9.9.9.9", 4444), status="ESTABLISHED", pid=6666),
    ]
    with patch_attrs(SM, psutil=make_fake_psutil(fake_procs, fake_conns),
                     socket=make_fake_socket()):
        r_short = SM.run_security_check("short")
        r_std = SM.run_security_check("standard")
        r_det = SM.run_security_check("detailed")

    short_n = len(r_short.splitlines())
    std_n = len(r_std.splitlines())
    det_n = len(r_det.splitlines())
    cases.append(("rsc_short_str",        isinstance(r_short, str) and "Risk seviyesi" in r_short))
    cases.append(("rsc_standard_mimikatz", "mimikatz.exe" in r_std))
    cases.append(("rsc_supheli_port_raporda", "9.9.9.9:4444" in r_std or "4444" in r_std))
    cases.append(("rsc_short_kisadir",    short_n < std_n))
    cases.append(("rsc_detayli_uzun",     det_n >= std_n))
    cases.append(("rsc_risk_yuksek",      "Risk seviyesi: yüksek" in r_std))  # mimikatz → yüksek

    # ── 4) SAFE-MODE DİSK KORUMASI (guard kilidi) ─────────────────────────────
    cases.append(("safe_mode_acik",       is_safe_mode() is True))

    # 4a) Güvenli modda _update_state, _save_state'i ÇAĞIRMAZ (yazma engellenir).
    _save_calls = {"n": 0}
    def _spy_save_state(data):
        _save_calls["n"] += 1
    with patch_attrs(SM, _save_state=_spy_save_state):
        SM._update_state({"process_keys": ["x"], "updated_at": "now"})
    cases.append(("safe_update_state_yazmadi", _save_calls["n"] == 0))

    # 4b) NEDENSEL kanıt: aynı fonksiyon güvenli mod KAPALIYKEN tmp'ye YAZAR,
    #     AÇIKKEN yazmaz. Gerçek data/'ya dokunmadan tmp dosyalarıyla kanıtlanır.
    _tmp = Path(tempfile.mkdtemp(prefix="klargus_sec_"))
    _off = _tmp / "off_report.txt"
    _on = _tmp / "on_report.txt"
    os.environ["KLARGUS_TEST_MODE"] = "0"          # güvenli mod KAPALI
    with patch_attrs(SM, LAST_REPORT_FILE=_off):
        SM._save_last_report("kapali-mod-raporu")
    os.environ["KLARGUS_TEST_MODE"] = "1"          # tekrar AÇ
    with patch_attrs(SM, LAST_REPORT_FILE=_on):
        SM._save_last_report("acik-mod-raporu")
    cases.append(("guard_kapali_yazar",   _off.exists()))       # mod kapalı → yazdı
    cases.append(("guard_acik_yazmaz",    not _on.exists()))    # mod açık → guard engelledi

    # 4c) Gerçek data/ state dosyası pipeline sonrası DEĞİŞMEDİ (yukarıdaki
    #     run_security_check çağrıları safe modda idi — dosyaya dokunmamalı).
    real_state = SM.STATE_FILE
    _before = real_state.stat().st_mtime if real_state.exists() else None
    with patch_attrs(SM, psutil=make_fake_psutil(fake_procs, fake_conns),
                     socket=make_fake_socket()):
        SM.run_security_check("short")
    _after = real_state.stat().st_mtime if real_state.exists() else None
    cases.append(("gercek_state_degismedi", _before == _after))

    # ── 5) security_check_tool → ToolResult ───────────────────────────────────
    with patch_attrs(SM, psutil=make_fake_psutil(fake_procs, fake_conns),
                     socket=make_fake_socket()):
        tr = SM.security_check_tool("short")
    cases.append(("tool_tipi_toolresult", isinstance(tr, ToolResult)))
    cases.append(("tool_ok_true",         tr.ok is True))
    cases.append(("tool_message_dolu",    bool(tr.message) and "Risk" in tr.message))
    cases.append(("tool_data_risk",       isinstance(tr.data, dict) and "risk" in tr.data
                                          and "risk_score" in tr.data))

    # ── 6) Deterministik route (5D) — iki katman ayrımı ───────────────────────
    from brain.sovereign_brain import get_sovereign_brain
    b = get_sovereign_brain()
    with patch_attrs(SM, psutil=make_fake_psutil(fake_procs, fake_conns),
                     socket=make_fake_socket()):
        r_net = b._handle_training_command("güvenlik kontrolü yap")
        r_kisa = b._handle_training_command("kısa güvenlik raporu")
    r_amb = b._handle_training_command("acaba sistemimde tuhaf bir şey var mı")
    cases.append(("route_net_komut_yakalanir", r_net is not None and "Risk seviyesi" in r_net))
    cases.append(("route_kisa_mod",            r_kisa is not None and "Risk seviyesi" in r_kisa))
    cases.append(("route_belirsiz_llme_duser", r_amb is None))

    return cases


def main() -> int:
    print("=" * 60)
    print("GÜVENLİK MONİTÖRÜ KARAKTERİZASYON TESTLERİ")
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
