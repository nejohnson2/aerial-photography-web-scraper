#!/usr/bin/env python3
"""
Stony Brook Aerial Photos Scraper

Downloads aerial photographs from the SBU Commons Library collection.
Handles AWS WAF bot protection by prompting for browser cookies.

Usage:
    python scraper.py

The script will:
1. Prompt for an AWS WAF token (from browser cookies)
2. Crawl the collection to find all items
3. Download native, medium, and thumbnail images
4. Skip already-downloaded files
5. Prompt for a new token if the current one expires
"""

import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests
import requests_cache
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

# ============================================================================
# Configuration
# ============================================================================

COLLECTION_ROOT = "https://commons.library.stonybrook.edu/long-island-black-and-white-aerial-photographs-collection/"
OUTDIR = Path("output")
ITEMS_DIR = OUTDIR / "items"
MANIFEST_PATH = OUTDIR / "manifest.jsonl"
TOKEN_FILE = Path("browser_cookies.json")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# ============================================================================
# Session Setup
# ============================================================================

def make_session() -> requests.Session:
    """Create a requests session with retry logic and caching for HTML pages."""
    requests_cache.install_cache("http_cache", expire_after=60 * 60 * 24 * 7)

    s = requests.Session()
    s.headers.update({
        "User-Agent": "SBU-Aerials-MetadataHarvester/0.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
    })

    retry = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


session = make_session()

# ============================================================================
# Token Management
# ============================================================================

def prompt_for_token() -> str:
    """Prompt user for AWS WAF token."""
    print("\n" + "=" * 70)
    print("AWS WAF TOKEN REQUIRED")
    print("=" * 70)
    print("""
To get the token:
1. Open Chrome and go to:
   https://commons.library.stonybrook.edu/long-island-black-and-white-aerial-photographs-collection/490/
2. Click the "Download" link to download any image
3. Open DevTools (F12) → Application tab → Cookies
4. Find 'aws-waf-token' and copy its value
""")
    token = input("Paste the aws-waf-token value here: ").strip()
    if not token:
        print("No token provided. Exiting.")
        sys.exit(1)
    return token


def load_token() -> str | None:
    """Load token from file if it exists."""
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            if isinstance(data, list) and data:
                return data[0].get("value")
            elif isinstance(data, dict):
                return data.get("aws-waf-token")
        except Exception:
            pass
    return None


def save_token(token: str):
    """Save token to file."""
    data = [{
        "name": "aws-waf-token",
        "value": token,
        "domain": "commons.library.stonybrook.edu"
    }]
    TOKEN_FILE.write_text(json.dumps(data, indent=2))


def set_session_token(token: str):
    """Apply token to the session."""
    session.cookies.set("aws-waf-token", token, domain="commons.library.stonybrook.edu")


def get_or_prompt_token() -> str:
    """Get existing token or prompt for a new one."""
    token = load_token()
    if token:
        print(f"Loaded existing token from {TOKEN_FILE}")
        use_existing = input("Use this token? [Y/n]: ").strip().lower()
        if use_existing != "n":
            return token
    return prompt_for_token()


# ============================================================================
# File Validation
# ============================================================================

def is_valid_image(path: Path) -> bool:
    """Check if file is a valid image (not HTML or corrupted)."""
    if not path.exists():
        return False
    if path.stat().st_size < 1000:
        return False

    with open(path, "rb") as f:
        header = f.read(20)

    # Check magic bytes
    if header.startswith(b'\xff\xd8\xff'):  # JPEG
        return True
    if header.startswith(b'\x89PNG'):  # PNG
        return True
    if header.startswith(b'II*\x00') or header.startswith(b'MM\x00*'):  # TIFF
        return True
    return False


def has_valid_native(item_dir: Path) -> bool:
    """Check if directory has a valid native image."""
    for ext in [".jpg", ".tif", ".png", ".jpeg", ".tiff"]:
        path = item_dir / f"image_native{ext}"
        if is_valid_image(path):
            return True
    return False


# ============================================================================
# Utilities
# ============================================================================

def polite_sleep(min_s: float = 1.0, max_s: float = 2.5):
    """Random delay to be polite to the server."""
    time.sleep(random.uniform(min_s, max_s))


def safe_text(el) -> str:
    """Extract clean text from a BeautifulSoup element."""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def item_id_from_url(item_url: str) -> str:
    """Extract item ID from URL."""
    m = re.search(r"/(\d+)/?$", item_url)
    if not m:
        raise ValueError(f"Cannot parse item id from {item_url}")
    return m.group(1)


def guess_ext_from_headers(resp: requests.Response, fallback: str = ".jpg") -> str:
    """Guess file extension from response headers."""
    cd = resp.headers.get("Content-Disposition", "")

    # Try Content-Disposition filename
    m = re.search(r'filename[*]?=(?:UTF-8\'\')?["\']?([^"\';]+)', cd, re.IGNORECASE)
    if m:
        ext = Path(unquote(m.group(1))).suffix.lower()
        if ext:
            return ext

    # Try Content-Type
    ct = resp.headers.get("Content-Type", "").lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "tiff" in ct or "tif" in ct:
        return ".tif"
    if "png" in ct:
        return ".png"

    return fallback


# ============================================================================
# Collection Crawling
# ============================================================================

def fetch_soup(url: str) -> BeautifulSoup:
    """Fetch a page and parse as BeautifulSoup."""
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def crawl_collection_urls() -> list[str]:
    """Crawl collection pages to find all item URLs."""
    print("\nCrawling collection pages...")

    first_url = urljoin(COLLECTION_ROOT, "index.html")
    first = fetch_soup(first_url)

    # Find total pages from "Page 1 of 61"
    txt = first.get_text(" ", strip=True)
    m = re.search(r"Page\s+\d+\s+of\s+(\d+)", txt)
    total_pages = int(m.group(1)) if m else 1

    all_items = set()

    for page_num in tqdm(range(1, total_pages + 1), desc="Scanning pages"):
        if page_num == 1:
            soup = first
        else:
            url = urljoin(COLLECTION_ROOT, f"index.{page_num}.html")
            soup = fetch_soup(url)

        # Find item links
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            abs_url = urljoin(COLLECTION_ROOT, href)
            if re.search(r"/long-island-black-and-white-aerial-photographs-collection/\d+/?$", abs_url):
                if not abs_url.endswith("/"):
                    abs_url += "/"
                all_items.add(abs_url)

        if page_num > 1:
            polite_sleep(0.5, 1.0)

    return sorted(all_items)


def parse_item_page(item_url: str) -> dict:
    """Parse an item page for metadata and download links."""
    r = session.get(item_url, timeout=60)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    title = safe_text(soup.find(["h1", "h2"]))

    # Extract metadata fields
    fields = {}
    for header in soup.find_all(["h2", "h3"]):
        key = safe_text(header)
        if not key or key.lower() in {"preview", "downloads", "share", "browse", "search", "author corner", "gallery locations"}:
            continue

        parts = []
        for sib in header.next_siblings:
            if getattr(sib, "name", None) in {"h2", "h3"}:
                break
            if hasattr(sib, "get_text"):
                t = safe_text(sib)
                if t:
                    parts.append(t)

        if parts:
            fields[key] = " ".join(parts)

    # Find download links
    links = {}
    for a in soup.select("a[href]"):
        label = safe_text(a).lower()
        href = a.get("href", "")
        if not href:
            continue
        abs_url = urljoin(item_url, href)

        if label == "download":
            links["native"] = abs_url
        elif label == "medium":
            links["medium"] = abs_url
        elif label == "thumbnail":
            links["thumbnail"] = abs_url

    return {
        "item_url": item_url,
        "title": title,
        "fields": fields,
        "links": links,
        "html": r.text,
    }


# ============================================================================
# Downloading
# ============================================================================

class TokenExpiredError(Exception):
    """Raised when the AWS WAF token has expired."""
    pass


def download_native(native_url: str, item_url: str, dest_dir: Path) -> Path:
    """
    Download the native (full resolution) image.
    Raises TokenExpiredError if the token has expired.
    """
    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = item_url

    with requests_cache.disabled():
        r = session.get(native_url, headers=headers, timeout=300, allow_redirects=True)

    ct = r.headers.get("Content-Type", "").lower()

    # Check for WAF challenge (token expired)
    if r.status_code in (202, 403) or "text/html" in ct:
        raise TokenExpiredError("Token expired or invalid")

    r.raise_for_status()
    content = r.content

    # Verify it's actually an image
    if content[:15].lower().startswith(b"<!doctype") or content[:10].lower().startswith(b"<html"):
        raise TokenExpiredError("Received HTML instead of image - token likely expired")

    ext = guess_ext_from_headers(r)
    dest = dest_dir / f"image_native{ext}"

    # Remove any old broken files
    for old_ext in [".jpg", ".tif", ".png", ".jpeg", ".tiff"]:
        old_file = dest_dir / f"image_native{old_ext}"
        if old_file.exists() and not is_valid_image(old_file):
            old_file.unlink()

    dest.write_bytes(content)
    return dest


def download_derivative(url: str, dest_path: Path, referer: str):
    """Download a derivative image (medium/thumbnail)."""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        return False

    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = referer

    with requests_cache.disabled():
        r = session.get(url, headers=headers, timeout=120)

    r.raise_for_status()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(r.content)
    return True


# ============================================================================
# Main Processing
# ============================================================================

def process_item(item_url: str) -> dict:
    """Process a single item: download metadata and images."""
    item_id = item_id_from_url(item_url)
    item_dir = ITEMS_DIR / item_id.zfill(6)
    item_dir.mkdir(parents=True, exist_ok=True)

    # Parse item page
    meta = parse_item_page(item_url)

    # Save metadata
    (item_dir / "item.html").write_text(meta["html"], encoding="utf-8")

    metadata = {
        "item_url": meta["item_url"],
        "title": meta["title"],
        "fields": meta["fields"],
        "links": meta["links"],
    }
    (item_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    result = {"item_id": item_id, "item_url": item_url}

    # Download derivatives (these usually work without WAF token)
    for key, filename in [("medium", "image_medium.jpg"), ("thumbnail", "image_thumbnail.jpg")]:
        if key in meta["links"]:
            try:
                download_derivative(meta["links"][key], item_dir / filename, item_url)
                result[key] = "ok"
            except Exception as e:
                result[key] = f"error: {e}"

    # Download native
    if "native" in meta["links"]:
        if has_valid_native(item_dir):
            result["native"] = "skipped (exists)"
        else:
            # This may raise TokenExpiredError
            download_native(meta["links"]["native"], item_url, item_dir)
            result["native"] = "ok"

    return result


def run_scraper():
    """Main scraper loop."""
    OUTDIR.mkdir(parents=True, exist_ok=True)
    ITEMS_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Stony Brook Aerial Photos Scraper")
    print("=" * 70)

    # Get token
    token = get_or_prompt_token()
    save_token(token)
    set_session_token(token)

    # Crawl collection
    item_urls = crawl_collection_urls()
    print(f"\nFound {len(item_urls)} items in collection")

    # Count already downloaded
    already_done = sum(1 for url in item_urls if has_valid_native(ITEMS_DIR / item_id_from_url(url).zfill(6)))
    print(f"Already downloaded: {already_done}")
    print(f"Remaining: {len(item_urls) - already_done}")

    if already_done == len(item_urls):
        print("\nAll items already downloaded!")
        return

    input("\nPress Enter to start downloading (Ctrl+C to cancel)...")

    # Process items
    failed = []
    token_prompts = 0

    with tqdm(item_urls, desc="Downloading") as pbar:
        for item_url in pbar:
            item_id = item_id_from_url(item_url)
            item_dir = ITEMS_DIR / item_id.zfill(6)

            # Skip if already done
            if has_valid_native(item_dir):
                pbar.set_postfix_str(f"{item_id}: skipped")
                continue

            try:
                result = process_item(item_url)
                pbar.set_postfix_str(f"{item_id}: {result.get('native', '?')}")
                polite_sleep()

            except TokenExpiredError:
                token_prompts += 1
                print(f"\n\nToken expired after {token_prompts} prompt(s)")

                token = prompt_for_token()
                save_token(token)
                set_session_token(token)

                # Retry this item
                try:
                    result = process_item(item_url)
                    pbar.set_postfix_str(f"{item_id}: {result.get('native', '?')}")
                except Exception as e:
                    failed.append((item_id, str(e)))
                    pbar.set_postfix_str(f"{item_id}: FAILED")

            except KeyboardInterrupt:
                print("\n\nInterrupted by user. Progress saved.")
                break

            except Exception as e:
                failed.append((item_id, str(e)))
                pbar.set_postfix_str(f"{item_id}: error")

    # Summary
    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)

    final_count = sum(1 for url in item_urls if has_valid_native(ITEMS_DIR / item_id_from_url(url).zfill(6)))
    print(f"Successfully downloaded: {final_count}/{len(item_urls)}")

    if failed:
        print(f"\nFailed items ({len(failed)}):")
        for item_id, error in failed[:10]:
            print(f"  {item_id}: {error}")
        if len(failed) > 10:
            print(f"  ... and {len(failed) - 10} more")


if __name__ == "__main__":
    try:
        run_scraper()
    except KeyboardInterrupt:
        print("\n\nExiting.")
        sys.exit(0)
