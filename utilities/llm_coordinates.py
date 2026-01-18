"""
LLM-based Coordinate Estimation for Long Island Aerial Photography

This script processes metadata from aerial photography items and uses OpenAI's GPT
model to estimate geographic coordinates based on location descriptions found in
the Coverage and Description fields.

Usage:
    python utilities/llm_coordinates.py [--dry-run] [--verbose] [--limit N]

Requirements:
    - OpenAI API key in .env file (OPENAI_API_KEY=your-key-here)
    - openai and python-dotenv packages installed

Output:
    Creates a coordinates.json file in each item directory containing:
    - latitude: Estimated latitude
    - longitude: Estimated longitude
    - confidence: LLM's confidence level (high/medium/low)
    - reasoning: Brief explanation of the estimate
    - excluded: Boolean indicating if coordinates are outside Long Island bounds
    - source_coverage: The Coverage field used for estimation
    - source_description: The Description field used for estimation
"""

import json
import os
import sys
import argparse
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

from dotenv import load_dotenv
from openai import OpenAI

# Long Island approximate bounding box for validation
# These bounds include all of Long Island, from western Queens/Brooklyn to Montauk
LONG_ISLAND_BOUNDS = {
    "min_lat": 40.5,   # Southern boundary
    "max_lat": 41.2,   # Northern boundary
    "min_lng": -74.1,  # Western boundary (Brooklyn/Queens)
    "max_lng": -71.8,  # Eastern boundary (Montauk)
}

# Default paths
PROJECT_ROOT = Path(__file__).parent.parent
ITEMS_DIR = PROJECT_ROOT / "output" / "items"
ENV_FILE = PROJECT_ROOT / ".env"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class CoordinateEstimate:
    """Represents an estimated coordinate with metadata."""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    confidence: str = "none"
    reasoning: str = ""
    excluded: bool = False
    exclusion_reason: str = ""
    source_coverage: str = ""
    source_description: str = ""
    error: str = ""


def load_environment() -> str:
    """Load OpenAI API key from .env file.

    Returns:
        The OpenAI API key.

    Raises:
        SystemExit: If .env file is missing or OPENAI_API_KEY is not set.
    """
    if not ENV_FILE.exists():
        logger.error(f".env file not found at {ENV_FILE}")
        logger.error("Please create a .env file with: OPENAI_API_KEY=your-key-here")
        sys.exit(1)

    load_dotenv(ENV_FILE)
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        logger.error("OPENAI_API_KEY not found in .env file")
        sys.exit(1)

    return api_key


def is_within_long_island_bounds(lat: float, lng: float) -> bool:
    """Check if coordinates fall within the Long Island bounding box.

    Args:
        lat: Latitude value.
        lng: Longitude value.

    Returns:
        True if coordinates are within Long Island bounds, False otherwise.
    """
    return (
        LONG_ISLAND_BOUNDS["min_lat"] <= lat <= LONG_ISLAND_BOUNDS["max_lat"]
        and LONG_ISLAND_BOUNDS["min_lng"] <= lng <= LONG_ISLAND_BOUNDS["max_lng"]
    )


def estimate_coordinates(
    client: OpenAI,
    coverage: str,
    description: str,
    model: str = "gpt-4o"
) -> CoordinateEstimate:
    """Use OpenAI to estimate coordinates from location descriptions.

    Args:
        client: OpenAI client instance.
        coverage: The Coverage field from metadata.
        description: The Description field from metadata.
        model: The OpenAI model to use.

    Returns:
        CoordinateEstimate with the estimated coordinates and metadata.
    """
    prompt = f"""You are a geographic expert specializing in Long Island, New York.
Given the following location description from a historical aerial photograph, estimate the approximate latitude and longitude coordinates.

Coverage field: {coverage}
Description field: {description}

Important context:
- These are aerial photographs of Long Island, NY from the 1930s-1960s
- Long Island includes Nassau County, Suffolk County, Queens, and Brooklyn
- Common landmarks and areas include: Montauk, the Hamptons, Fire Island, Jones Beach, Great South Bay, Long Island Sound, various towns and villages

Respond with a JSON object containing:
- "latitude": decimal latitude (e.g., 40.7589)
- "longitude": decimal longitude (must be negative for Long Island, e.g., -73.0852)
- "confidence": "high", "medium", or "low" based on how specific the location description is
- "reasoning": brief explanation of how you determined the location (1-2 sentences)

If you cannot determine any reasonable coordinates from the description, respond with:
- "latitude": null
- "longitude": null
- "confidence": "none"
- "reasoning": explanation of why coordinates couldn't be determined

Respond only with the JSON object, no additional text."""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a geographic expert. Respond only with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=300
        )

        response_text = response.choices[0].message.content.strip()

        # Handle potential markdown code blocks in response
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1])

        result = json.loads(response_text)

        estimate = CoordinateEstimate(
            latitude=result.get("latitude"),
            longitude=result.get("longitude"),
            confidence=result.get("confidence", "none"),
            reasoning=result.get("reasoning", ""),
            source_coverage=coverage,
            source_description=description
        )

        # Validate coordinates are within Long Island bounds
        if estimate.latitude is not None and estimate.longitude is not None:
            if not is_within_long_island_bounds(estimate.latitude, estimate.longitude):
                estimate.excluded = True
                estimate.exclusion_reason = (
                    f"Coordinates ({estimate.latitude}, {estimate.longitude}) "
                    "are outside Long Island bounding box"
                )

        return estimate

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM response as JSON: {e}")
        return CoordinateEstimate(
            source_coverage=coverage,
            source_description=description,
            error=f"JSON parse error: {e}"
        )
    except Exception as e:
        logger.error(f"Error calling OpenAI API: {e}")
        return CoordinateEstimate(
            source_coverage=coverage,
            source_description=description,
            error=str(e)
        )


def process_item(
    client: OpenAI,
    item_dir: Path,
    dry_run: bool = False
) -> tuple[bool, str]:
    """Process a single item directory.

    Args:
        client: OpenAI client instance.
        item_dir: Path to the item directory.
        dry_run: If True, don't write output files.

    Returns:
        Tuple of (success, message).
    """
    metadata_path = item_dir / "metadata.json"
    output_path = item_dir / "coordinates.json"

    # Check if already processed
    if output_path.exists():
        return True, "already processed"

    # Load metadata
    if not metadata_path.exists():
        return False, "metadata.json not found"

    try:
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
    except json.JSONDecodeError as e:
        return False, f"invalid metadata.json: {e}"

    # Extract fields
    fields = metadata.get("fields", {})
    coverage = fields.get("Coverage", "")
    description = fields.get("Description", "")

    # Check if we have any location information
    if not coverage and not description:
        estimate = CoordinateEstimate(
            error="No Coverage or Description fields available"
        )
    else:
        estimate = estimate_coordinates(client, coverage, description)

    # Write output
    if not dry_run:
        with open(output_path, "w") as f:
            json.dump(asdict(estimate), f, indent=2)

    # Generate status message
    if estimate.error:
        return False, f"error: {estimate.error}"
    elif estimate.excluded:
        return True, f"excluded: {estimate.exclusion_reason}"
    elif estimate.latitude is None:
        return True, "no coordinates determined"
    else:
        return True, f"({estimate.latitude:.4f}, {estimate.longitude:.4f}) [{estimate.confidence}]"


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Estimate coordinates for aerial photography items using OpenAI"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process items but don't write output files"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    parser.add_argument(
        "--limit", "-n",
        type=int,
        default=None,
        help="Limit processing to N items (useful for testing)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o",
        help="OpenAI model to use (default: gpt-4o)"
    )
    parser.add_argument(
        "--items-dir",
        type=Path,
        default=ITEMS_DIR,
        help=f"Path to items directory (default: {ITEMS_DIR})"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load API key and initialize client
    api_key = load_environment()
    client = OpenAI(api_key=api_key)

    # Find all item directories
    if not args.items_dir.exists():
        logger.error(f"Items directory not found: {args.items_dir}")
        sys.exit(1)

    item_dirs = sorted([
        d for d in args.items_dir.iterdir()
        if d.is_dir() and (d / "metadata.json").exists()
    ])

    if args.limit:
        item_dirs = item_dirs[:args.limit]

    logger.info(f"Found {len(item_dirs)} items to process")

    if args.dry_run:
        logger.info("DRY RUN - no files will be written")

    # Process items
    stats = {"processed": 0, "skipped": 0, "errors": 0, "excluded": 0}

    for item_dir in item_dirs:
        item_id = item_dir.name
        success, message = process_item(client, item_dir, args.dry_run)

        if "already processed" in message:
            stats["skipped"] += 1
            logger.debug(f"[{item_id}] {message}")
        elif success:
            stats["processed"] += 1
            if "excluded" in message:
                stats["excluded"] += 1
            logger.info(f"[{item_id}] {message}")
        else:
            stats["errors"] += 1
            logger.warning(f"[{item_id}] {message}")

    # Print summary
    logger.info(
        f"Complete: {stats['processed']} processed, "
        f"{stats['skipped']} skipped, "
        f"{stats['excluded']} excluded (outside bounds), "
        f"{stats['errors']} errors"
    )


if __name__ == "__main__":
    main()
