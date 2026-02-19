from scholar_scraper import GoogleScholarScraper
import tempfile


def test_load_authors_with_google_scholarid_header(tmp_path):
    csv_path = tmp_path / "authors_alt.csv"
    csv_content = (
        "ID,Nama,GoogleScholarID,Status\n"
        "1,Alpha,abc123,Aktif\n"
        "2,Beta,,Purnakarya\n"
        "3,Gamma,def456,Aktif\n"
    )
    csv_path.write_text(csv_content, encoding='utf-8')

    scraper = GoogleScholarScraper()
    authors = scraper.load_authors_from_csv(str(csv_path))

    assert len(authors) == 2
    assert authors[0] == ("Alpha", "abc123")
    assert authors[1] == ("Gamma", "def456")
