# Wiring guide and parts list

> ⚠️ **Mains electricity (230 V / 110 V) can kill.** Build and test everything with LEDs or 5–12 V loads first.
> Put relay boards that switch mains inside a closed, insulated enclosure, use fuses, and ask a qualified
> electrician to do the mains side. Never work on a live circuit.

## Parts list

| Qty | Part | Used for | Approx. cost |
|---|---|---|---|
| 1 | Raspberry Pi 4 (4 GB+) with official 5.1 V 3 A power supply | Hub | ₹5,500 / $55 |
| 1 | 32 GB microSD (A1/A2) | OS | ₹400 / $8 |
| 1 | Pi case with fan / heatsink | Cooling | ₹500 / $8 |
| 1 | 8-channel 5 V relay module, opto-isolated | Lights, fan, siren, lock | ₹450 / $8 |
| 2–4 | ESP32 DevKit V1 | Wireless room nodes | ₹400 / $6 each |
| 2–4 | 4-channel relay module | Relays on the room nodes | ₹250 / $4 each |
| 3+ | DHT22 (AM2302) | Temperature + humidity | ₹250 / $4 each |
| 2 | HC-SR501 PIR | Motion | ₹80 / $2 each |
| 1 | MQ-2 gas/smoke module | Gas alarm | ₹150 / $3 |
| 1 | Water-leak sensor module | Leak alarm | ₹60 / $2 |
| 2 | MC-38 magnetic reed switch | Door / window | ₹80 / $2 each |
| 1 | Capacitive soil moisture sensor v1.2 | Garden | ₹120 / $3 |
| 1 | RC522 RFID kit (reader + cards) | Door access | ₹150 / $3 |
| 1 | 12 V solenoid lock + 12 V 2 A adapter | Smart door | ₹600 / $10 |
| 1 | 12 V siren or 5 V active buzzer | Alarm | ₹150 / $3 |
| 1 | USB webcam **or** Pi Camera Module 3 | Security camera | ₹1,500 / $25 |
| 1 | USB microphone (optional) | Voice control | ₹400 / $6 |
| – | Breadboard, jumper wires, 10 kΩ resistors, logic level shifter | Prototyping | ₹400 / $6 |

**Total:** about ₹12,000 / $150 for the full build. You can start with just a Pi, a relay board, and a DHT22.

## Raspberry Pi 4 pin map (matches `hub/config.yaml`)

All numbers are **BCM GPIO** numbers. Physical pin numbers are in brackets.

| Function | GPIO (BCM) | Physical pin | Notes |
|---|---|---|---|
| DHT22 data (living room) | GPIO4 | 7 | Add a 10 kΩ pull-up from DATA to 3.3 V (modules often have one built in) |
| Relay IN1: living room light | GPIO17 | 11 | |
| Relay IN2: living room fan | GPIO27 | 13 | |
| Relay IN3: kitchen light | GPIO22 | 15 | |
| Relay IN4: door lock | GPIO16 | 36 | Switches the 12 V solenoid supply |
| Siren / buzzer (via transistor or relay) | GPIO24 | 18 | `active_high: true` |
| PIR OUT (living room) | GPIO23 | 16 | HC-SR501 outputs 3.3 V, so it's safe to connect directly |
| MQ-2 DO (kitchen) | GPIO5 | 29 | ⚠️ Power the MQ-2 from 5 V, so put a divider (10 k + 20 k) on DO |
| Front door reed switch | GPIO6 | 31 | Wire between the pin and GND (internal pull-up is used) |
| Water leak DO | GPIO13 | 33 | |
| RC522 SDA (SS) | GPIO8 (CE0) | 24 | SPI |
| RC522 SCK | GPIO11 | 23 | SPI |
| RC522 MOSI | GPIO10 | 19 | SPI |
| RC522 MISO | GPIO9 | 21 | SPI |
| RC522 RST | GPIO25 | 22 | Default RST pin of the `mfrc522` library |
| RC522 3.3 V / GND | 3.3 V / GND | 1 / 6 | ⚠️ **3.3 V only.** 5 V destroys the RC522 |
| Power for sensors | 5 V / 3.3 V / GND | 2,4 / 1,17 / 6,9,14,20,25,30,34,39 | |

### Relay board

```
Pi 5V  ──── VCC   (relay board)
Pi GND ──── GND
GPIO17 ──── IN1        COM ─── live wire from the supply
GPIO27 ──── IN2        NO  ─── to the lamp (ON when the relay is energised)
GPIO22 ──── IN3        NC  ─── (unused)
GPIO16 ──── IN4
```

Most boards are **active-low**: the relay turns on when the pin goes LOW. That's why `active_high: false` is set in the config.
For full isolation, remove the JD-VCC jumper and power JD-VCC from a separate 5 V supply.

### Door reed switch

```
GPIO6 ───[ reed switch ]─── GND
```
Door closed (magnet near) → switch closed → pin LOW → the hub reports `open: false`.

### Solenoid lock (12 V)

```
12V+ ── relay COM ; relay NO ── lock (+) ; lock (–) ── 12V–
Put a flyback diode (1N4007) across the lock: stripe to +.
```

### Camera
- **USB webcam:** plug it in and set `camera.source: usb`.
- **Pi Camera Module:** connect the ribbon cable (blue side facing the Ethernet port) and set `camera.source: picamera`.

## ESP32 room node (`firmware/esp32_room_node`)

| Function | ESP32 GPIO | Notes |
|---|---|---|
| Relay CH1 | 26 | |
| Relay CH2 | 27 | |
| Relay CH3 | 32 | |
| Relay CH4 | 33 | |
| DHT22 data | 4 | |
| PIR OUT | 13 | |
| Soil sensor AOUT | 34 | Must be an ADC1 pin (32–39), because ADC2 doesn't work while Wi-Fi is on |
| LDR divider | 35 | LDR to 3.3 V, 10 kΩ to GND, midpoint to GPIO35 |
| Button | 0 | The on-board BOOT button toggles relay 1 locally |

Power: a USB 5 V supply into the ESP32, and the relay board VCC from the ESP32's VIN (5 V) pin.

### Flashing
1. Install the Arduino IDE, then add the **esp32 by Espressif** board package.
2. Library Manager: install **PubSubClient**, **ArduinoJson**, **DHT sensor library** and **Adafruit Unified Sensor**.
3. Edit `config.h`: your Wi-Fi details, the Pi's IP address as `MQTT_HOST`, and `NODE_ID` (e.g. `bedroom`).
4. Board: *ESP32 Dev Module*. Then upload and open the Serial Monitor at 115200.
5. `NODE_ID` must match `node:` in `hub/config.yaml`.

### Calibrating the soil sensor
Open the Serial Monitor. Note the raw reading with the sensor in dry air and again in a glass of water, then
set `SOIL_DRY_RAW` and `SOIL_WET_RAW` in `config.h`.
