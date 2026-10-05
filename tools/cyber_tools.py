# -*- coding: utf-8 -*-
"""
tools/cyber_tools.py — Klargus Cyber Keşif & Network Araçları (Parça 1 + Parça 2)
===================================================================================
Pentest workflow'unun keşif aşaması.

GRUP A — Keşif (Python):
  dns_lookup      — DNS kayıtları (A, AAAA, MX, TXT, NS, CNAME, SOA)
  whois_lookup    — Domain WHOIS bilgisi
  subdomain_enum  — Pasif alt domain keşfi (crt.sh)
  ip_info         — IP geolocation + ASN + ISP (ipapi.co)
  reverse_dns     — IP → hostname (PTR)

GRUP B — Network (Karışık):
  nmap_scan       — Port + servis tarama (WSL/nmap)
  traceroute      — Network yol takibi (Windows tracert / Linux traceroute)

Her fonksiyon dict döner. Exception fırlatmaz.
"""

from __future__ import annotations

from core.logger import get_logger
import re
import shlex
import socket
import subprocess
import sys
from typing import Any

log = get_logger("cyber_tools")


# ── Zaman aşımı sabitleri ─────────────────────────────────────────────────────
# Değerler config.py'de merkezî (tek doğruluk kaynağı); yerel adlar korunuyor —
# kullanım noktaları (31 yer) değişmedi, sadece değer kaynağı config oldu.
from config import (
    CYBER_DNS_TIMEOUT, CYBER_WHOIS_TIMEOUT, CYBER_SUBDOMAIN_TIMEOUT,
    CYBER_IPINFO_TIMEOUT, CYBER_NMAP_TIMEOUTS, CYBER_TRACEROUTE_TIMEOUT,
    CYBER_HTTP_TIMEOUT, CYBER_SSL_TIMEOUT, CYBER_ROBOTS_TIMEOUT,
    CYBER_GOBUSTER_TIMEOUT, CYBER_NIKTO_TIMEOUT, CYBER_SQLMAP_TIMEOUT,
    CYBER_HASHCRACK_TIMEOUT,
)

_DNS_TIMEOUT        = CYBER_DNS_TIMEOUT
_WHOIS_TIMEOUT      = CYBER_WHOIS_TIMEOUT
_SUBDOMAIN_TIMEOUT  = CYBER_SUBDOMAIN_TIMEOUT
_IPINFO_TIMEOUT     = CYBER_IPINFO_TIMEOUT
_NMAP_TIMEOUTS      = CYBER_NMAP_TIMEOUTS
_TRACEROUTE_TIMEOUT = CYBER_TRACEROUTE_TIMEOUT


# ═════════════════════════════════════════════════════════════════════════════
# GRUP A — KEŞİF
# ═════════════════════════════════════════════════════════════════════════════

def dns_lookup(domain: str) -> dict[str, Any]:
    """
    Bir domain'in tüm DNS kayıtlarını çeker.

    Args:
        domain: Sorgu yapılacak domain adı (örn: "google.com")

    Returns:
        dict: {A: [...], AAAA: [...], MX: [...], TXT: [...],
               NS: [...], CNAME: [...], SOA: [...]}
              Hata durumunda: {"error": "..."}
    """
    domain = (domain or "").strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0]
    if not domain:
        return {"error": "Domain belirtilmedi."}

    try:
        import dns.resolver
        import dns.exception
    except ImportError:
        return {"error": "dnspython kurulu degil. 'pip install dnspython' calistir."}

    record_types = ["A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA"]
    results: dict[str, Any] = {}   # DNS kayıt listeleri + "domain" (str) karışık değer

    def _make_resolver():
        # dns.resolver.Resolver thread-safe DEĞİL → her sorgu kendi instance'ını kurar.
        # timeout=3 (tek sunucu yanıtı), lifetime=8 (tip başına toplam üst sınır).
        r = dns.resolver.Resolver()
        r.timeout = 3
        r.lifetime = 8
        return r

    # ── Ön-uçuş: NXDOMAIN'i HIZDAN ayır ───────────────────────────────────────
    # NXDOMAIN authoritative bir cevaptır; ilk (A) sorgusunda belli olur. Yavaş
    # ağlarda NXDOMAIN yanıtı geç gelebildiği için, olmayan domain'de 7 tipi de
    # beklemek hem gereksiz hem de agresif lifetime ile yanlış sonuç verirdi.
    # Önce TEK A sorgusu: NXDOMAIN ise HEMEN dön, kalan 6 tipe hiç girme.
    try:
        _pre = _make_resolver().resolve(domain, "A", raise_on_no_answer=False)
        results["A"] = [str(r).rstrip(".") for r in _pre] or None
        if not results["A"]:
            del results["A"]
    except dns.resolver.NXDOMAIN:
        return {"error": f"Domain bulunamadi: {domain}"}
    except dns.resolver.Timeout:
        results["A"] = ["(zaman asimi)"]
    except Exception:
        pass  # A yok/başka hata — domain var olabilir (ör. sadece MX), paralele devam

    # ── Paralel: kalan 6 tipi aynı anda sorgula ───────────────────────────────
    # NXDOMAIN elendi; başarılı sorgu paralel olduğu için toplam süre ≈ en yavaş
    # TEK tip (seri toplamı değil). A yukarıda çözüldü, tekrar sorgulanmaz.
    from concurrent.futures import ThreadPoolExecutor

    def _query(rtype: str):
        try:
            answers = _make_resolver().resolve(domain, rtype, raise_on_no_answer=False)
            values = [str(rdata).rstrip(".") for rdata in answers]
            return values or None
        except dns.resolver.Timeout:
            return ["(zaman asimi)"]
        except Exception:
            return None  # O kayit tipi yok, atla

    _rest = [rt for rt in record_types if rt != "A"]
    with ThreadPoolExecutor(max_workers=len(_rest)) as pool:
        answered = dict(zip(_rest, pool.map(_query, _rest)))

    for rtype in record_types:  # sabit sırada topla (A dahil)
        v = results.get(rtype) if rtype == "A" else answered.get(rtype)
        if v:
            results[rtype] = v

    # ── dnspython gerçek kayıt verdi mi? ──────────────────────────────────────
    _TIMEOUT_MARK = ["(zaman asimi)"]
    _real = any(v and v != _TIMEOUT_MARK
                for k, v in results.items() if k != "domain")
    if _real:
        results["domain"] = domain
        results["resolver"] = "dnspython"   # tam veri (tüm kayıt tipleri denendi)
        return results

    # ── dnspython HİÇ gerçek kayıt veremedi ───────────────────────────────────
    # NXDOMAIN yukarıda elendi. Burada iki olasılık var: (a) sunucular erişilemez
    # (timeout), (b) domain'in gerçekten kaydı yok. SADECE timeout varsa OS
    # resolver'a düş — dnspython sistem nameserver'larını (VPN/kurumsal 10.x)
    # DOĞRUDAN sorguluyor; onlar kararsızken bile OS resolver farklı yoldan
    # çalışabiliyor. Bu, aracı o sunucuların sağlığına tam bağımlı olmaktan kurtarır.
    _had_timeout = any(v == _TIMEOUT_MARK
                       for k, v in results.items() if k != "domain")
    if _had_timeout:
        _fb = _os_dns_fallback(domain)
        if _fb:
            log.warning(
                "dns_lookup: dnspython resolver timeout (%s) — OS resolver'a "
                "fallback (kismi: A/AAAA). Tipler: %s",
                domain, list(_fb.keys()),
            )
            _fb["domain"] = domain
            _fb["resolver"] = "os_fallback"   # KISMİ veri — yalnız A/AAAA
            return _fb

    return {"error": f"'{domain}' icin hicbir DNS kaydi bulunamadi."}


def _os_dns_fallback(domain: str) -> dict[str, list[str]]:
    """dnspython erişilemezken OS resolver (socket) ile A/AAAA çöz.

    getaddrinfo, işletim sisteminin resolver yolunu kullanır (dnspython'ın
    doğrudan sorguladığı nameserver'lardan bağımsız). Yalnız A/AAAA döndürebilir;
    MX/TXT/NS/SOA/CNAME OS resolver'dan alınamaz → sessizce atlanır (dict yapısı
    bozulmaz). Çözemezse ({} döner) çağıran hata yoluna gider — NXDOMAIN mantığına
    KARIŞMAZ (o zaten A ön-sorgusunda ele alındı)."""
    out: dict[str, list[str]] = {}
    try:
        infos = socket.getaddrinfo(domain, None)
    except Exception:
        return out  # OS resolver de çözemedi — kısmi veri bile yok
    _v4 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET})
    _v6 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET6})
    if _v4:
        out["A"] = _v4
    if _v6:
        out["AAAA"] = _v6
    return out


def whois_lookup(domain: str) -> dict[str, Any]:
    """
    Domain WHOIS bilgisi çeker.

    Args:
        domain: WHOIS sorgusu yapılacak domain (örn: "github.com")

    Returns:
        dict: {registrar, creation_date, expiration_date,
               updated_date, name_servers, registrant, status}
              Hata durumunda: {"error": "..."}
    """
    domain = (domain or "").strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0]
    if not domain:
        return {"error": "Domain belirtilmedi."}

    try:
        import whois as _whois
    except ImportError:
        return {"error": "python-whois kurulu degil. 'pip install python-whois' calistir."}

    try:
        w = _whois.whois(domain)
        if not w or not w.domain_name:
            return {"error": f"'{domain}' icin WHOIS verisi alinamadi."}

        def _str(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, list):
                return ", ".join(str(v) for v in val[:3])
            return str(val)

        return {
            "domain":          _str(w.domain_name).lower(),
            "registrar":       _str(w.registrar),
            "creation_date":   _str(w.creation_date),
            "expiration_date": _str(w.expiration_date),
            "updated_date":    _str(w.updated_date),
            "name_servers":    _str(w.name_servers),
            "registrant":      _str(w.registrant_country or w.org or ""),
            "status":          _str(w.status),
        }
    except Exception as exc:
        return {"error": f"WHOIS hatasi: {exc}"}


def subdomain_enum(domain: str) -> dict[str, Any]:
    """
    Pasif alt domain keşfi — crt.sh SSL sertifika geçmişinden arar.

    Args:
        domain: Ana domain (örn: "example.com")

    Returns:
        dict: {domain: str, count: int, subdomains: [str, ...]}
              Hata durumunda: {"error": "..."}
    """
    domain = (domain or "").strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0]
    if not domain:
        return {"error": "Domain belirtilmedi."}

    try:
        import requests
    except ImportError:
        return {"error": "requests kurulu degil."}

    url = f"https://crt.sh/?q=%25.{domain}&output=json"
    try:
        resp = requests.get(url, timeout=_SUBDOMAIN_TIMEOUT, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return {"error": "crt.sh zaman asimina ugradi (30s)."}
    except requests.exceptions.ConnectionError:
        return {"error": "crt.sh'e baglanilmadi. Internet baglantisinizi kontrol edin."}
    except Exception as exc:
        return {"error": f"crt.sh hatasi: {exc}"}

    seen: set[str] = set()
    for entry in data:
        # name_value birden fazla domain iceriyor olabilir
        for name in str(entry.get("name_value", "")).split("\n"):
            name = name.strip().lower().lstrip("*.")
            if name.endswith(f".{domain}") or name == domain:
                seen.add(name)

    subdomains = sorted(seen)
    return {
        "domain":     domain,
        "count":      len(subdomains),
        "subdomains": subdomains,
    }


def ip_info(ip: str) -> dict[str, Any]:
    """
    IP geolocation + ASN + ISP bilgisi çeker (ipapi.co).

    Args:
        ip: IPv4 veya IPv6 adresi (örn: "8.8.8.8")

    Returns:
        dict: {ip, country, country_code, region, city,
               latitude, longitude, asn, org, timezone}
              Hata durumunda: {"error": "..."}
    """
    ip = (ip or "").strip()
    if not ip:
        return {"error": "IP adresi belirtilmedi."}

    # Basit format kontrolu
    if not re.match(r"^[\d\.\:a-fA-F]+$", ip):
        return {"error": f"Gecersiz IP formati: {ip}"}

    try:
        import requests
    except ImportError:
        return {"error": "requests kurulu degil."}

    try:
        resp = requests.get(
            f"https://ipapi.co/{ip}/json/",
            timeout=_IPINFO_TIMEOUT,
            headers={"User-Agent": "Klargus/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return {"error": "ipapi.co zaman asimina ugradi."}
    except requests.exceptions.ConnectionError:
        return {"error": "ipapi.co'ya baglanilmadi."}
    except Exception as exc:
        return {"error": f"IP info hatasi: {exc}"}

    if "error" in data:
        return {"error": data.get("reason", str(data["error"]))}

    return {
        "ip":           data.get("ip", ip),
        "country":      data.get("country_name", ""),
        "country_code": data.get("country_code", ""),
        "region":       data.get("region", ""),
        "city":         data.get("city", ""),
        "latitude":     data.get("latitude", ""),
        "longitude":    data.get("longitude", ""),
        "asn":          data.get("asn", ""),
        "org":          data.get("org", ""),
        "timezone":     data.get("timezone", ""),
        "is_private":   bool(data.get("bogon", False)),
    }


def reverse_dns(ip: str) -> dict[str, Any]:
    """
    IP adresinden hostname çözer (PTR / ters DNS sorgusu).

    Args:
        ip: IPv4 veya IPv6 adresi (örn: "8.8.8.8")

    Returns:
        dict: {ip: str, hostname: str}  veya  {"error": "..."}
    """
    ip = (ip or "").strip()
    if not ip:
        return {"error": "IP adresi belirtilmedi."}

    try:
        socket.setdefaulttimeout(_DNS_TIMEOUT)
        hostname, _, _ = socket.gethostbyaddr(ip)
        return {"ip": ip, "hostname": hostname}
    except socket.herror:
        return {"ip": ip, "hostname": None, "error": f"'{ip}' icin PTR kaydi bulunamadi."}
    except socket.timeout:
        return {"error": f"PTR sorgusu zaman asimina ugradi ({_DNS_TIMEOUT}s)."}
    except OSError as exc:
        return {"error": f"Reverse DNS hatasi: {exc}"}
    finally:
        socket.setdefaulttimeout(None)


# ═════════════════════════════════════════════════════════════════════════════
# GRUP B — NETWORK
# ═════════════════════════════════════════════════════════════════════════════

def nmap_scan(target: str, mode: str = "fast") -> dict[str, Any]:
    """
    nmap ile port / servis tarama (WSL2/Kali üzerinden).

    Args:
        target: IP, hostname veya CIDR (örn: "192.168.1.1", "192.168.1.0/24")
        mode:   "fast"    → -F (top 100 port)       timeout: 60s
                "full"    → -p- (tüm portlar)        timeout: 600s
                "service" → -sV (servis tespit)      timeout: 180s
                "vuln"    → --script vuln             timeout: 900s

    Returns:
        dict: {target, mode, ports: [{port, state, service, version}], summary, raw}
              Hata durumunda: {"error": "..."}
    """
    target = (target or "").strip()
    if not target:
        return {"error": "Hedef belirtilmedi."}

    mode = (mode or "fast").lower().strip()
    _FLAGS = {
        "fast":    "-F",
        "full":    "-p-",
        "service": "-sV",
        "vuln":    "--script vuln",
    }
    if mode not in _FLAGS:
        return {"error": f"Gecersiz mod: {mode}. Gecerli modlar: {list(_FLAGS.keys())}"}

    flag    = _FLAGS[mode]
    timeout = _NMAP_TIMEOUTS.get(mode, 120)

    # WSL2 uzerinden calistir
    try:
        from tools.system_tools import run_wsl_command
    except ImportError:
        return {"error": "tools.system_tools import edilemedi."}

    # Bash-injection güvenliği (2026-09-29): hedef string bash'e GÖMÜLÜR — kaçırılmazsa
    # ';', '|', '$()' gibi metakarakterler injection'a yol açar (kali_tools.py'deki
    # aynı kök-neden savunmasıyla tutarlı). shlex.quote meşru hedefte no-op'tur.
    cmd    = f"nmap {flag} -T4 --open {shlex.quote(target)} 2>&1"
    result = run_wsl_command(cmd, timeout=timeout)

    if not result["success"] and not result["stdout"]:
        err = result["stderr"] or f"exit_code={result['exit_code']}"
        # nmap kurulu degil mi?
        if "not found" in err.lower() or "command not found" in err.lower():
            return {"error": "nmap WSL'de kurulu degil. 'sudo apt-get install -y nmap' calistir."}
        return {"error": f"nmap hatasi: {err}"}

    raw_output = result["stdout"]

    # ── Port satirlarini parse et ─────────────────────────────────────────────
    ports: list[dict[str, str]] = []
    for line in raw_output.splitlines():
        # Ornek: "80/tcp   open  http    nginx 1.18"
        m = re.match(
            r"^(\d+)/(tcp|udp)\s+(open|filtered|closed)\s+(\S+)\s*(.*)?$",
            line.strip(),
        )
        if m:
            ports.append({
                "port":    m.group(1),
                "proto":   m.group(2),
                "state":   m.group(3),
                "service": m.group(4),
                "version": m.group(5).strip() if m.group(5) else "",
            })

    # Ozet satiri
    summary_match = re.search(r"Nmap done.*?(\d+ host[s]? up)", raw_output)
    summary = summary_match.group(1) if summary_match else f"{len(ports)} port bulundu"

    return {
        "target":  target,
        "mode":    mode,
        "ports":   ports,
        "summary": summary,
        "raw":     raw_output[:3000],
    }


def traceroute(target: str) -> dict[str, Any]:
    """
    Network yol takibi. Windows'ta 'tracert', Linux/WSL'de 'traceroute' kullanır.

    Args:
        target: Hedef IP veya hostname (örn: "8.8.8.8", "google.com")

    Returns:
        dict: {target, platform, hops: [{hop, ip, hostname, time_ms}], raw}
              Hata durumunda: {"error": "..."}
    """
    target = (target or "").strip()
    if not target:
        return {"error": "Hedef belirtilmedi."}

    is_windows = sys.platform.startswith("win")

    if is_windows:
        cmd   = ["tracert", "-d", "-w", "2000", "-h", "20", target]
        label = "windows"
    else:
        cmd   = ["traceroute", "-n", "-w", "2", "-m", "20", target]
        label = "linux"

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=_TRACEROUTE_TIMEOUT,
        )
        raw = proc.stdout.decode("utf-8", errors="replace")
        if not raw:
            raw = proc.stderr.decode("utf-8", errors="replace")
    except FileNotFoundError:
        # Windows'ta tracert yoksa WSL'den dene
        try:
            from tools.system_tools import run_wsl_command
            res = run_wsl_command(f"traceroute -n -w 2 -m 20 {shlex.quote(target)}", timeout=_TRACEROUTE_TIMEOUT)
            raw   = res.get("stdout", "") or res.get("stderr", "")
            label = "wsl"
        except Exception as exc:
            return {"error": f"traceroute/tracert bulunamadi: {exc}"}
    except subprocess.TimeoutExpired:
        return {"error": f"Traceroute {_TRACEROUTE_TIMEOUT}s'de tamamlanamadi."}
    except Exception as exc:
        return {"error": f"Traceroute hatasi: {exc}"}

    # ── Hop satirlarini parse et ──────────────────────────────────────────────
    hops: list[dict[str, Any]] = []

    if label == "windows":
        # Windows tracert formati: "  1    <1 ms    <1 ms    <1 ms  192.168.1.1"
        for line in raw.splitlines():
            m = re.match(
                r"^\s*(\d+)\s+(?:<?\d+|[\*]+)\s*ms\s+(?:<?\d+|[\*]+)\s*ms\s+(?:<?\d+|[\*]+)\s*ms\s+([\d\.]+|[\*]+)",
                line,
            )
            if m:
                hop_num = int(m.group(1))
                ip      = m.group(2) if m.group(2) != "*" else ""
                # Ortalama sure icin ilk ms degerini al
                t_match = re.search(r"(\d+)\s*ms", line)
                time_ms = int(t_match.group(1)) if t_match else -1
                hops.append({"hop": hop_num, "ip": ip, "hostname": "", "time_ms": time_ms})
    else:
        # Linux/WSL traceroute: "  1  192.168.1.1  0.456 ms  0.312 ms  0.298 ms"
        for line in raw.splitlines():
            m = re.match(
                r"^\s*(\d+)\s+([\d\.]+|\*)\s+(\d+\.?\d*)\s*ms",
                line,
            )
            if m:
                hops.append({
                    "hop":      int(m.group(1)),
                    "ip":       m.group(2) if m.group(2) != "*" else "",
                    "hostname": "",
                    "time_ms":  float(m.group(3)),
                })

    return {
        "target":   target,
        "platform": label,
        "hops":     hops,
        "raw":      raw[:3000],
    }


# ═════════════════════════════════════════════════════════════════════════════
# GRUP C — WEB ANALİZ (Parça 2)
# ═════════════════════════════════════════════════════════════════════════════

_HTTP_TIMEOUT     = CYBER_HTTP_TIMEOUT
_SSL_TIMEOUT      = CYBER_SSL_TIMEOUT
_ROBOTS_TIMEOUT   = CYBER_ROBOTS_TIMEOUT
_GOBUSTER_TIMEOUT = CYBER_GOBUSTER_TIMEOUT

_UA = "Klargus-Cyber/1.0"

_SECURITY_HEADERS = {
    "Strict-Transport-Security":  "hsts",
    "Content-Security-Policy":    "csp",
    "X-Frame-Options":            "x_frame_options",
    "X-Content-Type-Options":     "x_content_type_options",
    "Referrer-Policy":            "referrer_policy",
    "Permissions-Policy":         "permissions_policy",
}


def http_analysis(url: str) -> dict[str, Any]:
    """
    Bir web sitesinin HTTP yanıtını analiz eder.

    Döner: status_code, redirect_chain, tüm headers,
    güvenlik header'ları, eksik güvenlik header'ları,
    cookie listesi (Secure/HttpOnly/SameSite), server, tech_hints.
    """
    url = (url or "").strip()
    if not url:
        return {"error": "URL belirtilmedi."}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        import requests
    except ImportError:
        return {"error": "requests kurulu degil."}

    try:
        resp = requests.get(
            url,
            timeout=_HTTP_TIMEOUT,
            allow_redirects=True,
            headers={"User-Agent": _UA},
            verify=True,
        )
    except requests.exceptions.SSLError as exc:
        return {"error": f"SSL hatasi: {exc}"}
    except requests.exceptions.ConnectionError as exc:
        return {"error": f"Baglanti hatasi: {exc}"}
    except requests.exceptions.Timeout:
        return {"error": f"Zaman asimi ({_HTTP_TIMEOUT}s)."}
    except Exception as exc:
        return {"error": f"HTTP hatasi: {exc}"}

    headers_lower = {k.lower(): v for k, v in resp.headers.items()}

    # Redirect zinciri
    redirect_chain = [r.url for r in resp.history]

    # Güvenlik header'ları
    sec: dict[str, Any] = {}
    missing: list[str] = []
    for header, key in _SECURITY_HEADERS.items():
        val = resp.headers.get(header)
        sec[key] = val if val is not None else None
        if val is None:
            missing.append(header)

    # Cookies
    cookies: list[dict[str, Any]] = []
    for c in resp.cookies:
        cookies.append({
            "name":     c.name,
            "value":    c.value[:40] + "..." if c.value and len(c.value) > 40 else c.value,
            "secure":   bool(c.secure),
            "httponly": "httponly" in str(c._rest).lower(),
            "samesite": c._rest.get("SameSite", "") if hasattr(c, "_rest") else "",
        })

    # Tech hints
    tech_hints: list[str] = []
    for h in ("x-powered-by", "x-generator", "x-drupal-cache",
              "x-wp-total", "x-shopify-stage", "x-aspnet-version"):
        if h in headers_lower:
            tech_hints.append(f"{h}: {headers_lower[h]}")

    return {
        "url":                    url,
        "final_url":              resp.url,
        "status_code":            resp.status_code,
        "redirect_chain":         redirect_chain,
        "headers":                dict(resp.headers),
        "security_headers":       sec,
        "missing_security_headers": missing,
        "cookies":                cookies,
        "server":                 resp.headers.get("Server", ""),
        "tech_hints":             tech_hints,
    }


def ssl_check(domain: str, port: int = 443) -> dict[str, Any]:
    """
    SSL/TLS sertifika bilgilerini analiz eder.

    Döner: geçerlilik tarihleri, kalan gün, issuer, SANs, TLS version, cipher.
    """
    domain = (domain or "").strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if not domain:
        return {"error": "Domain belirtilmedi."}

    import ssl as _ssl
    import datetime

    ctx = _ssl.create_default_context()

    try:
        conn = ctx.wrap_socket(
            socket.create_connection((domain, port), timeout=_SSL_TIMEOUT),
            server_hostname=domain,
        )
    except _ssl.SSLCertVerificationError as exc:
        return {"error": f"Sertifika dogrulanamadi: {exc}"}
    except ConnectionRefusedError:
        return {"error": f"{domain}:{port} baglantisi reddedildi."}
    except socket.timeout:
        return {"error": f"Baglanti zaman asimi ({_SSL_TIMEOUT}s)."}
    except Exception as exc:
        return {"error": f"SSL baglanti hatasi: {exc}"}

    try:
        cert: dict = conn.getpeercert() or {}   # None ise boş dict (çökme yerine graceful)
        tls_version = conn.version()
        cipher      = conn.cipher()
    finally:
        conn.close()

    def _parse_field(rdn_seq: tuple) -> dict[str, str]:
        out: dict[str, str] = {}
        for rdn in rdn_seq:
            for k, v in rdn:
                out[k] = v
        return out

    subject = _parse_field(cert.get("subject", ()))
    issuer  = _parse_field(cert.get("issuer", ()))

    # Tarihler
    fmt = "%b %d %H:%M:%S %Y %Z"
    not_before_str = cert.get("notBefore", "")
    not_after_str  = cert.get("notAfter", "")
    try:
        not_after_dt = datetime.datetime.strptime(not_after_str, fmt)
        days_remaining = (not_after_dt - datetime.datetime.utcnow()).days
        expired = days_remaining < 0
    except Exception:
        days_remaining = -1
        expired        = False

    # SANs
    sans: list[str] = []
    for kind, value in cert.get("subjectAltName", ()):
        if kind == "DNS":
            sans.append(value)

    return {
        "domain":        domain,
        "port":          port,
        "valid":         True,
        "subject":       subject,
        "issuer":        issuer,
        "not_before":    not_before_str,
        "not_after":     not_after_str,
        "days_remaining": days_remaining,
        "tls_version":   tls_version or "",
        "cipher":        cipher[0] if cipher else "",
        "sans":          sans[:10],
        "expired":       expired,
    }


def robots_txt(url: str) -> dict[str, Any]:
    """
    robots.txt dosyasını indirir ve sitemap URL'lerini çeker.

    Döner: raw içerik, user-agent kuralları, sitemap listesi,
    ilginç path'ler (admin, api, login gibi).
    """
    url = (url or "").strip()
    if not url:
        return {"error": "URL belirtilmedi."}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Kök URL'yi al
    from urllib.parse import urlparse, urljoin
    parsed   = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = urljoin(base_url, "/robots.txt")

    try:
        import requests
    except ImportError:
        return {"error": "requests kurulu degil."}

    try:
        resp = requests.get(
            robots_url,
            timeout=_ROBOTS_TIMEOUT,
            headers={"User-Agent": _UA},
        )
    except requests.exceptions.ConnectionError as exc:
        return {"error": f"Baglanti hatasi: {exc}"}
    except requests.exceptions.Timeout:
        return {"error": "Zaman asimi."}
    except Exception as exc:
        return {"error": f"robots.txt hatasi: {exc}"}

    if resp.status_code == 404:
        return {
            "url":           base_url,
            "robots_url":    robots_url,
            "robots_exists": False,
            "robots_content": "",
            "user_agents":  {},
            "sitemaps":     [],
            "sitemap_urls": [],
            "interesting_paths": [],
        }

    content = resp.text

    # User-agent ve direktifleri parse et
    user_agents: dict[str, dict[str, list[str]]] = {}
    current_ua: str = "*"
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("user-agent:"):
            current_ua = line.split(":", 1)[1].strip()
            if current_ua not in user_agents:
                user_agents[current_ua] = {"disallow": [], "allow": []}
        elif line.lower().startswith("disallow:"):
            path = line.split(":", 1)[1].strip()
            if path and current_ua in user_agents:
                user_agents[current_ua]["disallow"].append(path)
        elif line.lower().startswith("allow:"):
            path = line.split(":", 1)[1].strip()
            if path and current_ua in user_agents:
                user_agents[current_ua]["allow"].append(path)

    # Sitemaps
    sitemaps = [
        line.split(":", 1)[1].strip()
        for line in content.splitlines()
        if line.lower().startswith("sitemap:")
    ]

    # Sitemap URL'lerini çek (ilk sitemap, ilk 50 URL)
    sitemap_urls: list[str] = []
    if sitemaps:
        try:
            sr = requests.get(sitemaps[0], timeout=_ROBOTS_TIMEOUT, headers={"User-Agent": _UA})
            sitemap_urls = re.findall(r"<loc>\s*(https?://[^\s<]+)\s*</loc>", sr.text)[:50]
        except Exception:
            pass

    # İlginç path'ler (tüm disallow listesinden)
    interesting_keywords = {"admin", "api", "login", "wp-admin", "phpmyadmin",
                            "dashboard", "secret", "private", "backup", "config"}
    interesting: list[str] = []
    for ua_rules in user_agents.values():
        for path in ua_rules["disallow"]:
            pl = path.lower()
            if any(kw in pl for kw in interesting_keywords):
                if path not in interesting:
                    interesting.append(path)

    return {
        "url":               base_url,
        "robots_url":        robots_url,
        "robots_exists":     True,
        "robots_content":    content[:3000],
        "user_agents":       user_agents,
        "sitemaps":          sitemaps,
        "sitemap_urls":      sitemap_urls,
        "interesting_paths": interesting[:20],
    }


def gobuster_dir(url: str, wordlist: str = "common") -> dict[str, Any]:
    """
    gobuster ile web dizin/dosya keşfi yapar (WSL2/Kali üzerinden).

    wordlist: "common" | "big" | "medium"
    Döner: bulunan path'ler, status kodları ve boyutları.
    """
    url = (url or "").strip()
    if not url:
        return {"error": "URL belirtilmedi."}
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    _WORDLISTS = {
        "common": "/usr/share/wordlists/dirb/common.txt",
        "big":    "/usr/share/wordlists/dirb/big.txt",
        "medium": "/usr/share/wordlists/dirbuster/directory-list-2.3-medium.txt",
    }
    wordlist_key  = (wordlist or "common").lower().strip()
    wordlist_path = _WORDLISTS.get(wordlist_key, _WORDLISTS["common"])

    try:
        from tools.system_tools import run_wsl_command, check_linux_tool
    except ImportError:
        return {"error": "tools.system_tools import edilemedi."}

    # gobuster kurulu mu?
    if not check_linux_tool("gobuster"):
        return {
            "error": (
                "gobuster WSL/Kali'de kurulu degil. "
                "'sudo apt-get install -y gobuster' ile kur."
            )
        }

    import time
    t0  = time.time()
    # Bash-injection güvenliği (2026-09-29): URL bash'e gömülür → shlex.quote ile kaçır.
    cmd = f"gobuster dir -u {shlex.quote(url)} -w {wordlist_path} -q -t 30 --no-error 2>&1"
    res = run_wsl_command(cmd, timeout=_GOBUSTER_TIMEOUT)

    if not res["success"] and not res["stdout"]:
        return {"error": f"gobuster hatasi: {res['stderr'][:300]}"}

    raw = res["stdout"] or ""

    # Parse: "/admin              (Status: 301) [Size: 312]"
    found: list[dict[str, Any]] = []
    for line in raw.splitlines():
        m = re.match(r"^(/\S*)\s+\(Status:\s*(\d+)\)\s+\[Size:\s*(\d+)\]", line.strip())
        if m:
            found.append({
                "path":        m.group(1),
                "status_code": int(m.group(2)),
                "size":        int(m.group(3)),
            })

    return {
        "url":          url,
        "found":        found,
        "total_found":  len(found),
        "wordlist_used": wordlist_key,
        "duration":     round(time.time() - t0, 2),
        "raw":          raw[:2000],
    }


# ═════════════════════════════════════════════════════════════════════════════
# GRUP D — GÜVENLİK & ENCODING (Parça 2)
# ═════════════════════════════════════════════════════════════════════════════

_HASH_PATTERNS: dict[int, list[str]] = {
    32:  ["MD5", "NTLM", "LM"],
    40:  ["SHA1", "MySQL5 (*+40hex)"],
    56:  ["SHA224"],
    64:  ["SHA256"],
    96:  ["SHA384"],
    128: ["SHA512"],
}

_SUPPORTED_ALGORITHMS = ["md5", "sha1", "sha224", "sha256", "sha384", "sha512", "blake2b"]


def hash_identify(hash_string: str) -> dict[str, Any]:
    """
    Hash string'in türünü tahmin eder.

    Uzunluk + karakter seti analizi ile MD5, SHA1, SHA256 vb. tanır.
    bcrypt, argon2 prefix'lerini de tanır.
    """
    h = (hash_string or "").strip()
    if not h:
        return {"error": "Hash string belirtilmedi."}

    characteristics: list[str] = []
    possible_types:  list[str] = []
    confidence = "low"

    # ── Prefix tabanlı ────────────────────────────────────────────────────────
    if h.startswith(("$2a$", "$2b$", "$2y$")):
        return {
            "hash":           h[:30] + "...",
            "length":         len(h),
            "possible_types": ["bcrypt"],
            "most_likely":    "bcrypt",
            "confidence":     "high",
            "characteristics": ["bcrypt prefix"],
        }
    if h.startswith("$argon2"):
        return {
            "hash":           h[:30] + "...",
            "length":         len(h),
            "possible_types": ["Argon2"],
            "most_likely":    "Argon2",
            "confidence":     "high",
            "characteristics": ["argon2 prefix"],
        }
    if h.startswith("$P$") or h.startswith("$H$"):
        possible_types = ["phpBB3 MD5", "WordPress MD5"]
        confidence     = "high"
        characteristics.append("phpBB/WordPress prefix")

    # ── Karakter seti ─────────────────────────────────────────────────────────
    is_hex    = bool(re.fullmatch(r"[0-9a-fA-F]+", h))
    is_b64    = bool(re.fullmatch(r"[A-Za-z0-9+/=]+", h)) and len(h) % 4 == 0
    # MySQL5: * + 40 hex karakteri (toplam 41)
    is_mysql5 = (h.startswith("*") and len(h) == 41
                 and bool(re.fullmatch(r"[0-9a-fA-F]+", h[1:])))

    if is_mysql5:
        return {
            "hash":           h,
            "length":         len(h),
            "possible_types": ["MySQL5"],
            "most_likely":    "MySQL5",
            "confidence":     "high",
            "characteristics": ["MySQL5 star prefix", "hex"],
        }

    if is_hex:
        characteristics.append("hex")
    if is_b64 and not is_hex:
        characteristics.append("base64")

    # ── Uzunluk tabanlı ───────────────────────────────────────────────────────
    if not possible_types:
        possible_types = _HASH_PATTERNS.get(len(h), [])
        if possible_types:
            confidence = "high" if is_hex else "medium"
        elif is_b64:
            possible_types = ["Base64-encoded hash"]
            confidence     = "low"
        else:
            possible_types = ["Bilinmeyen format"]
            confidence     = "low"

    return {
        "hash":            h[:64] + ("..." if len(h) > 64 else ""),
        "length":          len(h),
        "possible_types":  possible_types,
        "most_likely":     possible_types[0] if possible_types else "Bilinmiyor",
        "confidence":      confidence,
        "characteristics": characteristics,
    }


def hash_calculate(text: str, algorithm: str = "sha256") -> dict[str, Any]:
    """
    Verilen metni belirtilen algoritma ile hash'ler.

    Algoritmalar: md5, sha1, sha224, sha256, sha384, sha512, blake2b
    """
    import hashlib

    text      = text or ""
    algorithm = (algorithm or "sha256").lower().strip()

    if algorithm not in _SUPPORTED_ALGORITHMS:
        return {
            "error": (
                f"Desteklenmeyen algoritma: '{algorithm}'. "
                f"Gecerli algoritmalar: {', '.join(_SUPPORTED_ALGORITHMS)}"
            )
        }

    try:
        encoded = text.encode("utf-8", errors="replace")
        if algorithm == "blake2b":
            digest = hashlib.blake2b(encoded).hexdigest()
        else:
            digest = hashlib.new(algorithm, encoded).hexdigest()
    except Exception as exc:
        return {"error": f"Hash hesaplama hatasi: {exc}"}

    return {
        "text":      text[:100] + ("..." if len(text) > 100 else ""),
        "algorithm": algorithm,
        "hash":      digest,
        "length":    len(digest),
    }


def encode_decode(text: str, method: str, action: str) -> dict[str, Any]:
    """
    Metni çeşitli yöntemlerle encode veya decode eder.

    method:  "base64" | "url" | "hex" | "rot13" | "html"
    action:  "encode" | "decode"
    """
    import base64
    import codecs
    from urllib.parse import quote, unquote
    from html import escape as html_escape, unescape as html_unescape

    text   = text or ""
    method = (method or "").lower().strip()
    action = (action or "").lower().strip()

    _VALID_METHODS = {"base64", "url", "hex", "rot13", "html"}
    _VALID_ACTIONS = {"encode", "decode"}

    if method not in _VALID_METHODS:
        return {"error": f"Gecersiz method: '{method}'. Gecerli: {sorted(_VALID_METHODS)}"}
    if action not in _VALID_ACTIONS:
        return {"error": f"Gecersiz action: '{action}'. Gecerli: encode veya decode"}

    try:
        if method == "base64":
            if action == "encode":
                output = base64.b64encode(text.encode("utf-8")).decode("ascii")
            else:
                # Padding duzelt
                padded = text + "=" * (-len(text) % 4)
                output = base64.b64decode(padded).decode("utf-8", errors="replace")

        elif method == "url":
            output = quote(text, safe="") if action == "encode" else unquote(text)

        elif method == "hex":
            if action == "encode":
                output = text.encode("utf-8").hex()
            else:
                # Bosluk veya : ile ayrilmis hex kabul et
                cleaned = re.sub(r"[\s:]+", "", text)
                output  = bytes.fromhex(cleaned).decode("utf-8", errors="replace")

        elif method == "rot13":
            # ROT13 encode == decode
            output = codecs.encode(text, "rot_13")

        elif method == "html":
            output = html_escape(text) if action == "encode" else html_unescape(text)

        else:
            return {"error": "Bilinmeyen method."}

    except Exception as exc:
        return {"error": f"{method} {action} hatasi: {exc}"}

    return {
        "input":         text[:100] + ("..." if len(text) > 100 else ""),
        "method":        method,
        "action":        action,
        "output":        output,
        "input_length":  len(text),
        "output_length": len(output),
    }


# ═════════════════════════════════════════════════════════════════════════════
# GRUP E — ŞİFRE GÜVENLİĞİ & ZAFİYET TARAMA (Parça 3)
# ═════════════════════════════════════════════════════════════════════════════

_NIKTO_TIMEOUT     = CYBER_NIKTO_TIMEOUT      # 20 dk
_SQLMAP_TIMEOUT    = CYBER_SQLMAP_TIMEOUT     # 10 dk
_HASHCRACK_TIMEOUT = CYBER_HASHCRACK_TIMEOUT  # 30 dk

# Yaygın şifreler (top-100)
_COMMON_PASSWORDS: set[str] = {
    "password","password1","123456","12345678","1234567890","qwerty","abc123",
    "monkey","1234567","letmein","trustno1","dragon","baseball","iloveyou",
    "master","sunshine","ashley","bailey","passw0rd","shadow","123123",
    "654321","superman","qazwsx","michael","football","password2","111111",
    "zxcvbn","mustang","access","shadow","master","michael","superman",
    "696969","123456789","batman","welcome","login","admin","pass","hello",
    "charlie","donald","password3","qwertyuiop","123","admin123","test",
    "qwerty123","p@ssw0rd","root","P@ssword1","monkey123","changeme",
    "1q2w3e4r","pass123","newpass","1qaz2wsx","q1w2e3r4","654321","zaq12wsx",
    "princess","dragon","pass@123","mypassword","1234qwer","test123","secret",
    "abc123456","2020","2021","2022","2023","2024","2025","admin@123",
    "password!","P@55word","Pa$$word","Summer2023","Winter2023","Spring2024",
    "iloveyou1","nicole","jessica","pepper","rockyou","passpass","hello123",
    "guest","letmein1","enter","pass1234","1a2b3c","a1b2c3","abc","aaa",
    "pass1","password0","pwd","p4ssword","qwerty1","0987654321","000000",
}

# hash_crack için geçerli karakter deseni (shell injection engeli)
_VALID_HASH_RE = re.compile(r"^[a-fA-F0-9\$\.\:\/\*\+\=]+$")

# hashcat mode mapping
_HASHCAT_MODES: dict[str, int] = {
    "md5": 0, "sha1": 100, "sha224": 1300, "sha256": 1400,
    "sha384": 10800, "sha512": 1700, "ntlm": 1000, "bcrypt": 3200,
    "mysql5": 300, "lm": 3000,
}


def password_strength(password: str) -> dict[str, Any]:
    """
    Bir şifrenin gücünü analiz eder.

    zxcvbn kütüphanesi varsa kullanır; yoksa builtin mantık devreye girer.
    GÜVENLİK: Şifre değeri hiçbir log'a yazdırılmaz.

    Returns:
        dict: score (0-4), strength, entropy_bits, crack_time_estimate,
              karakter kontrolleri, feedback, warnings.
    """
    pw = password or ""
    if not pw:
        return {"error": "Şifre boş."}

    # ── zxcvbn yolu ──────────────────────────────────────────────────────────
    try:
        from zxcvbn import zxcvbn as _zxcvbn
        z = _zxcvbn(pw)

        _STRENGTH_MAP = {0: "very_weak", 1: "weak", 2: "medium", 3: "strong", 4: "very_strong"}
        score    = int(z["score"])
        warnings = [z["feedback"]["warning"]] if z["feedback"]["warning"] else []

        # Manuel kontroller (zxcvbn bunları söylemeyebilir)
        has_upper   = any(c.isupper() for c in pw)
        has_lower   = any(c.islower() for c in pw)
        has_digit   = any(c.isdigit() for c in pw)
        has_special = any(not c.isalnum() for c in pw)
        is_common   = pw.lower() in _COMMON_PASSWORDS
        has_seq     = _has_sequential(pw)
        has_rep     = _has_repeated(pw)

        return {
            "password_length":     len(pw),
            "has_uppercase":       has_upper,
            "has_lowercase":       has_lower,
            "has_digit":           has_digit,
            "has_special":         has_special,
            "is_common":           is_common,
            "has_sequential":      has_seq,
            "has_repeated":        has_rep,
            "entropy_bits":        round(float(z.get("guesses_log10", 0) * 3.32), 2),
            "crack_time_estimate": str(z["crack_times_display"]["offline_slow_hashing_1e4_per_second"]),
            "score":               score,
            "strength":            _STRENGTH_MAP.get(score, "unknown"),
            "feedback":            list(z["feedback"]["suggestions"]),
            "warnings":            warnings,
            "engine":              "zxcvbn",
        }
    except ImportError:
        pass  # builtin'e düş

    # ── Builtin yol ──────────────────────────────────────────────────────────
    has_upper   = any(c.isupper() for c in pw)
    has_lower   = any(c.islower() for c in pw)
    has_digit   = any(c.isdigit() for c in pw)
    has_special = any(not c.isalnum() for c in pw)
    is_common   = pw.lower() in _COMMON_PASSWORDS
    has_seq     = _has_sequential(pw)
    has_rep     = _has_repeated(pw)

    # Entropy (karakter havuzu × uzunluk log2 tahmini)
    pool = 0
    if has_lower:   pool += 26
    if has_upper:   pool += 26
    if has_digit:   pool += 10
    if has_special: pool += 32
    pool = pool or 26
    import math
    entropy = round(len(pw) * math.log2(pool), 2)

    # Skor hesapla
    score = 0
    if len(pw) >= 8:  score += 1
    if len(pw) >= 12: score += 1
    if has_upper and has_lower and has_digit: score += 1
    if has_special: score += 1
    if is_common or has_seq or has_rep: score = max(0, score - 1)
    score = min(4, score)

    _STRENGTH_MAP = {0: "very_weak", 1: "weak", 2: "medium", 3: "strong", 4: "very_strong"}

    # Crack time tahmini (kaba kuvvet, 10k/s offline)
    guesses = pow(pool, len(pw))
    seconds = guesses / 10_000
    crack_time = _seconds_to_human(seconds)

    # Feedback
    feedback: list[str] = []
    warn_msgs: list[str] = []
    if len(pw) < 8:   feedback.append("En az 8 karakter kullanın.")
    if len(pw) < 12:  feedback.append("12+ karakter önerilir.")
    if not has_upper: feedback.append("Büyük harf ekleyin.")
    if not has_lower: feedback.append("Küçük harf ekleyin.")
    if not has_digit: feedback.append("Rakam ekleyin.")
    if not has_special: feedback.append("Özel karakter ekleyin (!, @, # vb.).")
    if is_common:     warn_msgs.append("Bu şifre çok yaygın — kırılması çok kolay.")
    if has_seq:       warn_msgs.append("Sıralı karakter dizisi (abc, 123) tespit edildi.")
    if has_rep:       warn_msgs.append("Tekrar eden karakterler tespit edildi.")

    return {
        "password_length":     len(pw),
        "has_uppercase":       has_upper,
        "has_lowercase":       has_lower,
        "has_digit":           has_digit,
        "has_special":         has_special,
        "is_common":           is_common,
        "has_sequential":      has_seq,
        "has_repeated":        has_rep,
        "entropy_bits":        entropy,
        "crack_time_estimate": crack_time,
        "score":               score,
        "strength":            _STRENGTH_MAP.get(score, "unknown"),
        "feedback":            feedback,
        "warnings":            warn_msgs,
        "engine":              "builtin",
    }


def _has_sequential(pw: str, min_run: int = 3) -> bool:
    """Sıralı karakter dizisi var mı (abc, 123, xyz)."""
    for i in range(len(pw) - min_run + 1):
        chunk = pw[i:i + min_run]
        if all(ord(chunk[j + 1]) - ord(chunk[j]) == 1 for j in range(min_run - 1)):
            return True
        if all(ord(chunk[j]) - ord(chunk[j + 1]) == 1 for j in range(min_run - 1)):
            return True
    return False


def _has_repeated(pw: str, min_run: int = 3) -> bool:
    """Tekrar eden karakter dizisi var mı (aaa, 111)."""
    for i in range(len(pw) - min_run + 1):
        chunk = pw[i:i + min_run]
        if len(set(chunk)) == 1:
            return True
    return False


def _seconds_to_human(seconds: float) -> str:
    """Saniyeyi insan okunabilir süreye çevir."""
    if seconds < 60:            return f"{seconds:.1f} saniye"
    if seconds < 3600:          return f"{seconds / 60:.1f} dakika"
    if seconds < 86400:         return f"{seconds / 3600:.1f} saat"
    if seconds < 31_536_000:    return f"{seconds / 86400:.1f} gün"
    if seconds < 3.15e9:        return f"{seconds / 31_536_000:.1f} yıl"
    if seconds < 3.15e12:       return f"{seconds / 3.15e9:.1f} bin yıl"
    return "milyonlarca yıl"


def nikto_scan(url: str, options: str = "default") -> dict[str, Any]:
    """
    Nikto ile web zafiyet taraması yapar (WSL2/Kali üzerinden).

    options: "default" | "ssl" | "quick"
    Döner: findings listesi, server_info, raw_output.
    UYARI: Sadece izinli sistemlerde kullanın.
    """
    url = (url or "").strip()
    if not url:
        return {"error": "URL belirtilmedi."}
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    try:
        from tools.system_tools import run_wsl_command, check_linux_tool
    except ImportError:
        return {"error": "tools.system_tools import edilemedi."}

    if not check_linux_tool("nikto"):
        return {
            "error":        "nikto WSL/Kali'de kurulu degil.",
            "install_hint": "WSL Kali terminalinde: sudo apt install -y nikto",
        }

    # cd /tmp: nikto'nun calisma dizinine dosya yazmasini engeller
    # Bash-injection güvenliği (2026-09-29): URL bash'e gömülür → shlex.quote ile kaçır.
    _safe_url = shlex.quote(url)
    _OPT_MAP = {
        "default": f"cd /tmp && nikto -h {_safe_url} -nointeractive",
        "ssl":     f"cd /tmp && nikto -h {_safe_url} -ssl -nointeractive",
        "quick":   f"cd /tmp && nikto -h {_safe_url} -Tuning 1234567890 -nointeractive",
    }
    cmd = _OPT_MAP.get((options or "default").lower(), _OPT_MAP["default"])

    import time
    t0     = time.time()
    result = run_wsl_command(cmd, timeout=_NIKTO_TIMEOUT)
    dur    = round(time.time() - t0, 2)

    raw = result.get("stdout", "") or result.get("stderr", "")

    # Parse: "+ " ile başlayan satırlar = findings
    findings: list[dict[str, str]] = []
    server_info = ""
    tests_run   = 0

    for line in raw.splitlines():
        line_s = line.strip()
        if line_s.startswith("+ "):
            desc = line_s[2:].strip()
            # References (OSVDB, CVE vb.)
            refs: list[str] = re.findall(r"(?:OSVDB|CVE|BID)-[\w\-]+", desc)
            findings.append({
                "description": desc,
                "references":  ", ".join(refs) if refs else "",
            })
        elif "Server:" in line_s:
            server_info = line_s.split("Server:", 1)[-1].strip()
        elif "item(s) reported" in line_s or "requests made" in line_s:
            m = re.search(r"(\d+)", line_s)
            if m:
                tests_run = int(m.group(1))

    return {
        "url":           url,
        "options":       options,
        "findings":      findings,
        "server_info":   server_info,
        "total_findings": len(findings),
        "tests_run":     tests_run,
        "duration":      dur,
        "raw_output":    raw[:5000],
    }


def sqlmap_test(url: str, options: str = "default") -> dict[str, Any]:
    """
    sqlmap ile SQL injection testi yapar (WSL2/Kali üzerinden).

    options: "default" (crawl=1) | "deep" (crawl=2, level=3) | "verify" (boolean only)
    --batch ZORUNLU: interaktif sorular otomatik yanıtlanır.
    YASAL UYARI: Sadece sahibi olduğunuz veya yazılı test izniniz
    olan sistemlerde kullanın. İzinsiz kullanım YASAL DEĞİL.
    """
    url = (url or "").strip()
    if not url:
        return {"error": "URL belirtilmedi."}
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    try:
        from tools.system_tools import run_wsl_command, check_linux_tool
    except ImportError:
        return {"error": "tools.system_tools import edilemedi."}

    if not check_linux_tool("sqlmap"):
        return {
            "error":        "sqlmap WSL/Kali'de kurulu degil.",
            "install_hint": "WSL Kali terminalinde: sudo apt install -y sqlmap",
        }

    _OPT_MAP = {
        "default": "--batch --crawl=1 --random-agent",
        "deep":    "--batch --crawl=2 --level=3 --risk=2 --random-agent",
        "verify":  "--batch --technique=B --random-agent",
    }
    extra = _OPT_MAP.get((options or "default").lower(), _OPT_MAP["default"])
    # Bash-injection güvenliği (2026-09-29): eski kod URL'yi naif '{url}' ile sarıyordu —
    # url içinde tek tırnak varsa bash string'i erken kapanır (injection). shlex.quote
    # gerçek/güvenli kaçış sağlar.
    cmd = f"sqlmap -u {shlex.quote(url)} {extra} 2>&1"

    import time
    t0     = time.time()
    result = run_wsl_command(cmd, timeout=_SQLMAP_TIMEOUT)
    dur    = round(time.time() - t0, 2)

    raw = result.get("stdout", "") or ""

    # Parse
    vulnerable       = False
    vulnerable_params: list[str] = []
    dbms_detected    = ""
    techniques: list[str] = []

    for line in raw.splitlines():
        ls = line.strip().lower()
        # "parameter 'id' is vulnerable" tarzı
        if "is vulnerable" in ls or "parameter" in ls and "injectable" in ls:
            vulnerable = True
            m = re.search(r"parameter\s+'?(\w+)'?", line, re.IGNORECASE)
            if m and m.group(1) not in vulnerable_params:
                vulnerable_params.append(m.group(1))
        # DBMS
        if "back-end dbms:" in ls:
            dbms_detected = line.split(":", 1)[-1].strip()
        # Teknikler
        for tech in ("boolean-based", "union-based", "time-based", "error-based", "stacked"):
            if tech in ls and tech.split("-")[0] not in [t.split("-")[0] for t in techniques]:
                techniques.append(tech)
        # Alternative vulnerable marker
        if "[critical]" in ls and "sql injection" in ls:
            vulnerable = True

    summary = (
        f"Zafiyet BULUNDU — parametre(ler): {', '.join(vulnerable_params)}"
        if vulnerable else "Zafiyet tespit edilmedi (bu tarama kapsamında)."
    )

    return {
        "url":               url,
        "options":           options,
        "vulnerable":        vulnerable,
        "vulnerable_params": vulnerable_params,
        "dbms_detected":     dbms_detected,
        "techniques":        techniques,
        "summary":           summary,
        "raw_output":        raw[:5000],
        "duration":          dur,
        "legal_warning":     "Sadece izinli/test sistemlerinde kullanin.",
    }


def hash_crack(
    hash_value: str,
    hash_type: str  = "auto",
    wordlist: str   = "rockyou",
    mode: str       = "cpu",
) -> dict[str, Any]:
    """
    hashcat (GPU) veya john (CPU) ile hash crackler (WSL2/Kali).

    hash_type: "auto" | "md5" | "sha1" | "sha256" | "ntlm" | "bcrypt" vb.
    wordlist:  "rockyou" | "fasttrack"
    mode:      "cpu" (john) | "gpu" (hashcat)
    GÜVENLİK: hash_value sanitize edilir; geçici dosya oluşturulup silinir.
    """
    import time

    hash_value = (hash_value or "").strip()
    if not hash_value:
        return {"error": "Hash degeri belirtilmedi."}

    # Sanitize — sadece gecerli hash karakterleri
    if not _VALID_HASH_RE.match(hash_value):
        return {"error": "Gecersiz hash formati. Sadece hex/bcrypt/argon2 hash kabul edilir."}

    try:
        from tools.system_tools import run_wsl_command, check_linux_tool
    except ImportError:
        return {"error": "tools.system_tools import edilemedi."}

    mode     = (mode or "cpu").lower().strip()
    tool     = "hashcat" if mode == "gpu" else "john"
    alt_tool = "john"    if mode == "gpu" else "hashcat"

    if not check_linux_tool(tool):
        if not check_linux_tool(alt_tool):
            return {
                "error":        f"{tool} ve {alt_tool} WSL/Kali'de kurulu degil.",
                "install_hint": "sudo apt install -y hashcat john",
            }
        tool = alt_tool  # fallback

    # Hash type tespiti
    resolved_type = (hash_type or "auto").lower().strip()
    if resolved_type == "auto":
        ident = hash_identify(hash_value)
        resolved_type = ident.get("most_likely", "md5").lower().split()[0]

    # Wordlist yolu
    _WORDLISTS = {
        "rockyou":   "/usr/share/wordlists/rockyou.txt",
        "fasttrack": "/usr/share/wordlists/fasttrack.txt",
    }
    wl_path = _WORDLISTS.get((wordlist or "rockyou").lower(), _WORDLISTS["rockyou"])

    # Geçici hash dosyası (WSL yolu)
    import time as _time
    tmp_name = f"/tmp/klargus_hash_{int(_time.time()*1000)}.txt"

    # Hash'i Kali'deki tmp'ye yaz
    write_res = run_wsl_command(f"echo '{hash_value}' > {tmp_name}", timeout=5)
    if not write_res["success"]:
        return {"error": f"Gecici dosya olusturulamadi: {write_res['stderr'][:200]}"}

    try:
        t0 = time.time()

        if tool == "hashcat":
            hm = _HASHCAT_MODES.get(resolved_type, 0)
            cmd = (
                f"hashcat -m {hm} -a 0 {tmp_name} {wl_path} "
                f"--quiet --status --status-timer=5 2>&1"
            )
        else:  # john
            fmt_map = {
                "md5": "raw-md5", "sha1": "raw-sha1", "sha256": "raw-sha256",
                "sha512": "raw-sha512", "ntlm": "nt", "bcrypt": "bcrypt",
            }
            fmt = fmt_map.get(resolved_type, "")
            fmt_arg = f"--format={fmt}" if fmt else ""
            cmd = f"john {fmt_arg} --wordlist={wl_path} {tmp_name} 2>&1"

        crack_res = run_wsl_command(cmd, timeout=_HASHCRACK_TIMEOUT)
        dur       = round(time.time() - t0, 2)
        raw       = crack_res.get("stdout", "") or ""

        # Sonuç parse et
        cracked   = False
        plaintext = None
        speed     = ""

        # hashcat: "hash:plaintext" formatı
        for line in raw.splitlines():
            ls = line.strip()
            if ":" in ls and ls.lower().startswith(hash_value.lower()[:8]):
                parts = ls.split(":", 1)
                if len(parts) == 2 and parts[1]:
                    cracked   = True
                    plaintext = parts[1].strip()
                    break
            # hashcat status satırı
            if "speed" in ls.lower() or "speed.#1" in ls.lower():
                speed = ls.split(":", 1)[-1].strip()[:60]
            # john: "plaintext (hash)" formatı
            m = re.match(r"^(.+?)\s+\(", ls)
            if m and "Loaded" not in ls and "Session" not in ls:
                # john çıktısı genellikle "plaintext (username)" şeklinde
                # hash dosyasına username yazmadık, sadece hash var
                possible = m.group(1).strip()
                if possible and len(possible) < 100:
                    cracked   = True
                    plaintext = possible

        return {
            "hash":       hash_value[:64],
            "hash_type":  resolved_type,
            "cracked":    cracked,
            "plaintext":  plaintext,
            "tool_used":  tool,
            "wordlist":   wordlist,
            "duration":   dur,
            "speed":      speed,
            "raw_output": raw[:3000],
        }

    finally:
        # Temizlik — gecici dosyayi sil
        run_wsl_command(f"rm -f {tmp_name}", timeout=5)
