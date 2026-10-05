# -*- coding: utf-8 -*-
"""
tools/wsl_tools.py — WSL2 / Kali Linux Araç Katmanı (Windows Tarafı)
======================================================================
Klargus'un WSL2 üzerindeki Kali Linux'u yönetmesini sağlar.

Strateji:
  1. Anlık/kısa komutlar  → wsl.exe subprocess (direkt)
  2. Uzun işlemler        → arka plan thread + kali_results klasörü
  3. Kalıcı servis        → kali_agent HTTP bridge (port 7474)

Sesli komutlar:
  "kali durumu"
  "kali terminali aç"
  "kali'de nmap çalıştır 192.168.1.1"
  "kali agent başlat"
  "kali sonuçları göster"
"""

import secrets
import subprocess
import threading
import time
from pathlib import Path

from brain.tool_result import ToolResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR  = PROJECT_ROOT / "data" / "kali_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

DISTRO         = "kali-linux"
AGENT_PORT     = 7474
AGENT_SCRIPT   = "/mnt/c/Users/youruser/jarvis/kali_agent/agent.py"

# ── Agent kimlik doğrulama token'ı (2026-09-29) ───────────────────────────────
# ÖNCEDEN: sabit, kaynak koduna gömülü "klargus-kali-bridge" — her Klargus
# kurulumunda AYNI, herkese açık (repo'da) → paylaşılan sır olarak SIFIR değeri.
# ARTIK: ilk çalıştırmada rastgele üretilip data/kali_agent_token.txt'e yazılır
# (git'e commit edilmez — .gitignore'a eklendi); kali_agent/agent.py AYNI dosyayı
# WSL mount'u (/mnt/c/...) üzerinden okur → iki taraf da aynı, kuruluma-özgü,
# tahmin edilemez sırrı paylaşır.
_TOKEN_FILE = PROJECT_ROOT / "data" / "kali_agent_token.txt"


def _get_agent_token() -> str:
    """Kuruluma özgü agent token'ını oku, yoksa rastgele üret ve kaydet."""
    try:
        if _TOKEN_FILE.exists():
            tok = _TOKEN_FILE.read_text(encoding="utf-8").strip()
            if tok:
                return tok
    except Exception:
        pass
    tok = secrets.token_hex(32)
    try:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(tok, encoding="utf-8")
    except Exception:
        pass
    return tok

# Aktif arka plan işlemleri {job_id: {"proc": ..., "out_file": ...}}
_active_jobs: dict = {}
_jobs_lock = threading.Lock()


# ── Temel WSL2 yardımcıları ───────────────────────────────────────────────────

def is_kali_running() -> bool:
    """Kali Linux distrosu çalışıyor mu?"""
    try:
        r = subprocess.run(
            ["wsl", "--list", "--running"],
            capture_output=True, text=True, timeout=5,
            encoding="utf-16-le",  # wsl --list UTF-16 döndürür
        )
        return DISTRO in r.stdout
    except Exception:
        try:
            # Fallback: Kali'de echo çalıştır
            r = subprocess.run(
                ["wsl", "-d", DISTRO, "--", "echo", "ping"],
                capture_output=True, text=True, timeout=5,
            )
            return r.returncode == 0
        except Exception:
            return False


def _wsl_path(windows_path: str) -> str:
    """Windows yolunu WSL2 yoluna çevir: C:\\foo → /mnt/c/foo (İÇ YARDIMCI, araç değil)."""
    p = str(windows_path).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        rest  = p[2:].lstrip("/")
        return f"/mnt/{drive}/{rest}"
    return p


# ── Tehlikeli komut kalıpları (2026-10-03) ────────────────────────────────────
# ÖNCEDEN: bu liste sadece run_kali() içinde yerel bir değişkendi — run_kali_bg()
# aynı kontrolü yapmıyordu. tools/kali_tools.py'deki nmap/gobuster/nikto/sqlmap
# sarmalayıcılarının HEPSİ run_kali_bg() kullanıyor (run_kali() değil), yani
# gerçek ağır pentest araçları bu güvenlik ağının tamamen DIŞINDAYDI. Artık
# modül seviyesinde tek liste + paylaşılan kontrol — ikisi de aynı korumayı alır.
_BLOCKED_PATTERNS = ("rm -rf /", "mkfs", "dd if=", ":(){:|:&};:", "shutdown", "reboot",
                     "wget http", "curl http", "nc -l", "ncat -l")


def _blocked_reason(cmd: str) -> str | None:
    """cmd tehlikeli bir kalıp içeriyorsa o kalıbı, içermiyorsa None döndür."""
    low = cmd.lower()
    for b in _BLOCKED_PATTERNS:
        if b in low:
            return b
    return None


# ── Komut çalıştırıcı ────────────────────────────────────────────────────────

def run_kali(cmd: str, timeout: int = 30) -> ToolResult:
    """
    Kali Linux'ta komut çalıştır ve çıktıyı döndür.
    Kısa/anlık komutlar için (max 30s).
    """
    if not cmd or not cmd.strip():
        return ToolResult(message="Komut belirtilmedi.", ok=True)

    # Tehlikeli komutları engelle
    _blocked = _blocked_reason(cmd)
    if _blocked:
        return ToolResult(message=f"Güvenlik: '{_blocked}' içeren komut engellendi.", ok=True)

    try:
        r = subprocess.run(
            ["wsl", "-d", DISTRO, "--", "bash", "-c", cmd],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode != 0 and err:
            return ToolResult(message=f"Hata (kod {r.returncode}):\n{err[:800]}",
                              ok=False, error=f"exit_{r.returncode}")
        return ToolResult(message=out[:1500] if out else "(çıktı yok)", ok=True, data=out)
    except subprocess.TimeoutExpired:
        return ToolResult(
            message=f"Zaman aşımı ({timeout}s). Uzun işlemler için 'arka planda çalıştır' kullan.",
            ok=False, error="timeout")
    except FileNotFoundError:
        return ToolResult(message="wsl.exe bulunamadı — WSL2 kurulu mu?", ok=False, error="wsl_yok")
    except Exception as e:
        return ToolResult(message=f"WSL çalıştırma hatası: {e}", ok=False, error=str(e))


def run_kali_bg(cmd: str, job_name: str = "") -> ToolResult:
    """
    Uzun süren komutu arka planda çalıştır.
    Sonuç data/kali_results/{job_name}.txt dosyasına yazılır.
    """
    if not cmd or not cmd.strip():
        return ToolResult(message="Komut belirtilmedi.", ok=True)

    _blocked = _blocked_reason(cmd)
    if _blocked:
        return ToolResult(message=f"Güvenlik: '{_blocked}' içeren komut engellendi.", ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c for c in (job_name or "job") if c.isalnum() or c == "_")
    out_file = RESULTS_DIR / f"{safe_name}_{ts}.txt"
    job_id   = f"{safe_name}_{ts}"

    def _run():
        try:
            # Komutu Kali'de çalıştır, çıktıyı Windows'taki ortak klasöre yaz
            full_cmd = f"({cmd}) 2>&1 | tee {_wsl_path(str(out_file))}"
            proc = subprocess.Popen(
                ["wsl", "-d", DISTRO, "--", "bash", "-c", full_cmd],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
            with _jobs_lock:
                _active_jobs[job_id]["proc"] = proc
            proc.wait()
            with _jobs_lock:
                _active_jobs[job_id]["done"] = True
                _active_jobs[job_id]["exit"] = proc.returncode
            print(f"[KaliJob] '{job_id}' tamamlandı (kod {proc.returncode})", flush=True)
        except Exception as e:
            print(f"[KaliJob] '{job_id}' hata: {e}", flush=True)
            try:
                out_file.write_text(f"HATA: {e}", encoding="utf-8")
            except Exception:
                pass

    with _jobs_lock:
        _active_jobs[job_id] = {"proc": None, "out_file": str(out_file), "done": False, "exit": None}

    t = threading.Thread(target=_run, daemon=True, name=f"KaliJob-{job_id}")
    t.start()

    return ToolResult(message=(
        f"Kali işi başladı: '{job_id}'\n"
        f"Sonuç: data\\kali_results\\{out_file.name}\n"
        f"'kali sonuçları göster' komutuyla kontrol edebilirsin."
    ), ok=True, data=job_id)


# ── Araç fonksiyonları (tool_registry'e kayıtlı) ─────────────────────────────

def tool_kali_status(arg: str = "") -> ToolResult:
    """Kali Linux WSL2 durumunu göster."""
    running = is_kali_running()
    status  = "🟢 Çalışıyor" if running else "🔴 Durdu"

    # Agent durumu
    agent_ok = _check_agent()
    agent_st = "🟢 Aktif" if agent_ok else "⚫ Pasif"

    # Aktif işler
    with _jobs_lock:
        jobs = [(jid, j) for jid, j in _active_jobs.items()]
    active   = [(jid, j) for jid, j in jobs if not j.get("done")]
    finished = [(jid, j) for jid, j in jobs if j.get("done")]

    lines = [
        f"=== Kali Linux Durumu ===",
        f"WSL2 Distro:  {status}",
        f"HTTP Agent:   {agent_st} (:{AGENT_PORT})",
        f"Aktif işler:  {len(active)}",
        f"Tamamlanan:   {len(finished)}",
    ]
    if active:
        lines.append("Aktif işler:")
        for jid, j in active:
            lines.append(f"  • {jid}")
    return ToolResult(message="\n".join(lines), ok=True, data=running)


def tool_kali_terminal(arg: str = "") -> ToolResult:
    """Windows Terminal'da Kali Linux sekmesi aç."""
    try:
        subprocess.Popen(
            ["wt", "-d", ".", "wsl", "-d", DISTRO],
            shell=False,
        )
        return ToolResult(message="Kali Linux terminali açıldı.", ok=True)
    except FileNotFoundError:
        try:
            subprocess.Popen(["wsl", "-d", DISTRO])
            return ToolResult(
                message="Kali Linux terminali açıldı (Windows Terminal bulunamadı, varsayılan kullanıldı).",
                ok=True)
        except Exception as e:
            return ToolResult(message=f"Terminal açma hatası: {e}", ok=False, error=str(e))
    except Exception as e:
        return ToolResult(message=f"Terminal açma hatası: {e}", ok=False, error=str(e))


def tool_kali_run(arg: str = "") -> ToolResult:
    """Kali Linux'ta komut çalıştır. Örnek: 'nmap -sV 192.168.1.1'

    BİLİNÇLİ TASARIM (gözden kaçma değil): tool_kali_shell gibi SERBEST-komut aracı —
    kullanıcının TAM komutunu çalıştırır. Hedef-araçlarındaki shlex.quote savunması
    burada UYGULANMAZ (escape aracın amacını bozar; yapısal alternatif yok). Tek
    savunma run_kali içindeki _BLOCKED kara listesidir. Hedef-injection ayrıca
    kali_tools tarafında _q() ile kapatıldı.
    """
    if not arg.strip():
        return ToolResult(message="Çalıştırılacak komutu belirt. Örnek: nmap -sV 192.168.1.1", ok=True)
    # 15 saniyeden uzun sürecek komutlar → arka plana
    long_cmds = ("nmap", "hydra", "sqlmap", "gobuster", "dirb", "nikto", "wpscan")
    if any(arg.strip().startswith(c) for c in long_cmds):
        job_name = arg.strip().split()[0]
        return run_kali_bg(arg.strip(), job_name=job_name)
    return run_kali(arg.strip(), timeout=20)


def tool_kali_results(arg: str = "") -> ToolResult:
    """Kali sonuç dosyalarını listele veya belirli birini göster."""
    files = sorted(RESULTS_DIR.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return ToolResult(message="Henüz Kali sonuç dosyası yok.", ok=True, data=[])

    if arg.strip():
        # Belirli dosyayı ara
        target = arg.strip().lower()
        match  = next((f for f in files if target in f.name.lower()), None)
        if match:
            try:
                content = match.read_text(encoding="utf-8", errors="replace")
                return ToolResult(message=f"=== {match.name} ===\n{content[:2000]}",
                                  ok=True, data=match.name)
            except Exception as e:
                return ToolResult(message=f"Dosya okunamadı: {e}", ok=False, error=str(e))
        return ToolResult(message=f"'{arg}' adında sonuç bulunamadı.", ok=False, error="sonuc_yok")

    lines = [f"Son Kali sonuçları ({len(files)} dosya):"]
    for f in files[:10]:
        size = f.stat().st_size
        lines.append(f"  • {f.name}  ({size} bayt)")
    return ToolResult(message="\n".join(lines), ok=True, data=[f.name for f in files[:10]])


def tool_kali_install(arg: str = "") -> ToolResult:
    """Kali'de apt ile araç kur. Örnek: 'nmap'"""
    tool = arg.strip().split()[0] if arg.strip() else ""
    if not tool:
        return ToolResult(message="Kurulacak araç adını belirt.", ok=True)
    safe_name = "".join(c for c in tool if c.isalnum() or c in ("-", "_", "."))
    if not safe_name:
        return ToolResult(message="Geçersiz araç adı.", ok=True)
    return run_kali_bg(
        f"sudo apt-get install -y {safe_name}",
        job_name=f"install_{safe_name}",
    )


def tool_kali_agent_start(arg: str = "") -> ToolResult:
    """Kali HTTP agent'ını başlat (Klargus ↔ Kali köprüsü)."""
    if _check_agent():
        return ToolResult(message=f"Kali agent zaten çalışıyor (:{AGENT_PORT}).", ok=True)
    return run_kali_bg(
        f"python3 {AGENT_SCRIPT}",
        job_name="kali_agent",
    )


# ── Agent sağlık kontrolü ─────────────────────────────────────────────────────

def _check_agent() -> bool:
    """HTTP agent çalışıyor mu?"""
    try:
        import requests
        r = requests.get(
            f"http://127.0.0.1:{AGENT_PORT}/ping",
            headers={"X-Token": _get_agent_token()},
            timeout=1.5,
        )
        return r.status_code == 200
    except Exception:
        return False


def agent_call(endpoint: str, data: dict | None = None) -> dict:
    """Kali agent'ına HTTP isteği gönder."""
    try:
        import requests
        r = requests.post(
            f"http://127.0.0.1:{AGENT_PORT}{endpoint}",
            json=data or {},
            headers={"X-Token": _get_agent_token()},
            timeout=10,
        )
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Tool registry kayıtları ───────────────────────────────────────────────────

from brain.tool_registry import register_tool

register_tool("kali_status",       tool_kali_status)
register_tool("kali_terminal",     tool_kali_terminal)
register_tool("kali_run",          tool_kali_run)
register_tool("kali_results",      tool_kali_results)
register_tool("kali_install",      tool_kali_install)
register_tool("kali_agent_start",  tool_kali_agent_start)
