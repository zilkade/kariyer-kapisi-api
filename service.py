"""İş mantığı: tarama, detay önbelleği, KPSS özeti, görsel önbelleği, Excel dışa aktarma."""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import db
import feed
import textutil
from config import CACHE_SAAT, IMG_DIR

log = logging.getLogger(__name__)


@dataclass
class TaraSonucu:
    toplam: int = 0
    yeni: list[tuple[str, str]] = field(default_factory=list)
    hata: str | None = None


def tara(rss_url: str) -> TaraSonucu:
    try:
        kayitlar = feed.feed_oku(rss_url)
    except Exception as e:                      # ağ, XML, HTTP hataları
        log.error("Besleme okunamadı: %s", e)
        return TaraSonucu(hata=str(e))
    if not kayitlar:
        log.warning("Besleme boş döndü; mevcut kayıtlar pasifleştirilmedi.")
    with db.session() as con:
        yeni = db.senkronla(con, kayitlar)
    log.info("Beslemede %d ilan, %d yeni", len(kayitlar), len(yeni))
    return TaraSonucu(len(kayitlar), yeni)


# ---------------- detay ----------------
def _taze(cekildi: str) -> bool:
    try:
        yas = (datetime.now() - datetime.strptime(cekildi, "%Y-%m-%d %H:%M")).total_seconds()
    except (TypeError, ValueError):
        return False
    return yas < CACHE_SAAT * 3600


def _ozetle(_id: str, veri) -> None:
    govde = textutil.ilan_govde(veri)
    if govde is None:
        return
    kpss, turler = textutil.kpss_analiz(govde.get("ilanMetni"))
    with db.session() as con:
        db.ozet_yaz(con, _id, govde.get("basTarih") or "", govde.get("bitTarih") or "", kpss, turler)


def detay_getir(_id: str, link: str, zorla: bool = False):
    if not zorla:
        satir = db.detay_oku(_id)
        if satir and _taze(satir["cekildi"]):
            try:
                veri = json.loads(satir["veri"])
            except ValueError:
                veri = None
            if veri is not None:
                _ozetle(_id, veri)
                return veri
    guid = textutil.guid_al(link)
    if not guid:
        raise RuntimeError("Linkte ilan numarası (GUID) bulunamadı")
    veri = feed.detay_cek(guid)
    with db.session() as con:
        db.detay_yaz(con, _id, json.dumps(veri, ensure_ascii=False))
    _ozetle(_id, veri)
    return veri


def detay_onyukle(limit: int, bekleme: float, durdur: threading.Event) -> int:
    """KPSS bilgisi eksik ilanların detayını nazikçe (aralıklı) çeker; kaç tanesi çekildi döner."""
    n = 0
    for satir in db.detay_bekleyenler(limit):
        if durdur.is_set():
            break
        try:
            detay_getir(satir["id"], satir["link"])
            n += 1
        except Exception as e:
            log.warning("Önyükleme atlandı (%s): %s", satir["id"][:8], e)
        if durdur.wait(bekleme):
            break
    return n


# ---------------- görsel ----------------
def resim_indir(_id: str, url: str) -> Path:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    var = next((p for p in IMG_DIR.glob(f"{_id}.*") if p.stat().st_size > 0), None)
    if var:
        return var
    veri, uzanti = feed.resim_cek(url)
    uzanti = uzanti or os.path.splitext(urlparse(url).path)[1].lower() or ".png"
    yol = IMG_DIR / f"{_id}{uzanti}"
    tmp = yol.with_suffix(yol.suffix + ".part")
    tmp.write_bytes(veri)
    os.replace(tmp, yol)
    return yol


# ---------------- Excel ----------------
def excel_aktar(yol: str) -> int:
    """Tüm ilanları .xlsx'e yazar; satır sayısını döndürür. pandas/openpyxl yoksa ImportError."""
    import pandas as pd

    satirlar = db.export_satirlari()
    df = pd.DataFrame([{
        "Kurum": r["kurum"], "Başlık": r["baslik"], "Kategori": r["kategori"],
        "Yayın": textutil.tarih_goster(r["tarih_iso"]),
        "Son Başvuru": textutil.bit_goster(r["bit_iso"]).split(" (")[0],
        "KPSS Türleri": r["kpss_turleri"] or "",
        "Favori": "Evet" if r["favori"] == 1 else "",
        "Not": r["not_metni"] or "", "Link": r["link"],
    } for r in satirlar])
    with pd.ExcelWriter(yol, engine="openpyxl") as yazici:
        df.to_excel(yazici, index=False, sheet_name="İlanlar")
        ws = yazici.sheets["İlanlar"]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for kolon in ws.columns:
            genislik = max((len(str(h.value or "")) for h in kolon), default=8)
            ws.column_dimensions[kolon[0].column_letter].width = min(max(genislik + 2, 10), 60)
    return len(df)
