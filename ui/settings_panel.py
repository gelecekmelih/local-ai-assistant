# -*- coding: utf-8 -*-
"""
Klargus Settings Panel — yeniden tasarlandı
Modern dark kart sistemi, hover efektleri, daha iyi UX
"""
import tkinter as tk
from tkinter import messagebox, ttk
from brain.system_settings import load_system_settings, set_system_setting
from core.startup_manager import enable_startup, disable_startup, startup_enabled
from brain.ocr_manager import detect_tesseract, get_ocr_status_text
import os

from ui.ui_theme import BG, CARD, CARD2, BORDER, TEXT, MUTED, ACCENT

FONT_LABEL  = ("Segoe UI", 9)
FONT_TITLE  = ("Segoe UI", 10, "bold")
FONT_BTN    = ("Segoe UI", 9, "bold")
FONT_MONO   = ("Consolas", 9)


def apply_settings(values, *, runner=None, on_settings_changed=None, project_dir=None):
    """Ayarları persist et + Windows başlangıcını yapılandır ve GERÇEK durumla DOĞRULA.

    tkinter'dan bağımsız (test edilebilir). set_system_setting hatası yükseltilir
    → çağıran (save_all) app.py:1404 deseniyle showerror gösterir. Windows başlangıcı
    yazımı izin/politika yüzünden sessizce başarısız olursa startup_enabled() ile
    yakalanır ve DÜRÜST bir uyarı mesajı döndürülür ("kaydedildi" yalanı YOK).

    Dönüş: (startup_ok: bool, message: str).
    """
    for key, val in values.items():
        set_system_setting(key, val)

    startup_desired = bool(values.get("windows_startup_enabled"))
    if project_dir is None:
        project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # Registry/dosya yazımı izin yüzünden fırlatabilir ya da sessizce başarısız olabilir;
    # her iki durum da aşağıda startup_enabled() doğrulamasıyla yakalanır (yutulmaz).
    startup_error = False
    try:
        if startup_desired:
            enable_startup(project_dir)
        else:
            disable_startup()
    except Exception:
        startup_error = True

    startup_ok = (not startup_error) and (startup_enabled() == startup_desired)

    # Canlı uygulama best-effort — ayar zaten persist edildi (A blokları, dokunulmadı).
    if runner is not None:
        try:
            runner.set_mode(values.get("autonomy_mode"))
        except Exception:
            pass
    if on_settings_changed is not None:
        try:
            on_settings_changed()
        except Exception:
            pass

    if startup_ok:
        return True, "✓ Ayarlar kaydedildi."
    return (False,
            "Ayarlar kaydedildi ama Windows başlangıcı ayarlanamadı "
            "(yönetici izni gerekebilir).")


class ModernSwitch(tk.Canvas):
    """Animasyonlu toggle switch."""
    def __init__(self, parent, variable, **kw):
        super().__init__(parent, width=46, height=24,
                         bg=CARD, highlightthickness=0, cursor="hand2", **kw)
        self.variable = variable
        self._draw()
        self.bind("<Button-1>", self._toggle)
        variable.trace_add("write", lambda *a: self._draw())

    def _draw(self):
        self.delete("all")
        on = bool(self.variable.get())
        track_color = ACCENT if on else "#1e3a5c"
        thumb_x = 30 if on else 14
        self.create_rounded_rect(2, 4, 44, 20, radius=8, fill=track_color, outline="")
        self.create_oval(thumb_x-8, 4, thumb_x+8, 20, fill=TEXT, outline="")

    def create_rounded_rect(self, x1, y1, x2, y2, radius=8, **kw):
        points = [
            x1+radius, y1,  x2-radius, y1,
            x2, y1,  x2, y1+radius,
            x2, y2-radius,  x2, y2,
            x2-radius, y2,  x1+radius, y2,
            x1, y2,  x1, y2-radius,
            x1, y1+radius,  x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kw)

    def _toggle(self, e):
        self.variable.set(not self.variable.get())


class RadioGroup(tk.Frame):
    """Pill tarzı radio button grubu."""
    def __init__(self, parent, variable, options, **kw):
        super().__init__(parent, bg=CARD, **kw)
        self.variable = variable
        self.buttons = {}
        for val, label in options:
            btn = tk.Button(
                self, text=label,
                command=lambda v=val: self._select(v),
                bg=CARD2, fg=MUTED,
                relief="flat", borderwidth=0,
                padx=14, pady=6, cursor="hand2",
                font=FONT_BTN,
            )
            btn.pack(side="left", padx=2)
            self.buttons[val] = btn
        self._refresh()
        variable.trace_add("write", lambda *a: self._refresh())

    def _select(self, val):
        self.variable.set(val)

    def _refresh(self):
        current = self.variable.get()
        for val, btn in self.buttons.items():
            if val == current:
                btn.configure(bg=ACCENT, fg=BG)
            else:
                btn.configure(bg=CARD2, fg=MUTED)


def _section_header(parent, title, icon="◈"):
    f = tk.Frame(parent, bg=CARD)
    f.pack(fill="x", padx=0, pady=(14, 4))
    tk.Label(f, text=f"{icon}  {title.upper()}", bg=CARD, fg=MUTED,
              font=("Segoe UI", 8, "bold")).pack(anchor="w")
    tk.Frame(f, bg=BORDER, height=1).pack(fill="x", pady=(4, 0))
    return f


def _entry_field(parent, label, variable):
    row = tk.Frame(parent, bg=CARD)
    row.pack(fill="x", pady=4)
    tk.Label(row, text=label, bg=CARD, fg=MUTED,
              font=FONT_LABEL, width=20, anchor="w").pack(side="left")
    entry_f = tk.Frame(row, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
    entry_f.pack(side="left", fill="x", expand=True)
    tk.Entry(entry_f, textvariable=variable, bg=CARD, fg=TEXT,
              insertbackground=ACCENT, relief="flat", borderwidth=0,
              font=("Segoe UI", 9)).pack(fill="x", ipady=6, padx=6)
    return row


class SettingsPanel(tk.Toplevel):
    def __init__(self, master=None, runner=None, on_settings_changed=None):
        super().__init__(master)
        self.runner = runner
        self.on_settings_changed = on_settings_changed

        self.title("Klargus — Ayarlar")
        self.geometry("560x860+960+80")
        self.configure(bg=BG)
        self.resizable(False, True)
        self.minsize(480, 500)

        self.settings = load_system_settings()

        self.mode_var      = tk.StringVar(value=self.settings.get("autonomy_mode", "balanced"))
        self.model_var     = tk.StringVar(value=self.settings.get("model_name", "llama3.1:8b"))
        self.ocr_var       = tk.BooleanVar(value=bool(self.settings.get("ocr_enabled", True)))
        self.log_var       = tk.StringVar(value=self.settings.get("log_level", "INFO"))
        self.memory_var    = tk.StringVar(value=self.settings.get("memory_level", "normal"))
        self.browser_var   = tk.StringVar(value=self.settings.get("preferred_browser", "brave"))
        self.start_min_var = tk.BooleanVar(value=bool(self.settings.get("start_minimized", False)))
        self.tray_var      = tk.BooleanVar(value=bool(self.settings.get("close_to_tray", True)))
        self.startup_var   = tk.BooleanVar(value=bool(self.settings.get("windows_startup_enabled", False)))
        self.tess_var      = tk.StringVar(value=self.settings.get("tesseract_path", ""))
        self.ocr_status_var= tk.StringVar(value=get_ocr_status_text())

        self._build_ui()

    def _build_ui(self):
        # Başlık
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=0)

        hdr_canvas = tk.Canvas(header, height=56, bg=BG, highlightthickness=0)
        hdr_canvas.pack(fill="x")
        hdr_canvas.create_text(20, 28, text="⚙  KLARGUS AYARLARI",
                                fill=ACCENT, anchor="w",
                                font=("Segoe UI", 14, "bold"))
        hdr_canvas.create_text(20, 45, text="Tüm sistem tercihlerini buradan yönet",
                                fill=MUTED, anchor="w", font=("Segoe UI", 9))
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # Scroll alanı
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self.scroll_frame = tk.Frame(canvas, bg=BG)
        self.scroll_frame.bind("<Configure>",
                               lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # NOT (2026-10-03): bind_all global bir binding'dir — pencere kapanırken
        # (destroy) unbind edilmediği için, bu panel kapandıktan sonra tüm
        # uygulamadaki mouse-wheel scroll bu (artık yok olmuş) canvas'a
        # yönlendirilmeye çalışılıyor (TclError). Ayrıca birden fazla
        # SettingsPanel açılabildiğinden (app.py'de tekil-örnek koruması yok),
        # global bind her yeni panelde öncekini ezip gidiyordu. Çözüm: global
        # bind'ı sadece imleç bu canvas'ın üzerindeyken aktif tut (<Enter>/<Leave>),
        # ve panel kapanırken de garanti olarak unbind et.
        def _on_mousewheel(e):
            try:
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass

        def _bind_wheel(_e=None):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_wheel(_e=None):
            try:
                canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)
        self.bind("<Destroy>", _unbind_wheel, add="+")

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        wrap = tk.Frame(self.scroll_frame, bg=BG)
        wrap.pack(fill="both", expand=True, padx=16, pady=12)

        self._build_autonomy(wrap)
        self._build_model(wrap)
        self._build_ocr(wrap)
        self._build_log(wrap)
        self._build_memory(wrap)
        self._build_browser(wrap)
        self._build_startup(wrap)
        self._build_buttons(wrap)

    def _card(self, parent):
        f = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        f.pack(fill="x", pady=(0, 8))
        return f

    def _build_autonomy(self, wrap):
        _section_header(wrap, "Operasyon Modu", "⚡")
        card = self._card(wrap)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=12, pady=12)
        tk.Label(inner, text="Otonom seviyesi", bg=CARD, fg=MUTED,
                  font=FONT_LABEL).pack(anchor="w", pady=(0, 6))
        RadioGroup(inner, self.mode_var, [
            ("safe", "🔒  Safe"),
            ("balanced", "⚡  Balanced"),
            ("autonomous", "🤖  Autonomous"),
        ]).pack(anchor="w")
        tk.Label(inner, text="Safe: onay ister  ·  Balanced: akıllı karar  ·  Autonomous: tam bağımsız",
                  bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(6, 0))

    def _build_model(self, wrap):
        _section_header(wrap, "Ollama Model", "🧠")
        card = self._card(wrap)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=12, pady=12)
        _entry_field(inner, "Model adı", self.model_var)
        tk.Label(inner, text="Örnek: llama3.1:8b  ·  mistral:7b  ·  qwen2.5:7b",
                  bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))

    def _build_ocr(self, wrap):
        _section_header(wrap, "OCR Ayarları", "👁")
        card = self._card(wrap)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=12, pady=12)

        sw_row = tk.Frame(inner, bg=CARD)
        sw_row.pack(fill="x", pady=(0, 8))
        ModernSwitch(sw_row, self.ocr_var).pack(side="left")
        tk.Label(sw_row, text="  OCR aktif (Tesseract)", bg=CARD, fg=TEXT,
                  font=FONT_LABEL).pack(side="left")

        tk.Label(inner, textvariable=self.ocr_status_var, bg=CARD, fg=MUTED,
                  font=("Segoe UI", 8), wraplength=480, justify="left").pack(anchor="w", pady=(0, 8))

        _entry_field(inner, "Tesseract yolu", self.tess_var)

        btn_row = tk.Frame(inner, bg=CARD)
        btn_row.pack(fill="x", pady=(8, 0))
        for label, cmd, color in [
            ("Otomatik Bul", self.auto_detect_tesseract, CARD2),
            ("Durumu Yenile", self.refresh_ocr_status, "#1e3a5c"),
        ]:
            btn = tk.Button(btn_row, text=label, command=cmd,
                             bg=color, fg=TEXT, relief="flat", borderwidth=0,
                             padx=12, pady=6, cursor="hand2", font=FONT_BTN)
            btn.pack(side="left", padx=(0, 6))

    def _build_log(self, wrap):
        _section_header(wrap, "Log Seviyesi", "📋")
        card = self._card(wrap)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=12, pady=12)
        RadioGroup(inner, self.log_var, [
            ("INFO", "INFO"),
            ("WARN", "WARN"),
            ("ERROR", "ERROR"),
        ]).pack(anchor="w")

    def _build_memory(self, wrap):
        _section_header(wrap, "Bellek Yoğunluğu", "💾")
        card = self._card(wrap)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=12, pady=12)
        RadioGroup(inner, self.memory_var, [
            ("low", "Düşük"),
            ("normal", "Normal"),
            ("high", "Yüksek"),
        ]).pack(anchor="w")
        tk.Label(inner, text="Yüksek mod daha fazla RAM kullanır, daha iyi bağlam sunar.",
                  bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(6, 0))

    def _build_browser(self, wrap):
        _section_header(wrap, "Tercih Edilen Tarayıcı", "🌐")
        card = self._card(wrap)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=12, pady=12)
        RadioGroup(inner, self.browser_var, [
            ("brave", "Brave"),
            ("chrome", "Chrome"),
            ("firefox", "Firefox"),
            ("edge", "Edge"),
        ]).pack(anchor="w")

    def _build_startup(self, wrap):
        _section_header(wrap, "Başlangıç ve Tray", "🚀")
        card = self._card(wrap)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=12, pady=12)

        for var, label in [
            (self.start_min_var, "Başlangıçta minimize aç"),
            (self.tray_var,      "Kapatınca tray'e al"),
            (self.startup_var,   "Windows açılışında başlat"),
        ]:
            row = tk.Frame(inner, bg=CARD)
            row.pack(fill="x", pady=4)
            ModernSwitch(row, var).pack(side="left")
            tk.Label(row, text=f"  {label}", bg=CARD, fg=TEXT,
                      font=FONT_LABEL).pack(side="left")

    def _build_buttons(self, wrap):
        tk.Frame(wrap, bg=BORDER, height=1).pack(fill="x", pady=12)
        btn_row = tk.Frame(wrap, bg=BG)
        btn_row.pack(fill="x", pady=(0, 16))

        for label, cmd, bg_color, fg in [
            ("  Kaydet  ✓", self.save_all, ACCENT, BG),
            ("Kapat",       self.destroy,  CARD2,  TEXT),
        ]:
            btn = tk.Button(btn_row, text=label, command=cmd,
                             bg=bg_color, fg=fg,
                             activebackground=bg_color, activeforeground=fg,
                             relief="flat", borderwidth=0,
                             padx=16, pady=8, cursor="hand2", font=FONT_BTN)
            btn.pack(side="left", padx=(0, 8))

    def auto_detect_tesseract(self):
        path = detect_tesseract()
        if path:
            self.tess_var.set(path)
            self.ocr_status_var.set(f"✓ Tesseract bulundu: {path}")
        else:
            self.ocr_status_var.set("✗ Tesseract bulunamadı. Manuel yol girin.")

    def refresh_ocr_status(self):
        self.ocr_status_var.set(get_ocr_status_text())

    def save_all(self):
        values = {
            "autonomy_mode": self.mode_var.get(),
            "model_name": self.model_var.get().strip(),
            "ocr_enabled": bool(self.ocr_var.get()),
            "log_level": self.log_var.get(),
            "memory_level": self.memory_var.get(),
            "preferred_browser": self.browser_var.get(),
            "start_minimized": bool(self.start_min_var.get()),
            "close_to_tray": bool(self.tray_var.get()),
            "windows_startup_enabled": bool(self.startup_var.get()),
            "tesseract_path": self.tess_var.get().strip(),
        }
        # app.py:1404 deseni — yazma (set_system_setting) başarısızsa "kaydedildi"
        # değil showerror. Startup doğrulaması apply_settings içinde yapılır.
        try:
            startup_ok, msg = apply_settings(
                values, runner=self.runner, on_settings_changed=self.on_settings_changed)
        except Exception as e:
            messagebox.showerror("Klargus", f"Ayarlar kaydedilemedi: {e}")
            return

        if startup_ok:
            messagebox.showinfo("Klargus", msg)
        else:
            messagebox.showwarning("Klargus", msg)
