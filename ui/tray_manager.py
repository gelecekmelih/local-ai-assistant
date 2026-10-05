# -*- coding: utf-8 -*-
"""
ui/tray_manager.py — System Tray (Bildirim Alanı) Yöneticisi
=============================================================
Windows bildirim alanında Klargus simgesini gösterir.
Sağ tıklama menüsü: Göster / Gizle / Çıkış
"""

import threading
from pathlib import Path


class TrayManager:
    def __init__(self, app):
        self.app = app
        self._icon = None
        self._running = False
        self._thread = None
        # ICO dosyası
        self._ico_path = Path(__file__).resolve().parents[1] / "assets" / "klargus.ico"
        # Tray'i arka planda başlat
        self._start_tray()

    # ── Tray başlatma ────────────────────────────────────────────────────────

    def _make_image(self):
        """Tray ikonu için PIL Image üret."""
        try:
            from PIL import Image
            if self._ico_path.exists():
                img = Image.open(str(self._ico_path))
                # En büyük kare (PIL varsayılanı 256) → 64x64 LANCZOS:
                # yüksek DPI tray'de keskin görünür (32 + varsayılan filtre bulanıktı)
                img = img.convert("RGBA")
                img = img.resize((64, 64), Image.LANCZOS)
                return img
        except Exception:
            pass
        # Fallback: düz renkli kare
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse((2, 2, 30, 30), fill=(0, 212, 255, 255))
            return img
        except Exception:
            return None

    def _make_menu(self):
        """Sağ tıklama menüsü."""
        import pystray
        return (
            pystray.MenuItem("Klargus'u Göster", self._on_show, default=True),
            pystray.MenuItem("Gizle",             self._on_hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Çıkış",             self._on_quit),
        )

    def _start_tray(self):
        """Tray ikonunu arka plan thread'inde başlat."""
        def _run():
            try:
                import pystray
                img = self._make_image()
                if img is None:
                    print("[TrayManager] PIL Image üretilemedi — tray çalışmayacak.", flush=True)
                    return
                self._icon = pystray.Icon(
                    name="klargus",
                    icon=img,
                    title="Klargus",
                    menu=pystray.Menu(*self._make_menu()),
                )
                self._running = True
                self._icon.run()          # bu satır thread'i bloke eder
            except Exception as e:
                print(f"[TrayManager] Tray başlatma hatası: {e}", flush=True)

        self._thread = threading.Thread(target=_run, daemon=True, name="KlargusTray")
        self._thread.start()

    # ── Menü eylemleri ───────────────────────────────────────────────────────

    def _on_show(self, icon=None, item=None):
        """Tray simgesine çift tık veya 'Göster' seçilince."""
        try:
            self.app.after(0, self._do_show)
        except Exception as e:
            print(f"[TrayManager] Göster hatası: {e}", flush=True)

    def _on_hide(self, icon=None, item=None):
        """'Gizle' seçilince."""
        try:
            self.app.after(0, self._do_hide)
        except Exception as e:
            print(f"[TrayManager] Gizle hatası: {e}", flush=True)

    def _on_quit(self, icon=None, item=None):
        """'Çıkış' seçilince uygulamayı kapat."""
        try:
            if self._icon:
                self._icon.stop()
            self.app.after(0, self.app.destroy)
        except Exception as e:
            print(f"[TrayManager] Çıkış hatası: {e}", flush=True)

    def _do_show(self):
        """Ana thread'de pencereyi göster."""
        try:
            self.app.deiconify()
            self.app.lift()
            self.app.focus_force()
        except Exception:
            pass

    def _do_hide(self):
        """Ana thread'de pencereyi gizle."""
        try:
            self.app.withdraw()
        except Exception:
            pass

    # ── Dış API ──────────────────────────────────────────────────────────────

    def minimize_to_tray(self):
        """Pencereyi gizle, tray'de kal."""
        try:
            self.app.withdraw()
            return "Klargus arka plana alındı."
        except Exception as e:
            return f"Tray minimize hatası: {e}"

    def restore_from_tray(self):
        """Pencereyi tray'den geri getir."""
        try:
            self.app.deiconify()
            self.app.lift()
            self.app.focus_force()
            return "Klargus geri getirildi."
        except Exception as e:
            return f"Tray restore hatası: {e}"

    def stop(self):
        """Tray simgesini kaldır (uygulama kapatılırken)."""
        try:
            if self._icon and self._running:
                self._icon.stop()
                self._running = False
        except Exception:
            pass
