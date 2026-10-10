"""
Kariyer Kapısı Takip — REST API (FastAPI)

Mevcut db.py / service.py / textutil.py katmanlarını dış dünyaya açar.
Flutter (Android/iOS) uygulaması bu sunucuya bağlanacak.

Çalıştırma:
    uvicorn api:app --host 0.0.0.0 --port 8000 --reload

Dokümantasyon:
    http://localhost:8000/docs
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
import db
import service
import textutil

# =====================================================================
# LOG
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("api")

# =====================================================================
# YAPILANDIRMA
# =====================================================================
TARAMA_ARALIK_DK = int(os.getenv("TARAMA_ARALIK_DK", "30"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
API_KEY = os.getenv("API_KEY", "").strip()   # boşsa auth devre dışı


# =====================================================================
# PYDANTIC ŞEMALARI
# =====================================================================
class IlanOzet(BaseModel):
    id: str
    kurum: Optional[str] = None
    baslik: Optional[str] = None
    tarih_iso: Optional[str] = None
    bit_iso: Optional[str] = None
    kpss: Optional[int] = None
    kpss_turleri: Optional[str] = None
    favori: int = 0
    yeni: int = 0
    aktif: int = 1
    link: Optional[str] = None
    resim: Optional[str] = None
    not_metni: Optional[str] = None
    # görüntü için hazır alanlar (Flutter doğrudan kullanabilsin)
    tarih_goster: Optional[str] = None
    bit_goster: Optional[str] = None
    kalan_gun: Optional[int] = None
    kpss_goster: Optional[str] = None


class IlanDetay(BaseModel):
    ilan: Optional[IlanOzet] = None
    detay: Optional[dict[str, Any]] = None
    basvuru_durumu: Optional[str] = None
    basvuru_seviye: Optional[str] = None   # "ok" | "warn" | "muted"


class TaraSonuc(BaseModel):
    hata: Optional[str] = None
    toplam: int = 0
    yeni_sayi: int = 0
    yeni: list[dict[str, str]] = Field(default_factory=list)


class FavoriSonuc(BaseModel):
    success: bool
    favori: Optional[int] = None
    hata: Optional[str] = None


class NotIstek(BaseModel):
    metin: str = ""


class OkunduSonuc(BaseModel):
    success: bool
    adet: int = 0


# =====================================================================
# ARKA PLAN ZAMANLAYICI (tarama)
# =====================================================================
_scheduler: BackgroundScheduler | None = None
_tarama_kilidi = threading.Lock()


def _arka_plan_tara():
    """Periyodik tarama — APScheduler tarafından çağrılır."""
    if not _tarama_kilidi.acquire(blocking=False):
        log.info("Tarama zaten sürüyor, atlanıyor.")
        return
    try:
        log.info("Periyodik tarama başlıyor…")
        s = service.tara(config.RSS_VARSAYILAN)
        if s.hata:
            log.warning("Tarama hatası: %s", s.hata)
        else:
            log.info("Tarama tamam — %d ilan, %d yeni", s.toplam, len(s.yeni))
            # KPSS bilgisi önyükleme (opsiyonel)
            try:
                service.detay_onyukle(config.ONYUKLE_MAX, config.ONYUKLE_BEKLEME,
                                      threading.Event())
            except Exception:
                log.exception("Detay önyükleme başarısız")
    except Exception:
        log.exception("Periyodik tarama başarısız")
    finally:
        _tarama_kilidi.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler

    # ---- 1) Veritabanı şemasını hazırla (tablolar + indeksler)
    try:
        db.init_db()
        log.info("Veritabanı şeması hazır: %s", config.DB_PATH)
    except Exception:
        log.exception("DB başlatma hatası")

    # ---- 2) Zamanlayıcıyı başlat
    _scheduler = BackgroundScheduler(timezone="Europe/Istanbul")
    _scheduler.add_job(_arka_plan_tara, "interval",
                       minutes=TARAMA_ARALIK_DK,
                       id="periyodik_tarama",
                       max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("Zamanlayıcı başladı — her %d dk taranacak", TARAMA_ARALIK_DK)

    # ---- 3) İlk taramayı hemen tetikle (arka planda)
    threading.Thread(target=_arka_plan_tara, daemon=True).start()

    yield

    if _scheduler:
        _scheduler.shutdown(wait=False)
    log.info("Sunucu kapanıyor")


# =====================================================================
# APP
# =====================================================================
app = FastAPI(
    title="Kariyer Kapısı Takip API",
    description="Kamu işe alım ilanlarını Flutter/mobil uygulamalara sunar.",
    version=config.VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- basit API key koruması (opsiyonel)
@app.middleware("http")
async def api_key_kontrol(request: Request, call_next):
    if API_KEY and request.url.path not in ("/", "/health", "/docs", "/openapi.json"):
        anahtar = request.headers.get("X-API-Key", "")
        if anahtar != API_KEY:
            return JSONResponse(status_code=401, content={"detail": "Yetkisiz"})
    return await call_next(request)


# =====================================================================
# YARDIMCILAR
# =====================================================================
def _satir_to_ozet(r) -> IlanOzet:
    """DB satırını Pydantic modele çevirir; ek gösterim alanlarını doldurur."""
    d = dict(r)
    return IlanOzet(
        id=str(d.get("id")),
        kurum=d.get("kurum"),
        baslik=d.get("baslik"),
        tarih_iso=d.get("tarih_iso"),
        bit_iso=d.get("bit_iso"),
        kpss=d.get("kpss"),
        kpss_turleri=d.get("kpss_turleri"),
        favori=int(d.get("favori") or 0),
        yeni=int(d.get("yeni") or 0),
        aktif=int(d.get("aktif") or 0),
        link=d.get("link"),
        resim=d.get("resim"),
        not_metni=d.get("not_metni"),
        tarih_goster=textutil.tarih_goster(d.get("tarih_iso")),
        bit_goster=textutil.bit_goster(d.get("bit_iso")),
        kalan_gun=textutil.kalan_gun(d.get("bit_iso")),
        kpss_goster=textutil.kpss_goster(d.get("kpss")),
    )


# =====================================================================
# UÇ NOKTALAR
# =====================================================================
@app.get("/", tags=["genel"])
def kok():
    return {
        "uygulama": config.APP_NAME,
        "versiyon": config.VERSION,
        "durum": "çalışıyor",
        "saat": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/health", tags=["genel"])
def saglik():
    return {"ok": True}


@app.get("/kategoriler", response_model=list[str], tags=["genel"])
def kategoriler():
    """Benzersiz kurum/kategori listesi."""
    return ["Tümü", *db.kategoriler()]


@app.get("/ilanlar", response_model=list[IlanOzet], tags=["ilanlar"])
def ilanlari_getir(
    arama: str = Query("", description="Başlık/kurum içinde arama"),
    kategori: str = Query("Tümü"),
    kpss_tur: str = Query("Tümü"),
    sadece_yeni: bool = False,
    sadece_aktif: bool = True,
    sadece_kpss: bool = False,
    sadece_favori: bool = False,
    alakasizlari_gizle: bool = True,
    siralama: str = Query("Yayın tarihi", pattern="^(Yayın tarihi|Son başvuru)$"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    """Filtrelenmiş ilan listesi. Flutter'ın ana ekranı buradan beslenir."""
    f = db.Filtre(
        metin=arama.strip(),
        kategori=kategori,
        kpss_tur=kpss_tur,
        sadece_yeni=sadece_yeni,
        sadece_aktif=sadece_aktif,
        sadece_kpss=sadece_kpss,
        sadece_favori=sadece_favori,
        alakasizlari_gizle=alakasizlari_gizle,
        siralama=siralama,
    )
    try:
        satirlar = db.ilanlari_getir(f)
    except Exception as e:
        log.exception("ilanlari_getir hatası")
        raise HTTPException(500, f"Liste alınamadı: {e}")

    # Sayfalama
    dilim = satirlar[offset: offset + limit]
    return [_satir_to_ozet(r) for r in dilim]


@app.get("/ilan/{ilan_id}", response_model=IlanDetay, tags=["ilanlar"])
def ilan_detay(ilan_id: str, taze: bool = Query(False, description="API'den yeniden çek")):
    """Belirli bir ilanın detayı. `taze=true` ise önbellek atlanır."""
    r = db.get_ilan(ilan_id)
    if not r:
        raise HTTPException(404, "İlan bulunamadı")
    ozet = _satir_to_ozet(r)

    detay_veri: Optional[dict] = None
    basvuru_metni: Optional[str] = None
    basvuru_seviye: Optional[str] = None

    try:
        ham = service.detay_getir(ilan_id, r["link"], zorla=taze)
        if isinstance(ham, dict):
            detay_veri = ham
        govde = textutil.ilan_govde(ham) if ham else None
        if govde:
            basvuru_metni, basvuru_seviye = textutil.basvuru_durumu(
                govde.get("basTarih"), govde.get("bitTarih")
            )
    except Exception as e:
        log.warning("Detay alınamadı (%s): %s", ilan_id, e)

    # Detay sonrası özet güncellenmiş olabilir
    r2 = db.get_ilan(ilan_id)
    if r2:
        ozet = _satir_to_ozet(r2)

    return IlanDetay(
        ilan=ozet,
        detay=detay_veri,
        basvuru_durumu=basvuru_metni,
        basvuru_seviye=basvuru_seviye,
    )


@app.post("/ilan/{ilan_id}/favori", response_model=FavoriSonuc, tags=["aksiyon"])
def favori_degistir(ilan_id: str):
    """Favori durumunu tersine çevirir."""
    r = db.get_ilan(ilan_id)
    if not r:
        raise HTTPException(404, "İlan bulunamadı")
    try:
        db.favori_degistir(ilan_id)
        r2 = db.get_ilan(ilan_id)
        return FavoriSonuc(success=True, favori=int(r2["favori"]) if r2 else None)
    except Exception as e:
        log.exception("Favori işlemi hatası")
        return FavoriSonuc(success=False, hata=str(e))


@app.post("/ilan/{ilan_id}/okundu", response_model=OkunduSonuc, tags=["aksiyon"])
def okundu_isaretle(ilan_id: str):
    try:
        db.okundu_isaretle(ilan_id)
        return OkunduSonuc(success=True, adet=1)
    except Exception as e:
        raise HTTPException(500, f"İşaretlenemedi: {e}")


@app.post("/okundu-hepsi", response_model=OkunduSonuc, tags=["aksiyon"])
def okundu_hepsi():
    try:
        n = db.hepsini_okundu()
        return OkunduSonuc(success=True, adet=int(n or 0))
    except Exception as e:
        raise HTTPException(500, f"Toplu işaretleme başarısız: {e}")


@app.post("/ilan/{ilan_id}/not", response_model=FavoriSonuc, tags=["aksiyon"])
def not_kaydet(ilan_id: str, istek: NotIstek):
    r = db.get_ilan(ilan_id)
    if not r:
        raise HTTPException(404, "İlan bulunamadı")
    try:
        db.not_kaydet(ilan_id, istek.metin.strip())
        return FavoriSonuc(success=True)
    except Exception as e:
        return FavoriSonuc(success=False, hata=str(e))


@app.post("/tara", response_model=TaraSonuc, tags=["aksiyon"])
def manuel_tara():
    """Kariyer Kapısı RSS'ini hemen tarar (senkron; genelde 5-20 sn)."""
    if not _tarama_kilidi.acquire(blocking=False):
        raise HTTPException(429, "Tarama zaten sürüyor, biraz bekleyin.")
    try:
        s = service.tara(config.RSS_VARSAYILAN)
        return TaraSonuc(
            hata=s.hata,
            toplam=s.toplam,
            yeni_sayi=len(s.yeni),
            yeni=[{"kurum": k or "", "baslik": b} for k, b in s.yeni],
        )
    finally:
        _tarama_kilidi.release()


@app.get("/istatistik", tags=["genel"])
def istatistik():
    """Genel sayılar — dashboard için."""
    try:
        tumu = db.ilanlari_getir(db.Filtre(sadece_aktif=False, alakasizlari_gizle=False))
        aktif = sum(1 for r in tumu if r["aktif"])
        yeni = sum(1 for r in tumu if r["yeni"])
        fav = sum(1 for r in tumu if r["favori"] == 1)
        return {
            "toplam": len(tumu),
            "aktif": aktif,
            "yeni": yeni,
            "favori": fav,
            "son_guncelleme": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as e:
        raise HTTPException(500, f"İstatistik alınamadı: {e}")