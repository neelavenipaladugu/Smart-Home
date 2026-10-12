/* PiHome dashboard - Material 3 UI, live updates over WebSocket */
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const fmtDateTime = (ts) => new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const icon = (name, cls = "") => `<span class="ms ${cls}">${name}</span>`;

const state = { devices: {}, sensors: {}, alerts: [], armed: false, mock: false };
let apiKey = safeGet("pihome-key") || "";
let energyChart, historyChart, historyHours = 24;
let currentTab = "home";
let cameraAvailable = false;

function safeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

// ------------------------------------------------------------------ Material Symbols mapping
const DEVICE_ICON = { light: "lightbulb", fan: "mode_fan", ac: "ac_unit", pump: "sprinkler", alarm: "e911_emergency",
  lock: "lock", plug: "power", tv: "tv", heater: "heat" };
const ROOM_ICON = { "living room": "chair", kitchen: "kitchen", bedroom: "bed", bathroom: "bathtub", hallway: "door_front",
  garden: "yard", system: "developer_board", garage: "garage", office: "desk" };
const FIELD_ICON = { temperature: "thermostat", humidity: "water_drop", soil: "potted_plant", light: "light_mode",
  motion: "directions_walk", gas: "local_fire_department", open: "door_open", leak: "water_damage",
  cpu_temp: "device_thermostat", cpu: "speed", memory: "memory", disk: "hard_drive", power: "bolt", rssi: "wifi",
  person: "face", pressed: "radio_button_checked", active: "sensors" };
const SENSOR_KIND_ICON = { pir: "directions_walk", door: "door_front", mq2: "local_fire_department", leak: "water_damage" };
const SOURCE_ICON = { rfid: "badge", camera: "videocam", hub: "home", mqtt: "router", telegram: "send", voice: "mic",
  dashboard: "touch_app" };

const deviceIcon = (d) => (d.type === "lock" && d.on ? "lock_open" : DEVICE_ICON[d.type] || "power");
const roomIcon = (room) => ROOM_ICON[room.toLowerCase()] || "home";
function alertIcon(a) {
  if (a.level === "danger") return "e911_emergency";
  if (a.level === "warning") return "warning";
  const src = String(a.source || "").split(":")[0];
  return src === "rule" || src === "schedule" ? "smart_toy" : SOURCE_ICON[src] || "info";
}

// ------------------------------------------------------------------ API
async function api(path, body) {
  const opts = { headers: { "Content-Type": "application/json" } };
  if (apiKey) opts.headers["X-API-Key"] = apiKey;
  if (body !== undefined) { opts.method = "POST"; opts.body = JSON.stringify(body); }
  const res = await fetch(`/api${path}`, opts);
  if (res.status === 401) {
    apiKey = prompt("PiHome API key:") || "";
    safeSet("pihome-key", apiKey);
    return api(path, body);
  }
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}
const withKey = (url) => (apiKey ? `${url}${url.includes("?") ? "&" : "?"}key=${encodeURIComponent(apiKey)}` : url);

// ------------------------------------------------------------------ WebSocket
let socket;
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(withKey(`${proto}://${location.host}/ws`));
  socket.onopen = () => $("#conn").classList.add("ok");
  socket.onclose = () => { $("#conn").classList.remove("ok"); setTimeout(connect, 2000); };
  socket.onmessage = (msg) => handleEvent(JSON.parse(msg.data));
}
setInterval(() => socket?.readyState === 1 && socket.send("ping"), 25000);

function handleEvent(ev) {
  switch (ev.type) {
    case "snapshot": loadSnapshot(ev.state); break;
    case "device":
      if (state.devices[ev.id]) { state.devices[ev.id].on = ev.on; state.devices[ev.id].changed_at = ev.ts; }
      renderHome(); break;
    case "sensor": {
      const s = state.sensors[ev.id];
      if (s) { Object.assign(s.values, ev.data); s.updated = ev.ts; }
      renderHome(); renderSecuritySensors(); break;
    }
    case "alert":
      state.alerts.unshift(ev); state.alerts = state.alerts.slice(0, 50);
      renderAlerts();
      if (ev.level !== "info") snackbar(ev.message, ev.level);
      if (ev.source === "rfid" && currentTab === "security") loadAccess();
      break;
    case "armed": state.armed = ev.armed; renderArmed(); renderGreeting(); break;
    case "node":
      for (const d of Object.values(state.devices)) if (d.driver === "mqtt") d.online = ev.online;
      renderRooms(); break;
  }
}

function loadSnapshot(s) {
  state.devices = Object.fromEntries(s.devices.map((d) => [d.id, d]));
  state.sensors = Object.fromEntries(s.sensors.map((x) => [x.id, x]));
  state.alerts = s.alerts; state.armed = s.armed; state.mock = s.mock;
  $("#home-name").textContent = s.name;
  document.title = s.name;
  $("#mode-badge").classList.toggle("hidden", !s.mock);
  $("#sim-card").classList.toggle("hidden", !s.mock);
  $("#rfid-sim").classList.toggle("hidden", !s.mock);
  setCamera(s.camera);
  renderHome(); renderAlerts(); renderArmed(); renderSecuritySensors();
}

// ------------------------------------------------------------------ Home
function renderHome() { renderRooms(); renderStats(); renderGreeting(); }

function sensorChip(field, value, unit) {
  const ic = FIELD_ICON[field] || "sensors";
  if (typeof value === "boolean") {
    const text = { motion: value ? "Motion" : "No motion", open: value ? "Open" : "Closed",
      gas: value ? "GAS DETECTED" : "Air OK", leak: value ? "LEAK" : "Dry", pressed: value ? "Pressed" : "Idle" }[field]
      ?? (value ? "On" : "Off");
    const alarm = value && (field === "gas" || field === "leak");
    const cls = value ? (alarm ? "alarm" : "active") : "";
    const shownIcon = field === "open" && !value ? "door_front" : ic;
    return `<span class="chip ${cls}">${icon(shownIcon)}${text}</span>`;
  }
  return `<span class="chip">${icon(ic)}${esc(value)}${esc(unit || "")}</span>`;
}

function deviceState(d) {
  if (d.online === false) return "Offline";
  if (d.type === "lock") return d.on ? "Unlocked" : "Locked";
  return d.on ? (d.watts ? `On · ${d.watts} W` : "On") : "Off";
}

function renderRooms() {
  const rooms = {};
  const add = (room) => (rooms[room] ??= { devices: [], sensors: [] });
  Object.values(state.sensors).forEach((s) => add(s.room).sensors.push(s));
  Object.values(state.devices).forEach((d) => add(d.room).devices.push(d));

  const order = Object.keys(rooms).sort((a, b) => (a === "System") - (b === "System") || a.localeCompare(b));
  $("#rooms").innerHTML = order.map((name) => {
    const r = rooms[name];
    const onCount = r.devices.filter((d) => d.on).length;
    const chips = r.sensors.flatMap((s) => Object.entries(s.values)
      .filter(([f]) => f !== "rssi")
      .map(([f, v]) => sensorChip(f, v, s.units?.[f]))).join("");
    const tiles = r.devices.map((d) => `
      <button class="tile ${d.on ? "on" : ""} ${d.type === "alarm" ? "alarm" : ""} ${d.online === false ? "offline" : ""}"
              data-device="${esc(d.id)}" title="${esc(d.name)}" aria-pressed="${d.on}">
        <span class="tile-icon">${icon(deviceIcon(d))}</span>
        <span class="tile-text"><div class="tile-name">${esc(d.name.replace(name, "").trim() || d.name)}</div>
        <div class="tile-state">${deviceState(d)}</div></span>
      </button>`).join("");
    const meta = r.devices.length ? `${onCount} of ${r.devices.length} on` : `${r.sensors.length} sensor${r.sensors.length > 1 ? "s" : ""}`;
    return `<div class="room">
      <div class="room-head"><span class="room-icon">${icon(roomIcon(name))}</span>
        <div><div class="room-name">${esc(name)}</div><div class="room-meta">${meta}</div></div></div>
      ${chips ? `<div class="chip-set">${chips}</div>` : ""}
      ${tiles ? `<div class="tiles">${tiles}</div>` : ""}
    </div>`;
  }).join("");
}

function indoorAvg(field) {
  const vals = Object.values(state.sensors).filter((s) => s.room !== "Garden" && s.room !== "System")
    .map((s) => s.values[field]).filter((v) => typeof v === "number");
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}

function renderStats() {
  const devs = Object.values(state.devices);
  const on = devs.filter((d) => d.on);
  $("#st-power").textContent = `${on.reduce((a, d) => a + d.watts, 0)} W`;
  $("#st-on").textContent = `${on.length} / ${devs.length}`;
  const t = indoorAvg("temperature"), h = indoorAvg("humidity");
  $("#st-temp").textContent = t === null ? "–" : `${t.toFixed(1)}°C`;
  $("#st-hum").textContent = h === null ? "–" : `${h.toFixed(0)}%`;
}

function renderGreeting() {
  const hr = new Date().getHours();
  $("#greet").textContent = hr < 5 ? "Good night" : hr < 12 ? "Good morning" : hr < 17 ? "Good afternoon" : "Good evening";
  const on = Object.values(state.devices).filter((d) => d.on).length;
  const t = indoorAvg("temperature");
  const alarms = Object.values(state.sensors).some((s) => s.values.gas === true || s.values.leak === true);
  const parts = [alarms ? "⚠ Alarm active" : null, `${on} device${on === 1 ? "" : "s"} on`,
    t !== null ? `${t.toFixed(1)}°C inside` : null, state.armed ? "Security armed" : "Security off"];
  $("#greet-sub").textContent = parts.filter(Boolean).join(" · ");
}

function renderAlerts() {
  $("#alerts").innerHTML = state.alerts.map((a) => `
    <li><span class="lead-icon ${a.level}">${icon(alertIcon(a))}</span>
      <div class="li-body"><div class="li-title sm">${esc(a.message)}</div></div>
      <span class="li-meta">${fmtTime(a.ts)}</span></li>`).join("") || `<li class="empty">No activity yet</li>`;
}

// ------------------------------------------------------------------ snackbar
function snackbar(text, level = "info") {
  const el = document.createElement("div");
  el.className = `snackbar ${level}`;
  el.innerHTML = `${icon(level === "danger" ? "e911_emergency" : level === "warning" ? "warning" : "check_circle")}<span>${esc(text)}</span>`;
  const box = $("#toasts");
  box.appendChild(el);
  while (box.children.length > 3) box.firstChild.remove();
  setTimeout(() => { el.classList.add("leaving"); setTimeout(() => el.remove(), 200); }, level === "danger" ? 7000 : 4000);
}

// ------------------------------------------------------------------ interactions
$("#rooms").addEventListener("click", async (e) => {
  const tile = e.target.closest("[data-device]");
  if (!tile) return;
  navigator.vibrate?.(10);
  try { await api(`/devices/${tile.dataset.device}`, { action: "toggle" }); } catch (err) { snackbar(err.message, "warning"); }
});
$("#all-off").onclick = async () => { await api("/all-off", {}); snackbar("All devices turned off"); };

async function sendCommand(text) {
  text = (text ?? $("#cmd-input").value).trim();
  if (!text) return;
  try { await api("/voice", { text }); $("#cmd-input").value = ""; snackbar(`“${text}”`); }
  catch (err) { snackbar(`Didn't understand “${text}”`, "warning"); }
}
$("#cmd-send").onclick = () => sendCommand();
$("#cmd-input").addEventListener("keydown", (e) => e.key === "Enter" && sendCommand());

document.querySelectorAll("[data-sim]").forEach((btn) => {
  btn.onclick = () => { const [id, data] = JSON.parse(btn.dataset.sim); api(`/sensors/${id}/simulate`, data); };
});

// Voice: browser speech recognition (needs HTTPS or localhost); falls back to typing
const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;
function startVoice() {
  if (!Speech || !window.isSecureContext) {
    showTab("home");
    $("#cmd-input").focus();
    snackbar(Speech ? "Voice needs HTTPS — type a command instead" : "Voice not supported in this browser — type a command");
    return;
  }
  recognizer = new Speech();
  recognizer.lang = navigator.language || "en-US";
  recognizer.interimResults = true;
  $("#listen-text").textContent = "Listening…";
  $("#listen-sheet").classList.remove("hidden");
  recognizer.onresult = (e) => {
    const r = e.results[e.results.length - 1];
    $("#listen-text").textContent = r[0].transcript;
    if (r.isFinal) sendCommand(r[0].transcript);
  };
  recognizer.onend = () => setTimeout(() => $("#listen-sheet").classList.add("hidden"), 400);
  recognizer.onerror = () => $("#listen-sheet").classList.add("hidden");
  recognizer.start();
}
["#fab", "#fab-rail", "#mic-btn"].forEach((id) => { $(id).onclick = startVoice; });
$("#listen-cancel").onclick = () => { recognizer?.abort(); $("#listen-sheet").classList.add("hidden"); };

// ------------------------------------------------------------------ Security
function renderArmed() {
  const armed = state.armed;
  const top = $("#arm-btn");
  top.innerHTML = `${icon(armed ? "lock" : "lock_open")}<span class="btn-text">${armed ? "Armed" : "Disarmed"}</span>`;
  top.classList.toggle("btn-armed", armed);
  $("#sec-icon").textContent = armed ? "shield_lock" : "lock_open";
  $("#sec-text").textContent = armed ? "Armed" : "Disarmed";
  $("#sec-hint").textContent = armed ? "Door, motion and camera alerts are active" : "Door and camera alerts are off";
  const btn = $("#arm-btn-2");
  btn.textContent = armed ? "Disarm" : "Arm system";
  btn.className = `btn wide ${armed ? "danger" : "filled"}`;
  $("#sec-card").classList.toggle("armed", armed);
}
const toggleArm = () => { navigator.vibrate?.(20); return api("/security", { armed: !state.armed }); };
$("#arm-btn").onclick = toggleArm;
$("#arm-btn-2").onclick = toggleArm;

function renderSecuritySensors() {
  const rows = Object.values(state.sensors).filter((s) => SENSOR_KIND_ICON[s.kind]);
  $("#sec-sensors").innerHTML = rows.map((s) => {
    const [field, v] = Object.entries(s.values)[0] || [];
    const alarm = v === true;
    return `<li><span class="lead-icon ${alarm ? (field === "gas" || field === "leak" ? "danger" : "warning") : "ok"}">${icon(SENSOR_KIND_ICON[s.kind])}</span>
      <div class="li-body"><div class="li-title sm">${esc(s.name)}</div><div class="li-sub">${esc(s.room)}</div></div>
      ${field ? sensorChip(field, v) : ""}</li>`;
  }).join("");
}

function setCamera(on) {
  cameraAvailable = on;
  $("#camera-off").classList.toggle("hidden", on);
  $("#camera-img").classList.toggle("hidden", !on);
  $("#live-badge").classList.toggle("hidden", !on);
  $("#snap-btn").href = withKey("/api/camera/snapshot");
  updateStream();
}
// Only stream video while the Security tab is open (saves Pi CPU + Wi-Fi)
function updateStream() {
  const img = $("#camera-img");
  const want = cameraAvailable && currentTab === "security";
  if (want && !img.getAttribute("src")) img.src = withKey("/api/camera/stream");
  if (!want && img.getAttribute("src")) img.removeAttribute("src");
}

async function loadAccess() {
  const rows = await api("/access");
  $("#access").innerHTML = rows.map((r) => `
    <li><span class="lead-icon ${r.granted ? "ok" : "danger"}">${icon(r.granted ? "how_to_reg" : "block")}</span>
      <div class="li-body"><div class="li-title">${esc(r.person)}</div><div class="li-sub">Card ${esc(r.card)} · ${r.granted ? "Access granted" : "Denied"}</div></div>
      <span class="li-meta">${fmtDateTime(r.ts)}</span></li>`).join("") || `<li class="empty">No door activity yet</li>`;
}
$("#rfid-btn").onclick = () => api("/rfid/scan", { card: $("#rfid-card").value.trim() }).then(loadAccess);

// ------------------------------------------------------------------ charts (themed with M3 tokens)
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
function chartOptions() {
  const grid = css("--outline-variant"), text = css("--on-surface-variant");
  return {
    responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false },
      tooltip: { backgroundColor: css("--inverse-surface"), titleColor: css("--inverse-on-surface"),
        bodyColor: css("--inverse-on-surface"), cornerRadius: 8, padding: 10, displayColors: false } },
    scales: { x: { grid: { display: false }, border: { display: false }, ticks: { color: text, maxTicksLimit: 8 } },
      y: { grid: { color: grid }, border: { display: false }, ticks: { color: text } } },
  };
}
function themeCharts() {
  if (energyChart) {
    energyChart.options = chartOptions();
    energyChart.data.datasets[0].backgroundColor = css("--primary");
    energyChart.update("none");
  }
  if (historyChart) {
    historyChart.options = chartOptions();
    Object.assign(historyChart.data.datasets[0], lineColors());
    historyChart.update("none");
  }
}
function alpha(hex, a) {
  const m = hex.replace("#", "").match(/^([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  return m ? `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${a})` : hex;
}
function lineColors() {
  return { borderColor: css("--primary"), backgroundColor: alpha(css("--primary"), 0.14) };
}
window.addEventListener("themechange", themeCharts);

// ------------------------------------------------------------------ Energy
async function loadEnergy() {
  const e = await api("/energy");
  const tariff = parseFloat($("#tariff").value) || 0;
  $("#en-live").textContent = `${e.live_watts} W`;
  $("#en-today").textContent = `${e.today_kwh.toFixed(2)} kWh`;
  $("#en-cost").textContent = (e.today_kwh * tariff).toFixed(2);
  $("#en-week").textContent = `${e.week.reduce((a, d) => a + d.kwh, 0).toFixed(1)} kWh`;
  $("#energy-devices").innerHTML = e.devices.map((d) => {
    const dev = state.devices[d.id] || { type: "plug" };
    return `<li><span class="lead-icon ${d.on ? "" : "ok"}">${icon(deviceIcon(dev))}</span>
      <div class="li-body"><div class="li-title sm">${esc(d.name)}</div><div class="li-sub">${d.on_hours} h · ${d.watts} W</div></div>
      <span class="li-meta">${d.kwh} kWh</span></li>`;
  }).join("");
  const data = { labels: e.week.map((d) => d.day),
    datasets: [{ data: e.week.map((d) => d.kwh), backgroundColor: css("--primary"), borderRadius: 12, borderSkipped: false, maxBarThickness: 40 }] };
  if (energyChart) { energyChart.data = data; energyChart.update(); return; }
  if (window.Chart) energyChart = new Chart($("#energy-chart"), { type: "bar", data, options: chartOptions() });
}
$("#tariff").value = safeGet("pihome-tariff") || 8;
$("#tariff").onchange = () => { safeSet("pihome-tariff", $("#tariff").value); loadEnergy(); };

// ------------------------------------------------------------------ Routines
async function loadAutomations() {
  const a = await api("/automations");
  $("#rules").innerHTML = a.rules.map((r) => `
    <li><span class="lead-icon ${r.active ? "" : "ok"}">${icon("smart_toy", r.active ? "fill" : "")}</span>
      <div class="li-body">
        <div class="li-title">${esc(r.name)}</div>
        <div class="li-sub">${esc(r.description)}</div>
        <div class="li-meta">${r.fire_count ? `Ran ${r.fire_count}× · last at ${fmtTime(r.last_fired)}` : "Hasn't run yet"}${r.active ? " · condition active" : ""}</div>
      </div>
      <label class="switch" title="Enable routine"><input type="checkbox" data-rule="${r.index}" ${r.enabled ? "checked" : ""}>
        <span class="track"></span><span class="handle">${icon("check")}</span></label>
    </li>`).join("");
  $("#schedules").innerHTML = a.schedules.map((s) => `
    <li><span class="lead-icon">${icon("schedule")}</span>
      <div class="li-body"><div class="li-title">${esc(s.name)}</div><div class="li-sub">${esc(s.description)}</div>
      <div class="li-meta">${s.days.length === 7 ? "Every day" : s.days.map((d) => d[0].toUpperCase() + d.slice(1)).join(", ")}</div></div>
      <span class="li-meta">${esc(s.at)}</span></li>`).join("") || `<li class="empty">No schedules</li>`;
}
$("#rules").addEventListener("change", (e) => {
  const idx = e.target.dataset.rule;
  if (idx === undefined) return;
  api(`/automations/${idx}`, { enabled: e.target.checked })
    .then((r) => snackbar(`${r.name} ${r.enabled ? "enabled" : "paused"}`));
});

// ------------------------------------------------------------------ History
async function loadHistoryKeys() {
  const keys = await api("/history/keys");
  const sel = $("#hist-key");
  const current = sel.value;
  sel.innerHTML = keys.map((k) => `<option value="${esc(k)}" ${k === current ? "selected" : ""}>${esc(k.replace(".", " · ").replace(/_/g, " "))}</option>`).join("");
  if (!current && keys.includes("living_env.temperature")) sel.value = "living_env.temperature";
}

async function loadHistory() {
  const key = $("#hist-key").value;
  if (!key || !window.Chart) return;
  const { points } = await api(`/history?key=${encodeURIComponent(key)}&hours=${historyHours}`);
  const binary = points.length && points.every((p) => p.value === 0 || p.value === 1);
  const data = {
    labels: points.map((p) => (historyHours > 24 ? fmtDateTime(p.ts) : fmtTime(p.ts))),
    datasets: [{ data: points.map((p) => p.value), ...lineColors(), fill: true,
      tension: 0.35, pointRadius: 0, pointHoverRadius: 5, borderWidth: 3, stepped: binary }],
  };
  if (historyChart) { historyChart.data = data; historyChart.update(); return; }
  historyChart = new Chart($("#history-chart"), { type: "line", data, options: chartOptions() });
}
$("#hist-key").onchange = loadHistory;
$("#hist-range").addEventListener("click", (e) => {
  const b = e.target.closest("[data-h]");
  if (!b) return;
  historyHours = +b.dataset.h;
  document.querySelectorAll("#hist-range button").forEach((x) => x.classList.toggle("selected", x === b));
  loadHistory();
});

async function loadEvents() {
  const rows = await api("/events?limit=100");
  $("#events").innerHTML = rows.map((r) => `
    <li><span class="lead-icon ${r.level}">${icon(alertIcon(r))}</span>
      <div class="li-body"><div class="li-title sm">${esc(r.message)}</div><div class="li-sub">${esc(r.source)}</div></div>
      <span class="li-meta">${fmtDateTime(r.ts)}</span></li>`).join("") || `<li class="empty">No events yet</li>`;
}

// ------------------------------------------------------------------ navigation
const TAB_LOADERS = {
  security: loadAccess,
  energy: loadEnergy,
  automations: loadAutomations,
  history: async () => { await loadHistoryKeys(); await loadHistory(); await loadEvents(); },
};
function showTab(name) {
  currentTab = name;
  document.querySelectorAll("#tabs .nav-item").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  TAB_LOADERS[name]?.().catch((err) => snackbar(err.message, "warning"));
  updateStream();
  safeSet("pihome-tab", name);
  window.scrollTo({ top: 0 });
}
$("#tabs").addEventListener("click", (e) => { const b = e.target.closest(".nav-item"); if (b) showTab(b.dataset.tab); });
setInterval(() => { if (TAB_LOADERS[currentTab] && currentTab !== "security") TAB_LOADERS[currentTab]().catch(() => {}); }, 30000);
setInterval(renderGreeting, 60000);

// top app bar gets a tinted container colour when content scrolls under it (M3 behaviour)
window.addEventListener("scroll", () => $("#app-bar").classList.toggle("scrolled", window.scrollY > 4), { passive: true });

connect();
showTab(safeGet("pihome-tab") || "home");
