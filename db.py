"""SQLite katmanı: şema/migrasyon, sorgular ve yazma işlemleri."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from config import DB_PATH, GIZLENEN
from textutil import fold

SCHEMA_VERSION = 2

_ILAN_KOLONLARI = (
    ("resim", "TEXT DEFAULT ''"), ("bas_iso", "TEXT DEFAULT ''"),
    ("bit_iso", "TEXT DEFAULT ''"), ("kpss", "INTEGER"),
    ("favori", "INTEGER DEFAULT 0"), ("not_metni", "TEXT DEFAULT ''"),
    ("kpss_turleri", "TEXT DEFAULT ''"),
)


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.create_function("fold", 1, fold, deterministic=True)  # Türkçe duyarsız arama
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Başarıda commit, hatada rollback, her durumda close."""
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------------- şema ----------------
def init_db() -> None:
    with session() as con:
        ver = con.execute("PRAGMA user_version").fetchone()[0]
        if ver < 1:
            con.execute("""CREATE TABLE IF NOT EXISTS ilan(
                id TEXT PRIMARY KEY, kurum TEXT, baslik TEXT, kategori TEXT, link TEXT,
                tarih_iso TEXT, eklenme TEXT, yeni INTEGER DEFAULT 1, aktif INTEGER DEFAULT 1)""")
            mevcut = {r["name"] for r in con.execute("PRAGMA table_info(ilan)")}
            for kolon, tip in _ILAN_KOLONLARI:      # eski veritabanlarını yükselt
                if kolon not in mevcut:
                    con.execute(f"ALTER TABLE ilan ADD COLUMN {kolon} {tip}")
            con.execute("CREATE TABLE IF NOT EXISTS detay"
                        "(id TEXT PRIMARY KEY, veri TEXT, cekildi TEXT)")
        if ver < 2:
            for kolon in ("aktif", "tarih_iso", "bit_iso", "kpss", "kategori"):
                con.execute(f"CREATE INDEX IF NOT EXISTS ix_ilan_{kolon} ON ilan({kolon})")
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


# ---------------- yazma ----------------
def ilan_id(link: str) -> str:
    return hashlib.md5(link.encode()).hexdigest()


def ilan_kaydet(con: sqlite3.Connection, kurum, baslik, kategori, link, tarih_iso, resim) -> bool:
    """Yeni eklendiyse True; var olanı günceller (boş değerlerle üzerine yazmaz)."""
    _id = ilan_id(link)
    cur = con.execute(
        "INSERT OR IGNORE INTO ilan(id,kurum,baslik,kategori,link,tarih_iso,eklenme,resim) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (_id, kurum, baslik, kategori, link, tarih_iso,
         datetime.now().strftime("%Y-%m-%d %H:%M"), resim))
    if cur.rowcount == 1:
        return True
    con.execute(
        "UPDATE ilan SET kurum=?, baslik=?, kategori=?, aktif=1, "
        "tarih_iso=COALESCE(NULLIF(?,''), tarih_iso), resim=COALESCE(NULLIF(?,''), resim) "
        "WHERE id=?", (kurum, baslik, kategori, tarih_iso, resim, _id))
    return False


def senkronla(con: sqlite3.Connection, kayitlar) -> list[tuple[str, str]]:
    """Beslemeyi veritabanıyla eşitler. Yeni eklenenlerin (kurum, başlık) listesini döndürür.
    Besleme boşsa mevcut kayıtlar pasifleştirilmez (site arızasına karşı koruma)."""
    if not kayitlar:
        return []
    con.execute("UPDATE ilan SET aktif=0")
    return [(k.kurum, k.baslik) for k in kayitlar
            if ilan_kaydet(con, k.kurum, k.baslik, k.kategori, k.link, k.tarih_iso, k.resim)]


def favori_degistir(_id: str) -> int:
    with session() as con:
        con.execute("UPDATE ilan SET favori = 1 - COALESCE(favori, 0) WHERE id=?", (_id,))
        return con.execute("SELECT COALESCE(favori,0) FROM ilan WHERE id=?", (_id,)).fetchone()[0]


def not_kaydet(_id: str, metin: str) -> None:
    with session() as con:
        con.execute("UPDATE ilan SET not_metni=? WHERE id=?", (metin, _id))


def okundu_isaretle(_id: str) -> None:
    with session() as con:
        con.execute("UPDATE ilan SET yeni=0 WHERE id=?", (_id,))


def hepsini_okundu() -> int:
    with session() as con:
        return con.execute("UPDATE ilan SET yeni=0 WHERE yeni=1").rowcount


def detay_yaz(con: sqlite3.Connection, _id: str, veri_json: str) -> None:
    con.execute("INSERT OR REPLACE INTO detay VALUES(?,?,?)",
                (_id, veri_json, datetime.now().strftime("%Y-%m-%d %H:%M")))


def ozet_yaz(con: sqlite3.Connection, _id: str, bas: str, bit: str, kpss: int, turler: str) -> None:
    con.execute("UPDATE ilan SET bas_iso=?, bit_iso=?, kpss=?, kpss_turleri=? WHERE id=?",
                (bas, bit, kpss, turler, _id))


# ---------------- okuma ----------------
def detay_oku(_id: str) -> sqlite3.Row | None:
    with session() as con:
        return con.execute("SELECT veri, cekildi FROM detay WHERE id=?", (_id,)).fetchone()


def get_ilan(_id: str) -> sqlite3.Row | None:
    with session() as con:
        return con.execute("SELECT * FROM ilan WHERE id=?", (_id,)).fetchone()


def kategoriler() -> list[str]:
    with session() as con:
        return [r[0] for r in con.execute(
            "SELECT DISTINCT kategori FROM ilan WHERE kategori<>'' ORDER BY kategori")]


def detay_bekleyenler(limit: int) -> list[sqlite3.Row]:
    """KPSS bilgisi henüz çıkarılmamış aktif ilanlar (en yeniden başlayarak)."""
    with session() as con:
        return con.execute(
            "SELECT id, link FROM ilan WHERE aktif=1 AND kpss IS NULL "
            "ORDER BY tarih_iso DESC LIMIT ?", (limit,)).fetchall()


def export_satirlari() -> list[sqlite3.Row]:
    with session() as con:
        return con.execute(
            "SELECT kurum, baslik, kategori, tarih_iso, bit_iso, kpss_turleri, favori, "
            "not_metni, link FROM ilan ORDER BY tarih_iso DESC").fetchall()


@dataclass
class Filtre:
    metin: str = ""
    kategori: str = "Tümü"
    kpss_tur: str = "Tümü"
    sadece_yeni: bool = False
    sadece_aktif: bool = True
    sadece_kpss: bool = False
    sadece_favori: bool = False
    alakasizlari_gizle: bool = True
    siralama: str = "Yayın tarihi"


def _like(s: str) -> str:
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def ilanlari_getir(f: Filtre) -> list[sqlite3.Row]:
    sql = ["SELECT id, kurum, baslik, tarih_iso, bit_iso, kpss, yeni, aktif, kategori, favori "
           "FROM ilan WHERE 1=1"]
    p: list = []
    for kelime in fold(f.metin).split():
        k = _like(kelime)
        sql.append("AND (fold(kurum) LIKE ? ESCAPE '\\' OR fold(baslik) LIKE ? ESCAPE '\\')")
        p += [k, k]
    if f.kategori and f.kategori != "Tümü":
        sql.append("AND kategori = ?")
        p.append(f.kategori)
    if f.kpss_tur and f.kpss_tur != "Tümü":
        sql.append("AND (',' || kpss_turleri || ',') LIKE ?")
        p.append(f"%,{f.kpss_tur},%")
    if f.sadece_yeni:
        sql.append("AND yeni = 1")
    if f.sadece_aktif:
        sql.append("AND aktif = 1")
    if f.sadece_kpss:
        sql.append("AND kpss = 1")
    if f.sadece_favori:
        sql.append("AND favori = 1")
    if f.alakasizlari_gizle:
        sql.append(f"AND COALESCE(kategori,'') NOT IN ({','.join('?' * len(GIZLENEN))})")
        p += list(GIZLENEN)
    if f.siralama == "Son başvuru":
        # Süresi dolmamışlar önce (yakın tarih başta), sonra dolanlar, en sonda tarihsizler
        sql.append("ORDER BY CASE WHEN COALESCE(bit_iso,'')='' THEN 2 "
                   "WHEN bit_iso < ? THEN 1 ELSE 0 END, bit_iso ASC")
        p.append(datetime.now().isoformat(timespec="seconds"))
    else:
        sql.append("ORDER BY tarih_iso DESC")
    with session() as con:
        return con.execute(" ".join(sql), p).fetchall()
