import os
import platform
import sys
from pathlib import Path
from typing import Dict, Optional

from core.command_router import normalize_command, route_system_command
from brain.tool_result import ToolResult
from core.safe_mode import is_safe_mode


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LAST_REPORT_FILE = DATA_DIR / "system_control_last_report.txt"
APPROVED_TOKEN = "approved"

# Gerçekten sistem durumu değiştiren aksiyonlar — güvenli modda bunlar UYGULANMAZ.
# (Bilinmeyen aksiyon bu kümede yok → "Bilinmeyen..." mesajı guard'a takılmadan döner.)
_MUTATING_SYSTEM_ACTIONS = frozenset({
    "startup_enable", "startup_disable", "mic_auto_select", "mic_default",
})


SETTINGS_TARGETS: Dict[str, Dict[str, str]] = {
    "main": {"label": "Windows Ayarlari", "uri": "ms-settings:"},
    "sound": {"label": "Ses ayarlari", "uri": "ms-settings:sound"},
    "microphone": {"label": "Mikrofon gizlilik ayarlari", "uri": "ms-settings:privacy-microphone"},
    "bluetooth": {"label": "Bluetooth ayarlari", "uri": "ms-settings:bluetooth"},
    "display": {"label": "Ekran ayarlari", "uri": "ms-settings:display"},
    "network": {"label": "Ag ayarlari", "uri": "ms-settings:network"},
    "startup_apps": {"label": "Baslangic uygulamalari", "uri": "ms-settings:startupapps"},
    "power": {"label": "Guc ve uyku ayarlari", "uri": "ms-settings:powersleep"},
    "default_apps": {"label": "Varsayilan uygulamalar", "uri": "ms-settings:defaultapps"},
    "installed_apps": {"label": "Yuklu uygulamalar", "uri": "ms-settings:appsfeatures"},
    "windows_update": {"label": "Windows Update", "uri": "ms-settings:windowsupdate"},
    "privacy": {"label": "Gizlilik ayarlari", "uri": "ms-settings:privacy"},
}


SENSITIVE_ACTION_LABELS = {
    "startup_enable": "Klargus'u Windows baslangicina ekleme",
    "startup_disable": "Klargus'u Windows baslangicindan kaldirma",
    "mic_auto_select": "Klargus mikrofonunu otomatik secme",
    "mic_default": "Klargus mikrofon secimini varsayilana alma",
}


CRITICAL_TERMS = {
    "regedit",
    "registry",
    "kayit defteri",
    "firewall kapat",
    "guvenlik duvarini kapat",
    "ag bagdastiricisini kapat",
    "internet kes",
    "servis durdur",
    "hizmet durdur",
    "process kill",
    "surec kapat",
    "islem sonlandir",
    "bilgisayari kapat",
    "restart",
    "yeniden baslat",
    "format",
}


def _write_last_report(text: str) -> None:
    try:
        LAST_REPORT_FILE.write_text(str(text or ""), encoding="utf-8")
    except Exception:
        pass


def _read_last_report() -> str:
    try:
        if LAST_REPORT_FILE.exists():
            return LAST_REPORT_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _format_bool(value: object) -> str:
    return "acik" if bool(value) else "kapali"


def _platform_lines() -> list[str]:
    lines = [
        f"Platform: {platform.system()} {platform.release()} ({platform.machine()}).",
        f"Python: {platform.python_version()} | calisan: {Path(sys.executable).name}.",
    ]

    try:
        import psutil

        cpu = psutil.cpu_percent(interval=0.05)
        mem = psutil.virtual_memory()
        lines.append(f"Kaynaklar: CPU %{cpu:.0f}, RAM %{mem.percent:.0f}.")
        lines.append(f"Surecler: {len(psutil.pids())} aktif surec gorunuyor.")
    except Exception:
        lines.append("Kaynak/surec ozeti: psutil okunamadi.")

    return lines


def _startup_lines() -> list[str]:
    try:
        from core.startup_manager import get_bat_path, startup_enabled

        enabled = startup_enabled()
        path = get_bat_path()
        return [
            f"Baslangic: {_format_bool(enabled)}.",
            f"Baslangic dosyasi: {path}",
        ]
    except Exception as exc:
        return [f"Baslangic durumu okunamadi: {exc}"]


def _audio_lines(detailed: bool = False) -> list[str]:
    try:
        from audio.device_manager import get_selected_input_device, list_input_devices

        selected = get_selected_input_device()
        devices = list_input_devices()
        selected_name = "varsayilan"
        for device in devices:
            if selected is not None and int(device.get("index", -1)) == int(selected):
                selected_name = str(device.get("name", "-"))
                break

        lines = [
            f"Klargus mikrofon secimi: {selected if selected is not None else 'varsayilan'} - {selected_name}.",
            f"Gorunen giris aygiti sayisi: {len(devices)}.",
        ]
        if detailed:
            for device in devices[:10]:
                lines.append(
                    f"- {device.get('index')}: {device.get('name')} | kanal={device.get('max_input_channels')} | sr={int(device.get('default_samplerate', 0) or 0)}"
                )
        return lines
    except Exception as exc:
        return [f"Mikrofon durumu okunamadi: {exc}"]


def _klargus_settings_lines() -> list[str]:
    try:
        from brain.settings_manager import load_settings

        data = load_settings()
        return [
            f"Wake motoru: {data.get('wake_engine', '-')}.",
            f"Surekli dinleme: {_format_bool(data.get('listen_without_wake', False))}.",
            f"Komut yakalama: {data.get('command_capture_mode', '-')}.",
            f"Ses kilidi: {_format_bool(data.get('voice_auth_enabled', False))}.",
            f"Kontrol komutlarinda siki ses dogrulama: {_format_bool(data.get('voice_auth_strict_for_control', True))}.",
        ]
    except Exception as exc:
        return [f"Klargus ayarlari okunamadi: {exc}"]


def _settings_target_list() -> str:
    names = ", ".join(item["label"] for item in SETTINGS_TARGETS.values())
    return f"Acilabilir ayar sayfalari: {names}."


def system_control_report(mode: str = "standard") -> str:
    mode = str(mode or "standard").strip().lower()

    if mode == "last":
        previous = _read_last_report()
        return previous or "Kayitli sistem kontrol raporu yok."

    detailed = mode == "detailed"
    lines = ["Klargus sistem kontrol raporu:"]
    lines.extend(_platform_lines())
    lines.extend(_startup_lines())
    lines.extend(_audio_lines(detailed=detailed))
    lines.extend(_klargus_settings_lines())
    lines.append(_settings_target_list())
    lines.append("Politika: ayar sayfalarini acabilirim; kalici degisikliklerde once onay isterim.")

    result = "\n".join(lines)
    _write_last_report(result)
    return result


def open_windows_settings(target: str = "main") -> str:
    key = str(target or "main").strip().lower()
    item = SETTINGS_TARGETS.get(key)
    if not item:
        return f"Ayar hedefi bulunamadi: {target}"

    if platform.system().lower() != "windows":
        return "Windows ayarlari sadece Windows uzerinde acilabilir."

    try:
        os.startfile(item["uri"])
        return f"{item['label']} acildi."
    except Exception as exc:
        return f"{item['label']} acilamadi: {exc}"


def _set_pending(action: str, arg: str = APPROVED_TOKEN) -> None:
    # pending_actions kaldırıldı → onay durumu approval_state ile tutulur
    from brain.approval_state import set_pending_approval

    set_pending_approval({"action": action, "arg": arg})


def request_system_change(action: str) -> str:
    action = str(action or "").strip()
    label = SENSITIVE_ACTION_LABELS.get(action)
    if not label:
        return "Bu sistem degisikligi taninmiyor."

    tool_name = f"system_{action}"
    _set_pending(tool_name, APPROVED_TOKEN)
    return (
        f"Onay gerekiyor: {label} kalici bir sistem degisikligi yapar.\n"
        "Devam etmek icin 'evet' veya 'onayliyorum' de. Iptal icin 'hayir' de."
    )


def apply_system_change(action: str, approval_token: Optional[str] = None) -> ToolResult:
    action = str(action or "").strip()
    if approval_token != APPROVED_TOKEN:
        return ToolResult(message="Bu sistem degisikligi once acik onay gerektirir.",
                          ok=False, error="onaysiz")

    # Güvenli mod: onaylı+bilinen sistem değişikliği GERÇEKTEN uygulanmaz (yan etki yok).
    # Nereden çağrılırsa çağrılsın (dispatcher/doğrudan) sistem durumu değişmez.
    if is_safe_mode() and action in _MUTATING_SYSTEM_ACTIONS:
        return ToolResult(message=f"[test modu: sistem degisikligi '{action}' uygulanmadi]",
                          ok=True)

    try:
        if action == "startup_enable":
            from core.startup_manager import enable_startup

            path = enable_startup(str(PROJECT_ROOT))
            return ToolResult(message=f"Klargus Windows başlangıcına eklendi: {path}",
                              ok=True, data=str(path))

        if action == "startup_disable":
            from core.startup_manager import disable_startup

            removed = disable_startup()
            if removed:
                return ToolResult(message="Klargus Windows başlangıcından kaldırıldı.",
                                  ok=True, data=True)
            # Zaten yok: istenen son-durum (baslangicta degil) saglanmis -> ok=True
            return ToolResult(message="Klargus başlangıç kaydı zaten yok.",
                              ok=True, data=False)

        if action == "mic_auto_select":
            from audio.device_manager import auto_select_input_device

            # Delege zaten ToolResult doner (gercek ok propagate olur — basari varsayilmaz)
            return auto_select_input_device()

        if action == "mic_default":
            from audio.device_manager import set_input_device

            return set_input_device(None, source="manual")
    except Exception as exc:
        return ToolResult(message=f"Sistem değişikliği uygulanamadı: {exc}",
                          ok=False, error=str(exc))

    return ToolResult(message=f"Bilinmeyen sistem degisikligi: {action}",
                      ok=False, error="bilinmeyen_islem")


def blocked_system_change_message(text: str) -> str:
    norm = normalize_command(text)
    matched = next((term for term in CRITICAL_TERMS if term in norm), "")
    if not matched:
        matched = "kritik sistem degisikligi"
    return (
        f"Engelledim: '{matched}' kapsamina giren islem otomatik yapilmaz.\n"
        "Klargus bu alanda sadece durum okuyabilir, ayar sayfasini acabilir ve risk raporu verebilir."
    )


def handle_system_control_command(text: str = "", mode: Optional[str] = None) -> str:
    raw = str(text or "")
    norm = normalize_command(raw)

    if any(term in norm for term in CRITICAL_TERMS):
        return blocked_system_change_message(raw)

    selected_mode = str(mode or "").strip()
    if not selected_mode:
        route = route_system_command(raw)
        if route and route.kind == "system_control":
            selected_mode = route.mode

    selected_mode = selected_mode or "report"

    if selected_mode.startswith("open:"):
        return open_windows_settings(selected_mode.split(":", 1)[1])

    if selected_mode.startswith("request:"):
        return request_system_change(selected_mode.split(":", 1)[1])

    if selected_mode.startswith("apply:"):
        return apply_system_change(selected_mode.split(":", 1)[1], approval_token=APPROVED_TOKEN)

    if selected_mode == "last":
        return system_control_report(mode="last")

    if selected_mode == "short":
        lines = ["Klargus sistem ozeti:"]
        lines.extend(_startup_lines()[:1])
        lines.extend(_audio_lines(detailed=False)[:1])
        lines.extend(_klargus_settings_lines()[:3])
        return "\n".join(lines)

    if selected_mode == "detailed":
        return system_control_report(mode="detailed")

    return system_control_report(mode="standard")


def system_startup_enable(arg: str = "") -> ToolResult:
    return apply_system_change("startup_enable", approval_token=str(arg or ""))


def system_startup_disable(arg: str = "") -> ToolResult:
    return apply_system_change("startup_disable", approval_token=str(arg or ""))


def system_mic_auto_select(arg: str = "") -> ToolResult:
    return apply_system_change("mic_auto_select", approval_token=str(arg or ""))


def system_mic_default(arg: str = "") -> ToolResult:
    return apply_system_change("mic_default", approval_token=str(arg or ""))
