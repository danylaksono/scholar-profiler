
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
        self.max_retries = 3
        self.backoff_factor = 0.5
        self.user_agents = None  # optional list of UAs for rotation

        # runtime objects
        self.browser = None
        self.playwright = None
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
    
    def _random_delay(self):
        """Add a random delay between requests to avoid rate limiting."""
        delay = random.uniform(*self.delay_range)
        self.logger.debug(f"Waiting {delay:.1f} seconds...")
        time.sleep(delay)
    
    def _parse_publication_details_from_html(self, html: str) -> Optional[Dict]:
        """Parse publication details from HTML (shared parser used by both drivers)."""
        try:
            soup = self._make_soup(html)
            self.logger.info("Parsing publication details...")

            details = {}

            # Extract title
            title = soup.find('div', id='gsc_oci_title')
            details['title'] = title.text.strip() if title else 'N/A'
            self.logger.info(f"Found title: {details['title']}")

            # Extract fields
            fields = soup.find_all('div', class_='gs_scl')
            self.logger.info(f"Found {len(fields)} field sections to parse")

            for field in fields:
                field_name_elem = field.find('div', class_='gsc_oci_field')
                field_value_elem = field.find('div', class_='gsc_oci_value')

                if not field_name_elem or not field_value_elem:
                    continue

                name = field_name_elem.text.strip().lower()
                value = field_value_elem.text.strip()
                self.logger.debug(f"Processing field: {name} = {value}")

                if name == 'authors':
                    details['authors'] = self._parse_authors_to_array(value)
                    self.logger.info(f"Found {len(details['authors'])} authors: {details['authors']}")
                elif name == 'publication date':
                    details['publication_date'] = value
                    self.logger.info(f"Found publication date: {value}")
                elif name == 'description':
                    details['abstract'] = value
                    self.logger.info(f"Found abstract (length: {len(value)} chars)")
                elif name == 'total citations':
                    citation_info = field.find('a')
                    if citation_info:
                        details['total_citations'] = citation_info.text.strip()
                        self.logger.info(f"Found total citations: {details['total_citations']}")
                    else:
                        details['total_citations'] = 'N/A'
                        self.logger.warning("No citation info found")

            details['venue'] = self._extract_publication_venue(soup)
            details['pdf_link'] = self._extract_pdf_link(soup)

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

            return self._parse_publication_details_from_html(self.browser.page_source)

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
        """Return a user-agent string (optional rotation)."""
        default_ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        if self.user_agents:
            return random.choice(self.user_agents)
        return default_ua

    async def _fetch_detail_via_httpx_async(self, url: str) -> Optional[str]:
        """Try to fetch a publication detail page over HTTP (async) with retries/backoff."""
        try:
            import httpx
        except Exception:
            self.logger.debug("httpx not installed; skipping httpx fast path")
            return None

        headers = {"User-Agent": self._pick_user_agent()}
        attempt = 0
        while attempt < self.max_retries:
            try:
                async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200 and resp.text:
                        return resp.text
                    self.logger.debug(f"httpx fetch status={resp.status_code} for {url}")
            except Exception as e:
                self.logger.debug(f"httpx fetch error (attempt {attempt + 1}) for {url}: {e}")

            # backoff
            await asyncio.sleep(self.backoff_factor * (2 ** attempt))
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
            context = await browser.new_context(user_agent=self._pick_user_agent(), ignore_https_errors=True)

            async def _worker(idx: int):
                async with sem:
                    page = await context.new_page()
                    url = publications[idx].get('citation_url')
                    if not url:
                        fail_holder[0] += 1
                        await page.close()
                        return
                    try:
                        await page.goto(url, wait_until='load')
                        html = await page.content()
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

            tasks = [asyncio.create_task(_worker(i)) for i in indices]
            await asyncio.gather(*tasks)

            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
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

            load_count = 0
            self.logger.info("Loading all publications by clicking 'Load More' button (playwright)...")
            while True:
                try:
                    btn = self.playwright.query_selector('#gsc_bpf_more')
                    if btn and self.playwright.locator_is_enabled('#gsc_bpf_more'):
                        self.playwright.click('#gsc_bpf_more')
                        load_count += 1
                        self.logger.info(f"Clicked 'Load More' button (attempt {load_count})")
                        self._random_delay()
                    else:
                        break
                except Exception as e:
                    self.logger.info(f"No more 'Load More' button found or error occurred: {e}")
                    break
            return

        # Selenium fallback (existing behavior)
        if self.browser is None:
            self.logger.error("Browser is not initialized")
            return
            
        self.browser.get(base_url)
        self._random_delay()

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

        if html is None:
            if self.driver == 'playwright':
                if not self.playwright:
                    self.logger.error("Playwright is not initialized")
                    return []
                soup = self._make_soup(self.playwright.page_content())
            else:
                if self.browser is None:
                    self.logger.error("Browser is not initialized")
                    return []
                soup = self._make_soup(self.browser.page_source)
        else:
            soup = self._make_soup(html)

        publications = []
        
        publication_rows = soup.find_all('tr', class_='gsc_a_tr')
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
    
    def save_to_json(self, data: List[Dict], user_id: str, output_dir: str = "output") -> str:
        """Save scraped data to a JSON file.

        Default output directory is `./output` (created if missing).
        """
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        
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
        
        Expected CSV format:
        name,user_id
        "John Doe","abc123"
        "Jane Smith","def456"
        
        Args:
            csv_file: Path to the CSV file
            
        Returns:
            List of tuples containing (name, user_id)
        """
        authors = []
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                # Check if required columns exist
                if not reader.fieldnames or 'name' not in reader.fieldnames or 'user_id' not in reader.fieldnames:
                    raise ValueError("CSV must contain 'name' and 'user_id' columns")
                
                for row in reader:
                    name = row['name'].strip()
                    user_id = row['user_id'].strip()
                    
                    if name and user_id:  # Skip empty rows
                        authors.append((name, user_id))
                        self.logger.info(f"Loaded author: {name} (ID: {user_id})")
                    else:
                        self.logger.warning(f"Skipping row with empty name or user_id: {row}")
                        
            self.logger.info(f"Successfully loaded {len(authors)} authors from {csv_file}")
            return authors
            
        except FileNotFoundError:
            self.logger.error(f"CSV file not found: {csv_file}")
            raise
        except Exception as e:
            self.logger.error(f"Error reading CSV file {csv_file}: {e}")
            raise

    def process_authors_batch(self, csv_file: str, output_dir: str = "output") -> Dict[str, bool]:
        """
        Process multiple authors from a CSV file.
        
        Args:
            csv_file: Path to the CSV file containing author information
            output_dir: Directory to save output files (default: ./output)
            
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
        
        results = {}
        total_authors = len(authors)
        
        for i, (name, user_id) in enumerate(authors, 1):
            self.logger.info(f"Processing author {i}/{total_authors}: {name} (ID: {user_id})")
            
            try:
                # Scrape the profile
                scholar_data = self.scrape_profile(user_id)
                
                if scholar_data:
                    # Save to JSON
                    output_file = self.save_to_json(scholar_data, user_id, output_dir)
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
    parser.add_argument("--no-headless", action="store_true", help="Run browser in non-headless mode")
    parser.add_argument("--delay-min", type=float, default=3.0, help="Minimum delay between requests (seconds)")
    parser.add_argument("--delay-max", type=float, default=7.0, help="Maximum delay between requests (seconds)")
    parser.add_argument("--driver", choices=["selenium", "playwright"], default="selenium", help="Browser driver to use (selenium or playwright)")
    
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

    try:
        if args.csv_file:
            # Batch processing mode
            logger.info(f"Starting batch processing from CSV: {args.csv_file}")
            results = scraper.process_authors_batch(args.csv_file, args.output_dir)
            
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
                    output_file = scraper.save_to_json(scholar_data, args.user_id, args.output_dir)
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

