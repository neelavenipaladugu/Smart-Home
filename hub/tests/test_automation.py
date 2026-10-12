"""Run with:  cd hub && python -m pytest -q"""
import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.automation import AutomationEngine, Rule, Schedule, in_time_window  # noqa: E402
from app.core import Home  # noqa: E402
from app.database import Database  # noqa: E402
from app.devices import build_devices  # noqa: E402
from app.hardware import Hardware  # noqa: E402
from app.voice import parse_command  # noqa: E402


def make_home(tmp_path, automations):
    cfg = {
        "hub": {"name": "Test", "mock_hardware": True},
        "mqtt": {"base_topic": "home"},
        "devices": [
            {"id": "fan", "name": "Living Room Fan", "pin": 1, "type": "fan", "watts": 60},
            {"id": "light", "name": "Kitchen Light", "pin": 2, "type": "light", "watts": 10},
            {"id": "siren", "name": "Siren", "pin": 3, "type": "alarm"},
        ],
        "sensors": [],
        "automations": automations,
        "schedules": [],
    }
    db = Database(tmp_path / "test.db")
    home = Home(cfg, Hardware(mock=True), db)
    build_devices(home)
    return home


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def test_threshold_with_hysteresis(tmp_path):
    async def run():
        home = make_home(tmp_path, [{
            "name": "hot", "when": {"sensor": "env.temperature", "above": 30, "hysteresis": 1},
            "then": [{"device": "fan", "action": "on"}], "otherwise": [{"device": "fan", "action": "off"}]}])
        home.loop = asyncio.get_running_loop()
        AutomationEngine(home)
        for temp, expected in [(29, False), (31, True), (29.5, True), (28.9, False), (30.5, True)]:
            home.update_sensor("env", {"temperature": temp})
            await settle()
            assert home.devices["fan"].on is expected, f"temp={temp}"
    asyncio.run(run())


def test_cooldown_and_alert(tmp_path):
    async def run():
        home = make_home(tmp_path, [{
            "name": "gas", "when": {"sensor": "gas.gas", "equals": True}, "cooldown": 60,
            "then": [{"device": "siren", "action": "on"}, {"alert": "Gas!", "level": "danger"}]}])
        home.loop = asyncio.get_running_loop()
        engine = AutomationEngine(home)
        for value in (True, False, True):
            home.update_sensor("gas", {"gas": value})
            await settle()
        assert engine.rules[0].fire_count == 1  # second trigger blocked by cooldown
        assert home.devices["siren"].on
        assert any(a["message"] == "Gas!" for a in home.alerts)
    asyncio.run(run())


def test_armed_only_rule(tmp_path):
    async def run():
        home = make_home(tmp_path, [{
            "name": "door", "when": {"sensor": "door.open", "equals": True}, "only_when_armed": True,
            "then": [{"device": "siren", "action": "on"}]}])
        home.loop = asyncio.get_running_loop()
        AutomationEngine(home)
        home.update_sensor("door", {"open": True})
        await settle()
        assert not home.devices["siren"].on
        home.update_sensor("door", {"open": False})
        home.armed = True
        home.update_sensor("door", {"open": True})
        await settle()
        assert home.devices["siren"].on
    asyncio.run(run())


def test_timed_action_turns_off(tmp_path):
    async def run():
        home = make_home(tmp_path, [])
        home.loop = asyncio.get_running_loop()
        await home.set_device("light", True, duration=0.05)
        assert home.devices["light"].on
        await asyncio.sleep(0.15)
        assert not home.devices["light"].on
    asyncio.run(run())


def test_energy_log(tmp_path):
    db = Database(tmp_path / "e.db")
    db._exec("INSERT INTO device_log VALUES (?,?,?,?)", (100, "fan", 1, "t"))
    db._exec("INSERT INTO device_log VALUES (?,?,?,?)", (400, "fan", 0, "t"))
    db._exec("INSERT INTO device_log VALUES (?,?,?,?)", (900, "fan", 1, "t"))
    assert db.on_seconds("fan", 0, 1000) == 300 + 100
    assert db.on_seconds("fan", 200, 1000) == 200 + 100  # started while already on


def test_time_window_across_midnight():
    assert in_time_window("18:00", "06:00", dt.time(23, 0))
    assert in_time_window("18:00", "06:00", dt.time(2, 0))
    assert not in_time_window("18:00", "06:00", dt.time(12, 0))
    assert in_time_window("08:00", "17:00", dt.time(12, 0))


def test_schedule_runs_once_per_minute():
    s = Schedule({"name": "x", "at": "07:30", "days": ["mon"], "then": []}, 0)
    monday = dt.datetime(2026, 10, 12, 7, 30, 5)
    assert s.due(monday)
    assert not s.due(monday.replace(second=40))
    assert not s.due(dt.datetime(2026, 10, 13, 7, 30))  # tuesday


def test_rule_bool_strings():
    r = Rule({"when": {"sensor": "a.b", "equals": True}, "then": []}, 0)
    assert r.evaluate("true") and r.evaluate(1) and not r.evaluate(False)


def test_voice_parser(tmp_path):
    home = make_home(tmp_path, [])
    assert parse_command("turn on the kitchen light", home.devices) == ("device", ("light", True))
    assert parse_command("switch off living room fan", home.devices) == ("device", ("fan", False))
    assert parse_command("turn everything off", home.devices) == ("all_off", None)
    assert parse_command("disarm", home.devices) == ("arm", False)
    assert parse_command("hello there", home.devices) is None
