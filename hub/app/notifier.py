"""Telegram bot: push alerts (with camera photos) and control the house from anywhere.

Commands: /status  /devices  /on <id>  /off <id>  /arm  /disarm  /photo  /alloff  /help
Only messages from the configured chat_id are accepted.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger("telegram")

HELP = (
    "🏠 *PiHome commands*\n"
    "/status – sensor readings\n"
    "/devices – list devices\n"
    "/on <id> – switch on\n"
    "/off <id> – switch off\n"
    "/alloff – everything off\n"
    "/arm, /disarm – security mode\n"
    "/photo – camera snapshot"
)
LEVEL_ICON = {"info": "ℹ️", "warning": "⚠️", "danger": "🚨"}


class TelegramBot:
    def __init__(self, home):
        self.home = home
        cfg = home.cfg["telegram"]
        self.enabled = bool(cfg.get("enabled") and cfg.get("token") and cfg.get("chat_id"))
        self.chat_id = str(cfg.get("chat_id"))
        self.api = f"https://api.telegram.org/bot{cfg.get('token')}"
        self.client = httpx.AsyncClient(timeout=40)
        if self.enabled:
            home.subscribe(self.on_event)
        else:
            log.info("Telegram disabled (set telegram.enabled, token and chat_id to use it)")

    # ------------------------------------------------------------ outgoing
    async def send(self, text: str) -> None:
        try:
            await self.client.post(f"{self.api}/sendMessage",
                                   data={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"})
        except httpx.HTTPError as exc:
            log.warning("Telegram send failed: %s", exc)

    async def send_photo(self, jpeg: bytes, caption: str) -> None:
        try:
            await self.client.post(f"{self.api}/sendPhoto", data={"chat_id": self.chat_id, "caption": caption},
                                   files={"photo": ("snapshot.jpg", jpeg, "image/jpeg")})
        except httpx.HTTPError as exc:
            log.warning("Telegram photo failed: %s", exc)

    async def on_event(self, event: dict) -> None:
        if event["type"] != "alert":
            return
        text = f"{LEVEL_ICON.get(event['level'], '')} {event['message']}"
        jpeg = self.home.camera.latest_jpeg() if (event.get("photo") and self.home.camera) else None
        if jpeg:
            await self.send_photo(jpeg, text)
        else:
            await self.send(text)

    # ------------------------------------------------------------ incoming commands
    async def poll(self) -> None:
        if not self.enabled:
            return
        offset = 0
        await self.send(f"✅ *{self.home.name}* hub is online. Send /help")
        while True:
            try:
                r = await self.client.get(f"{self.api}/getUpdates", params={"offset": offset, "timeout": 30})
                for upd in r.json().get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    if str(msg.get("chat", {}).get("id")) != self.chat_id:
                        continue  # ignore strangers
                    await self.handle(msg.get("text", ""))
            except (httpx.HTTPError, ValueError) as exc:
                log.debug("Telegram poll error: %s", exc)
                await asyncio.sleep(5)

    async def handle(self, text: str) -> None:
        parts = text.strip().split()
        if not parts:
            return
        cmd, args = parts[0].lower().split("@")[0], parts[1:]
        home = self.home

        if cmd in ("/start", "/help"):
            await self.send(HELP)
        elif cmd == "/status":
            lines = [f"🏠 *{home.name}* – {'🔒 ARMED' if home.armed else '🔓 disarmed'}"]
            for s in home.sensors.values():
                vals = ", ".join(f"{k}: {v}" for k, v in s.info()["values"].items())
                if vals:
                    lines.append(f"{s.icon} {s.name}: {vals}")
            await self.send("\n".join(lines))
        elif cmd == "/devices":
            await self.send("\n".join(f"{'🟢' if d.on else '⚫'} `{d.id}` – {d.name}" for d in home.devices.values()))
        elif cmd in ("/on", "/off") and args:
            try:
                await home.set_device(args[0], cmd == "/on", "telegram")
                await self.send(f"✅ {args[0]} {'ON' if cmd == '/on' else 'OFF'}")
            except KeyError:
                await self.send(f"❌ Unknown device `{args[0]}` – see /devices")
        elif cmd == "/alloff":
            await home.all_off("telegram")
            await self.send("✅ All devices off")
        elif cmd in ("/arm", "/disarm"):
            home.set_armed(cmd == "/arm", "telegram")
        elif cmd == "/photo":
            jpeg = home.camera.latest_jpeg() if home.camera else None
            if jpeg:
                await self.send_photo(jpeg, time.strftime("📷 %H:%M:%S"))
            else:
                await self.send("❌ Camera not available")
        else:
            await self.send("Unknown command. Send /help")

    async def close(self) -> None:
        await self.client.aclose()
