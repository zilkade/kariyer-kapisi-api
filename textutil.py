"""Saf (arayüzden/ağdan bağımsız) metin, tarih ve KPSS yardımcıları."""
from __future__ import annotations

import html
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlparse

from config import BELIRSIZ_YIL_ESIGI, KPSS_TURLERI

_TR = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iisSgGuUoOcC")


def fold(s) -> str:
    """Türkçe karakterleri sadeleştirip küçük harfe çevirir (arama/karşılaştırma için)."""
    return ("" if s is None else str(s)).translate(_TR).lower()


# ---------------- tarih ----------------
def tarih_parse(s) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s)[:19])
    except (ValueError, TypeError):
        return None


def tarih_cevir(s: str) -> str:
    """RSS pubDate -> 'YYYY-MM-DD HH:MM' (yerel saat)."""
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is not None:
            d = d.astimezone().replace(tzinfo=None)
        return d.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""


def tarih_goster(t) -> str:
    d = tarih_parse(t)
    return d.strftime("%d.%m.%Y %H:%M") if d else ""


def _belirsiz(d: datetime, simdi: datetime) -> bool:
    return d.year - simdi.year >= BELIRSIZ_YIL_ESIGI


def bit_goster(bit, simdi: datetime | None = None) -> str:
    simdi = simdi or datetime.now()
    e = tarih_parse(bit)
    if not e:
        return ""
    if _belirsiz(e, simdi):
        return "belirsiz"
    s = e.strftime("%d.%m.%Y")
    if e < simdi:
        return s + " (bitti)"
    return f"{s} ({(e - simdi).days} gün)"


def kalan_gun(bit, simdi: datetime | None = None) -> int | None:
    """Kalan tam gün; süresi dolmuşsa -1; tarih yok/belirsizse None."""
    simdi = simdi or datetime.now()
    e = tarih_parse(bit)
    if not e or _belirsiz(e, simdi):
        return None
    return -1 if e < simdi else (e - simdi).days


def basvuru_durumu(bas, bit, simdi: datetime | None = None) -> tuple[str, str]:
    """(gösterim metni, seviye) döner; seviye 'ok' veya 'warn'."""
    simdi = simdi or datetime.now()
    b, e = tarih_parse(bas), tarih_parse(bit)
    if not b or not e:
        return "-", "warn"
    if _belirsiz(b, simdi) or _belirsiz(e, simdi):
        return "Tarihler kurum tarafından daha sonra belirlenecektir.", "warn"
    s = f"{b:%d.%m.%Y %H:%M}  –  {e:%d.%m.%Y %H:%M}"
    if e < simdi:
        return s + "   (süresi dolmuş)", "warn"
    kalan = e - simdi
    if kalan.days == 0:
        return s + f"   (bugün bitiyor, {kalan.seconds // 3600} saat kaldı)", "ok"
    return s + f"   ({kalan.days} gün kaldı)", "ok"


def kpss_goster(k) -> str:
    return "KPSS" if k == 1 else ("—" if k == 0 else "")


# ---------------- metin temizleme ----------------
_ETIKET = re.compile(r"<[^>]+>")


def temiz(s) -> str:
    """Tek satırlık metin: etiketleri sil, boşlukları sadeleştir."""
    return " ".join(html.unescape(_ETIKET.sub(" ", s or "")).split())


_BB = r"b|i|u|s|url|list|size|color|justify|left|right|center|quote|code|email|img"
_BB_URL = re.compile(r"\[url=([^\]]+)\](.*?)\[/url\]", re.I | re.S)
_BB_URL_BASIT = re.compile(r"\[url\](.*?)\[/url\]", re.I | re.S)
_BB_BLOK_KAPAT = re.compile(r"\[/(?:list|justify|left|right|center|quote|code)\]", re.I)
_BB_KALAN = re.compile(rf"\[/?(?:{_BB})(?:=[^\]]*)?\]", re.I)
_HTML_SATIR = re.compile(r"<\s*(?:br\s*/?|/p|/div|/li|/tr|/h[1-6])\s*>", re.I)
_HTML_MADDE = re.compile(r"<\s*li[^>]*>", re.I)


def ilan_metni(s) -> str:
    """İlan gövdesini (HTML/BBCode karışık olabilir) okunur, satır yapısı korunmuş düz metne çevirir."""
    if not s:
        return ""
    s = str(s)
    s = _BB_URL.sub(lambda m: m.group(1) if m.group(2).strip() == m.group(1).strip()
                    else f"{m.group(2)} ({m.group(1)})", s)
    s = _BB_URL_BASIT.sub(r"\1", s)
    s = s.replace("[*]", "\n• ")
    s = _BB_BLOK_KAPAT.sub("\n", s)
    s = _BB_KALAN.sub("", s)
    s = _HTML_MADDE.sub("\n• ", s)
    s = _HTML_SATIR.sub("\n", s)
    s = _ETIKET.sub(" ", s)
    s = html.unescape(s).replace("\xa0", " ")
    metin = "\n".join(" ".join(satir.split()) for satir in s.splitlines())
    return re.sub(r"\n{3,}", "\n\n", metin).strip()


# ---------------- API/link yardımcıları ----------------
def guid_al(link: str) -> str:
    return (parse_qs(urlparse(link).query).get("i") or [""])[0]


def ilan_govde(veri):
    """API yanıtı {'ilanModel': {...}} veya doğrudan {...} olabilir; gövdeyi döndürür."""
    if isinstance(veri, dict):
        ic = veri.get("ilanModel")
        return ic if isinstance(ic, dict) else veri
    return None


# ---------------- KPSS tespiti ----------------
_KPSS_YOK = re.compile(
    r"kpss[^.\n;]{0,60}?(?:aranma|gerekme|sarti (?:yok|bulunmama)|tabi degil|tabi olmaks)")


def kpss_analiz(metin) -> tuple[int, str]:
    """(kpss_var 0/1, 'P3,P10' gibi tür listesi). Sezgiseldir; 'KPSS şartı aranmaz' gibi
    olumsuz ifadeleri yanlış pozitif saymaz."""
    m = fold(temiz(metin))
    var = "kpss" in m or "kamu personeli secme" in m
    if not var:
        return 0, ""
    turler = [t for t in KPSS_TURLERI if re.search(rf"\b{t.lower()}\b", m)]
    if turler:
        return 1, ",".join(sorted(turler, key=lambda t: int(t[1:])))
    if _KPSS_YOK.search(m):
        return 0, ""
    return 1, "KPSS"
