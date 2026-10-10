"""Sabitler, temalar ve kalıcı kullanıcı ayarları."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

APP_NAME = "Kariyer Kapısı Takip"
VERSION = "3.0"

# Tüm veriler betiğin yanındaki klasörde tutulur (çalışma klasöründen bağımsız).
BASE_DIR = PatBASE_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent)))
BASE_DIR.mkdir(parents=True, exist_ok=True)h(__file__).resolve().parent
DB_PATH = BASE_DIR / "kariyer_kapisi_v2.db"      # v2 şemasıyla uyumlu: eski veriniz korunur
CONFIG_PATH = BASE_DIR / "config.json"
LEGACY_RSS_PATH = BASE_DIR / "rss_url.txt"        # eski sürümden otomatik içe aktarılır
IMG_DIR = BASE_DIR / "ilan_resimleri"
LOG_PATH = BASE_DIR / "takip.log"

UA = f"KariyerKapisi-Takip/{VERSION} (kisisel kullanim)"
HEADERS = {"User-Agent": UA}
API_URL = "https://api.kariyerkapisi.gov.tr/api/ilan/GetIlanPreviewPublic"
API_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json; charset=utf-8",
}
RSS_VARSAYILAN = "https://kariyerkapisi.gov.tr/RSS"

HTTP_TIMEOUT = 25            # sn
RESIM_MAX_BAYT = 10 * 1024 * 1024
CACHE_SAAT = 12              # detay önbellek süresi
ONYUKLE_BEKLEME = 1.5        # arka plan detay çekimleri arası bekleme (sn)
ONYUKLE_MAX = 40             # tarama başına en fazla kaç detay önyüklenir
BELIRSIZ_YIL_ESIGI = 5       # tarih >= bugün+5 yıl ise "belirsiz" sayılır
YAKIN_GUN = 3                # son başvurusu <= 3 gün kalanlar vurgulanır

GIZLENEN = (
    "Askeri Personel İlanları",
    "Yurt Dışı Eğitim İlanları",
    "Görevde Yükselme ve Unvan Değişikliği İlanları",
    "Yeterlik Sınavı İlanları",
)
KPSS_TURLERI = ("P3", "P10", "P93", "P94", "P121")


# =====================================================================
# TEMA PALETLERİ — profesyonel, semantik renkler
# =====================================================================
TEMALAR = {
    "dark": {
        "bg":           "#0f1115",
        "panel":        "#171a21",
        "panel2":       "#1f232b",
        "panel3":       "#232833",       # hover / pressed
        "border":       "#2a2f3a",
        "border2":      "#3a4150",       # vurgulu kenarlık

        "text":         "#e5e7eb",
        "text_dim":     "#9ca3af",
        "text_muted":   "#6b7280",

        "accent":       "#3b82f6",
        "accent2":      "#60a5fa",
        "accent_soft":  "#1e3a5f",
        "accent_text":  "#dbeafe",

        "success":      "#10b981",
        "success_soft": "#064e3b",
        "warning":      "#f59e0b",
        "warning_soft": "#78350f",
        "danger":       "#ef4444",
        "danger_soft":  "#7f1d1d",

        "row_alt":      "#1a1d24",
        "row_hover":    "#232833",
        "row_sel":      "#1e3a5f",

        "yeni_fg":      "#60a5fa",
        "yakinda_fg":   "#fbbf24",
        "pasif_fg":     "#4b5563",

        "editor_bg":    "#0d1017",
        "editor_fg":    "#cbd5e1",

        "shadow":       "#0a0c10",
        "divider":      "#232833",
    },
    "light": {
        "bg":           "#f6f7f9",
        "panel":        "#ffffff",
        "panel2":       "#f0f2f5",
        "panel3":       "#e8ebf0",
        "border":       "#e3e6eb",
        "border2":      "#c7cdd6",

        "text":         "#1a1d21",
        "text_dim":     "#6b7280",
        "text_muted":   "#9ca3af",

        "accent":       "#2563eb",
        "accent2":      "#1d4ed8",
        "accent_soft":  "#dbeafe",
        "accent_text":  "#1e40af",

        "success":      "#059669",
        "success_soft": "#d1fae5",
        "warning":      "#d97706",
        "warning_soft": "#fef3c7",
        "danger":       "#dc2626",
        "danger_soft":  "#fee2e2",

        "row_alt":      "#fafbfc",
        "row_hover":    "#eef2ff",
        "row_sel":      "#dbeafe",

        "yeni_fg":      "#2563eb",
        "yakinda_fg":   "#d97706",
        "pasif_fg":     "#9ca3af",

        "editor_bg":    "#f8fafc",
        "editor_fg":    "#334155",

        "shadow":       "#e5e7eb",
        "divider":      "#eef0f3",
    },
}


def rss_normalize(url: str | None) -> str | None:
    """Adresi temizler; geçersizse None döner."""
    url = (url or "").strip()
    if not url:
        return None
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url if urlparse(url).netloc else None


_GEOM = re.compile(r"^\d{3,5}x\d{3,5}$")


@dataclass
class Ayarlar:
    rss_url: str = RSS_VARSAYILAN
    tema: str = "dark"
    otomatik_dk: int = 30
    detay_onyukle: bool = True      # yeni ilanların KPSS bilgisini arka planda çek
    pencere: str = "1520x880"

    def __post_init__(self) -> None:
        self.rss_url = rss_normalize(self.rss_url) or RSS_VARSAYILAN
        if self.tema not in TEMALAR:
            self.tema = "dark"
        try:
            self.otomatik_dk = max(5, int(self.otomatik_dk))
        except (TypeError, ValueError):
            self.otomatik_dk = 30
        self.detay_onyukle = bool(self.detay_onyukle)
        if not _GEOM.match(str(self.pencere)):
            self.pencere = "1520x880"

    @classmethod
    def yukle(cls) -> "Ayarlar":
        veri: object = {}
        try:
            if CONFIG_PATH.exists():
                veri = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            elif LEGACY_RSS_PATH.exists():
                veri = {"rss_url": LEGACY_RSS_PATH.read_text(encoding="utf-8").strip()}
        except (OSError, ValueError) as e:
            log.warning("Ayar dosyası okunamadı, varsayılanlar kullanılıyor: %s", e)
        if not isinstance(veri, dict):
            veri = {}
        izinli = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in veri.items() if k in izinli})

    def kaydet(self) -> bool:
        tmp = CONFIG_PATH.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, CONFIG_PATH)
            return True
        except OSError as e:
            log.error("Ayarlar kaydedilemedi: %s", e)
            return False