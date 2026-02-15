#!/usr/bin/env python3
"""
Run all state court scrapers and save output to JSON files.
Used by GitHub Action for daily updates.
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
    return output_file


async def run_alaska():
    """Run Alaska CourtView scraper."""
    print("\n[AK] Running Alaska scraper...")
    try:
        from scraper_alaska import AlaskaScraper

        async with AlaskaScraper(headless=True) as scraper:
            # Search for recent cases - use common names
            all_cases = []

            for name in ["Smith", "Johnson", "Williams", "Brown", "Jones"]:
                results = await scraper.search_by_name(
                    last_name=name,
                    case_type="All Cases",
                    case_status="All Statuses",
                    limit=20
                )
                all_cases.extend(results)
                await asyncio.sleep(1)

            # Deduplicate by case number
            seen = set()
            unique = []
            for case in all_cases:
                if case.get('case_number') and case['case_number'] not in seen:
                    seen.add(case['case_number'])
                    unique.append(case)

            save_results("AK", unique)
            return len(unique)

    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def run_oklahoma():
    """Run Oklahoma OSCN scraper."""
    print("\n[OK] Running Oklahoma scraper...")
    try:
        from scraper_oklahoma import OklahomaScraper

        async with OklahomaScraper(headless=True) as scraper:
            results = await scraper.search_recent_cases(limit=50)
            save_results("OK", results)
            return len(results)

    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def run_ohio():
    """Run Ohio Franklin County scraper."""
    print("\n[OH] Running Ohio Franklin scraper...")
    try:
        from scraper_ohio_franklin import OhioFranklinScraper

        async with OhioFranklinScraper(headless=True) as scraper:
            results = await scraper.search_recent_cases(limit=50)
            save_results("OH", results)
            return len(results)

    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def run_delaware():
    """Run Delaware scraper."""
    print("\n[DE] Running Delaware scraper...")
    try:
        from scraper_delaware import DelawareScraper

        async with DelawareScraper(headless=True) as scraper:
            results = await scraper.search_recent_cases(limit=50)
            save_results("DE", results)
            return len(results)

    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def run_pennsylvania():
    """Run Pennsylvania scraper."""
    print("\n[PA] Running Pennsylvania scraper...")
    try:
        from scraper_pennsylvania import PennsylvaniaScraper

        async with PennsylvaniaScraper(headless=True) as scraper:
            results = await scraper.search_recent_cases(limit=50)
            save_results("PA", results)
            return len(results)

    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def run_montana():
    """Run Montana scraper."""
    print("\n[MT] Running Montana scraper...")
    try:
        from scraper_montana import MontanaScraper

        async with MontanaScraper(headless=True) as scraper:
            results = await scraper.search_recent_cases(limit=50)
            save_results("MT", results)
            return len(results)

    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def run_north_dakota():
    """Run North Dakota scraper."""
    print("\n[ND] Running North Dakota scraper...")
    try:
        from scraper_north_dakota import NorthDakotaScraper

        async with NorthDakotaScraper(headless=True) as scraper:
            results = await scraper.search_recent_cases(limit=50)
            save_results("ND", results)
            return len(results)

    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def run_colorado():
    """Run Colorado scraper."""
    print("\n[CO] Running Colorado scraper...")
    try:
        from scraper_colorado import ColoradoScraper

        async with ColoradoScraper(headless=True) as scraper:
            results = await scraper.search_recent_cases(limit=50)
            save_results("CO", results)
            return len(results)

    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def run_nevada():
    """Run Nevada Appellate scraper."""
    print("\n[NV] Running Nevada Appellate scraper...")
    try:
        from nevada_appellate_scraper import NevadaAppellateScraper

        async with NevadaAppellateScraper(headless=True) as scraper:
            results = await scraper.search_recent_cases(limit=50)
            save_results("NV", results)
            return len(results)

    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def main():
    """Run all scrapers."""
    print("=" * 70)
    print("RUNNING ALL STATE COURT SCRAPERS")
    print(f"Output directory: {OUTPUT_DIR}")
    print("=" * 70)

    start_time = datetime.now()

    # Run scrapers (with timeouts)
    scrapers = [
        ("Alaska", run_alaska),
        ("Oklahoma", run_oklahoma),
        ("Ohio", run_ohio),
        ("Delaware", run_delaware),
        ("Pennsylvania", run_pennsylvania),
        ("Montana", run_montana),
        ("North Dakota", run_north_dakota),
        ("Colorado", run_colorado),
        ("Nevada", run_nevada),
    ]

    results = {}
    for name, scraper_func in scrapers:
        try:
            count = await asyncio.wait_for(scraper_func(), timeout=180)
            results[name] = count
        except asyncio.TimeoutError:
            print(f"  {name}: Timeout")
            results[name] = 0
        except Exception as e:
            print(f"  {name}: Error - {e}")
            results[name] = 0

    # Summary
    elapsed = (datetime.now() - start_time).total_seconds()

    print("\n" + "=" * 70)
    print("SCRAPER SUMMARY")
    print("=" * 70)

    total = 0
    for name, count in results.items():
        status = "✓" if count > 0 else "✗"
        print(f"  {status} {name}: {count} cases")
        total += count

    print(f"\n  Total: {total} cases")
    print(f"  Time: {elapsed:.1f} seconds")
    print("=" * 70)

    # List output files
    print("\nOutput files:")
    for f in OUTPUT_DIR.glob("*_cases.json"):
        size = f.stat().st_size
        print(f"  {f.name} ({size} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
