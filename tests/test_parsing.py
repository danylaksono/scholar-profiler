import pytest

from scholar_scraper import GoogleScholarScraper


def test_parse_authors_semicolon():
    s = GoogleScholarScraper()
    assert s._parse_authors_to_array("Alice; Bob; Charlie") == ["Alice", "Bob", "Charlie"]


def test_parse_authors_and():
    s = GoogleScholarScraper()
    assert s._parse_authors_to_array("Alice and Bob") == ["Alice", "Bob"]


def test_extract_pdf_link_from_html():
    s = GoogleScholarScraper()
    html = '<div id="gsc_oci_title_gg"><a href="http://example.com/paper.pdf">PDF</a></div>'
    soup = s._make_soup(html)
    assert s._extract_pdf_link(soup) == "http://example.com/paper.pdf"
