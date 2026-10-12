# Architecture

```
                         ┌──────────────────────── Raspberry Pi 4 ─────────────────────────┐
  Phone / PC browser ───►│  FastAPI  (api.py)  ──  REST /api/*  +  WebSocket /ws            │
  Telegram (anywhere) ──►│  TelegramBot (notifier.py)                                       │
                         │                     │                                            │
                         │              ┌──────▼──────┐   events   ┌─────────────────────┐   │
                         │              │    Home     │──────────► │ AutomationEngine    │   │
                         │              │  (core.py)  │            │ Database (SQLite)   │   │
                         │              │ state + bus │◄────────── │ WebSocketHub        │   │
                         │              └──────┬──────┘  commands  │ MqttBridge          │   │
                         │                     │                   └─────────────────────┘   │
                         │   Devices (relays)  │  Sensors  Camera  RFID  Voice              │
                         │   hardware.py ──► gpiozero / DHT / OpenCV / RC522 / Vosk         │
                         └─────────────────────┬────────────────────────────────────────────┘
                                               │ MQTT (Mosquitto on the Pi)
                       ┌───────────────────────┼───────────────────────┐
                 ESP32 "bedroom"         ESP32 "garden"           ESP32 "kitchen" …
                 DHT22, PIR, relays      soil, LDR, pump relay
```

## Design ideas

- **One state object (`Home`)** owns all device and sensor state. Modules never call each other directly. They
  subscribe to events (`device`, `sensor`, `alert`, `armed`, `node`). Adding a feature usually means adding one new
  listener.
- **Hardware abstraction (`hardware.py`)**: every GPIO, DHT, or camera access goes through a small interface that has a
  *mock* twin. That lets the whole system run and be tested on a laptop.
- **Thread → event loop**: GPIO callbacks, MQTT, and the camera run in their own threads. They hand work to the asyncio
  loop with `home.threadsafe()` / `home.spawn()`, so state only ever changes on one thread.
- **Configuration over code**: devices, sensors, rules, and schedules all live in `config.yaml`.

## Module map

| File | Responsibility |
|---|---|
| `hub/main.py` | Starts everything, FastAPI app, CLI flags |
| `app/config.py` | Loads and validates `config.yaml`, env-var secrets |
| `app/core.py` | `Home`: state, event bus, timers, alerts, arm/disarm |
| `app/hardware.py` | Real and mock GPIO outputs, inputs, DHT22 |
| `app/devices.py` | GPIO relays and MQTT relays |
| `app/sensors.py` | DHT22, PIR/gas/door/leak, MQTT node sensors, Pi system stats |
| `app/automation.py` | Rules (thresholds, hysteresis, time windows, cooldown) and schedules |
| `app/mqtt_client.py` | Bridge to the ESP32 nodes and outside integrations |
| `app/database.py` | SQLite history, energy log, events, access log, settings |
| `app/notifier.py` | Telegram alerts, photos, remote commands |
| `app/camera.py` | MJPEG stream, motion detection, snapshots, face recognition |
| `app/rfid.py` | RC522 card access → unlock door |
| `app/voice.py` | Offline Vosk speech → commands (also used by the text command box) |
| `app/api.py` | REST endpoints and WebSocket |
| `static/` | Dashboard (HTML/CSS/JS, installable as a phone app) |

## MQTT topics

| Topic | Direction | Payload |
|---|---|---|
| `home/<node>/sensor` | node → hub | JSON, e.g. `{"temperature":24.5,"humidity":55,"soil":40}` |
| `home/<node>/status` | node → hub | `online` / `offline` (retained, LWT) |
| `home/<node>/relay/<ch>/set` | hub → node | `ON` / `OFF` / `TOGGLE` |
| `home/<node>/relay/<ch>/state` | node → hub | `ON` / `OFF` |
| `home/hub/device/<id>/set` | anyone → hub | `ON` / `OFF` / `TOGGLE` |
| `home/hub/device/<id>/state` | hub → anyone | `ON` / `OFF` (retained) |
| `home/hub/alert` | hub → anyone | `{"message": "...", "level": "danger"}` |
| `home/hub/status` | hub → anyone | `online` / `offline` |

Test from the Pi:
```bash
mosquitto_sub -v -t 'home/#'
mosquitto_pub -t home/hub/device/living_light/set -m ON
mosquitto_pub -t home/bedroom/sensor -m '{"temperature": 31}'
```

## REST API (full interactive docs at `http://<pi>:8000/docs`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | Everything (devices, sensors, alerts, armed) |
| POST | `/api/devices/{id}` | `{"on": true}`, `{"action": "toggle"}`, optional `"duration": 60` |
| POST | `/api/all-off` | Switch every device off |
| POST | `/api/security` | `{"armed": true}` |
| GET | `/api/history?key=living_env.temperature&hours=24` | Sensor history |
| GET | `/api/energy` | Live watts, today's kWh, 7-day chart, per-device usage |
| GET/POST | `/api/automations[/{index}]` | List rules, or enable/disable one |
| POST | `/api/voice` | `{"text": "turn on kitchen light"}` |
| POST | `/api/rfid/scan` | `{"card": "123…"}` (simulate a card) |
| GET | `/api/camera/stream`, `/api/camera/snapshot` | MJPEG stream, JPEG still |
| POST | `/api/sensors/{id}/simulate` | Mock mode only: inject a reading |
| WS | `/ws` | Live event stream |

If `hub.api_key` is set, send it as the `X-API-Key` header or a `?key=` query parameter.

## Extending

- **New sensor type:** subclass `Sensor` in `sensors.py`, call `self.push({...})`, and register it in `build_sensors`.
- **New device driver** (e.g. IR blaster, Tasmota/Shelly plug): subclass `Device`, implement `apply(on)`.
- **New action type:** add a branch in `AutomationEngine.run_actions`.
- **New integration** (e.g. Home Assistant, Google Sheets): `home.subscribe(my_listener)`.
