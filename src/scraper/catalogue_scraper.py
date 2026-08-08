"""
catalogue_scraper.py

Generic dispatcher. Given a brand name, it looks up the matching
BaseScraper subclass, calls it, and applies the shared >40%-off filter.
It does NOT know or care how any individual brand's site works - that's
entirely encapsulated in each brand's own scraper class. Adding a new
brand means writing one new scraper class and adding one registry entry
here - nothing else in this file changes.
"""

from typing import Dict, List, Optional

from base_scraper import BaseScraper, Product
from zara_scraper import ZaraScraper
from anthropologie_scraper import AnthropologieScraper

# Tune the threshold here - one place, not scattered across brand scrapers.
MIN_DISCOUNT_THRESHOLD = 40  # percent

# Category IDs are discovered once per brand/section (see
# data_gathering/discovery/ for how - e.g. the `v1` param in a Zara
# category URL). Not something re-derived at runtime.
ZARA_CATEGORY_IDS = {
    "Women": 2726405,
    # "Men": None,   # discover once, add here
    # "Kids": None,
}

SCRAPER_REGISTRY: Dict[str, BaseScraper] = {
    "Zara": ZaraScraper(),
    "Anthropologie": AnthropologieScraper()
}


def is_worth_tracking(product: Product) -> bool:
    """
    Business rule, deliberately kept separate from each brand's parsing
    logic, so every brand gets the same filter without duplicating it
    per scraper.
    """
    if product.discount_percent is None:
        return False  # no discount data available - not the same as "0% off"
    return product.discount_percent >= MIN_DISCOUNT_THRESHOLD


class CatalogueScraper:
    def __init__(self, registry: Optional[Dict[str, BaseScraper]] = None):
        self.registry = registry or SCRAPER_REGISTRY

    def get_catalogue(self, brand: str, **kwargs) -> List[Product]:
        scraper = self.registry.get(brand)
        if scraper is None:
            raise ValueError(
                f"No scraper registered for brand: {brand!r}. "
                f"Available brands: {list(self.registry.keys())}"
            )

        raw_products = scraper.fetch_catalogue(**kwargs)
        return [p for p in raw_products if is_worth_tracking(p)]

    def available_brands(self) -> List[str]:
        """Powers the frontend dropdown - add a brand to the registry and
        it shows up here automatically, no separate config to keep in sync."""
        return list(self.registry.keys())


if __name__ == "__main__":
    # "---FOR ZARA---"
    # import pandas as pd
    # from pathlib import Path
    #
    # # File lives at root/src/scraper/catalogue_scraper.py, so climbing
    # # three levels (scraper -> src -> root) gets to the project root,
    # # then into the sibling data/ folder. If you move this file, this
    # # path needs to move with it - that's the tradeoff of building paths
    # # relative to __file__ instead of a hardcoded absolute path.
    # DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
    # DATA_DIR.mkdir(parents=True, exist_ok=True)  # create it if it doesn't exist yet
    #
    # catalogue = CatalogueScraper()
    # deals = catalogue.get_catalogue("Zara", category_id=ZARA_CATEGORY_IDS["Women"])
    #
    # df = pd.DataFrame([d.to_dict() for d in deals])
    # output_path = DATA_DIR / "zara_filtered_data.csv"
    # df.to_csv(output_path, index=False)
    # print(f"Saved {len(df)} deals over {MIN_DISCOUNT_THRESHOLD}% off to {output_path}")

    "---FOR ANTHROPOLOGIE---"

    if __name__ == "__main__":
        import pandas as pd
        from pathlib import Path

        DATA_DIR = (
                Path(__file__).resolve().parent.parent.parent
                / "data"
        )

        DATA_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        catalogue = CatalogueScraper()

        deals = catalogue.get_catalogue(
            "Anthropologie"
        )

        df = pd.DataFrame(
            [d.to_dict() for d in deals]
        )

        output_path = (
                DATA_DIR
                / "anthropologie_filtered_data.csv"
        )

        df.to_csv(
            output_path,
            index=False
        )

        print(
            f"Saved {len(df)} deals over "
            f"{MIN_DISCOUNT_THRESHOLD}% off to "
            f"{output_path}"
        )
