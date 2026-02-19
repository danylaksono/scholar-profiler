import os

from scholar_scraper import GoogleScholarScraper


def test_save_to_json_uses_output_folder(tmp_path, monkeypatch):
    # change cwd to temporary directory so test doesn't write into repo
    monkeypatch.chdir(tmp_path)

    scraper = GoogleScholarScraper()
    data = [{"title": "t1"}]
    user_id = "testuser"

    output_file = scraper.save_to_json(data, user_id)

    # expected path: ./output/testuser_scholar_data.json
    expected_dir = tmp_path / "output"
    expected_file = expected_dir / f"{user_id}_scholar_data.json"

    assert os.path.isdir(expected_dir)
    assert expected_file.exists()
    from pathlib import Path
    assert Path(output_file).resolve() == expected_file.resolve()
