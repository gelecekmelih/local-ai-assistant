import json
import logging
import os
import random
import re
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

log = logging.getLogger("klargus.basic_tools")

from brain.tool_registry import list_tools, register_tool
from brain.tool_result import ToolResult
from control.commands import run_command
from control.smart_finder import (
    build_program_index,
    find_best_file,
    find_best_folder,
    normalize as _smart_normalize,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA = _PROJECT_ROOT / "data"
DATA.mkdir(exist_ok=True)
HOME = Path.home()

FAV_FILE = DATA / "favorite_songs.json"
LEARNED_FILE = DATA / "learned_commands.json"

COMMON_FOLDERS = {
    "masaustu": HOME / "Desktop",
    "desktop": HOME / "Desktop",
    "indirilenler": HOME / "Downloads",
    "downloads": HOME / "Downloads",
    "belgeler": HOME / "Documents",
    "documents": HOME / "Documents",
    "muzik": HOME / "Music",
    "music": HOME / "Music",
    "resimler": HOME / "Pictures",
    "pictures": HOME / "Pictures",
    "videolar": HOME / "Videos",
    "videos": HOME / "Videos",
}

GAME_URLS = {
    "cs2": "steam://rungameid/730",
    "counter strike 2": "steam://rungameid/730",
    "counter strike": "steam://rungameid/730",
    "elden ring": "steam://rungameid/1245620",
    "cyberpunk 2077": "steam://rungameid/1091500",
    "witcher 3": "steam://rungameid/292030",
    "gta 5": "steam://rungameid/271590",
    "red dead redemption 2": "steam://rungameid/1174180",
}


def normalize(text):
    return _smart_normalize(text)


def _open_path(path) -> ToolResult:
    if not path:
        return ToolResult(message="Yol bulunamadı.", ok=False, error="yol_bulunamadi")

    p = Path(path)
    if not p.exists():
        return ToolResult(message="Yol bulunamadı.", ok=False, error="yol_bulunamadi")

    os.startfile(str(p))
    return ToolResult(message=f"Açılıyor: {p}", ok=True, data=str(p))


def _load_favorites():
    if not FAV_FILE.exists():
        return []
    try:
        # utf-8-sig: BOM'lu kaydedilmiş dosyaları da okur (düz utf-8 ile BOM
        # json.loads'u patlatıyordu ve favoriler sessizce boş dönüyordu)
        data = json.loads(FAV_FILE.read_text(encoding="utf-8-sig"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, str) and x.strip()]
    except Exception as e:
        # Sessizce [] dönmek davranış olarak korunuyor (çağıranlar bunu
        # "favori yok" ile ayırt etmiyor) ama artık en azından loglanıyor —
        # eskiden bozuk/okunamayan bir dosya sessizce "favori yok" gibi
        # görünüyordu, ayırt edilemiyordu.
        log.warning("favorite_songs.json okunamadı (%s): %s", FAV_FILE, e)
    return []


def _load_learned():
    if not LEARNED_FILE.exists():
        return {}
    try:
        data = json.loads(LEARNED_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("learned_commands.json okunamadı (%s): %s", LEARNED_FILE, e)
        return {}


def _save_learned(data):
    """Atomik yazma — yarım kalırsa öğrenilen komutlar korunur."""
    tmp = LEARNED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LEARNED_FILE)


def _open_search(base_url, query, label) -> ToolResult:
    query = str(query or "").strip()
    if not query:
        # Validation: aksiyon yapilmadi ama davranis-koruma icin ok=True (rapor'da isaretli)
        return ToolResult(message=f"{label} için arama metni yok.", ok=True, data=None)
    webbrowser.open(base_url + quote_plus(query))
    return ToolResult(message=f"{label} araması açıldı: {query}", ok=True, data=query)


def tool_rebuild_program_index(arg=None) -> ToolResult:
    programs = build_program_index()
    return ToolResult(message=f"{len(programs)} program/kısayol tarandı.",
                      ok=True, data=len(programs))


def tool_spotify_search(arg) -> ToolResult:
    query = str(arg or "").strip()
    if not query:
        return ToolResult(message="Spotify için arama metni yok.", ok=True, data=None)
    webbrowser.open("https://open.spotify.com/search/" + quote_plus(query))
    return ToolResult(message=f"Spotify araması açıldı: {query}", ok=True, data=query)



def tool_youtube_search(arg) -> ToolResult:
    return _open_search("https://www.youtube.com/results?search_query=", arg, "YouTube")


def tool_google_search(arg) -> ToolResult:
    return _open_search("https://www.google.com/search?q=", arg, "Google")


def web_search(arg=None):
    """DuckDuckGo ile arama yap — sonuçları metin olarak döndür (tarayıcı açmaz)."""
    query = str(arg or "").strip()
    if not query:
        return ToolResult(message="Arama sorgusu belirtilmedi.", ok=True, data=None)
    try:
        # ddgs yeni isim, duckduckgo_search eski isim — ikisini de dene
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as client:
            results = list(client.text(query, max_results=5, region="tr-tr"))
        if not results:
            return ToolResult(message=f"'{query}' için sonuç bulunamadı.",
                              ok=False, error="sonuc_yok")
        lines = [f"'{query}' arama sonuclari:\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "").strip()
            body  = r.get("body", "").strip()[:180]
            href  = r.get("href", "").strip()
            lines.append(f"{i}. {title}\n   {body}\n   {href}")
        return ToolResult(message="\n\n".join(lines), ok=True, data=results)
    except Exception as e:
        # Tarayıcı AÇMA — sessizce metin dön (arama modülü yoksa/ağ hatası).
        # Eskiden _open_search ile Google açılıyordu; bu ekranı/tarayıcıyı kaçırıyordu.
        # Exception handler → gercek basarisizlik → ok=False (basari VARSAYMA).
        return ToolResult(
            message=f"Web araması yapılamadı ({e}). Arama modülü kurulu değil veya ağ yok.",
            ok=False, error=str(e))


register_tool("web_search", web_search)


def tool_open_website(arg) -> ToolResult:
    url = str(arg or "").strip()
    if not url:
        return ToolResult(message="Açılacak site yok.", ok=True, data=None)

    if not url.startswith(("http://", "https://")):
        if "." not in url:
            return ToolResult(message="Geçerli site adı bulunamadı.", ok=False,
                              error="gecersiz_site")
        url = "https://" + url

    webbrowser.open(url)
    return ToolResult(message=f"Site açılıyor: {url}", ok=True, data=url)


def tool_spotify_liked_songs(arg=None):
    """Spotify 'Beğenilen Şarkılar' listesini açar.

    Masaüstü uygulaması URI'si en güvenilir yol (spotify:collection:tracks) —
    ekran okuma/tıklama gerektirmez. Uygulama yoksa web sürümüne düşer.
    """
    import os as _os
    try:
        _os.startfile("spotify:collection:tracks")
        return ToolResult(
            message=("Spotify Beğenilen Şarkılar açıldı. "
                     "Çalmak için listedeki ilk şarkıya çift tıklanabilir (find_and_double_click)."),
            ok=True, data="app")
    except Exception:
        # Uygulama yoksa web'e düş — bu da BAŞARILI bir yol (hata degil) → ok=True
        webbrowser.open("https://open.spotify.com/collection/tracks")
        return ToolResult(message="Beğenilen Şarkılar web'de açıldı.", ok=True, data="web")


register_tool("spotify_liked_songs", tool_spotify_liked_songs)


def tool_random_music(arg=None) -> ToolResult:
    songs = _load_favorites()
    if not songs:
        return ToolResult(message="Favori müzik listesi bulunamadı veya boş.",
                          ok=False, error="favori_yok")

    song = random.choice(songs)
    webbrowser.open("https://open.spotify.com/search/" + quote_plus(song))
    return ToolResult(message=f"Rastgele müzik açılıyor: {song}", ok=True, data=song)


def tool_open_program(arg) -> ToolResult:
    import shutil as _shutil
    name = str(arg or "").strip()
    if not name:
        return ToolResult(message="Program adı yok.", ok=True)

    # 1) Bilinen APP_TARGETS ile dene — run_command artık ToolResult döner:
    #    string-eşleştirme YOK, gerçek .ok'a bakılır (karar-mantığı bağı, Görev 8).
    result = run_command(f"{normalize(name)} aç")
    if result and result.ok:
        return result

    # 2) Doğrudan yürütülebilir dosya olarak dene (discord.exe, notepad.exe, ...)
    exe = name if name.endswith(".exe") else name + ".exe"
    found = _shutil.which(exe) or _shutil.which(name)
    if found:
        try:
            subprocess.Popen([found], creationflags=subprocess.CREATE_NO_WINDOW)
            return ToolResult(message=f"{name} açıldı.", ok=True)
        except Exception:
            pass

    # 3) os.startfile ile dene (UWP uygulamaları, URI protokolleri vb.)
    try:
        os.startfile(name)
        return ToolResult(message=f"{name} açıldı.", ok=True)
    except Exception:
        pass

    # 4) Start menüsünde ara
    try:
        from control.smart_finder import find_best_file as _fbf
        lnk = _fbf(name)
        if lnk:
            os.startfile(str(lnk))
            return ToolResult(message=f"{name} açıldı.", ok=True)
    except Exception:
        pass

    return ToolResult(message=f"'{name}' programı bulunamadı.", ok=False, error="program_bulunamadi")


def tool_open_folder(arg) -> ToolResult:
    key = normalize(arg)
    if not key:
        return ToolResult(message="Klasör adı yok.", ok=True, data=None)

    if key in COMMON_FOLDERS:
        return _open_path(COMMON_FOLDERS[key])

    p = Path(str(arg or "").strip())
    if p.exists() and p.is_dir():
        return _open_path(p)

    found = find_best_folder(key)
    if found:
        return _open_path(found)

    return ToolResult(message="Klasör bulunamadı.", ok=False, error="klasor_bulunamadi")


def tool_open_file(arg) -> ToolResult:
    raw = str(arg or "").strip()
    if not raw:
        return ToolResult(message="Dosya adı yok.", ok=True, data=None)

    p = Path(raw)
    if p.exists() and p.is_file():
        return _open_path(p)

    found = find_best_file(raw)
    if found:
        return _open_path(found)

    return ToolResult(message="Dosya bulunamadı.", ok=False, error="dosya_bulunamadi")


def tool_launch_game(arg) -> ToolResult:
    key = normalize(arg)
    if not key:
        return ToolResult(message="Oyun adı yok.", ok=True, data=None)

    url = GAME_URLS.get(key)
    if not url:
        # Kayitli launcher yok — davranis-koruma icin ok=True (rapor'da isaretli)
        return ToolResult(message="Bu oyun için kayıtlı başlatıcı yok.", ok=True, data=None)

    webbrowser.open(url)
    return ToolResult(message=f"Oyun başlatılıyor: {arg}", ok=True, data=url)


def tool_list_tools(arg=None) -> ToolResult:
    names = list_tools()
    if not names:
        return ToolResult(message="Kayıtlı araç yok.", ok=True, data=[])
    return ToolResult(message="Kullanılabilir araçlar: " + ", ".join(names),
                      ok=True, data=names)


def tool_system_startup_enable(arg=None) -> ToolResult:
    from core.system_control import system_startup_enable
    return system_startup_enable(str(arg or ""))


def tool_system_startup_disable(arg=None) -> ToolResult:
    from core.system_control import system_startup_disable
    return system_startup_disable(str(arg or ""))


def tool_system_mic_auto_select(arg=None) -> ToolResult:
    from core.system_control import system_mic_auto_select
    return system_mic_auto_select(str(arg or ""))


def tool_system_mic_default(arg=None) -> ToolResult:
    from core.system_control import system_mic_default
    return system_mic_default(str(arg or ""))


def tool_learn_command(arg) -> ToolResult:
    text = str(arg or "").strip()
    match = re.match(
        r"^\s*bundan\s+sonra\s+(.+?)\s+(?:dediğimde|dedigimde|deyince|dediğim zaman|dedigim zaman)\s+(.+?)\s*$",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return ToolResult(message="Öğretme cümlesini anlayamadım.", ok=True, data=None)

    trigger = match.group(1).strip()
    command = match.group(2).strip()
    if not trigger or not command:
        return ToolResult(message="Öğretilecek komut eksik.", ok=True, data=None)

    data = _load_learned()
    data[normalize(trigger)] = command
    _save_learned(data)
    return ToolResult(message=f"Öğrendim: '{trigger}' -> {command}", ok=True,
                      data={trigger: command})


def tool_run_learned_command(arg):
    key = normalize(arg)
    data = _load_learned()
    command = data.get(key)
    if not command:
        return None
    return run_command(command)


def tool_list_learned_commands(arg=None) -> ToolResult:
    data = _load_learned()
    if not data:
        return ToolResult(message="Öğretilmiş komut yok.", ok=True, data={})

    lines = ["Öğretilmiş komutlar:"]
    for key, value in data.items():
        lines.append(f"- {key} => {value}")
    return ToolResult(message="\n".join(lines), ok=True, data=data)


def tool_recipe_summary(arg=None) -> ToolResult:
    """Ajan'ın öğrendiği masaüstü görev tariflerini listeler."""
    try:
        from brain.task_recipe_store import recipe_summary
        return recipe_summary()   # zaten ToolResult → passthrough
    except Exception as e:
        # Exception handler → gercek basarisizlik → ok=False (basari VARSAYMA)
        return ToolResult(message=f"Tarif listesi alınamadı: {e}", ok=False, error=str(e))


def rebuild_program_index(arg="") -> ToolResult:
    programs = build_program_index()
    count = len(programs) if isinstance(programs, dict) else 0
    return ToolResult(message=f"{count} program/kısayol tarandı.", ok=True, data=count)


def open_program(arg=""):
    return tool_open_program(arg)


register_tool("rebuild_program_index", tool_rebuild_program_index)
register_tool("spotify_search", tool_spotify_search)
register_tool("youtube_search", tool_youtube_search)
register_tool("google_search", tool_google_search)
register_tool("open_website", tool_open_website)
register_tool("random_music", tool_random_music)
register_tool("open_program", tool_open_program)
register_tool("open_folder", tool_open_folder)
register_tool("open_file", tool_open_file)
register_tool("launch_game", tool_launch_game)
register_tool("list_tools", tool_list_tools)
register_tool("system_startup_enable", tool_system_startup_enable)
register_tool("system_startup_disable", tool_system_startup_disable)
register_tool("system_mic_auto_select", tool_system_mic_auto_select)
register_tool("system_mic_default", tool_system_mic_default)
register_tool("learn_command", tool_learn_command)
register_tool("run_learned_command", tool_run_learned_command)
register_tool("list_learned_commands", tool_list_learned_commands)
register_tool("recipe_summary", tool_recipe_summary)
