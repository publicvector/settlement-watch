#!/usr/bin/env python3
"""
Colorado Judicial Branch Docket Search Scraper

This scraper accesses the Colorado Judicial Branch docket search system.
URL: https://www.coloradojudicial.gov/dockets

Features:
- No CAPTCHA required
- No login required
- AJAX-based Drupal form
- Supports party name, case number, county, and date range searches

Usage:
    python scraper_colorado.py --name "Smith" --county "Denver"
    python scraper_colorado.py --case-number "2024CV123"
"""

import asyncio
import argparse
import json
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
from typing import List, Dict, Any, Optional

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False


class ColoradoDocketScraper:
    """Scraper for Colorado Judicial Branch Docket Search."""

    BASE_URL = "https://www.coloradojudicial.gov/dockets"

    def __init__(self, headless: bool = True, timeout: int = 60000):
        self.headless = headless
        self.timeout = timeout
        self.browser = None
        self.context = None
        self.page = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        """Initialize browser and page."""
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='en-US',
        )
        self.page = await self.context.new_page()
        self.page.set_default_timeout(self.timeout)

        if HAS_STEALTH:
            stealth = Stealth()
            await stealth.apply_stealth_async(self.page)

    async def close(self):
        """Close browser."""
        if self.browser:
            await self.browser.close()
        if hasattr(self, '_playwright'):
            await self._playwright.stop()

    async def search(
        self,
        party_name: Optional[str] = None,
        case_number: Optional[str] = None,
        county: Optional[str] = None,
        court_type: Optional[str] = None,  # "county", "district", or "both"
        date_range: Optional[str] = None,  # e.g., "6 Months", "1 Year"
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search Colorado docket records.

        Args:
            party_name: Name of party to search for
            case_number: Case number to search for
            county: County name to filter by
            court_type: Type of court (county, district, both)
            date_range: Date range filter
            limit: Maximum number of results to return

        Returns:
            List of case dictionaries
        """
        try:
            print(f"[1] Loading Colorado Docket Search...")
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Check for CAPTCHA (should not be present)
            content = await self.page.content()
            if 'captcha' in content.lower() or 'recaptcha' in content.lower():
                print("    ERROR: CAPTCHA detected!")
                return []

            # Fill in search criteria
            if county:
                print(f"[2] Selecting county: {county}...")
                county_select = await self.page.query_selector(
                    'select[name*="county" i], select[id*="county" i]'
                )
                if county_select:
                    # Try to find matching option
                    await county_select.select_option(label=county)
                    await self.page.wait_for_timeout(1000)

            if court_type:
                print(f"[3] Selecting court type: {court_type}...")
                court_select = await self.page.query_selector(
                    'select[name*="court" i], select[id*="court" i]'
                )
                if court_select:
                    await court_select.select_option(label=court_type.capitalize())
                    await self.page.wait_for_timeout(500)

            if party_name:
                print(f"[4] Filling party name: {party_name}...")
                # Look for party name input fields
                party_input = await self.page.query_selector(
                    'input[name*="party" i], input[id*="party" i], '
                    'input[placeholder*="name" i], input[name*="name" i]'
                )
                if party_input:
                    await party_input.fill(party_name)
                else:
                    # Try alternative - may be labeled differently
                    all_inputs = await self.page.query_selector_all('input[type="text"]')
                    for inp in all_inputs:
                        placeholder = await inp.get_attribute('placeholder') or ''
                        if 'name' in placeholder.lower() or 'party' in placeholder.lower():
                            await inp.fill(party_name)
                            break

            if case_number:
                print(f"[5] Filling case number: {case_number}...")
                case_input = await self.page.query_selector(
                    'input[name*="case" i], input[id*="case" i], '
                    'input[placeholder*="case" i]'
                )
                if case_input:
                    await case_input.fill(case_number)

            await self.page.wait_for_timeout(500)

            # Submit search
            print("[6] Submitting search...")
            search_btn = await self.page.query_selector(
                'button:has-text("Search"), input[type="submit"][value*="Search" i], '
                'button[type="submit"]:has-text("Search"), button.btn-primary'
            )
            if search_btn:
                await search_btn.click()
            else:
                await self.page.keyboard.press('Enter')

            # Wait for results to load
            print("    Waiting for results...")
            await self.page.wait_for_timeout(5000)

            # Try to wait for results table/container
            try:
                await self.page.wait_for_selector(
                    'table, .results, .docket-results, [class*="result"]',
                    timeout=15000
                )
            except Exception:
                pass

            # Extract results
            print("[7] Extracting results...")
            results = await self._extract_results()

            print(f"    Found {len(results)} results")
            return results[:limit]

        except Exception as e:
            print(f"    Error during search: {e}")
            import traceback
            traceback.print_exc()
            await self.page.screenshot(path="/tmp/colorado_error.png")
            return []

    async def _extract_results(self) -> List[Dict[str, Any]]:
        """Extract case results from the page."""
        results = await self.page.evaluate('''() => {
            const cases = [];

            // Try multiple selectors for result rows
            const selectors = [
                'table tbody tr',
                '.docket-row',
                '.result-row',
                '[class*="docket-item"]',
                '.views-row'
            ];

            for (const selector of selectors) {
                const rows = document.querySelectorAll(selector);
                if (rows.length > 0) {
                    rows.forEach(row => {
                        const cells = row.querySelectorAll('td');
                        const rowText = row.innerText?.trim() || '';

                        if (cells.length >= 2) {
                            // Table format
                            cases.push({
                                case_number: cells[0]?.innerText?.trim() || '',
                                party_name: cells[1]?.innerText?.trim() || '',
                                court: cells[2]?.innerText?.trim() || '',
                                case_type: cells[3]?.innerText?.trim() || '',
                                filed_date: cells[4]?.innerText?.trim() || '',
                                hearing_date: cells[5]?.innerText?.trim() || '',
                                raw: rowText.substring(0, 300)
                            });
                        } else if (rowText.length > 20) {
                            // Non-table format
                            cases.push({
                                raw: rowText.substring(0, 300)
                            });
                        }
                    });
                    break;
                }
            }

            // Also try to find case links
            if (cases.length === 0) {
                document.querySelectorAll('a[href*="case"], a[href*="docket"]').forEach(link => {
                    const text = link.innerText?.trim();
                    const parent = link.closest('div, li, tr');
                    const context = parent?.innerText?.trim() || text;
                    if (text && text.length > 5) {
                        cases.push({
                            case_number: text,
                            link: link.href,
                            raw: context.substring(0, 300)
                        });
                    }
                });
            }

            return cases;
        }''')

        # Deduplicate results
        seen = set()
        unique = []
        for r in results:
            key = r.get('case_number', '') or r.get('raw', '')[:50]
            if key and key not in seen:
                seen.add(key)
                unique.append(r)

        return unique

    async def get_case_details(self, case_number: str) -> Dict[str, Any]:
        """Get detailed information for a specific case."""
        results = await self.search(case_number=case_number, limit=1)
        if results:
            return results[0]
        return {}


async def main():
    parser = argparse.ArgumentParser(description='Colorado Docket Search Scraper')
    parser.add_argument('--name', '-n', help='Party name to search')
    parser.add_argument('--case-number', '-c', help='Case number to search')
    parser.add_argument('--county', help='County to filter by')
    parser.add_argument('--court-type', choices=['county', 'district', 'both'],
                        help='Type of court')
    parser.add_argument('--limit', '-l', type=int, default=25,
                        help='Maximum results to return')
    parser.add_argument('--headless', action='store_true', default=True,
                        help='Run in headless mode')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    # Default search if no args
    if not args.name and not args.case_number:
        args.name = "Smith"

    print("=" * 70)
    print("COLORADO DOCKET SEARCH SCRAPER")
    print("=" * 70)
    print(f"Search parameters:")
    print(f"  Party name: {args.name or 'N/A'}")
    print(f"  Case number: {args.case_number or 'N/A'}")
    print(f"  County: {args.county or 'All'}")
    print(f"  Court type: {args.court_type or 'All'}")
    print(f"  Limit: {args.limit}")
    print("=" * 70)

    async with ColoradoDocketScraper(headless=args.headless) as scraper:
        results = await scraper.search(
            party_name=args.name,
            case_number=args.case_number,
            county=args.county,
            court_type=args.court_type,
            limit=args.limit
        )

        if results:
            print(f"\nFound {len(results)} results:")
            for i, r in enumerate(results[:15], 1):
                print(f"\n[{i}]")
                for k, v in r.items():
                    if v and k != 'raw':
                        print(f"    {k}: {str(v)[:60]}")

            if args.output:
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                print(f"\nResults saved to {args.output}")
        else:
            print("\nNo results found. Check /tmp/colorado_*.png for screenshots.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
