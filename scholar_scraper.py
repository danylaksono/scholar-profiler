
import argparse
import asyncio
import csv
import json
import os
import random
import time
import logging
from typing import List, Dict, Optional, Tuple

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains



class GoogleScholarScraper:
    """A class to scrape Google Scholar profiles and publication details."""
    
    def __init__(self, headless: bool = True, delay_range: tuple = (2, 5), driver: str = "selenium"):
        """
        Initialize the Google Scholar scraper.
        
        Args:
            headless: Whether to run browser in headless mode
            delay_range: Range of random delays between requests (min, max) in seconds
            driver: Browser driver to use ('selenium' or 'playwright')
        """
        if driver not in ("selenium", "playwright"):
            raise ValueError("driver must be 'selenium' or 'playwright'")
        self.headless = headless
        self.delay_range = delay_range
        self.driver = driver

        # Phase 2 defaults
        self.use_httpx = True
        self.concurrency = 8
        # Increase retries slightly so the scraper has a better chance to recover
        # from transient 429/redirect -> /sorry responses before giving up.
        self.max_retries = 5
        self.backoff_factor = 0.5
        self.user_agents: Optional[List[str]] = None  # optional list of UAs for rotation
        self.proxies: Optional[List[str]] = None     # optional list of proxy servers (rotated)

        # runtime objects
        self.browser = None
        self.playwright = None
        self._blocked_reason: Optional[str] = None  # reason text when Google blocks requests (captcha / unusual traffic)
        self._block_count: int = 0                   # how many consecutive block detections we've seen
        self.block_retry_limit: int = 3              # how many block detections before we treat as persistent
        self.pause_on_block: bool = True             # whether to pause the batch when persistent block detected
        self.blocked_pause_seconds: float = 300.0    # default pause duration (seconds)
        self._profile_html_cache: Optional[str] = None  # cached profile HTML when httpx fallback is used
        self.logger = self._setup_logging()
        
    def _setup_logging(self) -> logging.Logger:
        """Set up logging configuration."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('scholar_scraper.log'),
                logging.StreamHandler()
            ]
        )
        return logging.getLogger(__name__)
    
    def _setup_browser(self) -> webdriver.Chrome:
        """Set up the Chrome browser."""
        self.logger.info("Setting up Chrome browser...")
        try:
            chrome_options = Options()
            if self.headless:
                chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

            # webdriver-manager is an optional runtime dependency; import here so tests that don't need it don't fail
            try:
                from webdriver_manager.chrome import ChromeDriverManager
            except Exception as e:
                self.logger.error("webdriver_manager is required for Selenium driver but is not installed")
                raise

            self.logger.info("Installing ChromeDriver...")
            service = Service(ChromeDriverManager().install())
            browser = webdriver.Chrome(service=service, options=chrome_options)
            
            # Execute script to remove webdriver property
            browser.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            self.logger.info("Chrome browser setup completed successfully")
            return browser
        except Exception as e:
            self.logger.error(f"Error setting up Chrome browser: {e}")
            raise

    def _setup_playwright(self):
        """Set up Playwright (sync) driver as an alternative to Selenium."""
        try:
            from playwright_driver import PlaywrightDriver
        except Exception:
            self.logger.error("Playwright is not installed. Install with: pip install playwright")
            raise

        self.logger.info("Setting up Playwright browser...")
        self.playwright = PlaywrightDriver(headless=self.headless, delay_range=self.delay_range, logger=self.logger)
        self.playwright.start()
        self.logger.info("Playwright browser setup completed successfully")
        return self.playwright

    def _make_soup(self, html: str) -> BeautifulSoup:
        """Create a BeautifulSoup object, preferring 'lxml' but falling back.

        This avoids hard failure when the 'lxml' parser isn't installed.
        """
        try:
            return BeautifulSoup(html, 'lxml')
        except Exception:
            # lxml may not be installed in the environment; fall back to the
            # built-in parser and log a helpful message.
            self.logger.warning("Couldn't use 'lxml' parser; falling back to 'html.parser'. To enable the faster 'lxml' parser install it with: pip install lxml")
            return BeautifulSoup(html, 'html.parser')

    def _detect_captcha_or_unusual_traffic(self, html: str) -> Optional[str]:
        """Heuristics to detect CAPTCHA / 'unusual traffic' / block pages returned by Google Scholar.

        Returns a short reason string when a blocking page is detected, otherwise None.
        """
        if not html:
            return None
        txt = html.lower()
        # common Google blocking / captcha indicators
        if 'our systems have detected unusual traffic' in txt or 'unusual traffic' in txt:
            return 'unusual traffic detected'
        if 'please type the characters you see in the image' in txt or 'to continue, please type' in txt:
            return 'captcha challenge'
        if 'recaptcha' in txt or 'g-recaptcha' in txt or 'id="captcha"' in txt:
            return 'captcha challenge'
        if "we're sorry" in txt and 'unusual traffic' in txt:
            return 'blocked - sorry/unusual traffic'
        return None

    def _random_delay(self):
        """Add a random delay between requests to avoid rate limiting."""
        delay = random.uniform(*self.delay_range)
        self.logger.debug(f"Waiting {delay:.1f} seconds...")
        time.sleep(delay)

    def _record_block(self, reason: str) -> None:
        """Record a blocking/captcha detection and increment the consecutive counter.

        This does NOT pause — calling code (e.g. the batch processor) decides when to
        pause or abort based on the counter and configuration.
        """
        self._blocked_reason = reason
        self._block_count = getattr(self, '_block_count', 0) + 1
        self.logger.warning(f"Google Scholar block detected: {reason} (count={self._block_count})")

    def _clear_block(self) -> None:
        """Clear block state after a successful fetch or manual reset."""
        if self._blocked_reason is not None or getattr(self, '_block_count', 0) > 0:
            self.logger.debug("Clearing Google Scholar block state")
        self._blocked_reason = None
        self._block_count = 0
    
    def _parse_publication_details_from_html(self, html: str) -> Optional[Dict]:
        """Parse publication details from HTML (shared parser used by both drivers)."""
        try:
            # detect blocking/captcha pages first
            block_reason = self._detect_captcha_or_unusual_traffic(html)
            if block_reason:
                self._record_block(block_reason)
                return None

            soup = self._make_soup(html)
            self.logger.info("Parsing publication details...")

            details = {}

            # Extract title - only set if present (don't overwrite a valid list title with 'N/A')
            title = soup.find('div', id='gsc_oci_title')
            if title and title.text and title.text.strip():
                details['title'] = title.text.strip()
                self.logger.info(f"Found title: {details['title']}")
            else:
                self.logger.debug("No title found on publication detail page; leaving existing title unchanged")

            # Extract fields (only add keys when we actually have values)
            fields = soup.find_all('div', class_='gs_scl')
            self.logger.info(f"Found {len(fields)} field sections to parse")

            for field in fields:
                field_name_elem = field.find('div', class_='gsc_oci_field')
                field_value_elem = field.find('div', class_='gsc_oci_value')

                if not field_name_elem or not field_value_elem:
                    continue

                name = field_name_elem.text.strip().lower()
                value = field_value_elem.text.strip()
                if not value:
                    continue

                self.logger.debug(f"Processing field: {name} = {value}")

                if name == 'authors':
                    authors = self._parse_authors_to_array(value)
                    if authors:
                        details['authors'] = authors
                        self.logger.info(f"Found {len(details['authors'])} authors: {details['authors']}")
                elif name == 'publication date':
                    details['publication_date'] = value
                    self.logger.info(f"Found publication date: {value}")
                elif name == 'description':
                    details['abstract'] = value
                    self.logger.info(f"Found abstract (length: {len(value)} chars)")
                elif name == 'total citations':
                    citation_info = field.find('a')
                    if citation_info and citation_info.text.strip():
                        details['total_citations'] = citation_info.text.strip()
                        self.logger.info(f"Found total citations: {details['total_citations']}")
                    else:
                        self.logger.debug("No citation info found in 'total citations' field")

            # Venue and PDF link helpers may return 'N/A' when absent; only include them
            # if they return useful values to avoid clobbering existing data.
            venue = self._extract_publication_venue(soup)
            if venue and venue != 'N/A':
                details['venue'] = venue

            pdf_link = self._extract_pdf_link(soup)
            if pdf_link and pdf_link != 'N/A':
                details['pdf_link'] = pdf_link

            self.logger.info("Publication details extraction completed")
            return details
        except Exception as e:
            self.logger.error(f"Error parsing publication HTML: {e}")
            return None

    def _get_publication_details(self, url: str) -> Optional[Dict]:
        """Fetch and parse publication details from its dedicated page (driver-agnostic)."""
        self.logger.info(f"Fetching publication details from: {url}")
        try:
            if self.driver == 'playwright':
                if not self.playwright:
                    self.logger.error("Playwright is not initialized")
                    return None
                self.playwright.get(url)
                self._random_delay()
                try:
                    self.playwright.wait_for_selector('#gsc_oci_title', timeout=10000)
                except Exception:
                    pass
                html = self.playwright.page_content()
                block_reason = self._detect_captcha_or_unusual_traffic(html)
                if block_reason:
                    self.logger.warning(f"Blocked by Google Scholar while fetching publication details (driver): {block_reason}; attempting httpx fallback...")
                    if self.use_httpx:
                        try:
                            fetched = asyncio.run(self._fetch_detail_via_httpx_async(url))
                            if fetched and not self._detect_captcha_or_unusual_traffic(fetched):
                                self._clear_block()
                                return self._parse_publication_details_from_html(fetched)
                        except Exception:
                            pass
                    self._record_block(block_reason)
                    self.logger.error(f"Blocked by Google Scholar while fetching publication details: {block_reason}")
                    return None
                return self._parse_publication_details_from_html(html)

            # Default: selenium
            if self.browser is None:
                self.logger.error("Browser is not initialized")
                return None

            self.browser.get(url)
            self._random_delay()
            WebDriverWait(self.browser, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            page_html = self.browser.page_source
            block_reason = self._detect_captcha_or_unusual_traffic(page_html)
            if block_reason:
                self.logger.warning(f"Blocked by Google Scholar while fetching publication details (driver): {block_reason}; attempting httpx fallback...")
                if self.use_httpx:
                    try:
                        fetched = asyncio.run(self._fetch_detail_via_httpx_async(url))
                        if fetched and not self._detect_captcha_or_unusual_traffic(fetched):
                            self._clear_block()
                            return self._parse_publication_details_from_html(fetched)
                    except Exception:
                        pass
                self._record_block(block_reason)
                self.logger.error(f"Blocked by Google Scholar while fetching publication details: {block_reason}")
                return None

            return self._parse_publication_details_from_html(page_html)

        except Exception as e:
            self.logger.error(f"Error fetching publication details from {url}: {e}")
            return None
    
    def _extract_pdf_link(self, soup: BeautifulSoup) -> str:
        """Extract PDF link from the publication page."""
        try:
            pdf_container = soup.find('div', id='gsc_oci_title_gg')
            if pdf_container:
                pdf_link = pdf_container.find('a')
                if pdf_link and hasattr(pdf_link, 'get'):
                    href = pdf_link.get('href')  # type: ignore
                    if href and isinstance(href, str):
                        self.logger.info(f"PDF link: {href}")
                        return href
                    else:
                        self.logger.info("No PDF link found in container")
                else:
                    self.logger.info("No PDF link element found")
            else:
                self.logger.info("No PDF link found")
        except Exception as e:
            self.logger.warning(f"Error extracting PDF link: {e}")
        
        return 'N/A'

    def _pick_user_agent(self) -> str:
        """Return a user-agent string (optional rotation).

        If the user provides a UA file we pick from that list. Otherwise rotate
        a small built-in set to reduce fingerprinting when running multiple
        requests in the same process.
        """
        builtin_uas = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
        ]
        if self.user_agents:
            return random.choice(self.user_agents)
        return random.choice(builtin_uas)

    def load_user_agents_from_file(self, path: str) -> None:
        """Load newline-separated user-agent strings from a file."""
        uas: List[str] = []
        with open(path, 'r', encoding='utf-8') as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    uas.append(ln)
        if not uas:
            raise ValueError("User-agent file is empty")
        self.user_agents = uas
        self.logger.info(f"Loaded {len(uas)} user-agents from {path}")

    def load_proxies_from_file(self, path: str) -> None:
        """Load newline-separated proxy servers (e.g. http://host:port) from a file."""
        proxies: List[str] = []
        with open(path, 'r', encoding='utf-8') as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    proxies.append(ln)
        if not proxies:
            raise ValueError("Proxy file is empty")
        self.proxies = proxies
        self.logger.info(f"Loaded {len(proxies)} proxies from {path}")

    def _pick_proxy(self) -> Optional[str]:
        """Return a proxy URL (random rotation)."""
        if not self.proxies:
            return None
        return random.choice(self.proxies)

    async def _fetch_detail_via_httpx_async(self, url: str) -> Optional[str]:
        """Try to fetch a publication detail page over HTTP (async) with retries/backoff.

        Enhancements:
        - jittered exponential backoff
        - treat HTTP 429 / redirect-to-'/sorry' as transient blocking and retry
        - try direct first, then fall back to configured proxies on subsequent attempts
        - rotate UA on every attempt
        """
        try:
            import httpx
        except Exception:
            self.logger.debug("httpx not installed; skipping httpx fast path")
            return None

        attempt = 0
        while attempt < self.max_retries:
            # rotate user-agent each attempt
            headers = {"User-Agent": self._pick_user_agent()}

            # If proxies are configured, prefer a direct attempt first and then
            # use a proxy on subsequent retries (proxy-fallback behavior).
            proxy = None
            if self.proxies:
                proxy = None if attempt == 0 else self._pick_proxy()
            else:
                proxy = None

            try:
                async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                    if proxy:
                        resp = await client.get(url, headers=headers, proxies=proxy)
                    else:
                        resp = await client.get(url, headers=headers)

                    # handle successful HTTP response body
                    if resp.status_code == 200 and resp.text:
                        # check for blocking pages inside body
                        block_reason = self._detect_captcha_or_unusual_traffic(resp.text)
                        if block_reason:
                            self._record_block(block_reason)
                            self.logger.warning(f"[httpx] blocking detected (attempt {attempt + 1}): {block_reason}; retrying with different UA/proxy")
                        else:
                            self._clear_block()
                            return resp.text

                    # treat rate-limiting / server-side throttling as transient
                    if resp.status_code == 429:
                        self._record_block('httpx 429')
                        self.logger.warning(f"[httpx] received 429 Too Many Requests for {url} (attempt {attempt + 1}); will retry")
                    elif 500 <= resp.status_code < 600:
                        self.logger.warning(f"[httpx] server error {resp.status_code} for {url} (attempt {attempt + 1}); will retry")
                    else:
                        # other non-200 responses logged for debugging
                        self.logger.debug(f"httpx fetch status={resp.status_code} for {url}")

            except Exception as e:
                self.logger.debug(f"httpx fetch error (attempt {attempt + 1}) for {url}: {e}")

            # jittered exponential backoff before next attempt
            jitter = random.uniform(0.8, 1.25)
            sleep_time = self.backoff_factor * (2 ** attempt) * jitter
            self.logger.debug(f"[httpx] sleeping {sleep_time:.2f}s before retry (attempt {attempt + 1})")
            await asyncio.sleep(sleep_time)
            attempt += 1

        return None

    async def _fetch_all_details_async(self, publications: List[Dict]) -> List[int]:
        """Concurrent HTTP fetching of publication detail pages. Returns indices that failed and need driver fallback."""
        sem = asyncio.Semaphore(self.concurrency)
        failed_indices: List[int] = []

        async def _worker(idx: int, pub: Dict):
            async with sem:
                url = pub.get('citation_url')
                if not url:
                    failed_indices.append(idx)
                    return

                html = await self._fetch_detail_via_httpx_async(url)
                if html:
                    details = self._parse_publication_details_from_html(html)
                    if details:
                        pub.update(details)
                        self.logger.info(f"[httpx] ✓ Updated publication {idx + 1}: {pub.get('title', '')[:40]}")
                        return
                # mark for fallback
                failed_indices.append(idx)

        tasks = [asyncio.create_task(_worker(i, p)) for i, p in enumerate(publications)]
        await asyncio.gather(*tasks)
        return failed_indices

    async def _fetch_details_with_playwright_async(self, publications: List[Dict], indices: List[int]) -> Tuple[int, int]:
        """Use Playwright (async) to concurrently fetch pages for JS-only fallbacks.

        Returns a tuple (successful_count, failed_count).
        """
        try:
            from playwright.async_api import async_playwright
        except Exception as e:
            self.logger.warning(f"Playwright async not available: {e}")
            return 0, len(indices)

        success_holder = [0]
        fail_holder = [0]
        sem = asyncio.Semaphore(self.concurrency)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            async def _worker(idx: int):
                async with sem:
                    url = publications[idx].get('citation_url')
                    if not url:
                        fail_holder[0] += 1
                        return

                    # create a context per worker if proxies are used, otherwise reuse a shared context
                    proxy = self._pick_proxy()
                    if proxy:
                        context = await browser.new_context(user_agent=self._pick_user_agent(), ignore_https_errors=True, proxy={"server": proxy})
                    else:
                        # create a shared context lazily
                        nonlocal_context = getattr(_worker, "_shared_context", None)
                        if nonlocal_context is None:
                            _worker._shared_context = await browser.new_context(user_agent=self._pick_user_agent(), ignore_https_errors=True)
                        context = _worker._shared_context

                        page = await context.new_page()
                    try:
                        await page.goto(url, wait_until='load')
                        html = await page.content()
                        # detect blocking on JS-driven detail pages
                        block_reason = self._detect_captcha_or_unusual_traffic(html)
                        if block_reason:
                            self._record_block(block_reason)
                            self.logger.error(f"Blocked by Google Scholar while fetching publication details (playwright): {block_reason}")
                            fail_holder[0] += 1
                            return

                        details = self._parse_publication_details_from_html(html)
                        if details:
                            publications[idx].update(details)
                            success_holder[0] += 1
                        else:
                            fail_holder[0] += 1
                    except Exception as e:
                        self.logger.debug(f"Playwright fetch error for {url}: {e}")
                        fail_holder[0] += 1
                    finally:
                        await page.close()
                        if proxy:
                            try:
                                await context.close()
                            except Exception:
                                pass

            tasks = [asyncio.create_task(_worker(i)) for i in indices]
            await asyncio.gather(*tasks)

            # close shared context if present
            if hasattr(_worker, '_shared_context'):
                try:
                    await _worker._shared_context.close()
                except Exception:
                    pass

        return success_holder[0], fail_holder[0]

    def _fetch_details_concurrently(self, publications: List[Dict]) -> Tuple[int, int]:
        """Public synchronous entry that performs concurrent HTTP fetches and falls back to driver for misses."""
        successful = 0
        failed = 0

        if self.use_httpx:
            try:
                failed_indices = asyncio.run(self._fetch_all_details_async(publications))
            except Exception as e:
                self.logger.warning(f"Async httpx fetch failed, falling back to sequential driver: {e}")
                failed_indices = list(range(len(publications)))

            # Count successful updates from httpx
            for i, p in enumerate(publications):
                # we consider that a publication updated if it contains 'title' different from initial stub
                if i not in failed_indices and p.get('title') and not p.get('title').startswith('N/A'):
                    successful += 1

            # Fallback to driver for failed indices
            if failed_indices:
                self.logger.info(f"Falling back to driver for {len(failed_indices)} publications")

                if self.driver == 'playwright':
                    try:
                        pw_success, pw_failed = asyncio.run(self._fetch_details_with_playwright_async(publications, failed_indices))
                        successful += pw_success
                        failed += pw_failed
                    except Exception as e:
                        self.logger.warning(f"Playwright async fallback failed: {e}; falling back to sequential driver.")
                        # sequential fallback to whichever driver is configured
                        for idx in failed_indices:
                            pub = publications[idx]
                            details = self._get_publication_details(pub['citation_url'])
                            if details:
                                pub.update(details)
                                successful += 1
                            else:
                                failed += 1
                            self._random_delay()
                else:
                    for idx in failed_indices:
                        pub = publications[idx]
                        details = self._get_publication_details(pub['citation_url'])
                        if details:
                            pub.update(details)
                            successful += 1
                        else:
                            failed += 1
                        self._random_delay()

            return successful, failed

        # If httpx disabled, do sequential driver-based fetching
        for i, pub in enumerate(publications, 1):
            details = self._get_publication_details(pub['citation_url'])
            if details:
                pub.update(details)
                successful += 1
            else:
                failed += 1
            if i < len(publications):
                self._random_delay()

        return successful, failed
    
    def _load_all_publications(self, base_url: str) -> None:
        """Load all publications by clicking the 'Load More' button."""
        self.logger.info(f"Navigating to: {base_url}")
        if self.driver == 'playwright':
            if not self.playwright:
                self.logger.error("Playwright is not initialized")
                return

            self.playwright.get(base_url)
            self._random_delay()

            # detect blocking/captcha immediately after page load; try httpx fallback if blocked
            block_reason = self._detect_captcha_or_unusual_traffic(self.playwright.page_content())
            if block_reason:
                self.logger.warning(f"Blocked by Google Scholar while loading profile (driver): {block_reason}; attempting httpx fallback...")

                if self.use_httpx:
                    try:
                        html = asyncio.run(self._fetch_detail_via_httpx_async(base_url))
                        if html and not self._detect_captcha_or_unusual_traffic(html):
                            self._profile_html_cache = html
                            self._clear_block()
                            self.logger.info("Successfully fetched profile HTML via httpx fallback")
                            return
                    except Exception as e:
                        self.logger.debug(f"httpx fallback failed: {e}")

                # fallback failed — record a block and stop
                self._record_block(block_reason)
                self.logger.error(f"Blocked by Google Scholar while loading profile: {block_reason}")
                return

        # Selenium fallback (existing behavior)
        if self.browser is None:
            self.logger.error("Browser is not initialized")
            return
            
        self.browser.get(base_url)
        self._random_delay()

        # detect blocking/captcha immediately after page load; try httpx fallback if blocked
        block_reason = self._detect_captcha_or_unusual_traffic(self.browser.page_source)
        if block_reason:
            self.logger.warning(f"Blocked by Google Scholar while loading profile (driver): {block_reason}; attempting httpx fallback...")

            if self.use_httpx:
                try:
                    html = asyncio.run(self._fetch_detail_via_httpx_async(base_url))
                    if html and not self._detect_captcha_or_unusual_traffic(html):
                        self._profile_html_cache = html
                        self._clear_block()
                        self.logger.info("Successfully fetched profile HTML via httpx fallback")
                        return
                except Exception as e:
                    self.logger.debug(f"httpx fallback failed: {e}")

            # fallback failed — record a block and stop
            self._record_block(block_reason)
            self.logger.error(f"Blocked by Google Scholar while loading profile: {block_reason}")
            return

        # Click the "Load More" button until it's no longer present
        load_count = 0
        self.logger.info("Loading all publications by clicking 'Load More' button...")
        
        while True:
            try:
                load_more_button = WebDriverWait(self.browser, 10).until(
                    EC.element_to_be_clickable((By.ID, "gsc_bpf_more"))
                )
                if load_more_button.is_enabled():
                    # Scroll to the button to ensure it's visible
                    self.browser.execute_script("arguments[0].scrollIntoView();", load_more_button)
                    self._random_delay()
                    
                    # Click the button
                    load_more_button.click()
                    load_count += 1
                    self.logger.info(f"Clicked 'Load More' button (attempt {load_count})")
                    
                    # Wait for new content to load
                    self._random_delay()
                else:
                    self.logger.info("'Load More' button is no longer enabled")
                    break
            except Exception as e:
                self.logger.info(f"No more 'Load More' button found or error occurred: {e}")
                break
    
    def _parse_publication_list(self, html: Optional[str] = None) -> List[Dict]:
        """Parse the publication list from the loaded page or provided HTML."""
        self.logger.info("Parsing publication list from page...")

        # If we previously fetched profile HTML via httpx fallback, prefer that
        if html is None and self._profile_html_cache:
            html = self._profile_html_cache
            self._profile_html_cache = None

        if html is None:
            if self.driver == 'playwright':
                if not self.playwright:
                    self.logger.error("Playwright is not initialized")
                    return []
                raw_html = self.playwright.page_content()
            else:
                if self.browser is None:
                    self.logger.error("Browser is not initialized")
                    return []
                raw_html = self.browser.page_source
            # detect blocking / captcha pages early
            block_reason = self._detect_captcha_or_unusual_traffic(raw_html)
            if block_reason:
                self._record_block(block_reason)
                self.logger.error(f"Blocked by Google Scholar while loading profile: {block_reason}")
                return []
            soup = self._make_soup(raw_html)
        else:
            block_reason = self._detect_captcha_or_unusual_traffic(html)
            if block_reason:
                self._record_block(block_reason)
                self.logger.error(f"Blocked by Google Scholar while loading profile: {block_reason}")
                return []
            soup = self._make_soup(html)

        publications = []
        
        # Find publication rows by class name (some Scholar profiles use <tr> while
        # others render row-like blocks as <div class="gsc_a_tr">). Using
        # `find_all(class_='gsc_a_tr')` covers both table-based and div-based
        # layouts so we don't miss publications for certain profiles.
        publication_rows = soup.find_all(class_='gsc_a_tr')
        self.logger.info(f"Found {len(publication_rows)} publication rows to process")
        
        for i, row in enumerate(publication_rows, 1):
            title_element = row.find('a', class_='gsc_a_at')
            if not title_element:
                self.logger.warning(f"Row {i}: No title element found, skipping")
                continue

            # Extract basic publication info
            authors_elem = row.find('div', class_='gs_gray')
            cited_by_elem = row.find('a', class_='gsc_a_ac')
            year_elem = row.find('span', class_='gsc_a_h')
            
            # Try to extract venue from the publication row
            venue_elem = row.find('div', class_='gs_gray')
            venue = 'N/A'
            if venue_elem:
                # The venue might be in the same element as authors, separated by some delimiter
                venue_text = venue_elem.text.strip()
                # Look for common venue indicators in the text
                if any(indicator in venue_text.lower() for indicator in ['journal', 'conference', 'proceedings', 'transactions']):
                    venue = venue_text
            
            pub = {
                'title': title_element.text.strip(),
                'authors': self._parse_authors_to_array(authors_elem.text.strip() if authors_elem else 'N/A'),
                'cited_by': cited_by_elem.text.strip() if cited_by_elem else '0',
                'year': year_elem.text.strip() if year_elem else 'N/A',
                'venue': venue,
                'citation_url': 'https://scholar.google.com' + title_element.get('href', '')
            }
            publications.append(pub)
            self.logger.info(f"Row {i}: Added publication '{pub['title'][:50]}...' (Year: {pub['year']}, Citations: {pub['cited_by']}, Authors: {len(pub['authors'])})")

        self.logger.info(f"Successfully parsed {len(publications)} publications from the main page")
        return publications
    
    def scrape_profile(self, user_id: str) -> Optional[List[Dict]]:
        """Scrape a Google Scholar profile for all publications and their details."""
        self.logger.info(f"Starting to scrape Google Scholar profile for user: {user_id}")
        
        try:
            base_url = f"https://scholar.google.com/citations?user={user_id}&hl=en"

            # Set up the chosen driver
            if self.driver == 'playwright':
                self._setup_playwright()
            else:
                self.browser = self._setup_browser()

            # Load all publications
            self._load_all_publications(base_url)
            
            # Parse the publication list
            publications = self._parse_publication_list()
            
            if not publications:
                self.logger.warning("No publications found on the profile")
                return []

            # Scrape detailed information for each publication (concurrent httpx where possible)
            self.logger.info("Starting to fetch detailed information for each publication (concurrent)...")

            successful_details, failed_details = self._fetch_details_concurrently(publications)

            self.logger.info(f"Detail scraping completed: {successful_details} successful, {failed_details} failed")
            self.logger.info(f"Total publications processed: {len(publications)}")
            return publications

        except Exception as e:
            self.logger.error(f"Error during profile scraping: {e}")
            return None
        finally:
            if self.driver == 'playwright' and self.playwright:
                self.logger.info("Closing Playwright browser...")
                try:
                    self.playwright.stop()
                except Exception:
                    pass
            elif self.browser:
                self.logger.info("Closing browser...")
                self.browser.quit()
    
    def save_to_json(self, data: List[Dict], user_id: str, output_dir: str = "output", name: Optional[str] = None) -> str:
        """Save scraped data to a JSON file.

        Default output directory is `./output` (created if missing).
        If `name` is provided the filename becomes `<user_id>_<name>_scholar_data.json`.
        """
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

        # Build filename; sanitize `name` to avoid path separators or strange chars
        if name:
            safe_name = ''.join(c if (c.isalnum() or c in ('-', '_')) else '_' for c in name)
            output_file = os.path.join(output_dir, f"{user_id}_{safe_name}_scholar_data.json")
        else:
            output_file = os.path.join(output_dir, f'{user_id}_scholar_data.json')

        self.logger.info(f"Saving {len(data)} publications to {output_file}...")
        
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            self.logger.info(f"✓ Successfully saved data to {output_file}")
            return output_file
        except Exception as e:
            self.logger.error(f"Error saving data to {output_file}: {e}")
            raise

    def load_authors_from_csv(self, csv_file: str) -> List[Tuple[str, str]]:
        """
        Load authors and their Google Scholar IDs from a CSV file.

        This method is tolerant of several common CSV formats. It accepts either
        the canonical `name,user_id` headers or variants such as
        `Nama,GoogleScholarID,Status` (case-insensitive).

        Args:
            csv_file: Path to the CSV file

        Returns:
            List of tuples containing (name, user_id). Rows missing a scholar id are skipped.
        """
        authors: List[Tuple[str, str]] = []
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)

                if not reader.fieldnames:
                    raise ValueError("CSV file has no header row")

                # Normalize fieldnames -> map lowercase -> original
                fld_map = {fn.strip().lower(): fn for fn in reader.fieldnames}

                # possible column names for name and user id (lowercased)
                possible_name_cols = ['name', 'nama', 'full_name', 'display_name']
                possible_id_cols = ['user_id', 'google_scholar_id', 'googlescholarid', 'googleid', 'scholar_id', 'googlescholarid']

                name_col = next((fld_map[c] for c in possible_name_cols if c in fld_map), None)
                id_col = next((fld_map[c] for c in possible_id_cols if c in fld_map), None)

                if not name_col or not id_col:
                    raise ValueError(
                        "CSV must contain a name column (name|Nama) and a scholar id column (user_id|GoogleScholarID)"
                    )

                for row in reader:
                    raw_name = (row.get(name_col) or '').strip()
                    raw_id = (row.get(id_col) or '').strip()

                    if raw_name and raw_id:
                        authors.append((raw_name, raw_id))
                        self.logger.info(f"Loaded author: {raw_name} (ID: {raw_id})")
                    else:
                        self.logger.info(f"Skipping row without a Google Scholar ID: {row}")

            self.logger.info(f"Successfully loaded {len(authors)} authors from {csv_file}")
            return authors

        except Exception as e:
            self.logger.error(f"Error reading CSV file {csv_file}: {e}")
            raise

    def process_authors_batch(self, csv_file: str, output_dir: str = "output", author_concurrency: int = 1, label: Optional[str] = None) -> Dict[str, bool]:
        """
        Process multiple authors from a CSV file.

        Args:
            csv_file: Path to the CSV file containing author information
            output_dir: Directory to save output files (default: ./output)
            author_concurrency: Number of author profiles to process in parallel (default: 1)
            label: Optional label to include in output filenames (e.g. 'labA'). If provided,
                   filenames will be `<user_id>_<label>_scholar_data.json`.

        Returns:
            Dictionary mapping user_id to success status (True/False)
        """
        self.logger.info(f"Starting batch processing from CSV: {csv_file}")
        
        try:
            authors = self.load_authors_from_csv(csv_file)
        except Exception as e:
            self.logger.error(f"Failed to load authors from CSV: {e}")
            return {}
        
        if not authors:
            self.logger.warning("No authors found in CSV file")
            return {}

        results: Dict[str, bool] = {}
        total_authors = len(authors)

        # If author_concurrency == 1, keep the simple sequential flow (preserves previous behavior)
        if author_concurrency <= 1:
            for i, (name, user_id) in enumerate(authors, 1):
                self.logger.info(f"Processing author {i}/{total_authors}: {name} (ID: {user_id})")

                try:
                    # Scrape the profile (uses this instance's configuration)
                    scholar_data = self.scrape_profile(user_id)

                    if scholar_data:
                        # Save to JSON
                        output_file = self.save_to_json(scholar_data, user_id, output_dir, name=label)
                        results[user_id] = True
                        self.logger.info(f"✓ Successfully processed {name}: {len(scholar_data)} publications saved to {output_file}")
                    else:
                        results[user_id] = False
                        self.logger.error(f"✗ Failed to scrape data for {name} (ID: {user_id})")

                except Exception as e:
                    results[user_id] = False
                    self.logger.error(f"✗ Error processing {name} (ID: {user_id}): {e}")

                # Add delay between authors to avoid rate limiting
                if i < total_authors:
                    self.logger.info("Waiting between authors...")
                    self._random_delay()

                # If we have seen persistent blocking, optionally pause the batch so
                # the operator or automated system can recover (rotate IP/UA, etc.).
                if self._block_count >= self.block_retry_limit and self.pause_on_block:
                    self.logger.warning(
                        f"Persistent Google Scholar blocking detected (count={self._block_count}). Pausing for {self.blocked_pause_seconds} seconds..."
                    )
                    time.sleep(self.blocked_pause_seconds)
                    # clear the block state so we can continue after the pause
                    self._clear_block()

        else:
            # Concurrent author processing using threads; each worker instantiates its own scraper
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _worker(name: str, user_id: str) -> Tuple[str, bool]:
                """Worker that creates a fresh scraper instance and runs it for a single author."""
                try:
                    child = GoogleScholarScraper(headless=self.headless, delay_range=self.delay_range, driver=self.driver)
                    # copy relevant runtime options
                    child.use_httpx = self.use_httpx
                    child.concurrency = self.concurrency
                    child.max_retries = self.max_retries
                    child.backoff_factor = self.backoff_factor
                    child.user_agents = self.user_agents

                    self.logger.info(f"[worker] Starting scrape for {name} (ID: {user_id})")
                    scholar_data = child.scrape_profile(user_id)

                    if scholar_data:
                        child.save_to_json(scholar_data, user_id, output_dir, name=label)
                        self.logger.info(f"[worker] ✓ {name} ({user_id}) -> {len(scholar_data)} pubs")
                        return user_id, True
                    else:
                        self.logger.warning(f"[worker] ✗ No data for {name} ({user_id})")
                        return user_id, False
                except Exception as e:
                    self.logger.error(f"[worker] ✗ Error processing {name} ({user_id}): {e}")
                    return user_id, False

            with ThreadPoolExecutor(max_workers=author_concurrency) as exe:
                futures = {exe.submit(_worker, name, uid): (name, uid) for name, uid in authors}

                for fut in as_completed(futures):
                    uid, ok = fut.result()
                    results[uid] = ok

        # Print summary
        successful = sum(1 for success in results.values() if success)
        failed = len(results) - successful

        self.logger.info("=" * 60)
        self.logger.info(f"BATCH PROCESSING SUMMARY:")
        self.logger.info(f"Total authors: {total_authors}")
        self.logger.info(f"Successful: {successful}")
        self.logger.info(f"Failed: {failed}")
        self.logger.info("=" * 60)

        return results
    
    def _extract_publication_venue(self, soup: BeautifulSoup) -> str:
        """Extract and standardize publication venue/source from the publication page."""
        try:
            # Look for various possible venue field names
            venue_field_names = ['journal', 'conference', 'publisher', 'source', 'venue']
            
            for field_name in venue_field_names:
                # Find field by exact match or partial match
                fields = soup.find_all('div', class_='gsc_oci_field')
                for field in fields:
                    if field.text and field_name.lower() in field.text.lower():
                        # Get the corresponding value
                        value_elem = field.find_next_sibling('div', class_='gsc_oci_value')
                        if value_elem:
                            venue = value_elem.text.strip()
                            self.logger.info(f"Found venue from '{field_name}': {venue}")
                            return venue
            
            # If no specific venue field found, try to extract from other common patterns
            # Look for any field that might contain venue information
            all_fields = soup.find_all('div', class_='gs_scl')
            for field in all_fields:
                field_name_elem = field.find('div', class_='gsc_oci_field')
                if field_name_elem:
                    field_name = field_name_elem.text.strip().lower()
                    # Skip fields we already handle
                    if field_name in ['authors', 'publication date', 'description', 'total citations']:
                        continue
                    
                    # Check if this field might contain venue information
                    field_value_elem = field.find('div', class_='gsc_oci_value')
                    if field_value_elem:
                        value = field_value_elem.text.strip()
                        # If the value looks like a venue (contains common venue indicators)
                        if any(indicator in value.lower() for indicator in ['journal', 'conference', 'proceedings', 'transactions', 'letters', 'review']):
                            self.logger.info(f"Found potential venue from '{field_name}': {value}")
                            return value
            
            self.logger.info("No venue information found")
            return 'N/A'
            
        except Exception as e:
            self.logger.warning(f"Error extracting publication venue: {e}")
            return 'N/A'
    
    def _parse_authors_to_array(self, authors_string: str) -> List[str]:
        """Convert authors string to an array of individual author names."""
        if not authors_string or authors_string == 'N/A':
            return []
        
        try:
            # Split by common delimiters and clean up
            # Handle various formats: "Author1, Author2", "Author1; Author2", "Author1 and Author2"
            authors = []
            
            # First, try to split by semicolon
            if ';' in authors_string:
                authors = [author.strip() for author in authors_string.split(';')]
            # Then try comma (but be careful with "Last, First" names)
            elif ',' in authors_string:
                # Split by comma and handle potential "Last, First" format
                parts = [part.strip() for part in authors_string.split(',')]
                authors = []
                i = 0
                while i < len(parts):
                    if i + 1 < len(parts) and len(parts[i + 1].split()) == 1:
                        # This might be "Last, First" format
                        authors.append(f"{parts[i]}, {parts[i + 1]}")
                        i += 2
                    else:
                        authors.append(parts[i])
                        i += 1
            # Then try "and"
            elif ' and ' in authors_string:
                authors = [author.strip() for author in authors_string.split(' and ')]
            else:
                # Single author or unknown format
                authors = [authors_string.strip()]
            
            # Clean up and filter out empty entries
            authors = [author for author in authors if author and author.strip()]
            
            self.logger.debug(f"Parsed authors: {authors}")
            return authors
            
        except Exception as e:
            self.logger.warning(f"Error parsing authors string '{authors_string}': {e}")
            return [authors_string] if authors_string else []


def main():
    """Main function to run the scraper."""
    parser = argparse.ArgumentParser(description="Scrape Google Scholar profiles.")
    parser.add_argument("--user-id", help="The Google Scholar user ID to scrape (single author mode)")
    parser.add_argument("--csv-file", help="Path to CSV file containing multiple authors (batch mode)")
    parser.add_argument("--output-dir", default="output", help="Output directory for JSON files (default: ./output)")
    parser.add_argument("--name", default=None, help="Optional label to include in output filename (e.g. lab_name)")
    parser.add_argument("--no-headless", action="store_true", help="Run browser in non-headless mode")
    parser.add_argument("--delay-min", type=float, default=3.0, help="Minimum delay between requests (seconds)")
    parser.add_argument("--delay-max", type=float, default=7.0, help="Maximum delay between requests (seconds)")
    parser.add_argument("--driver", choices=["selenium", "playwright"], default="selenium", help="Browser driver to use (selenium or playwright)")
    parser.add_argument("--concurrency", type=int, default=8, help="Per-profile concurrency for fetching publication details (default: 8)")
    parser.add_argument("--user-agent-file", help="Path to a newline-separated user-agent file (optional)")
    parser.add_argument("--generate-ua-file", help="Write a default user-agent file to PATH and exit", metavar="PATH")
    parser.add_argument("--proxy-file", help="Path to a newline-separated proxy file (optional)")
    parser.add_argument("--proxy", help="Single proxy URL to use for all requests (overrides proxy-file)")
    parser.add_argument("--author-concurrency", type=int, default=1, help="How many author profiles to process in parallel (default: 1)")
    parser.add_argument("--no-pause-on-block", action="store_true", help="Do not pause when persistent Google Scholar blocks are detected")
    parser.add_argument("--block-retry-limit", type=int, default=3, help="Number of blocking detections to tolerate before pausing (default: 3)")
    parser.add_argument("--block-pause-seconds", type=float, default=300.0, help="Seconds to pause when a persistent block is detected (default: 300)")

    args = parser.parse_args()

    # Validate arguments
    if not args.user_id and not args.csv_file:
        parser.error("Either --user-id or --csv-file must be specified")
    if args.user_id and args.csv_file:
        parser.error("Cannot specify both --user-id and --csv-file")

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("GOOGLE SCHOLAR SCRAPER STARTED")
    logger.info("=" * 60)

    # Create scraper instance
    scraper = GoogleScholarScraper(
        headless=not args.no_headless,
        delay_range=(args.delay_min, args.delay_max),
        driver=args.driver
    )

    # CLI-configurable runtime options
    scraper.concurrency = args.concurrency
    scraper.pause_on_block = not args.no_pause_on_block
    scraper.block_retry_limit = max(1, args.block_retry_limit)
    scraper.blocked_pause_seconds = max(0.0, float(args.block_pause_seconds))

    # UA file generation (write and exit)
    if args.generate_ua_file:
        default_uas = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
        ]
        with open(args.generate_ua_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(default_uas))
        logger.info(f"Wrote default user-agent file: {args.generate_ua_file}")
        return

    # load UA list if provided
    if args.user_agent_file:
        try:
            scraper.load_user_agents_from_file(args.user_agent_file)
        except Exception as e:
            logger.error(f"Failed to load user-agent file: {e}")
            return

    # load proxies if provided
    if args.proxy_file:
        try:
            scraper.load_proxies_from_file(args.proxy_file)
        except Exception as e:
            logger.error(f"Failed to load proxy file: {e}")
            return
    elif args.proxy:
        scraper.proxies = [args.proxy]

    try:
        if args.csv_file:
            # Batch processing mode
            logger.info(f"Starting batch processing from CSV: {args.csv_file}")
            results = scraper.process_authors_batch(
                args.csv_file,
                args.output_dir,
                author_concurrency=args.author_concurrency,
                label=args.name,
            )
            
            if results:
                successful = sum(1 for success in results.values() if success)
                logger.info(f"Batch processing completed: {successful}/{len(results)} authors successful")
            else:
                logger.error("No authors were processed")
        else:
            # Single author mode
            logger.info(f"Starting single author scraping for user ID: {args.user_id}")
            scholar_data = scraper.scrape_profile(args.user_id)

            if scholar_data:
                try:
                    output_file = scraper.save_to_json(scholar_data, args.user_id, args.output_dir, name=args.name)
                    logger.info(f"Total publications scraped: {len(scholar_data)}")
                    logger.info(f"Data saved to: {output_file}")
                except Exception as e:
                    logger.error(f"Failed to save data: {e}")
            else:
                logger.error("✗ Failed to scrape any data")

    except Exception as e:
        logger.error(f"Unexpected error: {e}")

    logger.info("=" * 60)
    logger.info("GOOGLE SCHOLAR SCRAPER FINISHED")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

