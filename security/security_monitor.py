import ipaddress
import json
from core.logger import get_logger
import re
import socket
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from core.safe_mode import is_safe_mode
from brain.tool_result import ToolResult

try:
    import psutil
except Exception:  # pragma: no cover - defensive fallback for broken envs
    psutil = None

log = get_logger("security_monitor")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "security_monitor_state.json"
LAST_REPORT_FILE = DATA_DIR / "security_last_report.txt"

# Kişisel/mutlak yol gömmemek için proje kökü çalışma-anında türetilir
# (taşınabilirlik + gizlilik). exe yolları küçük harfle karşılaştırılır.
_PROJECT_ROOT_STR = str(PROJECT_ROOT).lower()

# run_security_check son çalıştığında hesapladığı risk özetini burada tutar;
# ToolResult sarmalı (security_check_tool) bunu data alanına koyar. Salt bellek —
# diske YAZILMAZ, safe_mode'dan etkilenmez.
_LAST_RUN_META: Dict[str, Any] = {}

HIGH_RISK_NAMES = {
    "mimikatz.exe",
    "meterpreter.exe",
    "cobaltstrike.exe",
    "beacon.exe",
    "rubeus.exe",
}

ADMIN_TOOL_NAMES = {
    "psexec.exe",
    "procdump.exe",
    "nc.exe",
    "ncat.exe",
    "netcat.exe",
    "plink.exe",
}

SCRIPT_HOSTS = {
    "powershell.exe",
    "pwsh.exe",
    "cmd.exe",
    "wscript.exe",
    "cscript.exe",
    "mshta.exe",
    "rundll32.exe",
    "regsvr32.exe",
    "certutil.exe",
    "bitsadmin.exe",
}

SUSPICIOUS_CMD_RULES = [
    (r"-enc(?:odedcommand)?\b", "encoded komut kullanımı", 18),
    (r"frombase64string", "base64 çözme davranışı", 16),
    (r"downloadstring", "uzaktan script indirme", 18),
    (r"invoke-webrequest", "web üzerinden indirme", 12),
    (r"invoke-expression", "dinamik komut çalıştırma", 18),
    (r"\biwr\b", "kısaltılmış web indirme komutu", 10),
    (r"\biex\b", "kısaltılmış dinamik çalıştırma komutu", 18),
    (r"\b-nop\b", "profil yüklemeden shell çalıştırma", 10),
    (r"\b-windowstyle\s+hidden\b", "gizli pencere parametresi", 16),
    (r"\b-bypass\b", "policy bypass parametresi", 16),
    (r"\burlcache\b", "certutil indirme/cache davranışı", 16),
]

SUSPICIOUS_LOCATIONS = [
    "\\appdata\\local\\temp\\",
    "\\downloads\\",
    "\\users\\public\\",
]

TRUSTED_PATH_PREFIXES = [
    "c:\\windows\\",
    "c:\\program files\\",
    "c:\\program files (x86)\\",
    "c:\\programdata\\microsoft\\windows defender\\",
]

SUSPICIOUS_REMOTE_PORTS = {4444, 5555, 6666, 6667, 6668, 6669, 9001, 31337}
COMMON_WEB_PORTS = {80, 443}
COMMON_INFRA_PORTS = {53, 67, 68, 123, 137, 138, 139, 445, 853}

EXPECTED_CHURN_PROCESS_NAMES = {
    "backgroundtaskhost.exe",
    "conhost.exe",
    "dllhost.exe",
    "runtimebroker.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "textinputhost.exe",
    "widgetservice.exe",
    "widgets.exe",
    "brave.exe",
    "chrome.exe",
    "msedge.exe",
    "codex.exe",
    "python.exe",
    "pythonw.exe",
    "powershell.exe",
}


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load_state() -> Dict[str, Any]:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_state(data: Dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_state(updates: Dict[str, Any]) -> None:
    if is_safe_mode():
        # Test/güvenli mod: baseline'ı diske YAZMA — data/ kirlenmesin.
        # Okuma serbest; yalnız kalıcı yazım atlanır (baseline bellekte kalır).
        return
    state = _load_state()
    state.update(updates)
    _save_state(state)


def _clip(text: Any, limit: int = 180) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _safe_join_cmdline(cmdline: Any) -> str:
    if isinstance(cmdline, (list, tuple)):
        return " ".join(str(x) for x in cmdline if str(x).strip())
    return str(cmdline or "")


def _proc_key(proc: Dict[str, Any]) -> str:
    created = int(float(proc.get("create_time", 0.0) or 0.0))
    return f"{proc.get('pid')}:{created}:{str(proc.get('name', '')).lower()}"


def _risk_label(score: int) -> str:
    if score >= 45:
        return "yüksek"
    if score >= 18:
        return "orta"
    return "düşük"


def _is_trusted_path(path: str) -> bool:
    value = str(path or "").lower()
    return bool(value and any(value.startswith(prefix) for prefix in TRUSTED_PATH_PREFIXES))


def _process_trust_level(proc: Dict[str, Any]) -> str:
    exe = str(proc.get("exe", "") or "").lower()
    if exe.startswith("c:\\windows\\"):
        return "windows"
    if exe.startswith("c:\\program files\\") or exe.startswith("c:\\program files (x86)\\"):
        return "program-files"
    if exe.startswith("c:\\programdata\\microsoft\\windows defender\\"):
        return "defender"
    if "\\appdata\\local\\programs\\" in exe or "\\users\\" in exe:
        return "user-space"
    if exe:
        return "unknown-path"
    return "no-path"


def _is_expected_churn_process(proc: Dict[str, Any]) -> bool:
    name = str(proc.get("name", "") or "").lower()
    exe = str(proc.get("exe", "") or "").lower()
    if name not in EXPECTED_CHURN_PROCESS_NAMES:
        return False
    if name in {"powershell.exe", "python.exe", "pythonw.exe"}:
        # Proje kökü (veya venv) altından çalışan yorumlayıcı/shell = beklenen.
        # Yol çalışma-anında PROJECT_ROOT'tan türetilir (kişisel mutlak yol gömülmez).
        return _PROJECT_ROOT_STR in exe or ".venv\\scripts\\" in exe
    return _is_trusted_path(exe)


def _is_notable_new_process(proc: Dict[str, Any], reasons: List[str]) -> bool:
    if reasons:
        return True
    if _is_expected_churn_process(proc):
        return False
    exe = str(proc.get("exe", "") or "").lower()
    if not exe or _is_trusted_path(exe):
        return False
    return any(location in exe for location in SUSPICIOUS_LOCATIONS)


def _is_private_or_local(ip: str) -> bool:
    try:
        parsed = ipaddress.ip_address(ip)
        return bool(
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_multicast
            or parsed.is_reserved
        )
    except Exception:
        return True


def _ip_scope(ip: str) -> str:
    try:
        parsed = ipaddress.ip_address(ip)
        if parsed.is_loopback:
            return "loopback"
        if parsed.is_private:
            return "private"
        if parsed.is_link_local:
            return "link-local"
        if parsed.is_multicast:
            return "multicast"
        if parsed.is_reserved:
            return "reserved"
        return "public"
    except Exception:
        return "unknown"


def _reverse_dns(ip: str, timeout: float = 0.22) -> str:
    if not ip or _is_private_or_local(ip):
        return ""

    def lookup() -> str:
        try:
            return socket.gethostbyaddr(ip)[0]
        except Exception:
            return ""

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(lookup)
        return future.result(timeout=timeout)
    except TimeoutError:
        return ""
    except Exception:
        return ""
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def list_processes() -> List[Dict[str, Any]]:
    if psutil is None:
        return []

    processes: List[Dict[str, Any]] = []
    attrs = ["pid", "ppid", "name", "exe", "cmdline", "username", "create_time", "status"]
    for proc in psutil.process_iter(attrs=attrs):
        info = dict(getattr(proc, "info", {}) or {})
        try:
            memory_mb = proc.memory_info().rss / (1024 * 1024)
        except Exception:
            memory_mb = 0.0
        processes.append({
            "pid": int(info.get("pid", 0) or 0),
            "ppid": int(info.get("ppid", 0) or 0),
            "name": str(info.get("name", "") or ""),
            "exe": str(info.get("exe", "") or ""),
            "cmdline": _safe_join_cmdline(info.get("cmdline", "")),
            "username": str(info.get("username", "") or ""),
            "create_time": float(info.get("create_time", 0.0) or 0.0),
            "status": str(info.get("status", "") or ""),
            "trust": _process_trust_level(info),
            "memory_mb": round(float(memory_mb), 1),
        })

    names = {int(item.get("pid", 0) or 0): str(item.get("name", "") or "") for item in processes}
    for item in processes:
        item["parent_name"] = names.get(int(item.get("ppid", 0) or 0), "")

    return sorted(processes, key=lambda item: (str(item.get("name", "")).lower(), int(item.get("pid", 0))))


def _process_reasons(proc: Dict[str, Any]) -> Tuple[int, List[str]]:
    name = str(proc.get("name", "") or "").lower()
    exe = str(proc.get("exe", "") or "").lower()
    cmdline = str(proc.get("cmdline", "") or "").lower()
    haystack = " ".join([name, exe, cmdline])
    reasons: List[str] = []
    score = 0
    trusted_path = _is_trusted_path(exe)

    if name in HIGH_RISK_NAMES:
        score += 45
        reasons.append("yüksek riskli araç adı")

    if name in ADMIN_TOOL_NAMES:
        score += 18
        reasons.append("saldırı testlerinde de kullanılan yönetim aracı")

    for pattern, label, weight in SUSPICIOUS_CMD_RULES:
        if re.search(pattern, haystack, flags=re.IGNORECASE):
            score += weight
            reasons.append(label)

    if name in SCRIPT_HOSTS and any(token in cmdline for token in ["http://", "https://", "-enc", "hidden", "bypass"]):
        score += 16
        reasons.append("script host olağandışı parametrelerle çalışıyor")

    if not trusted_path and exe.endswith(".exe") and any(location in exe for location in SUSPICIOUS_LOCATIONS):
        score += 10
        reasons.append("alışılmadık çalışma konumu")

    if not trusted_path and name.endswith(".exe"):
        stem = name[:-4]
        if len(stem) >= 12 and re.fullmatch(r"[a-z0-9]+", stem) and len(set(stem)) >= 8:
            score += 6
            reasons.append("rastgele görünümlü process adı")

    if trusted_path and score <= 10:
        return 0, []

    return score, reasons


def find_suspicious_processes(processes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings = []
    for proc in processes:
        score, reasons = _process_reasons(proc)
        if not reasons:
            continue
        finding = dict(proc)
        finding["score"] = score
        finding["severity"] = _risk_label(score)
        finding["reasons"] = reasons
        findings.append(finding)

    return sorted(findings, key=lambda item: int(item.get("score", 0)), reverse=True)


def _pid_name_map(processes: Iterable[Dict[str, Any]]) -> Dict[int, str]:
    return {int(p.get("pid", 0) or 0): str(p.get("name", "") or "") for p in processes}


def list_network_connections(processes: Iterable[Dict[str, Any]], dns_limit: int = 12) -> List[Dict[str, Any]]:
    if psutil is None:
        return []

    names = _pid_name_map(processes)
    raw_connections = []
    try:
        raw_connections = psutil.net_connections(kind="inet")
    except Exception:
        return []

    connections: List[Dict[str, Any]] = []
    public_dns_lookups = 0
    for conn in raw_connections:
        local_ip = ""
        local_port = 0
        remote_ip = ""
        remote_port = 0

        if conn.laddr:
            local_ip = str(conn.laddr.ip)
            local_port = int(conn.laddr.port)
        if conn.raddr:
            remote_ip = str(conn.raddr.ip)
            remote_port = int(conn.raddr.port)

        domain = ""
        if remote_ip and not _is_private_or_local(remote_ip) and public_dns_lookups < dns_limit:
            public_dns_lookups += 1
            domain = _reverse_dns(remote_ip)

        pid = int(conn.pid or 0)
        connections.append({
            "pid": pid,
            "process": names.get(pid, "-") if pid else "-",
            "status": str(conn.status or ""),
            "local_ip": local_ip,
            "local_port": local_port,
            "remote_ip": remote_ip,
            "remote_port": remote_port,
            "domain": domain,
            "remote_scope": _ip_scope(remote_ip) if remote_ip else "",
        })

    return connections


def analyze_network(connections: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    connections = list(connections)
    external = [
        c for c in connections
        if c.get("remote_ip") and c.get("remote_scope") == "public"
    ]
    established = [c for c in connections if str(c.get("status", "")).upper() == "ESTABLISHED"]
    repeated_counter = Counter(
        (str(c.get("process", "-")), str(c.get("remote_ip", "")), int(c.get("remote_port", 0) or 0))
        for c in external
    )
    repeated = []
    suspicious_repeated = []
    for (proc, ip, port), count in repeated_counter.items():
        if not ip or count < 3:
            continue
        item = {
            "process": proc,
            "remote_ip": ip,
            "remote_port": port,
            "count": count,
            "suspicious": port not in COMMON_WEB_PORTS and port not in COMMON_INFRA_PORTS,
        }
        repeated.append(item)
        if item["suspicious"] or count >= 10:
            suspicious_repeated.append(item)
    suspicious_ports = [
        c for c in external
        if int(c.get("remote_port", 0) or 0) in SUSPICIOUS_REMOTE_PORTS
    ]

    return {
        "total": len(connections),
        "external": len(external),
        "established": len(established),
        "repeated": sorted(repeated, key=lambda item: item["count"], reverse=True),
        "suspicious_repeated": sorted(suspicious_repeated, key=lambda item: item["count"], reverse=True),
        "suspicious_ports": suspicious_ports,
    }


def compare_new_processes(processes: List[Dict[str, Any]]) -> Dict[str, Any]:
    state = _load_state()
    previous_keys = set(state.get("process_keys", []) or [])
    current_keys = [_proc_key(proc) for proc in processes]
    current_set = set(current_keys)

    new_processes = []
    notable_new_processes = []
    if previous_keys:
        for proc in processes:
            if _proc_key(proc) not in previous_keys:
                new_processes.append(proc)
                score, reasons = _process_reasons(proc)
                if _is_notable_new_process(proc, reasons):
                    enriched = dict(proc)
                    enriched["score"] = score
                    enriched["severity"] = _risk_label(score)
                    enriched["reasons"] = reasons
                    notable_new_processes.append(enriched)

    _update_state({
        "updated_at": _now_text(),
        "process_keys": sorted(current_set),
    })

    return {
        "has_baseline": bool(previous_keys),
        "new_processes": new_processes,
        "notable_new_processes": notable_new_processes,
    }


def _format_process(proc: Dict[str, Any]) -> str:
    reasons = ", ".join(proc.get("reasons", [])[:3])
    cmd = _clip(proc.get("cmdline") or proc.get("exe") or "-", 130)
    return (
        f"- [{proc.get('severity', 'düşük')}] {proc.get('name') or '-'} "
        f"(PID {proc.get('pid')}, parent={proc.get('parent_name') or '-'}) | "
        f"{reasons or 'neden yok'} | trust={proc.get('trust') or '-'} | {cmd}"
    )


def _format_connection(conn: Dict[str, Any]) -> str:
    remote = "-"
    if conn.get("remote_ip"):
        remote = f"{conn.get('remote_ip')}:{conn.get('remote_port')}"
    domain = conn.get("domain") or "-"
    count = int(conn.get("count", 1) or 1)
    repeat = f" x{count}" if count > 1 else ""
    return (
        f"- {conn.get('process') or '-'} (PID {conn.get('pid') or '-'}){repeat} "
        f"{conn.get('status') or '-'} -> {remote} | domain={domain} | scope={conn.get('remote_scope') or '-'}"
    )


def _format_new_process(proc: Dict[str, Any]) -> str:
    score = int(proc.get("score", 0) or 0)
    reasons = ", ".join(proc.get("reasons", [])[:2]) or "yeni process"
    marker = _risk_label(score) if score else "bilgi"
    return (
        f"- [{marker}] {proc.get('name') or '-'} (PID {proc.get('pid')}) | "
        f"{reasons} | trust={proc.get('trust') or '-'} | "
        f"{_clip(proc.get('exe') or proc.get('cmdline') or '-', 120)}"
    )


def _top_connections(connections: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    grouped = {}
    for conn in connections:
        key = (
            conn.get("pid"),
            conn.get("process"),
            conn.get("status"),
            conn.get("remote_ip"),
            conn.get("remote_port"),
        )
        if key not in grouped:
            grouped[key] = dict(conn)
            grouped[key]["count"] = 1
        else:
            grouped[key]["count"] += 1
            if not grouped[key].get("domain") and conn.get("domain"):
                grouped[key]["domain"] = conn.get("domain")

    scored = []
    for conn in grouped.values():
        score = 0
        if conn.get("remote_scope") == "public":
            score += 10
        if str(conn.get("status", "")).upper() == "ESTABLISHED":
            score += 6
        if int(conn.get("remote_port", 0) or 0) in SUSPICIOUS_REMOTE_PORTS:
            score += 8
        if conn.get("remote_ip"):
            score += 2
        score += min(4, int(conn.get("count", 1) or 1) - 1)
        scored.append((score, conn))
    return [item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:limit]]


def _build_recommendations(
    suspicious_processes: List[Dict[str, Any]],
    network: Dict[str, Any],
    process_delta: Dict[str, Any],
) -> List[str]:
    recommendations = []
    high = [p for p in suspicious_processes if p.get("severity") == "yüksek"]
    medium = [p for p in suspicious_processes if p.get("severity") == "orta"]
    notable_new = process_delta.get("notable_new_processes", [])

    if high:
        recommendations.append("Yüksek riskli process adları görüldü; manuel doğrulama öncelikli olmalı.")
    if medium:
        recommendations.append("Orta riskli processler var; komut satırı ve parent process bilgisi kontrol edilmeli.")
    if notable_new:
        recommendations.append("Son kontrolden beri dikkat çeken yeni process var; beklenen uygulama mı doğrulanmalı.")
    if network.get("suspicious_ports"):
        recommendations.append("Nadir kullanılan uzak port bağlantıları incelenmeli.")
    if network.get("suspicious_repeated"):
        recommendations.append("Web dışı portlarda tekrar eden dış bağlantılar gözden geçirilmeli.")
    if not recommendations:
        recommendations.append("Belirgin alarm yok; periyodik kontrol yeterli görünüyor.")
    recommendations.append("Otomatik müdahale yapılmadı; kapatma veya izolasyon gerekirse ayrıca onay alınmalı.")
    return recommendations


def _save_last_report(report: str) -> None:
    if is_safe_mode():
        # Test/güvenli mod: son raporu diske YAZMA (data/ kirlenmesin).
        return
    try:
        LAST_REPORT_FILE.write_text(report, encoding="utf-8")
    except Exception:
        pass


def get_last_security_report() -> str:
    try:
        if LAST_REPORT_FILE.exists():
            return LAST_REPORT_FILE.read_text(encoding="utf-8")
    except Exception:
        pass
    return "Henüz kayıtlı güvenlik raporu yok."


def run_security_check(mode: str = "standard") -> str:
    global _LAST_RUN_META
    mode = str(mode or "standard").lower().strip()
    _LAST_RUN_META = {"mode": mode}

    if mode in {"last", "son"}:
        return get_last_security_report()

    if psutil is None:
        _LAST_RUN_META = {"mode": mode, "error": "psutil yok", "risk": "bilinmiyor"}
        return (
            "Güvenlik kontrolü yapılamadı: psutil paketi bulunamadı.\n"
            "Müdahale yapılmadı."
        )

    processes = list_processes()
    suspicious_processes = find_suspicious_processes(processes)
    process_delta = compare_new_processes(processes)
    connections = list_network_connections(processes)
    network = analyze_network(connections)

    risk_score = 0
    risk_drivers = []
    for proc in suspicious_processes:
        severity = proc.get("severity")
        if severity == "yüksek":
            risk_score += 35
            risk_drivers.append(f"yüksek process: {proc.get('name')}")
        elif severity == "orta":
            risk_score += 18
            risk_drivers.append(f"orta process: {proc.get('name')}")
        else:
            risk_score += 6
    notable_new_count = len(process_delta.get("notable_new_processes", []))
    if notable_new_count:
        risk_score += min(16, notable_new_count * 4)
        risk_drivers.append(f"dikkat çeken yeni process: {notable_new_count}")
    if network.get("suspicious_repeated"):
        risk_score += min(20, len(network.get("suspicious_repeated", [])) * 8)
        risk_drivers.append("web dışı tekrar eden dış bağlantı")
    risk_score += min(18, len(network.get("suspicious_ports", [])) * 9)
    if network.get("suspicious_ports"):
        risk_drivers.append("dikkat çeken uzak port")
    risk = _risk_label(risk_score)
    recommendations = _build_recommendations(suspicious_processes, network, process_delta)

    # Risk özetini bellek-içi meta'ya yaz (ToolResult sarmalı data alanına koyar).
    _LAST_RUN_META = {
        "mode": mode,
        "risk": risk,
        "risk_score": risk_score,
        "process_count": len(processes),
        "connection_count": len(connections),
        "external_connections": network.get("external", 0),
        "established_connections": network.get("established", 0),
        "suspicious_process_count": len(suspicious_processes),
        "notable_new_process_count": notable_new_count,
        "risk_drivers": risk_drivers[:5],
    }

    lines = [
        "Klargus güvenlik kontrolü tamamlandı. Bu sadece pasif izleme raporudur; müdahale yapılmadı.",
        f"Zaman: {_now_text()}",
        "",
        "Sistem durumu:",
        f"- Process sayısı: {len(processes)}",
        f"- Ağ bağlantısı: {len(connections)}",
        f"- Dış bağlantı: {network.get('external', 0)}",
        f"- Established bağlantı: {network.get('established', 0)}",
        f"- Risk seviyesi: {risk}",
        f"- Risk puanı: {risk_score}",
        f"- Ana gerekçeler: {', '.join(risk_drivers[:5]) if risk_drivers else 'belirgin alarm yok'}",
        "",
        "Önerilen dikkat noktaları:",
    ]
    lines.extend(f"- {item}" for item in recommendations)

    if mode in {"short", "kisa", "özet", "ozet"}:
        report = "\n".join(lines)
        _save_last_report(report)
        _update_state({
            "last_risk": risk,
            "last_risk_score": risk_score,
            "last_report_at": _now_text(),
        })
        return report

    lines.extend([
        "",
        "Şüpheli processler:",
    ])

    if suspicious_processes:
        lines.extend(_format_process(proc) for proc in suspicious_processes[:8])
    else:
        lines.append("- Belirgin şüpheli process bulunmadı.")

    lines.append("")
    lines.append("Yeni process takibi:")
    if not process_delta.get("has_baseline"):
        lines.append("- İlk güvenlik kontrolü olduğu için baseline alındı. Bir sonraki kontrolde yeni processler karşılaştırılacak.")
    else:
        notable_new = process_delta.get("notable_new_processes", [])[:10]
        if notable_new:
            lines.extend(_format_new_process(proc) for proc in notable_new)
        elif mode in {"detailed", "detayli", "detaylı", "full"} and process_delta.get("new_processes"):
            lines.extend(_format_new_process(proc) for proc in process_delta.get("new_processes", [])[:20])
        else:
            total_new = len(process_delta.get("new_processes", []) or [])
            if total_new:
                lines.append(f"- {total_new} yeni process görüldü ama beklenen sistem/uygulama hareketi olarak sınıflandı.")
            else:
                lines.append("- Son kontrolden beri yeni process görülmedi.")

    lines.append("")
    lines.append("Ağ bağlantıları:")
    top_connections = _top_connections(connections, limit=10)
    if top_connections:
        lines.extend(_format_connection(conn) for conn in top_connections)
    else:
        lines.append("- Aktif bağlantı okunamadı veya bağlantı yok.")

    lines.append("")
    lines.append("Tekrar eden dış bağlantılar:")
    repeated = (
        network.get("repeated", [])[:12]
        if mode in {"detailed", "detayli", "detaylı", "full"}
        else network.get("suspicious_repeated", [])[:8]
    )
    if repeated:
        for item in repeated:
            note = "dikkat" if item.get("suspicious") else "normal web/altyapı olabilir"
            lines.append(f"- {item['process']} -> {item['remote_ip']}:{item['remote_port']} | tekrar={item['count']} | {note}")
    else:
        lines.append("- Şüpheli tekrar eden dış bağlantı görülmedi.")

    if network.get("suspicious_ports"):
        lines.append("")
        lines.append("Dikkat çeken portlar:")
        for conn in network["suspicious_ports"][:6]:
            lines.append(_format_connection(conn))

    lines.append("")
    lines.append("Not: Bu rapor sezgisel bir kontroldür; kesin zararlı yazılım tespiti değildir.")
    report = "\n".join(lines)
    _save_last_report(report)
    _update_state({
        "last_risk": risk,
        "last_risk_score": risk_score,
        "last_report_at": _now_text(),
    })
    return report


def security_check_tool(mode: str = "standard") -> ToolResult:
    """Dış giriş noktası (LLM-tool / run_tool için). run_security_check'in str
    raporunu ToolResult'a sarar; risk özetini data alanına koyar. İç fonksiyonlar
    (list_processes vb.) ToolResult'a ÇEVRİLMEZ — onlar iç API'dir.

    5C'de run_tool'a kaydedilecek. Key-command yolu doğrudan run_security_check
    kullanmaya devam eder (str dönüşü bozulmadı)."""
    report = run_security_check(mode)
    data = dict(_LAST_RUN_META)
    data.setdefault("mode", str(mode or "standard").lower().strip())
    ok = "error" not in data
    return ToolResult(message=report, ok=ok, data=data)
