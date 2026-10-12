"""Sensors: DHT22, digital inputs (PIR / gas / door / leak), MQTT room nodes, Pi system stats."""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

log = logging.getLogger("sensors")

UNITS = {"temperature": "°C", "humidity": "%", "soil": "%", "cpu_temp": "°C", "cpu": "%",
         "memory": "%", "disk": "%", "power": "W", "energy": "kWh", "light": "%", "voltage": "V", "current": "A"}


class Sensor:
    kind = "base"
    icon = "📟"

    def __init__(self, cfg: dict, home):
        self.home = home
        self.cfg = cfg
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.room = cfg.get("room", "Other")
        self.interval = float(cfg.get("interval", 15))

    async def run(self) -> None:
        """Long-running task (optional)."""

    def push(self, data: dict) -> None:
        self.home.update_sensor(self.id, data)

    def simulate(self, data: dict) -> None:
        self.push(data)

    def info(self) -> dict:
        values = {k: v for k, v in self.home.values.get(self.id, {}).items() if not k.startswith("_")}
        return {"id": self.id, "name": self.name, "room": self.room, "kind": self.kind, "icon": self.icon,
                "values": values, "units": {k: UNITS.get(k, "") for k in values},
                "updated": self.home.values.get(self.id, {}).get("_ts")}


class DHT22Sensor(Sensor):
    kind, icon = "dht22", "🌡️"

    def __init__(self, cfg, home):
        super().__init__(cfg, home)
        self.dev = home.hw.dht22(cfg["pin"])

    async def run(self) -> None:
        while True:
            data = await asyncio.to_thread(self.dev.read)
            if data:
                self.push(data)
            else:
                log.debug("%s: read failed", self.id)
            await asyncio.sleep(self.interval)


class BinarySensor(Sensor):
    # kind -> (field name, icon, default pull, invert)
    TYPES = {
        "pir": ("motion", "🏃", "none", False),
        "mq2": ("gas", "🔥", "none", False),
        "door": ("open", "🚪", "up", True),   # reed switch closed (door shut) = pin LOW = active
        "leak": ("leak", "💧", "none", False),
        "button": ("pressed", "🔘", "up", False),
        "digital": ("active", "📟", "none", False),
    }

    def __init__(self, cfg, home):
        super().__init__(cfg, home)
        self.kind = cfg["type"]
        self.field, self.icon, pull, invert = self.TYPES[self.kind]
        self.invert = cfg.get("invert", invert)
        self.input = home.hw.input(cfg["pin"], self.kind, self._on_change,
                                   pull=cfg.get("pull", pull), active_low=cfg.get("active_low", False))
        if home.hw.mock:
            self.input.value = self.invert  # simulated sensors start in their "normal" state (door closed)

    def _value(self, raw: bool) -> bool:
        return (not raw) if self.invert else raw

    def _on_change(self, raw: bool) -> None:
        # Called from a GPIO thread -> hop onto the event loop
        self.home.threadsafe(self.push, {self.field: self._value(raw)})

    async def run(self) -> None:
        self.push({self.field: self._value(self.input.value)})

    def simulate(self, data: dict) -> None:
        if self.field in data and hasattr(self.input, "inject"):
            raw = (not data[self.field]) if self.invert else bool(data[self.field])
            self.input.value = raw
        self.push(data)


class MqttSensor(Sensor):
    """Values arrive from an ESP32 node on <base>/<node>/sensor as JSON (see mqtt_client.py)."""
    kind, icon = "mqtt", "📡"

    def __init__(self, cfg, home):
        super().__init__(cfg, home)
        self.node = cfg["node"]

    async def run(self) -> None:
        # In mock mode with no broker, pretend to be the ESP32 so the dashboard has data
        if not self.home.hw.mock:
            return
        state = {"temperature": random.uniform(25, 30), "humidity": random.uniform(40, 70),
                 "light": random.uniform(20, 80)}
        has_soil = any(getattr(d, "node", None) == self.node and d.type == "pump" for d in self.home.devices.values())
        if has_soil:
            state["soil"] = random.uniform(40, 60)
        while True:
            await asyncio.sleep(self.interval)
            if self.home.mqtt and self.home.mqtt.connected:
                continue
            pump_on = any(getattr(d, "node", None) == self.node and d.type == "pump" and d.on
                          for d in self.home.devices.values())
            state["temperature"] = min(38, max(18, state["temperature"] + random.uniform(-0.35, 0.4)))
            state["humidity"] = min(95, max(20, state["humidity"] + random.uniform(-1, 1)))
            if has_soil:  # slowly dries out, jumps up while the pump runs
                state["soil"] = min(95, max(10, state["soil"] + (8 if pump_on else random.uniform(-0.6, 0.4))))
            state["light"] = min(100, max(0, state["light"] + random.uniform(-3, 3)))
            self.push({k: round(v, 1) for k, v in state.items()})


class SystemSensor(Sensor):
    kind, icon = "system", "🖥️"

    async def run(self) -> None:
        try:
            import psutil
        except ImportError:
            psutil = None
        while True:
            data = {}
            if psutil:
                data["cpu"] = psutil.cpu_percent(interval=None)
                data["memory"] = psutil.virtual_memory().percent
                data["disk"] = psutil.disk_usage("/").percent
            temp = self._cpu_temp()
            data["cpu_temp"] = temp if temp is not None else round(random.uniform(42, 55), 1)
            self.push(data)
            await asyncio.sleep(self.interval)

    @staticmethod
    def _cpu_temp() -> float | None:
        try:
            return round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
        except (OSError, ValueError):
            return None


def build_sensors(home) -> None:
    for cfg in home.cfg["sensors"]:
        kind = cfg.get("type")
        if kind == "dht22":
            sensor = DHT22Sensor(cfg, home)
        elif kind in BinarySensor.TYPES:
            sensor = BinarySensor(cfg, home)
        elif kind == "mqtt":
            sensor = MqttSensor(cfg, home)
        elif kind == "system":
            sensor = SystemSensor(cfg, home)
        else:
            raise ValueError(f"Unknown sensor type '{kind}' for {cfg['id']}")
        home.sensors[sensor.id] = sensor
    log.info("Loaded %d sensors", len(home.sensors))
