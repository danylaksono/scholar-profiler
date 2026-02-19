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