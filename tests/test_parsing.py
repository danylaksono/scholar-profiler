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


def test_parse_publication_list_with_div_rows():
    """Some Scholar profiles render publication rows as <div class="gsc_a_tr">.
    Ensure the parser finds rows regardless of the tag name.
    """
    s = GoogleScholarScraper()

    html = '''
    <div id="gsc_a_t">
      <div class="gsc_a_tr">
        <div class="gsc_a_t">
          <a class="gsc_a_at" href="/citations?view_op=view_citation&citation_for_view=1">Title One</a>
          <div class="gs_gray">Author One, Author Two</div>
          <a class="gsc_a_ac">5</a>
          <span class="gsc_a_h">2020</span>
        </div>
      </div>
    </div>
    '''

    pubs = s._parse_publication_list(html)
    assert len(pubs) == 1
    assert pubs[0]["title"] == "Title One"
    assert pubs[0]["authors"] == ["Author One", "Author Two"]
    assert pubs[0]["cited_by"] == "5"
    assert pubs[0]["year"] == "2020"


def test_playwright_block_then_httpx_profile_fallback(monkeypatch):
    """When the driver returns a blocked/captcha page, the scraper should
    attempt an httpx fallback and parse the profile HTML from that response.
    """
    s = GoogleScholarScraper(driver='playwright')

    class FakePlaywright:
        def get(self, url):
            return None
        def page_content(self):
            # simulate a Google 'unusual traffic' page
            return "<html><body><p>Our systems have detected unusual traffic</p></body></html>"
        def query_selector(self, sel):
            return None
        def locator_is_enabled(self, sel):
            return False
        def click(self, sel):
            pass
        def wait_for_selector(self, sel, timeout=10000):
            return None

    s.playwright = FakePlaywright()

    async def fake_httpx_fetch(self, url: str):
        # return a minimal valid profile HTML with one publication row
        return ('<div id="gsc_a_t">'
                '<tr class="gsc_a_tr">'
                '<td class="gsc_a_t">'
                '<a class="gsc_a_at" href="/citations?view_op=view_citation&citation_for_view=1">FW Title</a>'
                '<div class="gs_gray">F. Author</div>'
                '<a class="gsc_a_ac">2</a>'
                '<span class="gsc_a_h">2021</span>'
                '</td></tr></div>')

    monkeypatch.setattr(GoogleScholarScraper, "_fetch_detail_via_httpx_async", fake_httpx_fetch)

    # should use httpx fallback and cache the profile HTML for parsing
    s._load_all_publications("https://scholar.google.com/citations?user=FAKE&hl=en")
    pubs = s._parse_publication_list()

    assert len(pubs) == 1
    assert pubs[0]["title"] == "FW Title"
    assert pubs[0]["authors"] == ["F. Author"]
    assert pubs[0]["cited_by"] == "2"


def test_detect_unusual_traffic_in_profile_html():
    s = GoogleScholarScraper()
    html = "<html><body><h1>We're sorry...</h1><p>Our systems have detected unusual traffic from your network.</p></body></html>"

    pubs = s._parse_publication_list(html)
    assert pubs == []
    assert s._blocked_reason is not None
    assert 'unusual traffic' in s._blocked_reason


def test_process_authors_batch_pauses_on_persistent_block(tmp_path, monkeypatch):
    csv_file = tmp_path / "authors.csv"
    csv_file.write_text("name,user_id\nTest One,ID1\nTest Two,ID2\n")

    s = GoogleScholarScraper()
    s.block_retry_limit = 1
    s.blocked_pause_seconds = 0.01

    # Monkeypatch scrape_profile to simulate persistent block detection
    def fake_scrape_profile(user_id: str):
        # simulate that a block was recorded during scraping
        s._record_block('captcha challenge')
        return []

    monkeypatch.setattr(s, 'scrape_profile', fake_scrape_profile)

    sleep_calls = []

    def fake_sleep(sec):
        sleep_calls.append(sec)

    monkeypatch.setattr('time.sleep', fake_sleep)

    results = s.process_authors_batch(str(csv_file), output_dir=str(tmp_path / "out"), author_concurrency=1)

    # batch should have paused once because block_count reached limit
    assert any(abs(sec - s.blocked_pause_seconds) < 1e-6 for sec in sleep_calls)
    # block state should be cleared after the pause
    assert s._block_count == 0
    assert results  # function should still return a results dict
