"""SQLite storage: sensor history, device on/off log (for energy), alerts, RFID access, settings."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time

log = logging.getLogger("database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (ts REAL, sensor TEXT, field TEXT, value REAL);
CREATE INDEX IF NOT EXISTS idx_readings ON readings(sensor, field, ts);
CREATE TABLE IF NOT EXISTS device_log (ts REAL, device TEXT, on_state INTEGER, source TEXT);
CREATE INDEX IF NOT EXISTS idx_device_log ON device_log(device, ts);
CREATE TABLE IF NOT EXISTS events (ts REAL, level TEXT, source TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS access_log (ts REAL, card TEXT, person TEXT, granted INTEGER);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

NUMERIC_SAMPLE_SECONDS = 60  # store numeric readings at most once a minute per field


class Database:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()
        self._last_sample: dict[str, float] = {}
        log.info("Database: %s", path)

    def _exec(self, sql: str, params=()) -> None:
        with self.lock:
            self.conn.execute(sql, params)
            self.conn.commit()

    def _query(self, sql: str, params=()) -> list:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------ event listener
    def on_event(self, event: dict) -> None:
        ts = event.get("ts", time.time())
        kind = event["type"]
        if kind == "sensor":
            for field, value in event["data"].items():
                is_bool = isinstance(value, bool)
                if not isinstance(value, (int, float)):
                    continue
                key = f"{event['id']}.{field}"
                # booleans: store every change; numbers: sample once a minute
                if is_bool and field not in event.get("changed", {}):
                    continue
                if not is_bool and ts - self._last_sample.get(key, 0) < NUMERIC_SAMPLE_SECONDS:
                    continue
                self._last_sample[key] = ts
                self._exec("INSERT INTO readings VALUES (?,?,?,?)", (ts, event["id"], field, float(value)))
        elif kind == "device":
            self._exec("INSERT INTO device_log VALUES (?,?,?,?)", (ts, event["id"], int(event["on"]), event["source"]))
        elif kind == "alert":
            self._exec("INSERT INTO events VALUES (?,?,?,?)", (ts, event["level"], event["source"], event["message"]))

    # ------------------------------------------------------------ queries
    def history(self, sensor: str, field: str, hours: float = 24) -> list[dict]:
        since = time.time() - hours * 3600
        rows = self._query("SELECT ts, value FROM readings WHERE sensor=? AND field=? AND ts>=? ORDER BY ts",
                           (sensor, field, since))
        return [{"ts": ts, "value": v} for ts, v in rows]

    def history_keys(self) -> list[str]:
        return [f"{s}.{f}" for s, f in self._query("SELECT DISTINCT sensor, field FROM readings ORDER BY sensor, field")]

    def events(self, limit: int = 100) -> list[dict]:
        rows = self._query("SELECT ts, level, source, message FROM events ORDER BY ts DESC LIMIT ?", (limit,))
        return [{"ts": ts, "level": lv, "source": src, "message": msg} for ts, lv, src, msg in rows]

    def log_access(self, card: str, person: str | None, granted: bool) -> None:
        self._exec("INSERT INTO access_log VALUES (?,?,?,?)", (time.time(), card, person or "Unknown", int(granted)))

    def access_log(self, limit: int = 50) -> list[dict]:
        rows = self._query("SELECT ts, card, person, granted FROM access_log ORDER BY ts DESC LIMIT ?", (limit,))
        return [{"ts": ts, "card": c, "person": p, "granted": bool(g)} for ts, c, p, g in rows]

    def on_seconds(self, device: str, since: float, until: float) -> float:
        """Total seconds a device was ON between `since` and `until` (from the on/off log)."""
        rows = self._query("SELECT ts, on_state FROM device_log WHERE device=? AND ts>=? AND ts<? ORDER BY ts",
                           (device, since, until))
        prev = self._query("SELECT on_state FROM device_log WHERE device=? AND ts<? ORDER BY ts DESC LIMIT 1",
                           (device, since))
        state = bool(prev[0][0]) if prev else False
        total, last = 0.0, since
        for ts, on in rows:
            if state:
                total += ts - last
            state, last = bool(on), ts
        if state:
            total += until - last
        return total

    def mark_all_off(self, devices: list[str]) -> None:
        """After a restart every relay is OFF - close any open 'on' periods in the energy log."""
        now = time.time()
        for dev in devices:
            last = self._query("SELECT on_state FROM device_log WHERE device=? ORDER BY ts DESC LIMIT 1", (dev,))
            if last and last[0][0]:
                self._exec("INSERT INTO device_log VALUES (?,?,?,?)", (now, dev, 0, "restart"))

    # ------------------------------------------------------------ settings + maintenance
    def get_setting(self, key: str) -> str | None:
        rows = self._query("SELECT value FROM settings WHERE key=?", (key,))
        return rows[0][0] if rows else None

    def set_setting(self, key: str, value: str) -> None:
        self._exec("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))

    def cleanup(self, days: int) -> None:
        cutoff = time.time() - days * 86400
        for table in ("readings", "device_log", "events", "access_log"):
            self._exec(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))  # noqa: S608 - fixed table names
        log.info("Removed data older than %d days", days)

    def close(self) -> None:
        self.conn.close()
