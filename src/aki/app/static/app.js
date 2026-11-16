// The Felz Reading Room: a single-page app over the local AKI service.
// No build step and no third-party code. Everything is served from this computer.

import { roman, sunDevice, swell } from "./art.js";
import { drawChart } from "./chart.js";
import { HOLDS, clock, holdLabel, light, onLight, setHold, startLight } from "./light.js";

const main = document.getElementById("main");
const toastEl = document.getElementById("toast");
const cache = new Map();

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }) : "");
const fmt = (n) => Number(n || 0).toLocaleString("en-US");
const plural = (n, one, many = one + "s") => `${fmt(n)} ${n === 1 ? one : many}`;
const thumb = (pageId, w = 360) => `/api/pages/${encodeURIComponent(pageId)}/thumb?w=${w}`;
const pageImage = (pageId) => `/api/pages/${encodeURIComponent(pageId)}/image`;
const bookHref = (id, index, hl) => {
  const q = new URLSearchParams();
  if (index !== undefined && index !== null) q.set("p", index + 1);
  if (hl) q.set("hl", hl);
  const s = q.toString();
  return `#/book/${encodeURIComponent(id)}${s ? "?" + s : ""}`;
};
const lead = (left, right, cls = "") => `<span class="lead ${cls}">${left}<span class="dots"></span>${right}</span>`;
const list = (items) => (items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} &amp; ${items.at(-1)}`);

// Every navigation takes a number. A view whose answer arrives after the reader has moved
// on is dropped, so a slow page can never draw over a newer one.
let routeSeq = 0;
const STALE = Symbol("overtaken by a newer page");

async function api(path, options = {}) {
  const useCache = !options.method && !options.fresh;
  if (useCache && cache.has(path)) return cache.get(path);
  const started = routeSeq;
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!response.ok) {
    let detail = `The reading room answered ${response.status}.`;
    try {
      const told = (await response.json()).detail;
      if (typeof told === "string" && told) detail = told;
    } catch { /* keep default */ }
    throw new Error(detail);
  }
  const data = await response.json();
  if (useCache) cache.set(path, data);
  if (started !== routeSeq) throw STALE;
  return data;
}

function toast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => toastEl.classList.remove("show"), 2200);
}

const store = {
  get(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};

function render(html, title) {
  main.innerHTML = html;
  document.title = title ? `${title} · The Felz Reading Room` : "The Felz Reading Room";
}

function pageHead(label, title, lede = "") {
  return `<header class="page-head"><span class="label">${label}</span><h1>${title}</h1>
    ${lede ? `<p class="lede">${lede}</p>` : ""}${swell(200)}</header>`;
}

function failure(error) {
  render(`<div class="page"><div class="note"><strong>This page could not be opened.</strong>
    ${esc(error.message)} Check that the reading room is still running in its window, then reload.</div></div>`, "Unavailable");
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const area = Object.assign(document.createElement("textarea"), { value: text });
    document.body.append(area); area.select(); document.execCommand("copy"); area.remove();
  }
}

// ------------------------------------------------------------------ the light and the sundial
function setUpDial() {
  const button = document.getElementById("dial-btn");
  const menu = document.getElementById("dial-menu");
  document.getElementById("mark-device").innerHTML = sunDevice();
  document.getElementById("dial-device").innerHTML = sunDevice({ rays: false });
  document.getElementById("foot-rule").innerHTML = swell(180);
  const close = () => { menu.hidden = true; button.setAttribute("aria-expanded", "false"); };
  menu.innerHTML = HOLDS.map((value) => `<button type="button" data-hold="${value}"
      aria-pressed="false">${esc(holdLabel(value))}</button>`).join("") + `<p id="dial-note"></p>`;
  const draw = (state) => {
    document.getElementById("dial-label").textContent = state.label;
    button.title = `The light of the room: ${state.label.toLowerCase()}${state.hold === "sun" ? ", following your clock" : ", held"}`;
    // Updated in place: this runs every minute, and rebuilding would drop keyboard focus.
    menu.querySelectorAll("[data-hold]").forEach((choice) =>
      choice.setAttribute("aria-pressed", String(choice.dataset.hold === state.hold)));
    document.getElementById("dial-note").textContent = `The room is lit by your computer's clock: sunrise about ${clock(state.rise)}, sunset about ${clock(state.set)} in an ordinary northern year. It does not know where you are.`;
  };
  onLight(draw);
  button.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    button.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) menu.querySelector('[aria-pressed="true"]')?.focus();
  });
  menu.addEventListener("click", (event) => {
    const choice = event.target.closest("[data-hold]");
    if (choice) setHold(choice.dataset.hold);
  });
  document.querySelector(".dial").addEventListener("focusout", (event) => {
    if (!event.relatedTarget || !event.relatedTarget.closest(".dial")) close();
  });
  document.addEventListener("click", (event) => { if (!event.target.closest(".dial")) close(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) { close(); button.focus(); }
  });
}

// ------------------------------------------------------------------ books as objects
const BINDINGS = ["#22385F", "#5A2A2E", "#24443A", "#4F3A22", "#3A4256", "#2E2F4F", "#4A2F33", "#3B3A2A"];
function hash(text) { let h = 0; for (const c of text) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h; }
function spine(book) {
  const pages = Math.max(book.pages, 8);
  const height = Math.round(126 + Math.min(Math.log10(pages) * 46, 112));
  const width = Math.round(21 + Math.min(Math.sqrt(pages) * 1.1, 27));
  const tone = BINDINGS[hash(book.id) % BINDINGS.length];
  return `<a class="spine${book.pages ? "" : " waiting"}${book.joes ? " joes-spine" : ""}" href="${bookHref(book.id)}"
    style="--h:${height}px;--w:${width}px;--tone:${tone}"
    title="${esc(book.title)}${book.author ? ", " + esc(book.author) : ""}${book.pages ? "" : " (not yet read)"}">
    <span>${esc(book.title)}</span></a>`;
}

function cover(book, w = 360) {
  if (book.cover_page) {
    return `<div class="scan"><img loading="lazy" decoding="async" src="${thumb(book.cover_page, w)}"
      alt="First page of ${esc(book.title)}"></div>`;
  }
  return `<div class="scan"><div class="blank">${esc(book.title)}</div></div>`;
}

// ------------------------------------------------------------------ home: the book lies open
async function viewHome() {
  const [overview, books, memorial, plates] = await Promise.all([
    api("/api/overview"), api("/api/books"), api("/api/memorial"), api("/api/plates?limit=120")]);
  const joes = overview.joes_books[0];
  const last = store.get("felz.lastRead");
  const byCategory = new Map();
  for (const book of books) {
    if (!byCategory.has(book.category)) byCategory.set(book.category, []);
    byCategory.get(book.category).push(book);
  }
  const shelfNames = overview.categories.map(([name]) => esc(name));
  const year = roman(new Date().getFullYear());
  const today = new Date();
  let plateAt = plates.items.length
    ? (today.getFullYear() * 372 + today.getMonth() * 31 + today.getDate()) * 7 % plates.items.length : -1;
  const front = () => plates.items[plateAt] || null;
  const frontHtml = (p) => (p ? `<a href="#/page/${encodeURIComponent(p.page_id)}" aria-label="Open this plate in its book">
      <img id="front-img" src="${thumb(p.page_id, 900)}" alt="A plate from ${esc(p.title)}"></a>`
    : joes && joes.cover_page ? `<a href="${bookHref(joes.id)}"><img id="front-img" src="${thumb(joes.cover_page, 900)}"
        alt="First page of ${esc(joes.title)}"></a>` : `<div class="tp-device">${sunDevice()}</div>`);
  const capHtml = (p) => (p ? `From <i>${esc(p.title)}</i>, page ${esc(p.page_label)}.`
    : joes ? `<i>${esc(joes.title)}</i>` : "");

  const contents = [
    ["library", "The Library", `Every book on its shelf, ${list(shelfNames)}.`, plural(overview.books, "book")],
    ["search", "Search", "Find a word, a name or an idea on any page.", `${fmt(overview.passages)} pages`],
    ["index", "The Index", "People, places, books and ideas, and every page that names them.", overview.concordance.entities ? plural(overview.concordance.entities, "entry", "entries") : "not yet built"],
    ["chart", "The Chart", "Which names keep company on the same pages.", overview.concordance.mentions ? `${fmt(overview.concordance.mentions)} mentions` : "not yet built"],
    ["plates", "The Plates", "Engravings, maps, seals and diagrams.", overview.plates ? plural(overview.plates, "plate") : "not yet found"],
    ["research", "Research", "Ask a question; a model on this computer answers from the pages, every sentence cited and checked.", "cited to the page"],
    ["joe", "Joe Felz", "The man this room is kept open for.", esc(memorial.years)],
  ];

  render(`
    <section class="sky" aria-labelledby="tp-name">
      <div class="spread paper">
        <div class="leaf leaf-front">
          <div class="front-frame" id="front-frame">${frontHtml(front())}</div>
          <p class="front-cap"><span id="front-cap">${capHtml(front())}</span>
            ${plates.items.length > 1 ? `<br><button class="text-btn" id="front-turn" type="button">Turn to another plate</button>` : ""}</p>
        </div>
        <div class="leaf leaf-title">
          <p class="tp-the">THE</p>
          <h1 class="tp-name" id="tp-name">Felz <em>Reading Room</em></h1>
          ${swell(190)}
          <p class="tp-being">being a library of <b>${plural(overview.books, "book")}</b>
            of ${list(shelfNames)}, gathered by The Holy Word</p>
          <p class="tp-memory">and kept open in memory of</p>
          <p class="tp-joe">${esc(memorial.name)}</p>
          <p class="tp-years">${esc(memorial.years)}</p>
          <div class="tp-device">${sunDevice({ title: "A sun whose disc is ruled like a solar cell" })}</div>
          <form class="tp-search" role="search" id="tp-search">
            <label class="sr" for="tp-q">Search every page</label>
            <input class="line-input" id="tp-q" name="q" type="search" autocomplete="off"
              placeholder="Search ${fmt(overview.passages)} pages">
            <button class="btn btn-primary" type="submit">Search</button>
          </form>
          <p class="tp-imprint sc">Kept on this computer only · ${year}</p>
        </div>
      </div>
      <blockquote class="epigraph">“${esc(memorial.his_words.text)}”<cite>${esc(memorial.his_words.source)}</cite></blockquote>
    </section>
    <div class="home">
      <section aria-labelledby="contents">
        <div class="center-head"><span class="label">What is here</span><h2 id="contents">The Contents</h2>${swell(160)}</div>
        ${last ? `<p class="resume"><a href="${bookHref(last.book, last.index)}">Your place is kept:
          ${esc(last.title)}, page ${esc(last.label)}</a></p>` : ""}
        <ol class="contents">${contents.map(([to, title, gist, folio], i) => `
          <li><a href="#/${to}"><span class="numeral">${roman(i + 1)}.</span>
            ${lead(`<span class="entry-title">${title}</span>`, `<span class="folio">${folio}</span>`)}
            <span class="gist">${gist}</span></a></li>`).join("")}</ol>
      </section>
      ${joes ? `<section class="joes" aria-labelledby="joes-book">
        <a href="${bookHref(joes.id)}" aria-label="Open ${esc(joes.title)}">${cover(joes, 460)}</a>
        <div><span class="label">The book Joe showed them</span>
          <h2 id="joes-book">${esc(joes.title)}</h2>
          <p>${esc(memorial.joes_book_note)}</p>
          <a class="btn btn-primary" href="${bookHref(joes.id)}">Open the book</a></div>
      </section>` : ""}
      <section aria-labelledby="shelves">
        <div class="center-head"><span class="label">${plural(overview.readable_books, "book")} open to read${overview.waiting_books ? `, ${fmt(overview.waiting_books)} waiting` : ""}</span>
          <h2 id="shelves">The Shelves</h2>${swell(160)}</div>
        ${overview.categories.map(([name, count]) => `
          <div class="shelf">
            <div class="shelf-label lead"><a href="#/library?c=${encodeURIComponent(name)}">${esc(name)}</a>
              <span class="dots"></span><span class="folio">${plural(count, "book")}</span></div>
            <div class="spines">${(byCategory.get(name) || [])
              .sort((a, b) => (b.joes - a.joes) || b.downloads - a.downloads).map(spine).join("")}</div>
          </div>`).join("")}
      </section>
      <p class="colophon-line"><b>${fmt(overview.pages)}</b> pages have been photographed, and
        <b>${fmt(overview.passages)}</b> of them turned into text you can search; <b>${fmt(overview.ocr_pages)}</b> were read
        by a machine where the scan carried no text of its own.
        ${overview.waiting_books ? `<b>${plural(overview.waiting_books, "book")}</b> ${overview.waiting_books === 1 ? "is" : "are"} still waiting to be read.` : "Every book is open."}</p>
    </div>`);

  document.getElementById("tp-search").addEventListener("submit", (event) => {
    event.preventDefault();
    const q = new FormData(event.target).get("q").trim();
    if (q) location.hash = `#/search?q=${encodeURIComponent(q)}`;
  });
  const turn = document.getElementById("front-turn");
  if (turn) turn.addEventListener("click", () => {
    const image = document.getElementById("front-img");
    plateAt = (plateAt + 1 + Math.floor(Math.random() * (plates.items.length - 1))) % plates.items.length;
    image.classList.add("turning");
    setTimeout(() => {
      document.getElementById("front-frame").innerHTML = frontHtml(front());
      document.getElementById("front-cap").innerHTML = capHtml(front());
    }, 220);
  });
}

// ------------------------------------------------------------------ library
async function viewLibrary(params) {
  const books = await api("/api/books");
  const categories = [...new Set(books.map((b) => b.category))].sort();
  const state = { c: params.get("c") || "", q: params.get("q") || "", sort: params.get("sort") || "joe" };
  render(`<div class="page">
    ${pageHead("The Holy Word library", "The Library", `All ${fmt(books.length)} books. Titles are shown as they were listed, tidied only where they were file names.`)}
    <div class="filters">
      <div class="choices" role="group" aria-label="Shelf">
        <button data-c="" aria-pressed="${!state.c}">Every shelf</button>
        ${categories.map((c) => `<button data-c="${esc(c)}" aria-pressed="${state.c === c}">${esc(c)}</button>`).join("")}
      </div>
      <div class="row">
        <label class="sr" for="lib-q">Find a title or an author</label>
        <input class="line-input" id="lib-q" type="search" placeholder="Find a title or an author" value="${esc(state.q)}">
        <label class="sr" for="lib-sort">Order</label>
        <select class="line-input" id="lib-sort">
          <option value="joe">Joe's books first</option>
          <option value="title">By title</option>
          <option value="read">Most downloaded</option>
          <option value="pages">Longest first</option>
        </select>
      </div>
    </div>
    <div id="lib-grid"></div></div>`, "The Library");
  const grid = document.getElementById("lib-grid");
  const sortEl = document.getElementById("lib-sort");
  sortEl.value = state.sort;
  const draw = () => {
    const q = state.q.toLowerCase();
    let items = books.filter((b) => (!state.c || b.category === state.c)
      && (!q || `${b.title} ${b.author || ""} ${b.listed_title}`.toLowerCase().includes(q)));
    const sorters = {
      joe: (a, b) => (b.joes - a.joes) || b.downloads - a.downloads,
      title: (a, b) => a.title.localeCompare(b.title),
      read: (a, b) => b.downloads - a.downloads,
      pages: (a, b) => b.pages - a.pages,
    };
    items = items.sort(sorters[state.sort] || sorters.joe);
    grid.innerHTML = items.length ? `<div class="books">${items.map((b) => `
      <a class="book" href="${bookHref(b.id)}">
        ${cover(b)}
        <div><h3>${esc(b.title)}</h3>
        <p>${b.joes ? `<span class="pin">Joe's book</span><br>` : ""}${b.author ? esc(b.author) + "<br>" : ""}<i>${b.pages ? plural(b.pages, "page") : "waiting to be read"}</i></p></div>
      </a>`).join("")}</div>` : `<div class="note"><strong>No book matches.</strong>Clear the words above or choose another shelf.</div>`;
    const query = new URLSearchParams();
    if (state.c) query.set("c", state.c);
    if (state.q) query.set("q", state.q);
    if (state.sort !== "joe") query.set("sort", state.sort);
    history.replaceState(null, "", `#/library${query.toString() ? "?" + query : ""}`);
  };
  main.querySelectorAll("[data-c]").forEach((choice) => choice.addEventListener("click", () => {
    state.c = choice.dataset.c;
    main.querySelectorAll("[data-c]").forEach((c) => c.setAttribute("aria-pressed", String(c === choice)));
    draw();
  }));
  document.getElementById("lib-q").addEventListener("input", (e) => { state.q = e.target.value; draw(); });
  sortEl.addEventListener("change", (e) => { state.sort = e.target.value; draw(); });
  draw();
}

// ------------------------------------------------------------------ the reader
function tidy(text) {
  return String(text || "").replace(/[ \t ]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}
const LITTLE = new Set(("the and for that with this from his her its was were are not but have had has you your they"
  + " them their thou thee thy unto shall which who whom what when then than there here into upon all one").split(" "));
const wordPattern = (term) => {
  const words = (term || "").split(/\s+/).filter((w) => w.length > 2 && !LITTLE.has(w.toLowerCase()))
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return words.length ? new RegExp(`(?<![\\p{L}\\p{N}])(${words.join("|")})(?![\\p{L}\\p{N}])`, "giu") : null;
};
/** Marks whole words in plain text. The text is escaped piece by piece, so a mark can never split an entity. */
function highlight(text, term) {
  const pattern = wordPattern(term);
  if (!pattern) return esc(text);
  let out = "", last = 0;
  for (const match of String(text ?? "").matchAll(pattern)) {
    out += `${esc(text.slice(last, match.index))}<mark>${esc(match[0])}</mark>`;
    last = match.index + match[0].length;
  }
  return out + esc(text.slice(last));
}
/** Said beside an excerpt from a book that carries a curator's note. */
const noted = (flag) => (flag ? ` <span class="noted">see the note on this book</span>` : "");
/** The scan's lines joined back into paragraphs, with words broken at a line end mended. */
function reflow(text) {
  const lines = tidy(text).split("\n").map((line) => line.trim());
  const lengths = lines.map((l) => l.length).filter(Boolean).sort((a, b) => a - b);
  const full = lengths.length ? lengths[Math.floor(lengths.length * 0.85)] : 0;
  const blocks = [[]];
  lines.forEach((line) => {
    if (!line) { blocks.push([]); return; }
    blocks.at(-1).push(line);
    // A line that stops well short of the measure, at the end of a sentence, ends a paragraph.
    if (full > 30 && line.length < full * 0.72 && /[.!?:;"'”’)]$/.test(line)) blocks.push([]);
  });
  return blocks.filter((b) => b.length).map((b) => b.join("\n")
    .replace(/([A-Za-zÀ-ɏ])[-­‐]\n(?=[a-zà-ɏ])/g, "$1")
    .replace(/\s*\n\s*/g, " ").trim());
}
const byMachine = (page) => Boolean(page.engine) && !page.engine.startsWith("PyMuPDF");
/** A machine reading this unsure is mostly noise: say so, and do not build on it. */
const unsure = (page) => byMachine(page) && page.confidence != null && page.confidence < 0.8;

function sourceNote(page) {
  if (!page.engine) return "This page has no transcribed text.";
  if (page.engine.startsWith("PyMuPDF-text-layer")) return "This text came with the scan. Older scans often carry an earlier machine reading, so compare it with the page.";
  if (page.engine.startsWith("PyMuPDF-epub")) return "This text is taken from the EPUB itself; the page breaks are this archive's.";
  const confidence = page.confidence != null ? `, average confidence ${Math.round(page.confidence * 100)} in 100` : "";
  if (unsure(page)) return `A machine tried to read this page (${esc(page.engine)} ${esc(page.engine_version || "")}${confidence}) and was unsure of it. Black-letter type, a turned page or a faint scan defeats it. Trust the picture, not these words.`;
  return `Read by a machine (${esc(page.engine)} ${esc(page.engine_version || "")}${confidence}) and not yet checked by a person. Expect misread letters.`;
}
const sourceShort = (page) => (!page.engine ? "No text" : page.engine.startsWith("PyMuPDF-text-layer") ? "Text from the scan"
  : page.engine.startsWith("PyMuPDF-epub") ? "Text from the EPUB" : unsure(page) ? "Read by machine, unsurely" : "Read by machine");

async function viewBook(id, params) {
  const book = await api(`/api/books/${encodeURIComponent(id)}`);
  const warning = book.note ? `<div class="warn"><span class="label">A note on this book</span>${esc(book.note)}</div>` : "";
  if (!book.page_list.length) {
    render(`<div class="page">${pageHead(esc(book.category), esc(book.title), book.author ? esc(book.author) : "")}${warning}
      <div class="note"><strong>This book has not been read into the archive yet.</strong>
      Its original file is safe in the vault. It will open here after the next run of
      “Finish the reading room”.</div></div>`, book.title);
    return;
  }
  let hl = params.get("hl") || "";
  const total = book.page_list.length;
  // Links carry the page's own index; where it stands in the list is looked up, not assumed.
  const place = (pageIndex) => {
    const at = book.page_list.findIndex((p) => p.index === pageIndex);
    return at >= 0 ? at : Math.min(Math.max(pageIndex, 0), total - 1);
  };
  let index = place((parseInt(params.get("p"), 10) || 1) - 1);
  let asSet = store.get("felz.asSet") === true;
  const plateTicks = book.page_list.filter((p) => p.plate)
    .map((p) => `<i style="left:${total > 1 ? ((p.index / (total - 1)) * 100).toFixed(2) : 0}%"></i>`).join("");
  const plateCount = book.page_list.filter((p) => p.plate).length;

  render(`<div class="page reader-page">
    ${warning}
    <header class="book-head">
      <div><h1>${esc(book.title)}</h1>
        <p class="by">${book.author ? esc(book.author) + ". " : ""}${esc(book.category)}, ${plural(book.pages, "page")}${plateCount ? `, ${plural(plateCount, "plate")}` : ""}.
        ${book.description ? ` “${esc(book.description)}” <span class="sc">The Holy Word</span>` : ""}</p></div>
      <div class="turner">
        <button class="btn" id="prev" type="button">Previous</button>
        <label for="goto">Page</label>
        <input id="goto" type="number" min="1" max="${total}" inputmode="numeric" aria-describedby="of">
        <span id="of" class="muted it">of ${fmt(total)}</span>
        <button class="btn" id="next" type="button">Next</button>
      </div>
    </header>
    <div class="ribbon">
      <div class="ticks" aria-hidden="true">${plateTicks}</div>
      <label class="sr" for="scrub">Move through the book</label>
      <input id="scrub" type="range" min="1" max="${total}" step="1">
      ${plateCount ? `<div class="legend"><span>The gold marks are plates.</span><span>Arrow keys turn the page.</span></div>` : ""}
    </div>
    <div class="reader">
      <div class="plate-side" id="view">
        <div class="scan" id="paper"></div>
        <div class="under"><span id="scan-note"></span><button class="text-btn" id="zoom" type="button">Look closer</button></div>
      </div>
      <article aria-label="Transcription">
        <div class="running"><span class="label" id="run-left">Transcription</span><span class="label" id="run-right"></span></div>
        <div id="transcript"></div>
        <section class="in-book" aria-label="Search inside this book">
          <form id="book-search" role="search">
            <label class="sr" for="book-q">Search inside this book</label>
            <input class="line-input" id="book-q" type="search" placeholder="Search inside this book" value="${esc(hl)}">
            <button class="btn" type="submit">Find</button>
          </form>
          <div id="book-hits"></div>
        </section>
      </article>
      <aside class="margin" id="margin" aria-label="Side-notes for this page"></aside>
    </div>
  </div>`, book.title);

  const paper = document.getElementById("paper");
  const view = document.getElementById("view");
  const transcript = document.getElementById("transcript");
  const margin = document.getElementById("margin");
  const scrub = document.getElementById("scrub");
  const goto = document.getElementById("goto");
  const zoomBtn = document.getElementById("zoom");
  const setZoom = (on) => { view.classList.toggle("zoomed", on); zoomBtn.textContent = on ? "Step back" : "Look closer"; };
  zoomBtn.addEventListener("click", () => setZoom(!view.classList.contains("zoomed")));
  paper.addEventListener("click", (event) => {
    if (view.classList.contains("zoomed")) { setZoom(false); return; }
    const box = paper.getBoundingClientRect();
    const fx = (event.clientX - box.left) / box.width, fy = (event.clientY - box.top + paper.scrollTop) / paper.scrollHeight;
    setZoom(true);
    paper.scrollLeft = fx * paper.scrollWidth - paper.clientWidth / 2;
    paper.scrollTop = fy * paper.scrollHeight - paper.clientHeight / 2;
  });
  let token = 0;

  function drawText(page) {
    if (!page.text) {
      transcript.innerHTML = `<p class="no-text">${page.plate ? "This page is a plate: a picture, not prose." : "No text was found on this page."}</p>
        <p class="source">${sourceNote(page)}</p>
        <div class="actions"><button class="btn" id="cite" type="button">Copy the citation</button></div>`;
    } else {
      const body = asSet ? `<div class="transcript-text as-set" tabindex="0" lang="en">${highlight(tidy(page.text), hl)}</div>`
        : `<div class="transcript-text${!unsure(page) && /^[A-Z][a-z]/.test(page.text.trim()) ? " dropcap" : ""}${unsure(page) ? " unsure" : ""}" tabindex="0" lang="en">${reflow(page.text).map((p) => `<p>${highlight(p, hl)}</p>`).join("")}</div>`;
      transcript.innerHTML = `${body}
        <p class="source">${sourceNote(page)}</p>
        <div class="actions">
          <button class="btn" id="cite" type="button">Copy the citation</button>
          <button class="btn" id="copy" type="button">Copy the text</button>
          <button class="text-btn" id="set" type="button">${asSet ? "Join the lines into paragraphs" : "Show the lines as the page sets them"}</button>
        </div>`;
      document.getElementById("copy").addEventListener("click", async () => { await copy(page.text); toast("Text copied"); });
      document.getElementById("set").addEventListener("click", () => { asSet = !asSet; store.set("felz.asSet", asSet); drawText(page); });
    }
    document.getElementById("cite").addEventListener("click", async () => { await copy(page.citation); toast("Citation copied"); });
  }

  async function drawMargin(entry, mine, page) {
    let gloss;
    try { gloss = await api(`/api/pages/${encodeURIComponent(entry.id)}/gloss`); } catch { return; }
    if (mine !== token) return;
    const parts = [];
    if (gloss.names.length) {
      parts.push(`<section><h2>Named on this page</h2><ul class="names">${gloss.names.map((n) => `
        <li><a href="#/index/${encodeURIComponent(n.id)}">${esc(n.canonical_name)}</a></li>`).join("")}</ul></section>`);
    }
    if (unsure(page)) {
      parts.push(`<section><h2>Elsewhere in the library</h2><p class="aside">The machine's reading of this page is too unsure to set beside other books.</p></section>`);
    } else if (gloss.elsewhere.length) {
      parts.push(`<section><h2>Elsewhere in the library</h2><ul class="near">${gloss.elsewhere.map((e) => `
        <li><a href="${bookHref(e.book.id, e.page_index)}"><b>${esc(e.book.title)}, p. ${esc(e.page_label)}</b>
          <q>${esc(e.excerpt)}</q></a>${noted(e.book.noted)}</li>`).join("")}</ul>
        <p class="aside">Found by likeness of meaning, by a machine. Near is not the same as in agreement.</p></section>`);
    }
    if (!parts.length && entry.text) {
      parts.push(`<section><h2>Side-notes</h2><p class="aside">${gloss.meaning ? "No index name is on this page, and nothing elsewhere reads like it." : "The index and the meaning search are built by “Finish the reading room”. Side-notes will appear here afterwards."}</p></section>`);
    }
    margin.innerHTML = parts.join("");
  }

  async function show(i, { keepScroll = false } = {}) {
    index = i;
    const mine = ++token;
    const entry = book.page_list[i];
    goto.value = String(i + 1);
    scrub.value = String(i + 1);
    document.getElementById("prev").disabled = i === 0;
    document.getElementById("next").disabled = i === total - 1;
    setZoom(false);
    paper.innerHTML = `<img src="${pageImage(entry.id)}" alt="Scan of page ${esc(entry.label)} of ${esc(book.title)}" title="Click to look closer">`;
    document.getElementById("run-right").textContent = `Page ${entry.label}`;
    document.getElementById("scan-note").textContent = entry.plate ? "A plate" : "";
    transcript.innerHTML = `<p class="loading">Turning to page ${esc(entry.label)}…</p>`;
    const q = new URLSearchParams({ p: String(entry.index + 1) });
    if (hl) q.set("hl", hl);
    history.replaceState(null, "", `#/book/${encodeURIComponent(book.id)}?${q}`);
    store.set("felz.lastRead", { book: book.id, title: book.title, index: entry.index, label: entry.label });
    if (!keepScroll && window.scrollY > 260) window.scrollTo({ top: 0 });
    margin.innerHTML = "";
    let page;
    try { page = await api(`/api/pages/${encodeURIComponent(entry.id)}`); }
    catch (error) {
      if (error !== STALE && mine === token) {
        transcript.innerHTML = `<p class="no-text">This page could not be opened. ${esc(error.message)}
          Check that the reading room is still running, then turn the page again.</p>`;
      }
      return;
    }
    if (mine !== token) return;
    drawMargin(entry, mine, page);
    document.getElementById("run-left").textContent = sourceShort(page);
    drawText(page);
    const nextEntry = book.page_list[i + 1];
    if (nextEntry) { const img = new Image(); img.src = pageImage(nextEntry.id); }
  }
  const step = (by) => { const to = index + by; if (to >= 0 && to < total) show(to); };
  document.getElementById("prev").addEventListener("click", () => step(-1));
  document.getElementById("next").addEventListener("click", () => step(1));
  goto.addEventListener("change", () => {
    const to = Math.min(Math.max(parseInt(goto.value, 10) || 1, 1), total) - 1;
    show(to);
  });
  let scrubTimer = 0;
  scrub.addEventListener("input", () => {
    goto.value = scrub.value;
    clearTimeout(scrubTimer);
    scrubTimer = setTimeout(() => show(Number(scrub.value) - 1, { keepScroll: true }), 140);
  });
  keyHandler = (event) => {
    if (event.target.matches("input, textarea, select") || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.key === "ArrowRight") step(1);
    if (event.key === "ArrowLeft") step(-1);
  };
  document.getElementById("book-search").addEventListener("submit", async (event) => {
    event.preventDefault();
    const q = document.getElementById("book-q").value.trim();
    const box = document.getElementById("book-hits");
    if (!q) { box.innerHTML = ""; return; }
    box.innerHTML = `<p class="loading">Looking through this book…</p>`;
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(q)}&mode=words&book=${encodeURIComponent(book.id)}&limit=40`);
      box.innerHTML = data.results.length ? `<ul class="passages" style="margin-top:1.6rem">${data.results
        .sort((a, b) => a.page_index - b.page_index).map((r) => `
        <li><a href="${bookHref(book.id, r.page_index, q)}" data-i="${Number(r.page_index)}">
          <span class="where">Page ${esc(r.page_label)}</span><p>${r.snippet}</p></a></li>`).join("")}</ul>`
        : `<p class="muted it" style="margin-top:1rem">No page in this book has “${esc(q)}”.</p>`;
      box.querySelectorAll("a[data-i]").forEach((a) => a.addEventListener("click", (e) => {
        e.preventDefault(); hl = q; show(place(Number(a.dataset.i)));
      }));
    } catch (error) { box.innerHTML = `<p class="muted it">${esc(error.message)}</p>`; }
  });
  show(index, { keepScroll: true });
}

// ------------------------------------------------------------------ search
async function viewSearch(params) {
  const q = params.get("q") || "";
  let mode = params.get("mode") || "both";
  let category = params.get("c") || "";
  const overview = await api("/api/overview");
  const meaningReady = Boolean(overview.semantic);
  render(`<div class="page">
    ${pageHead(`${fmt(overview.passages)} pages of text`, "Search")}
    <form class="ask" id="search-form" role="search">
      <div><label class="sr" for="q">Search every page</label>
      <input class="line-input" id="q" name="q" type="search" value="${esc(q)}" placeholder="A name, a phrase, an idea" autocomplete="off"></div>
      <button class="btn btn-primary" type="submit">Search</button>
    </form>
    <div class="modes">
      <div class="choices" role="group" aria-label="How to match">
        <button type="button" data-mode="words" aria-pressed="${(meaningReady ? mode : "words") === "words"}">The exact words</button>
        <button type="button" data-mode="meaning" aria-pressed="${meaningReady && mode === "meaning"}" ${meaningReady ? "" : "disabled"}>The meaning</button>
        <button type="button" data-mode="both" aria-pressed="${meaningReady && mode === "both"}" ${meaningReady ? "" : "disabled"}>Both</button>
      </div>
      <label class="it">on <select class="line-input" id="cat">
        <option value="">every shelf</option>
        ${overview.categories.map(([c]) => `<option value="${esc(c)}" ${c === category ? "selected" : ""}>the ${esc(c)} shelf</option>`).join("")}
      </select></label>
      ${meaningReady ? "" : `<span class="it">Meaning search is built by “Finish the reading room”. Until then, search matches the exact words.</span>`}
    </div>
    <div id="results">${q ? `<p class="loading">Reading…</p>` : `<div class="note"><strong>Try a name or an idea.</strong>
      “The exact words” finds what you type. “The meaning” also finds pages that say the same thing in other words,
      which helps with old spellings. “Both” weighs the two together.</div>`}</div></div>`, q ? `Search: ${q}` : "Search");
  const go = (next) => {
    const query = new URLSearchParams({ q: next.q ?? q, mode });
    if (category) query.set("c", category);
    location.hash = `#/search?${query}`;
  };
  const field = document.getElementById("q");
  if (!q) field.focus({ preventScroll: true });
  document.getElementById("search-form").addEventListener("submit", (e) => {
    e.preventDefault(); const value = field.value.trim(); if (value) go({ q: value });
  });
  main.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
    mode = b.dataset.mode;
    if (q) go({});
    else main.querySelectorAll("[data-mode]").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
  }));
  document.getElementById("cat").addEventListener("change", (e) => { category = e.target.value; if (q) go({}); });
  if (!q) return;
  const effective = meaningReady ? mode : "words";
  const data = await api(`/api/search?q=${encodeURIComponent(q)}&mode=${effective}&limit=40${category ? "&category=" + encodeURIComponent(category) : ""}`);
  const label = { words: "by its words", meaning: "by its meaning" };
  const trouble = data.notes && data.notes.length
    ? `<p class="tally">The meaning search could not be used just now, so these match the words only. (${esc(data.notes.join(" "))})</p>` : "";
  document.getElementById("results").innerHTML = trouble + (data.results.length ? `
    <p class="tally">${plural(data.results.length, "page")} for “${esc(q)}”${category ? ` on the ${esc(category)} shelf` : ""}, the nearest first.</p>
    <ol class="results">${data.results.map((r) => `
      <li class="hit">
        <a href="${bookHref(r.book.id, r.page_index, q)}" tabindex="-1" aria-hidden="true">
          <div class="scan"><img loading="lazy" src="${thumb(r.page_id, 160)}" alt=""></div></a>
        <div>
          <h3><a href="${bookHref(r.book.id, r.page_index, q)}">${esc(r.book.title)}</a></h3>
          <p class="where">Page ${esc(r.page_label)}${r.book.author ? ` · ${esc(r.book.author)}` : ""}
            <span class="by-leg"> · found ${r.found_by.map((leg) => label[leg]).join(" and ")}</span>${noted(r.book.noted)}</p>
          <p class="snippet">${r.snippet}</p>
        </div>
      </li>`).join("")}</ol>`
    : `<div class="note"><strong>Nothing found for “${esc(q)}”.</strong>
       Old books spell things differently. Try a shorter word, another spelling${meaningReady ? ", or search by the meaning" : ""}.</div>`);
}

// ------------------------------------------------------------------ the index
function notBuilt(title, what) {
  render(`<div class="page">${pageHead("Not yet built", title)}<div class="note"><strong>${what}</strong>
    It is made by “Finish the reading room”, after the books have been read.</div></div>`, title);
}

async function viewIndex() {
  const entries = await api("/api/index");
  if (!entries.length) { notBuilt("The Index", "The index has not been built yet."); return; }
  const groups = new Map();
  for (const entry of entries) {
    if (!groups.has(entry.type_label)) groups.set(entry.type_label, []);
    groups.get(entry.type_label).push(entry);
  }
  render(`<div class="page">
    ${pageHead(`${plural(entries.length, "entry", "entries")}`, "The Index", `The people, places, books, traditions and ideas that run through the library, and the number of pages that name each one. It was made by matching a list of names against the text, so a name is missed wherever the machine misread it.`)}
    <p style="text-align:center;margin:-1rem 0 3rem"><a class="btn" href="#/chart">See the names as a chart</a></p>
    ${[...groups].map(([label, items]) => `
      <section class="index-section"><div class="center-head"><h2 class="sub">${esc(label)}</h2></div>
        <ul class="index-cols">${items.sort((a, b) => a.canonical_name.localeCompare(b.canonical_name)).map((e) => `
          <li><a class="lead ${e.pages ? "" : "quiet"}" href="#/index/${encodeURIComponent(e.id)}"><span class="n">${esc(e.canonical_name)}</span><span class="dots"></span><span class="folio">${fmt(e.pages)}</span></a></li>`).join("")}
        </ul></section>`).join("")}</div>`, "The Index");
}

async function viewEntry(id, params) {
  const offset = Math.max(0, parseInt(params.get("o"), 10) || 0);
  const d = await api(`/api/index/${encodeURIComponent(id)}?offset=${offset}`);
  const said = (p) => String(p.mention_text || d.aliases[0] || "").replace(/\s*\(\d+×\)$/, "");
  const also = d.aliases.filter((a) => a !== d.entity.canonical_name);
  render(`<div class="page">
    ${pageHead(`<a href="#/index">The Index</a> · ${esc(d.entity.type_label)}`, esc(d.entity.canonical_name),
      `Named on ${plural(d.pages, "page")} in ${plural(d.book_count ?? d.books.length, "book")}${also.length ? `; also as ${also.map(esc).join(", ")}` : ""}.`)}
    <div class="entry">
      <div>${d.passages.length ? `<ul class="passages">${d.passages.map((p) => `
        <li><a href="${bookHref(p.edition_id, p.page_index, said(p))}">
          <span class="where">${esc(p.title)} · page ${esc(p.printed_page_label || p.page_index + 1)}</span>
          <p>${highlight(p.excerpt, said(p))}</p></a>${noted(p.noted)}</li>`).join("")}</ul>
        <div class="pager">
          ${offset > 0 ? `<a class="btn" href="#/index/${encodeURIComponent(id)}?o=${Math.max(0, offset - 60)}">Earlier pages</a>` : ""}
          <span class="count">${fmt(offset + 1)} to ${fmt(Math.min(offset + 60, d.pages))} of ${fmt(d.pages)}</span>
          ${offset + 60 < d.pages ? `<a class="btn" href="#/index/${encodeURIComponent(id)}?o=${offset + 60}">Later pages</a>` : ""}
        </div>` : `<div class="note">No page names ${esc(d.entity.canonical_name)} yet.</div>`}</div>
      <aside class="side">
        ${d.books.length ? `<section><h2>${(d.book_count ?? 0) > d.books.length ? `In these books, and ${fmt(d.book_count - d.books.length)} more` : "In these books"}</h2>
        <ol>${d.books.map((b) => `<li class="lead"><a href="${bookHref(b.edition_id)}">${esc(b.title)}</a><span class="dots"></span><span class="folio">${fmt(b.pages)}</span></li>`).join("")}</ol></section>` : ""}
        ${d.related.length ? `<section><h2>Often on the same page</h2>
        <p class="aside">Shared pages only. This says the names stand together, not how they are related.</p>
        <ul>${d.related.map((r) => `<li class="lead"><a href="#/index/${encodeURIComponent(r.id)}">${esc(r.canonical_name)}</a><span class="dots"></span><span class="folio">${fmt(r.shared)}</span></li>`).join("")}</ul></section>` : ""}
      </aside>
    </div></div>`, d.entity.canonical_name);
}

// ------------------------------------------------------------------ the chart
async function viewChart() {
  const data = await api("/api/chart");
  if (!data.nodes.length) { notBuilt("The Chart", "The chart is drawn from the index, which has not been built yet."); return; }
  const types = [...new Map(data.nodes.map((n) => [n.type, n.type_label])).entries()];
  const { svg } = drawChart(data, esc);
  const idle = `<i>Rest on a name to see the company it keeps. Choose it to open every page that names it.</i>`;
  render(`<div class="page">
    ${pageHead(`${plural(data.nodes.length, "name")} · ${plural(data.links.length, "join")}`, "The Chart",
      `Every star is a name from the index; the more pages name it, the greater the star. A line joins two names that often stand on the same page. It shows who keeps company, and nothing about why.`)}
    <div class="choices" role="group" aria-label="Which names to light" style="margin-bottom:1.4rem">
      <button type="button" data-type="" aria-pressed="true">Every name</button>
      ${types.map(([type, label]) => `<button type="button" data-type="${esc(type)}" aria-pressed="false">${esc(label)}</button>`).join("")}
    </div>
    <div class="chart-wrap">${svg}</div>
    <p class="chart-read" id="chart-read" aria-live="polite">${idle}</p>
    <p style="text-align:center;margin-top:1.5rem"><a class="btn" href="#/index">The same names as a list</a></p>
  </div>`, "The Chart");

  const chart = main.querySelector(".chart");
  const read = document.getElementById("chart-read");
  const byId = new Map(data.nodes.map((n) => [n.id, n]));
  const stars = new Map([...chart.querySelectorAll(".star")].map((el) => [el.dataset.id, el]));
  const joins = [...chart.querySelectorAll(".join")];
  let type = "";
  const clear = () => {
    chart.querySelectorAll(".lit, .focus").forEach((el) => el.classList.remove("lit", "focus"));
    if (type) {
      chart.classList.add("dim");
      stars.forEach((el) => el.classList.toggle("lit", el.dataset.type === type));
    } else chart.classList.remove("dim");
    read.innerHTML = idle;
  };
  const focus = (id) => {
    const node = byId.get(id);
    if (!node) return;
    chart.classList.add("dim");
    chart.querySelectorAll(".lit, .focus").forEach((el) => el.classList.remove("lit", "focus"));
    stars.get(id).classList.add("lit", "focus");
    const company = [];
    for (const line of joins) {
      const other = line.dataset.a === id ? line.dataset.b : line.dataset.b === id ? line.dataset.a : null;
      if (!other) continue;
      line.classList.add("lit");
      stars.get(other)?.classList.add("lit");
      const link = data.links.find((l) => (l.a === id && l.b === other) || (l.b === id && l.a === other));
      company.push([byId.get(other).name, link ? link.shared : 0]);
    }
    company.sort((a, b) => b[1] - a[1]);
    read.innerHTML = `<b>${esc(node.name)}</b> <i>(${esc(node.type_label.toLowerCase())})</i> is named on ${plural(node.pages, "page")} in ${plural(node.books, "book")}.`
      + (company.length ? ` It shares pages with ${list(company.slice(0, 6).map(([name, shared]) => `${esc(name)} <i>(${fmt(shared)})</i>`))}.` : "");
  };
  stars.forEach((el, id) => {
    el.addEventListener("pointerenter", () => focus(id));
    el.addEventListener("focus", () => focus(id));
    el.addEventListener("pointerleave", clear);
    el.addEventListener("blur", clear);
  });
  main.querySelectorAll("[data-type]").forEach((b) => b.addEventListener("click", () => {
    type = b.dataset.type;
    main.querySelectorAll("button[data-type]").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
    clear();
  }));
}

// ------------------------------------------------------------------ plates
async function viewPlates(params) {
  const offset = Math.max(0, parseInt(params.get("o"), 10) || 0);
  const data = await api(`/api/plates?offset=${offset}&limit=60`);
  if (!data.total) { notBuilt("The Plates", "No plates have been found yet."); return; }
  render(`<div class="page">
    ${pageHead(plural(data.total, "plate"), "The Plates", `Engravings, maps, seals and diagrams: pages that are pictures and not prose. A machine found them by measuring each scan, so a few are stains or blank leaves.`)}
    <div class="plates">${data.items.map((p, i) => `
      <button type="button" data-i="${i}">
        <div class="scan"><img loading="lazy" src="${thumb(p.page_id, 360)}" alt="Plate from ${esc(p.title)}, page ${esc(p.page_label)}"></div>
        <span>${esc(p.title)}, page ${esc(p.page_label)}</span></button>`).join("")}</div>
    <div class="pager">
      ${offset > 0 ? `<a class="btn" href="#/plates?o=${Math.max(0, offset - 60)}">Earlier plates</a>` : ""}
      <span class="count">${fmt(offset + 1)} to ${fmt(Math.min(offset + 60, data.total))} of ${fmt(data.total)}</span>
      ${offset + 60 < data.total ? `<a class="btn" href="#/plates?o=${offset + 60}">More plates</a>` : ""}
    </div>
    <dialog class="viewer" id="viewer" aria-label="Plate"></dialog>
  </div>`, "The Plates");
  const viewer = document.getElementById("viewer");
  let at = 0;
  const open = (i, keep = "") => {
    at = (i + data.items.length) % data.items.length;
    const p = data.items[at];
    viewer.innerHTML = `<figure>
      <img src="${thumb(p.page_id, 900)}" alt="Plate from ${esc(p.title)}, page ${esc(p.page_label)}">
      <figcaption><span><i>${esc(p.title)}</i>, page ${esc(p.page_label)}</span>
        <button class="btn" type="button" data-step="-1">Previous</button>
        <a class="btn" href="#/page/${encodeURIComponent(p.page_id)}">Open in the book</a>
        <button class="btn" type="button" data-step="1">Next</button>
        <button class="btn" type="button" data-close>Close</button></figcaption></figure>`;
    if (!viewer.open) viewer.showModal();
    if (keep) viewer.querySelector(`[data-step="${keep}"]`)?.focus();
  };
  main.querySelectorAll(".plates button").forEach((b) => b.addEventListener("click", () => open(Number(b.dataset.i))));
  viewer.addEventListener("click", (event) => {
    const stepBtn = event.target.closest("[data-step]");
    if (stepBtn) open(at + Number(stepBtn.dataset.step), stepBtn.dataset.step);
    else if (event.target.closest("[data-close]") || event.target === viewer) viewer.close();
  });
  keyHandler = (event) => {
    if (!viewer.open) return;
    if (event.key === "ArrowRight") open(at + 1);
    if (event.key === "ArrowLeft") open(at - 1);
  };
}

async function viewPage(pageId) {
  const page = await api(`/api/pages/${encodeURIComponent(pageId)}`);
  location.replace(bookHref(page.edition.id, page.index));
}

// ------------------------------------------------------------------ research
async function viewResearch(params) {
  let earlier = [];
  try { earlier = await api("/api/research", { fresh: true }); } catch { /* the list is a convenience */ }
  const seen = new Set();
  earlier = earlier.filter((c) => c.question && !seen.has(c.question) && seen.add(c.question)).slice(0, 8);
  const asked = params.get("q") || "";
  render(`<div class="page">
    ${pageHead("Answers, cited to the page", "Research", `Ask a question. A language model on this computer reads the pages the archive gathers and answers from them alone. Every sentence is checked against the page it cites, and any sentence that fails is withheld.`)}
    <form class="ask ask-answer" id="research-form">
      <div><label class="sr" for="question">Your question</label>
      <input class="line-input" id="question" maxlength="400" value="${esc(asked)}" placeholder="What do these books say about the Watchers?" autocomplete="off"></div>
      <div class="ask-actions">
        <button class="btn btn-primary" type="submit" data-depth="quick" id="ask-quick">Answer</button>
        <button class="btn" type="button" data-depth="deep" id="ask-deep">Think harder</button>
      </div>
      <p class="ask-models" id="ask-models"></p>
      <button class="text-btn ask-gather" type="button" id="ask-gather">Only gather the pages, without an answer</button>
    </form>
    <div id="research-out"></div>
    ${earlier.length ? `<section class="earlier" aria-labelledby="earlier"><div class="center-head"><h2 class="sub" id="earlier">Asked before</h2></div>
      <ul>${earlier.map((c) => `<li class="lead"><button class="text-btn" type="button" data-q="${esc(c.question)}">${esc(c.question)}</button><span class="dots"></span><span class="muted it">${esc(day(c.created_at))}</span></li>`).join("")}</ul></section>` : ""}
  </div>`, "Research");
  const out = document.getElementById("research-out");
  const field = document.getElementById("question");
  const buttons = { quick: document.getElementById("ask-quick"), deep: document.getElementById("ask-deep") };
  let models = null;
  api("/api/answer/models", { fresh: true }).then((m) => {
    models = m;
    const line = document.getElementById("ask-models");
    if (!line) return;
    const by = Object.fromEntries(m.models.map((x) => [x.depth, x]));
    for (const [depth, button] of Object.entries(buttons)) {
      const choice = by[depth];
      const ready = m.running && choice?.installed;
      button.disabled = !ready;
      button.title = choice ? `${choice.label}. ${choice.note}` : "";
    }
    line.innerHTML = !m.running
      ? `The local model service (Ollama) is not running, so answers cannot be written. Start it and reload; gathering the pages still works.`
      : m.models.map((x) => `<span>${esc(x.depth === "quick" ? "Answer" : "Think harder")}: ${esc(x.label)}${x.installed ? "" : ` <em>(not installed: <code>ollama pull ${esc(x.model)}</code>)</em>`}</span>`).join(" · ")
        + ` <span class="muted">· runs on this computer; nothing is sent away</span>`;
  }).catch(() => { /* the buttons stay usable; the server will explain */ });

  const answer = async (question, depth) => {
    if (question.length < 8) { out.innerHTML = `<p class="tally" style="margin-top:2rem">Write a question of at least a few words.</p>`; return; }
    const started = routeSeq;
    const label = models?.models.find((m) => m.depth === depth)?.label || "The model";
    const t0 = performance.now();
    let stage = "Gathering the pages…";
    const draw = () => {
      if (started !== routeSeq) return;
      const secs = Math.round((performance.now() - t0) / 1000);
      out.innerHTML = `<p class="loading answer-stage" role="status">${esc(stage)}<small>${secs ? `${secs} s` : ""}</small></p>`;
    };
    draw();
    const tick = setInterval(draw, 1000);
    Object.values(buttons).forEach((b) => { b.dataset.wasDisabled = b.disabled; b.disabled = true; });
    try {
      const response = await fetch("/api/answer", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, depth }),
      });
      if (!response.ok) {
        let detail = `The reading room answered ${response.status}.`;
        try { detail = (await response.json()).detail || detail; } catch { /* keep default */ }
        throw new Error(detail);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let result = null;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, cut).trim();
          buffer = buffer.slice(cut + 1);
          if (!line) continue;
          const event = JSON.parse(line);
          if (event.type === "passages") stage = event.count ? `Found ${plural(event.count, "passage")} in ${plural(event.books, "book")}. ${label} is reading them…` : "No page bears on this question.";
          else if (event.type === "progress") stage = `${label} is writing, about ${plural(event.words, "word")} so far…`;
          else if (event.type === "stage" && event.stage === "checking") stage = "Checking every sentence against the page it cites…";
          else if (event.type === "error") throw new Error(event.detail);
          else if (event.type === "answer") result = event;
        }
      }
      clearInterval(tick);
      if (started !== routeSeq) return;
      if (!result) throw new Error("The answer ended before it was finished. Ask again.");
      showAnswer(result);
    } catch (error) {
      clearInterval(tick);
      if (started === routeSeq) out.innerHTML = `<div class="note">${esc(error.message)}</div>`;
    } finally {
      Object.values(buttons).forEach((b) => { b.disabled = b.dataset.wasDisabled === "true"; });
    }
  };

  const showAnswer = (r) => {
    const cited = r.sources.filter((s) => s.cited);
    const books = new Set(cited.map((s) => s.book.id));
    const cite = (n) => `<button type="button" class="cite" data-src="${n}" aria-label="Passage ${n}">${n}</button>`;
    const body = r.paragraphs.map((para) => `<p>${para.map((s) => `<span class="said${s.noted ? " leans-on-note" : ""}">${esc(s.text)}${s.cites.length ? `<sup>${s.cites.map(cite).join("")}</sup>` : ""}${s.noted ? `<span class="noted-mark" title="This sentence draws on a book with a curator's note; read the note under its passage.">†</span>` : ""}</span>`).join(" ")}</p>`).join("");
    const withheld = r.withheld.length ? `<details class="withheld"><summary>${plural(r.withheld.length, "sentence")} withheld: the model wrote ${r.withheld.length === 1 ? "it" : "them"}, but the check could not find ${r.withheld.length === 1 ? "it" : "them"} on the cited page</summary>
        <ul>${r.withheld.map((w) => `<li><q>${esc(w.text)}</q> <span class="why">${esc(w.reason)}</span></li>`).join("")}</ul></details>` : "";
    out.innerHTML = `
      <article class="answer paper" aria-labelledby="answer-head">
        <h2 class="sr" id="answer-head">The answer</h2>
        ${r.answered ? body : `<p class="verdict">${r.sources.length ? "The passages the archive found do not answer this question." : "No page in the library bears on this question."}<small>This is a gap in what was found, not a finding about the past.</small></p>`}
        <p class="answer-colophon">Written by ${esc(r.model_label)} on this computer${r.seconds ? ` in ${fmt(Math.round(r.seconds))} s` : ""}, from ${plural(r.sources.length, "passage")}${r.answered ? `; ${plural(cited.length, "passage")} in ${plural(books.size, "book")} cited` : ""}.
          Each sentence was checked against its page: its names, numbers and quotations must be there, and most of its words.
          The check cannot tell whether a paraphrase keeps the page's sense: read the passages.</p>
        ${withheld}
      </article>
      <section class="sources" aria-labelledby="sources-head">
        <div class="center-head"><h2 class="sub" id="sources-head">The passages</h2></div>
        <ol>${r.sources.map((s) => `<li id="src-${s.number}" class="${s.cited ? "" : "unused"}">
          <span class="num">${s.number}</span>
          <div><h3><a href="${bookHref(s.book.id, s.page_index)}">${esc(s.book.title)}</a>, <a href="${bookHref(s.book.id, s.page_index)}">page ${esc(s.page_label)}</a>${s.cited ? "" : ` <span class="muted it">not cited</span>`}</h3>
          ${s.note ? `<p class="src-note"><strong>Curator's note.</strong> ${esc(s.note)}</p>` : ""}
          ${s.uncertain_reading ? `<p class="src-flag">Read by a machine from old print; some words may be misread. Compare the page image.</p>` : ""}
          <details><summary>The passage the model read</summary><p class="passage">${esc(s.text)}</p>
            <button class="text-btn" type="button" data-copy="${esc(s.evidence_id)}">Copy evidence id</button></details></div>
        </li>`).join("")}</ol>
      </section>`;
    out.querySelectorAll(".cite").forEach((b) => b.addEventListener("click", () => {
      const target = document.getElementById(`src-${b.dataset.src}`);
      if (!target) return;
      target.querySelector("details").open = true;
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.remove("lit"); void target.offsetWidth; target.classList.add("lit");
    }));
    out.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
      await copy(b.dataset.copy); toast("Evidence id copied");
    }));
  };

  const ask = async (question) => {
    if (question.length < 8) { out.innerHTML = `<p class="tally" style="margin-top:2rem">Write a question of at least a few words.</p>`; return; }
    out.innerHTML = `<p class="loading">Gathering the pages and checking each citation…</p>`;
    try {
      const r = await api("/api/research", { method: "POST", body: { question, limit: 16 } });
      const groups = new Map();
      for (const f of r.findings) {
        if (!groups.has(f.book.id)) groups.set(f.book.id, { book: f.book, items: [] });
        groups.get(f.book.id).items.push(f);
      }
      const seal = (f) => (f.status === "supported" ? "cited and checked" : f.status === "not_claimed" ? "too short to cite" : esc(f.status.replace(/_/g, " ")));
      out.innerHTML = `
        <p class="verdict">${r.findings.length ? `${plural(r.verified, "passage")} in ${plural(groups.size, "book")}, each cited and checked against
          the archive${r.unverified ? `; ${fmt(r.unverified)} could not be checked` : ""}.` : "No page bears on this question."}
          <small>The archive looked for: ${r.terms.map(esc).join(", ")}.</small></p>
        <div class="dossier">${[...groups.values()].map((g) => `
          <section><h3><a href="${bookHref(g.book.id)}">${esc(g.book.title)}</a>${noted(g.book.noted)}</h3>
          <ul>${g.items.map((f) => `<li>
            <span class="where"><a href="#/page/${encodeURIComponent(f.page_id)}">Page ${esc(f.page_label)}</a>
              <span class="seal ${esc(f.status)}">${seal(f)}</span></span>
            <p>${f.snippet}</p></li>`).join("")}</ul></section>`).join("")}</div>`;
    } catch (error) { out.innerHTML = `<div class="note">${esc(error.message)}</div>`; }
  };
  const canAnswer = () => !buttons.quick.disabled;
  document.getElementById("research-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (canAnswer()) answer(field.value.trim(), "quick"); else ask(field.value.trim());
  });
  buttons.deep.addEventListener("click", () => answer(field.value.trim(), "deep"));
  document.getElementById("ask-gather").addEventListener("click", () => ask(field.value.trim()));
  main.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => {
    field.value = b.dataset.q; window.scrollTo({ top: 0 });
    if (canAnswer()) answer(b.dataset.q, "quick"); else ask(b.dataset.q);
  }));
  if (asked) ask(asked);
}

// ------------------------------------------------------------------ Joe
async function viewJoe() {
  const m = await api("/api/memorial");
  render(`<article class="memorial">
    <header class="memorial-sky">
      ${sunDevice({ title: "A sun whose disc is ruled like a solar cell" })}
      <h1>${esc(m.name)}</h1>
      <p class="full">${esc(m.full_name)}</p>
      <p class="years">${esc(m.years)}</p>
    </header>
    <div class="life">${m.biography.map((p) => `<p>${esc(p)}</p>`).join("")}</div>
    <div class="his-words">${swell(200)}
      <blockquote>“${esc(m.his_words.text)}”</blockquote><cite class="sc">${esc(m.his_words.source)}</cite></div>
    <section aria-labelledby="his-years"><div class="center-head"><h2 class="sub" id="his-years">His years, in order</h2></div>
      <ol class="years-line">${m.timeline.map((t) => `<li><b>${esc(t.when)}</b><span>${esc(t.what)}</span></li>`).join("")}</ol></section>
    <section class="lives-on" aria-labelledby="lives-on"><div class="center-head"><h2 class="sub" id="lives-on">Where his work lives on</h2></div>
      <ul>${m.links.map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.title)}</a></li>`).join("")}</ul>
      <p class="muted it" style="margin-top:.8rem">These open on the web, outside this computer.</p></section>
    <p class="care">${esc(m.care_line)}</p>
  </article>`, "Joe Felz");
}

// ------------------------------------------------------------------ the colophon
async function viewArchive() {
  const [a, o] = await Promise.all([api("/api/archive", { fresh: true }), api("/api/overview", { fresh: true })]);
  const scope = a.rights ? JSON.parse(a.rights.scope || "{}") : {};
  const ok = a.integrity.quick_check === "ok" && !a.integrity.foreign_key_violations;
  const rows = (pairs) => `<dl>${pairs.filter(Boolean).map(([k, v]) => `<div class="lead"><dt>${k}</dt><span class="dots"></span><dd>${v}</dd></div>`).join("")}</dl>`;
  const engine = (name) => (name.startsWith("PyMuPDF-text-layer") ? "Text that came with the scan" : name.startsWith("PyMuPDF-epub") ? "Text from the EPUB" : `Read by machine <span class="mono">${esc(name)}</span>`);
  const s = light();
  render(`<div class="page">
    ${pageHead("How this archive works", "The Colophon", `Every page you read here traces back to an original file whose fingerprint is checked, and every line of text records what produced it. Nothing here leaves this computer.`)}
    <div class="ledger">
      <section><h2>The collection</h2>${rows([
        ["Books", fmt(o.books)], ["Open to read", fmt(o.readable_books)], ["Waiting to be read", fmt(o.waiting_books)],
        ["Pages photographed", fmt(o.pages)], ["Pages with text", fmt(o.passages)], ["Plates found", fmt(o.plates)],
        ["Size of the originals", `${(o.bytes / 2 ** 30).toFixed(2)} GiB`]])}</section>
      <section><h2>Where the text came from</h2>${rows(a.engines.map((e) => [engine(e.engine_name), fmt(e.pages)]))}
        <p>No person has checked the machine's reading. Its confidence is kept for every page it read.</p></section>
      <section><h2>Search and the index</h2>${rows([
        ["The exact words", "ready"],
        ["The meaning", o.semantic ? (o.semantic.provider === "lsa" ? "ready (built from this library)" : "ready") : "not built"],
        o.semantic && ["Model", `<span class="mono">${esc(o.semantic.model_name)}</span>`],
        o.semantic && ["Passages weighed", fmt(o.semantic.passages)],
        ["Index entries", fmt(o.concordance.entities)], ["Index mentions", fmt(o.concordance.mentions)]])}
        <p>The index matches a list of names against the text. The chart and the side-notes show shared pages and likeness
        of meaning. None of them states how two things are related.</p></section>
      <section><h2>Soundness</h2>${rows([
        ["Database check", ok ? "passed" : esc(a.integrity.quick_check)], ["Broken links", fmt(a.integrity.foreign_key_violations)],
        ["Schema", esc(a.integrity.schema_version)]])}
        <p>To re-check every original file against its fingerprint, run <code>.\\scripts\\aki.ps1 -Command verify</code>.</p></section>
      <section><h2>Permission</h2>
        <p class="plain">${a.rights ? `${esc(a.rights.license_name || a.rights.state)}, recorded ${esc(day(a.rights.created_at))}.` : "No rights decision is recorded."}
        The Holy Word gave permission in a conversation; no signed licence exists. It covers keeping, reading,
        searching and researching these books here. It does not cover sharing, exporting or selling them, which is why
        this reading room opens only on this computer.</p>
        ${scope.prohibited_operations ? `<p>Not permitted: ${scope.prohibited_operations.map((x) => esc(x.replace(/_/g, " "))).join(", ")}.</p>` : ""}</section>
      <section><h2>Latest work</h2>${rows(a.runs.slice(0, 8).map((r) => [esc(r.task_type.replace(/_/g, " ")), esc(day(r.completed_at))]))
        || "<p>None yet.</p>"}</section>
      <section><h2>The light</h2>
        <p class="plain">Joe wired solar panels and photographed old books. Both catch light. So this room is lit by your own sun:
        by day the blue of a solar cell is its ink, and after dark gilt is its lamp.</p>
        ${s ? rows([["The light now", esc(s.label.toLowerCase()) + (s.hold === "sun" ? "" : " (held)")],
          ["Sunrise, about", esc(clock(s.rise))], ["Sunset, about", esc(clock(s.set))]]) : ""}
        <p>It reads only this computer's clock and assumes an ordinary northern year. Change or hold the light with the
        sundial at the top of the page.</p></section>
      <section><h2>The types</h2>
        <p class="plain">Titles are set in the Fell types, cut in the seventeenth century and revived by Igino Marini.
        The text is EB Garamond, by Georg Duffner and Octavio Pardo. Both are open fonts, kept inside this program
        with their licences (<a href="/fonts/OFL-IMFell.txt">Fell</a>, <a href="/fonts/OFL-EBGaramond.txt">Garamond</a>).</p></section>
    </div></div>`, "The Colophon");
}

// ------------------------------------------------------------------ the router
let keyHandler = null;
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !event.target.matches("input, textarea, select")) {
    event.preventDefault(); document.getElementById("head-q").focus();
  }
  if (keyHandler) keyHandler(event);
});
document.getElementById("head-search").addEventListener("submit", (event) => {
  event.preventDefault();
  const q = document.getElementById("head-q").value.trim();
  if (q) location.hash = `#/search?q=${encodeURIComponent(q)}`;
});

async function route() {
  const mine = ++routeSeq;
  keyHandler = null;
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  const params = new URLSearchParams(query);
  let parts;
  try { parts = path.split("/").filter(Boolean).map(decodeURIComponent); }
  catch { parts = ["?"]; }
  const section = parts[0] || "";
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === section || (section === "book" && a.dataset.nav === "library")) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  if (section !== "search") document.getElementById("head-q").value = "";
  delete main.dataset.ready;
  if (!main.firstChild) main.innerHTML = `<div class="page"><p class="loading">Opening…</p></div>`;
  // Keep the old page for a moment so quick changes do not flash; after that, say so.
  setTimeout(() => {
    if (mine === routeSeq && !main.dataset.ready) {
      main.innerHTML = `<div class="page"><p class="loading">Opening…</p></div>`;
    }
  }, 300);
  try {
    if (!section) await viewHome();
    else if (section === "library") await viewLibrary(params);
    else if (section === "book" && parts[1]) await viewBook(parts[1], params);
    else if (section === "page" && parts[1]) await viewPage(parts[1]);
    else if (section === "search") await viewSearch(params);
    else if (section === "index" && parts[1]) await viewEntry(parts[1], params);
    else if (section === "index") await viewIndex();
    else if (section === "chart") await viewChart();
    else if (section === "plates") await viewPlates(params);
    else if (section === "research") await viewResearch(params);
    else if (section === "joe") await viewJoe();
    else if (section === "archive") await viewArchive();
    else render(`<div class="page"><div class="note"><strong>There is no page at this address.</strong>
      <a href="#/">Return to the title page</a>.</div></div>`, "Not found");
  } catch (error) {
    if (error === STALE || mine !== routeSeq) return;
    failure(error);
  }
  if (mine !== routeSeq) return;
  if (!raw.startsWith("/book")) window.scrollTo(0, 0);
  main.dataset.ready = raw;
  if (!main.contains(document.activeElement)) main.focus({ preventScroll: true });
}

startLight();
setUpDial();
window.addEventListener("hashchange", route);
route();
