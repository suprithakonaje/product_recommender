"""

NOT AVAILABLE FOR AUTOMATIC CATALOGUE LOADER


anthropologie_scraper.py

Anthropologie-specific implementation of BaseScraper.

Uses Playwright because the catalogue is rendered through the browser.
Extracts product-card information from the public sale-all catalogue
and converts each item into the common Product shape used by the
rest of the application.
"""

import re
import time
from typing import Optional, List, Dict

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from base_scraper import BaseScraper, Product


class AnthropologieScraper(BaseScraper):
    brand_name = "Anthropologie"

    # Anthropologie currently displays a large catalogue, so use a
    # reasonably large page size where supported.
    PRODUCTS_PER_PAGE = 96

    # Maximum number of pages to protect against an unexpected
    # pagination loop.
    MAX_PAGES = 100

    def __init__(self):
        self.base_url = "https://www.anthropologie.com/sale-all"

    def fetch_catalogue(self, **kwargs) -> List[Product]:
        """
        Fetch all products from Anthropologie's Sale All catalogue.

        Discount filtering is deliberately NOT performed here.
        CatalogueScraper handles the shared >40% business rule.
        """

        products: List[Product] = []
        seen_product_ids = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)

            context = browser.new_context(
                locale="en-US",
                viewport={"width": 1440, "height": 900},
            )

            page = context.new_page()

            try:
                for page_number in range(1, self.MAX_PAGES + 1):

                    url = self._build_page_url(page_number)

                    print(
                        f"[Anthropologie] Opening page "
                        f"{page_number}: {url}"
                    )

                    try:
                        page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )
                        page_text = page.locator("body").inner_text().lower()

                        if "unusual activity" in page_text:
                            print(
                                "[Anthropologie] Security page detected. "
                                "The site is blocking automated access."
                            )
                            return []

                    except PlaywrightTimeoutError:
                        print(
                            f"[warn] Page {page_number} timed out. "
                            f"Continuing with whatever loaded."
                        )

                    # Give the product grid time to render.
                    try:
                        page.locator(
                            "[data-testid='product-card']"
                        ).first.wait_for(
                            state="visible",
                            timeout=30000,
                        )
                    except PlaywrightTimeoutError:
                        print(
                            f"[warn] No product cards found on "
                            f"page {page_number}."
                        )

                    # Allow images/lazy-loaded product information
                    # to finish rendering.
                    page.wait_for_timeout(2000)

                    page_products = self._extract_products_from_page(page)

                    print(
                        f"[Anthropologie] Page {page_number}: "
                        f"found {len(page_products)} products"
                    )

                    if not page_products:
                        print(
                            "[Anthropologie] No products found. "
                            "Stopping pagination."
                        )
                        break

                    new_products = 0

                    for product in page_products:
                        unique_id = (
                                product.product_id
                                or product.url
                                or product.name
                        )

                        if unique_id not in seen_product_ids:
                            seen_product_ids.add(unique_id)
                            products.append(product)
                            new_products += 1

                    print(
                        f"[Anthropologie] Added {new_products} new products. "
                        f"Total: {len(products)}"
                    )

                    # If the page contains products but every one of
                    # them has already been seen, pagination is no
                    # longer producing new data.
                    if new_products == 0:
                        print(
                            "[Anthropologie] No new products on this page. "
                            "Stopping pagination."
                        )
                        break

                    # Check whether a next page exists.
                    if not self._has_next_page(page):
                        print(
                            "[Anthropologie] No next page found. "
                            "Finished."
                        )
                        break

                    self._rate_limit()

            finally:
                browser.close()

        print(
            f"[Anthropologie] Finished. "
            f"Fetched {len(products)} unique products."
        )

        return products

    def _build_page_url(self, page_number: int) -> str:
        """
        Anthropologie uses a `page` query parameter for pagination.
        Page 1 can simply use the base URL.
        """

        if page_number == 1:
            return self.base_url

        separator = "&" if "?" in self.base_url else "?"

        return (
            f"{self.base_url}"
            f"{separator}page={page_number}"
        )

    def _extract_products_from_page(self, page) -> List[Product]:
        """
        Extract product information from Anthropologie product cards.

        Anthropologie currently exposes stable data-testid attributes
        on its product-card elements.
        """

        cards = page.locator(
            "[data-testid='product-card']"
        )

        count = cards.count()

        products: List[Product] = []

        for i in range(count):
            card = cards.nth(i)

            try:
                product = self._parse_product_card(card)

                if product is not None:
                    products.append(product)

            except Exception as e:
                print(
                    f"[warn] Failed to parse Anthropologie "
                    f"product card {i}: {e}"
                )

        return products

    def _parse_product_card(self, card) -> Optional[Product]:
        """
        Convert one Anthropologie product card into Product.
        """

        # ---------------------------------------------------------
        # Product name
        # ---------------------------------------------------------

        name_locator = card.locator(
            "[data-testid='product-card-title']"
        )

        name = self._safe_text(name_locator)

        if not name:
            return None

        # ---------------------------------------------------------
        # Brand
        # ---------------------------------------------------------

        brand_locator = card.locator(
            "[data-testid='product-card-brand']"
        )

        product_brand = self._safe_text(brand_locator)

        # ---------------------------------------------------------
        # Price
        # ---------------------------------------------------------

        price_locator = card.locator(
            "[data-testid='product-card-price']"
        )

        price_text = self._safe_text(price_locator)

        sale_price, original_price = self._parse_prices(
            price_text
        )

        # ---------------------------------------------------------
        # Product URL
        # ---------------------------------------------------------

        url = None

        links = card.locator("a[href]")

        if links.count() > 0:
            for i in range(links.count()):
                href = links.nth(i).get_attribute("href")

                if href and (
                        "/shop/" in href
                        or "/product/" in href
                ):
                    url = self._absolute_url(href)
                    break

            # Fallback to the first link.
            if url is None:
                href = links.first.get_attribute("href")

                if href:
                    url = self._absolute_url(href)

        # ---------------------------------------------------------
        # Product ID / SKU
        # ---------------------------------------------------------

        product_id = self._extract_product_id(
            card,
            url
        )

        sku = product_id

        # ---------------------------------------------------------
        # Images
        # ---------------------------------------------------------

        images = self._extract_images(card)

        # ---------------------------------------------------------
        # Discount
        # ---------------------------------------------------------

        discount_percent = None

        if original_price and sale_price:
            if original_price > 0 and sale_price < original_price:
                discount_percent = round(
                    ((original_price - sale_price)
                     / original_price) * 100,
                    2,
                )

        # Anthropologie may show additional promotional text such
        # as "Extra 40% Off In Cart". Keep that separately.
        discount_label = self._extract_discount_label(card)

        # ---------------------------------------------------------
        # Product object
        # ---------------------------------------------------------

        return Product(
            brand=self.brand_name,
            product_id=product_id,
            sku=sku,
            name=name,
            original_price=original_price,
            sale_price=sale_price,
            discount_percent=discount_percent,
            discount_label=discount_label,
            images=", ".join(images),
            url=url,
            scraped_at=pd.Timestamp.now(),
        )

    @staticmethod
    def _safe_text(locator) -> Optional[str]:
        """
        Safely get text from a Playwright locator.
        """

        try:
            if locator.count() == 0:
                return None

            text = locator.first.inner_text().strip()

            return text if text else None

        except Exception:
            return None

    @staticmethod
    def _parse_prices(
            price_text: Optional[str],
    ) -> tuple[Optional[float], Optional[float]]:
        """
        Parse sale and original prices.

        Examples:

            "$99.95 Original price: $158.00"

        becomes:

            sale_price = 99.95
            original_price = 158.00

        Also handles price ranges such as:

            "$34.95 – $49.95 Original price: $48.00 – $78.00"

        For ranges, the lowest value is used for each side.
        """

        if not price_text:
            return None, None

        normalized = (
            price_text
                .replace(",", "")
                .replace("–", "-")
                .replace("—", "-")
        )

        # Find every dollar amount.
        amounts = re.findall(
            r"\$(\d+(?:\.\d{1,2})?)",
            normalized
        )

        if not amounts:
            return None, None

        values = [float(value) for value in amounts]

        # Usually the first amount is the sale price and the
        # second amount is the original price.
        sale_price = values[0]

        original_price = values[1] if len(values) >= 2 else None

        return sale_price, original_price

    @staticmethod
    def _absolute_url(href: str) -> str:
        """
        Convert a relative Anthropologie URL to an absolute URL.
        """

        if href.startswith("http://") or href.startswith("https://"):
            return href

        if href.startswith("//"):
            return "https:" + href

        if href.startswith("/"):
            return "https://www.anthropologie.com" + href

        return "https://www.anthropologie.com/" + href

    @staticmethod
    def _extract_product_id(
            card,
            url: Optional[str],
    ) -> Optional[str]:
        """
        Try several sources for the product identifier.
        """

        # Check common data attributes first.
        attributes = [
            "data-product-id",
            "data-productid",
            "data-id",
        ]

        for attribute in attributes:
            try:
                value = card.get_attribute(attribute)

                if value:
                    return value.strip()

            except Exception:
                pass

        # Anthropologie product URLs often contain the product
        # identifier. If one is present, use it as a fallback.
        if url:
            match = re.search(
                r"([A-Z]{1,4}-\d{6,})",
                url,
                re.IGNORECASE,
            )

            if match:
                return match.group(1)

        return None

    @staticmethod
    def _extract_images(card) -> List[str]:
        """
        Extract product image URLs from the card.
        """

        images = []

        image_elements = card.locator("img")

        for i in range(image_elements.count()):
            img = image_elements.nth(i)

            # Try normal src first.
            src = img.get_attribute("src")

            # Then lazy-loading attributes.
            if not src:
                src = img.get_attribute("data-src")

            if not src:
                src = img.get_attribute("data-lazy-src")

            if src:
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = (
                            "https://www.anthropologie.com"
                            + src
                    )

                if src not in images:
                    images.append(src)

        return images

    @staticmethod
    def _extract_discount_label(card) -> Optional[str]:
        """
        Capture promotional text displayed on the product card.

        Examples include:
            Extra 40% Off In Cart
            Perks Members: Extra 40% off In Cart
        """

        try:
            text = card.inner_text().strip()

            patterns = [
                r"Extra\s+\d+%\s+Off\s+In\s+Cart",
                r"Extra\s+\d+%\s+off\s+In\s+Cart",
                r"Perks Members:\s*Extra\s+\d+%\s+off\s+In\s+Cart",
            ]

            for pattern in patterns:
                match = re.search(
                    pattern,
                    text,
                    flags=re.IGNORECASE,
                )

                if match:
                    return match.group(0).strip()

        except Exception:
            pass

        return None

    @staticmethod
    def _has_next_page(page) -> bool:
        """
        Determine whether another catalogue page exists.

        We first look for conventional next-page links/buttons.
        """

        selectors = [
            "a[aria-label='Next']",
            "button[aria-label='Next']",
            "a[aria-label*='Next']",
            "button[aria-label*='Next']",
            "a[rel='next']",
        ]

        for selector in selectors:
            locator = page.locator(selector)

            if locator.count() == 0:
                continue

            for i in range(locator.count()):
                element = locator.nth(i)

                try:
                    if not element.is_visible():
                        continue

                    # Disabled next button means there is no next page.
                    disabled = element.get_attribute("disabled")

                    if disabled is not None:
                        continue

                    aria_disabled = element.get_attribute(
                        "aria-disabled"
                    )

                    if aria_disabled == "true":
                        continue

                    return True

                except Exception:
                    continue

        # Fallback: inspect the page for a numbered next page.
        # The current Anthropologie catalogue exposes page numbers,
        # so look for a link containing a page query parameter.
        try:
            current_url = page.url

            current_page = 1

            match = re.search(
                r"[?&]page=(\d+)",
                current_url,
            )

            if match:
                current_page = int(match.group(1))

            next_page = current_page + 1

            next_link = page.locator(
                f"a[href*='page={next_page}']"
            )

            if next_link.count() > 0:
                return True

        except Exception:
            pass

        return False
