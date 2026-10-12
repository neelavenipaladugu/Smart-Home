"""Switchable devices: relays on the Pi (GPIO) or on ESP32 room nodes (MQTT)."""
from __future__ import annotations

import logging
import time

log = logging.getLogger("devices")

ICONS = {"light": "💡", "fan": "🌀", "ac": "❄️", "pump": "💧", "alarm": "🚨",
         "lock": "🔒", "plug": "🔌", "tv": "📺", "heater": "🔥"}


class Device:
    driver = "base"

    def __init__(self, cfg: dict, home):
        self.home = home
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.room = cfg.get("room", "Other")
        self.type = cfg.get("type", "plug")
        self.watts = float(cfg.get("watts", 0))
        self.on = False
        self.changed_at = time.time()

    def apply(self, on: bool) -> None:
        raise NotImplementedError

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "room": self.room, "type": self.type,
                "icon": ICONS.get(self.type, "🔌"), "on": self.on, "driver": self.driver,
                "watts": self.watts, "changed_at": self.changed_at, "online": self.online}

    @property
    def online(self) -> bool:
        return True


class GpioDevice(Device):
    driver = "gpio"

    def __init__(self, cfg: dict, home):
        super().__init__(cfg, home)
        self.pin = cfg["pin"]
        self.output = home.hw.output(self.pin, cfg.get("active_high", True))

    def apply(self, on: bool) -> None:
        self.output.set(on)


class MqttDevice(Device):
    driver = "mqtt"

    def __init__(self, cfg: dict, home):
        super().__init__(cfg, home)
        self.node = cfg["node"]
        self.channel = int(cfg["channel"])
        base = home.cfg["mqtt"]["base_topic"]
        self.set_topic = f"{base}/{self.node}/relay/{self.channel}/set"

    def apply(self, on: bool) -> None:
        if self.home.mqtt and self.home.mqtt.connected:
            self.home.mqtt.publish(self.set_topic, "ON" if on else "OFF")
        else:
            log.warning("MQTT not connected - %s state changed locally only", self.id)

    @property
    def online(self) -> bool:
        # In mock mode without a broker the virtual nodes are always "online"
        if self.home.hw.mock and not (self.home.mqtt and self.home.mqtt.connected):
            return True
        return self.home.nodes.get(self.node, False)


def build_devices(home) -> None:
    for cfg in home.cfg["devices"]:
        driver = cfg.get("driver", "gpio")
        cls = {"gpio": GpioDevice, "mqtt": MqttDevice}.get(driver)
        if cls is None:
            raise ValueError(f"Unknown device driver '{driver}' for {cfg['id']}")
        home.devices[cfg["id"]] = cls(cfg, home)
    log.info("Loaded %d devices", len(home.devices))
