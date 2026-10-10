"""Kariyer Kapısı Takip — giriş noktası.

Kurulum:  pip install -r requirements.txt
Çalıştır: python main.py
"""
from __future__ import annotations

import logging
import queue
import sys
from logging.handlers import RotatingFileHandler

import config


class KuyrukHandler(logging.Handler):
    """Log kayıtlarını arayüzdeki log kutusuna iletmek için kuyruğa yazar."""
    def __init__(self, q: "queue.Queue[str]"):
        super().__init__(level=logging.INFO)
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s  [%(levelname)s] %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.q.put(self.format(record))


def logging_kur() -> "queue.Queue[str]":
    kok = logging.getLogger()
    kok.setLevel(logging.DEBUG)
    dosya = RotatingFileHandler(config.LOG_PATH, maxBytes=512_000, backupCount=3, encoding="utf-8")
    dosya.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    kok.addHandler(dosya)
    q: "queue.Queue[str]" = queue.Queue()
    kok.addHandler(KuyrukHandler(q))
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    return q


def main() -> int:
    log_kuyrugu = logging_kur()
    if sys.platform.startswith("win"):           # yüksek DPI'da net görüntü
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    import db
    import ui
    ayarlar = config.Ayarlar.yukle()
    db.init_db()
    logging.getLogger(__name__).info("%s v%s başlatıldı", config.APP_NAME, config.VERSION)
    ui.App(ayarlar, log_kuyrugu).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
