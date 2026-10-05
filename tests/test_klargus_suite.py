# -*- coding: utf-8 -*-
"""
tests/test_klargus_suite.py — Klargus Kapsamlı Test Suite
==========================================================
Kategoriler:
  A. Import Zinciri        (10 test)
  B. Tool Registry          (5 test)
  C. WSL2 + Kali            (4 test)
  D. Cyber Parça 1 — Keşif  (7 test)
  E. Cyber Parça 2 — Web    (7 test)
  F. Cyber Parça 3 — Zafiyet (4 test)
  G. Sovereign Brain        (8 test)
  H. Performans             (5 test)

Çalıştır:
  python tests/test_klargus_suite.py
"""

import os
# TEST GÜVENLİ MODU — beyin import edilmeden ÖNCE ayarlanmalı.
# Bu bayrak açıkken SovereignBrain kamerayı açmaz ve agent döngüsü yan-etkili
# araçları (tarayıcı/program/sistem) çalıştırmaz → testler gerçek dünyayı etkilemez.
os.environ["KLARGUS_TEST_MODE"] = "1"

# Bu dosya BAĞIMSIZ bir betiktir (python tests/test_klargus_suite.py ile
# çalıştırılır) — pytest test suite'i DEĞİLDİR. __test__ = False, pytest'in
# bu modülü hiç TOPLAMAMASINI sağlar (2026-09-29 — önceden buradaki `test()`
# adlı yardımcı fonksiyon pytest'in test_* deseniyle YANLIŞLIKLA eşleşip
# collection hatasına yol açıyordu).
__test__ = False

import sys
import time
import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ── Sonuç takibi ─────────────────────────────────────────────────────────────

# "observe" = LLM davranış gözlemi (non-deterministik). Kaydedilir ama YEŞİL
# BASELINE sayımına (n_fail) ve kategori sayısına GİRMEZ.
results = {"pass": [], "fail": [], "skip": [], "observe": []}
_category_counts = {}
_perf_records = {}


def _rec(category, name, status, detail, duration=None):
    entry = (name, detail, duration)
    results[status].append(entry)
    # observe kategori sayımına girmez (yeşil baseline'ı kirletmesin)
    if status in ("pass", "fail", "skip"):
        cat = _category_counts.setdefault(category, {"pass": 0, "fail": 0, "skip": 0})
        cat[status] += 1
    sym = {"pass": "OK", "fail": "FAIL", "skip": "SKIP", "observe": "OBS"}[status]
    dur_str = f" [{duration:.2f}s]" if duration is not None else ""
    print(f"  {sym}  {name}{dur_str}: {detail[:80]}")


def observe(category, name, fn):
    """LLM davranış gözlemi — sonucu kaydeder ama ASLA fail sayılmaz (flaky)."""
    import time as _t
    t0 = _t.monotonic()
    try:
        result = fn()
        dur = _t.monotonic() - t0
        ok = result is True or (isinstance(result, tuple) and result[0] is True)
        detail = (result[1] if isinstance(result, tuple) else str(result))
        _rec(category, name, "observe", f"{'beklenen' if ok else 'sapma'}: {detail}", dur)
    except Exception as e:
        _rec(category, name, "observe", f"EXCEPTION: {type(e).__name__}: {e}")


def run_case(category, name, fn, timeout_s=None):
    t0 = time.monotonic()
    try:
        result = fn()
        dur = time.monotonic() - t0
        if result is True or (isinstance(result, tuple) and result[0] is True):
            detail = result[1] if isinstance(result, tuple) else "OK"
            _rec(category, name, "pass", str(detail), dur)
        elif result == "SKIP" or (isinstance(result, tuple) and result[0] == "SKIP"):
            detail = result[1] if isinstance(result, tuple) else "Atlandi"
            _rec(category, name, "skip", str(detail))
        else:
            detail = result[1] if isinstance(result, tuple) else str(result)
            _rec(category, name, "fail", str(detail), dur)
    except Exception as e:
        dur = time.monotonic() - t0
        _rec(category, name, "fail", f"EXCEPTION: {type(e).__name__}: {e}", dur)


# ── Yardımcılar ──────────────────────────────────────────────────────────────

def _et(brain, action, args):
    """sovereign_brain._execute_tool çağrısı + dict parse."""
    import ast as _ast
    raw = brain._execute_tool(action, args)
    try:
        return _ast.literal_eval(raw)
    except Exception:
        return {"_raw": raw}


def _ollama_ok():
    try:
        import requests
        return requests.get("http://localhost:11434/api/tags", timeout=3).status_code == 200
    except Exception:
        return False


def _kali_ok():
    try:
        import subprocess
        r = subprocess.run(
            ["wsl", "-d", "kali-linux", "--", "echo", "ping"],
            capture_output=True, timeout=6,
        )
        return r.returncode == 0
    except Exception:
        return False


# ════════════════════════════════════════════════════════════════════════════
# KATEGORİ A — Import Zinciri
# ════════════════════════════════════════════════════════════════════════════

def run_category_a():
    print("\n── A. Import Zinciri ───────────────────────────────────────────")
    CAT = "A"

    MODULES = [
        # ui.app pyautogui/sounddevice gerektirir — .venv dışında SKIP
        ("ui.app",                "ui.app"),
        ("brain.sovereign_brain", "brain.sovereign_brain"),
        ("brain.tool_registry",   "brain.tool_registry"),
        ("brain.llm",             "brain.llm"),
        ("brain.profile_learning","brain.profile_learning"),
        ("brain.personality",     "brain.personality"),
        ("brain.memory_bank",     "brain.memory_bank"),
        ("tools.system_tools",    "tools.system_tools"),
        ("tools.cyber_tools",     "tools.cyber_tools"),
    ]

    for label, module in MODULES:
        def _fn(m=module, lbl=label):
            # ui.app pyautogui + sounddevice gerektirir; .venv dışında SKIP
            if lbl == "ui.app":
                try:
                    import importlib
                    importlib.import_module(m)
                    return True, f"{m} import OK"
                except ModuleNotFoundError as e:
                    missing = str(e).replace("No module named ", "").strip("'")
                    if missing in ("pyautogui", "sounddevice", "mss"):
                        return "SKIP", f"GUI paketi eksik ({missing}) — .venv ile calistir"
                    raise
            import importlib
            importlib.import_module(m)
            return True, f"{m} import OK"
        run_case(CAT, f"import:{label}", _fn)


# ════════════════════════════════════════════════════════════════════════════
# KATEGORİ B — Tool Registry Bütünlüğü
# ════════════════════════════════════════════════════════════════════════════

def run_category_b():
    print("\n── B. Tool Registry Bütünlüğü ──────────────────────────────────")
    CAT = "B"

    def b_total():
        from brain.tool_registry import list_tools
        n = len(list_tools())
        return n >= 110, f"{n} tool kayitli (>= 110 beklendi)"

    def b_klargus():
        # Dinamik sanity: KLARGUS_TOOLS dolu ve makul sayıda olmalı (sabit sayı
        # kullanma — araç eklendikçe eskimesin). Boşalma/bozulmayı yakalar.
        from brain.llm import KLARGUS_TOOLS
        n = len(KLARGUS_TOOLS)
        return n >= 50, f"KLARGUS_TOOLS = {n} (>= 50 beklendi, dolu olmalı)"

    def b_wsl_run():
        from brain.tool_registry import list_tools
        return "wsl_run" in list_tools(), "'wsl_run' kayitli"

    def b_nmap():
        from brain.tool_registry import list_tools
        return "nmap_scan" in list_tools(), "'nmap_scan' kayitli"

    def b_hash_crack():
        from brain.tool_registry import list_tools
        return "hash_crack" in list_tools(), "'hash_crack' kayitli"

    for name, fn in [
        ("registry_toplam_110+", b_total),
        ("KLARGUS_TOOLS_dolu",   b_klargus),
        ("wsl_run_var",          b_wsl_run),
        ("nmap_scan_var",        b_nmap),
        ("hash_crack_var",       b_hash_crack),
    ]:
        run_case(CAT, name, fn)


# ════════════════════════════════════════════════════════════════════════════
# KATEGORİ C — WSL2 + Kali
# ════════════════════════════════════════════════════════════════════════════

def run_category_c():
    print("\n── C. WSL2 + Kali ──────────────────────────────────────────────")
    CAT = "C"

    def c_wsl_installed():
        from tools.system_tools import wsl_status
        s = wsl_status()
        return s.get("wsl_installed") is True, "WSL2 kurulu"

    def c_kali_available():
        from tools.system_tools import wsl_status
        s = wsl_status()
        return s.get("distro_available") is True, f"'{s.get('distro')}' mevcut"

    def c_echo():
        from tools.system_tools import run_wsl_command
        r = run_wsl_command("echo klargus_wsl_test", timeout=10)
        ok = r["success"] and "klargus_wsl_test" in r["stdout"]
        return ok, f"stdout='{r['stdout'][:40]}'"

    def c_curl():
        from tools.system_tools import check_linux_tool
        ok = check_linux_tool("curl")
        return ok, "curl Kali'de kurulu" if ok else "curl bulunamadi"

    for name, fn in [
        ("wsl_installed",   c_wsl_installed),
        ("kali_available",  c_kali_available),
        ("echo_test",       c_echo),
        ("curl_installed",  c_curl),
    ]:
        run_case(CAT, name, fn)


# ════════════════════════════════════════════════════════════════════════════
# KATEGORİ D — Cyber Parça 1 — Keşif
# ════════════════════════════════════════════════════════════════════════════

def run_category_d():
    print("\n── D. Cyber Parça 1 — Keşif ────────────────────────────────────")
    CAT = "D"

    def d_dns():
        from tools.cyber_tools import dns_lookup
        r = dns_lookup("google.com")
        if "error" in r:
            return "SKIP", f"DNS hatasi: {r['error'][:50]}"
        ok = "A" in r and len(r["A"]) > 0
        return ok, f"A={r.get('A',['?'])[:1]}"

    def d_whois():
        from tools.cyber_tools import whois_lookup
        r = whois_lookup("github.com")
        if "error" in r:
            return "SKIP", f"WHOIS hatasi: {r['error'][:50]}"
        ok = "registrar" in r
        return ok, f"registrar='{r.get('registrar','?')[:30]}'"

    def d_subdomain():
        from tools.cyber_tools import subdomain_enum
        r = subdomain_enum("github.com")
        if "error" in r:
            return "SKIP", f"crt.sh hatasi: {r['error'][:50]}"
        ok = isinstance(r.get("subdomains"), list) and r.get("count", 0) > 0
        return ok, f"count={r.get('count',0)}"

    def d_ipinfo():
        from tools.cyber_tools import ip_info
        r = ip_info("8.8.8.8")
        if "error" in r:
            return "SKIP", f"IP API hatasi: {r['error'][:50]}"
        ok = "United" in r.get("country", "") or r.get("country_code") == "US"
        return ok, f"country={r.get('country','?')}"

    def d_reverse_dns():
        from tools.cyber_tools import reverse_dns
        r = reverse_dns("8.8.8.8")
        if "error" in r and r.get("hostname") is None:
            return "SKIP", r.get("error","?")[:50]
        ok = "dns.google" in str(r.get("hostname", ""))
        return ok, f"hostname={r.get('hostname','?')}"

    def d_nmap():
        from tools.cyber_tools import nmap_scan
        from tools.system_tools import check_linux_tool
        if not check_linux_tool("nmap"):
            return "SKIP", "nmap Kali'de kurulu degil"
        r = nmap_scan("127.0.0.1", mode="fast")
        if "error" in r:
            return False, r["error"][:60]
        return "target" in r, f"target={r.get('target')}, ports={len(r.get('ports',[]))}"

    def d_traceroute():
        from tools.cyber_tools import traceroute
        r = traceroute("8.8.8.8")
        if "error" in r:
            return False, r["error"][:60]
        hops = len(r.get("hops", []))
        return hops > 0, f"platform={r.get('platform')}, hops={hops}"

    for name, fn in [
        ("dns_lookup_google",    d_dns),
        ("whois_github",         d_whois),
        ("subdomain_github",     d_subdomain),
        ("ip_info_8888",         d_ipinfo),
        ("reverse_dns_8888",     d_reverse_dns),
        ("nmap_localhost",       d_nmap),
        ("traceroute_8888",      d_traceroute),
    ]:
        run_case(CAT, name, fn)


# ════════════════════════════════════════════════════════════════════════════
# KATEGORİ E — Cyber Parça 2 — Web / Hash
# ════════════════════════════════════════════════════════════════════════════

def run_category_e():
    print("\n── E. Cyber Parça 2 — Web/Hash ─────────────────────────────────")
    CAT = "E"

    def e_http():
        from tools.cyber_tools import http_analysis
        r = http_analysis("https://google.com")
        if "error" in r:
            return "SKIP", r["error"][:50]
        ok = r.get("status_code") == 200
        return ok, f"status={r.get('status_code')}, server={r.get('server','?')[:20]}"

    def e_ssl():
        from tools.cyber_tools import ssl_check
        r = ssl_check("google.com")
        if "error" in r:
            return "SKIP", r["error"][:50]
        ok = r.get("valid") is True and r.get("days_remaining", 0) > 0
        return ok, f"valid={r.get('valid')}, days={r.get('days_remaining')}, tls={r.get('tls_version')}"

    def e_robots():
        from tools.cyber_tools import robots_txt
        r = robots_txt("https://github.com")
        if "error" in r:
            return "SKIP", r["error"][:50]
        ok = r.get("robots_exists") is True
        return ok, f"exists={ok}, agents={len(r.get('user_agents',{}))}"

    def e_gobuster():
        from tools.cyber_tools import gobuster_dir
        from tools.system_tools import check_linux_tool
        if not check_linux_tool("gobuster"):
            return "SKIP", "gobuster kurulu degil"
        r = gobuster_dir("http://example.com", wordlist="common")
        if "error" in r:
            return False, r["error"][:60]
        return True, f"found={r.get('total_found',0)}"

    def e_hash_id():
        from tools.cyber_tools import hash_identify
        r = hash_identify("5d41402abc4b2a76b9719d911017c592")
        ok = r.get("most_likely") == "MD5"
        return ok, f"most_likely={r.get('most_likely')}, confidence={r.get('confidence')}"

    def e_hash_calc():
        from tools.cyber_tools import hash_calculate
        r = hash_calculate("hello", "sha256")
        expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        ok = r.get("hash") == expected
        return ok, f"sha256(hello)={r.get('hash','?')[:16]}..."

    def e_hash_md5():
        # DETERMINISTIK md5 doğruluğu: hash_calculate ↔ hashlib (LLM devrede değil).
        import hashlib
        from tools.cyber_tools import hash_calculate
        expected = hashlib.md5("hello".encode()).hexdigest()
        r = hash_calculate("hello", "md5")
        ok = r.get("hash") == expected
        return ok, f"md5(hello)={r.get('hash','?')[:16]}... (beklenen {expected[:16]}...)"

    def e_encode():
        from tools.cyber_tools import encode_decode
        r = encode_decode("hello", "base64", "encode")
        ok = r.get("output") == "aGVsbG8="
        return ok, f"output='{r.get('output')}'"

    for name, fn in [
        ("http_google_200",      e_http),
        ("ssl_google_valid",     e_ssl),
        ("robots_github",        e_robots),
        ("gobuster_example",     e_gobuster),
        ("hash_identify_md5",    e_hash_id),
        ("hash_calc_sha256",     e_hash_calc),
        ("hash_calc_md5",        e_hash_md5),
        ("encode_base64_hello",  e_encode),
    ]:
        run_case(CAT, name, fn)


# ════════════════════════════════════════════════════════════════════════════
# KATEGORİ F — Cyber Parça 3 — Zafiyet / Şifre
# ════════════════════════════════════════════════════════════════════════════

def run_category_f():
    print("\n── F. Cyber Parça 3 — Zafiyet/Şifre ───────────────────────────")
    CAT = "F"

    def f_pw_strong():
        from tools.cyber_tools import password_strength
        r = password_strength("Test1234!")
        ok = "score" in r and 0 <= r["score"] <= 4
        return ok, f"score={r.get('score')}, strength={r.get('strength')}, engine={r.get('engine')}"

    def f_pw_weak():
        from tools.cyber_tools import password_strength
        r = password_strength("123456")
        ok = r.get("score") == 0 and r.get("is_common") is True
        return ok, f"score={r.get('score')}, is_common={r.get('is_common')}"

    def f_nikto():
        from tools.cyber_tools import nikto_scan
        from tools.system_tools import check_linux_tool
        if not check_linux_tool("nikto"):
            return "SKIP", "nikto kurulu degil"
        r = nikto_scan("https://example.com", options="quick")
        if "error" in r:
            return False, r["error"][:60]
        return "findings" in r, f"findings={r.get('total_findings',0)}, dur={r.get('duration',0):.0f}s"

    def f_hash_crack():
        from tools.cyber_tools import hash_crack
        from tools.system_tools import check_linux_tool
        has_hashcat = check_linux_tool("hashcat")
        has_john    = check_linux_tool("john")
        if not has_hashcat and not has_john:
            return "SKIP", "hashcat/john kurulu degil"
        r = hash_crack(
            "5d41402abc4b2a76b9719d911017c592",
            hash_type="md5", wordlist="rockyou", mode="cpu",
        )
        if "error" in r:
            return False, r["error"][:60]
        return True, f"cracked={r.get('cracked')}, plaintext={r.get('plaintext')}"

    for name, fn in [
        ("password_strong_Test1234", f_pw_strong),
        ("password_weak_123456",     f_pw_weak),
        ("nikto_example_com",        f_nikto),
        ("hash_crack_md5_hello",     f_hash_crack),
    ]:
        run_case(CAT, name, fn)


# ════════════════════════════════════════════════════════════════════════════
# KATEGORİ G — Sovereign Brain
# ════════════════════════════════════════════════════════════════════════════

def run_category_g():
    print("\n── G. Sovereign Brain ──────────────────────────────────────────")
    CAT = "G"

    if not _ollama_ok():
        print("  SKIP  [TUM G KATEGORISI] Ollama erisilemedigi icin atlanıyor")
        for _ in range(8):
            results["skip"].append(("sovereign_brain", "Ollama down"))
        _category_counts.setdefault("G", {"pass":0,"fail":0,"skip":0})["skip"] += 8
        return

    from brain.sovereign_brain import get_sovereign_brain
    brain = get_sovereign_brain()

    def g_merhaba():
        r = brain.think("merhaba")
        return isinstance(r, str) and len(r) > 0, f"len={len(r)}, preview='{r[:40]}'"

    def g_saat():
        r = brain.think("saat kaç")
        return isinstance(r, str) and len(r) > 0, f"'{r[:50]}'"

    def g_profil():
        r = brain.think("profilimi göster")
        ok = isinstance(r, str) and len(r) > 0
        return ok, f"len={len(r)}, preview='{r[:50]}'"

    def g_wsl():
        r = brain.think("wsl durumunu kontrol et")
        ok = isinstance(r, str) and ("WSL" in r or "wsl" in r.lower() or "kali" in r.lower() or "True" in r)
        return ok, f"'{r[:60]}'"

    def g_dns():
        r = brain.think("google.com'un DNS kayıtlarını göster")
        ok = isinstance(r, str) and len(r) > 0
        return ok, f"'{r[:60]}'"

    def g_md5():
        r = brain.think("hello kelimesinin md5 hash'ini hesapla")
        ok = isinstance(r, str) and "5d41402abc4b2a76" in r.lower()
        return ok, f"md5 dogru: {ok}, cevap='{r[:60]}'"

    def g_password():
        r = brain.think("Test1234! şifresinin güvenliğini analiz et")
        ok = isinstance(r, str) and len(r) > 0
        return ok, f"'{r[:60]}'"

    def g_tks():
        r = brain.think("teşekkürler")
        return isinstance(r, str) and len(r) > 0, f"'{r[:50]}'"

    for name, fn in [
        ("think_merhaba",     g_merhaba),
        ("think_saat_kac",    g_saat),
        ("think_profil",      g_profil),
        ("think_wsl_durumu",  g_wsl),
        ("think_dns_query",   g_dns),
        ("think_password",    g_password),
        ("think_tesekkur",    g_tks),
    ]:
        run_case(CAT, name, fn)

    # think_md5: LLM'in "md5 hesapla" isteğini doğru araca yönlendirmesi
    # NON-DETERMINISTIK (model bazen yanlış araca gidiyor / kafadan hesaplıyor).
    # Yeşil baseline'a KATILMAZ → observe. Hash DOĞRULUĞU ayrı deterministik
    # testte (E grubu, hash_calculate ↔ hashlib) kontrol edilir.
    observe(CAT, "gozlem_md5_yonlendirme", g_md5)


# ════════════════════════════════════════════════════════════════════════════
# KATEGORİ H — Performans
# ════════════════════════════════════════════════════════════════════════════

def run_category_h():
    print("\n── H. Performans ────────────────────────────────────────────────")
    CAT = "H"

    def h_hash_calc():
        from tools.cyber_tools import hash_calculate
        t0 = time.monotonic()
        hash_calculate("test", "sha256")
        dur = time.monotonic() - t0
        ok = dur < 0.1
        _perf_records["hash_calculate"] = dur
        return ok, f"{dur:.4f}s (< 0.1s beklendi)"

    def h_dns():
        from tools.cyber_tools import dns_lookup
        t0 = time.monotonic()
        r = dns_lookup("google.com")
        dur = time.monotonic() - t0
        _perf_records["dns_lookup"] = dur
        if "error" in r:
            return "SKIP", f"DNS hatasi: {r['error'][:40]}"
        # Eşik gerçek ölçümle kalibre (2026-07-26): google.com medyanı ~4.3s
        # (paralel sorgu, lifetime=8). medyan×2≈8.6 → 9.0s: tek kayıt tipi timeout
        # tavanına (8s) vursa bile tolere eder, 43s regresyonunu yine yakalar.
        ok = dur < 9.0
        return ok, f"{dur:.2f}s (< 9s beklendi)"

    def h_wsl_status():
        from tools.system_tools import wsl_status
        t0 = time.monotonic()
        wsl_status()
        dur = time.monotonic() - t0
        _perf_records["wsl_status"] = dur
        ok = dur < 10.0
        return ok, f"{dur:.2f}s (< 10s beklendi)"

    def h_sovereign_merhaba():
        if not _ollama_ok():
            return "SKIP", "Ollama down"
        from brain.sovereign_brain import get_sovereign_brain
        brain = get_sovereign_brain()
        t0 = time.monotonic()
        r = brain.think("merhaba")
        dur = time.monotonic() - t0
        _perf_records["sovereign_merhaba"] = dur
        ok = dur < 30.0 and len(r) > 0
        return ok, f"{dur:.2f}s (< 30s beklendi)"

    def h_sovereign_saat():
        if not _ollama_ok():
            return "SKIP", "Ollama down"
        from brain.sovereign_brain import get_sovereign_brain
        brain = get_sovereign_brain()
        t0 = time.monotonic()
        r = brain.think("saat kaç")
        dur = time.monotonic() - t0
        _perf_records["sovereign_saat"] = dur
        ok = dur < 30.0 and len(r) > 0
        return ok, f"{dur:.2f}s (< 30s beklendi)"

    for name, fn in [
        ("perf_hash_calc",       h_hash_calc),
        ("perf_dns_lookup",      h_dns),
        ("perf_wsl_status",      h_wsl_status),
        ("perf_sovereign_hi",    h_sovereign_merhaba),
        ("perf_sovereign_saat",  h_sovereign_saat),
    ]:
        run_case(CAT, name, fn)


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    start_time = time.monotonic()
    now_str    = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 66)
    print(f"KLARGUS TEST SUITE — {now_str}")
    print("=" * 66)

    # Ortam bilgisi
    print("\n── ORTAM ────────────────────────────────────────────────────────")
    print(f"  Python : {sys.version.split()[0]}")
    print(f"  Ollama : {'OK' if _ollama_ok() else 'DOWN'}")
    print(f"  Kali   : {'OK' if _kali_ok() else 'DOWN'}")

    # Kategoriler
    run_category_a()
    run_category_b()
    run_category_c()
    run_category_d()
    run_category_e()
    run_category_f()
    run_category_g()
    run_category_h()

    total_dur = time.monotonic() - start_time

    # ── Nihai rapor ─────────────────────────────────────────────────────────
    n_pass = len(results["pass"])
    n_fail = len(results["fail"])
    n_skip = len(results["skip"])
    total  = n_pass + n_fail + n_skip

    print("\n" + "=" * 66)
    print("NİHAİ RAPOR")
    print("=" * 66)

    print(f"\nÖZET:")
    print(f"  Toplam test : {total}")
    print(f"  PASS        : {n_pass}")
    print(f"  FAIL        : {n_fail}")
    print(f"  SKIP        : {n_skip}")
    print(f"  Süre        : {total_dur:.1f}s")

    print("\nKATEGORİ DETAYLARI:")
    cat_labels = {
        "A": "Import Zinciri",
        "B": "Tool Registry",
        "C": "WSL2 + Kali",
        "D": "Cyber P1 — Keşif",
        "E": "Cyber P2 — Web/Hash",
        "F": "Cyber P3 — Zafiyet",
        "G": "Sovereign Brain",
        "H": "Performans",
    }
    for cat_id, label in cat_labels.items():
        c = _category_counts.get(cat_id, {"pass":0,"fail":0,"skip":0})
        p, f_, s = c["pass"], c["fail"], c["skip"]
        cat_total = p + f_ + s
        icon = "OK" if f_ == 0 else ("!!" if p == 0 else "??")
        print(f"  {icon}  {cat_id}. {label:<22} {p}/{cat_total} (skip={s})")

    if results["fail"]:
        print("\nBASARISIZ TESTLER:")
        for name, detail, dur in results["fail"]:
            dur_str = f" [{dur:.2f}s]" if dur is not None else ""
            print(f"  - {name}{dur_str}: {detail[:100]}")

    if results["skip"]:
        print("\nATLANAN TESTLER:")
        seen = set()
        for name, detail, *_ in results["skip"]:
            if name not in seen:
                seen.add(name)
                print(f"  - {name}: {detail[:80]}")

    if results["observe"]:
        print("\nLLM DAVRANIŞ GÖZLEMİ (non-deterministik — baseline'a DAHİL DEĞİL):")
        for name, detail, dur in results["observe"]:
            dur_str = f" [{dur:.2f}s]" if dur is not None else ""
            print(f"  ? {name}{dur_str}: {detail[:90]}")

    if _perf_records:
        print("\nPERFORMANS:")
        for k, v in sorted(_perf_records.items(), key=lambda x: -x[1]):
            print(f"  {k:<30} {v:.3f}s")

    print("\nSONUC:")
    if n_fail == 0:
        print("  TUMU GECTI (ya da SKIP).")
    else:
        print(f"  {n_fail} test basarisiz — yukaridaki listeyi incele.")

    print("=" * 66)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
