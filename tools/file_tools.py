# -*- coding: utf-8 -*-
"""
tools/file_tools.py — Temel dosya/klasör araçları (ToolResult sözleşmesi).

Yol çözümleme (_resolve_path — TEK KAYNAK, kopyala-yapıştır yok):
  "masaüstü"/"desktop" → Path.home()/"Desktop"
  göreli yol           → Desktop tabanlı
  mutlak yol           → aynen

Güvenlik: tüm işlemler (okuma/yazma/silme/listeleme/bilgi) yalnız kullanıcı
profili (Path.home()) altında; dışına istek → ok=False, error="guvenlik_siniri".
Okuma tarafı da yazma ile SİMETRİK sınırlıdır (bilinçli karar — sistem dosyası
sızıntısını engeller; gözden kaçma değil).

message alanı TÜRKÇE ve sesli okunmaya uygun kısa cümledir.
Hata kodları (ToolResult.error): dosya_bulunamadi, izin_yok, guvenlik_siniri,
klasor_degil, klasor_silinmez, ikili_dosya.
"""

from datetime import datetime
from pathlib import Path

from brain.tool_result import ToolResult
from brain.tool_registry import register_tool

_HOME = Path.home()
_DESKTOP = _HOME / "Desktop"
_MAX_READ = 50 * 1024       # 50 KB — okuma üst sınırı
_BINARY_SNIFF = 8 * 1024    # 8 KB — ikili tespiti için ilk blok


def _resolve_path(path) -> Path:
    """Kullanıcı yolunu çöz (masaüstü/desktop → Desktop; göreli → Desktop; mutlak → aynen)."""
    raw = str(path or "").strip().strip('"').strip("'")
    low = raw.lower()
    if low in ("masaüstü", "masaustu", "desktop"):
        return _DESKTOP
    for pref in ("masaüstü", "masaustu", "desktop"):
        for sep in ("/", "\\"):
            if low.startswith(pref + sep):
                return _DESKTOP / raw[len(pref) + 1:]
    if not raw:
        return _DESKTOP
    p = Path(raw)
    return p if p.is_absolute() else (_DESKTOP / raw)


def _within_home(p: Path) -> bool:
    """p, kullanıcı profili (home) altında mı? (yazma/silme güvenlik sınırı)"""
    try:
        p.resolve().relative_to(_HOME.resolve())
        return True
    except Exception:
        return False


def _where(p: Path) -> str:
    """Sesli okuma için kısa konum ifadesi."""
    try:
        if p.parent.resolve() == _DESKTOP.resolve():
            return "masaüstünde"
    except Exception:
        pass
    return f"{p.parent} içinde"


def create_file(path, content="") -> ToolResult:
    """Dosya oluştur ve içerik yaz (üst klasörler otomatik açılır)."""
    p = _resolve_path(path)
    if not _within_home(p):
        return ToolResult(message="Bu konuma yazamam — güvenlik sınırı dışında.",
                          ok=False, error="guvenlik_siniri")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content or ""), encoding="utf-8")
        return ToolResult(message=f"{p.name} {_where(p)} oluşturuldu.", ok=True, data=str(p))
    except PermissionError:
        return ToolResult(message=f"{p.name} oluşturulamadı — izin yok.", ok=False, error="izin_yok")
    except Exception as e:
        return ToolResult(message=f"{p.name} oluşturulamadı: {e}", ok=False, error=str(e))


def read_file(path) -> ToolResult:
    """Metin dosyasının içeriğini oku (data'ya tam içerik, en fazla 50KB)."""
    p = _resolve_path(path)
    if not _within_home(p):
        return ToolResult(message="Bu dosyayı okuyamam — güvenlik sınırı dışında.",
                          ok=False, error="guvenlik_siniri")
    if not p.exists() or not p.is_file():
        return ToolResult(message=f"{p.name} bulunamadı.", ok=False, error="dosya_bulunamadi")
    try:
        raw = p.read_bytes()
        # İkili (binary) koruması: ilk blokta null byte varsa metin değildir →
        # errors="replace" ile çöp döndürmek yerine açık hata ver (sessiz yanlış olmasın).
        if b"\x00" in raw[:_BINARY_SNIFF]:
            return ToolResult(message=f"{p.name} metin dosyası değil (ikili içerik), okuyamam.",
                              ok=False, error="ikili_dosya")
        text = raw[:_MAX_READ].decode("utf-8", errors="replace")
        if len(raw) > _MAX_READ:
            return ToolResult(
                message=f"{p.name} okundu (dosya 50KB'den büyük, ilk 50KB alındı).",
                ok=True, data=text + "\n\n... (kırpıldı — dosya 50KB'den büyük)")
        return ToolResult(message=f"{p.name} okundu.", ok=True, data=text)
    except Exception as e:
        return ToolResult(message=f"{p.name} okunamadı: {e}", ok=False, error=str(e))


def append_file(path, content="") -> ToolResult:
    """Dosyanın sonuna metin ekle (yoksa oluşturur)."""
    p = _resolve_path(path)
    if not _within_home(p):
        return ToolResult(message="Bu konuma yazamam — güvenlik sınırı dışında.",
                          ok=False, error="guvenlik_siniri")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(str(content or ""))
        return ToolResult(message=f"{p.name} dosyasının sonuna eklendi.", ok=True, data=str(p))
    except PermissionError:
        return ToolResult(message=f"{p.name} dosyasına eklenemedi — izin yok.", ok=False, error="izin_yok")
    except Exception as e:
        return ToolResult(message=f"{p.name} dosyasına eklenemedi: {e}", ok=False, error=str(e))


def list_directory(path="") -> ToolResult:
    """Klasördeki dosya ve klasörleri boyutlarıyla listele."""
    p = _resolve_path(path)
    if not _within_home(p):
        return ToolResult(message="Bu klasörü listeleyemem — güvenlik sınırı dışında.",
                          ok=False, error="guvenlik_siniri")
    if not p.exists():
        return ToolResult(message=f"{p.name or 'Klasör'} bulunamadı.", ok=False, error="dosya_bulunamadi")
    if not p.is_dir():
        return ToolResult(message=f"{p.name} bir klasör değil.", ok=False, error="klasor_degil")
    try:
        items = []
        for it in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                items.append({
                    "name": it.name,
                    "is_dir": it.is_dir(),
                    "size_bytes": (it.stat().st_size if it.is_file() else 0),
                })
            except Exception:
                continue
        _loc = "Masaüstünde" if p.resolve() == _DESKTOP.resolve() else str(p)
        return ToolResult(message=f"{_loc} {len(items)} öğe var.", ok=True, data=items)
    except Exception as e:
        return ToolResult(message=f"Klasör listelenemedi: {e}", ok=False, error=str(e))


def delete_file(path) -> ToolResult:
    """Dosyayı sil (YALNIZ dosya — güvenlik için klasör silinmez)."""
    p = _resolve_path(path)
    if not _within_home(p):
        return ToolResult(message="Bu dosyayı silemem — güvenlik sınırı dışında.",
                          ok=False, error="guvenlik_siniri")
    if not p.exists():
        return ToolResult(message=f"{p.name} bulunamadı.", ok=False, error="dosya_bulunamadi")
    if p.is_dir():
        return ToolResult(message=f"{p.name} bir klasör — güvenlik için klasör silmiyorum.",
                          ok=False, error="klasor_silinmez")
    try:
        p.unlink()
        return ToolResult(message=f"{p.name} silindi.", ok=True, data=str(p))
    except PermissionError:
        return ToolResult(message=f"{p.name} silinemedi — izin yok.", ok=False, error="izin_yok")
    except Exception as e:
        return ToolResult(message=f"{p.name} silinemedi: {e}", ok=False, error=str(e))


def file_info(path) -> ToolResult:
    """Dosya/klasör var mı, boyutu ve son değiştirilme tarihi."""
    p = _resolve_path(path)
    if not _within_home(p):
        return ToolResult(message="Bu yolun bilgisini veremem — güvenlik sınırı dışında.",
                          ok=False, error="guvenlik_siniri")
    if not p.exists():
        return ToolResult(message=f"{p.name} bulunamadı.", ok=False, error="dosya_bulunamadi")
    try:
        st = p.stat()
        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        kind = "klasör" if p.is_dir() else "dosya"
        size = st.st_size if p.is_file() else 0
        return ToolResult(
            message=f"{p.name}: {kind}, {size} bayt, son değişiklik {mtime}.",
            ok=True,
            data={"name": p.name, "is_dir": p.is_dir(), "size_bytes": size,
                  "modified": mtime, "path": str(p)})
    except Exception as e:
        return ToolResult(message=f"{p.name} bilgisi alınamadı: {e}", ok=False, error=str(e))


def create_directory(path) -> ToolResult:
    """Klasör oluştur (üst klasörlerle birlikte, parents=True)."""
    p = _resolve_path(path)
    if not _within_home(p):
        return ToolResult(message="Bu konuma klasör oluşturamam — güvenlik sınırı dışında.",
                          ok=False, error="guvenlik_siniri")
    try:
        p.mkdir(parents=True, exist_ok=True)
        return ToolResult(message=f"{p.name} klasörü {_where(p)} oluşturuldu.", ok=True, data=str(p))
    except PermissionError:
        return ToolResult(message=f"{p.name} klasörü oluşturulamadı — izin yok.", ok=False, error="izin_yok")
    except Exception as e:
        return ToolResult(message=f"{p.name} klasörü oluşturulamadı: {e}", ok=False, error=str(e))


register_tool("create_file", create_file)
register_tool("read_file", read_file)
register_tool("append_file", append_file)
register_tool("list_directory", list_directory)
register_tool("delete_file", delete_file)          # system_tools.delete_file DEVRE DIŞI → bu geçerli
register_tool("file_info", file_info)
register_tool("create_directory", create_directory)
