"""Rule engine: sensor-triggered automations + time schedules (both defined in config.yaml)."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

log = logging.getLogger("automation")

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_hm(text: str) -> dt.time:
    h, m = text.split(":")
    return dt.time(int(h), int(m))


def in_time_window(start: str, end: str, now: dt.time | None = None) -> bool:
    now = now or dt.datetime.now().time()
    a, b = _parse_hm(start), _parse_hm(end)
    return a <= now < b if a <= b else (now >= a or now < b)  # window may cross midnight


def _as_bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "on", "yes")
    return bool(v)


class Rule:
    def __init__(self, cfg: dict, index: int):
        self.index = index
        self.name = cfg.get("name", f"Rule {index + 1}")
        when = cfg.get("when") or {}
        self.key = when["sensor"]
        self.sensor_id, _, self.field = self.key.partition(".")
        self.above = when.get("above")
        self.below = when.get("below")
        self.has_equals = "equals" in when
        self.equals = when.get("equals")
        self.hysteresis = float(when.get("hysteresis", 0))
        self.then = cfg.get("then") or []
        self.otherwise = cfg.get("otherwise") or []
        self.cooldown = float(cfg.get("cooldown", 0))
        self.only_between = cfg.get("only_between")
        self.only_when_armed = bool(cfg.get("only_when_armed", False))
        self.enabled = bool(cfg.get("enabled", True))
        self.active = False
        self.last_fired = 0.0
        self.fire_count = 0

    def evaluate(self, value) -> bool | None:
        if value is None:
            return None
        if self.has_equals:
            if isinstance(self.equals, bool):
                return _as_bool(value) == self.equals
            return value == self.equals
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if self.above is not None:
            threshold = self.above - self.hysteresis if self.active else self.above
            return v > threshold
        if self.below is not None:
            threshold = self.below + self.hysteresis if self.active else self.below
            return v < threshold
        return None

    def describe(self) -> str:
        if self.has_equals:
            cond = f"{self.key} is {self.equals}"
        elif self.above is not None:
            cond = f"{self.key} > {self.above}"
        else:
            cond = f"{self.key} < {self.below}"
        parts = [describe_action(a) for a in self.then]
        text = f"When {cond} → {', '.join(parts)}"
        if self.only_between:
            text += f" (only {self.only_between[0]}–{self.only_between[1]})"
        if self.only_when_armed:
            text += " (only when armed)"
        return text

    def info(self) -> dict:
        return {"index": self.index, "name": self.name, "description": self.describe(), "enabled": self.enabled,
                "active": self.active, "last_fired": self.last_fired or None, "fire_count": self.fire_count}


def action_word(a: dict) -> str:
    """YAML turns bare on/off into True/False - normalise back to on/off/toggle."""
    value = a.get("action", "on")
    if isinstance(value, bool):
        return "on" if value else "off"
    value = str(value).lower()
    return value if value in ("on", "off", "toggle") else ("on" if value in ("true", "1") else "off")


def describe_action(a: dict) -> str:
    if "device" in a:
        text = f"{a['device']} {action_word(a)}"
        return text + (f" for {a['for']}s" if a.get("for") else "")
    if "notify" in a:
        return "notify"
    if "alert" in a:
        return f"{a.get('level', 'warning')} alert"
    if "snapshot" in a:
        return "camera snapshot"
    if "arm" in a:
        return "arm" if a["arm"] else "disarm"
    return "?"


class Schedule:
    def __init__(self, cfg: dict, index: int):
        self.index = index
        self.name = cfg.get("name", f"Schedule {index + 1}")
        self.at = cfg["at"]
        _parse_hm(self.at)  # validate early
        self.days = [d.lower()[:3] for d in cfg.get("days", DAYS)]
        self.then = cfg.get("then") or []
        self.enabled = bool(cfg.get("enabled", True))
        self.last_run_key = ""

    def due(self, now: dt.datetime) -> bool:
        key = now.strftime("%Y-%m-%d %H:%M")
        if (self.enabled and now.strftime("%H:%M") == _parse_hm(self.at).strftime("%H:%M")
                and DAYS[now.weekday()] in self.days and key != self.last_run_key):
            self.last_run_key = key
            return True
        return False

    def info(self) -> dict:
        return {"index": self.index, "name": self.name, "at": self.at, "days": self.days, "enabled": self.enabled,
                "description": f"At {self.at} → " + ", ".join(describe_action(a) for a in self.then)}


class AutomationEngine:
    def __init__(self, home):
        self.home = home
        self.rules = [Rule(c, i) for i, c in enumerate(home.cfg["automations"])]
        self.schedules = [Schedule(c, i) for i, c in enumerate(home.cfg["schedules"])]
        for rule in self.rules:
            saved = home.db.get_setting(f"rule:{rule.name}") if home.db else None
            if saved is not None:
                rule.enabled = saved == "1"
        home.subscribe(self.on_event)
        log.info("Loaded %d automations and %d schedules", len(self.rules), len(self.schedules))

    # ------------------------------------------------------------ sensor triggers
    def on_event(self, event: dict) -> None:
        if event["type"] != "sensor":
            return
        for rule in self.rules:
            if rule.sensor_id == event["id"] and rule.field in event["data"]:
                self.check(rule)

    def check(self, rule: Rule) -> None:
        if not rule.enabled:
            return
        result = rule.evaluate(self.home.value(rule.key))
        if result is None or result == rule.active:
            return
        rule.active = result
        value = self.home.value(rule.key)

        if not result:
            if rule.otherwise:
                self.home.spawn(self.run_actions(rule.otherwise, rule.name, value))
            return

        if rule.only_between and not in_time_window(*rule.only_between):
            return
        if rule.only_when_armed and not self.home.armed:
            return
        if rule.cooldown and time.time() - rule.last_fired < rule.cooldown:
            log.debug("Rule '%s' in cooldown", rule.name)
            return
        rule.last_fired = time.time()
        rule.fire_count += 1
        log.info("Rule fired: %s (value=%s)", rule.name, value)
        self.home.spawn(self.run_actions(rule.then, rule.name, value))

    def set_enabled(self, index: int, enabled: bool) -> dict:
        rule = self.rules[index]
        rule.enabled = enabled
        if not enabled:
            rule.active = False
        if self.home.db:
            self.home.db.set_setting(f"rule:{rule.name}", "1" if enabled else "0")
        return rule.info()

    # ------------------------------------------------------------ actions
    async def run_actions(self, actions: list[dict], source: str, value=None) -> None:
        def fmt(text: str) -> str:
            try:
                return text.format(value=value, rule=source)
            except (KeyError, IndexError, ValueError):
                return text

        for a in actions:
            try:
                if "device" in a:
                    action = action_word(a)
                    on = "toggle" if action == "toggle" else action == "on"
                    await self.home.set_device(a["device"], on, f"rule:{source}", a.get("for"))
                elif "notify" in a:
                    self.home.alert(fmt(a["notify"]), "info", f"rule:{source}")
                elif "alert" in a:
                    self.home.alert(fmt(a["alert"]), a.get("level", "warning"), f"rule:{source}")
                elif "snapshot" in a:
                    self.home.alert(f"📷 Camera snapshot ({source})", "info", f"rule:{source}", photo=True)
                elif "arm" in a:
                    self.home.set_armed(bool(a["arm"]), f"rule:{source}")
            except Exception:
                log.exception("Action %s in '%s' failed", a, source)

    # ------------------------------------------------------------ schedules
    async def run_schedules(self) -> None:
        while True:
            now = dt.datetime.now()
            for sched in self.schedules:
                if sched.due(now):
                    log.info("Schedule: %s", sched.name)
                    await self.run_actions(sched.then, f"schedule:{sched.name}")
            await asyncio.sleep(10)

    def info(self) -> dict:
        return {"rules": [r.info() for r in self.rules], "schedules": [s.info() for s in self.schedules]}
