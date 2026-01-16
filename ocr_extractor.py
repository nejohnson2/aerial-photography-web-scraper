#!/usr/bin/env python3
"""
OCR Text Extractor for Aerial Photos

Scans all image_native files and extracts any visible text using EasyOCR.
Saves results to ocr_text.json in each image's directory.

Usage:
    pip install easyocr
    python ocr_extractor.py

First run will download the OCR model (~100MB).
"""

import json
import sys
from pathlib import Path

try:
    import easyocr
except ImportError:
    print("EasyOCR not installed. Install with:")
    print("  pip install easyocr")
    sys.exit(1)

from tqdm import tqdm

# ============================================================================
# Configuration
# ============================================================================

ITEMS_DIR = Path("output/items")
OCR_OUTPUT_FILE = "ocr_text.json"
CONFIDENCE_THRESHOLD = 0.3  # Minimum confidence to include text


# ============================================================================
# Image Validation
# ============================================================================

def find_native_image(item_dir: Path) -> Path | None:
    """Find the native image file in an item directory."""
    for ext in [".jpg", ".jpeg", ".tif", ".tiff", ".png"]:
        path = item_dir / f"image_native{ext}"
        if path.exists() and path.stat().st_size > 1000:
            # Verify it's a real image (not HTML)
            with open(path, "rb") as f:
                header = f.read(20)
            if header.startswith(b'\xff\xd8\xff'):  # JPEG
                return path
            if header.startswith(b'\x89PNG'):  # PNG
                return path
            if header.startswith(b'II*\x00') or header.startswith(b'MM\x00*'):  # TIFF
                return path
    return None


# ============================================================================
# OCR Processing
# ============================================================================

def extract_text(reader: easyocr.Reader, image_path: Path) -> list[dict]:
    """
    Extract text from an image using EasyOCR.

    Returns list of detections:
    [
        {
            "text": "detected text",
            "confidence": 0.95,
            "bbox": [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
        },
        ...
    ]
    """
    try:
        results = reader.readtext(str(image_path))
    except Exception as e:
        print(f"  Error reading {image_path.name}: {e}")
        return []

    detections = []
    for bbox, text, confidence in results:
        if confidence >= CONFIDENCE_THRESHOLD and text.strip():
            detections.append({
                "text": text.strip(),
                "confidence": round(confidence, 4),
                "bbox": [[int(p[0]), int(p[1])] for p in bbox]
            })

    return detections


def process_item(reader: easyocr.Reader, item_dir: Path) -> dict | None:
    """
    Process a single item directory.
    Returns OCR result dict or None if no text found.
    """
    image_path = find_native_image(item_dir)
    if not image_path:
        return None

    detections = extract_text(reader, image_path)

    if not detections:
        return None

    # Combine all text
    all_text = " ".join(d["text"] for d in detections)

    result = {
        "image_file": image_path.name,
        "text_found": True,
        "full_text": all_text,
        "detection_count": len(detections),
        "detections": detections
    }

    return result


# ============================================================================
# Main
# ============================================================================

def run_ocr():
    """Main OCR extraction loop."""
    print("=" * 70)
    print("OCR Text Extractor for Aerial Photos")
    print("=" * 70)

    if not ITEMS_DIR.exists():
        print(f"Error: {ITEMS_DIR} not found. Run scraper.py first.")
        sys.exit(1)

    # Find all item directories
    item_dirs = sorted([d for d in ITEMS_DIR.iterdir() if d.is_dir()])
    print(f"Found {len(item_dirs)} item directories")

    # Filter to those with native images but no OCR output
    to_process = []
    already_done = 0
    no_image = 0

    for item_dir in item_dirs:
        ocr_file = item_dir / OCR_OUTPUT_FILE
        if ocr_file.exists():
            already_done += 1
            continue

        if find_native_image(item_dir):
            to_process.append(item_dir)
        else:
            no_image += 1

    print(f"Already processed: {already_done}")
    print(f"No native image: {no_image}")
    print(f"To process: {len(to_process)}")

    if not to_process:
        print("\nNothing to process!")
        return

    # Initialize EasyOCR
    print("\nInitializing EasyOCR (first run downloads model ~100MB)...")
    reader = easyocr.Reader(['en'], gpu=False)  # CPU mode for compatibility
    print("EasyOCR ready.\n")

    # Process images
    text_found_count = 0
    no_text_count = 0
    error_count = 0

    with tqdm(to_process, desc="Processing") as pbar:
        for item_dir in pbar:
            item_id = item_dir.name
            pbar.set_postfix_str(item_id)

            try:
                result = process_item(reader, item_dir)

                ocr_file = item_dir / OCR_OUTPUT_FILE

                if result:
                    # Text found - save full result
                    ocr_file.write_text(
                        json.dumps(result, indent=2, ensure_ascii=False),
                        encoding="utf-8"
                    )
                    text_found_count += 1
                    pbar.set_postfix_str(f"{item_id}: {result['detection_count']} texts")
                else:
                    # No text found - save empty marker
                    empty_result = {
                        "image_file": find_native_image(item_dir).name if find_native_image(item_dir) else None,
                        "text_found": False,
                        "full_text": "",
                        "detection_count": 0,
                        "detections": []
                    }
                    ocr_file.write_text(
                        json.dumps(empty_result, indent=2, ensure_ascii=False),
                        encoding="utf-8"
                    )
                    no_text_count += 1
                    pbar.set_postfix_str(f"{item_id}: no text")

            except KeyboardInterrupt:
                print("\n\nInterrupted. Progress saved.")
                break
            except Exception as e:
                error_count += 1
                pbar.set_postfix_str(f"{item_id}: ERROR")
                print(f"\nError processing {item_id}: {e}")

    # Summary
    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"Text found: {text_found_count}")
    print(f"No text: {no_text_count}")
    print(f"Errors: {error_count}")


if __name__ == "__main__":
    try:
        run_ocr()
    except KeyboardInterrupt:
        print("\n\nExiting.")
        sys.exit(0)
