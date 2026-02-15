#!/usr/bin/env python3
"""
Run state court scrapers and save output to JSON files.
Includes docket entry extraction for recent filings and opinions.
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


def save_results(state: str, cases: list, docket_entries: list = None):
    """Save scraper results to JSON file."""
    output_file = OUTPUT_DIR / f"{state.lower()}_cases.json"

    # Separate opinions and orders
    opinions = [e for e in (docket_entries or []) if e.get('is_opinion')]
    orders = [e for e in (docket_entries or []) if e.get('is_order')]

    data = {
        "state": state,
        "scraped_at": datetime.now().isoformat(),
        "count": len(cases),
        "cases": cases,
        "docket_entries": docket_entries or [],
        "docket_count": len(docket_entries or []),
        "opinions": opinions,
        "opinion_count": len(opinions),
        "orders": orders,
        "order_count": len(orders)
    }

    with open(output_file, 'w') as f:
        json.dump(data, f, indent=2, default=str)

    print(f"  Saved {len(cases)} cases, {len(docket_entries or [])} docket entries to {output_file.name}")
    if opinions:
        print(f"    - {len(opinions)} opinions/decisions detected")
    if orders:
        print(f"    - {len(orders)} orders detected")

    return len(cases)


async def run_alaska():
    """Run Alaska CourtView scraper with docket entry extraction."""
    print("\n[AK] Running Alaska scraper...")
    try:
        from scraper_alaska import AlaskaScraper

        async with AlaskaScraper(headless=True) as scraper:
            all_cases = []
            all_docket_entries = []

            # Search for common names
            for name in ["Smith", "Johnson", "Williams"]:
                try:
                    results = await scraper.search_by_name(
                        last_name=name,
                        first_name="",
                        case_type="All Cases",
                        case_status="All Statuses",
                        limit=10
                    )
                    all_cases.extend(results)
                except Exception as e:
                    print(f"    Search error for {name}: {e}")
                await asyncio.sleep(1)

            # Deduplicate cases
            seen = set()
            unique = []
            for case in all_cases:
                key = case.get('case_number', str(case))
                if key not in seen:
                    seen.add(key)
                    unique.append(case)

            # Fetch docket entries for a subset of cases
            print(f"  Fetching docket entries for up to 10 cases...")
            for case in unique[:10]:
                try:
                    case_num = case.get('case_number')
                    if case_num:
                        detail = await scraper.get_case_detail(case_num)
                        entries = detail.get('docket_entries', [])
                        for entry in entries:
                            entry['case_number'] = case_num
                            entry['state'] = 'AK'
                            all_docket_entries.append(entry)
                        await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"    Error fetching detail: {e}")

            return save_results("AK", unique, all_docket_entries)

    except Exception as e:
        print(f"  Error: {e}")
        return 0


async def run_oklahoma():
    """Run Oklahoma OSCN scraper with docket entry extraction."""
    print("\n[OK] Running Oklahoma scraper...")
    try:
        from scraper_oklahoma import OklahomaScraper
        from dataclasses import asdict

        scraper = OklahomaScraper()
        all_docket_entries = []

        # Search for recent cases
        cases = scraper.search(last_name="Smith", county="oklahoma", limit=20)
        case_dicts = [asdict(c) for c in cases]

        # Fetch docket entries for a subset
        print(f"  Fetching docket entries for up to 10 cases...")
        for case in cases[:10]:
            try:
                details = scraper.get_case_details(case.case_number, case.county.lower())
                entries = details.get('docket_entries', [])
                for entry in entries:
                    entry['case_number'] = case.case_number
                    entry['case_style'] = case.style
                    entry['state'] = 'OK'
                    entry['county'] = case.county
                    all_docket_entries.append(entry)
            except Exception as e:
                print(f"    Error fetching detail for {case.case_number}: {e}")

        return save_results("OK", case_dicts, all_docket_entries)

    except Exception as e:
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()
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
