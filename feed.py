"""Ağ katmanı: RSS besleme, ilan detay API'si ve görsel indirme (veritabanı bilmez)."""
from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from typing import NamedTuple

import requests
from requests.adapters import HTTPAdapter

from config import API_HEADERS, API_URL, HEADERS, HTTP_TIMEOUT, RESIM_MAX_BAYT
from textutil import tarih_cevir, temiz

try:  # varsa güvenli XML ayrıştırıcı
    from defusedxml import ElementTree as _SafeET  # type: ignore
    _fromstring = _SafeET.fromstring
except ImportError:
    _fromstring = ET.fromstring

log = logging.getLogger(__name__)


def _oturum() -> requests.Session:
    s = requests.Session()
    try:
        from urllib3.util.retry import Retry
        try:
            retry = Retry(total=3, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=frozenset({"GET", "POST"}))
        except TypeError:   # eski urllib3
            retry = Retry(total=3, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504))
        adapter = HTTPAdapter(max_retries=retry)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
    except ImportError:
        pass
    return s


_SESSION = _oturum()


class FeedKaydi(NamedTuple):
    kurum: str
    baslik: str
    kategori: str
    link: str
    tarih_iso: str
    resim: str


def _etiket(tag: str) -> str:
    return tag.split("}")[-1]


def feed_ayristir(icerik: bytes) -> list[FeedKaydi]:
    try:
        root = _fromstring(icerik)
    except ET.ParseError:
        raise RuntimeError("Yanıt geçerli XML değil: " + icerik[:200].decode("utf-8", "replace"))
    kayitlar = []
    for it in root.iter("item"):
        d: dict[str, str] = {}
        resim = ""
        for c in it:
            t = _etiket(c.tag)
            if t in ("enclosure", "content") and c.get("url") and not resim:
                resim = c.get("url")
            d.setdefault(t, (c.text or "").strip())
        tam = temiz(d.get("title", ""))
        link = d.get("link") or d.get("guid", "")
        if not tam or not link:
            continue
        kurum, _, baslik = tam.partition(" - ")
        if not baslik:
            kurum, baslik = "", tam
        kayitlar.append(FeedKaydi(kurum.strip(), baslik.strip(), d.get("category", ""),
                                  link, tarih_cevir(d.get("pubDate", "")), resim))
    return kayitlar


def feed_oku(url: str) -> list[FeedKaydi]:
    r = _SESSION.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return feed_ayristir(r.content)


def detay_cek(guid: str):
    """İlan detayını API'den çeker (dict, ya da ayrıştırılamazsa {'_ham_yanit': ...})."""
    r = _SESSION.post(API_URL, json={"ilanGuid": guid}, headers=API_HEADERS, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"API {r.status_code}: {r.text[:200]}")
    ct = r.headers.get("Content-Type", "")
    if "json" not in ct.lower() and r.text.lstrip()[:1] not in ("[", "{"):
        raise RuntimeError(f"API beklenen JSON yerine döndü: {ct or 'bilinmiyor'}")
    try:
        veri = r.json()
        if isinstance(veri, str):
            veri = json.loads(veri)
    except ValueError:
        veri = {"_ham_yanit": r.text}
    return veri


_UZANTI = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
           "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp"}


def resim_cek(url: str) -> tuple[bytes, str]:
    """(içerik, uzantı). Boyut sınırı aşılırsa hata verir."""
    with _SESSION.get(url, headers=HEADERS, timeout=30, stream=True) as r:
        r.raise_for_status()
        veri = bytearray()
        for parca in r.iter_content(65536):
            veri += parca
            if len(veri) > RESIM_MAX_BAYT:
                raise RuntimeError("Görsel çok büyük")
        ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    return bytes(veri), _UZANTI.get(ct, "")
