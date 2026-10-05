# -*- coding: utf-8 -*-
"""
ui/hud_panel.py — Klargus Sinematik HUD Paneli  v3
===================================================
2560×1440 için tam sinematik yeniden tasarım.

Layout:
  ┌─ HEADER (canvas, geometrik dekorlar, saat, animasyonlu dot) ────────────┐
  │  ◈  KLARGUS  ── ── ── OPERASYON MERKEZİ v2.0 ── ── ──  ● SYS  HH:MM  │
  ├─ BODY ───────────────────────────────────────────┬─ SIDE (380px) ───────┤
  │  ╔─ YANIT // KLARGUS ÇIKTISI ─────────────────╗ │  ┌─ DURUM ─────────┐ │
  │  ║                                             ║ │  └─────────────────┘ │
  │  ║   [büyük cyan yanıt metni]                  ║ │  ┌─ SON KOMUT ─────┐ │
  │  ║                                             ║ │  └─────────────────┘ │
  │  ╚═════════════════════════════════════════════╝ │  ┌─ SON EYLEM ─────┐ │
  │                                                  │  └─────────────────┘ │
  │                                                  │  ┌─ AKTİF PENCERE ─┐ │
  │                                                  │  └─────────────────┘ │
  │                                                  │  ┌─ BEKLEYEN ──────┐ │
  │                                                  │  └─────────────────┘ │
  ├─ LOG (sol accent çizgisi) ───────────────────────┴──────────────────────┤
  │ ▸ sistem logu akışı                                                      │
  ├─ ENTRY ──────────────────────────────────────────────────────────────────┤
  │  ❯  [komut kutusu]                                              GÖNDER  │
  └──────────────────────────────────────────────────────────────────────────┘
"""

import threading
import time
import tkinter as tk
from datetime import datetime

# ── Renk paleti ───────────────────────────────────────────────────────────────
BG        = "#020810"
BAR_BG    = "#010608"
CARD      = "#050f1c"
CARD2     = "#071520"
BORDER    = "#0a2030"
CYAN      = "#00d4ff"
CYAN_DIM  = "#006e99"
CYAN_DK   = "#003a52"
CYAN_MID  = "#009bb8"
TEXT      = "#a8c4d8"
TEXT_DIM  = "#4a6878"
SEP       = "#0a1e2e"
ACCENT    = "#00aacc"
ACCENT_DK = "#003d52"
GREEN     = "#00e676"
GREEN_DK  = "#003320"
YELLOW    = "#ffcc00"
YELLOW_DK = "#332800"
RED       = "#ff3344"

FONT_LARGE = ("Consolas", 15, "bold")
FONT_SMALL = ("Consolas", 8)
FONT_LABEL = ("Consolas", 7, "bold")
FONT_CARD  = ("Consolas", 10)
FONT_MONO  = ("Consolas", 8)

SIDE_W = 390   # Sağ panel genişliği (px)
HDR_H  = 58    # Üst header yüksekliği (px)
EBAR_H = 50    # Komut girişi yüksekliği (px)


class HUDPanel(tk.Frame):
    """
    Sinematik HUD — canvas tabanlı header, animasyonlu bileşenler,
    köşe bracket'lı bilgi kartları.

    Dışa açık API (app.py çağırır):
      command_entry   — Entry widget
      set_status(v)
      set_command(v)
      set_answer(v)
      log(msg)
      set_action(v)
      set_window(v)
      set_pending(v)
    """

    def __init__(self, parent, process_callback=None, **kwargs):
        super().__init__(parent, bg=BG, **kwargs)
        self._process_cb   = process_callback
        self._boot_done    = False
        self._pulse_state  = True

        # Canvas item ID'leri (Configure sonrası set edilir)
        self._hdr_status_id = None
        self._hdr_clock_id  = None
        self._hdr_dot_id    = None

        self._status_var = tk.StringVar(value="Hazır")
        self._cmd_var    = tk.StringVar(value="—")
        self._card_vars  = {}

        # Yanıt başlığındaki CPU/RAM göstergesi — eskiden statik metin
        # ("CORE:8x MEM:OK FREQ:2.4GHz") hiç değişmiyordu; artık psutil ile
        # gerçek, canlı kullanım gösteriliyor.
        self._ans_hdr_canvas = None
        self._ans_stats_id   = None
        self._cpu_pct        = None
        self._mem_pct        = None

        # Header'daki statik metin yerine hareket eden 'tarama' ışığı
        self._hdr_scan_id   = None
        self._hdr_scan_span = (0, 0)
        self._hdr_scan_y    = 0

        # Komut girişi çevresindeki nabız animasyonu (JARVIS tarzı, klasik
        # düz kutu yerine) — odaklanınca _btn_highlight devreye girer, bu
        # döngü sadece boştayken çalışır.
        self._entry_pulse_state = True

        self._build()
        self.after(200,  self._boot_sequence)
        self.after(600,  self._pulse_dot)
        self.after(1000, self._tick_clock)
        self.after(1500, self._poll_system_stats)
        self.after(700,  self._pulse_entry)
        self.after(40,   self._scan_sweep)

    # ══════════════════════════════════════════════════════════════════════════
    # İNŞA
    # ══════════════════════════════════════════════════════════════════════════

    def _build(self):
        # Tkinter pack kuralı: expand=True olan widget son çağrılmalı.
        # Sabit yükseklikteki elemanlar önce side="bottom" ile yerleştirilir,
        # aksi hâlde body tüm alanı yiyor ve alttaki elemanlar görünmüyor.
        self._build_header()        # üstte sabit — pack top
        self._build_entry()         # altta sabit — pack bottom (ÖNCELİKLİ)
        tk.Frame(self, bg=CYAN_DK, height=1).pack(side="bottom", fill="x")
        self._build_log()           # altta sabit — pack bottom
        tk.Frame(self, bg=CYAN_DK, height=1).pack(side="bottom", fill="x")
        self._build_body()          # geri kalan tüm alanı alır — en son

    # ── Header (Canvas) ───────────────────────────────────────────────────────

    def _build_header(self):
        self._hdr = tk.Canvas(
            self, bg=BAR_BG, height=HDR_H,
            highlightthickness=0, bd=0,
        )
        self._hdr.pack(fill="x")
        self._hdr.bind("<Configure>", self._draw_header)
        self._hdr.after(120, self._draw_header)

    def _draw_header(self, event=None):
        c = self._hdr
        c.delete("all")
        W = c.winfo_width()
        H = HDR_H
        if W < 20:
            return

        # --- Diyagonal arka plan hatch çizgileri ---
        for x in range(-H, W + H, 48):
            c.create_line(x, 0, x + H, H, fill="#020d1c", width=1)

        # --- Sol dikey accent çizgisi ---
        c.create_line(0, 0, 0, H, fill=CYAN, width=3)

        # --- Logo ---
        c.create_text(16, H // 2 - 9, anchor="w",
                      text="◈  KLARGUS", fill=CYAN,
                      font=("Consolas", 15, "bold"))
        c.create_text(16, H // 2 + 9, anchor="w",
                      text="OPERASYON MERKEZİ", fill=CYAN_DIM,
                      font=("Consolas", 7, "bold"))

        # --- Orta dekoratif kesik çizgiler ---
        mid = W // 2
        c.create_line(210, H // 2, mid - 160, H // 2,
                      fill=CYAN_DK, width=1, dash=(3, 5))
        c.create_line(mid + 160, H // 2, W - 220, H // 2,
                      fill=CYAN_DK, width=1, dash=(3, 5))

        # --- Orta: statik metin yerine hareketli tarama ışığı ---
        # (eskiden burada hep aynı "SovereignBrain Aktif / v2.0..." metni
        # dururdu — footer'da zaten aynı bilgi var; statik tekrar yerine
        # gerçek hareket.)
        self._hdr_scan_y    = H // 2
        self._hdr_scan_span = (210, W - 220)
        self._hdr_scan_id   = c.create_line(210, H // 2, 210, H // 2,
                                             fill=CYAN, width=2)

        # --- Sağ bölüm: dot + SYS + status + clock ---
        rx = W - 14

        # Saat
        self._hdr_clock_id = c.create_text(rx, H // 2 + 12, anchor="e",
                                            text=self._now_str(),
                                            fill=TEXT_DIM,
                                            font=("Consolas", 8))

        # Status
        self._hdr_status_id = c.create_text(rx, H // 2 - 8, anchor="e",
                                             text=self._status_var.get(),
                                             fill=CYAN,
                                             font=("Consolas", 8, "bold"))

        # Pulsing dot (oval)
        dot_x = W - 185
        dot_y = H // 2 - 8
        r = 5
        self._hdr_dot_id = c.create_oval(dot_x - r, dot_y - r,
                                          dot_x + r, dot_y + r,
                                          fill=CYAN, outline="",
                                          tags="dot")
        c.create_text(dot_x + r + 4, dot_y, anchor="w",
                      text="SYS", fill=CYAN_DIM,
                      font=("Consolas", 7, "bold"))

        # --- Alt border çizgisi ---
        c.create_line(0, H - 1, W, H - 1, fill=CYAN_DK, width=1)

    @staticmethod
    def _now_str() -> str:
        return datetime.now().strftime("%H:%M:%S")

    # ── Body ──────────────────────────────────────────────────────────────────

    def _build_body(self):
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        self._build_answer_area(body)
        tk.Frame(body, bg=BORDER, width=1).pack(side="left", fill="y")
        self._build_side_panel(body)

    # ── Yanıt alanı (sol, genişleyici) ────────────────────────────────────────

    def _build_answer_area(self, parent):
        left = tk.Frame(parent, bg=BG)
        left.pack(side="left", fill="both", expand=True)

        # Canvas başlık şeridi
        ans_hdr = tk.Canvas(left, bg=CARD, height=34,
                            highlightthickness=0, bd=0)
        ans_hdr.pack(fill="x")

        def _draw_ans_hdr(event=None, c=ans_hdr):
            c.delete("all")
            W = c.winfo_width()
            H = 34
            if W < 10:
                return
            # Sol köşe bracket
            c.create_line(8,  6, 8,  H - 6, fill=CYAN, width=2)
            c.create_line(8,  6, 24, 6,     fill=CYAN, width=2)
            # Başlık
            c.create_text(30, H // 2, anchor="w",
                          text="YANIT  //  KLARGUS ÇIKTISI",
                          fill=CYAN_DIM, font=("Consolas", 8, "bold"))
            # Sağ teknik metrik — canlı CPU/RAM (bkz. _poll_system_stats)
            self._ans_hdr_canvas = c
            self._ans_stats_id = c.create_text(
                W - 10, H // 2, anchor="e",
                text=self._sys_stats_text(),
                fill=TEXT_DIM, font=("Consolas", 7))
            # Sağ köşe bracket
            c.create_line(W - 8,  6, W - 8,  H - 6, fill=CYAN_DK, width=1)
            c.create_line(W - 24, 6, W - 8,  6,     fill=CYAN_DK, width=1)
            # Alt çizgi
            c.create_line(0, H - 1, W, H - 1, fill=CYAN_DK, width=1)

        ans_hdr.bind("<Configure>", _draw_ans_hdr)
        ans_hdr.after(120, _draw_ans_hdr)

        # Metin alanı kapsayıcısı
        ans_frame = tk.Frame(left, bg=CARD)
        ans_frame.pack(fill="both", expand=True)

        # Sol ince accent çizgisi
        tk.Frame(ans_frame, bg=CYAN, width=3).pack(side="left", fill="y")

        text_wrap = tk.Frame(ans_frame, bg=CARD)
        text_wrap.pack(side="left", fill="both", expand=True)

        self._answer_text = tk.Text(
            text_wrap,
            bg=CARD, fg=CYAN,
            font=FONT_LARGE,
            wrap="word",
            relief="flat", bd=0,
            padx=26, pady=22,
            state="disabled",
            cursor="arrow",
            selectbackground=CYAN_DK,
            selectforeground=CYAN,
            spacing1=2, spacing3=8,
        )
        sb = tk.Scrollbar(text_wrap, command=self._answer_text.yview,
                          bg=CARD2, troughcolor=BG,
                          relief="flat", width=5, highlightthickness=0)
        self._answer_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=8)
        self._answer_text.pack(fill="both", expand=True)

        # Renk etiketleri
        self._answer_text.tag_config("dim",    foreground=TEXT_DIM)
        self._answer_text.tag_config("normal", foreground=CYAN)
        self._answer_text.tag_config("boot",   foreground=CYAN_MID)
        self._answer_text.tag_config("ok",     foreground=GREEN)
        self._answer_text.tag_config("warn",   foreground=YELLOW)
        self._answer_text.tag_config("error",  foreground=RED)

    # ── Sağ yan panel ─────────────────────────────────────────────────────────

    def _build_side_panel(self, parent):
        side = tk.Frame(parent, bg=BG, width=SIDE_W)
        side.pack(side="right", fill="y")
        side.pack_propagate(False)

        # Panel başlığı
        side_hdr = tk.Canvas(side, bg=CARD2, height=34,
                             highlightthickness=0, bd=0)
        side_hdr.pack(fill="x")

        def _draw_side_hdr(event=None, c=side_hdr):
            c.delete("all")
            W = c.winfo_width()
            H = 34
            if W < 10:
                return
            c.create_line(0, 0, 0, H, fill=CYAN_MID, width=3)
            c.create_text(14, H // 2, anchor="w",
                          text="◈  SİSTEM PANELİ",
                          fill=CYAN_DIM, font=("Consolas", 8, "bold"))
            c.create_line(0, H - 1, W, H - 1, fill=CYAN_DK, width=1)

        side_hdr.bind("<Configure>", _draw_side_hdr)
        side_hdr.after(120, _draw_side_hdr)

        # Kartlar
        cards = [
            ("DURUM",         "status",  CYAN,   CARD,  CYAN),
            ("SON KOMUT",     "command", TEXT,   CARD,  CYAN_DIM),
            ("SON EYLEM",     "action",  YELLOW, CARD2, YELLOW),
            ("AKTİF PENCERE", "window",  TEXT,   CARD,  ACCENT),
            ("BEKLEYEN",      "pending", GREEN,  CARD2, GREEN),
        ]
        cards_frame = tk.Frame(side, bg=BG)
        cards_frame.pack(fill="x", pady=(6, 0))

        for label, key, fg, bg_card, accent in cards:
            self._card_vars[key] = self._make_card(
                cards_frame, label, fg, bg_card, accent)

        # Alt genişletici
        tk.Frame(side, bg=BG).pack(fill="both", expand=True)

        # Ses kayıt butonu
        def _on_voice_enroll():
            if self._process_cb:
                import threading
                threading.Thread(
                    target=self._process_cb,
                    args=("sesimi tani",),
                    kwargs={"manual": True},
                    daemon=True,
                ).start()

        voice_btn_frame = tk.Frame(side, bg=BG)
        voice_btn_frame.pack(fill="x", padx=8, pady=(4, 6))
        voice_btn = tk.Button(
            voice_btn_frame,
            text="🎤  Sesimi Kaydet",
            bg=CARD2, fg=CYAN,
            font=("Segoe UI", 9, "bold"),
            relief="flat", cursor="hand2",
            activebackground=CARD, activeforeground=CYAN,
            command=_on_voice_enroll,
        )
        voice_btn.pack(fill="x", ipady=5)

        # Alt sistem kimlik şeridi
        foot = tk.Frame(side, bg=CARD2)
        foot.pack(fill="x", side="bottom")
        tk.Frame(foot, bg=CYAN_DK, height=1).pack(fill="x")
        tk.Label(foot,
                 text="■  KLARGUS v2.0  //  SovereignBrain  //  Klargus OS",
                 bg=CARD2, fg=TEXT_DIM,
                 font=("Consolas", 7)).pack(anchor="w", padx=10, pady=5)

    def _make_card(self, parent, label: str, fg: str,
                   bg_card: str, accent: str) -> tk.StringVar:
        """Bilgi kartı oluştur. Döner: StringVar."""
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="x", padx=8, pady=(0, 5))

        inner = tk.Frame(outer, bg=bg_card)
        inner.pack(fill="x")

        # Sol dikey accent çizgisi
        tk.Frame(inner, bg=accent, width=3).pack(side="left", fill="y")

        content = tk.Frame(inner, bg=bg_card)
        content.pack(side="left", fill="both", expand=True)

        # Başlık satırı — köşe bracket karakterleri + etiket
        lbl_row = tk.Frame(content, bg=bg_card)
        lbl_row.pack(fill="x", padx=(8, 6), pady=(5, 0))
        tk.Label(lbl_row, text=f"┌─  {label}",
                 bg=bg_card, fg=accent,
                 font=("Consolas", 7, "bold")).pack(side="left")

        # Değer
        var = tk.StringVar(value="—")
        tk.Label(content,
                 textvariable=var,
                 bg=bg_card, fg=fg,
                 font=FONT_CARD,
                 wraplength=330, justify="left",
                 anchor="w").pack(fill="x", padx=(8, 6), pady=(2, 6))

        # Alt ince border
        tk.Frame(outer, bg=BORDER, height=1).pack(fill="x")
        return var

    # ── Log bandı ─────────────────────────────────────────────────────────────

    def _build_log(self):
        log_outer = tk.Frame(self, bg=BG)
        log_outer.pack(side="bottom", fill="x")

        # Sol ince accent
        tk.Frame(log_outer, bg=CYAN_DIM, width=3).pack(side="left", fill="y")

        log_inner = tk.Frame(log_outer, bg=BG)
        log_inner.pack(side="left", fill="both", expand=True)

        # Küçük başlık etiketi
        tk.Label(log_inner, text="  SİSTEM LOGU  ▼",
                 bg=BG, fg=TEXT_DIM,
                 font=("Consolas", 6, "bold")).pack(anchor="w", padx=6, pady=(3, 0))

        self._log_text = tk.Text(
            log_inner,
            bg=BG, fg=TEXT_DIM,
            font=("Consolas", 8),
            wrap="word",
            relief="flat", bd=0,
            padx=10, pady=2,
            height=4,
            state="disabled",
            cursor="arrow",
        )
        self._log_text.pack(fill="x", expand=False)

    # ── Komut girişi ──────────────────────────────────────────────────────────

    def _build_entry(self):
        # side="bottom" zorunlu — aksi hâlde body expand=True tüm alanı alır
        # Üst ince border — girişin nerede başladığını belli eder
        tk.Frame(self, bg=CYAN, height=2).pack(side="bottom", fill="x")

        entry_frame = tk.Frame(self, bg="#040e1a", height=EBAR_H)
        entry_frame.pack(side="bottom", fill="x")
        entry_frame.pack_propagate(False)

        # Sol kalın accent — girişi belirginleştirir (nabız animasyonu: _pulse_entry)
        self._entry_accent = tk.Frame(entry_frame, bg=CYAN, width=4)
        self._entry_accent.pack(side="left", fill="y")

        # Prompt sembolü (nabız animasyonu: _pulse_entry)
        self._entry_prompt = tk.Label(entry_frame, text="❯",
                 bg="#040e1a", fg=CYAN,
                 font=("Consolas", 16, "bold"))
        self._entry_prompt.pack(side="left", padx=(10, 4))

        self.command_entry = tk.Entry(
            entry_frame,
            bg="#040e1a", fg=CYAN,
            insertbackground=CYAN,
            font=("Consolas", 13),
            relief="flat", bd=0,
            highlightthickness=1,
            highlightbackground=CYAN_DK,
            highlightcolor=CYAN,
        )
        self.command_entry.pack(side="left", fill="both", expand=True, pady=8, padx=(0, 6))
        self.command_entry.bind("<Return>",   self._on_enter)
        self.command_entry.bind("<FocusIn>",  self._on_entry_focus_in)
        self.command_entry.bind("<FocusOut>", self._on_entry_focus_out)

        # Placeholder metni göster
        self._placeholder = "Buraya komut yazın ve Enter'a basın…"
        self._ph_active = True
        self.command_entry.insert(0, self._placeholder)
        self.command_entry.config(fg=CYAN_DIM)

        self._send_btn = tk.Button(
            entry_frame,
            text="  GÖNDER  ⏎",
            bg=ACCENT_DK, fg=CYAN,
            activebackground=ACCENT, activeforeground=BG,
            font=("Consolas", 10, "bold"),
            relief="flat", bd=0,
            padx=16, cursor="hand2",
            command=self._on_send,
            highlightthickness=0,
        )
        self._send_btn.pack(side="right", padx=(0, 12), pady=8)

    def _on_entry_focus_in(self, event=None):
        if self._ph_active:
            self.command_entry.delete(0, "end")
            self.command_entry.config(fg=CYAN)
            self._ph_active = False
        self._btn_highlight(True)

    def _on_entry_focus_out(self, event=None):
        if not self.command_entry.get().strip():
            self.command_entry.insert(0, self._placeholder)
            self.command_entry.config(fg=CYAN_DIM)
            self._ph_active = True
        self._btn_highlight(False)

    # ══════════════════════════════════════════════════════════════════════════
    # ANİMASYONLAR
    # ══════════════════════════════════════════════════════════════════════════

    def _pulse_dot(self):
        """Header canvas üzerindeki SYS dot rengini yakıp söndür."""
        try:
            self._pulse_state = not self._pulse_state
            color = CYAN if self._pulse_state else CYAN_DK
            self._hdr.itemconfig("dot", fill=color)
        except Exception:
            pass
        self.after(900, self._pulse_dot)

    def _tick_clock(self):
        """Header canvas saatini her saniye güncelle."""
        try:
            if self._hdr_clock_id is not None:
                self._hdr.itemconfig(self._hdr_clock_id, text=self._now_str())
        except Exception:
            pass
        self.after(1000, self._tick_clock)

    def _sys_stats_text(self) -> str:
        if self._cpu_pct is None:
            return "CPU:--%  RAM:--%"
        return f"CPU:{self._cpu_pct:>3.0f}%  RAM:{self._mem_pct:>3.0f}%"

    def _poll_system_stats(self):
        """CPU/RAM kullanımını periyodik oku ve yanıt başlığını güncelle.
        psutil.cpu_percent(interval=None) blocklamaz (son çağrıya göre anlık
        değer döner) — UI thread'i asla bekletmez."""
        try:
            import psutil
            self._cpu_pct = psutil.cpu_percent(interval=None)
            self._mem_pct = psutil.virtual_memory().percent
            if self._ans_hdr_canvas is not None and self._ans_stats_id is not None:
                self._ans_hdr_canvas.itemconfig(self._ans_stats_id,
                                                 text=self._sys_stats_text())
        except Exception:
            pass
        self.after(2000, self._poll_system_stats)

    def _scan_sweep(self):
        """Header ortasındaki ışık çizgisini ileri-geri kaydır — statik
        metnin yerini alan gerçek hareket (üçgen dalga, ~3.3sn periyot)."""
        try:
            x0, x1 = self._hdr_scan_span
            span = max(1, x1 - x0)
            t = (time.monotonic() * 0.3) % 2.0
            frac = t if t <= 1.0 else 2.0 - t   # 0→1→0 üçgen dalga
            x = x0 + span * frac
            seg = 26
            if self._hdr_scan_id is not None:
                self._hdr.coords(self._hdr_scan_id,
                                  max(x0, x - seg), self._hdr_scan_y,
                                  min(x1, x + seg), self._hdr_scan_y)
        except Exception:
            pass
        self.after(40, self._scan_sweep)

    def _pulse_entry(self):
        """Komut girişi accent çizgisi + prompt sembolünü nabız gibi attır.
        Odaklanınca _btn_highlight devreye girdiği için burada dokunmuyoruz."""
        try:
            if self.command_entry.focus_get() is not self.command_entry:
                self._entry_pulse_state = not self._entry_pulse_state
                c1 = CYAN if self._entry_pulse_state else ACCENT
                self._entry_accent.configure(bg=c1)
                self._entry_prompt.configure(fg=c1)
        except Exception:
            pass
        self.after(1100, self._pulse_entry)

    # ══════════════════════════════════════════════════════════════════════════
    # BOOT ANİMASYONU
    # ══════════════════════════════════════════════════════════════════════════

    def _boot_sequence(self):
        # NOT: KLARGUS/SovereignBrain marka metni buradan kaldırıldı — header
        # zaten aynı bilgiyi gösteriyor, iki yerde tekrar "gereksiz yazı"ydı.
        # Kontrol paneli çubukları (typewriter reveal) kalıyor — bu gerçek
        # animasyon, tutulması istenen kısım budur.
        lines = [
            ("  ▸ [██████░░░░]  Bellek bankası...........  60%", "dim"),
            ("  ▸ [████████░░]  Araç kataloğu.............  82%", "dim"),
            ("  ▸ [█████████░]  Ses sistemi kontrol.......  94%", "dim"),
            ("  ▸ [██████████]  SovereignBrain yükleniyor. 100%", "ok"),
            ("", "dim"),
            ("  ✓  Tüm sistemler çevrim içi — bellek, araç kataloğu, ses hazır.", "ok"),
        ]

        def _write(idx=0):
            if idx >= len(lines):
                self._boot_done = True
                self.after(1800, self._enter_idle_state)
                return
            text, tag = lines[idx]
            try:
                self._answer_text.configure(state="normal")
                if idx > 0:
                    self._answer_text.insert("end", "\n")
                self._answer_text.insert("end", text, tag)
                self._answer_text.see("end")
                self._answer_text.configure(state="disabled")
            except Exception:
                pass
            delay = 45 if text else 15
            self.after(delay, lambda: _write(idx + 1))

        _write()

    def _enter_idle_state(self):
        """Boot günlüğü ekranda kalıcı bir yazı yığını olarak durmasın —
        kısa süre gösterildikten sonra minimal, nabız gibi atan bekleme
        durumuna geç. Gerçek bir komut/cevap gelince set_answer() zaten
        bu içeriği temizleyip yerine geçer; bu döngü onunla çakışmaz."""
        try:
            self._answer_text.configure(state="normal")
            self._answer_text.delete("1.0", "end")
            self._answer_text.insert("end", "●", "idle_dot")
            self._answer_text.insert("end", "  Hazır — komutunuzu bekliyorum, Patron.", "normal")
            self._answer_text.configure(state="disabled")
        except Exception:
            pass
        self._idle_dot_on = True
        self._pulse_idle_dot()

    def _pulse_idle_dot(self):
        try:
            self._idle_dot_on = not self._idle_dot_on
            self._answer_text.tag_configure(
                "idle_dot", foreground=GREEN if self._idle_dot_on else GREEN_DK)
        except Exception:
            pass
        self.after(900, self._pulse_idle_dot)

    # ══════════════════════════════════════════════════════════════════════════
    # OLAYLAR
    # ══════════════════════════════════════════════════════════════════════════

    def _on_enter(self, event=None):
        self._on_send()

    def _on_send(self):
        text = self.command_entry.get().strip()
        # Placeholder metin gönderilmesin
        if not text or text == self._placeholder:
            return
        self.command_entry.delete(0, "end")
        # Gönderildi → placeholder'ı geri getir
        self.command_entry.insert(0, self._placeholder)
        self.command_entry.config(fg=CYAN_DIM)
        self._ph_active = True
        if self._process_cb:
            threading.Thread(
                target=self._process_cb,
                args=(text,),
                kwargs={"manual": True},
                daemon=True,
            ).start()

    def _btn_highlight(self, focused: bool):
        try:
            self._send_btn.configure(
                bg=ACCENT if focused else ACCENT_DK,
                fg=BG      if focused else CYAN,
            )
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    # DIŞ API
    # ══════════════════════════════════════════════════════════════════════════

    def set_status(self, value: str):
        try:
            v = str(value or "Hazır")[:60]
            self._status_var.set(v)
            self._card_vars["status"].set(v)
            if self._hdr_status_id is not None:
                self._hdr.itemconfig(self._hdr_status_id, text=v)
        except Exception:
            pass

    def set_command(self, value: str):
        try:
            v = str(value or "—")[:100]
            self._cmd_var.set(v)
            self._card_vars["command"].set(v)
        except Exception:
            pass

    def set_answer(self, value: str):
        try:
            self._boot_done = True
            self._answer_text.configure(state="normal")
            self._answer_text.delete("1.0", "end")
            if value:
                self._answer_text.insert("end", str(value), "normal")
                self._answer_text.see("end")
            self._answer_text.configure(state="disabled")
        except Exception:
            pass

    def set_action(self, value: str):
        try:
            self._card_vars["action"].set(str(value or "—")[:80])
        except Exception:
            pass

    def set_window(self, value: str):
        try:
            self._card_vars["window"].set(str(value or "—")[:80])
        except Exception:
            pass

    def set_pending(self, value: str):
        try:
            self._card_vars["pending"].set(str(value or "—")[:80])
        except Exception:
            pass

    def log(self, message: str):
        try:
            self._log_text.configure(state="normal")
            self._log_text.insert("end", str(message) + "\n")
            lines = int(self._log_text.index("end").split(".")[0]) - 1
            if lines > 400:
                self._log_text.delete("1.0", f"{lines - 400}.0")
            self._log_text.see("end")
            self._log_text.configure(state="disabled")
        except Exception:
            pass
