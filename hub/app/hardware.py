"""Hardware abstraction layer.

Real mode uses gpiozero / adafruit-circuitpython-dht on a Raspberry Pi.
Mock mode simulates every pin so the whole hub runs on a normal PC.
"""
from __future__ import annotations

import logging
import random
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger("hardware")


def is_raspberry_pi() -> bool:
    try:
        return "raspberry pi" in Path("/proc/device-tree/model").read_text().lower()
    except OSError:
        return False


def decide_mock(setting) -> bool:
    if isinstance(setting, bool):
        return setting
    return not is_raspberry_pi()


# ---------------------------------------------------------------- outputs
class MockOutput:
    def __init__(self, pin: int):
        self.pin = pin
        self.value = False

    def set(self, on: bool) -> None:
        self.value = on
        log.debug("[mock] GPIO%s -> %s", self.pin, "HIGH(on)" if on else "LOW(off)")

    def close(self) -> None:
        pass


class RealOutput:
    def __init__(self, pin: int, active_high: bool):
        from gpiozero import OutputDevice

        self.dev = OutputDevice(pin, active_high=active_high, initial_value=False)

    @property
    def value(self) -> bool:
        return bool(self.dev.value)

    def set(self, on: bool) -> None:
        self.dev.on() if on else self.dev.off()

    def close(self) -> None:
        self.dev.close()


# ---------------------------------------------------------------- inputs
class MockInput:
    """Simulated digital input. PIR sensors fire randomly so automations can be seen working."""

    def __init__(self, pin: int, kind: str, on_change: Callable[[bool], None]):
        self.pin = pin
        self.kind = kind
        self.value = False
        self.on_change = on_change
        self._stop = threading.Event()
        if kind == "pir":
            threading.Thread(target=self._simulate_motion, daemon=True).start()

    def _simulate_motion(self) -> None:
        while not self._stop.wait(random.uniform(40, 120)):
            self.inject(True)
            if self._stop.wait(random.uniform(4, 10)):
                return
            self.inject(False)

    def inject(self, value: bool) -> None:
        if value != self.value:
            self.value = value
            self.on_change(value)

    def close(self) -> None:
        self._stop.set()


class RealInput:
    def __init__(self, pin: int, pull: str, active_low: bool, on_change: Callable[[bool], None]):
        from gpiozero import DigitalInputDevice

        if pull == "up":
            self.dev = DigitalInputDevice(pin, pull_up=True, bounce_time=0.05)
        elif pull == "down":
            self.dev = DigitalInputDevice(pin, pull_up=False, bounce_time=0.05)
        else:
            self.dev = DigitalInputDevice(pin, pull_up=None, active_state=not active_low, bounce_time=0.05)
        self.dev.when_activated = lambda: on_change(True)
        self.dev.when_deactivated = lambda: on_change(False)

    @property
    def value(self) -> bool:
        return bool(self.dev.is_active)

    def close(self) -> None:
        self.dev.close()


# ---------------------------------------------------------------- DHT22
class MockDHT:
    def __init__(self, pin: int):
        self.t = random.uniform(24, 29)
        self.h = random.uniform(45, 65)

    def read(self) -> dict | None:
        self.t = min(38, max(16, self.t + random.uniform(-0.4, 0.45)))
        self.h = min(95, max(20, self.h + random.uniform(-1.5, 1.5)))
        return {"temperature": round(self.t, 1), "humidity": round(self.h, 1)}

    def close(self) -> None:
        pass


class RealDHT:
    def __init__(self, pin: int):
        import adafruit_dht
        import board

        self.dev = adafruit_dht.DHT22(getattr(board, f"D{pin}"), use_pulseio=False)

    def read(self) -> dict | None:
        # DHT sensors often fail a single read - retry a few times
        for _ in range(3):
            try:
                t, h = self.dev.temperature, self.dev.humidity
                if t is not None and h is not None:
                    return {"temperature": round(t, 1), "humidity": round(h, 1)}
            except RuntimeError:
                pass
            threading.Event().wait(2)
        return None

    def close(self) -> None:
        self.dev.exit()


# ---------------------------------------------------------------- factory
class Hardware:
    def __init__(self, mock: bool):
        self.mock = mock
        self._closables = []
        log.info("Hardware mode: %s", "SIMULATED (mock)" if mock else "REAL GPIO")

    def output(self, pin: int, active_high: bool = True):
        dev = MockOutput(pin) if self.mock else RealOutput(pin, active_high)
        self._closables.append(dev)
        return dev

    def input(self, pin: int, kind: str, on_change, pull: str = "none", active_low: bool = False):
        if self.mock:
            dev = MockInput(pin, kind, on_change)
        else:
            dev = RealInput(pin, pull, active_low, on_change)
        self._closables.append(dev)
        return dev

    def dht22(self, pin: int):
        dev = MockDHT(pin) if self.mock else RealDHT(pin)
        self._closables.append(dev)
        return dev

    def close(self) -> None:
        for dev in self._closables:
            try:
                dev.close()
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
