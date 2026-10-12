/*
 * PiHome ESP32 room node
 * ----------------------------------------------------------------------------
 * Reads room sensors and drives up to 4 relays, talking to the Raspberry Pi hub
 * over MQTT.
 *
 *   publishes  home/<node>/sensor            {"temperature":24.5,"humidity":55,...}
 *   publishes  home/<node>/status            "online" / "offline" (retained, LWT)
 *   publishes  home/<node>/relay/<ch>/state  "ON" / "OFF"
 *   subscribes home/<node>/relay/<ch>/set    "ON" / "OFF" / "TOGGLE"
 *
 * Libraries (Arduino Library Manager):
 *   - PubSubClient           by Nick O'Leary
 *   - ArduinoJson (v7)       by Benoit Blanchon
 *   - DHT sensor library     by Adafruit  (+ Adafruit Unified Sensor)
 * Board: "ESP32 Dev Module" (esp32 by Espressif)
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include "config.h"

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
DHT dht(DHT_PIN, DHT22);

bool relayState[RELAY_COUNT] = {false};
bool lastMotion = false;
unsigned long lastReport = 0;
unsigned long lastReconnectAttempt = 0;
unsigned long lastButton = 0;

String topic(const String &suffix) { return String(BASE_TOPIC) + "/" + NODE_ID + "/" + suffix; }

// ------------------------------------------------------------------ relays
void setRelay(int ch, bool on) {          // ch is 0-based
  if (ch < 0 || ch >= RELAY_COUNT || RELAY_PINS[ch] < 0) return;
  relayState[ch] = on;
  digitalWrite(RELAY_PINS[ch], (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
  mqtt.publish(topic("relay/" + String(ch + 1) + "/state").c_str(), on ? "ON" : "OFF", true);
  Serial.printf("Relay %d -> %s\n", ch + 1, on ? "ON" : "OFF");
}

// ------------------------------------------------------------------ sensors
int soilPercent() {
  if (SOIL_PIN < 0) return -1;
  int raw = analogRead(SOIL_PIN);
  int pct = map(raw, SOIL_DRY_RAW, SOIL_WET_RAW, 0, 100);
  return constrain(pct, 0, 100);
}

void reportSensors() {
  JsonDocument doc;
  if (DHT_PIN >= 0) {
    float t = dht.readTemperature();
    float h = dht.readHumidity();
    if (!isnan(t)) doc["temperature"] = round(t * 10) / 10.0;
    if (!isnan(h)) doc["humidity"] = round(h * 10) / 10.0;
  }
  if (PIR_PIN >= 0) doc["motion"] = (bool)digitalRead(PIR_PIN);
  if (SOIL_PIN >= 0) doc["soil"] = soilPercent();
  if (LDR_PIN >= 0) doc["light"] = map(analogRead(LDR_PIN), 0, 4095, 0, 100);
  doc["rssi"] = WiFi.RSSI();

  char buf[256];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(topic("sensor").c_str(), buf, n);
  Serial.println(buf);
}

// ------------------------------------------------------------------ MQTT
void onMessage(char *t, byte *payload, unsigned int len) {
  String tp(t), msg;
  for (unsigned int i = 0; i < len; i++) msg += (char)payload[i];
  msg.trim();
  msg.toUpperCase();

  // home/<node>/relay/<ch>/set
  int r = tp.indexOf("/relay/");
  if (r < 0 || !tp.endsWith("/set")) return;
  int ch = tp.substring(r + 7, tp.lastIndexOf('/')).toInt() - 1;
  if (ch < 0 || ch >= RELAY_COUNT) return;
  bool on = (msg == "TOGGLE") ? !relayState[ch] : (msg == "ON" || msg == "1" || msg == "TRUE");
  setRelay(ch, on);
}

bool mqttConnect() {
  String clientId = String("pihome-") + NODE_ID + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  String willTopic = topic("status");
  bool ok = strlen(MQTT_USER)
      ? mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS, willTopic.c_str(), 1, true, "offline")
      : mqtt.connect(clientId.c_str(), willTopic.c_str(), 1, true, "offline");
  if (ok) {
    Serial.println("MQTT connected");
    mqtt.publish(willTopic.c_str(), "online", true);
    mqtt.subscribe(topic("relay/+/set").c_str());
    for (int i = 0; i < RELAY_COUNT; i++)
      if (RELAY_PINS[i] >= 0) mqtt.publish(topic("relay/" + String(i + 1) + "/state").c_str(), relayState[i] ? "ON" : "OFF", true);
    reportSensors();
  }
  return ok;
}

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting to Wi-Fi %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) { delay(500); Serial.print("."); }
  Serial.println(WiFi.status() == WL_CONNECTED ? "\nWi-Fi OK, IP: " + WiFi.localIP().toString() : "\nWi-Fi failed, retrying later");
}

// ------------------------------------------------------------------ setup / loop
void setup() {
  Serial.begin(115200);
  for (int i = 0; i < RELAY_COUNT; i++) {
    if (RELAY_PINS[i] < 0) continue;
    pinMode(RELAY_PINS[i], OUTPUT);
    digitalWrite(RELAY_PINS[i], RELAY_ACTIVE_LOW ? HIGH : LOW);  // start OFF
  }
  if (PIR_PIN >= 0) pinMode(PIR_PIN, INPUT);
  if (BUTTON_PIN >= 0) pinMode(BUTTON_PIN, INPUT_PULLUP);
  if (DHT_PIN >= 0) dht.begin();
  analogReadResolution(12);

  connectWifi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setBufferSize(512);
}

void loop() {
  unsigned long now = millis();

  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
    return;
  }
  if (!mqtt.connected()) {
    if (now - lastReconnectAttempt > 5000) {
      lastReconnectAttempt = now;
      mqttConnect();
    }
  } else {
    mqtt.loop();
  }

  // Motion: report instantly on change so automations react fast
  if (PIR_PIN >= 0) {
    bool motion = digitalRead(PIR_PIN);
    if (motion != lastMotion) {
      lastMotion = motion;
      String msg = String("{\"motion\":") + (motion ? "true" : "false") + "}";
      mqtt.publish(topic("sensor").c_str(), msg.c_str());
    }
  }

  // Local button toggles relay 1 (works even if the hub is down)
  if (BUTTON_PIN >= 0 && digitalRead(BUTTON_PIN) == LOW && now - lastButton > 400) {
    lastButton = now;
    setRelay(0, !relayState[0]);
  }

  if (now - lastReport > REPORT_INTERVAL) {
    lastReport = now;
    reportSensors();
  }
}
