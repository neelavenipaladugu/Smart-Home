"""Load and validate config.yaml (secrets can be overridden with environment variables)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    pass


def load_config(path: str | os.PathLike | None = None) -> dict:
    path = Path(path or os.environ.get("PIHOME_CONFIG", BASE_DIR / "config.yaml"))
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    hub = cfg.setdefault("hub", {})
    hub.setdefault("name", "PiHome")
    hub.setdefault("host", "0.0.0.0")
    hub.setdefault("port", 8000)
    hub.setdefault("mock_hardware", "auto")
    hub.setdefault("db_path", "data/smarthome.db")
    hub.setdefault("history_days", 30)
    hub["api_key"] = os.environ.get("PIHOME_API_KEY", hub.get("api_key") or "")
    if os.environ.get("PIHOME_MOCK"):
        hub["mock_hardware"] = os.environ["PIHOME_MOCK"].lower() in ("1", "true", "yes")

    mqtt = cfg.setdefault("mqtt", {})
    mqtt.setdefault("enabled", True)
    mqtt.setdefault("host", "localhost")
    mqtt.setdefault("port", 1883)
    mqtt.setdefault("base_topic", "home")

    tg = cfg.setdefault("telegram", {})
    tg["token"] = os.environ.get("TELEGRAM_TOKEN", tg.get("token") or "")
    tg["chat_id"] = str(os.environ.get("TELEGRAM_CHAT_ID", tg.get("chat_id") or ""))
    tg.setdefault("enabled", False)

    for key in ("devices", "sensors", "automations", "schedules"):
        cfg[key] = cfg.get(key) or []
    for key in ("camera", "rfid", "voice"):
        cfg[key] = cfg.get(key) or {"enabled": False}

    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    ids = set()
    for item in cfg["devices"] + cfg["sensors"]:
        if "id" not in item:
            raise ConfigError(f"Every device/sensor needs an 'id': {item}")
        if item["id"] in ids:
            raise ConfigError(f"Duplicate id: {item['id']}")
        ids.add(item["id"])

    device_ids = {d["id"] for d in cfg["devices"]}
    for d in cfg["devices"]:
        driver = d.get("driver", "gpio")
        if driver == "gpio" and "pin" not in d:
            raise ConfigError(f"GPIO device '{d['id']}' needs a 'pin'")
        if driver == "mqtt" and ("node" not in d or "channel" not in d):
            raise ConfigError(f"MQTT device '{d['id']}' needs 'node' and 'channel'")

    for rule in cfg["automations"] + cfg["schedules"]:
        for action in (rule.get("then") or []) + (rule.get("otherwise") or []):
            if "device" in action and action["device"] not in device_ids:
                raise ConfigError(f"Rule '{rule.get('name')}' uses unknown device '{action['device']}'")


def resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else BASE_DIR / path
