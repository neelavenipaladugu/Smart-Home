"""PiHome hub - entry point.

    python main.py                 # real GPIO on a Pi, simulated on a PC
    python main.py --mock          # force simulation
    python main.py --config my.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import WebSocketHub, add_websocket, build_router
from app.automation import AutomationEngine
from app.camera import Camera
from app.config import BASE_DIR, load_config, resolve_path
from app.core import Home
from app.database import Database
from app.devices import build_devices
from app.hardware import Hardware, decide_mock
from app.mqtt_client import MqttBridge
from app.notifier import TelegramBot
from app.rfid import RfidAccess
from app.sensors import build_sensors
from app.voice import VoiceControl

log = logging.getLogger("pihome")
STATIC_DIR = BASE_DIR / "static"


class Context:
    """Holds every module so the API can reach them."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.hw = Hardware(decide_mock(cfg["hub"]["mock_hardware"]))
        self.db = Database(resolve_path(cfg["hub"]["db_path"]))
        self.home = Home(cfg, self.hw, self.db)
        build_devices(self.home)
        build_sensors(self.home)
        self.db.mark_all_off(list(self.home.devices))  # relays start OFF after a restart
        self.automation = AutomationEngine(self.home)
        self.home.automation = self.automation
        self.ws_hub = WebSocketHub()
        self.telegram = TelegramBot(self.home)
        self.rfid = RfidAccess(self.home) if cfg["rfid"].get("enabled") else None
        self.voice = VoiceControl(self.home) if cfg["voice"].get("enabled") else None
        self.tasks: list[asyncio.Task] = []

        self.home.subscribe(self.db.on_event)
        self.home.subscribe(self.ws_hub.on_event)
        self.home.armed = self.db.get_setting("armed") == "1"

    async def start(self) -> None:
        home = self.home
        home.loop = asyncio.get_running_loop()

        if self.cfg["mqtt"].get("enabled"):
            home.mqtt = MqttBridge(home)
            home.mqtt.start()
        if self.cfg["camera"].get("enabled"):
            home.camera = Camera(home)
            home.camera.start()
        if self.rfid:
            self.rfid.start()
        if self.voice:
            self.voice.start()

        for sensor in home.sensors.values():
            self.tasks.append(asyncio.create_task(sensor.run(), name=f"sensor:{sensor.id}"))
        self.tasks.append(asyncio.create_task(self.automation.run_schedules(), name="schedules"))
        self.tasks.append(asyncio.create_task(self.telegram.poll(), name="telegram"))
        self.tasks.append(asyncio.create_task(self._maintenance(), name="maintenance"))
        home.alert(f"{home.name} hub started ({'simulation' if self.hw.mock else 'hardware'} mode)", "info", "hub")

    async def _maintenance(self) -> None:
        while True:
            await asyncio.to_thread(self.db.cleanup, int(self.cfg["hub"]["history_days"]))
            await asyncio.sleep(24 * 3600)

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        if self.home.mqtt:
            self.home.mqtt.stop()
        if self.home.camera:
            self.home.camera.stop()
        if self.rfid:
            self.rfid.stop()
        await self.telegram.close()
        self.hw.close()
        self.db.close()


def create_app(cfg: dict) -> FastAPI:
    ctx = Context(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await ctx.start()
        yield
        await ctx.stop()

    app = FastAPI(title="PiHome Smart Home Hub", version="1.0.0", lifespan=lifespan)
    app.state.ctx = ctx
    app.include_router(build_router(ctx))
    add_websocket(app, ctx)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="PiHome smart home hub")
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--mock", action="store_true", help="simulate all hardware")
    parser.add_argument("--port", type=int)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)-10s %(message)s", datefmt="%H:%M:%S")
    if args.mock:
        os.environ["PIHOME_MOCK"] = "1"
    os.chdir(BASE_DIR)
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    cfg = load_config(args.config)
    port = args.port or int(cfg["hub"]["port"])
    log.info("Dashboard: http://localhost:%d  (API docs: /docs)", port)
    uvicorn.run(create_app(cfg), host=cfg["hub"]["host"], port=port, log_level="warning")


if __name__ == "__main__":
    main()
