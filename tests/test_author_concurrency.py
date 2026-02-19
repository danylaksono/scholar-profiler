import time
import csv
from pathlib import Path

from scholar_scraper import GoogleScholarScraper


def test_process_authors_batch_with_author_concurrency(tmp_path, monkeypatch):
    # create CSV with 4 authors (one missing id should be skipped)
    csv_path = tmp_path / "authors.csv"
    rows = [
        ("name", "user_id"),
        ("A", "idA"),
        ("B", "idB"),
        ("C", ""),
        ("D", "idD"),
    ]
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    # patch scrape_profile to simulate work (sleep) and return dummy data
    def fake_scrape(self, user_id):
        time.sleep(0.12)
        return [{"title": user_id}]

    monkeypatch.setattr(GoogleScholarScraper, 'scrape_profile', fake_scrape)

    s = GoogleScholarScraper()

    start = time.monotonic()
    results = s.process_authors_batch(str(csv_path), output_dir=str(tmp_path / 'output'), author_concurrency=2)
    elapsed = time.monotonic() - start

    # 3 valid IDs -> with concurrency=2 total time should be about 0.24s (+ overhead)
    assert len(results) == 3
    assert all(uid in results for uid in ("idA", "idB", "idD"))
    assert elapsed < 0.6  # allow some headroom for CI/environment

    # check files were written
    out_dir = Path(tmp_path / 'output')
    assert (out_dir / 'idA_scholar_data.json').exists()
    assert (out_dir / 'idB_scholar_data.json').exists()
    assert (out_dir / 'idD_scholar_data.json').exists()


def test_process_authors_batch_with_name_in_filenames(tmp_path, monkeypatch):
    # single-author batch with name should produce filenames that include the name
    csv_path = tmp_path / "authors.csv"
    rows = [
        ("name", "user_id"),
        ("A", "idA"),
    ]
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    def fake_scrape(self, user_id):
        return [{"title": user_id}]

    monkeypatch.setattr(GoogleScholarScraper, 'scrape_profile', fake_scrape)

    s = GoogleScholarScraper()
    results = s.process_authors_batch(str(csv_path), output_dir=str(tmp_path / 'output'), author_concurrency=1, label='group1')

    out_dir = Path(tmp_path / 'output')
    assert (out_dir / 'idA_group1_scholar_data.json').exists()
    assert results.get('idA') is True
