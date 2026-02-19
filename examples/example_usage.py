#!/usr/bin/env python3
"""
Example usage of the GoogleScholarScraper class.

This script demonstrates how to use the refactored GoogleScholarScraper
to scrape a Google Scholar profile with different configurations.
"""

from scholar_scraper import GoogleScholarScraper
import logging

def main():
    """Example usage of the GoogleScholarScraper."""
    
    # Example Google Scholar user ID (replace with actual user ID)
    user_id = "LsZIhbcAAAAJ"  # my id for example
    
    print("Google Scholar Scraper Example")
    print("=" * 40)
    
    # Example 1: Basic usage with default settings
    print("\n1. Basic scraping with default settings:")
    scraper1 = GoogleScholarScraper()
    try:
        publications = scraper1.scrape_profile(user_id)
        if publications:
            print(f"✓ Successfully scraped {len(publications)} publications")
            scraper1.save_to_json(publications, user_id)
        else:
            print("✗ No publications found")
    except Exception as e:
        print(f"✗ Error during scraping: {e}")
    
    # Example 2: Non-headless mode with longer delays (for debugging)
    print("\n2. Non-headless mode with longer delays:")
    scraper2 = GoogleScholarScraper(
        headless=False,  # Show browser window
        delay_range=(5, 10)  # Longer delays between requests
    )
    try:
        publications = scraper2.scrape_profile(user_id)
        if publications:
            print(f"✓ Successfully scraped {len(publications)} publications")
            scraper2.save_to_json(publications, f"{user_id}_debug")
        else:
            print("✗ No publications found")
    except Exception as e:
        print(f"✗ Error during scraping: {e}")
    
    # Example 3: Custom configuration for rate limiting
    print("\n3. Conservative settings to avoid rate limiting:")
    scraper3 = GoogleScholarScraper(
        headless=True,
        delay_range=(8, 15)  # Very long delays
    )
    try:
        publications = scraper3.scrape_profile(user_id)
        if publications:
            print(f"✓ Successfully scraped {len(publications)} publications")
            scraper3.save_to_json(publications, f"{user_id}_conservative")
        else:
            print("✗ No publications found")
    except Exception as e:
        print(f"✗ Error during scraping: {e}")

if __name__ == "__main__":
    main() 