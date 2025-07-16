
import argparse
import json
import random
import time
import logging
from typing import List, Dict, Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager


class GoogleScholarScraper:
    """A class to scrape Google Scholar profiles and publication details."""
    
    def __init__(self, headless: bool = True, delay_range: tuple = (2, 5)):
        """
        Initialize the Google Scholar scraper.
        
        Args:
            headless: Whether to run browser in headless mode
            delay_range: Range of random delays between requests (min, max) in seconds
        """
        self.headless = headless
        self.delay_range = delay_range
        self.browser = None
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
    
    def _random_delay(self):
        """Add a random delay between requests to avoid rate limiting."""
        delay = random.uniform(*self.delay_range)
        self.logger.debug(f"Waiting {delay:.1f} seconds...")
        time.sleep(delay)
    
    def _get_publication_details(self, url: str) -> Optional[Dict]:
        """Fetch and parse publication details from its dedicated page using Selenium."""
        self.logger.info(f"Fetching publication details from: {url}")
        try:
            # Navigate to the publication page
            if self.browser is None:
                self.logger.error("Browser is not initialized")
                return None
                
            self.browser.get(url)
            self._random_delay()
            
            # Wait for the page to load
            WebDriverWait(self.browser, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            soup = BeautifulSoup(self.browser.page_source, 'lxml')
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
                    # Convert authors to array format
                    details['authors'] = self._parse_authors_to_array(value)
                    self.logger.info(f"Found {len(details['authors'])} authors: {details['authors']}")
                elif name == 'publication date':
                    details['publication_date'] = value
                    self.logger.info(f"Found publication date: {value}")
                elif name == 'description':
                    details['abstract'] = value
                    self.logger.info(f"Found abstract (length: {len(value)} chars)")
                elif name == 'total citations':
                    # Find the link within the 'Total citations' section
                    citation_info = field.find('a')
                    if citation_info:
                        details['total_citations'] = citation_info.text.strip()
                        self.logger.info(f"Found total citations: {details['total_citations']}")
                    else:
                        details['total_citations'] = 'N/A'
                        self.logger.warning("No citation info found")

            # Extract publication venue/source
            details['venue'] = self._extract_publication_venue(soup)
            
            # Handle PDF link extraction more safely
            details['pdf_link'] = self._extract_pdf_link(soup)

            self.logger.info("Publication details extraction completed")
            return details
            
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
    
    def _load_all_publications(self, base_url: str) -> None:
        """Load all publications by clicking the 'Load More' button."""
        self.logger.info(f"Navigating to: {base_url}")
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
    
    def _parse_publication_list(self) -> List[Dict]:
        """Parse the publication list from the loaded page."""
        self.logger.info("Parsing publication list from page...")
        if self.browser is None:
            self.logger.error("Browser is not initialized")
            return []
            
        soup = BeautifulSoup(self.browser.page_source, 'lxml')
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
            self.browser = self._setup_browser()
            base_url = f"https://scholar.google.com/citations?user={user_id}&hl=en"
            
            # Load all publications
            self._load_all_publications(base_url)
            
            # Parse the publication list
            publications = self._parse_publication_list()
            
            if not publications:
                self.logger.warning("No publications found on the profile")
                return []

            # Scrape detailed information for each publication
            self.logger.info("Starting to fetch detailed information for each publication...")
            successful_details = 0
            failed_details = 0
            
            for i, pub in enumerate(publications, 1):
                self.logger.info(f"Processing publication {i}/{len(publications)}: {pub['title'][:50]}...")
                details = self._get_publication_details(pub['citation_url'])
                if details:
                    pub.update(details)
                    successful_details += 1
                    self.logger.info(f"✓ Successfully updated details for publication {i}")
                else:
                    failed_details += 1
                    self.logger.warning(f"✗ Failed to get details for publication {i}")
                
                # Add delay between requests to avoid rate limiting
                if i < len(publications):  # Don't delay after the last request
                    self._random_delay()

            self.logger.info(f"Detail scraping completed: {successful_details} successful, {failed_details} failed")
            self.logger.info(f"Total publications processed: {len(publications)}")
            return publications

        except Exception as e:
            self.logger.error(f"Error during profile scraping: {e}")
            return None
        finally:
            if self.browser:
                self.logger.info("Closing browser...")
                self.browser.quit()
    
    def save_to_json(self, data: List[Dict], user_id: str) -> str:
        """Save scraped data to a JSON file."""
        output_file = f'{user_id}_scholar_data.json'
        self.logger.info(f"Saving {len(data)} publications to {output_file}...")
        
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            self.logger.info(f"✓ Successfully saved data to {output_file}")
            return output_file
        except Exception as e:
            self.logger.error(f"Error saving data to {output_file}: {e}")
            raise

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
    parser = argparse.ArgumentParser(description="Scrape a Google Scholar profile.")
    parser.add_argument("user_id", help="The Google Scholar user ID to scrape.")
    parser.add_argument("--no-headless", action="store_true", help="Run browser in non-headless mode")
    parser.add_argument("--delay-min", type=float, default=3.0, help="Minimum delay between requests (seconds)")
    parser.add_argument("--delay-max", type=float, default=7.0, help="Maximum delay between requests (seconds)")
    
    args = parser.parse_args()

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("GOOGLE SCHOLAR SCRAPER STARTED")
    logger.info("=" * 60)

    # Create scraper instance
    scraper = GoogleScholarScraper(
        headless=not args.no_headless,
        delay_range=(args.delay_min, args.delay_max)
    )

    # Scrape the profile
    scholar_data = scraper.scrape_profile(args.user_id)

    if scholar_data:
        try:
            output_file = scraper.save_to_json(scholar_data, args.user_id)
            logger.info(f"Total publications scraped: {len(scholar_data)}")
        except Exception as e:
            logger.error(f"Failed to save data: {e}")
    else:
        logger.error("✗ Failed to scrape any data")

    logger.info("=" * 60)
    logger.info("GOOGLE SCHOLAR SCRAPER FINISHED")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

