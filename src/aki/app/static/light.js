// The light of the room.
//
// Joe wired solar panels and photographed old books: both catch light. So the reading room
// is lit by the reader's own sun. Five keyed palettes (dawn, noon, golden hour, dusk, night)
// are blended by the computer's clock. In the light phases solar-cell blue is the ink and
// accent; in the dark phases gilt is the lamp. The clock is all it knows: it assumes an
// ordinary northern year and never asks where the reader is.

const PALETTES = {
  dawn: {
    dark: false, label: "Dawn",
    ground: "#F6EBE4", ground2: "#EEDDD4", ink: "#201A1A", ink2: "#6A5750", rule: "#DCC4B8",
    accent: "#2B3966", accentInk: "#FAF1EA", gilt: "#985822", paper: "#FDF8F2",
    sky1: "#F3C9B8", sky2: "#F6EBE4", sun: "#F0A46A", mark: "#F6D9A8",
  },
  noon: {
    dark: false, label: "Noon",
    ground: "#F4EEE3", ground2: "#EAE2D3", ink: "#1C1915", ink2: "#5E564A", rule: "#CDC1AB",
    accent: "#1E3663", accentInk: "#F7F2E8", gilt: "#8C600C", paper: "#FCF8F0",
    sky1: "#E1E9EF", sky2: "#F4EEE3", sun: "#E3A83A", mark: "#F3DC9B",
  },
  golden: {
    dark: false, label: "Golden hour",
    ground: "#F5E7CF", ground2: "#EDDAB9", ink: "#211A10", ink2: "#67553A", rule: "#D8C095",
    accent: "#243861", accentInk: "#FBF2DF", gilt: "#8D5609", paper: "#FDF7EA",
    sky1: "#F2C27A", sky2: "#F5E7CF", sun: "#EE9A3A", mark: "#F5D58C",
  },
  dusk: {
    dark: true, label: "Dusk",
    ground: "#1D1A33", ground2: "#282345", ink: "#F2E7DB", ink2: "#B9AAB4", rule: "#40385C",
    accent: "#F1B560", accentInk: "#1B1730", gilt: "#E9A95A", paper: "#F0E4CF",
    sky1: "#6B3A55", sky2: "#1D1A33", sun: "#E8794A", mark: "#5A4630",
  },
  night: {
    dark: true, label: "Night",
    ground: "#0F1B33", ground2: "#182846", ink: "#E8EEF7", ink2: "#9DB0CC", rule: "#2B3E63",
    accent: "#E7B64F", accentInk: "#0F1B33", gilt: "#D9A93F", paper: "#EFE6D2",
    sky1: "#16284A", sky2: "#0F1B33", sun: "#D9A93F", mark: "#4A4326",
  },
};
const TOKENS = ["ground", "ground2", "ink", "ink2", "rule", "accent", "accentInk", "gilt", "paper", "sky1", "sky2", "sun", "mark"];
const CSS_NAME = { ground2: "ground-2", ink2: "ink-2", accentInk: "accent-ink", sky1: "sky-1", sky2: "sky-2" };
export const HOLDS = ["sun", "dawn", "noon", "golden", "dusk", "night"];

// ------------------------------------------------------------------ colour blending in OKLab
const toLinear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const toGamma = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
function lab(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => toLinear(parseInt(hex.slice(i, i + 2), 16) / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}
function hex([L, a, b]) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
  return "#" + rgb.map((c) => Math.round(Math.min(1, Math.max(0, toGamma(c))) * 255)
    .toString(16).padStart(2, "0")).join("");
}
function blend(from, to, t) {
  if (t <= 0) return from;
  if (t >= 1) return to;
  const a = lab(from), b = lab(to);
  return hex(a.map((v, i) => v + (b[i] - v) * t));
}

// ------------------------------------------------------------------ the sun's day
export function sunTimes(date) {
  const day = Math.floor((date - new Date(date.getFullYear(), 0, 0)) / 864e5);
  const season = Math.cos((2 * Math.PI * (day + 10)) / 365); // +1 midwinter, -1 midsummer
  return { rise: 6.4 + 1.1 * season, set: 18.6 - 1.5 * season };
}

/** Which two keyed palettes the hour falls between, and how far along. */
export function phaseAt(date) {
  const h = date.getHours() + date.getMinutes() / 60;
  const { rise, set } = sunTimes(date);
  const span = (from, to, a, b) => ({ from: a, to: b, t: (h - from) / (to - from) });
  let mix;
  if (h < rise - 1.0) mix = { from: "night", to: "night", t: 0 };
  else if (h < rise - 0.15) mix = span(rise - 1.0, rise - 0.15, "night", "dusk");
  else if (h < rise + 0.6) mix = { from: "dawn", to: "dawn", t: 0 };
  else if (h < rise + 3.0) mix = span(rise + 0.6, rise + 3.0, "dawn", "noon");
  else if (h < set - 2.6) mix = { from: "noon", to: "noon", t: 0 };
  else if (h < set - 0.5) mix = span(set - 2.6, set - 0.5, "noon", "golden");
  else if (h < set + 0.2) mix = { from: "golden", to: "golden", t: 0 };
  else if (h < set + 1.6) mix = span(set + 0.2, set + 1.6, "dusk", "night");
  else mix = { from: "night", to: "night", t: 0 };
  const day = (h - rise) / (set - rise);
  return { ...mix, hour: h, rise, set, day, altitude: day > 0 && day < 1 ? Math.sin(Math.PI * day) : 0 };
}

const HELD = {
  dawn: { day: 0.04, altitude: 0.12 }, noon: { day: 0.5, altitude: 1 },
  golden: { day: 0.93, altitude: 0.2 }, dusk: { day: 1.03, altitude: 0 }, night: { day: 1.4, altitude: 0 },
};

let hold = "sun";
let state = null;
const listeners = new Set();

function read() {
  try { const v = localStorage.getItem("felz.light"); return HOLDS.includes(v) ? v : "sun"; } catch { return "sun"; }
}

function apply() {
  const now = new Date();
  const phase = phaseAt(now);
  let from, to, t, sky;
  if (hold === "sun") {
    ({ from, to, t } = phase);
    sky = { day: phase.day, altitude: phase.altitude };
  } else {
    from = to = hold; t = 0; sky = HELD[hold];
  }
  const a = PALETTES[from], b = PALETTES[to];
  const nearest = t < 0.5 ? from : to;
  const beforeSunrise = hold === "sun" && nearest === "dusk" && phase.hour < phase.rise;
  const root = document.documentElement;
  for (const token of TOKENS) {
    root.style.setProperty(`--${CSS_NAME[token] || token}`, blend(a[token], b[token], t));
  }
  root.dataset.light = a.dark ? "dark" : "light";
  root.dataset.phase = nearest;
  root.style.colorScheme = a.dark ? "dark" : "light";
  root.style.setProperty("--sun-day", String(Math.min(1.5, Math.max(-0.5, sky.day))));
  root.style.setProperty("--sun-altitude", sky.altitude.toFixed(3));
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = blend(a.ground, b.ground, t);
  state = { hold, phase: nearest, label: beforeSunrise ? "First light" : PALETTES[nearest].label, dark: a.dark, ...sky,
    hour: phase.hour, rise: phase.rise, set: phase.set };
  listeners.forEach((fn) => fn(state));
}

export function startLight() {
  hold = read();
  apply();
  setInterval(apply, 60_000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) apply(); });
}

export function setHold(value) {
  hold = HOLDS.includes(value) ? value : "sun";
  try { localStorage.setItem("felz.light", hold); } catch { /* private mode */ }
  apply();
}

export const light = () => state;
export const onLight = (fn) => { listeners.add(fn); if (state) fn(state); };
export const holdLabel = (value) => (value === "sun" ? "Follow the sun" : PALETTES[value].label);
export const clock = (hours) => {
  const h = Math.floor(hours), m = Math.round((hours - h) * 60);
  return new Date(2000, 0, 1, h, m).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
};
