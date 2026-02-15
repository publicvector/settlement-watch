#!/usr/bin/env python3
"""
Run state court scrapers and save output to JSON files.
Simplified version for GitHub Actions.
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# Output directory
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def save_results(state: str, cases: list):
    """Save scraper results to JSON file."""
    output_file = OUTPUT_DIR / f"{state.lower()}_cases.json"

    data = {
        "state": state,
        "scraped_at": datetime.now().isoformat(),
        "count": len(cases),
        "cases": cases
    }

    with open(output_file, 'w') as f:
        json.dump(data, f, indent=2, default=str)

    print(f"  Saved {len(cases)} cases to {output_file.name}")
    return len(cases)


async def run_alaska():
    """Run Alaska CourtView scraper."""
    print("\n[AK] Running Alaska scraper...")
    try:
        from scraper_alaska import AlaskaScraper

        async with AlaskaScraper(headless=True) as scraper:
            all_cases = []

            # Search for common names
            for name in ["Smith", "Johnson", "Williams"]:
                try:
                    results = await scraper.search_by_name(
                        last_name=name,
                        case_type="All Cases",
                        case_status="All Statuses",
                        limit=10
                    )
                    all_cases.extend(results)
                except Exception as e:
                    print(f"    Search error for {name}: {e}")
                await asyncio.sleep(1)

            # Deduplicate
            seen = set()
            unique = []
            for case in all_cases:
                key = case.get('case_number', str(case))
                if key not in seen:
                    seen.add(key)
                    unique.append(case)

            return save_results("AK", unique)

    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def run_oklahoma():
    """Run Oklahoma OSCN scraper."""
    print("\n[OK] Running Oklahoma scraper...")
    try:
        from scraper_oklahoma import search_oklahoma_cases

        results = await search_oklahoma_cases(limit=20)
        return save_results("OK", results)

    except ImportError:
        # Try alternative approach
        try:
            from scraper_oklahoma import OklahomaScraper
            scraper = OklahomaScraper()
            await scraper.start()
            results = await scraper.search(limit=20)
            await scraper.close()
            return save_results("OK", results)
        except Exception as e:
            print(f"  Error: {e}")
            return 0
    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def main():
    """Run available scrapers."""
    print("=" * 60)
    print("RUNNING STATE COURT SCRAPERS")
    print(f"Output: {OUTPUT_DIR}")
    print("=" * 60)

    results = {}

    # Only run scrapers we know work
    scrapers = [
        ("Alaska", run_alaska),
        ("Oklahoma", run_oklahoma),
    ]

    for name, func in scrapers:
        try:
            count = await asyncio.wait_for(func(), timeout=120)
            results[name] = count
        except asyncio.TimeoutError:
            print(f"  {name}: Timeout")
            results[name] = 0
        except Exception as e:
            print(f"  {name}: Error - {e}")
            results[name] = 0

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    total = 0
    for name, count in results.items():
        status = "✓" if count > 0 else "✗"
        print(f"  {status} {name}: {count} cases")
        total += count

    print(f"\n  Total: {total} cases")

    # List output files
    print("\nOutput files:")
    for f in sorted(OUTPUT_DIR.glob("*_cases.json")):
        print(f"  {f.name}")

    return total


if __name__ == "__main__":
    count = asyncio.run(main())
    sys.exit(0 if count >= 0 else 1)
