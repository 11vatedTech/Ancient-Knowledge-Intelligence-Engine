// Drawn things: the room's device and its printer's rules. All inline SVG that takes its
// colours from the light, so each one changes with the hour.

/** The device: a woodcut sun whose disc is ruled like a solar cell. */
export function sunDevice({ rays = true, title = "" } = {}) {
  const straight = [], flames = [];
  for (let i = 0; i < 12; i += 1) {
    const turn = i * 30;
    straight.push(`<path transform="rotate(${turn})" d="M-8.5 -76 L0 -146 L8.5 -76 Z"/>`);
    flames.push(`<path transform="rotate(${turn + 15})" d="M-6.5 -76 C-13 -92 7 -101 -1.5 -128 C11 -106 1 -93 6.5 -76 Z"/>`);
  }
  const grid = [];
  for (let v = -52.5; v <= 52.5; v += 17.5) grid.push(`M${v} -70V70M-70 ${v}H70`);
  const bus = [];
  for (let v = -61.25; v <= 61.25; v += 17.5) bus.push(`M-70 ${v - 2.6}H70M-70 ${v + 2.6}H70`);
  const id = `d${Math.random().toString(36).slice(2, 8)}`;
  return `<svg class="device" viewBox="-150 -150 300 300" ${title ? `role="img" aria-label="${title}"` : 'aria-hidden="true"'}>
    <defs><clipPath id="${id}"><circle r="62"/></clipPath></defs>
    ${rays ? `<g class="device-rays">${straight.join("")}</g>
    <g class="device-flames">${flames.join("")}</g>` : ""}
    <circle class="device-disc" r="62"/>
    <g clip-path="url(#${id})">
      <path class="device-bus" d="${bus.join("")}"/>
      <path class="device-grid" d="${grid.join("")}"/>
    </g>
    <circle class="device-ring" r="62"/>
    <circle class="device-ring thin" r="69"/>
  </svg>`;
}

/** A swelled rule with a lozenge: the commonest ornament on an old title page. */
export function swell(width = 220) {
  const half = width / 2;
  return `<svg class="swell" viewBox="${-half} -6 ${width} 12" width="${width}" height="12" aria-hidden="true">
    <path d="M${-half} 0 Q${-half / 2} -1.9 -12 -0.5 L-12 0.5 Q${-half / 2} 1.9 ${-half} 0 Z"/>
    <path d="M${half} 0 Q${half / 2} -1.9 12 -0.5 L12 0.5 Q${half / 2} 1.9 ${half} 0 Z"/>
    <path d="M0 -4.6 L5.2 0 L0 4.6 L-5.2 0 Z"/>
  </svg>`;
}

/** A small star of n points, for the chart's magnitudes. */
export function star(points, outer, inner) {
  const steps = [];
  for (let i = 0; i < points * 2; i += 1) {
    const r = i % 2 ? inner : outer;
    const a = (Math.PI * i) / points - Math.PI / 2;
    steps.push(`${(r * Math.cos(a)).toFixed(2)} ${(r * Math.sin(a)).toFixed(2)}`);
  }
  return `M${steps.join("L")}Z`;
}

const ROMAN = [[1000, "M"], [900, "CM"], [500, "D"], [400, "CD"], [100, "C"], [90, "XC"],
  [50, "L"], [40, "XL"], [10, "X"], [9, "IX"], [5, "V"], [4, "IV"], [1, "I"]];
export function roman(n) {
  let out = "";
  for (const [value, numeral] of ROMAN) while (n >= value) { out += numeral; n -= value; }
  return out;
}
