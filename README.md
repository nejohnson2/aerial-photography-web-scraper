# Long Island Aerial Photography Web Scraper

Downloads aerial photographs from the [Stony Brook University Long Island Black and White Aerial Photographs Collection](https://commons.library.stonybrook.edu/long-island-black-and-white-aerial-photographs-collection/).

## Setup

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

### 1. Download Images

```bash
python scraper.py
```

The scraper will:
1. Prompt for an AWS WAF token (required for downloads)
2. Crawl the collection to find all items (~722 photos)
3. Download native (full resolution), medium, and thumbnail images
4. Skip already-downloaded files
5. Prompt for a new token if the current one expires

**Getting the WAF token:**
1. Open Chrome and visit any item page, e.g.: https://commons.library.stonybrook.edu/long-island-black-and-white-aerial-photographs-collection/490/
2. Click the "Download" link to download the image
3. Open DevTools (F12) → Application → Cookies → `commons.library.stonybrook.edu`
4. Copy the `aws-waf-token` value and paste it when prompted

### 2. Extract Text (OCR)

```bash
python ocr_extractor.py
```

Scans all downloaded native images for visible text (photo IDs, dates, etc.) using EasyOCR. Results are saved to `ocr_text.json` in each item's directory.

## Output Structure

```
output/
└── items/
    ├── 000490/
    │   ├── image_native.jpg      # Full resolution image
    │   ├── image_medium.jpg      # Medium resolution
    │   ├── image_thumbnail.jpg   # Thumbnail
    │   ├── metadata.json         # Item metadata (title, date, location, etc.)
    │   ├── item.html             # Original HTML page
    │   └── ocr_text.json         # Extracted text (after running OCR)
    ├── 000491/
    │   └── ...
    └── ...
```

## Files

- `scraper.py` - Main download script
- `ocr_extractor.py` - OCR text extraction script
- `scraper.ipynb` - Original Jupyter notebook (for development)
- `browser_cookies.json` - Saved WAF token (auto-generated, not in git)
- `http_cache.sqlite` - HTTP response cache (auto-generated, not in git)
