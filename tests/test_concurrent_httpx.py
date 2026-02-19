import asyncio

from scholar_scraper import GoogleScholarScraper


def test_fetch_details_concurrently_httpx(monkeypatch):
    scraper = GoogleScholarScraper()
    scraper.use_httpx = True
    scraper.concurrency = 2

    publications = [
        {"title": "stub1", "citation_url": "http://example.com/p1"},
        {"title": "stub2", "citation_url": "http://example.com/p2"},
    ]

    async def fake_fetch(self, url: str):
        # return minimal valid HTML that the parser expects
        return '<div id="gsc_oci_title">Async Paper</div>'

    monkeypatch.setattr(GoogleScholarScraper, "_fetch_detail_via_httpx_async", fake_fetch)

    successful, failed = scraper._fetch_details_concurrently(publications)

    assert successful == 2
    assert failed == 0
    assert publications[0]["title"] == "Async Paper"
    assert publications[1]["title"] == "Async Paper"


def test_httpx_missing_detail_does_not_overwrite_title(monkeypatch):
    """If the detail page is missing the title element, the existing
    publication title from the list must not be replaced with 'N/A'.
    """
    scraper = GoogleScholarScraper()
    scraper.use_httpx = True
    scraper.concurrency = 1

    publications = [
        {"title": "Original Title", "citation_url": "http://example.com/p1"},
    ]

    async def fake_fetch_missing(self, url: str):
        # return HTML without the expected title element
        return '<html><body><div class="no_title_here">oops</div></body></html>'

    monkeypatch.setattr(GoogleScholarScraper, "_fetch_detail_via_httpx_async", fake_fetch_missing)

    successful, failed = scraper._fetch_details_concurrently(publications)

    # no successful detail updates, fallback will also fail because browser isn't initialized
    assert successful == 0
    assert failed == 1
    # original title must remain unchanged
    assert publications[0]["title"] == "Original Title"


def test_httpx_retries_and_clears_blocked_reason(monkeypatch):
    """_simulate httpx returning a blocking page first, then a valid page on retry
    and ensure the scraper recovers and clears the blocked flag.
    """
    scraper = GoogleScholarScraper()
    scraper.use_httpx = True

    # fake httpx.AsyncClient to return blocked HTML first, then valid HTML
    call = {"n": 0}

    class FakeResp:
        def __init__(self, code, text):
            self.status_code = code
            self.text = text

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def get(self, url, headers=None, proxies=None):
            if call["n"] == 0:
                call["n"] += 1
                return FakeResp(200, '<html><body>Our systems have detected unusual traffic</body></html>')
            return FakeResp(200, '<div id="gsc_oci_title">Recovered Paper</div>')

    import httpx
    monkeypatch.setattr(httpx, 'AsyncClient', FakeAsyncClient)

    html = asyncio.run(scraper._fetch_detail_via_httpx_async('http://example.com/p1'))

    assert html is not None
    assert 'Recovered Paper' in html
    # ensure blocked_reason was cleared after successful retry
    assert scraper._blocked_reason is None


def test_httpx_blocking_detection(monkeypatch):
    scraper = GoogleScholarScraper()
    scraper.use_httpx = True
    scraper.concurrency = 2

    publications = [
        {"title": "stub1", "citation_url": "http://example.com/p1"},
        {"title": "stub2", "citation_url": "http://example.com/p2"},
    ]

    async def fake_fetch(self, url: str):
        return "<html><body><p>Our systems have detected unusual traffic</p></body></html>"

    monkeypatch.setattr(GoogleScholarScraper, "_fetch_detail_via_httpx_async", fake_fetch)

    successful, failed = scraper._fetch_details_concurrently(publications)

    assert successful == 0
    assert failed == 2
    assert scraper._blocked_reason is not None
    assert 'unusual' in scraper._blocked_reason


async def _fake_sequence_client_factory(seq):
    """Helper to create a fake AsyncClient class that returns items from `seq` on each get()."""
    class FakeResp:
        def __init__(self, code, text, url=None):
            self.status_code = code
            self.text = text
            self.url = url or ''

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            self._seq = list(seq)
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def get(self, url, headers=None, proxies=None):
            # pop next response if available, otherwise return last
            if self._seq:
                code, text = self._seq.pop(0)
            else:
                code, text = (200, '<div id="gsc_oci_title">Recovered Paper</div>')
            return FakeResp(code, text)
    return FakeAsyncClient


def test_httpx_handles_429_and_redirect_then_recovers(monkeypatch):
    """Simulate 302 -> 429 -> 200 sequence and ensure the fetch recovers."""
    scraper = GoogleScholarScraper()
    scraper.use_httpx = True

    seq = [
        (302, ''),
        (429, ''),
        (200, '<div id="gsc_oci_title">Recovered Paper</div>'),
    ]

    import httpx

    class FakeResp:
        def __init__(self, code, text):
            self.status_code = code
            self.text = text

    # share a single iterator across client instances because production code
    # creates a new AsyncClient on each retry attempt
    responses = iter(list(seq))

    def _next_response():
        try:
            return next(responses)
        except StopIteration:
            return (200, '<div id="gsc_oci_title">Recovered Paper</div>')

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, headers=None, proxies=None):
                code, text = _next_response()
                return FakeResp(code, text)
    import httpx
    monkeypatch.setattr(httpx, 'AsyncClient', FakeAsyncClient)

    html = asyncio.run(scraper._fetch_detail_via_httpx_async('http://example.com/p1'))

    assert html is not None
    assert 'Recovered Paper' in html
    assert scraper._blocked_reason is None


def test_httpx_falls_back_to_proxy_on_block(monkeypatch):
    """When a direct request is blocked, subsequent proxy attempt should succeed."""
    scraper = GoogleScholarScraper()
    scraper.use_httpx = True
    # configure a proxy so the fetch code will attempt direct first, then proxy
    scraper.proxies = ['http://127.0.0.1:8888']

    class FakeResp:
        def __init__(self, code, text):
            self.status_code = code
            self.text = text

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, headers=None, proxies=None):
            # simulate direct attempts failing (proxies is None), proxy attempt succeeds
            if not proxies:
                return FakeResp(429, '')
            return FakeResp(200, '<div id="gsc_oci_title">Proxy Paper</div>')

    import httpx
    monkeypatch.setattr(httpx, 'AsyncClient', FakeAsyncClient)

    html = asyncio.run(scraper._fetch_detail_via_httpx_async('http://example.com/p1'))
    assert html is not None
    assert 'Proxy Paper' in html
    assert scraper._blocked_reason is None