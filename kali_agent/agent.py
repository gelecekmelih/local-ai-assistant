#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kali_agent/agent.py — Kali Linux HTTP Köprüsü
==============================================
WSL2 içinde çalışan hafif HTTP sunucu.
Klargus (Windows) bu sunucuya istek gönderir,
sunucu Kali araçlarını çalıştırır ve sonucu döndürür.

Başlatma (Kali terminali):
  python3 /mnt/c/Users/youruser/jarvis/kali_agent/agent.py

Otomatik başlatma için:
  ~/.bashrc'ye ekle:
  nohup python3 /mnt/c/Users/youruser/jarvis/kali_agent/agent.py > /tmp/klargus_agent.log 2>&1 &

Port: 7474 (localhost only)
"""

import hmac
import http.server
import json
import os
import secrets
import subprocess
import threading
import time

HOST  = "127.0.0.1"
PORT  = 7474

RESULTS_DIR = "/mnt/c/Users/youruser/jarvis/data/kali_results"
os.makedirs(RESULTS_DIR, exist_ok=True)

# ── Kimlik doğrulama token'ı (2026-09-29) ─────────────────────────────────────
# ÖNCEDEN: sabit, kaynak koduna gömülü "klargus-kali-bridge" — her kurulumda AYNI,
# repo'da herkese açık → paylaşılan sır olarak SIFIR değeri. ARTIK: Windows tarafı
# (tools/wsl_tools.py) tarafından üretilen data/kali_agent_token.txt AYNI dosyayı
# bu mount (/mnt/c/...) üzerinden okuyarak paylaşılır — kuruluma özgü, tahmin
# edilemez. Windows tarafı henüz üretmediyse burası üretir (hangi taraf önce
# başlarsa o üretir; diğeri aynı dosyayı okur).
_TOKEN_FILE = "/mnt/c/Users/youruser/jarvis/data/kali_agent_token.txt"


def _load_or_create_token() -> str:
    try:
        if os.path.exists(_TOKEN_FILE):
            with open(_TOKEN_FILE, "r", encoding="utf-8") as f:
                tok = f.read().strip()
                if tok:
                    return tok
    except Exception:
        pass
    tok = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(_TOKEN_FILE), exist_ok=True)
        with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(tok)
    except Exception:
        pass
    return tok


TOKEN = _load_or_create_token()

# Aktif arka plan işlemleri
_jobs: dict = {}
_jobs_lock = threading.Lock()

# Tehlikeli komutlar — NOT: bu bir güvenlik SINIRI değil, kazara zarar için
# ikinci bir güvenlik katmanı. Asıl koruma X-Token kimlik doğrulamasıdır
# (127.0.0.1'e bağlı + kuruluma özgü rastgele token). Bu araç TASARIM GEREĞİ
# keyfi pentest komutları çalıştırır — sıkı bir izin listesi amacını bozar.
_BLOCKED = (
    "rm -rf /", "mkfs", "dd if=/dev/zero", ":(){:|:&};:", ":(){ :|:& };:",
    "shutdown", "reboot", "init 0", "init 6", "halt",
    "> /dev/sda", "wget http", "curl http",
)


def _safe(cmd: str) -> bool:
    # Boşluk/tab varyasyonlarını normalize et — "rm  -rf   /" gibi trivial
    # kaçışları da yakala (tam sandbox değil, ucuz bir sertleştirme).
    low = " ".join(cmd.lower().split())
    return not any(b in low for b in _BLOCKED)


def _run_cmd(cmd: str, timeout: int = 30) -> dict:
    """Komutu çalıştır ve sonuç sözlüğü döndür."""
    if not _safe(cmd):
        return {"ok": False, "error": "Güvenlik: engellenen komut kalıbı."}
    try:
        r = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
        return {
            "ok":       r.returncode == 0,
            "stdout":   r.stdout[:4000],
            "stderr":   r.stderr[:1000],
            "exitcode": r.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Zaman aşımı ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


_JOB_RETENTION_SECONDS = 6 * 3600   # tamamlanan iş kayıtları bu süreden sonra silinir


def _prune_old_jobs() -> None:
    """Tamamlanmış, _JOB_RETENTION_SECONDS'den eski işleri _jobs'tan ve
    sonuç dosyalarından temizle. Ajan günlerce açık kalabiliyor; eskiden
    her iş sonsuza dek bellekte/diskte birikiyordu (sızıntı)."""
    now = time.time()
    with _jobs_lock:
        stale = [jid for jid, j in _jobs.items()
                 if j.get("done") and (now - j.get("start", now)) > _JOB_RETENTION_SECONDS]
        for jid in stale:
            job = _jobs.pop(jid, None)
            if job:
                try:
                    os.remove(job["file"])
                except Exception:
                    pass


def _run_bg(cmd: str, job_id: str) -> None:
    """Komutu arka planda çalıştır."""
    _prune_old_jobs()
    out_file = f"{RESULTS_DIR}/{job_id}.txt"
    full_cmd = f"({cmd}) > {out_file} 2>&1"
    try:
        proc = subprocess.Popen(["bash", "-c", full_cmd])
        with _jobs_lock:
            _jobs[job_id] = {"pid": proc.pid, "done": False, "file": out_file, "start": time.time()}
        proc.wait()
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["done"]     = True
                _jobs[job_id]["exitcode"] = proc.returncode
        print(f"[Agent] Job '{job_id}' done (exit={proc.returncode})", flush=True)
    except Exception as e:
        print(f"[Agent] Job '{job_id}' error: {e}", flush=True)


class AgentHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # Sessiz log

    def _send_json(self, data: dict, code: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_token(self) -> bool:
        # Sabit-zamanlı karşılaştırma (2026-09-29) — '==' timing side-channel'a
        # açıktı (localhost-bound olsa da savunma derinliği).
        supplied = self.headers.get("X-Token", "")
        return hmac.compare_digest(supplied, TOKEN)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            try:
                return json.loads(self.rfile.read(length))
            except Exception:
                pass
        return {}

    def do_GET(self):
        if not self._check_token():
            self._send_json({"ok": False, "error": "Yetkisiz"}, 401)
            return

        if self.path == "/ping":
            self._send_json({"ok": True, "msg": "Kali agent aktif", "port": PORT})

        elif self.path == "/jobs":
            with _jobs_lock:
                self._send_json({"ok": True, "jobs": dict(_jobs)})

        elif self.path.startswith("/result/"):
            job_id = self.path[8:]
            with _jobs_lock:
                job = _jobs.get(job_id)
            if not job:
                self._send_json({"ok": False, "error": "İş bulunamadı"})
                return
            try:
                content = open(job["file"], encoding="utf-8", errors="replace").read()
                self._send_json({"ok": True, "done": job["done"], "content": content[:5000]})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})

        else:
            self._send_json({"ok": False, "error": "Bilinmeyen endpoint"}, 404)

    def do_POST(self):
        if not self._check_token():
            self._send_json({"ok": False, "error": "Yetkisiz"}, 401)
            return

        body = self._read_body()

        if self.path == "/run":
            cmd     = str(body.get("cmd", "")).strip()
            timeout = int(body.get("timeout", 30))
            if not cmd:
                self._send_json({"ok": False, "error": "cmd boş"})
                return
            result = _run_cmd(cmd, timeout)
            self._send_json(result)

        elif self.path == "/run_bg":
            cmd    = str(body.get("cmd", "")).strip()
            job_id = str(body.get("job_id", f"job_{int(time.time())}"))
            if not cmd:
                self._send_json({"ok": False, "error": "cmd boş"})
                return
            if not _safe(cmd):
                self._send_json({"ok": False, "error": "Güvenlik: engellenen komut."})
                return
            t = threading.Thread(target=_run_bg, args=(cmd, job_id), daemon=True)
            t.start()
            self._send_json({"ok": True, "job_id": job_id, "msg": "Arka planda başlatıldı"})

        elif self.path == "/install":
            pkg = "".join(
                c for c in str(body.get("package", ""))
                if c.isalnum() or c in ("-", "_", ".")
            )
            if not pkg:
                self._send_json({"ok": False, "error": "Paket adı boş"})
                return
            job_id = f"install_{pkg}_{int(time.time())}"
            t = threading.Thread(
                target=_run_bg,
                args=(f"apt-get install -y {pkg}", job_id),
                daemon=True,
            )
            t.start()
            self._send_json({"ok": True, "job_id": job_id, "msg": f"{pkg} kuruluyor..."})

        else:
            self._send_json({"ok": False, "error": "Bilinmeyen endpoint"}, 404)


def main():
    print(f"[KaliAgent] Başlıyor → {HOST}:{PORT}", flush=True)
    server = http.server.ThreadingHTTPServer((HOST, PORT), AgentHandler)
    print("[KaliAgent] Hazır. Token: data/kali_agent_token.txt", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[KaliAgent] Durduruluyor...", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
