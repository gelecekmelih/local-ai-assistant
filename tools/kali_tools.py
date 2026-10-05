# -*- coding: utf-8 -*-
"""
tools/kali_tools.py — Kali Linux Güvenlik Araçları (Windows Tarafı)
====================================================================
Klargus'un Kali araçlarını sesli komutla çalıştırmasını sağlar.
Tüm komutlar WSL2 üzerinden Kali'de çalışır.

Kullanım örnekleri:
  "nmap tara 192.168.1.0/24"
  "whatweb example.com"
  "gobuster tara http://hedef.com"
  "sqlmap tara http://hedef.com/page?id=1"
  "nikto tara http://hedef.com"

⚠ Yalnızca izinli olduğun sistemlerde kullan.
"""

from tools.wsl_tools import run_kali, run_kali_bg, _wsl_path, RESULTS_DIR
from brain.tool_registry import register_tool
from brain.tool_result import ToolResult
import shlex
import time


# ── Güvenlik notu ─────────────────────────────────────────────────────────────
# Bu araçlar yalnızca sahip olduğun veya yazılı izne sahip olduğun
# sistemlerde kullanılmalıdır.


# ── Bash-injection güvenliği (kök-neden savunması) ────────────────────────────
# Komutlar `run_kali` üzerinden `bash -c "<string>"` olarak çalışır (pipe/redirect
# gerektirdiği için argv-listesi kullanılamaz). Kullanıcı hedefi (domain/IP/URL/hash)
# bu string'e GÖMÜLÜR → sanitize edilmezse `;`, `|`, `$()`, backtick gibi
# metakarakterler command injection'a yol açar. Savunma: hedefi shlex.quote ile
# escape et — metakarakterler LİTERAL olur, komut olarak yorumlanmaz. Meşru
# hedeflerde no-op (shlex.quote gereksiz tırnak eklemez).

def _q(value) -> str:
    """Tek hedefi (domain/IP/URL/hash) bash için güvenli kıl. Injection'ı yapısal
    olarak imkânsız kılar; meşru hedefte no-op."""
    return shlex.quote(str(value or ""))


def _q_tokens(value) -> str:
    """Bayrak KABUL EDEN araçlar (nmap: '192.168.1.1 -sV') için: boşlukla ayrılmış
    her token'ı ayrı escape et → meşru bayraklar korunur, injection ('; rm') quote'lanıp
    etkisiz kalır."""
    return " ".join(shlex.quote(t) for t in str(value or "").split())


def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


# ── Ağ Keşfi ─────────────────────────────────────────────────────────────────

def tool_kali_nmap(arg: str = "") -> ToolResult:
    """Nmap ile ağ/port tarama. Örnek: '192.168.1.1' veya '192.168.1.0/24 -sV'"""
    if not arg.strip():
        return ToolResult(message="Hedef IP veya aralık belirt. Örnek: nmap tara 192.168.1.1", ok=True)
    target = arg.strip()
    out_file = RESULTS_DIR / f"nmap_{_ts()}.txt"
    cmd = f"nmap -T4 {_q_tokens(target)} -oN {_wsl_path(str(out_file))} 2>&1"
    return run_kali_bg(cmd, job_name="nmap")


def tool_kali_nmap_quick(arg: str = "") -> ToolResult:
    """Hızlı nmap ping tarama (yaşayan hostlar). Örnek: '192.168.1.0/24'"""
    if not arg.strip():
        return ToolResult(message="Ağ aralığı belirt. Örnek: 192.168.1.0/24", ok=True)
    # Hızlı tarama → direkt çıktı (timeout 25s)
    result = run_kali(f"nmap -sn {_q_tokens(arg.strip())} --open 2>&1 | head -40", timeout=25)
    return result


def tool_kali_whatweb(arg: str = "") -> ToolResult:
    """WhatWeb ile web teknolojisi tespiti. Örnek: 'example.com'"""
    if not arg.strip():
        return ToolResult(message="Hedef URL belirt. Örnek: example.com", ok=True)
    target = arg.strip()
    if not target.startswith("http"):
        target = "http://" + target
    return run_kali(f"whatweb {_q(target)} 2>&1 | head -30", timeout=20)


def tool_kali_gobuster(arg: str = "") -> ToolResult:
    """Gobuster ile dizin/dosya keşfi. Örnek: 'http://hedef.com'"""
    if not arg.strip():
        return ToolResult(message="Hedef URL belirt. Örnek: http://hedef.com", ok=True)
    target = arg.strip()
    wordlist = "/usr/share/wordlists/dirb/common.txt"
    out_file  = RESULTS_DIR / f"gobuster_{_ts()}.txt"
    cmd = (
        f"gobuster dir -u {_q(target)} -w {wordlist} "
        f"-o {_wsl_path(str(out_file))} --no-progress 2>&1"
    )
    return run_kali_bg(cmd, job_name="gobuster")


def tool_kali_nikto(arg: str = "") -> ToolResult:
    """Nikto ile web sunucu güvenlik taraması. Örnek: 'http://hedef.com'"""
    if not arg.strip():
        return ToolResult(message="Hedef URL belirt. Örnek: http://hedef.com", ok=True)
    target   = arg.strip()
    out_file = RESULTS_DIR / f"nikto_{_ts()}.txt"
    cmd = f"nikto -h {_q(target)} -output {_wsl_path(str(out_file))} 2>&1"
    return run_kali_bg(cmd, job_name="nikto")


def tool_kali_sqlmap(arg: str = "") -> ToolResult:
    """SQLMap ile SQL injection testi. Örnek: 'http://hedef.com/page?id=1'"""
    if not arg.strip():
        return ToolResult(message="Hedef URL belirt (parametre içermeli). Örnek: http://hedef.com/page?id=1", ok=True)
    target   = arg.strip()
    out_file = RESULTS_DIR / f"sqlmap_{_ts()}"
    cmd = (
        f"sqlmap -u {_q(target)} --batch --level=2 --risk=1 "
        f"--output-dir={_wsl_path(str(out_file))} 2>&1 | tail -30"
    )
    return run_kali_bg(cmd, job_name="sqlmap")


# ── DNS & Ağ Analizi ──────────────────────────────────────────────────────────

def tool_kali_dig(arg: str = "") -> ToolResult:
    """DNS sorgusu. Örnek: 'example.com MX'"""
    if not arg.strip():
        return ToolResult(message="Domain belirt. Örnek: example.com", ok=True)
    parts  = arg.strip().split()
    domain = parts[0]
    qtype  = parts[1].upper() if len(parts) > 1 else "ANY"
    return run_kali(f"dig {_q(domain)} {_q(qtype)} +short 2>&1 | head -20", timeout=10)


def tool_kali_whois(arg: str = "") -> ToolResult:
    """WHOIS sorgusu. Örnek: 'example.com'"""
    if not arg.strip():
        return ToolResult(message="Domain veya IP belirt.", ok=True)
    return run_kali(f"whois {_q(arg.strip())} 2>&1 | head -40", timeout=15)


def tool_kali_traceroute(arg: str = "") -> ToolResult:
    """Traceroute ile ağ yolu izleme. Örnek: '8.8.8.8'"""
    if not arg.strip():
        return ToolResult(message="Hedef IP belirt. Örnek: 8.8.8.8", ok=True)
    return run_kali(f"traceroute -m 15 {_q(arg.strip())} 2>&1", timeout=30)


def tool_kali_netstat(arg: str = "") -> ToolResult:
    """Kali üzerindeki aktif ağ bağlantılarını listele."""
    return run_kali("ss -tulnp 2>&1 | head -30", timeout=10)


# ── Şifre & Hash ─────────────────────────────────────────────────────────────

def tool_kali_hash_crack(arg: str = "") -> ToolResult:
    """Hash türünü tespit et. Örnek: '5d41402abc4b2a76b9719d911017c592'"""
    if not arg.strip():
        return ToolResult(message="Hash değeri belirt.", ok=True)
    h = arg.strip()
    return run_kali(f"hash-identifier {_q(h)} 2>&1 | head -20", timeout=10)


def tool_kali_hashid(arg: str = "") -> ToolResult:
    """hashid ile hash türü belirle. Örnek: '$1$abc$xyz'"""
    if not arg.strip():
        return ToolResult(message="Hash değeri belirt.", ok=True)
    return run_kali(f"hashid {_q(arg.strip())} 2>&1", timeout=10)


# ── Sistem & Genel ────────────────────────────────────────────────────────────

def tool_kali_tools_list(arg: str = "") -> ToolResult:
    """Kali'de kurulu güvenlik araçlarını listele."""
    return run_kali(
        "dpkg --get-selections | grep -E 'nmap|nikto|sqlmap|gobuster|hydra|"
        "metasploit|burp|whatweb|dirb|wfuzz|ffuf|hashcat|john' | awk '{print $1}' 2>&1",
        timeout=10,
    )


def tool_kali_shell(arg: str = "") -> ToolResult:
    """
    Kali'de serbest komut çalıştır (güvenli modda).
    Tehlikeli komutlar engellenir.

    BİLİNÇLİ TASARIM (gözden kaçma değil): Bu araç TAM komutu çalıştırmak içindir
    (kullanıcı pipe/redirect/argüman verebilir) → hedef-araçlarındaki _q()/shlex.quote
    savunması BURADA UYGULANMAZ; escape etmek aracın amacını (serbest komut) bozar.
    Yapısal alternatif (argv-listesi) yok. Tek savunma run_kali içindeki _BLOCKED kara
    listesidir (kaba ama serbest-komutta eldeki en iyisi). Hedef-injection kapatıldı
    (kali_dig/whois/nmap... _q'lu); serbest-komut riski araçların doğasında var.
    """
    if not arg.strip():
        return ToolResult(message="Çalıştırılacak komutu belirt.", ok=True)
    return run_kali(arg.strip(), timeout=20)


# ── Tool registry kayıtları ───────────────────────────────────────────────────

register_tool("kali_nmap",        tool_kali_nmap)
register_tool("kali_nmap_quick",  tool_kali_nmap_quick)
register_tool("kali_whatweb",     tool_kali_whatweb)
register_tool("kali_gobuster",    tool_kali_gobuster)
register_tool("kali_nikto",       tool_kali_nikto)
register_tool("kali_sqlmap",      tool_kali_sqlmap)
register_tool("kali_dig",         tool_kali_dig)
register_tool("kali_whois",       tool_kali_whois)
register_tool("kali_traceroute",  tool_kali_traceroute)
register_tool("kali_netstat",     tool_kali_netstat)
register_tool("kali_hash_crack",  tool_kali_hash_crack)
register_tool("kali_hashid",      tool_kali_hashid)
register_tool("kali_tools_list",  tool_kali_tools_list)
register_tool("kali_shell",       tool_kali_shell)
