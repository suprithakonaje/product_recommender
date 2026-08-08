"""
network_capture.py

Automated network-tab discovery tool.

Given a product page URL, this launches a headless browser, loads the page,
and records every network response that looks like it might be an internal
API returning product/catalogue data (JSON responses over a size threshold,
containing price/product-like keys).

This replaces the manual "open DevTools > Network tab > eyeball XHR calls"
step you did for Zara, so you can point this at a new brand and get a
ranked shortlist of candidate API endpoints instead of hunting for them
by hand.

This is a DISCOVERY tool, not a production scraper. Once it points you at
the right endpoint, you write a proper scraper class (like ZaraScraper)
that hits that endpoint directly and parses the known JSON shape — you
don't run a headless browser in production just to fetch a catalogue.

Usage:
    python network_capture.py "https://www.hm.com/en_us/some-product.html"

Setup:
    pip install playwright
    playwright install chromium
"""

from playwright.sync_api import sync_playwright
import json
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

# Keywords that suggest a JSON response contains product/price data.
# Extend this as you discover new brand-specific field names — e.g. Zara's
# catalogue JSON used "commercialComponents", which isn't an obvious guess
# up front. Expect to add to this list after each new brand.
PRODUCT_KEYWORDS = [
    "price", "product", "sku", "commercialComponents", "discount",
    "salePrice", "currentPrice", "originalPrice", "productId",
]

MIN_RESPONSE_SIZE_BYTES = 500  # skip tiny responses (analytics pings, etc.)


@dataclass
class CapturedEndpoint:
    url: str
    method: str
    status: int
    content_type: str
    size_bytes: int
    keyword_hits: list = field(default_factory=list)
    sample_json: Optional[dict] = None

    def score(self) -> int:
        """Rough relevance ranking - more keyword hits = more likely to be
        the product/catalogue API. Treat this as a shortlist, not an
        answer - eyeball the top few candidates yourself before wiring
        one up as a real scraper."""
        return len(self.keyword_hits)


def scan_inline_json(page) -> list[CapturedEndpoint]:
    """
    Some sites (Next.js/Nuxt SSR apps in particular) never make a separate
    network call for the initial product list at all - they embed the
    catalogue as inline JSON in a <script> tag (e.g. __NEXT_DATA__) and
    hydrate the page from that. If capture_network_calls() finds nothing,
    this is the most likely reason, and this function checks for it.
    """
    found: list[CapturedEndpoint] = []
    scripts = page.eval_on_selector_all(
        "script",
        "els => els.map(e => ({id: e.id, type: e.type, text: e.textContent}))",
    )
    for s in scripts:
        text = s.get("text") or ""
        if len(text) < MIN_RESPONSE_SIZE_BYTES:
            continue
        hits = [kw for kw in PRODUCT_KEYWORDS if kw.lower() in text.lower()]
        if not hits:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        found.append(CapturedEndpoint(
            url=f"<inline script id={s.get('id') or 'unnamed'}>",
            method="INLINE",
            status=200,
            content_type=s.get("type") or "text/javascript",
            size_bytes=len(text),
            keyword_hits=hits,
            sample_json=parsed,
        ))
    return found


def capture_network_calls(
    url: str, wait_ms: int = 5000, debug: bool = False, headless: bool = True
) -> list[CapturedEndpoint]:
    """
    Loads `url` in a headless browser and records every JSON network
    response, scoring each by whether it looks like a product/catalogue
    API based on PRODUCT_KEYWORDS.

    If debug=True, prints every response seen (regardless of match) so
    you can tell "no matches because nothing product-like came back" apart
    from "no matches because the site returned a captcha/block page" or
    "no matches because there simply were no XHR calls at all."

    Also scans for product data embedded as inline JSON in <script> tags,
    since SSR sites often hydrate from inline data rather than a separate
    API call - this is a very common reason for zero network matches.

    Returns candidates sorted by score (highest first), so the most
    likely internal API endpoint (or inline data source) surfaces first.
    """
    captured: list[CapturedEndpoint] = []
    all_responses_seen = []  # for debug logging only

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        def handle_response(response):
            try:
                content_type = response.headers.get("content-type", "")
                if debug:
                    all_responses_seen.append(
                        (response.status, content_type, response.url)
                    )

                if "application/json" not in content_type:
                    return

                body = response.body()
                if len(body) < MIN_RESPONSE_SIZE_BYTES:
                    return

                text = body.decode("utf-8", errors="ignore")
                hits = [kw for kw in PRODUCT_KEYWORDS if kw.lower() in text.lower()]
                if not hits:
                    return

                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None

                captured.append(CapturedEndpoint(
                    url=response.url,
                    method=response.request.method,
                    status=response.status,
                    content_type=content_type,
                    size_bytes=len(body),
                    keyword_hits=hits,
                    sample_json=parsed,
                ))
            except Exception:
                # Best-effort capture - one malformed response shouldn't
                # kill the whole run.
                pass

        page.on("response", handle_response)

        try:
            # networkidle can hang on sites with persistent polling/analytics
            # connections. Fall back to "load" if it times out, rather than
            # crashing with no output.
            page.goto(url, wait_until="networkidle", timeout=20000)
        except Exception:
            if debug:
                print("networkidle timed out, falling back to 'load' event")
            page.goto(url, wait_until="load", timeout=30000)

        page.wait_for_timeout(wait_ms)

        if debug:
            print(f"\n--- DEBUG: {len(all_responses_seen)} total responses seen ---")
            for status, ctype, resp_url in all_responses_seen[:30]:
                print(f"  {status}  {ctype:30s}  {resp_url[:100]}")
            if len(all_responses_seen) > 30:
                print(f"  ... and {len(all_responses_seen) - 30} more")
            title = page.title()
            print(f"\n--- DEBUG: page title after load: {title!r} ---")
            print("    (if this looks like a captcha/verification page title,")
            print("     you're likely hitting bot detection, not a code bug)\n")

        inline_hits = scan_inline_json(page)
        if inline_hits:
            captured.extend(inline_hits)

        browser.close()

    captured.sort(key=lambda c: c.score(), reverse=True)
    return captured


def print_report(candidates: list[CapturedEndpoint], top_n: int = 5):
    print(f"\nFound {len(candidates)} candidate JSON endpoints\n")
    for i, c in enumerate(candidates[:top_n], 1):
        print(f"[{i}] score={c.score()}  {c.method} {c.status}  ({c.size_bytes} bytes)")
        print(f"    url: {c.url}")
        print(f"    keyword hits: {c.keyword_hits}")
        if isinstance(c.sample_json, dict):
            print(f"    top-level keys: {list(c.sample_json.keys())[:10]}")
        print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python network_capture.py <product_url> [--debug] [--headed]")
        sys.exit(1)

    target_url = sys.argv[1]
    debug_mode = "--debug" in sys.argv
    headed_mode = "--headed" in sys.argv  # run with a visible browser window

    domain = urlparse(target_url).netloc
    print(f"Capturing network traffic for {domain} ...")

    results = capture_network_calls(
        target_url, debug=debug_mode, headless=not headed_mode
    )
    print_report(results)