"""RFID door access with an RC522 reader (SPI). Known card -> unlock door for N seconds."""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("rfid")


class RfidAccess:
    def __init__(self, home):
        self.home = home
        self.cfg = home.cfg["rfid"]
        self.cards = {str(k): v for k, v in (self.cfg.get("cards") or {}).items()}
        self.lock_device = self.cfg.get("lock_device")
        self._stop = threading.Event()

    def start(self) -> None:
        if self.home.hw.mock:
            log.info("RFID in mock mode - use POST /api/rfid/scan to simulate a card")
            return
        try:
            from mfrc522 import SimpleMFRC522
        except ImportError:
            log.warning("mfrc522 not installed - RFID disabled")
            return
        reader = SimpleMFRC522()
        threading.Thread(target=self._loop, args=(reader,), name="rfid", daemon=True).start()
        log.info("RFID reader started")

    def _loop(self, reader) -> None:
        last_card, last_time = None, 0.0
        while not self._stop.is_set():
            card_id, _ = reader.read_no_block()
            if card_id:
                card = str(card_id)
                if card != last_card or time.time() - last_time > 3:  # debounce
                    self.home.threadsafe(self.scan, card)
                last_card, last_time = card, time.time()
            time.sleep(0.2)

    def scan(self, card: str) -> dict:
        person = self.cards.get(card)
        granted = person is not None
        if self.home.db:
            self.home.db.log_access(card, person, granted)
        if granted:
            self.home.alert(f"Door unlocked by {person}", "info", "rfid")
            if self.home.armed:
                self.home.set_armed(False, f"rfid:{person}")
            if self.lock_device in self.home.devices:
                self.home.spawn(self.home.set_device(self.lock_device, True, f"rfid:{person}",
                                                           float(self.cfg.get("unlock_seconds", 5))))
        else:
            self.home.alert(f"Unknown RFID card tried to open the door (id {card})", "warning", "rfid", photo=True)
        return {"card": card, "person": person, "granted": granted}

    def stop(self) -> None:
        self._stop.set()
