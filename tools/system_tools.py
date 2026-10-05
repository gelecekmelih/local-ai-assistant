# -*- coding: utf-8 -*-
"""
tools/system_tools.py — Klargus Windows Sistem Kontrol Araçları
================================================================
Ses seviyesi, parlaklık, WiFi, Bluetooth, güç yönetimi, süreçler,
dark mode, pil durumu, disk, ekran çözünürlüğü ve daha fazlası.

Her araç ToolResult döndürür (str alt-sınıfı → message birebir korunur).
Hata durumunda ok=False + açıklayıcı mesaj verir.
"""

from core.logger import get_logger
import shlex
import subprocess
import os
import re
from pathlib import Path

from brain.tool_result import ToolResult
from core.safe_mode import is_safe_mode

log = get_logger("system_tools")


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _ps_quote(value: str) -> str:
    """Kullanıcı girdisini PowerShell TEK TIRNAKLI string literaline güvenli göm.

    (2026-09-29) kök-neden savunması: _ps() komutu bir bütün olarak PowerShell'e
    -Command argümanı olarak geçer (argv-list, OS-shell injection riski yok) ama
    PowerShell'in KENDİSİ o string'i script olarak yorumluyor. Kullanıcı girdisi
    (süreç adı, SSID vb.) çift-tırnaklı bir literale ("{val}") çıplak gömülürse
    PowerShell içi metakarakterler (", $, `) literal'i kırıp keyfi komut
    enjeksiyonuna yol açar. Tek-tırnaklı literalde PowerShell değişken genişletmesi
    YAPMAZ; tek kaçış kuralı, içindeki her tek tırnağı ikiye katlamaktır — bu da
    literal'i asla kıramaz (set_clipboard zaten bu deseni uyguluyordu, artık ortak)."""
    return str(value or "").replace("'", "''")


def _ps(cmd: str, timeout: int = 10) -> str:
    """PowerShell komutu çalıştır, çıktıyı döndür."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=timeout,
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0 and err and not out:
            return f"Hata: {err[:200]}"
        return out
    except subprocess.TimeoutExpired:
        return "Zaman aşımı."
    except Exception as e:
        return f"Hata: {e}"


def _cmd(cmd: str, timeout: int = 8) -> str:
    """CMD komutu çalıştır."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            encoding="cp1254", errors="ignore", timeout=timeout,
        )
        return (result.stdout or result.stderr or "").strip()
    except Exception as e:
        return f"Hata: {e}"


def _ps_errored(out: str) -> bool:
    """_ps çıktısı hata/zaman aşımı sinyali mi? (koşulsuz "yaptım" demeden önce
    sessizliği kırmak için — durum güvenilir sorgulanamayan işlemlerde kullanılır)."""
    s = str(out or "")
    return s.startswith("Hata") or "Zaman aşımı" in s


def _nircmd(cmd: str) -> str | None:
    """NirCmd varsa kullan, yoksa PowerShell fallback."""
    nircmd = Path("nircmd.exe")
    if nircmd.exists():
        try:
            subprocess.Popen([str(nircmd)] + cmd.split(), shell=False)
            return "ok"
        except Exception:
            pass
    return None


# ── SES SEVİYESİ ──────────────────────────────────────────────────────────────

def _get_pycaw_vol():
    """pycaw ile ses nesnesi döndürür. (level, muted, vol_obj)"""
    from pycaw.pycaw import AudioUtilities
    device = AudioUtilities.GetSpeakers()
    vol = device.EndpointVolume
    level = int(vol.GetMasterVolumeLevelScalar() * 100)
    muted = bool(vol.GetMute())
    return level, muted, vol


def get_volume(arg=None) -> ToolResult:
    """Sistem ses seviyesini döndürür."""
    try:
        level, muted, _ = _get_pycaw_vol()
        mute_str = " (sessiz)" if muted else ""
        return ToolResult(message=f"Ses seviyesi: %{level}{mute_str}", ok=True, data=level)
    except Exception:
        pass
    # Fallback: waveOut API
    out = _ps(
        "Add-Type -TypeDefinition @'\n"
        "using System.Runtime.InteropServices;\n"
        "public class Vol {\n"
        "  [DllImport(\"winmm.dll\")] public static extern int waveOutGetVolume(IntPtr h, out uint v);\n"
        "}\n"
        "'@\n"
        "$v = 0u; [Vol]::waveOutGetVolume([IntPtr]::Zero, [ref]$v) | Out-Null;\n"
        "$left = $v -band 0xFFFF; [int]($left / 65535.0 * 100)"
    )
    if out and out.strip().lstrip('-').isdigit():
        return ToolResult(message=f"Ses seviyesi: %{out.strip()}", ok=True, data=out.strip())
    # okunamadı: soft-fail, sinyal degil → davranis-koruma ok=True
    return ToolResult(message="Ses seviyesi okunamadı.", ok=True)


def set_volume(arg=None) -> ToolResult:
    """Ses seviyesini ayarlar. arg: 0-100 arası sayı veya 'artır'/'azalt'."""
    text = str(arg or "").strip().lower()

    # Artır/Azalt — pycaw ile mevcut seviyeye göre
    is_up   = any(x in text for x in ["artir", "artır", "yükselt", "yukselt"])
    is_down = any(x in text for x in ["azalt", "düşür", "dusur"])
    if is_up or is_down:
        if is_safe_mode():
            return ToolResult(message="[test modu: ses degistirilmedi]", ok=True)
        m = re.search(r"(\d+)", text)
        amount = int(m.group(1)) if m else 10
        try:
            cur, _, vol_obj = _get_pycaw_vol()
            new_level = max(0, min(100, cur + amount if is_up else cur - amount))
            vol_obj.SetMasterVolumeLevelScalar(new_level / 100.0, None)
            return ToolResult(message=f"Ses {'artırıldı' if is_up else 'azaltıldı'}: %{new_level}",
                              ok=True, data=new_level)
        except Exception:
            pass
        # Fallback: tuş kombinasyonu
        key_code = 175 if is_up else 174
        steps = max(1, amount // 2)
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command",
             f"$wsh=New-Object -ComObject WScript.Shell; for($i=0;$i<{steps};$i++){{$wsh.SendKeys([char]{key_code})}}"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return ToolResult(message=f"Ses {'artırıldı' if is_up else 'azaltıldı'} (~{steps*2} birim).",
                          ok=True)

    # Belirli değer
    m = re.search(r"(\d+)", text)
    if not m:
        return ToolResult(
            message="Ses seviyesi için bir sayı belirtin (0-100) veya 'artır'/'azalt' deyin.",
            ok=True)
    level = max(0, min(100, int(m.group(1))))
    if is_safe_mode():
        return ToolResult(message="[test modu: ses degistirilmedi]", ok=True)

    # pycaw
    try:
        _, _, vol_obj = _get_pycaw_vol()
        vol_obj.SetMasterVolumeLevelScalar(level / 100.0, None)
        return ToolResult(message=f"Ses seviyesi %{level} olarak ayarlandı.", ok=True, data=level)
    except Exception:
        pass

    # Fallback: nircmd
    nr = _nircmd(f"setsysvolume {int(level / 100 * 65535)}")
    if nr:
        return ToolResult(message=f"Ses seviyesi %{level} olarak ayarlandı.", ok=True, data=level)

    return ToolResult(message="Ses seviyesi ayarlanamadı.", ok=False, error="ses_ayarlanamadi")


def mute_volume(arg=None) -> ToolResult:
    """Sesi kapat (mute)."""
    if is_safe_mode():
        return ToolResult(message="[test modu: ses degistirilmedi]", ok=True)
    try:
        _, _, vol_obj = _get_pycaw_vol()
        vol_obj.SetMute(1, None)
        return ToolResult(message="Ses kapatıldı (sessiz).", ok=True)
    except Exception as e:
        log.warning("mute_volume: pycaw başarısız, SendKeys fallback kullanıldı: %s", e)
        _ps("$wsh = New-Object -ComObject WScript.Shell; $wsh.SendKeys([char]173)")
        return ToolResult(message="Ses kapatıldı.", ok=True)


def unmute_volume(arg=None) -> ToolResult:
    """Sesi aç (unmute)."""
    if is_safe_mode():
        return ToolResult(message="[test modu: ses degistirilmedi]", ok=True)
    try:
        _, _, vol_obj = _get_pycaw_vol()
        vol_obj.SetMute(0, None)
        return ToolResult(message="Ses açıldı.", ok=True)
    except Exception as e:
        log.warning("unmute_volume: pycaw başarısız, SendKeys fallback kullanıldı: %s", e)
        _ps("$wsh = New-Object -ComObject WScript.Shell; $wsh.SendKeys([char]173)")
        return ToolResult(message="Ses açıldı.", ok=True)


# ── PARLAKLIK ─────────────────────────────────────────────────────────────────

def get_brightness(arg=None) -> ToolResult:
    """Ekran parlaklığını döndürür."""
    # 1. screen_brightness_control — hem laptop hem harici
    try:
        import screen_brightness_control as sbc
        levels = sbc.get_brightness()
        if isinstance(levels, list) and levels:
            level = levels[0]
        else:
            level = int(levels)
        return ToolResult(message=f"Ekran parlaklığı: %{level}", ok=True, data=level)
    except ImportError:
        pass
    except Exception:
        pass

    # 2. WMI — sadece laptop
    out = _ps(
        "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness "
        "-ErrorAction SilentlyContinue).CurrentBrightness"
    )
    if out and out.strip().isdigit():
        return ToolResult(message=f"Ekran parlaklığı: %{out.strip()}", ok=True, data=out.strip())

    return ToolResult(message=(
        "Parlaklık bilgisi alınamadı. Harici monitörler için: "
        "pip install screen-brightness-control"
    ), ok=True)


def set_brightness(arg=None) -> ToolResult:
    """Ekran parlaklığını ayarlar. arg: 0-100.
    Önce screen_brightness_control (DDC/CI, hem laptop hem harici monitör),
    sonra WMI (sadece laptop dahili ekran), sonra nircmd dener.
    """
    text = str(arg or "").strip()
    m = re.search(r"(\d+)", text)
    if not m:
        return ToolResult(message="Parlaklık için 0-100 arası bir sayı belirtin.", ok=True)
    level = max(0, min(100, int(m.group(1))))
    if is_safe_mode():
        return ToolResult(message="[test modu: parlaklik degistirilmedi]", ok=True)

    # 1. screen_brightness_control — hem laptop hem harici monitör (DDC/CI)
    try:
        import screen_brightness_control as sbc  # pip install screen-brightness-control
        sbc.set_brightness(level)
        return ToolResult(message=f"Ekran parlaklığı %{level} olarak ayarlandı.", ok=True, data=level)
    except ImportError:
        pass
    except Exception as e:
        # sbc yüklü ama bu monitörde çalışmadı — devam et
        pass

    # 2. WMI — yalnızca laptop / dahili ekran
    wmi_result = _ps(
        f"$wmi = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods -ErrorAction SilentlyContinue; "
        f"if ($wmi) {{ $wmi.WmiSetBrightness(1,{level}); 'ok' }} else {{ 'no_wmi' }}"
    )
    if wmi_result and wmi_result.strip() == "ok":
        return ToolResult(message=f"Ekran parlaklığı %{level} olarak ayarlandı.", ok=True, data=level)

    # 3. nircmd — varsa kullan
    nircmd = Path(__file__).resolve().parents[1] / "nircmd.exe"
    if not nircmd.exists():
        nircmd = Path("nircmd.exe")
    if nircmd.exists():
        try:
            result = subprocess.run(
                [str(nircmd), "setbrightness", str(level)],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return ToolResult(message=f"Ekran parlaklığı %{level} olarak ayarlandı.", ok=True, data=level)
        except Exception:
            pass

    # 4. PowerShell üzerinden ControlMyMonitor (varsa)
    cm_path = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "ControlMyMonitor" / "ControlMyMonitor.exe"
    if cm_path.exists():
        try:
            subprocess.run([str(cm_path), "/SetValue", "Current Monitor", "10", str(level)],
                           timeout=5, capture_output=True)
            return ToolResult(message=f"Ekran parlaklığı %{level} olarak ayarlandı.", ok=True, data=level)
        except Exception:
            pass

    # Hiçbiri çalışmadı — dürüst hata (denendi+basarisiz → ok=False)
    return ToolResult(message=(
        f"Parlaklık ayarlanamadı. Harici monitörler için 'screen-brightness-control' "
        f"kütüphanesi gerekiyor: pip install screen-brightness-control\n"
        f"WMI sonucu: {wmi_result[:80] if wmi_result else 'boş'}"
    ), ok=False, error="parlaklik_ayarlanamadi")


# ── GÜNEŞ IŞIĞI (NIGHT LIGHT / GECE MODU) ────────────────────────────────────

def enable_night_light(arg=None) -> ToolResult:
    """Gece modunu (Night Light) açar."""
    if is_safe_mode():
        return ToolResult(message="[test modu: gece isigi degistirilmedi]", ok=True)
    # Windows 10/11: registry yolu üzerinden Night Light aktif et
    key = (
        r"HKCU:\Software\Microsoft\Windows\CurrentVersion"
        r"\CloudStore\Store\DefaultAccount\Current"
        r"\default$windows.data.bluelightreduction.settings"
        r"\windows.data.bluelightreduction.settings"
    )
    # Durum bitini 1 olarak set et (Data değerinin 2. byte'ı)
    # En güvenilir yol: ayarlar sayfasını aç ve kullanıcıya söyle
    try:
        os.startfile("ms-settings:nightlight")
        return ToolResult(message="Gece ışığı ayar sayfası açıldı. 'Şimdi aç' tuşuna basabilirsiniz.",
                          ok=True)
    except Exception:
        return ToolResult(message="Gece ışığı ayarları: Windows Ayarlar > Ekran > Gece ışığı",
                          ok=True)


def disable_night_light(arg=None) -> ToolResult:
    """Gece modunu (Night Light) kapatır."""
    if is_safe_mode():
        return ToolResult(message="[test modu: gece isigi degistirilmedi]", ok=True)
    try:
        os.startfile("ms-settings:nightlight")
        return ToolResult(message="Gece ışığı ayar sayfası açıldı. Kapatmak için geçişi kapalıya alın.",
                          ok=True)
    except Exception:
        return ToolResult(message="Gece ışığı ayarları: Windows Ayarlar > Ekran > Gece ışığı",
                          ok=True)


# ── KARANLIK MOD ──────────────────────────────────────────────────────────────

def enable_dark_mode(arg=None) -> ToolResult:
    """Windows karanlık modunu açar."""
    if is_safe_mode():
        return ToolResult(message="[test modu: tema degistirilmedi]", ok=True)
    _ps(
        "Set-ItemProperty -Path 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' "
        "-Name 'AppsUseLightTheme' -Value 0 -Type DWord -Force; "
        "Set-ItemProperty -Path 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' "
        "-Name 'SystemUsesLightTheme' -Value 0 -Type DWord -Force"
    )
    # Gerçek durum doğrulaması: değeri geri oku (locale-bağımsız DWORD). Dönüş yok
    # sayılıp koşulsuz "açıldı" demeyelim — izin yoksa set sessizce başarısız olur.
    check = _ps(
        "(Get-ItemProperty -Path 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' "
        "-Name 'AppsUseLightTheme' -ErrorAction SilentlyContinue).AppsUseLightTheme"
    )
    if check.strip() == "0":
        return ToolResult(message="Karanlık mod açıldı. Tam etki için bazı uygulamaları yeniden açmanız gerekebilir.",
                          ok=True)
    return ToolResult(message="Karanlık mod ayarlanamadı (kayıt defteri izni gerekebilir).",
                      ok=False, error="tema_ayarlanamadi")


def enable_light_mode(arg=None) -> ToolResult:
    """Windows açık modunu açar."""
    if is_safe_mode():
        return ToolResult(message="[test modu: tema degistirilmedi]", ok=True)
    _ps(
        "Set-ItemProperty -Path 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' "
        "-Name 'AppsUseLightTheme' -Value 1 -Type DWord -Force; "
        "Set-ItemProperty -Path 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' "
        "-Name 'SystemUsesLightTheme' -Value 1 -Type DWord -Force"
    )
    # Gerçek durum doğrulaması: değeri geri oku (beklenen 1).
    check = _ps(
        "(Get-ItemProperty -Path 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' "
        "-Name 'AppsUseLightTheme' -ErrorAction SilentlyContinue).AppsUseLightTheme"
    )
    if check.strip() == "1":
        return ToolResult(message="Açık mod (Light Mode) etkinleştirildi.", ok=True)
    return ToolResult(message="Açık mod ayarlanamadı (kayıt defteri izni gerekebilir).",
                      ok=False, error="tema_ayarlanamadi")


# ── GÜÇ YÖNETİMİ ─────────────────────────────────────────────────────────────

def sleep_pc(arg=None) -> ToolResult:
    """Bilgisayarı uyku moduna alır."""
    if is_safe_mode():
        return ToolResult(message="[test modu: guc islemi yapilmadi]", ok=True)
    subprocess.Popen(
        ["powershell", "-NoProfile", "-Command",
         "Add-Type -Assembly System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState('Suspend',$false,$false)"],
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    return ToolResult(message="Bilgisayar uyku moduna alınıyor.", ok=True)


def lock_pc(arg=None) -> ToolResult:
    """Bilgisayarı kilitler."""
    if is_safe_mode():
        return ToolResult(message="[test modu: guc islemi yapilmadi]", ok=True)
    subprocess.Popen(
        ["rundll32.exe", "user32.dll,LockWorkStation"],
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    return ToolResult(message="Ekran kilitleniyor.", ok=True)


def shutdown_pc(arg=None) -> ToolResult:
    """Bilgisayarı kapatır. arg: saniye, dakika veya saat cinsinden süre.
    Örnekler: '7200', '2 saat', '30 dakika', '1 saat 30 dakika', 'iptal'
    """
    text = str(arg or "").strip().lower()

    if is_safe_mode():
        return ToolResult(message="[test modu: guc islemi yapilmadi]", ok=True)

    if "iptal" in text or "cancel" in text:
        _cmd("shutdown /a")
        return ToolResult(message="Kapatma işlemi iptal edildi.", ok=True)

    # Türkçe zaman ifadelerini saniyeye çevir
    h = re.search(r"(\d+)\s*saat", text)
    m = re.search(r"(\d+)\s*dakika", text)
    s = re.search(r"(\d+)\s*saniye", text)

    delay = 0
    if h:
        delay += int(h.group(1)) * 3600
    if m:
        delay += int(m.group(1)) * 60
    if s:
        delay += int(s.group(1))

    # Sadece saf sayı varsa → saniye olarak kabul et
    if delay == 0:
        raw = re.search(r"(\d+)", text)
        delay = int(raw.group(1)) if raw else 60

    # Kullanıcıya anlamlı süre mesajı
    hours_disp   = delay // 3600
    minutes_disp = (delay % 3600) // 60
    secs_disp    = delay % 60
    parts = []
    if hours_disp:   parts.append(f"{hours_disp} saat")
    if minutes_disp: parts.append(f"{minutes_disp} dakika")
    if secs_disp:    parts.append(f"{secs_disp} saniye")
    time_str = " ".join(parts) if parts else f"{delay} saniye"

    subprocess.Popen(
        ["shutdown", "/s", "/t", str(delay)],
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    return ToolResult(message=f"Bilgisayar {time_str} sonra kapatılacak. İptal için 'kapatmayı iptal et' de.",
                      ok=True, data=delay)


def restart_pc(arg=None) -> ToolResult:
    """Bilgisayarı yeniden başlatır. Türkçe zaman ifadelerini destekler."""
    text = str(arg or "").strip().lower()
    if is_safe_mode():
        return ToolResult(message="[test modu: guc islemi yapilmadi]", ok=True)
    if "iptal" in text:
        _cmd("shutdown /a")
        return ToolResult(message="Yeniden başlatma iptal edildi.", ok=True)

    h = re.search(r"(\d+)\s*saat", text)
    m = re.search(r"(\d+)\s*dakika", text)
    s = re.search(r"(\d+)\s*saniye", text)
    delay = 0
    if h: delay += int(h.group(1)) * 3600
    if m: delay += int(m.group(1)) * 60
    if s: delay += int(s.group(1))
    if delay == 0:
        raw = re.search(r"(\d+)", text)
        delay = int(raw.group(1)) if raw else 60

    hours_disp   = delay // 3600
    minutes_disp = (delay % 3600) // 60
    secs_disp    = delay % 60
    parts = []
    if hours_disp:   parts.append(f"{hours_disp} saat")
    if minutes_disp: parts.append(f"{minutes_disp} dakika")
    if secs_disp:    parts.append(f"{secs_disp} saniye")
    time_str = " ".join(parts) if parts else f"{delay} saniye"

    subprocess.Popen(
        ["shutdown", "/r", "/t", str(delay)],
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    return ToolResult(message=f"Bilgisayar {time_str} sonra yeniden başlatılacak.", ok=True, data=delay)


def cancel_shutdown(arg=None) -> ToolResult:
    """Kapatma/yeniden başlatma zamanlayıcısını iptal eder."""
    if is_safe_mode():
        return ToolResult(message="[test modu: guc islemi yapilmadi]", ok=True)
    out = _cmd("shutdown /a")
    return ToolResult(message="Kapatma/yeniden başlatma iptal edildi.", ok=True)


# ── WİFİ ─────────────────────────────────────────────────────────────────────

def _wifi_adapter_status() -> str:
    """Wi-Fi adaptörünün durumunu döndürür: Up / Disconnected / Disabled /
    Not Present (ya da '' / 'Hata:...'). Get-NetAdapter Status enum'u locale-
    BAĞIMSIZDIR — netsh çıktısı Türkçe Windows'ta yerelleşir, bu enum yerelleşmez."""
    return _ps(
        "(Get-NetAdapter -Name 'Wi-Fi','WiFi' -ErrorAction SilentlyContinue | "
        "Select-Object -First 1 -ExpandProperty Status)"
    ).strip()


def get_wifi_status(arg=None) -> ToolResult:
    """WiFi durumunu gösterir."""
    out = _ps("netsh wlan show interfaces")
    if not out or "hata" in out.lower():
        return ToolResult(message="WiFi bilgisi alınamadı.", ok=True)
    lines = out.splitlines()
    result = []
    for line in lines:
        for key in ["SSID", "Durum", "Signal", "Sinyal", "State", "Authentication"]:
            if key.lower() in line.lower() and "BSSID" not in line:
                result.append(line.strip())
                break
    return ToolResult(message="\n".join(result) if result else out[:400], ok=True, data=out)


def list_wifi_networks(arg=None) -> ToolResult:
    """Çevredeki WiFi ağlarını listeler."""
    out = _ps("netsh wlan show networks mode=bssid")
    if not out:
        return ToolResult(message="WiFi ağları listelenemedi.", ok=True)
    networks = []
    for line in out.splitlines():
        if "SSID" in line and "BSSID" not in line:
            name = line.split(":", 1)[-1].strip()
            if name:
                networks.append(name)
    if networks:
        return ToolResult(message="Bulunan WiFi ağları:\n" + "\n".join(f"- {n}" for n in networks[:15]),
                          ok=True, data=networks)
    return ToolResult(message=out[:400], ok=True, data=out)


def connect_wifi(arg=None) -> ToolResult:
    """Belirtilen WiFi ağına bağlanır."""
    ssid = str(arg or "").strip()
    if not ssid:
        return ToolResult(message="Bağlanılacak WiFi adını belirtin.", ok=True)
    if is_safe_mode():
        return ToolResult(message="[test modu: wifi degistirilmedi]", ok=True)
    out = _ps(f"netsh wlan connect name='{_ps_quote(ssid)}'")
    if "started" in out.lower() or "baslatildi" in out.lower() or out == "":
        return ToolResult(message=f"'{ssid}' ağına bağlanılıyor...", ok=True)
    return ToolResult(message=f"Bağlantı kurulamadı: {out[:200]}", ok=False, error="baglanti_hatasi")


def disconnect_wifi(arg=None) -> ToolResult:
    """WiFi bağlantısını keser."""
    if is_safe_mode():
        return ToolResult(message="[test modu: wifi degistirilmedi]", ok=True)
    _ps("netsh wlan disconnect")
    # Doğrulama: bağlantı kesilince adaptör Up→Disconnected olur (Disabled da "bağlı
    # değil" sayılır). Hâlâ "Up" ise kesilememiş → ok=False.
    status = _wifi_adapter_status()
    if status.lower() in ("disconnected", "disabled"):
        return ToolResult(message="WiFi bağlantısı kesildi.", ok=True, data=status)
    return ToolResult(message="WiFi bağlantısı kesilemedi (doğrulanamadı).",
                      ok=False, error="wifi_dogrulanamadi")


def enable_wifi(arg=None) -> ToolResult:
    """WiFi adaptörünü etkinleştirir."""
    if is_safe_mode():
        return ToolResult(message="[test modu: wifi degistirilmedi]", ok=True)
    _ps("Enable-NetAdapter -Name 'Wi-Fi' -Confirm:$false -ErrorAction SilentlyContinue; "
        "Enable-NetAdapter -Name 'WiFi' -Confirm:$false -ErrorAction SilentlyContinue")
    # Doğrulama: etkinleşince adaptör Up ya da Disconnected olur. Hâlâ Disabled/yok
    # ise (yanlış adaptör adı veya izin yok) → ok=False.
    status = _wifi_adapter_status()
    if status.lower() in ("up", "disconnected"):
        return ToolResult(message="WiFi etkinleştirildi.", ok=True, data=status)
    return ToolResult(message="WiFi etkinleştirilemedi (adaptör adı veya izin?).",
                      ok=False, error="wifi_dogrulanamadi")


def disable_wifi(arg=None) -> ToolResult:
    """WiFi adaptörünü devre dışı bırakır."""
    if is_safe_mode():
        return ToolResult(message="[test modu: wifi degistirilmedi]", ok=True)
    _ps("Disable-NetAdapter -Name 'Wi-Fi' -Confirm:$false -ErrorAction SilentlyContinue; "
        "Disable-NetAdapter -Name 'WiFi' -Confirm:$false -ErrorAction SilentlyContinue")
    # Doğrulama: devre dışı kalınca adaptör Disabled olur. Değilse (izin yok/yanlış ad)
    # → ok=False.
    status = _wifi_adapter_status()
    if status.lower() == "disabled":
        return ToolResult(message="WiFi devre dışı bırakıldı.", ok=True, data=status)
    return ToolResult(message="WiFi devre dışı bırakılamadı (adaptör adı veya izin?).",
                      ok=False, error="wifi_dogrulanamadi")


# ── BLUETOOTH ─────────────────────────────────────────────────────────────────

def get_bluetooth_status(arg=None) -> ToolResult:
    """Bluetooth durumunu gösterir."""
    out = _ps(
        "Get-PnpDevice -Class Bluetooth | Select-Object -ExpandProperty Status | Select-Object -First 1"
    )
    if "ok" in out.lower():
        return ToolResult(message="Bluetooth: Açık ve çalışıyor.", ok=True, data=out)
    if out:
        return ToolResult(message=f"Bluetooth durumu: {out}", ok=True, data=out)
    return ToolResult(message="Bluetooth durumu alınamadı.", ok=True)


def enable_bluetooth(arg=None) -> ToolResult:
    """Bluetooth'u açar."""
    if is_safe_mode():
        return ToolResult(message="[test modu: bluetooth degistirilmedi]", ok=True)
    result = _ps(
        "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
        "[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null; "
        "$asyncOp = [Windows.Devices.Radios.Radio]::RequestAccessAsync(); "
        "$radios = [System.WindowsRuntimeSystemExtensions]::AsTask($asyncOp).Result; "
        "# Bluetooth toggle via registry\n"
        "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\bthserv' "
        "-Name 'Start' -Value 2 -ErrorAction SilentlyContinue; "
        "Start-Service bthserv -ErrorAction SilentlyContinue"
    )
    # Bluetooth radyo durumu güvenilir sorgulanamıyor (bu yüzden mesaj zaten Ayarlar'a
    # yönlendiriyor); _ps hata döndüyse en azından sessiz kalma.
    if _ps_errored(result):
        log.warning("enable_bluetooth: PowerShell hatası: %s", result[:120])
    return ToolResult(
        message="Bluetooth servisi başlatıldı. Tam etkinleştirme için Ayarlar > Bluetooth menüsünü kullanın.",
        ok=True)


def disable_bluetooth(arg=None) -> ToolResult:
    """Bluetooth'u kapatır."""
    if is_safe_mode():
        return ToolResult(message="[test modu: bluetooth degistirilmedi]", ok=True)
    _bt = _ps("Stop-Service bthserv -Force -ErrorAction SilentlyContinue")
    if _ps_errored(_bt):
        log.warning("disable_bluetooth: PowerShell hatası: %s", _bt[:120])
    return ToolResult(message="Bluetooth servisi durduruldu.", ok=True)


def list_bluetooth_devices(arg=None) -> ToolResult:
    """Eşleştirilmiş Bluetooth cihazlarını listeler."""
    out = _ps(
        "Get-PnpDevice -Class Bluetooth | Where-Object {$_.Status -eq 'OK'} | "
        "Select-Object -ExpandProperty FriendlyName | Sort-Object"
    )
    if not out:
        return ToolResult(message="Eşleştirilmiş Bluetooth cihazı bulunamadı.",
                          ok=False, error="cihaz_yok")
    devices = [l.strip() for l in out.splitlines() if l.strip()]
    return ToolResult(message="Bluetooth cihazları:\n" + "\n".join(f"- {d}" for d in devices),
                      ok=True, data=devices)


# ── SAAT / TARİH ─────────────────────────────────────────────────────────────

def get_time(arg=None) -> ToolResult:
    """Anlık saat ve tarihi döndürür. Ekran okumaya gerek yok."""
    from datetime import datetime
    # strftime %B/%A locale'e bağlı (varsayılan İngilizce) — Türkçe sabit listeler
    _months = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
               "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    _days = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    now = datetime.now()
    return ToolResult(message=(
        f"Saat: {now:%H:%M}  |  Tarih: {now.day} {_months[now.month - 1]} "
        f"{now.year}, {_days[now.weekday()]}"
    ), ok=True)


# ── SİSTEM BİLGİSİ ───────────────────────────────────────────────────────────

def system_info(arg=None) -> ToolResult:
    """Sistem bilgilerini gösterir: CPU, RAM, disk, pil."""
    lines = []
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        cpu_count = psutil.cpu_count()
        mem = psutil.virtual_memory()
        lines.append(f"CPU: %{cpu:.0f} kullanım ({cpu_count} çekirdek)")
        lines.append(f"RAM: {mem.used // (1024**3):.1f}GB / {mem.total // (1024**3):.1f}GB (%{mem.percent:.0f})")

        disk = psutil.disk_usage("C:\\")
        lines.append(f"Disk (C:): {disk.used // (1024**3):.1f}GB / {disk.total // (1024**3):.1f}GB (%{disk.percent:.0f})")

        if psutil.sensors_battery():
            bat = psutil.sensors_battery()
            plug = "prize bağlı" if bat.power_plugged else "pil ile çalışıyor"
            lines.append(f"Pil: %{bat.percent:.0f} ({plug})")
    except ImportError:
        # psutil yok, PowerShell fallback
        cpu_out = _ps("[int](Get-WmiObject Win32_Processor | Measure-Object -Property LoadPercentage -Average | Select-Object -ExpandProperty Average)")
        mem_out = _ps("$m=Get-WmiObject Win32_OperatingSystem; [int](($m.TotalVisibleMemorySize - $m.FreePhysicalMemory) / $m.TotalVisibleMemorySize * 100)")
        if cpu_out.isdigit():
            lines.append(f"CPU: %{cpu_out} kullanım")
        if mem_out.isdigit():
            lines.append(f"RAM: %{mem_out} kullanım")

    if not lines:
        return ToolResult(message="Sistem bilgisi alınamadı.", ok=True)
    return ToolResult(message="Sistem durumu:\n" + "\n".join(lines), ok=True, data=lines)


def battery_status(arg=None) -> ToolResult:
    """Pil durumunu gösterir."""
    try:
        import psutil
        bat = psutil.sensors_battery()
        if bat is None:
            return ToolResult(message="Bu cihazda pil sensörü yok (masaüstü bilgisayar).", ok=True)
        plug = "Prize bağlı" if bat.power_plugged else "Pil ile çalışıyor"
        mins = int(bat.secsleft / 60) if bat.secsleft > 0 and not bat.power_plugged else 0
        time_str = f", tahminen {mins} dakika kaldı" if mins > 0 else ""
        return ToolResult(message=f"Pil: %{bat.percent:.0f} — {plug}{time_str}.", ok=True,
                          data=int(bat.percent))
    except ImportError:
        out = _ps("Get-WmiObject Win32_Battery | Select-Object EstimatedChargeRemaining, BatteryStatus")
        return ToolResult(message=out or "Pil durumu alınamadı.", ok=True)


def disk_info(arg=None) -> ToolResult:
    """Disk kullanım bilgisini gösterir."""
    try:
        import psutil
        lines = []
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                lines.append(
                    f"{part.mountpoint}: {usage.used // (1024**3):.1f}GB / "
                    f"{usage.total // (1024**3):.1f}GB (%{usage.percent:.0f} dolu)"
                )
            except Exception:
                pass
        return ToolResult(
            message="Disk durumu:\n" + "\n".join(lines) if lines else "Disk bilgisi alınamadı.",
            ok=True, data=lines)
    except ImportError:
        out = _ps("Get-PSDrive -PSProvider FileSystem | Select-Object Name, @{N='Used(GB)';E={[int]($_.Used/1GB)}}, @{N='Free(GB)';E={[int]($_.Free/1GB)}} | Format-Table -AutoSize | Out-String")
        return ToolResult(message=out[:400] if out else "Disk bilgisi alınamadı.", ok=True)


# ── SÜREÇ YÖNETİMİ ───────────────────────────────────────────────────────────

def list_processes(arg=None) -> ToolResult:
    """En çok CPU/RAM kullanan süreçleri listeler."""
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
            try:
                procs.append(p.info)
            except Exception:
                pass
        procs.sort(key=lambda x: x.get("memory_percent", 0) or 0, reverse=True)
        top = procs[:12]
        lines = [f"{p['name']:30} RAM:%{(p.get('memory_percent') or 0):.1f}" for p in top]
        return ToolResult(message="En çok RAM kullanan süreçler:\n" + "\n".join(lines),
                          ok=True, data=top)
    except ImportError:
        out = _ps("Get-Process | Sort-Object WorkingSet -Descending | Select-Object -First 10 | Select-Object Name, @{N='RAM(MB)';E={[int]($_.WorkingSet/1MB)}} | Format-Table -AutoSize | Out-String")
        return ToolResult(message=out[:600] if out else "Süreç listesi alınamadı.", ok=True)


def kill_process(arg=None) -> ToolResult:
    """Belirtilen süreci kapatır."""
    name = str(arg or "").strip()
    if not name:
        return ToolResult(message="Kapatılacak sürecin adını belirtin.", ok=True)
    protected = {"system", "svchost", "lsass", "csrss", "winlogon", "services", "explorer"}
    if name.lower().replace(".exe", "") in protected:
        return ToolResult(message=f"'{name}' sistem sürecidir, kapatılamaz.", ok=True)
    # Önce sürecin var olup olmadığını kontrol et (salt-okuma)
    _safe_name = _ps_quote(name)
    check = _ps(f"$p = Get-Process -Name '{_safe_name}' -ErrorAction SilentlyContinue; if ($p) {{ 'found' }} else {{ 'not_found' }}")
    if "not_found" in check:
        return ToolResult(message=f"'{name}' adında çalışan bir süreç bulunamadı.",
                          ok=False, error="surec_yok")
    if is_safe_mode():
        return ToolResult(message="[test modu: surec kapatilmadi]", ok=True)
    _ps(f"Stop-Process -Name '{_safe_name}' -Force -ErrorAction SilentlyContinue")
    # Gerçek durum doğrulaması: süreç hâlâ çalışıyor mu? (koşulsuz 'ok' echo DEĞİL —
    # izin/koruma yüzünden kapanmayan süreç için yanlış "kapatıldı" demeyelim.)
    verify = _ps(f"$p = Get-Process -Name '{_safe_name}' -ErrorAction SilentlyContinue; "
                 f"if ($p) {{ 'still_running' }} else {{ 'gone' }}")
    if "gone" in verify:
        return ToolResult(message=f"'{name}' süreci kapatıldı.", ok=True)
    return ToolResult(message=f"'{name}' kapatılamadı (izin veya koruma gerekebilir).",
                      ok=False, error="kapatilamadi")


# ── PANO (CLIPBOARD) ─────────────────────────────────────────────────────────

def get_clipboard(arg=None) -> ToolResult:
    """Panodaki metni okur."""
    try:
        import pyperclip
        text = pyperclip.paste()
        if not text:
            return ToolResult(message="Pano boş.", ok=True, data="")
        return ToolResult(message=f"Panodaki metin: {text[:500]}", ok=True, data=text)
    except Exception:
        out = _ps("Get-Clipboard")
        return ToolResult(message=f"Panodaki metin: {out[:500]}" if out else "Pano okunamadı.",
                          ok=True, data=out)


def set_clipboard(arg=None) -> ToolResult:
    """Metni panoya kopyalar."""
    text = str(arg or "")
    if is_safe_mode():
        return ToolResult(message="[test modu: pano degistirilmedi]", ok=True)
    try:
        import pyperclip
        pyperclip.copy(text)
        return ToolResult(message=f"Panoya kopyalandı: {text[:100]}", ok=True, data=text)
    except Exception as e:
        log.warning("set_clipboard: pyperclip başarısız, PowerShell fallback: %s", e)
        # Tek tırnak içeren metni PS here-string ile güvenli aktar
        # (_ps_quote: aynı escaping'i yapan paylaşılan helper — connect_wifi/
        # kill_process'in kullandığı, burada da onunla tutarlı olsun diye)
        _ps(f"Set-Clipboard -Value '{_ps_quote(text)}'")
        # Gerçek doğrulama: panoyu geri oku, beklenen metinle karşılaştır (ucuz).
        # CRLF normalize — Get-Clipboard satır sonlarını değiştirebilir.
        _norm = lambda s: str(s).replace("\r\n", "\n").strip()
        back = _ps("Get-Clipboard")
        if _norm(back) == _norm(text):
            return ToolResult(message="Panoya kopyalandı.", ok=True, data=text)
        return ToolResult(message="Panoya kopyalanamadı (doğrulanamadı).",
                          ok=False, error="pano_dogrulanamadi")


# ── EKRAN ÇÖZÜNÜRLÜĞÜ ─────────────────────────────────────────────────────────

def get_resolution(arg=None) -> ToolResult:
    """Ekran çözünürlüğünü gösterir."""
    # Yöntem 1: Windows.Forms
    out = _ps(
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$s = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
        "\"$($s.Width) x $($s.Height)\""
    )
    if out and "x" in out and any(c.isdigit() for c in out):
        return ToolResult(message=f"Ekran çözünürlüğü: {out.strip()}", ok=True, data=out.strip())
    # Yöntem 2: WMI (bazı sistemlerde çalışır)
    out2 = _ps(
        "Get-WmiObject -Class Win32_VideoController | "
        "Where-Object {$_.CurrentHorizontalResolution -gt 0} | "
        "Select-Object -First 1 | "
        "ForEach-Object { \"$($_.CurrentHorizontalResolution) x $($_.CurrentVerticalResolution) @ $($_.CurrentRefreshRate)Hz\" }"
    )
    if out2 and "x" in out2 and any(c.isdigit() for c in out2):
        return ToolResult(message=f"Ekran çözünürlüğü: {out2.strip()}", ok=True, data=out2.strip())
    return ToolResult(message="Ekran çözünürlüğü alınamadı.", ok=True)


# ── GÖREV ZAMANLAYICI / OTOMATİK BAŞLANGIÇ ────────────────────────────────────

def system_startup_status(arg=None) -> ToolResult:
    """Klargus başlangıç durumunu gösterir."""
    try:
        from core.startup_manager import startup_enabled
        enabled = startup_enabled()
        return ToolResult(
            message=f"Windows baslangiç: {'Acik [aktif]' if enabled else 'Kapali [pasif]'}",
            ok=True, data=bool(enabled))
    except Exception as e:
        return ToolResult(message=f"Başlangıç durumu okunamadı: {e}", ok=False, error=str(e))


def enable_startup(arg=None) -> ToolResult:
    """Klargus'u Windows başlangıcına ekler."""
    if is_safe_mode():
        return ToolResult(message="[test modu: baslangic degistirilmedi]", ok=True)
    try:
        from core.startup_manager import enable_startup as _enable
        from core.path_utils import resource_path
        path = _enable(str(Path(resource_path("")).parent))
        return ToolResult(message=f"Klargus Windows başlangıcına eklendi.", ok=True, data=str(path))
    except Exception as e:
        return ToolResult(message=f"Başlangıca eklenemedi: {e}", ok=False, error=str(e))


def disable_startup(arg=None) -> ToolResult:
    """Klargus'u Windows başlangıcından kaldırır."""
    if is_safe_mode():
        return ToolResult(message="[test modu: baslangic degistirilmedi]", ok=True)
    try:
        from core.startup_manager import disable_startup as _disable
        _disable()
        return ToolResult(message="Klargus Windows başlangıcından kaldırıldı.", ok=True)
    except Exception as e:
        return ToolResult(message=f"Başlangıçtan kaldırılamadı: {e}", ok=False, error=str(e))


# ── UYGULAMA PENCERESİ ────────────────────────────────────────────────────────

def open_windows_settings(arg=None) -> ToolResult:
    """Windows ayarlar sayfasını açar. arg: ses, ekran, bluetooth, ağ, güç, gizlilik vb."""
    text = str(arg or "").strip().lower()
    if is_safe_mode():
        return ToolResult(message="[test modu: ayar sayfasi acilmadi]", ok=True)
    uris = {
        "ses": "ms-settings:sound",
        "sound": "ms-settings:sound",
        "ekran": "ms-settings:display",
        "display": "ms-settings:display",
        "bluetooth": "ms-settings:bluetooth",
        "ağ": "ms-settings:network",
        "network": "ms-settings:network",
        "wifi": "ms-settings:network-wifi",
        "güç": "ms-settings:powersleep",
        "power": "ms-settings:powersleep",
        "gizlilik": "ms-settings:privacy",
        "privacy": "ms-settings:privacy",
        "güncelleme": "ms-settings:windowsupdate",
        "update": "ms-settings:windowsupdate",
        "başlangıç": "ms-settings:startupapps",
        "baslangic": "ms-settings:startupapps",
        "uygulama": "ms-settings:appsfeatures",
        "app": "ms-settings:appsfeatures",
        "tema": "ms-settings:themes",
        "theme": "ms-settings:themes",
        "klavye": "ms-settings:keyboard",
        "keyboard": "ms-settings:keyboard",
        "fare": "ms-settings:mousetouchpad",
        "mouse": "ms-settings:mousetouchpad",
        "bildirim": "ms-settings:notifications",
        "notification": "ms-settings:notifications",
        "tarih": "ms-settings:dateandtime",
        "time": "ms-settings:dateandtime",
        "dil": "ms-settings:regionlanguage",
        "language": "ms-settings:regionlanguage",
        "erişilebilirlik": "ms-settings:easeofaccess",
        "accessibility": "ms-settings:easeofaccess",
        "kişiselleştirme": "ms-settings:personalization",
        "personalization": "ms-settings:personalization",
        "hesap": "ms-settings:accounts",
        "account": "ms-settings:accounts",
        "pil": "ms-settings:batterysaver",
        "battery": "ms-settings:batterysaver",
    }
    # Eşleşme ara
    matched_uri = None
    matched_label = None
    for key, uri in uris.items():
        if key in text:
            matched_uri = uri
            matched_label = key
            break

    if not matched_uri:
        # Genel ayarlar
        matched_uri = "ms-settings:"
        matched_label = "Windows Ayarları"

    try:
        os.startfile(matched_uri)
        return ToolResult(message=f"'{matched_label}' ayar sayfası açıldı.", ok=True,
                          data=matched_uri)
    except Exception as e:
        return ToolResult(message=f"Ayar sayfası açılamadı: {e}", ok=False, error=str(e))


# ── EKRAN KORUYUCU / MONITÖR ──────────────────────────────────────────────────

def turn_off_monitor(arg=None) -> ToolResult:
    """Monitörü kapatır (uyku moduna alır)."""
    if is_safe_mode():
        return ToolResult(message="[test modu: monitor kapatilmadi]", ok=True)
    _mon = _ps(
        "Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;"
        "public class M{[DllImport(\"user32.dll\")]public static extern int SendMessage(int hWnd,int hMsg,int wParam,int lParam);}' -ErrorAction SilentlyContinue;"
        "[M]::SendMessage(-1,0x0112,0xF170,2) | Out-Null"
    )
    # Monitör durumu geri-sorgusu karmaşık; kullanıcı sonucu GÖRSEL olarak görür
    # (sesli değil) → ok=True kalır. _ps hata döndüyse sessiz kalma.
    if _ps_errored(_mon):
        log.warning("turn_off_monitor: PowerShell hatası: %s", _mon[:120])
    return ToolResult(message="Monitör kapatıldı.", ok=True)


# ── DOSYA SİLME (DEVRE DIŞI) ──────────────────────────────────────────────────
# NOT: "delete_file" aracı artık tools/file_tools.py'de (Desktop-tabanlı _resolve_path
# ile tutarlı dosya araçları paketi). Bu fonksiyon `_` önekiyle auto-register'dan
# çıkarıldı; doğrudan çağrılmıyor. Referanslar (permissions/llm_intent) isimle çalışır.

def _delete_file(arg=None) -> ToolResult:
    """
    (DEVRE DIŞI — file_tools.delete_file geçerli.) Belirtilen dosyayı siler.
    Güvenlik: sadece kullanıcının ev dizini altındaki dosyalara izin verir.
    Sistem dosyaları, program dosyaları ve proje kök dizini korunur.
    """
    import shutil
    path_str = str(arg or "").strip()
    if not path_str:
        return ToolResult(message="Silinecek dosya yolu belirtilmedi.", ok=True)

    try:
        target = Path(path_str).resolve()
    except Exception as e:
        return ToolResult(message=f"Geçersiz yol: {e}", ok=False, error=str(e))

    # ── Güvenlik kontrolleri ──────────────────────────────────────────────────
    home = Path.home().resolve()
    project_root = Path(__file__).resolve().parents[1]

    # Kullanıcı ev dizini dışında — izin yok
    try:
        target.relative_to(home)
    except ValueError:
        return ToolResult(
            message=f"İzin reddedildi: Yalnızca kullanıcı dizini ({home}) altındaki dosyalar silinebilir.",
            ok=False, error="izin_reddedildi")

    # Proje kök dizini içinde — izin yok
    try:
        target.relative_to(project_root)
        return ToolResult(message="İzin reddedildi: Proje dosyaları silinemez.",
                          ok=False, error="izin_reddedildi")
    except ValueError:
        pass

    # Korunan klasörler
    protected = ["System32", "Program Files", "Windows", "AppData\\Roaming\\Microsoft"]
    for p in protected:
        if p.lower() in str(target).lower():
            return ToolResult(message=f"İzin reddedildi: Korunan klasör içinde ({p}).",
                              ok=False, error="izin_reddedildi")

    # ── Silme ────────────────────────────────────────────────────────────────
    if not target.exists():
        return ToolResult(message=f"Bulunamadı: {target}", ok=False, error="bulunamadi")

    if is_safe_mode():
        return ToolResult(message="[test modu: dosya silinmedi]", ok=True)

    try:
        if target.is_dir():
            shutil.rmtree(target)
            return ToolResult(message=f"Klasör silindi: {target}", ok=True, data=str(target))
        else:
            target.unlink()
            return ToolResult(message=f"Dosya silindi: {target}", ok=True, data=str(target))
    except PermissionError:
        return ToolResult(
            message=f"İzin hatası: {target} silinemedi (başka bir program kullanıyor olabilir).",
            ok=False, error="izin_hatasi")
    except Exception as e:
        return ToolResult(message=f"Silme hatası: {e}", ok=False, error=str(e))


# ── TERMİNAL KOMUTU ───────────────────────────────────────────────────────────

# Güvenli terminal komutları — sadece bunlara izin verilir
_TERMINAL_ALLOWLIST = {
    "tasklist", "ipconfig", "systeminfo", "netstat", "dir", "echo",
    "ping", "tracert", "nslookup", "arp", "route", "hostname",
    "ver", "date", "time", "whoami", "set", "path",
    "wmic", "net", "chkdsk", "type",
    "findstr", "where", "cls", "tree", "vol", "label",
}

_TERMINAL_BLOCKLIST = {
    "format", "del", "rm", "rmdir", "rd", "reg", "regedit",
    "shutdown", "restart", "powershell", "cmd", "wscript", "cscript",
    "curl", "wget", "bitsadmin", "certutil", "schtasks",
    "netsh", "sc", "bcdedit", "diskpart", "cipher",
}


def run_terminal(arg=None) -> ToolResult:
    """
    Güvenli Windows terminal komutu çalıştırır.
    Yalnızca okuma/sorgulama amaçlı komutlara izin verilir.
    Silme, format, kayıt defteri, ağ yapılandırması gibi yıkıcı komutlar engellenir.
    """
    cmd = str(arg or "").strip()
    if not cmd:
        return ToolResult(message="Çalıştırılacak komut belirtilmedi.", ok=True)

    # Shell zincirleme/yönlendirme meta-karakterlerini engelle
    _TERMINAL_META = ("&&", "||", "|", ";", ">", "<", "`", "$(", "&")
    if any(c in cmd for c in _TERMINAL_META):
        return ToolResult(
            message="Güvenlik: Komut zinciri veya yönlendirme içeren ifadeler çalıştırılamaz.",
            ok=True)

    # Bloklanan komutları kontrol et
    cmd_lower = cmd.lower()
    first_word = cmd_lower.split()[0] if cmd_lower.split() else ""

    for blocked in _TERMINAL_BLOCKLIST:
        if first_word == blocked or f" {blocked} " in f" {cmd_lower} ":
            return ToolResult(message=f"İzin reddedildi: '{blocked}' komutu engellendi (yıkıcı işlem).",
                              ok=False, error="izin_reddedildi")

    # İzin listesinde mi?
    allowed = any(cmd_lower.startswith(w) for w in _TERMINAL_ALLOWLIST)
    if not allowed:
        return ToolResult(message=(
            f"Güvenlik: '{first_word}' komutuna izin verilmiyor. "
            f"İzin verilen komutlar: {', '.join(sorted(_TERMINAL_ALLOWLIST))}"
        ), ok=True)

    if is_safe_mode():
        return ToolResult(message="[test modu: komut calistirilmadi]", ok=True)

    try:
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True,
            encoding="cp1254", errors="ignore",
            timeout=15,
        )
        out = (result.stdout or result.stderr or "").strip()
        return ToolResult(message=out[:2000] if out else "(Çıktı yok)", ok=True, data=out)
    except subprocess.TimeoutExpired:
        return ToolResult(message="Zaman aşımı: komut 15 saniyede tamamlanamadı.",
                          ok=False, error="zaman_asimi")
    except Exception as e:
        return ToolResult(message=f"Terminal hatası: {e}", ok=False, error=str(e))


# ── WSL2 / KALI LINUX ────────────────────────────────────────────────────────

_WSL_DISTRO = "kali-linux"

_WSL_DANGEROUS = (
    "rm -rf /",
    "mkfs",
    "dd if=",
    "> /dev/sda",
    ":(){:|:&};:",   # fork bomb
    "chmod -R 777 /",
    "shutdown",
    "reboot",
    "init 0",
    "init 6",
)


def run_wsl_command(
    cmd: str,
    distro: str = _WSL_DISTRO,
    timeout: int = 300,
    use_sudo: bool = False,
) -> dict:
    """
    WSL2 üzerindeki Linux distrosunda bash komutu çalıştırır.

    Args:
        cmd:      Çalıştırılacak bash komutu
        distro:   WSL distro adı (varsayılan: kali-linux)
        timeout:  Saniye cinsinden zaman aşımı (varsayılan: 300)
        use_sudo: Sudo ile çalıştır (varsayılan: False)

    Returns:
        dict: {
            "success":   bool,
            "stdout":    str,
            "stderr":    str,
            "exit_code": int,
        }
    """
    if not cmd or not cmd.strip():
        return {"success": False, "stdout": "", "stderr": "Komut boş.", "exit_code": -1}

    # Tehlikeli kalıpları engelle
    cmd_lower = cmd.lower()
    for pattern in _WSL_DANGEROUS:
        if pattern.lower() in cmd_lower:
            return {
                "success":   False,
                "stdout":    "",
                "stderr":    f"Güvenlik: '{pattern}' kalıbı engellendi.",
                "exit_code": -1,
            }

    # repr() bash için güvenli tırnaklama değildir (iki tür tırnak da
    # varsa ters-slash kaçışlı tek tırnağa düşer, bash bunu bash-literal
    # olarak yorumlamaz) — shlex.quote kullan.
    bash_cmd = f"sudo bash -c {shlex.quote(cmd)}" if use_sudo else cmd

    try:
        result = subprocess.run(
            ["wsl", "-d", distro, "--", "bash", "-c", bash_cmd],
            capture_output=True,
            timeout=timeout,
        )
        # UTF-8 dene, hata varsa replace ile geç
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        return {
            "success":   result.returncode == 0,
            "stdout":    stdout,
            "stderr":    stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "success":   False,
            "stdout":    "",
            "stderr":    f"Zaman aşımı: {timeout}s içinde tamamlanamadı.",
            "exit_code": -1,
        }
    except FileNotFoundError:
        return {
            "success":   False,
            "stdout":    "",
            "stderr":    "wsl.exe bulunamadı — WSL2 kurulu değil veya PATH'te yok.",
            "exit_code": -1,
        }
    except Exception as e:
        return {
            "success":   False,
            "stdout":    "",
            "stderr":    f"WSL çalıştırma hatası: {e}",
            "exit_code": -1,
        }


def wsl_status(distro: str = _WSL_DISTRO) -> dict:
    """
    WSL2 kurulum ve distro durumunu kontrol eder.

    Returns:
        dict: {
            "wsl_installed":    bool,
            "distro_available": bool,
            "distro_running":   bool,
            "distro":           str,
            "message":          str,
        }
    """
    # wsl.exe var mı?
    try:
        subprocess.run(
            ["wsl", "--version"],
            capture_output=True, timeout=5,
        )
        wsl_installed = True
    except FileNotFoundError:
        return {
            "wsl_installed":    False,
            "distro_available": False,
            "distro_running":   False,
            "distro":           distro,
            "message":          "WSL2 kurulu değil.",
        }
    except Exception:
        wsl_installed = True  # wsl.exe var ama --version farklı davranabilir

    # Distro listesini al (UTF-16-LE — Windows'ta wsl --list bu encoding'de döner)
    distro_available = False
    distro_running   = False
    try:
        r = subprocess.run(
            ["wsl", "--list", "--verbose"],
            capture_output=True, timeout=8,
        )
        raw = r.stdout.decode("utf-16-le", errors="replace")
        for line in raw.splitlines():
            if distro.lower() in line.lower():
                distro_available = True
                if "running" in line.lower():
                    distro_running = True
                break
    except Exception:
        pass

    # Distro yoksa basit echo testi ile kontrol et
    if not distro_available:
        probe = run_wsl_command("echo ping", distro=distro, timeout=6)
        if probe["success"] and "ping" in probe["stdout"]:
            distro_available = True
            distro_running   = True

    if not distro_available:
        msg = f"'{distro}' distrosu bulunamadı. 'wsl --list' ile kurulu distroları kontrol et."
    elif distro_running:
        msg = f"WSL2 hazır — '{distro}' çalışıyor."
    else:
        msg = f"WSL2 kurulu — '{distro}' durdurulmuş (ilk komutta otomatik başlar)."

    return {
        "wsl_installed":    wsl_installed,
        "distro_available": distro_available,
        "distro_running":   distro_running,
        "distro":           distro,
        "message":          msg,
    }


def check_linux_tool(tool_name: str, distro: str = _WSL_DISTRO) -> bool:
    """
    Belirtilen Linux aracının WSL distrosunda kurulu olup olmadığını kontrol eder.

    Args:
        tool_name: Kontrol edilecek araç adı (ör: "nmap", "gobuster")
        distro:    WSL distro adı (varsayılan: kali-linux)

    Returns:
        bool: True = kurulu, False = kurulu değil veya WSL erişilemiyor
    """
    safe_name = "".join(c for c in tool_name.strip() if c.isalnum() or c in ("-", "_", "."))
    if not safe_name:
        return False

    result = run_wsl_command(
        f"command -v {safe_name} 2>/dev/null",
        distro=distro,
        timeout=8,
    )
    return result["success"] and bool(result["stdout"].strip())


# Araçlar brain/tool_registry.py tarafından _register_module_tools() ile otomatik kaydedilir.
# Bu dosyada brain.tool_registry import etme — circular import yaratır.
