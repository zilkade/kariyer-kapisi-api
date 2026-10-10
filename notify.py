"""Opsiyonel masaüstü bildirimi (winotify -> plyer -> sessizce vazgeç)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def bildir(baslik: str, mesaj: str) -> None:
    try:
        from winotify import Notification, audio  # type: ignore
        n = Notification(app_id="Kariyer Kapısı Takip", title=baslik, msg=mesaj, duration="short")
        n.set_audio(audio.Default, loop=False)
        n.show()
        return
    except ImportError:
        pass
    except Exception as e:
        log.debug("winotify hatası: %s", e)
    try:
        from plyer import notification  # type: ignore
        notification.notify(title=baslik, message=mesaj, timeout=8)
    except ImportError:
        log.debug("Bildirim paketi kurulu değil (winotify/plyer)")
    except Exception as e:
        log.debug("plyer hatası: %s", e)
