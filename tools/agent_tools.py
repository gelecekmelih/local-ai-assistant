# -*- coding: utf-8 -*-
"""
tools/agent_tools.py — Agent Döngüsü Araçları
==============================================
Çok adımlı görevlerde LLM'in kullandığı yardımcı araçlar:
  - Ekran görüşü (screenshot + vision)
  - Bekleme + durum kontrolü
  - Shell komutu çalıştırma
  - Hedef doğrulama
"""

import subprocess
import time
from pathlib import Path

from brain.tool_result import ToolResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ─────────────────────────────────────────────────────────────────────────────
# 1) Ekran Görüşü — Screenshot + Vision Model
# ─────────────────────────────────────────────────────────────────────────────

def take_screenshot(arg: str = "") -> ToolResult:
    """
    Ekranın anlık görüntüsünü al ve vision + OCR ile kapsamlı analiz et.
    arg = analiz sorusu (boş = genel bakış)
    """
    question = str(arg or "").strip()

    # Birleşik ekran anlama motoru
    try:
        from vision.screen_understanding import understand_screen
        return understand_screen(question)  # ToolResult passthrough (.ok iç çıktıdan)
    except Exception as e:
        pass

    # Fallback: eski yöntem
    try:
        import base64
        import io
        from PIL import ImageGrab
        from brain.system_settings import get_system_setting
        import requests as _req

        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()

        host  = get_system_setting("ollama_host", "127.0.0.1")
        port  = get_system_setting("ollama_port", 11434)
        model = get_system_setting("vision_model", "llava:7b")
        q = question or "Ekranda tam olarak ne var? Türkçe kısaca açıkla."

        r = _req.post(
            f"http://{host}:{port}/api/generate",
            json={"model": model, "prompt": q, "images": [b64],
                  "stream": False, "keep_alive": "2m",
                  "options": {"num_predict": 300, "temperature": 0.1}},
            timeout=20,
        )
        r.raise_for_status()
        result = r.json().get("response", "").strip()
        if result:
            return ToolResult(message=result, ok=True)
    except Exception:
        pass

    # Son fallback: OCR
    try:
        from vision.screen_actions_v2 import read_screen_text_v2
        text = read_screen_text_v2()
        return ToolResult(message=f"[OCR] {text[:800]}" if text else "Ekran okunamadı.", ok=True)
    except Exception:
        return ToolResult(message="Ekran analiz edilemedi.", ok=False, error="analiz_edilemedi")


# ─────────────────────────────────────────────────────────────────────────────
# 2) Ekranda Metin Var Mı Kontrolü
# ─────────────────────────────────────────────────────────────────────────────

def check_screen_for(arg: str) -> ToolResult:
    """
    Ekranda belirli bir metin veya unsur var mı kontrol et.
    arg = aranacak metin (örn: "Hata", "Başarıyla kaydedildi", "OK butonu")
    Dönüş: "VAR: ..." veya "YOK"
    """
    if not arg or not arg.strip():
        return ToolResult(message="Aranacak metin belirtilmedi.", ok=True)

    # Yeni hızlı kontrol fonksiyonu
    try:
        from vision.screen_understanding import quick_screen_check
        return quick_screen_check(arg)  # ToolResult passthrough (.ok iç çıktıdan)
    except Exception:
        pass

    # Fallback: eski OCR yöntemi (VAR/YOK ikisi de geçerli sonuç → ok=True)
    try:
        from vision.screen_actions_v2 import read_screen_text_v2
        screen_text = read_screen_text_v2() or ""
        if arg.lower() in screen_text.lower():
            idx = screen_text.lower().find(arg.lower())
            context = screen_text[max(0, idx-40):idx+len(arg)+60].strip()
            return ToolResult(message=f"VAR: '{arg}' bulundu → ...{context}...", ok=True, data=True)
        return ToolResult(message=f"YOK: '{arg}' ekranda görünmüyor.", ok=True, data=False)
    except Exception as e:
        return ToolResult(message=f"Ekran kontrolü yapılamadı: {e}", ok=False, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 3) Bekleme
# ─────────────────────────────────────────────────────────────────────────────

def wait_seconds(arg: str = "1") -> ToolResult:
    """
    Belirtilen saniye kadar bekle (uygulama yüklenirken, animasyon bitince vb.).
    arg = saniye sayısı (max 10)
    """
    try:
        secs = float(str(arg or "1").strip())
        secs = max(0.1, min(secs, 10.0))   # 0.1–10 arası sınırla
        time.sleep(secs)
        return ToolResult(message=f"{secs:.1f} saniye beklendi.", ok=True)
    except Exception:
        time.sleep(1.0)
        return ToolResult(message="1 saniye beklendi.", ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 4) Shell Komutu Çalıştır
# ─────────────────────────────────────────────────────────────────────────────

# İzin verilen güvenli komutlar (whitelist) — LLM keyfi shell açmasın
_SAFE_COMMANDS = {
    "tasklist", "systeminfo", "ipconfig", "ping", "netstat", "dir",
    "whoami", "hostname", "ver", "echo", "type", "find", "findstr",
    "wmic", "powercfg", "diskpart", "chkdsk", "sfc",
}


def run_shell(arg: str) -> ToolResult:
    """
    Güvenli Windows shell komutu çalıştır.
    arg = komut (sadece whitelist'tekilere izin verilir)
    Sonucu döndürür.
    """
    if not arg or not arg.strip():
        return ToolResult(message="Komut belirtilmedi.", ok=True)

    cmd = str(arg).strip()
    base_cmd = cmd.split()[0].lower().removesuffix(".exe")

    if base_cmd not in _SAFE_COMMANDS:
        return ToolResult(message=(
            f"'{base_cmd}' komutu güvenli listede değil. "
            f"İzinli: {', '.join(sorted(_SAFE_COMMANDS))}"
        ), ok=True)

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="cp1254",
            errors="replace",
            timeout=10,
        )
        output = (result.stdout or result.stderr or "").strip()
        return ToolResult(message=output[:800] if output else "(çıktı yok)", ok=True, data=output)
    except subprocess.TimeoutExpired:
        return ToolResult(message="Komut zaman aşımına uğradı (10s).", ok=False, error="timeout")
    except Exception as e:
        return ToolResult(message=f"Komut çalıştırılamadı: {e}", ok=False, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 5) Hedef Doğrulama — LLM sonraki adıma geçmeden önce kontrol etsin
# ─────────────────────────────────────────────────────────────────────────────

def verify_goal(arg: str) -> ToolResult:
    """
    Ekranı okuyarak belirli bir hedefin gerçekleşip gerçekleşmediğini doğrula.
    arg = beklenen durum (örn: "Spotify penceresi açık", "Dosya kaydedildi")
    LLM bu sonucu görerek devam eder mi karar verir.
    """
    if not arg or not arg.strip():
        return ToolResult(message="Doğrulanacak hedef belirtilmedi.", ok=True)

    # Aktif uygulama bilgisi
    try:
        from vision.screen_understanding import get_active_app_info
        info = get_active_app_info()
        active = info.get("title", "") or info.get("app_name", "")
    except Exception:
        try:
            from brain.tool_registry import run_tool
            active = str(run_tool("active_window", "") or "").strip()
        except Exception:
            active = ""

    # OCR ile hızlı metin kontrolü
    try:
        from vision.screen_actions_v2 import read_screen_text_v2
        screen = (read_screen_text_v2() or "").strip()
    except Exception:
        screen = ""

    parts = []
    if active:
        parts.append(f"Aktif pencere: {active}")
    if screen:
        parts.append(f"Ekran içeriği (OCR): {screen[:500]}")

    context = "\n".join(parts) if parts else "Ekran bilgisi alınamadı."
    return ToolResult(message=f"[Durum]\n{context}\n\n[Doğrulanacak Hedef]: {arg}", ok=True)
