/* Material You dynamic colour: generates a full M3 colour scheme from a seed colour
   using Google's material-color-utilities (same algorithm Android uses for wallpapers).
   If the library can't load (offline LAN), the baseline tokens in style.css are used. */

const SEEDS = ["#6750A4", "#0B57D0", "#006A6A", "#386A20", "#8B5000", "#B3261E", "#984061", "#5D5F71"];
const DEFAULT_SEED = SEEDS[0];

let mcu = null;
try {
  mcu = await import("https://cdn.jsdelivr.net/npm/@material/material-color-utilities@0.3.0/+esm");
} catch {
  console.warn("material-color-utilities unavailable - using baseline theme");
}

const get = (k, d) => { try { return localStorage.getItem(k) || d; } catch { return d; } };
const set = (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } };
const kebab = (s) => s.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
const darkQuery = matchMedia("(prefers-color-scheme: dark)");

const SCHEME_KEYS = ["primary", "onPrimary", "primaryContainer", "onPrimaryContainer", "secondary", "onSecondary",
  "secondaryContainer", "onSecondaryContainer", "tertiary", "onTertiary", "tertiaryContainer", "onTertiaryContainer",
  "error", "onError", "errorContainer", "onErrorContainer", "onSurface", "onSurfaceVariant", "outline",
  "outlineVariant", "inverseSurface", "inverseOnSurface", "inversePrimary"];
// M3 surface-container tones (from the neutral palette)
const SURFACE_TONES = {
  light: { "surface": 98, "surface-container-lowest": 100, "surface-container-low": 96, "surface-container": 94,
    "surface-container-high": 92, "surface-container-highest": 90 },
  dark: { "surface": 6, "surface-container-lowest": 4, "surface-container-low": 10, "surface-container": 12,
    "surface-container-high": 17, "surface-container-highest": 22 },
};

const mode = () => get("pihome-mode", "auto");
const isDark = () => mode() === "dark" || (mode() === "auto" && darkQuery.matches);

function apply() {
  const root = document.documentElement;
  if (mode() === "auto") delete root.dataset.theme; else root.dataset.theme = mode();

  if (mcu) {
    const theme = mcu.themeFromSourceColor(mcu.argbFromHex(get("pihome-seed", DEFAULT_SEED)));
    const dark = isDark();
    const scheme = (dark ? theme.schemes.dark : theme.schemes.light).toJSON();
    for (const key of SCHEME_KEYS) root.style.setProperty(`--${kebab(key)}`, mcu.hexFromArgb(scheme[key]));
    for (const [name, tone] of Object.entries(SURFACE_TONES[dark ? "dark" : "light"])) {
      root.style.setProperty(`--${name}`, mcu.hexFromArgb(theme.palettes.neutral.tone(tone)));
    }
  }
  const surface = getComputedStyle(root).getPropertyValue("--surface").trim();
  document.getElementById("meta-theme")?.setAttribute("content", surface);
  window.dispatchEvent(new Event("themechange"));
}

// ------------------------------------------------------------------ dialog
function renderSwatches() {
  const current = get("pihome-seed", DEFAULT_SEED);
  const dark = isDark();
  document.getElementById("swatches").innerHTML = SEEDS.map((seed) => {
    let colors = [seed, seed, seed];
    if (mcu) {
      const p = mcu.themeFromSourceColor(mcu.argbFromHex(seed)).palettes;
      colors = [p.primary.tone(dark ? 80 : 40), p.secondary.tone(dark ? 60 : 80), p.tertiary.tone(dark ? 70 : 60)]
        .map(mcu.hexFromArgb);
    }
    return `<button class="swatch ${seed === current ? "selected" : ""}" data-seed="${seed}" aria-label="Colour ${seed}">
      ${colors.map((c) => `<span style="background:${c}"></span>`).join("")}</button>`;
  }).join("");
  document.querySelectorAll("#mode-seg button").forEach((b) => b.classList.toggle("selected", b.dataset.mode === mode()));
}

const dialog = document.getElementById("theme-dialog");
document.getElementById("theme-btn").onclick = () => { renderSwatches(); dialog.classList.remove("hidden"); };
document.getElementById("theme-close").onclick = () => dialog.classList.add("hidden");
dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.classList.add("hidden"); });
document.getElementById("swatches").addEventListener("click", (e) => {
  const sw = e.target.closest("[data-seed]");
  if (!sw) return;
  set("pihome-seed", sw.dataset.seed);
  apply(); renderSwatches();
});
document.getElementById("mode-seg").addEventListener("click", (e) => {
  const b = e.target.closest("[data-mode]");
  if (!b) return;
  set("pihome-mode", b.dataset.mode);
  apply(); renderSwatches();
});
darkQuery.addEventListener("change", () => mode() === "auto" && apply());
document.addEventListener("keydown", (e) => e.key === "Escape" && dialog.classList.add("hidden"));

apply();
