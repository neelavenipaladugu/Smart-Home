"""The Home object: single source of truth for device/sensor state + a tiny event bus.

Every module (automations, dashboard websocket, database, Telegram, MQTT) subscribes to
events emitted here, so modules never need to know about each other.

Event shapes:
    {"type": "device", "id": ..., "on": bool, "source": ...}
    {"type": "sensor", "id": ..., "data": {...}, "changed": {...}}
    {"type": "alert",  "message": ..., "level": info|warning|danger, "photo": bool}
    {"type": "armed",  "armed": bool, "source": ...}
    {"type": "node",   "node": ..., "online": bool}
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from typing import Any, Callable

log = logging.getLogger("core")


class Home:
    def __init__(self, cfg: dict, hw, db):
        self.cfg = cfg
        self.hw = hw
        self.db = db
        self.name = cfg["hub"]["name"]
        self.devices: dict[str, Any] = {}
        self.sensors: dict[str, Any] = {}
        self.values: dict[str, dict] = {}
        self.nodes: dict[str, bool] = {}
        self.armed = False
        self.alerts: deque = deque(maxlen=50)
        self.mqtt = None
        self.camera = None
        self.automation = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.started_at = time.time()
        self._listeners: list[Callable] = []
        self._timers: dict[str, asyncio.TimerHandle] = {}

    # ------------------------------------------------------------ event bus
    def subscribe(self, fn: Callable) -> None:
        self._listeners.append(fn)

    def emit(self, event: dict) -> None:
        event.setdefault("ts", time.time())
        for fn in list(self._listeners):
            try:
                result = fn(event)
                if inspect.isawaitable(result):
                    self.spawn(result)
            except Exception:
                log.exception("Listener %s failed on %s", fn, event.get("type"))

    def spawn(self, coro) -> None:
        """Schedule a coroutine on the hub's event loop from any thread."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and (self.loop is None or running is self.loop):
            running.create_task(coro)
        elif self.loop and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        else:
            coro.close()

    def threadsafe(self, fn: Callable, *args) -> None:
        """Run fn(*args) on the event loop - used by GPIO/MQTT/camera threads."""
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(fn, *args)

    # ------------------------------------------------------------ devices
    async def set_device(self, device_id: str, on: bool | str, source: str = "user",
                         duration: float | None = None) -> dict:
        dev = self.devices.get(device_id)
        if dev is None:
            raise KeyError(f"Unknown device '{device_id}'")
        if on == "toggle":
            on = not dev.on
        on = bool(on)

        self._cancel_timer(device_id)
        if duration:
            self._timers[device_id] = self.loop.call_later(
                duration, lambda: self.spawn(self.set_device(device_id, not on, "timer"))
            )

        if dev.on != on:
            dev.apply(on)
            self._device_changed(dev, on, source)
        return dev.info()

    def device_reported(self, device_id: str, on: bool) -> None:
        """A remote node confirmed its relay state (does not send a command back)."""
        dev = self.devices.get(device_id)
        if dev and dev.on != on:
            self._device_changed(dev, on, "node")

    def _device_changed(self, dev, on: bool, source: str) -> None:
        dev.on = on
        dev.changed_at = time.time()
        log.info("%s -> %s (%s)", dev.id, "ON" if on else "OFF", source)
        self.emit({"type": "device", "id": dev.id, "on": on, "source": source})

    def _cancel_timer(self, device_id: str) -> None:
        handle = self._timers.pop(device_id, None)
        if handle:
            handle.cancel()

    async def all_off(self, source: str = "user") -> None:
        for dev_id in self.devices:
            await self.set_device(dev_id, False, source)

    # ------------------------------------------------------------ sensors
    def update_sensor(self, sensor_id: str, data: dict) -> None:
        current = self.values.setdefault(sensor_id, {})
        changed = {k: v for k, v in data.items() if current.get(k) != v}
        current.update(data)
        current["_ts"] = time.time()
        self.emit({"type": "sensor", "id": sensor_id, "data": dict(data), "changed": changed})

    def value(self, key: str):
        """Read 'sensor_id.field'."""
        sensor_id, _, field = key.partition(".")
        return self.values.get(sensor_id, {}).get(field)

    # ------------------------------------------------------------ nodes / security / alerts
    def node_status(self, node: str, online: bool) -> None:
        if self.nodes.get(node) != online:
            self.nodes[node] = online
            self.emit({"type": "node", "node": node, "online": online})
            if not online:
                self.alert(f"Room node '{node}' went offline", "warning", "mqtt")

    def set_armed(self, armed: bool, source: str = "user") -> None:
        if self.armed != armed:
            self.armed = armed
            self.emit({"type": "armed", "armed": armed, "source": source})
            self.alert(f"Security system {'ARMED' if armed else 'disarmed'} ({source})", "info", source)

    def alert(self, message: str, level: str = "info", source: str = "system", photo: bool = False) -> None:
        event = {"type": "alert", "message": message, "level": level, "source": source,
                 "photo": photo, "ts": time.time()}
        self.alerts.appendleft(event)
        log.warning("ALERT [%s] %s", level, message) if level != "info" else log.info("ALERT %s", message)
        self.emit(event)

    # ------------------------------------------------------------ snapshot for UI/API
    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "mock": self.hw.mock,
            "armed": self.armed,
            "uptime": int(time.time() - self.started_at),
            "mqtt_connected": bool(self.mqtt and self.mqtt.connected),
            "camera": bool(self.camera and self.camera.running),
            "nodes": self.nodes,
            "devices": [d.info() for d in self.devices.values()],
            "sensors": [s.info() for s in self.sensors.values()],
            "alerts": list(self.alerts)[:20],
        }
