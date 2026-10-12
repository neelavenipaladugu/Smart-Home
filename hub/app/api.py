"""REST API + WebSocket for the dashboard."""
from __future__ import annotations

import datetime as dt
import json
import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

log = logging.getLogger("api")


class DeviceCommand(BaseModel):
    on: bool | None = None
    action: str | None = None  # "toggle"
    duration: float | None = None


class ArmCommand(BaseModel):
    armed: bool


class EnableCommand(BaseModel):
    enabled: bool


class CardScan(BaseModel):
    card: str


class VoiceText(BaseModel):
    text: str


class WebSocketHub:
    """Pushes every home event to all connected dashboards."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def on_event(self, event: dict) -> None:
        if not self.clients:
            return
        msg = json.dumps(event, default=str)
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:  # noqa: BLE001 - client went away
                self.clients.discard(ws)


def build_router(ctx) -> APIRouter:
    """ctx: object with .home .automation .rfid .voice .ws_hub"""
    home = ctx.home
    api_key = home.cfg["hub"].get("api_key") or ""

    def check_key(request: Request):
        if not api_key:
            return
        given = request.headers.get("x-api-key") or request.query_params.get("key") or ""
        if not secrets.compare_digest(given, api_key):
            raise HTTPException(401, "Invalid API key")

    r = APIRouter(prefix="/api", dependencies=[Depends(check_key)])

    # ------------------------------------------------------------ state
    @r.get("/state")
    def state():
        return home.snapshot()

    @r.post("/devices/{device_id}")
    async def set_device(device_id: str, cmd: DeviceCommand):
        value = "toggle" if cmd.action == "toggle" or cmd.on is None else cmd.on
        try:
            return await home.set_device(device_id, value, "dashboard", cmd.duration)
        except KeyError:
            raise HTTPException(404, f"Unknown device {device_id}") from None

    @r.post("/all-off")
    async def all_off():
        await home.all_off("dashboard")
        return {"ok": True}

    @r.post("/security")
    async def security(cmd: ArmCommand):
        home.set_armed(cmd.armed, "dashboard")
        if home.db:
            home.db.set_setting("armed", "1" if cmd.armed else "0")
        return {"armed": home.armed}

    # ------------------------------------------------------------ history / energy / events
    @r.get("/history")
    def history(key: str, hours: float = 24):
        sensor, _, field = key.partition(".")
        return {"key": key, "points": home.db.history(sensor, field, min(hours, 24 * 31))}

    @r.get("/history/keys")
    def history_keys():
        return home.db.history_keys()

    @r.get("/events")
    def events(limit: int = 100):
        return home.db.events(min(limit, 500))

    @r.get("/energy")
    def energy():
        now = time.time()
        midnight = dt.datetime.combine(dt.date.today(), dt.time()).timestamp()
        devices, total_today = [], 0.0
        for d in home.devices.values():
            secs = home.db.on_seconds(d.id, midnight, now)
            kwh = d.watts * secs / 3_600_000
            total_today += kwh
            devices.append({"id": d.id, "name": d.name, "type": d.type, "watts": d.watts, "on_hours": round(secs / 3600, 2),
                            "kwh": round(kwh, 3), "on": d.on})
        week = []
        for i in range(6, -1, -1):
            day = dt.date.today() - dt.timedelta(days=i)
            start = dt.datetime.combine(day, dt.time()).timestamp()
            end = min(start + 86400, now)
            kwh = sum(d.watts * home.db.on_seconds(d.id, start, end) / 3_600_000
                      for d in home.devices.values())
            week.append({"day": day.strftime("%a"), "kwh": round(kwh, 3)})
        live_watts = sum(d.watts for d in home.devices.values() if d.on)
        return {"live_watts": live_watts, "today_kwh": round(total_today, 3),
                "devices": sorted(devices, key=lambda x: -x["kwh"]), "week": week}

    # ------------------------------------------------------------ automations
    @r.get("/automations")
    def automations():
        return ctx.automation.info()

    @r.post("/automations/{index}")
    async def toggle_rule(index: int, cmd: EnableCommand):
        if not 0 <= index < len(ctx.automation.rules):
            raise HTTPException(404, "No such rule")
        return ctx.automation.set_enabled(index, cmd.enabled)

    # ------------------------------------------------------------ access / voice / simulation
    @r.get("/access")
    def access():
        return home.db.access_log()

    @r.post("/rfid/scan")
    async def rfid_scan(scan: CardScan):
        if not ctx.rfid:
            raise HTTPException(400, "RFID disabled")
        return ctx.rfid.scan(scan.card)

    @r.post("/voice")
    async def voice(cmd: VoiceText):
        """Send a text command as if it were spoken (also handy from phone shortcuts)."""
        from .voice import parse_command

        parsed = parse_command(cmd.text, home.devices)
        if not parsed:
            raise HTTPException(400, "Did not understand the command")
        kind, arg = parsed
        if kind == "device":
            home.spawn(home.set_device(arg[0], arg[1], "voice"))
        elif kind == "all_off":
            home.spawn(home.all_off("voice"))
        elif kind == "arm":
            home.set_armed(arg, "voice")
        return {"understood": kind, "arg": arg}

    @r.post("/sensors/{sensor_id}/simulate")
    async def simulate(sensor_id: str, data: dict):
        """Mock mode only: inject a sensor reading to test automations."""
        if not home.hw.mock:
            raise HTTPException(403, "Simulation is only available in mock mode")
        sensor = home.sensors.get(sensor_id)
        if not sensor:
            raise HTTPException(404, "Unknown sensor")
        sensor.simulate(data)
        return {"ok": True}

    # ------------------------------------------------------------ camera
    @r.get("/camera/snapshot")
    def snapshot():
        jpeg = home.camera.latest_jpeg() if home.camera else None
        if not jpeg:
            raise HTTPException(404, "Camera not available")
        return Response(jpeg, media_type="image/jpeg")

    @r.get("/camera/stream")
    def stream():
        if not (home.camera and home.camera.running):
            raise HTTPException(404, "Camera not available")
        return StreamingResponse(home.camera.mjpeg(), media_type="multipart/x-mixed-replace; boundary=frame")

    return r


def add_websocket(app, ctx) -> None:
    api_key = ctx.home.cfg["hub"].get("api_key") or ""

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        if api_key and not secrets.compare_digest(ws.query_params.get("key", ""), api_key):
            await ws.close(code=4401)
            return
        await ws.accept()
        ctx.ws_hub.clients.add(ws)
        try:
            await ws.send_text(json.dumps({"type": "snapshot", "state": ctx.home.snapshot()}, default=str))
            while True:
                await ws.receive_text()  # keep-alive pings from the browser
        except WebSocketDisconnect:
            pass
        finally:
            ctx.ws_hub.clients.discard(ws)
