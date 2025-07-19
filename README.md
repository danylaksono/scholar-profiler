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
python scholar_scraper.py --user-id "abc123def456" --output-dir "./data"
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

- `--user-id`: Google Scholar user ID (single author mode)
- `--csv-file`: Path to CSV file with multiple authors (batch mode)
- `--output-dir`: Output directory for JSON files (default: current directory)
- `--no-headless`: Run browser in non-headless mode (for debugging)
- `--delay-min`: Minimum delay between requests in seconds (default: 3.0)
- `--delay-max`: Maximum delay between requests in seconds (default: 7.0)

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

- The scraper includes random delays between requests to avoid rate limiting
- All operations are logged to both console and `scholar_scraper.log`
- The browser runs in headless mode by default for better performance
- Use `--no-headless` for debugging or to see the scraping process

## Example

```bash
# Process a single author
python scholar_scraper.py --user-id "abc123def456" --output-dir "./scholar_data"

# Process multiple authors from CSV
python scholar_scraper.py --csv-file "authors.csv" --output-dir "./scholar_data" --delay-min 5 --delay-max 10
```