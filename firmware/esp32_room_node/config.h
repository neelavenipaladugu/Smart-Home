// ============================================================
//  PiHome ESP32 room node - per-node settings
//  Copy this sketch once per room and change NODE_ID + pins.
// ============================================================
#pragma once

// ---- Wi-Fi ----
#define WIFI_SSID      "YOUR_WIFI_NAME"
#define WIFI_PASSWORD  "YOUR_WIFI_PASSWORD"

// ---- MQTT broker (the Raspberry Pi's IP address) ----
#define MQTT_HOST      "192.168.1.50"
#define MQTT_PORT      1883
#define MQTT_USER      ""
#define MQTT_PASS      ""
#define BASE_TOPIC     "home"

// ---- This node ----
// Must match "node:" in hub/config.yaml (e.g. bedroom, garden, kitchen)
#define NODE_ID        "bedroom"

// How often to send sensor readings (milliseconds)
#define REPORT_INTERVAL 10000

// ---- Relays (channel 1..4). Set a pin to -1 if unused ----
#define RELAY_COUNT 4
const int RELAY_PINS[RELAY_COUNT] = {26, 27, 32, 33};
#define RELAY_ACTIVE_LOW true      // most relay boards switch on LOW

// ---- Sensors. Set a pin to -1 if not connected ----
#define DHT_PIN        4           // DHT22 data pin
#define PIR_PIN        13          // PIR motion OUT
#define SOIL_PIN       34          // capacitive soil sensor AOUT (ADC1 pin only!)
#define LDR_PIN        35          // light sensor (LDR voltage divider)
#define BUTTON_PIN     0           // optional local button: toggles relay 1 (BOOT button)

// Soil calibration: raw ADC reading in dry air and in a glass of water
#define SOIL_DRY_RAW   3200
#define SOIL_WET_RAW   1300
