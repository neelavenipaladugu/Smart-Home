"""MQTT bridge between the hub and ESP32 room nodes.

Topics (base = mqtt.base_topic, default "home"):
    <base>/<node>/sensor             node -> hub   JSON {"temperature": 24.1, "soil": 40, ...}
    <base>/<node>/status             node -> hub   "online" / "offline" (retained + LWT)
    <base>/<node>/relay/<ch>/set     hub  -> node  "ON" / "OFF"
    <base>/<node>/relay/<ch>/state   node -> hub   "ON" / "OFF"
    <base>/hub/device/<id>/set       anyone -> hub "ON" / "OFF" / "TOGGLE"
    <base>/hub/device/<id>/state     hub  -> anyone (retained)
    <base>/hub/status                hub  -> anyone "online" / "offline" (LWT)
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("mqtt")


class MqttBridge:
    def __init__(self, home):
        self.home = home
        cfg = home.cfg["mqtt"]
        self.cfg = cfg
        self.base = cfg["base_topic"]
        self.connected = False
        self.client = None

    def start(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.warning("paho-mqtt not installed - MQTT disabled")
            return

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pihome-hub")
        if self.cfg.get("username"):
            self.client.username_pw_set(self.cfg["username"], self.cfg.get("password") or None)
        self.client.will_set(f"{self.base}/hub/status", "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=2, max_delay=60)
        try:
            self.client.connect_async(self.cfg["host"], int(self.cfg["port"]), keepalive=30)
            self.client.loop_start()
            log.info("Connecting to MQTT broker %s:%s ...", self.cfg["host"], self.cfg["port"])
        except Exception as exc:  # noqa: BLE001
            log.warning("MQTT start failed: %s", exc)
        self.home.subscribe(self._on_home_event)

    def stop(self) -> None:
        if self.client:
            self.publish(f"{self.base}/hub/status", "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()

    def publish(self, topic: str, payload, retain: bool = False) -> None:
        if self.client and self.connected:
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload)
            self.client.publish(topic, payload, qos=1, retain=retain)

    # ------------------------------------------------------------ paho callbacks (network thread)
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            log.warning("MQTT connect refused: %s", reason_code)
            return
        self.connected = True
        log.info("MQTT connected")
        client.subscribe([(f"{self.base}/+/sensor", 1), (f"{self.base}/+/status", 1),
                          (f"{self.base}/+/relay/+/state", 1), (f"{self.base}/hub/device/+/set", 1)])
        client.publish(f"{self.base}/hub/status", "online", retain=True)
        # Re-send current states so nodes that rebooted get in sync
        for dev in self.home.devices.values():
            if dev.driver == "mqtt":
                client.publish(dev.set_topic, "ON" if dev.on else "OFF", qos=1)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        log.warning("MQTT disconnected (%s) - will retry", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", "replace").strip()
            parts = msg.topic.split("/")[1:]  # drop base
            self.home.threadsafe(self._handle, parts, payload)
        except Exception:
            log.exception("Bad MQTT message on %s", msg.topic)

    # ------------------------------------------------------------ handling (event loop)
    def _handle(self, parts: list[str], payload: str) -> None:
        if len(parts) == 4 and parts[0] == "hub" and parts[1] == "device" and parts[3] == "set":
            value = "toggle" if payload.upper() == "TOGGLE" else payload.upper() in ("ON", "1", "TRUE")
            self.home.spawn(self.home.set_device(parts[2], value, "mqtt"))
            return

        node = parts[0]
        if len(parts) == 2 and parts[1] == "status":
            self.home.node_status(node, payload.lower() == "online")
        elif len(parts) == 2 and parts[1] == "sensor":
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                log.warning("Node %s sent invalid JSON: %s", node, payload[:80])
                return
            self.home.node_status(node, True)
            for sensor in self.home.sensors.values():
                if getattr(sensor, "node", None) == node:
                    sensor.push(data)
        elif len(parts) == 4 and parts[1] == "relay" and parts[3] == "state":
            channel = int(parts[2])
            for dev in self.home.devices.values():
                if getattr(dev, "node", None) == node and dev.channel == channel:
                    self.home.device_reported(dev.id, payload.upper() == "ON")

    def _on_home_event(self, event: dict) -> None:
        if event["type"] == "device":
            self.publish(f"{self.base}/hub/device/{event['id']}/state", "ON" if event["on"] else "OFF", retain=True)
        elif event["type"] == "alert":
            self.publish(f"{self.base}/hub/alert", {"message": event["message"], "level": event["level"]})
