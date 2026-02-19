# Google Scholar Scraper

A Python tool to scrape Google Scholar profiles and extract publication details including citations, abstracts, and metadata.

## Features

- Scrape individual Google Scholar profiles
- Batch process multiple authors from CSV files
- Extract detailed publication information including:
  - Title, authors, publication date
  - Abstract/description
  - Citation counts
  - Publication venue/source
  - PDF links (when available)
- Configurable output directory
- Rate limiting with random delays
- Comprehensive logging

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Single Author Mode

Scrape a single Google Scholar profile:

```bash
python scholar_scraper.py --user-id "abc123def456" --output-dir "./data" --name "labA"
```

### Batch Processing Mode

Process multiple authors from a CSV file:

```bash
python scholar_scraper.py --csv-file "authors.csv" --output-dir "./data"
```

### CSV Format

The CSV file should have the following format:

```csv
name,user_id
"John Doe","abc123def456"
"Jane Smith","ghi789jkl012"
"Bob Johnson","mno345pqr678"
```

Where:
- `name`: Author's full name (for logging purposes)
- `user_id`: Google Scholar user ID (found in the profile URL)

### Command Line Options

- `--user-id` — Google Scholar user ID for single-author scraping. Use this to fetch one profile and write a single JSON file.

- `--csv-file` — Path to a CSV file for batch processing. CSV must include `name,user_id` (case-insensitive headers are supported). Each valid row produces one JSON output file.

- `--output-dir` — Directory where JSON output files are written. Default: `./output`.

- `--name` — Optional label to include in each output filename (useful to tag runs or groups). When provided filenames become `USERID_<sanitized-name>_scholar_data.json` (non-alphanumeric characters are replaced with `_`). Example: `--name "Group A/Team"` → `USERID_Group_A_Team_scholar_data.json`.

- `--author-concurrency` — Number of author profiles to process in parallel when using `--csv-file`. Default: `1` (sequential). Increase to speed up batch runs (each worker creates its own scraper instance).

- `--driver` — Browser driver to use for JS-rendered fallbacks: `selenium` (default) or `playwright`.

- `--concurrency` — Per-profile concurrency for fetching publication detail pages (used by the HTTP/Playwright fast path). Default: `8`.

- `--delay-min` / `--delay-max` — Minimum and maximum random delay (in seconds) inserted between requests to reduce rate-limiting. Defaults: `3.0` / `7.0`.

- `--no-headless` — Run the browser in non-headless (visible) mode — useful for debugging.

- `--user-agent-file` — Path to a newline-separated user-agent file; the scraper will rotate UAs from this list.

- `--generate-ua-file PATH` — Write a small default user-agent file to `PATH` and exit (convenience helper).

- `--proxy-file` — Path to a newline-separated proxy list (one `http://host:port` entry per line).

- `--proxy` — Single proxy URL to use for all requests (overrides `--proxy-file`).

- `--no-pause-on-block` — Disable the automatic pause that occurs when the scraper detects persistent Google Scholar blocking (captcha/unusual traffic).

- `--block-retry-limit` — Number of block/captcha detections to tolerate before pausing the batch. Default: `3`.

- `--block-pause-seconds` — Seconds to pause when a persistent block is detected. Default: `300.0` (5 minutes).

- `--help` — Show CLI usage information.

Examples:

- Single author with name tag:

  `python scholar_scraper.py --user-id "abc123def456" --output-dir ./data --name labA`

- Batch with concurrency and a proxy file:

  `python scholar_scraper.py --csv-file authors.csv --output-dir ./data --author-concurrency 4 --proxy-file proxies.txt`


## Output

The scraper generates JSON files with the following structure:

```json
[
  {
    "title": "Publication Title",
    "authors": ["Author 1", "Author 2"],
    "cited_by": "42",
    "year": "2023",
    "venue": "Journal Name",
    "citation_url": "https://scholar.google.com/...",
    "publication_date": "2023",
    "abstract": "Publication abstract...",
    "total_citations": "42",
    "pdf_link": "https://..."
  }
]
```

## Finding Google Scholar User IDs

1. Go to the author's Google Scholar profile
2. The user ID is in the URL: `https://scholar.google.com/citations?user=USER_ID_HERE&hl=en`
3. Copy the `USER_ID_HERE` part

## Notes

- The scraper includes random delays between requests to avoid rate limiting. It now uses jittered exponential backoff and will retry transient HTTP errors (e.g. `429 Too Many Requests`) before giving up.
- If proxies are configured (`--proxy-file` or `--proxy`) the scraper will attempt a direct request first and automatically fall back to a proxy on retries when blocking is detected.
- User-agent rotation is supported (via `--user-agent-file`) and a small built-in UA rotation is used when no UA file is provided to reduce fingerprinting.
- All operations are logged to both console and `scholar_scraper.log` (including 429/redirect events).
- The browser runs in headless mode by default for better performance
- Use `--no-headless` for debugging or to see the scraping process

## Example

```bash
# Process a single author
python scholar_scraper.py --user-id "abc123def456" --output-dir "./scholar_data"

# Process multiple authors from CSV
python scholar_scraper.py --csv-file "authors.csv" --output-dir "./scholar_data" --delay-min 5 --delay-max 10
```