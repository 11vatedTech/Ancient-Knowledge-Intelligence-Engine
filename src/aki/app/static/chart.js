// The chart: the names of the library drawn as an engraved star chart.
//
// A star's magnitude is the number of pages that name the entry. A line joins two names
// that often stand on the same page. That is all it shows: shared pages, not a claim about
// how two names relate.

import { star } from "./art.js";

const W = 1500, H = 860, PAD = 70;
const TYPE_ORDER = ["divine", "person", "place", "text", "tradition", "concept"];

function seeded(seed) {
  let s = seed >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}

/** A small deterministic force layout: the same archive always draws the same sky. */
export function layout(nodes, links) {
  const random = seeded(1986);
  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const points = nodes.map((n) => {
    const sector = (Math.max(0, TYPE_ORDER.indexOf(n.type)) / TYPE_ORDER.length) * Math.PI * 2;
    const angle = sector + (random() - 0.5) * 1.1, radius = 180 + random() * 220;
    return { x: Math.cos(angle) * radius * 1.6, y: Math.sin(angle) * radius, vx: 0, vy: 0, node: n };
  });
  const springs = links.map((l) => [index.get(l.a), index.get(l.b), l.weight])
    .filter(([a, b]) => a !== undefined && b !== undefined);
  for (let step = 0; step < 420; step += 1) {
    const heat = 1 - step / 420;
    for (let i = 0; i < points.length; i += 1) {
      const p = points[i];
      for (let j = i + 1; j < points.length; j += 1) {
        const q = points[j];
        let dx = p.x - q.x, dy = (p.y - q.y) * 1.75;
        const d2 = dx * dx + dy * dy + 40;
        const push = 5200 / d2;
        dx *= push / Math.sqrt(d2); dy *= push / Math.sqrt(d2);
        p.vx += dx; p.vy += dy; q.vx -= dx; q.vy -= dy;
      }
      p.vx -= p.x * 0.0026; p.vy -= p.y * 0.0062;
    }
    for (const [a, b, weight] of springs) {
      const p = points[a], q = points[b];
      const dx = q.x - p.x, dy = q.y - p.y;
      const distance = Math.sqrt(dx * dx + dy * dy) || 1;
      const pull = (distance - 78) * 0.012 * (0.55 + Math.min(weight, 0.5));
      p.vx += (dx / distance) * pull; p.vy += (dy / distance) * pull;
      q.vx -= (dx / distance) * pull; q.vy -= (dy / distance) * pull;
    }
    for (const p of points) {
      const speed = Math.hypot(p.vx, p.vy), limit = 26 * heat + 1;
      if (speed > limit) { p.vx *= limit / speed; p.vy *= limit / speed; }
      p.x += p.vx; p.y += p.vy; p.vx *= 0.55; p.vy *= 0.55;
    }
  }
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  for (const p of points) {
    p.x = maxX > minX ? PAD + 40 + ((p.x - minX) / (maxX - minX)) * (W - 2 * PAD - 140) : W / 2;
    p.y = maxY > minY ? PAD + ((p.y - minY) / (maxY - minY)) * (H - 2 * PAD) : H / 2;
  }
  return points;
}

function magnitude(rank, total) {
  if (rank < Math.max(4, total * 0.04)) return 1;
  if (rank < total * 0.16) return 2;
  if (rank < total * 0.42) return 3;
  return 4;
}
const GLYPH = {
  1: () => `<path d="${star(8, 11, 4.4)}"/>`,
  2: () => `<path d="${star(6, 7.6, 3.2)}"/>`,
  3: () => `<path d="${star(4, 5.4, 1.9)}"/>`,
  4: () => `<circle r="2"/>`,
};
const REACH = { 1: 13, 2: 10, 3: 8, 4: 5 };

function placeLabels(points) {
  const taken = [];
  const hit = (box) => taken.some((t) => box.x < t.x + t.w && box.x + box.w > t.x && box.y < t.y + t.h && box.y + box.h > t.y);
  for (const p of points) taken.push({ x: p.x - REACH[p.mag], y: p.y - REACH[p.mag], w: REACH[p.mag] * 2, h: REACH[p.mag] * 2 });
  for (const p of points) {
    const size = p.mag === 1 ? 18 : 13.5;
    const w = p.node.name.length * size * 0.56 + 6, h = size + 3, r = REACH[p.mag];
    const options = [
      { x: p.x + r, y: p.y - h / 2, anchor: "start", tx: r + 2, ty: size * 0.34 },
      { x: p.x - r - w, y: p.y - h / 2, anchor: "end", tx: -r - 2, ty: size * 0.34 },
      { x: p.x - w / 2, y: p.y - r - h, anchor: "middle", tx: 0, ty: -r - 3 },
      { x: p.x - w / 2, y: p.y + r, anchor: "middle", tx: 0, ty: r + size * 0.8 },
    ];
    const free = options.find((o) => o.x > 8 && o.x + w < W - 8 && o.y > 8 && o.y + h < H - 8 && !hit({ x: o.x, y: o.y, w, h }));
    const chosen = free || options[0];
    p.label = { ...chosen, shown: Boolean(free) || p.mag === 1 };
    if (p.label.shown) taken.push({ x: chosen.x, y: chosen.y, w, h });
  }
}

export function drawChart(data, esc) {
  const points = layout(data.nodes, data.links);
  points.forEach((p, rank) => { p.mag = magnitude(rank, points.length); });
  placeLabels(points);
  const at = new Map(points.map((p) => [p.node.id, p]));
  const joins = data.links.filter((l) => at.has(l.a) && at.has(l.b)).map((l) => {
    const a = at.get(l.a), b = at.get(l.b);
    return `<line class="join" data-a="${esc(l.a)}" data-b="${esc(l.b)}" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"/>`;
  });
  const stars = points.map((p) => `
    <a class="star${p.mag === 1 ? " major" : ""}${p.label.shown ? "" : " unlabelled"}" data-id="${esc(p.node.id)}" data-type="${esc(p.node.type)}"
       href="#/index/${encodeURIComponent(p.node.id)}" transform="translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})"
       aria-label="${esc(p.node.name)}, ${esc(p.node.type_label)}, named on ${Number(p.node.pages)} pages in ${Number(p.node.books)} books">
      <circle class="halo" r="${REACH[p.mag] + 5}"/>${GLYPH[p.mag]()}
      <text x="${p.label.tx.toFixed(1)}" y="${p.label.ty.toFixed(1)}" text-anchor="${p.label.anchor}">${esc(p.node.name)}</text>
    </a>`);
  const cut = (m) => points.filter((p) => p.mag === m).at(-1)?.node.pages ?? 0;
  const scale = [1, 2, 3, 4].filter((m) => points.some((p) => p.mag === m)).map((m, i) => `
    <g transform="translate(${PAD - 20 + i * 190} ${H - 32})">${GLYPH[m]()}
      <text x="16" y="4">${m === 4 ? "fewer" : `${cut(m).toLocaleString("en-US")} pages or more`}</text></g>`);
  return {
    points,
    svg: `<svg class="chart" viewBox="0 0 ${W} ${H}" role="group" aria-label="Chart of the names that share pages">
      <rect class="frame" x="14" y="14" width="${W - 28}" height="${H - 28}"/>
      <rect class="frame" x="20" y="20" width="${W - 40}" height="${H - 40}"/>
      <g>${joins.join("")}</g><g>${stars.join("")}</g>
      <g class="scale">${scale.join("")}</g>
    </svg>`,
  };
}
