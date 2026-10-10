"""Tkinter arayüzü. Ağ/DB işleri arka plan iş parçacığında, arayüz güncellemeleri
yalnızca ana iş parçacığında (kuyruk üzerinden) yapılır.

Tasarım ilkeleri:
    • 4px grid spacing ölçeği (SP)
    • 7 seviyeli tipografi ölçeği (FS)
    • Kart tabanlı hiyerarşi (panel + border + padding)
    • Buton hiyerarşisi: Accent (birincil) / Success / TButton (ikincil) / Ghost (üçüncül)
    • Semantik renkler: accent / success / warning / danger + soft varyantlar
"""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, simpledialog, ttk

import config
import db
import notify
import service
import textutil
from config import TEMALAR, Ayarlar

try:
    from PIL import Image, ImageTk
    _LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
except ImportError:                      # Pillow yoksa görseller devre dışı
    Image = ImageTk = _LANCZOS = None

log = logging.getLogger(__name__)

# =====================================================================
# TASARIM SİSTEMİ
# =====================================================================
FONT = "Segoe UI"
MONO = "Cascadia Mono" if sys.platform.startswith("win") else "Consolas"

# Tipografi ölçeği
FS = {
    "h1":    (FONT, 15, "bold"),
    "h2":    (FONT, 12, "bold"),
    "h3":    (FONT, 10, "bold"),
    "body":  (FONT, 10),
    "small": (FONT, 9),
    "tiny":  (FONT, 8),
    "mono":  (MONO, 9),
}

# Spacing ölçeği (4px grid)
SP = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}

THUMB = 108


def dosya_ac(yol) -> None:
    yol = str(yol)
    if sys.platform.startswith("win"):
        os.startfile(yol)                # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", yol])
    else:
        subprocess.Popen(["xdg-open", yol])


class App(tk.Tk):
    def __init__(self, ayarlar: Ayarlar, log_kuyrugu: "queue.Queue[str]"):
        super().__init__()
        self.ayarlar = ayarlar
        self.R = TEMALAR[ayarlar.tema]
        self._log_kuyrugu = log_kuyrugu
        self._kuyruk: "queue.Queue" = queue.Queue()
        self._durdur = threading.Event()

        self.title(f"{config.APP_NAME}  ·  v{config.VERSION}")
        self.minsize(1100, 680)
        self._pencere_yerlestir(ayarlar.pencere)

        self.suruyor = False
        self.onyukleniyor = False
        self.secili: str | None = None
        self._gosterilen: str | None = None
        self.resim_yolu = None
        self.tkimg = None
        self._temali: list = []
        self._ids = {"yukle": None, "periyodik": None, "pump": None, "durum": None}
        self._sayi_toplam = 0
        self._sayi_yeni = 0
        self._hover_id: str | None = None

        self._stil()
        self._ust_bar()
        self._arac_cubugu()
        self._durum_cubugu()          # alt çubuklar içerikten ÖNCE paketlenir
        self._icerik()
        self._sag_tik_menu()
        self._kisayollar()
        self._tema_uygula()

        self.protocol("WM_DELETE_WINDOW", self._kapat)
        self._pump()
        self.yukle()
        self.after(500, self.tara_baslat)
        self._ids["periyodik"] = self.after(self._aralik_ms(), self.periyodik)

    # ================= altyapı =================
    def _pencere_yerlestir(self, geometri: str):
        """Kayıtlı boyutu kullan ama ekran dışına taşarsa ortala."""
        try:
            w_s, h_s = geometri.split("x")
            w, h = int(w_s), int(h_s)
        except (ValueError, AttributeError):
            w, h = 1520, 880
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(w, sw - 40)
        h = min(h, sh - 80)
        x = (sw - w) // 2
        y = max(0, (sh - h) // 3)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def report_callback_exception(self, exc, val, tb):
        log.error("Beklenmeyen arayüz hatası", exc_info=(exc, val, tb))
        self._durum_yaz("Beklenmeyen bir hata oluştu — ayrıntı takip.log dosyasında.", 8000)

    def _arka_plan(self, is_fn, basari=None, hata=None):
        """is_fn'i iş parçacığında çalıştırır; sonucu ana iş parçacığında basari/hata'ya iletir."""
        def calis():
            try:
                sonuc = is_fn()
            except Exception as e:
                self._kuyruk.put(lambda e=e: (hata(e) if hata else log.error("Arka plan hatası: %s", e)))
            else:
                if basari:
                    self._kuyruk.put(lambda: basari(sonuc))
        threading.Thread(target=calis, daemon=True).start()

    def _pump(self):
        try:
            while True:
                fn = self._kuyruk.get_nowait()
                try:
                    fn()
                except Exception:
                    log.exception("Kuyruk işlemi başarısız")
        except queue.Empty:
            pass
        satirlar = []
        try:
            while True:
                satirlar.append(self._log_kuyrugu.get_nowait())
        except queue.Empty:
            pass
        if satirlar:
            self.logbox.config(state="normal")
            self.logbox.insert("end", "\n".join(satirlar) + "\n")
            if int(self.logbox.index("end-1c").split(".")[0]) > 300:
                self.logbox.delete("1.0", "100.0")
            self.logbox.see("end")
            self.logbox.config(state="disabled")
        self._ids["pump"] = self.after(100, self._pump)

    def _durum_yaz(self, metin: str, ms: int = 4000):
        self.durum.set(metin)
        if self._ids["durum"]:
            self.after_cancel(self._ids["durum"])
        if ms:
            self._ids["durum"] = self.after(ms, lambda: self.durum.set("Hazır"))

    # ================= tema / stil =================
    def _kayit(self, w, **roller):
        self._temali.append((w, roller))
        return w

    def _frm(self, parent, bg="panel", **kw):
        return self._kayit(tk.Frame(parent, **kw), bg=bg)

    def _lbl(self, parent, text="", bg="panel", fg="text", **kw):
        return self._kayit(tk.Label(parent, text=text, **kw), bg=bg, fg=fg)

    def _kart(self, parent, **kw):
        """Kenarlıklı, panel arka planlı kart konteyneri."""
        f = tk.Frame(parent, bg=self.R["panel"],
                     highlightthickness=1,
                     highlightbackground=self.R["border"],
                     highlightcolor=self.R["border"],
                     **kw)
        self._kayit(f, bg="panel", highlightbackground="border", highlightcolor="border")
        return f

    def _stil(self):
        R = self.R
        self.configure(bg=R["bg"])
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass

        # Genel
        s.configure(".", background=R["bg"], foreground=R["text"],
                    fieldbackground=R["panel"], font=FS["body"])
        s.configure("TFrame", background=R["bg"])
        s.configure("TLabel", background=R["bg"], foreground=R["text"], font=FS["body"])
        s.configure("TSeparator", background=R["divider"])

        # --- Butonlar
        s.configure("TButton",
                    background=R["panel2"], foreground=R["text"],
                    borderwidth=1, focusthickness=0, padding=(14, 8),
                    font=FS["small"], relief="flat")
        s.map("TButton",
              background=[("active", R["panel3"]), ("pressed", R["panel3"]),
                          ("disabled", R["panel"])],
              foreground=[("active", R["accent"]), ("disabled", R["text_muted"])],
              bordercolor=[("active", R["accent"]), ("!active", R["border"])])

        s.configure("Accent.TButton",
                    background=R["accent"], foreground="#ffffff",
                    font=(FONT, 10, "bold"), padding=(16, 9), borderwidth=0)
        s.map("Accent.TButton",
              background=[("active", R["accent2"]), ("pressed", R["accent2"]),
                          ("disabled", R["border"])],
              foreground=[("disabled", R["text_muted"])])

        s.configure("Success.TButton",
                    background=R["success"], foreground="#ffffff",
                    font=(FONT, 10, "bold"), padding=(16, 9), borderwidth=0)
        s.map("Success.TButton",
              background=[("active", "#047857"), ("pressed", "#047857"),
                          ("disabled", R["border"])],
              foreground=[("disabled", R["text_muted"])])

        s.configure("Ghost.TButton",
                    background=R["panel"], foreground=R["text_dim"],
                    borderwidth=0, padding=(10, 8), font=FS["small"], relief="flat")
        s.map("Ghost.TButton",
              background=[("active", R["panel2"]), ("pressed", R["panel3"])],
              foreground=[("active", R["accent"])])

        # --- Checkbutton
        s.configure("TCheckbutton",
                    background=R["panel"], foreground=R["text"], font=FS["small"],
                    focusthickness=0, padding=(4, 4))
        s.map("TCheckbutton",
              background=[("active", R["panel"])],
              foreground=[("active", R["accent"]), ("selected", R["accent"])])

        s.configure("Chip.TCheckbutton",
                    background=R["panel"], foreground=R["text_dim"],
                    font=FS["small"], padding=(10, 6), focusthickness=0)
        s.map("Chip.TCheckbutton",
              background=[("active", R["accent_soft"]), ("selected", R["accent_soft"])],
              foreground=[("active", R["accent"]), ("selected", R["accent"])])

        # --- Girdi alanları
        s.configure("TEntry",
                    fieldbackground=R["panel2"], foreground=R["text"],
                    bordercolor=R["border"], insertcolor=R["text"],
                    padding=6, relief="flat")
        s.map("TEntry",
              bordercolor=[("focus", R["accent"])],
              lightcolor=[("focus", R["accent"])],
              darkcolor=[("focus", R["accent"])])

        s.configure("TCombobox",
                    fieldbackground=R["panel2"], background=R["panel2"],
                    foreground=R["text"], arrowcolor=R["text_dim"],
                    bordercolor=R["border"], padding=4)
        s.map("TCombobox",
              fieldbackground=[("readonly", R["panel2"])],
              foreground=[("readonly", R["text"])],
              bordercolor=[("focus", R["accent"])],
              arrowcolor=[("active", R["accent"])])

        # --- Treeview
        s.configure("Treeview",
                    background=R["panel"], fieldbackground=R["panel"],
                    foreground=R["text"], rowheight=32, borderwidth=0,
                    font=FS["body"])
        s.configure("Treeview.Heading",
                    background=R["panel2"], foreground=R["text_dim"],
                    font=(FONT, 9, "bold"), borderwidth=0, padding=(10, 8),
                    relief="flat")
        s.map("Treeview.Heading",
              background=[("active", R["panel3"])],
              foreground=[("active", R["accent"])])
        s.map("Treeview",
              background=[("selected", R["row_sel"])],
              foreground=[("selected", R["text"])])

        # --- Notebook
        s.configure("TNotebook", background=R["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        s.configure("TNotebook.Tab",
                    background=R["panel2"], foreground=R["text_dim"],
                    padding=(16, 9), font=FS["small"], borderwidth=0)
        s.map("TNotebook.Tab",
              background=[("selected", R["panel"]), ("active", R["panel3"])],
              foreground=[("selected", R["accent"]), ("active", R["text"])],
              expand=[("selected", (0, 0, 0, 0))])

        # --- Scrollbar
        s.configure("Vertical.TScrollbar",
                    background=R["panel2"], troughcolor=R["bg"],
                    bordercolor=R["bg"], arrowcolor=R["text_dim"],
                    relief="flat", width=12)
        s.map("Vertical.TScrollbar",
              background=[("active", R["panel3"])])

        # --- PanedWindow
        s.configure("Sash", sashthickness=6, gripcount=0, background=R["divider"])

        # Combobox dropdown
        self.option_add("*TCombobox*Listbox.background", R["panel"])
        self.option_add("*TCombobox*Listbox.foreground", R["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", R["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.option_add("*TCombobox*Listbox.font", FS["small"])

    def _tema_uygula(self):
        R = self.R
        self.configure(bg=R["bg"])
        for w, roller in self._temali:
            try:
                w.configure(**{opt: R[anahtar] for opt, anahtar in roller.items()})
            except tk.TclError:
                pass

        # Treeview tag'leri — Tk'de İLK tanımlanan etiket öncelik kazanır
        t = self.tree
        t.tag_configure("favori", foreground=R["warning"])
        t.tag_configure("yakinda", foreground=R["yakinda_fg"])
        t.tag_configure("yeni", font=(FONT, 10, "bold"), foreground=R["yeni_fg"])
        t.tag_configure("pasif", foreground=R["pasif_fg"])
        t.tag_configure("kpss_ok", foreground=R["success"])
        t.tag_configure("hover", background=R["row_hover"])
        t.tag_configure("odd", background=R["row_alt"])

        # Detay metin kutusu tag'leri
        b = self.t_bilgi
        b.tag_configure("h", font=(FONT, 13, "bold"), foreground=R["text"],
                        spacing1=4, spacing3=8)
        b.tag_configure("sep", foreground=R["border"])
        b.tag_configure("ok", foreground=R["success"], font=(FONT, 10, "bold"))
        b.tag_configure("warn", foreground=R["warning"], font=(FONT, 10, "bold"))
        b.tag_configure("muted", foreground=R["text_dim"])
        for tb in (self.t_bilgi, self.t_ham):
            tb.configure(selectbackground=R["accent"], selectforeground="#ffffff",
                         insertbackground=R["text"])

        self.tema_btn.configure(text="☀" if self.ayarlar.tema == "dark" else "🌙")

    def _tema_degistir(self):
        self.ayarlar.tema = "light" if self.ayarlar.tema == "dark" else "dark"
        self.R = TEMALAR[self.ayarlar.tema]
        self._stil()
        self._tema_uygula()
        self.ayarlar.kaydet()

    # ================= üst bar =================
    def _ust_bar(self):
        bar = self._frm(self, height=68)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        # Sol: logo + başlık bloğu
        sol = self._frm(bar)
        sol.pack(side="left", padx=SP["xl"])

        logo_kutu = self._frm(sol, width=44, height=44, bg="panel2")
        self._kayit(logo_kutu, bg="panel2")
        logo_kutu.pack(side="left", padx=(0, SP["md"]), pady=SP["md"])
        logo_kutu.pack_propagate(False)
        self._lbl(logo_kutu, "🇹🇷", bg="panel2", font=(FONT, 20)).pack(expand=True)

        blok = self._frm(sol)
        blok.pack(side="left", pady=SP["md"])
        self._lbl(blok, "Kariyer Kapısı Takip", font=FS["h1"]).pack(anchor="w")
        self._lbl(blok, "Kamu İşe Alım İlanları · Otomatik İzleme",
                  fg="text_dim", font=FS["tiny"]).pack(anchor="w", pady=(1, 0))

        # Sağ: aksiyon grubu
        sag = self._frm(bar)
        sag.pack(side="right", padx=SP["xl"])

        self.tema_btn = ttk.Button(sag, text="☀", width=3,
                                   style="Ghost.TButton",
                                   command=self._tema_degistir)
        self.tema_btn.pack(side="right", padx=(SP["xs"], 0))

        ttk.Button(sag, text="⚙  RSS", style="Ghost.TButton",
                   command=self.rss_gir, width=8).pack(side="right", padx=SP["xs"])

        self.b_tara = ttk.Button(sag, text="🔄  Şimdi Tara",
                                 style="Accent.TButton", command=self.tara_baslat)
        self.b_tara.pack(side="right", padx=(0, SP["sm"]))

        # İnce ayırıcı
        self._frm(self, height=1, bg="border").pack(fill="x", side="top")

    # ================= araç çubuğu =================
    def _arac_cubugu(self):
        dis = self._frm(self, bg="bg")
        dis.pack(fill="x", padx=SP["md"], pady=(SP["md"], 0))

        kart = self._kart(dis)
        kart.pack(fill="x")

        satir1 = self._frm(kart)
        satir1.pack(fill="x", padx=SP["md"], pady=(SP["md"], SP["sm"]))
        satir2 = self._frm(kart)
        satir2.pack(fill="x", padx=SP["md"], pady=(SP["sm"], SP["md"]))

        # --- Arama
        arama = self._frm(satir1)
        arama.pack(side="left", fill="x", expand=True, padx=(0, SP["lg"]))
        self._lbl(arama, "🔎", fg="text_dim", font=(FONT, 11)).pack(side="left", padx=(0, SP["xs"]))
        self.q = tk.StringVar()
        self.q.trace_add("write", lambda *_: self._yukle_planla())
        self.e_ara = ttk.Entry(arama, textvariable=self.q, font=FS["body"])
        self.e_ara.pack(side="left", fill="x", expand=True)

        # --- Kategori
        self._lbl(satir1, "Kategori", fg="text_dim", font=FS["tiny"]).pack(side="left", padx=(0, SP["xs"]))
        self.kat = tk.StringVar(value="Tümü")
        self.cb = ttk.Combobox(satir1, textvariable=self.kat, state="readonly",
                               width=22, values=["Tümü"], font=FS["small"])
        self.cb.pack(side="left", padx=(0, SP["lg"]))
        self.cb.bind("<<ComboboxSelected>>", lambda e: self.yukle())

        # --- KPSS
        self._lbl(satir1, "KPSS", fg="text_dim", font=FS["tiny"]).pack(side="left", padx=(0, SP["xs"]))
        self.kpss_tur = tk.StringVar(value="Tümü")
        cbk = ttk.Combobox(satir1, textvariable=self.kpss_tur, state="readonly",
                           width=8, values=["Tümü", *config.KPSS_TURLERI], font=FS["small"])
        cbk.pack(side="left", padx=(0, SP["lg"]))
        cbk.bind("<<ComboboxSelected>>", lambda e: self.yukle())

        # --- Sıralama
        self._lbl(satir1, "Sırala", fg="text_dim", font=FS["tiny"]).pack(side="left", padx=(0, SP["xs"]))
        self.sirala = tk.StringVar(value="Yayın tarihi")
        cs = ttk.Combobox(satir1, textvariable=self.sirala, state="readonly",
                          width=13, values=["Yayın tarihi", "Son başvuru"], font=FS["small"])
        cs.pack(side="left")
        cs.bind("<<ComboboxSelected>>", lambda e: self.yukle())

        # --- Filtre chip'leri
        self.v_yeni = tk.BooleanVar()
        self.v_aktif = tk.BooleanVar(value=True)
        self.v_gizle = tk.BooleanVar(value=True)
        self.v_kpss = tk.BooleanVar()
        self.v_fav = tk.BooleanVar()

        chip_f = self._frm(satir2)
        chip_f.pack(side="left")
        for metin, var in (("✨ Yeni", self.v_yeni),
                           ("✅ Aktif", self.v_aktif),
                           ("🙈 Alakasızları gizle", self.v_gizle),
                           ("📚 Sadece KPSS", self.v_kpss),
                           ("⭐ Favoriler", self.v_fav)):
            ttk.Checkbutton(chip_f, text=metin, variable=var,
                            style="Chip.TCheckbutton",
                            command=self.yukle).pack(side="left", padx=(0, SP["xs"]))

        ttk.Button(satir2, text="📥  Excel", style="Ghost.TButton",
                   command=self.excel).pack(side="right", padx=(SP["sm"], 0))
        ttk.Button(satir2, text="✓  Tümünü Okundu", style="Ghost.TButton",
                   command=self.okundu).pack(side="right")

    # ================= durum çubuğu =================
    def _durum_cubugu(self):
        # Log kartı
        logf = self._frm(self, bg="bg")
        logf.pack(fill="x", side="bottom", padx=SP["md"], pady=(0, SP["sm"]))
        log_kart = tk.Frame(logf, bg=self.R["editor_bg"],
                            highlightthickness=1,
                            highlightbackground=self.R["border"])
        self._kayit(log_kart, bg="editor_bg", highlightbackground="border")
        log_kart.pack(fill="x")

        self.logbox = tk.Text(log_kart, height=3, state="disabled",
                              font=FS["mono"], relief="flat", padx=10, pady=6,
                              borderwidth=0, wrap="none")
        self._kayit(self.logbox, bg="editor_bg", fg="editor_fg")
        self.logbox.pack(fill="x")

        # Alt durum barı
        bar = self._frm(self, height=32)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        sol = self._frm(bar)
        sol.pack(side="left", padx=SP["lg"])

        self.durum_nokta = self._lbl(sol, "●", fg="success", font=(FONT, 9))
        self.durum_nokta.pack(side="left", padx=(0, SP["xs"]))

        self.durum = tk.StringVar(value="Hazır")
        self._lbl(sol, font=FS["small"], anchor="w",
                  textvariable=self.durum).pack(side="left")

        self.rss_lbl = self._lbl(bar, fg="text_dim", font=FS["tiny"])
        self.rss_lbl.pack(side="right", padx=SP["lg"])
        self._rss_lbl_guncelle()

    # ================= içerik =================
    def _icerik(self):
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=SP["md"], pady=(SP["md"], SP["sm"]))

        # ---- sol: liste
        sol = self._frm(pw, bg="bg")
        pw.add(sol, weight=4)

        ust = self._frm(sol, bg="bg")
        ust.pack(fill="x", pady=(0, SP["sm"]))
        self._lbl(ust, "İLANLAR", bg="bg", fg="text_dim",
                  font=(FONT, 9, "bold")).pack(side="left")
        self.sayac = self._lbl(ust, bg="bg", fg="accent", font=(FONT, 9, "bold"))
        self.sayac.pack(side="left", padx=SP["sm"])

        # Tablo kartı
        wrap = tk.Frame(sol, bg=self.R["panel"],
                        highlightthickness=1,
                        highlightbackground=self.R["border"])
        self._kayit(wrap, bg="panel", highlightbackground="border")
        wrap.pack(fill="both", expand=True)

        cols = ("fav", "kurum", "baslik", "tarih", "bit", "kpss")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for c, t, w, anc in (("fav", "★", 30, "center"),
                             ("kurum", "Kurum", 160, "w"),
                             ("baslik", "İlan Başlığı", 260, "w"),
                             ("tarih", "Yayın", 110, "w"),
                             ("bit", "Son Başvuru", 130, "w"),
                             ("kpss", "KPSS", 60, "center")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w,
                             minwidth=40 if c in ("kurum", "baslik") else w,
                             anchor=anc, stretch=(c in ("kurum", "baslik")))

        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self.sec)
        self.tree.bind("<Double-1>", lambda e: self.ilan_sayfasi())
        self.tree.bind("<Return>", lambda e: self.ilan_sayfasi())
        self.tree.bind("<Button-3>", self._sag_tik_goster)
        self.tree.bind("<Motion>", self._tree_hover)
        self.tree.bind("<Leave>", lambda e: self._tree_hover_reset())

        # ---- sağ: detay
        sag = self._frm(pw, bg="bg")
        pw.add(sag, weight=5)

        # Hero kart
        kart = self._kart(sag)
        kart.pack(fill="x", pady=(0, SP["md"]))
        ic = self._frm(kart)
        ic.pack(fill="x", padx=SP["lg"], pady=SP["lg"])

        self.img_cerceve = self._frm(ic, width=THUMB, height=THUMB, bg="panel2")
        self._kayit(self.img_cerceve, bg="panel2")
        self.img_cerceve.pack(side="left", padx=(0, SP["lg"]))
        self.img_cerceve.pack_propagate(False)
        self.img_lbl = self._lbl(self.img_cerceve, "🏛", bg="panel2",
                                 fg="text_dim", font=(FONT, 32))
        self.img_lbl.pack(fill="both", expand=True)
        self.img_lbl.bind("<Button-1>", lambda e: self.resmi_ac())

        bilgi = self._frm(ic)
        bilgi.pack(side="left", fill="both", expand=True)

        self.l_kurum = self._lbl(bilgi, "Bir ilan seçin", font=FS["h2"],
                                 anchor="w", justify="left")
        self.l_kurum.pack(anchor="w")

        self.l_baslik = self._lbl(bilgi, font=FS["h3"], fg="text_dim",
                                  anchor="w", justify="left")
        self.l_baslik.pack(anchor="w", pady=(2, SP["sm"]))

        # Meta chip satırı
        self.meta_frame = self._frm(bilgi)
        self.meta_frame.pack(anchor="w")

        self.l_meta = self._lbl(bilgi, fg="text_dim", anchor="w",
                                justify="left", font=FS["small"])
        self.l_meta.pack(anchor="w", pady=(SP["sm"], 0))

        # Wrapping: pencere boyutu değişince etiketleri sar
        def _wrap(event, _lbls=(self.l_kurum, self.l_baslik, self.l_meta)):
            for l in _lbls:
                l.configure(wraplength=max(200, event.width - 8))
        bilgi.bind("<Configure>", _wrap)

        # Aksiyon buton satırı
        btn = self._frm(sag, bg="bg")
        btn.pack(fill="x", pady=(0, SP["md"]))

        sol_btn = self._frm(btn, bg="bg")
        sol_btn.pack(side="left")
        self._ilan_butonlari = []
        for metin, komut in (("📄  İlan Sayfası", self.ilan_sayfasi),
                             ("🔗  Linki Kopyala", self.kopyala),
                             ("🔄  Detayı Yenile", self.detay_yenile),
                             ("🖼  Görseli Aç", self.resmi_ac)):
            b = ttk.Button(sol_btn, text=metin, style="TButton",
                           command=komut, state="disabled")
            b.pack(side="left", padx=(0, SP["xs"]))
            self._ilan_butonlari.append(b)
        self.b_resim = self._ilan_butonlari[-1]

        self.b_basvur = ttk.Button(btn, text="🌐  Başvur (e-Devlet)",
                                   style="Success.TButton",
                                   command=self.basvur, state="disabled")
        self.b_basvur.pack(side="right")
        self._ilan_butonlari.append(self.b_basvur)

        # Sekmeler
        nb = ttk.Notebook(sag)
        nb.pack(fill="both", expand=True)
        f1 = self._frm(nb)
        f2 = self._frm(nb)
        nb.add(f1, text="  📋  İlan Bilgileri  ")
        nb.add(f2, text="  🔧  Ham Veri  ")
        self.t_bilgi = self._metin_kutusu(f1, "panel", "text", FS["body"])
        self.t_ham = self._metin_kutusu(f2, "editor_bg", "editor_fg", FS["mono"])

    def _metin_kutusu(self, parent, bg, fg, font):
        # Kart içinde metin kutusu
        kart = tk.Frame(parent, bg=self.R[bg],
                        highlightthickness=1,
                        highlightbackground=self.R["border"])
        self._kayit(kart, bg=bg, highlightbackground="border")
        kart.pack(fill="both", expand=True)

        t = tk.Text(kart, wrap="word", font=font, state="disabled", relief="flat",
                    padx=16, pady=14, borderwidth=0,
                    spacing1=2, spacing2=2, spacing3=4)
        self._kayit(t, bg=bg, fg=fg)
        sb = ttk.Scrollbar(kart, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return t

    # ---- tablo hover
    def _tree_hover(self, event):
        item = self.tree.identify_row(event.y)
        if item == self._hover_id:
            return
        self._tree_hover_reset()
        self._hover_id = item
        if item:
            etiketler = tuple(self.tree.item(item, "tags")) + ("hover",)
            self.tree.item(item, tags=etiketler)

    def _tree_hover_reset(self):
        if self._hover_id and self.tree.exists(self._hover_id):
            etiketler = tuple(t for t in self.tree.item(self._hover_id, "tags") if t != "hover")
            self.tree.item(self._hover_id, tags=etiketler)
        self._hover_id = None

    # ================= sağ tık / kısayollar =================
    def _sag_tik_menu(self):
        self.menu_sag = tk.Menu(self, tearoff=0)
        self.menu_sag.add_command(label="⭐ Favorilere Ekle / Çıkar   (Ctrl+D)",
                                  command=self.favori_degis)
        self.menu_sag.add_command(label="📝 Not Ekle / Düzenle", command=self.not_ekle)
        self.menu_sag.add_separator()
        self.menu_sag.add_command(label="🌐 e-Devlet ile Başvur", command=self.basvur)

    def _sag_tik_goster(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.menu_sag.tk_popup(event.x_root, event.y_root)

    def _kisayollar(self):
        self.bind("<F5>", lambda e: self.tara_baslat())
        self.bind("<Control-f>",
                  lambda e: (self.e_ara.focus_set(), self.e_ara.select_range(0, "end")))
        self.bind("<Control-d>", lambda e: self.favori_degis())

    # ================= liste =================
    def _filtre(self) -> db.Filtre:
        return db.Filtre(
            metin=self.q.get().strip(), kategori=self.kat.get(), kpss_tur=self.kpss_tur.get(),
            sadece_yeni=self.v_yeni.get(), sadece_aktif=self.v_aktif.get(),
            sadece_kpss=self.v_kpss.get(), sadece_favori=self.v_fav.get(),
            alakasizlari_gizle=self.v_gizle.get(), siralama=self.sirala.get())

    def _yukle_planla(self):
        """Her tuş vuruşunda değil, yazma durunca (250 ms) listeyi yeniler."""
        if self._ids["yukle"]:
            self.after_cancel(self._ids["yukle"])
        self._ids["yukle"] = self.after(250, self.yukle)

    def yukle(self):
        self._ids["yukle"] = None
        self.cb["values"] = ["Tümü", *db.kategoriler()]
        satirlar = db.ilanlari_getir(self._filtre())
        onceki = self.secili

        self.tree.delete(*self.tree.get_children())
        yeni_sayi = 0
        for i, r in enumerate(satirlar):
            gun = textutil.kalan_gun(r["bit_iso"])
            etiketler = []
            if i % 2:
                etiketler.append("odd")
            if r["favori"] == 1:
                etiketler.append("favori")
            elif r["yeni"]:
                etiketler.append("yeni")
            if not r["aktif"]:
                etiketler.append("pasif")
            else:
                if r["kpss"] == 1:
                    etiketler.append("kpss_ok")
                if gun is not None and 0 <= gun <= config.YAKIN_GUN:
                    etiketler.append("yakinda")
            yeni_sayi += 1 if r["yeni"] else 0
            self.tree.insert("", "end", iid=r["id"], tags=tuple(etiketler), values=(
                "⭐" if r["favori"] == 1 else "", r["kurum"], r["baslik"],
                textutil.tarih_goster(r["tarih_iso"]), textutil.bit_goster(r["bit_iso"]),
                textutil.kpss_goster(r["kpss"])))
        self._sayi_toplam, self._sayi_yeni = len(satirlar), yeni_sayi
        self._sayac_guncelle()
        if onceki and self.tree.exists(onceki):
            self.tree.selection_set(onceki)
            self.tree.see(onceki)

    def _sayac_guncelle(self):
        self.sayac.config(text=f"({self._sayi_toplam} ilan"
                               + (f", {self._sayi_yeni} yeni" if self._sayi_yeni else "") + ")")

    # ================= seçim / detay =================
    def sec(self, _event=None):
        sel = self.tree.selection()
        if not sel or sel[0] == self._gosterilen:
            return
        self._ilan_goster(sel[0])

    def _ilan_goster(self, _id: str):
        self.secili = self._gosterilen = _id
        r = db.get_ilan(_id)
        if not r:
            return
        self._ust_kart(r)
        for b in self._ilan_butonlari:
            b.config(state="normal")
        self.b_resim.config(state="disabled")
        self.resim_yolu = None

        if r["yeni"]:                                # görüntülenen ilan okundu sayılır
            db.okundu_isaretle(_id)
            etiketler = tuple(t for t in self.tree.item(_id, "tags") if t != "yeni")
            self.tree.item(_id, tags=etiketler)
            self._sayi_yeni = max(0, self._sayi_yeni - 1)
            self._sayac_guncelle()

        if r["resim"] and Image:
            self._resim_yukle(_id, r["resim"])
        else:
            self.img_lbl.config(image="", text="🏛")
            self.tkimg = None

        self._metin_yaz(self.t_bilgi, "Detay yükleniyor…")
        self._arka_plan(lambda: service.detay_getir(_id, r["link"]),
                        basari=lambda v: self._detay_goster(_id, v),
                        hata=lambda e: self._detay_hata(_id, e))

    def _ust_kart(self, r):
        fav = "⭐ " if r["favori"] == 1 else ""
        self.l_kurum.config(text=fav + (r["kurum"] or "(Kurum belirtilmemiş)"))
        self.l_baslik.config(text=r["baslik"] or "")

        # Meta chip'leri yenile
        for w in self.meta_frame.winfo_children():
            w.destroy()

        R = self.R
        def chip(metin, fg_key="text_dim", bg_key="panel2"):
            c = tk.Label(self.meta_frame, text=metin, bg=R[bg_key], fg=R[fg_key],
                         font=FS["tiny"], padx=8, pady=3)
            self._kayit(c, bg=bg_key, fg=fg_key)
            c.pack(side="left", padx=(0, 6))
            return c

        bit = textutil.bit_goster(r["bit_iso"])
        if bit:
            chip(f"⏰ {bit}", "warning" if r["aktif"] else "pasif_fg",
                 "warning_soft" if r["aktif"] else "panel2")

        if r["kpss_turleri"]:
            chip(f"📚 KPSS {r['kpss_turleri']}", "accent_text", "accent_soft")

        if r["yeni"]:
            chip("✨ YENİ", "accent_text", "accent_soft")

        if not r["aktif"]:
            chip("⛔ Pasif", "text_muted", "panel2")

        self.l_meta.config(text=f"📝 {r['not_metni']}" if r["not_metni"] else "")

    def _metin_yaz(self, kutu: tk.Text, metin: str, etiket: str | None = None):
        kutu.config(state="normal")
        kutu.delete("1.0", "end")
        if etiket:
            kutu.insert("end", metin, etiket)
        else:
            kutu.insert("end", metin)
        kutu.config(state="disabled")

    def _detay_hata(self, _id, e):
        log.error("Detay alınamadı: %s", e)
        if _id == self.secili:
            self._metin_yaz(self.t_bilgi,
                            f"Detay alınamadı:\n{e}\n\n“Detayı Yenile” ile tekrar deneyebilirsiniz.")

    def _detay_goster(self, _id, veri):
        if _id != self.secili:                       # kullanıcı bu arada başka ilana geçti
            return
        t = self.t_bilgi
        t.config(state="normal")
        t.delete("1.0", "end")
        govde = textutil.ilan_govde(veri)
        if govde is None:
            t.insert("1.0", str(veri))
        else:
            metin, seviye = textutil.basvuru_durumu(govde.get("basTarih"), govde.get("bitTarih"))
            t.insert("end", "BAŞVURU TARİHLERİ\n", "h")
            t.insert("end", metin + "\n\n", seviye)
            t.insert("end", "―" * 60 + "\n\n", "sep")
            t.insert("end", textutil.ilan_metni(govde.get("ilanMetni")) or "Detay metni bulunamadı.")
        t.config(state="disabled")
        self._metin_yaz(self.t_ham, json.dumps(veri, indent=2, ensure_ascii=False))
        # Özet bu çağrıyla güncellenmiş olabilir
        r = db.get_ilan(_id)
        if r:
            self._ust_kart(r)

    # ---- görsel
    def _resim_yukle(self, _id, url):
        def is_():
            yol = service.resim_indir(_id, url)
            img = Image.open(yol)
            img.load()
            img = img.convert("RGB")
            img.thumbnail((THUMB, THUMB), _LANCZOS)
            return yol, img
        self._arka_plan(is_, basari=lambda s: self._resim_goster(_id, *s),
                        hata=lambda e: log.debug("Görsel yüklenemedi: %s", e))

    def _resim_goster(self, _id, yol, img):
        if _id != self.secili:
            return
        self.resim_yolu = yol
        self.tkimg = ImageTk.PhotoImage(img)
        self.img_lbl.config(image=self.tkimg, text="")
        self.b_resim.config(state="normal")

    # ================= tarama =================
    def _aralik_ms(self) -> int:
        return self.ayarlar.otomatik_dk * 60 * 1000

    def periyodik(self):
        self.tara_baslat()
        self._ids["periyodik"] = self.after(self._aralik_ms(), self.periyodik)

    def tara_baslat(self):
        if self.suruyor:
            return
        self.suruyor = True
        self.b_tara.config(state="disabled")
        self.durum.set("İlanlar taranıyor…")
        rss = self.ayarlar.rss_url
        self._arka_plan(lambda: service.tara(rss), basari=self._tara_bitti,
                        hata=lambda e: self._tara_bitti(service.TaraSonucu(hata=str(e))))

    def _tara_bitti(self, s: service.TaraSonucu):
        self.suruyor = False
        self.b_tara.config(state="normal")
        if s.hata:
            self._durum_yaz(f"Tarama başarısız: {s.hata}", 8000)
            return
        self._durum_yaz(f"Tarama tamamlandı — {s.toplam} ilan, {len(s.yeni)} yeni")
        self.yukle()
        if s.yeni:
            ozet = "\n".join(f"• {k + ' — ' if k else ''}{b}" for k, b in s.yeni[:3])
            if len(s.yeni) > 3:
                ozet += f"\n… ve {len(s.yeni) - 3} ilan daha"
            notify.bildir(f"{len(s.yeni)} yeni kamu ilanı", ozet)
        self._onyukle_baslat()

    def _onyukle_baslat(self):
        """Yeni ilanların KPSS bilgisini arka planda (nazikçe) çıkarır."""
        if not self.ayarlar.detay_onyukle or self.onyukleniyor or self._durdur.is_set():
            return
        self.onyukleniyor = True
        self._arka_plan(
            lambda: service.detay_onyukle(config.ONYUKLE_MAX, config.ONYUKLE_BEKLEME, self._durdur),
            basari=self._onyukle_bitti, hata=lambda e: self._onyukle_bitti(0))

    def _onyukle_bitti(self, n: int):
        self.onyukleniyor = False
        if n:
            log.info("%d ilanın detay/KPSS bilgisi güncellendi", n)
            self.yukle()

    # ================= aksiyonlar =================
    def _secili_ilan(self):
        return db.get_ilan(self.secili) if self.secili else None

    def favori_degis(self):
        if not self.secili:
            return
        db.favori_degistir(self.secili)
        self.yukle()
        self._ilan_goster(self.secili)

    def not_ekle(self):
        r = self._secili_ilan()
        if not r:
            return
        notum = simpledialog.askstring("İlan Notu", "Bu ilan için kişisel notunuzu girin:",
                                       initialvalue=r["not_metni"] or "", parent=self)
        if notum is not None:
            db.not_kaydet(r["id"], notum.strip())
            self._ust_kart(db.get_ilan(r["id"]))

    def basvur(self):
        r = self._secili_ilan()
        if r and r["link"]:
            webbrowser.open(r["link"])

    ilan_sayfasi = basvur

    def kopyala(self):
        r = self._secili_ilan()
        if r and r["link"]:
            self.clipboard_clear()
            self.clipboard_append(r["link"])
            self._durum_yaz("İlan linki panoya kopyalandı.")

    def detay_yenile(self):
        r = self._secili_ilan()
        if not r:
            return
        _id = r["id"]
        self._durum_yaz("Detay yenileniyor…", 0)
        self._arka_plan(
            lambda: service.detay_getir(_id, r["link"], zorla=True),
            basari=lambda v: (self._detay_goster(_id, v),
                              self._durum_yaz("İlan detayları güncellendi.")),
            hata=lambda e: (self._durum_yaz("Detay yenilenemedi.", 6000),
                            messagebox.showerror("Hata",
                                                 f"Detay yenilenemedi:\n{e}", parent=self)))

    def resmi_ac(self):
        if self.resim_yolu and os.path.exists(self.resim_yolu):
            try:
                dosya_ac(self.resim_yolu)
            except OSError as e:
                messagebox.showerror("Hata", f"Görsel açılamadı: {e}", parent=self)
        else:
            messagebox.showinfo("Bilgi", "Bu ilan için indirilmiş görsel yok.", parent=self)

    def okundu(self):
        n = db.hepsini_okundu()
        self.yukle()
        self._durum_yaz(f"{n} ilan okundu olarak işaretlendi.")

    def excel(self):
        yol = filedialog.asksaveasfilename(
            parent=self, defaultextension=".xlsx",
            filetypes=[("Excel Dosyası", "*.xlsx")],
            initialfile="kariyer_kapisi_ilanlar.xlsx")
        if not yol:
            return
        self._durum_yaz("Excel hazırlanıyor…", 0)

        def hata(e):
            self._durum_yaz("Excel dışa aktarma başarısız.", 6000)
            if isinstance(e, ImportError):
                messagebox.showerror("Eksik Paket",
                                     "Excel için 'pandas' ve 'openpyxl' gerekli:\n"
                                     "pip install pandas openpyxl", parent=self)
            else:
                messagebox.showerror("Hata", f"Dosya kaydedilemedi: {e}", parent=self)

        self._arka_plan(
            lambda: service.excel_aktar(yol),
            basari=lambda n: (self._durum_yaz(f"{n} ilan Excel'e aktarıldı."),
                              messagebox.showinfo("Başarılı",
                                                  f"{n} ilan aktarıldı:\n{yol}",
                                                  parent=self)),
            hata=hata)

    # ================= ayarlar / kapanış =================
    def _rss_lbl_guncelle(self):
        self.rss_lbl.config(text=f"RSS: {self.ayarlar.rss_url}")

    def rss_gir(self):
        url = simpledialog.askstring(
            "RSS Linki",
            f"Kariyer Kapısı RSS linkini girin:\n(Varsayılan: {config.RSS_VARSAYILAN})",
            initialvalue=self.ayarlar.rss_url, parent=self)
        if url is None:
            return
        temiz = config.rss_normalize(url)
        if not temiz:
            messagebox.showerror("Hata", "Geçersiz RSS linki.", parent=self)
            return
        self.ayarlar.rss_url = temiz
        self.ayarlar.kaydet()
        self._rss_lbl_guncelle()
        self._durum_yaz("RSS linki güncellendi.")
        self.tara_baslat()

    def _kapat(self):
        self._durdur.set()                       # arka plan işlerine dur sinyali
        for anahtar in ("yukle", "periyodik", "pump", "durum"):
            if self._ids.get(anahtar):
                try:
                    self.after_cancel(self._ids[anahtar])
                except tk.TclError:
                    pass
        self.ayarlar.pencere = f"{self.winfo_width()}x{self.winfo_height()}"
        self.ayarlar.kaydet()
        self.destroy()