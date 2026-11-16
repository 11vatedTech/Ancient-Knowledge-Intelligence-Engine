"""Walk the reading room in a real browser and check what a reader would notice.

    python scripts/room_walk.py [--base http://127.0.0.1:8765] [--shots <folder>]

Needs a running reading room (``aki serve``) and Playwright with an installed Edge or
Chrome (``pip install playwright``; no browser download is needed with ``--channel``).
Playwright is deliberately not a project dependency: this is an inspection tool.

It opens every section at desktop and phone width in day and night light, fails on any
console error, failed request or sideways scroll, and exercises the behaviours that are
easy to break: a slow page must not draw over a newer one, the sundial and the plate viewer
must keep keyboard focus, search must keep a choice made before typing, and highlighting
must mark whole words only.

Screenshots are written only when ``--shots`` is given. They contain page scans, which the
rights record does not allow to be exported or shared, so keep that folder on this computer
and out of any synchronised or published place.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SECTIONS = ["/", "/library", "/search", "/index", "/chart", "/plates", "/research", "/joe", "/archive"]


def main() -> int:
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--channel", default="msedge")
    parser.add_argument("--shots", default=None)
    args = parser.parse_args()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    passed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed
        if ok:
            passed += 1
        else:
            failures.append(f"{name}: {detail}")
        print(("ok    " if ok else "FAIL  ") + name + (f"  ({detail})" if detail and not ok else ""))

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel, headless=True)

        def open_page(width: int, height: int, light: str):
            context = browser.new_context(viewport={"width": width, "height": height})
            context.add_init_script(f"try{{localStorage.setItem('felz.light','{light}')}}catch(e){{}}")
            page = context.new_page()
            problems: list[str] = []
            page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}")
                    if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
            page.on("response", lambda r: problems.append(f"http {r.status} {r.url}")
                    if r.status >= 400 else None)
            return context, page, problems

        def go(page, route: str, wait: int = 900) -> None:
            page.goto(f"{args.base}/#{route}", wait_until="networkidle")
            page.wait_for_timeout(wait)
            # Wait for this route's own view, not whatever was on screen before it.
            page.wait_for_function(
                "(r) => document.querySelector('main').dataset.ready === r",
                arg=route if route.startswith("/") else "/" + route, timeout=60_000)
            page.wait_for_timeout(500)  # let the view finish fading in before it is judged

        # ---------------------------------------------------------------- every section
        for label, width, height, light in (("desktop-noon", 1440, 900, "noon"),
                                            ("desktop-night", 1440, 900, "night"),
                                            ("phone-dusk", 390, 844, "dusk")):
            context, page, problems = open_page(width, height, light)
            for route in SECTIONS:
                del problems[:]
                go(page, route)
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                heading = page.evaluate("(document.querySelector('main h1')||{}).textContent || ''")
                check(f"{label} {route}", not problems and overflow <= 0 and bool(heading.strip()),
                      "; ".join(problems[:3]) or f"sideways overflow {overflow}px, heading {heading!r}")
                if shots:
                    page.screenshot(path=str(shots / f"{label}{route.replace('/', '_') or '_home'}.png"))
            context.close()

        context, page, problems = open_page(1440, 900, "noon")
        books = page.request.get(f"{args.base}/api/books").json()
        readable = sorted((b for b in books if b["pages"] > 3), key=lambda b: -b["text_pages"])

        # ---------------------------------------------------------------- the reader
        if readable:
            book = readable[0]
            go(page, f"/book/{book['id']}?p=2", 1500)
            check("reader shows a page", page.evaluate("document.querySelector('#goto').value") == "2")
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(900)
            check("arrow key turns the page", page.evaluate("document.querySelector('#goto').value") == "3")
            page.keyboard.press("Shift+ArrowRight")
            page.wait_for_timeout(400)
            check("shift+arrow is left for selecting text",
                  page.evaluate("document.querySelector('#goto').value") == "3")
            check("reader has no errors", not problems, "; ".join(problems[:3]))
            if shots:
                page.screenshot(path=str(shots / "reader.png"))
            # a page that cannot be fetched says so and does not hang
            page.route("**/api/pages/*", lambda route: route.abort()
                       if route.request.resource_type in ("fetch", "xhr") else route.continue_())
            page.click("#next")
            page.wait_for_timeout(700)
            said = page.evaluate("document.querySelector('#transcript').textContent")
            check("a page that cannot be fetched says so", "could not be opened" in said, said[:80])
            page.unroute("**/api/pages/*")
            del problems[:]

        # ---------------------------------------------------------------- a slow page must not win
        go(page, "/library")
        page.route("**/api/chart", lambda route: (page.wait_for_timeout(1200), route.continue_()))
        page.evaluate("location.hash = '#/chart'")
        page.wait_for_timeout(150)
        page.evaluate("location.hash = '#/joe'")
        page.wait_for_timeout(2600)
        heading = page.evaluate("document.querySelector('main h1').textContent")
        check("a slow page does not draw over a newer one", "Chart" not in heading, heading)
        page.unroute("**/api/chart")

        # ---------------------------------------------------------------- the sundial
        page.click("#dial-btn")
        page.wait_for_timeout(150)
        check("sundial opens with the current light focused",
              page.evaluate("document.activeElement.getAttribute('aria-pressed')") == "true")
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
        page.wait_for_timeout(250)
        held = page.evaluate("document.activeElement.dataset.hold || ''")
        check("choosing a light keeps keyboard focus on it",
              bool(held) and page.evaluate("document.activeElement.getAttribute('aria-pressed')") == "true", held)
        page.keyboard.press("Escape")
        check("escape closes the sundial and returns focus",
              page.evaluate("document.getElementById('dial-menu').hidden && document.activeElement.id === 'dial-btn'"))
        page.evaluate("localStorage.setItem('felz.light','noon')")

        # ---------------------------------------------------------------- search
        go(page, "/search")
        page.wait_for_selector("[data-mode=meaning]", timeout=60_000)
        if page.evaluate("!document.querySelector('[data-mode=meaning]').disabled"):
            page.click("[data-mode=words]")
            page.fill("#q", "the Watchers")
            page.keyboard.press("Enter")
            page.wait_for_timeout(1800)
            check("search keeps a choice made before typing", "mode=words" in page.url, page.url)
        else:
            page.fill("#q", "the Watchers")
            page.keyboard.press("Enter")
            page.wait_for_timeout(1800)
        results = page.evaluate("document.querySelectorAll('.hit').length")
        if results:
            page.click(".hit h3 a")
            page.wait_for_timeout(1600)
            marks = page.evaluate("[...document.querySelectorAll('.transcript-text mark')].map(m => m.textContent.toLowerCase())")
            check("highlighting marks whole words and never 'the'",
                  "the" not in marks and all(m == "watchers" for m in marks), str(marks[:6]))

        # ---------------------------------------------------------------- the plate viewer
        go(page, "/plates", 1500)
        if page.evaluate("document.querySelectorAll('.plates button').length") > 1:
            page.focus(".plates button")
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            page.focus("[data-step='1']")
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            check("the plate viewer keeps focus on Next",
                  page.evaluate("document.activeElement.dataset.step") == "1")
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            check("escape closes the plate viewer", not page.evaluate("document.getElementById('viewer').open"))

        # ---------------------------------------------------------------- the chart
        go(page, "/chart", 1500)
        stars = page.evaluate("document.querySelectorAll('.chart .star').length")
        if stars:
            page.focus(".chart .star")
            page.wait_for_timeout(200)
            check("a focused star reads out its company",
                  "is named on" in page.evaluate("document.getElementById('chart-read').textContent"))
        go(page, "/%E0%A4%A", 400)
        check("a broken address is an ordinary 'no page' message",
              "no page" in page.evaluate("document.querySelector('main').textContent"))
        check("no errors during the interaction checks", not problems, "; ".join(problems[:4]))
        browser.close()

    print(f"\n{passed} passed, {len(failures)} failed")
    for line in failures:
        print("  -", line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
