def test_playwright_async_fallback(monkeypatch):
    from scholar_scraper import GoogleScholarScraper

    scraper = GoogleScholarScraper()
    scraper.use_httpx = True
    scraper.driver = 'playwright'

    publications = [
        {"title": "stub1", "citation_url": "http://example.com/p1"},
        {"title": "stub2", "citation_url": "http://example.com/p2"},
    ]

    # Simulate httpx step failing for all publications
    async def fake_fetch_all_details_async(self, pubs):
        return [0, 1]

    async def fake_playwright_fetch(self, pubs, indices):
        # simulate successful playwright fetches
        for i in indices:
            pubs[i]["title"] = "PW_Fetched"
        return (len(indices), 0)

    monkeypatch.setattr(GoogleScholarScraper, "_fetch_all_details_async", fake_fetch_all_details_async)
    monkeypatch.setattr(GoogleScholarScraper, "_fetch_details_with_playwright_async", fake_playwright_fetch)

    successful, failed = scraper._fetch_details_concurrently(publications)

    assert successful == 2
    assert failed == 0
    assert publications[0]["title"] == "PW_Fetched"
    assert publications[1]["title"] == "PW_Fetched"