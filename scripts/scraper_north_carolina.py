#!/usr/bin/env python3
"""
North Carolina eCourts Portal Scraper

This scraper accesses the NC Courts Tyler Technology Portal.
URL: https://portal-nc.tylertech.cloud/Portal/

Features:
- Statewide case search
- Party name search
- Hearing/calendar search
- No login required for basic searches

Note: The NC Portal is JavaScript-heavy and requires careful handling.

Usage:
    python scraper_north_carolina.py --name "Smith"
    python scraper_north_carolina.py --case-number "24CV123456"
"""

import asyncio
import argparse
import json
from datetime import datetime
from playwright.async_api import async_playwright
from typing import List, Dict, Any, Optional

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False


class NorthCarolinaScraper:
    """Scraper for North Carolina eCourts Portal."""

    BASE_URL = "https://portal-nc.tylertech.cloud/Portal/"
    SEARCH_URL = "https://portal-nc.tylertech.cloud/Portal/Home/Dashboard/29"

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

    async def _navigate_to_search(self) -> bool:
        """Navigate to the Smart Search page."""
        try:
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            # Look for Smart Search link
            smart_search = await self.page.query_selector(
                'a:has-text("Smart Search"), a[href*="Dashboard/29"]'
            )
            if smart_search:
                await smart_search.click()
                await self.page.wait_for_timeout(3000)
                return True

            # Try direct navigation
            await self.page.goto(self.SEARCH_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)
            return True

        except Exception as e:
            print(f"    Error navigating to search: {e}")
            return False

    async def search(
        self,
        party_name: Optional[str] = None,
        case_number: Optional[str] = None,
        county: Optional[str] = None,
        case_type: Optional[str] = None,
        date_from: Optional[str] = None,  # MM/DD/YYYY
        date_to: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search NC Courts Portal for cases.

        Args:
            party_name: Name to search for (partial match supported)
            case_number: Case number to search
            county: County name to filter
            case_type: Type of case
            date_from: Filing date from
            date_to: Filing date to
            limit: Maximum results to return

        Returns:
            List of case dictionaries
        """
        try:
            print(f"[1] Loading NC Courts Portal...")
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(4000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Check for JavaScript requirement
            content = await self.page.content()
            if 'JavaScript' in content and 'enable' in content.lower():
                print("    Waiting for JavaScript to initialize...")
                await self.page.wait_for_timeout(5000)

            # Navigate to Smart Search
            print("[2] Navigating to Smart Search...")
            smart_search = await self.page.query_selector(
                'a:has-text("Smart Search"), [href*="Dashboard"], a[title*="Search"]'
            )
            if smart_search:
                await smart_search.click()
                await self.page.wait_for_timeout(4000)
            else:
                # Try clicking through the dashboard
                await self.page.click('text=Smart Search', timeout=10000)
                await self.page.wait_for_timeout(3000)

            await self.page.screenshot(path="/tmp/nc_search_page.png")
            print("    Screenshot: /tmp/nc_search_page.png")

            # Look for search input
            print("[3] Looking for search input...")
            search_input = await self.page.query_selector(
                'input[type="text"], input[type="search"], '
                'input[placeholder*="search" i], input[placeholder*="name" i], '
                'input[id*="search" i], input[name*="search" i]'
            )

            if search_input and party_name:
                print(f"    Filling search: {party_name}")
                await search_input.fill(party_name)
                await self.page.wait_for_timeout(500)

                # Try to submit
                search_btn = await self.page.query_selector(
                    'button:has-text("Search"), input[type="submit"], '
                    'button[type="submit"], .search-button, [aria-label*="search" i]'
                )
                if search_btn:
                    await search_btn.click()
                else:
                    await self.page.keyboard.press('Enter')

                print("    Waiting for results...")
                await self.page.wait_for_timeout(5000)

                await self.page.screenshot(path="/tmp/nc_results.png")
                print("    Screenshot: /tmp/nc_results.png")

            # Also try case number search
            if case_number:
                case_input = await self.page.query_selector(
                    'input[placeholder*="case" i], input[name*="case" i], '
                    'input[id*="case" i]'
                )
                if case_input:
                    print(f"    Searching case number: {case_number}")
                    await case_input.fill(case_number)
                    await self.page.keyboard.press('Enter')
                    await self.page.wait_for_timeout(5000)

            # Extract results
            print("[4] Extracting results...")
            results = await self._extract_results()

            print(f"    Found {len(results)} results")

            # Save HTML for debugging
            html = await self.page.content()
            with open("/tmp/nc_results.html", "w") as f:
                f.write(html)

            return results[:limit]

        except Exception as e:
            print(f"    Error during search: {e}")
            import traceback
            traceback.print_exc()
            await self.page.screenshot(path="/tmp/nc_error.png")
            return []

    async def _extract_results(self) -> List[Dict[str, Any]]:
        """Extract case results from the page."""
        results = await self.page.evaluate('''() => {
            const cases = [];

            // Try multiple selectors for results
            const selectors = [
                'table tbody tr',
                '.case-row',
                '.search-result',
                '.result-item',
                '[class*="case"]',
                '[class*="result"]',
                '.card'
            ];

            for (const selector of selectors) {
                const rows = document.querySelectorAll(selector);
                if (rows.length > 0) {
                    rows.forEach(row => {
                        const text = row.innerText?.trim() || '';
                        const cells = row.querySelectorAll('td, .cell, span');
                        const link = row.querySelector('a');

                        // Skip header rows
                        if (row.closest('thead')) return;

                        // Look for case number patterns
                        const caseMatch = text.match(/\\d{2}[A-Z]{2,3}\\d{5,}/i) ||
                                         text.match(/[A-Z]{2,3}-\\d{4}-\\d+/i);

                        if (text.length > 20 || caseMatch) {
                            const caseData = {
                                raw: text.substring(0, 400).replace(/\\s+/g, ' '),
                                url: link?.href || ''
                            };

                            if (cells.length >= 2) {
                                caseData.case_number = cells[0]?.innerText?.trim() || '';
                                caseData.party_name = cells[1]?.innerText?.trim() || '';
                                caseData.case_type = cells[2]?.innerText?.trim() || '';
                                caseData.filed_date = cells[3]?.innerText?.trim() || '';
                                caseData.county = cells[4]?.innerText?.trim() || '';
                            } else if (caseMatch) {
                                caseData.case_number = caseMatch[0];
                            }

                            cases.push(caseData);
                        }
                    });
                    break;
                }
            }

            // Try to find case links if no table results
            if (cases.length === 0) {
                document.querySelectorAll('a[href*="Case"], a[href*="case"]').forEach(link => {
                    const text = link.innerText?.trim();
                    const parent = link.closest('div, li, tr');
                    const context = parent?.innerText?.trim() || text;

                    if (text && text.length > 5) {
                        cases.push({
                            case_number: text,
                            url: link.href,
                            raw: context.substring(0, 300)
                        });
                    }
                });
            }

            return cases;
        }''')

        # Parse results
        parsed = []
        seen = set()

        for r in results:
            case_num = r.get('case_number', '') or ''
            raw = r.get('raw', '')

            # Skip duplicates
            key = case_num or raw[:50]
            if key in seen:
                continue
            seen.add(key)

            case = {
                'case_number': case_num,
                'party_name': r.get('party_name', ''),
                'case_type': r.get('case_type', ''),
                'filed_date': r.get('filed_date', ''),
                'county': r.get('county', ''),
                'url': r.get('url', ''),
                'raw': raw,
                'court': 'North Carolina Courts',
                'state': 'NC',
                'source_url': self.BASE_URL
            }
            parsed.append(case)

        return parsed

    async def search_hearings(
        self,
        party_name: Optional[str] = None,
        county: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search NC Courts Portal for court hearings.

        Args:
            party_name: Name to search for
            county: County to filter
            date_from: Hearing date from (MM/DD/YYYY)
            date_to: Hearing date to
            limit: Maximum results

        Returns:
            List of hearing dictionaries
        """
        try:
            print(f"[1] Loading NC Courts Portal for hearing search...")
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(4000)

            # Navigate to Search Hearings
            print("[2] Navigating to Search Hearings...")
            hearings_link = await self.page.query_selector(
                'a:has-text("Search Hearings"), a:has-text("Hearings")'
            )
            if hearings_link:
                await hearings_link.click()
                await self.page.wait_for_timeout(4000)

            await self.page.screenshot(path="/tmp/nc_hearings_page.png")

            # Fill search form
            if party_name:
                name_input = await self.page.query_selector(
                    'input[placeholder*="name" i], input[name*="name" i]'
                )
                if name_input:
                    await name_input.fill(party_name)

            if county:
                county_select = await self.page.query_selector('select[name*="county" i]')
                if county_select:
                    try:
                        await county_select.select_option(label=county)
                    except:
                        pass

            # Submit and extract
            search_btn = await self.page.query_selector('button:has-text("Search")')
            if search_btn:
                await search_btn.click()
                await self.page.wait_for_timeout(5000)

            results = await self._extract_results()
            return results[:limit]

        except Exception as e:
            print(f"    Error during hearing search: {e}")
            return []


async def main():
    parser = argparse.ArgumentParser(description='North Carolina Courts Portal Search')
    parser.add_argument('--name', '-n', help='Party name to search')
    parser.add_argument('--case-number', '-c', help='Case number to search')
    parser.add_argument('--county', help='County to filter')
    parser.add_argument('--hearings', action='store_true', help='Search hearings instead of cases')
    parser.add_argument('--limit', '-l', type=int, default=25, help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True, help='Headless mode')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    # Default search
    if not args.name and not args.case_number:
        args.name = "Smith"

    print("=" * 70)
    print("NORTH CAROLINA COURTS PORTAL SEARCH")
    print("=" * 70)
    print(f"Search parameters:")
    print(f"  Party name: {args.name or 'N/A'}")
    print(f"  Case number: {args.case_number or 'N/A'}")
    print(f"  County: {args.county or 'All'}")
    print(f"  Search type: {'Hearings' if args.hearings else 'Cases'}")
    print(f"  Limit: {args.limit}")
    print("=" * 70)

    async with NorthCarolinaScraper(headless=args.headless) as scraper:
        if args.hearings:
            results = await scraper.search_hearings(
                party_name=args.name,
                county=args.county,
                limit=args.limit
            )
        else:
            results = await scraper.search(
                party_name=args.name,
                case_number=args.case_number,
                county=args.county,
                limit=args.limit
            )

        if results:
            print(f"\nFound {len(results)} results:")
            for i, r in enumerate(results[:15], 1):
                print(f"\n[{i}]")
                if r.get('case_number'):
                    print(f"    Case: {r['case_number']}")
                if r.get('party_name'):
                    print(f"    Party: {r['party_name'][:50]}")
                if r.get('case_type'):
                    print(f"    Type: {r['case_type']}")
                if r.get('county'):
                    print(f"    County: {r['county']}")
                if r.get('filed_date'):
                    print(f"    Filed: {r['filed_date']}")
                if r.get('raw') and not r.get('case_number'):
                    print(f"    Raw: {r['raw'][:80]}...")

            if args.output:
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                print(f"\nResults saved to {args.output}")
        else:
            print("\nNo results found. Check /tmp/nc_*.png for screenshots.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
