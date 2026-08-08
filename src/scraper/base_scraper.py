"""
base_scraper.py

Every brand-specific scraper (ZaraScraper, future HMScraper, etc.) implements
this interface. CatalogueScraper - the dispatcher - only ever calls
fetch_catalogue(). It doesn't know or care HOW a given brand gets its data
(direct JSON API like Zara, or BeautifulSoup against server-rendered HTML
for a site that doesn't have one). That's the whole point of this pattern:
adding a new brand means writing one new class here, never touching the
dispatcher.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Optional, List
import time
import pandas as pd


@dataclass
class Product:
    """
    Common shape every brand's scraper must produce, regardless of how
    different their raw JSON/HTML looks. This is what makes the dispatcher
    and the discount filter brand-agnostic.
    """
    brand: str
    product_id: Optional[str]
    sku: Optional[str]
    name: Optional[str]
    original_price: Optional[float]
    sale_price: Optional[float]
    discount_percent: Optional[float]
    discount_label: Optional[str]
    images: str
    url: Optional[str]
    scraped_at: pd.Timestamp

    def to_dict(self) -> dict:
        return asdict(self)


class BaseScraper(ABC):
    brand_name: str  # each subclass sets this, e.g. "Zara"

    # Considerate-scraping default - override per brand if you learn a
    # site tolerates faster requests, but never remove this outright.
    request_delay_seconds: float = 1.5

    @abstractmethod
    def fetch_catalogue(self, **kwargs) -> List[Product]:
        """
        Fetch the catalogue for this brand and return a list of Product
        objects. Should NOT apply discount filtering - that's the
        dispatcher's job (CatalogueScraper), not the scraper's. This
        method's only responsibility is: go get the data, parse it,
        hand back clean Product objects.

        kwargs are brand-specific (e.g. Zara needs category_id) since
        different sites are structured differently - the interface only
        guarantees the OUTPUT shape (list[Product]), not the input.
        """
        raise NotImplementedError

    def _rate_limit(self):
        """Call this between requests, especially once pagination is added."""
        time.sleep(self.request_delay_seconds)

    def close(self):
        """
        Release any held resources (browser instances, open sessions,
        etc). No-op by default. ZaraScraper doesn't need this right now
        (plain `requests`, nothing persistent to clean up), but it's here
        so any future scraper that DOES hold state (e.g. a browser
        session, a persistent connection) has somewhere consistent to
        put cleanup logic without changing the dispatcher.
        """
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()