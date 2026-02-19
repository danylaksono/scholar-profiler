import logging
from typing import Optional

try:
    from playwright.sync_api import sync_playwright
except Exception:  # Playwright may not be installed in all environments
    sync_playwright = None


class PlaywrightDriver:
    """A small synchronous wrapper around Playwright's sync API.

    Exposes a minimal subset of methods used by `GoogleScholarScraper` so the
    existing scraper can remain mostly unchanged.
    """

    def __init__(self, headless: bool = True, delay_range: tuple = (2, 5), logger: Optional[logging.Logger] = None):
        self.headless = headless
        self.delay_range = delay_range
        self.logger = logger or logging.getLogger(__name__)
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def start(self) -> None:
        if sync_playwright is None:
            raise RuntimeError("Playwright is not installed. Install with: pip install playwright")

        self._playwright = sync_playwright().start()
        # Use Chromium to mirror Chrome behavior
        self.browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
        )
        self.page = self.context.new_page()
        # sensible default timeout for Playwright operations
        self.page.set_default_timeout(10000)

    def stop(self) -> None:
        try:
            if self.context:
                try:
                    self.context.close()
                except Exception:
                    pass
            if self.browser:
                try:
                    self.browser.close()
                except Exception:
                    pass
        finally:
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass

    def get(self, url: str) -> None:
        if not self.page:
            raise RuntimeError("Playwright page is not started")
        self.page.goto(url, wait_until="load")

    def page_content(self) -> str:
        if not self.page:
            return ""
        return self.page.content()

    def wait_for_selector(self, selector: str, timeout: int = 10000):
        if not self.page:
            return None
        return self.page.wait_for_selector(selector, timeout=timeout)

    def query_selector(self, selector: str):
        if not self.page:
            return None
        return self.page.query_selector(selector)

    def click(self, selector: str) -> None:
        if not self.page:
            raise RuntimeError("Playwright page is not started")
        self.page.click(selector)

    def locator_is_enabled(self, selector: str) -> bool:
        if not self.page:
            return False
        return self.page.locator(selector).is_enabled()
