#!/usr/bin/env python3
"""
Georgia Courts Scraper

This scraper provides access to Georgia court records through various portals.

Available systems:
1. GSCCCA (Georgia Superior Court Clerks' Cooperative Authority)
   - Real estate records
   - UCC/Lien records
   - Court records (limited counties)
   URL: https://www.gsccca.org/search

2. Fulton County (Atlanta) - Odyssey Portal
3. Cobb County
4. DeKalb County

Note: Georgia does not have a unified statewide court search.
Individual county systems must be accessed separately.

Usage:
    python scraper_georgia.py --name "Smith" --county "Fulton"
    python scraper_georgia.py --search-type "real_estate" --name "Smith"
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


class GeorgiaScraper:
    """Scraper for Georgia Court Records."""

    GSCCCA_URL = "https://www.gsccca.org/search"
    GSCCCA_SEARCH_BASE = "https://search.gsccca.org/"

    # County-specific URLs
    COUNTY_URLS = {
        "Fulton": "https://odysseyportal.fultoncountyga.gov/Portal/",
        "Cobb": "https://www.cobbcounty.org/courts/",
        "DeKalb": "https://www.dekalbcountyga.gov/clerk-superior-court/",
        "Gwinnett": "https://www.gwinnettcourts.com/"
    }

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

    async def get_gsccca_search_types(self) -> List[Dict[str, str]]:
        """Get available search types from GSCCCA."""
        try:
            await self.page.goto(self.GSCCCA_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            types = await self.page.evaluate('''() => {
                const types = [];
                document.querySelectorAll('a').forEach(a => {
                    const text = (a.textContent || '').trim();
                    const href = a.href || '';
                    if (href.includes('search.gsccca.org') && text.length > 5) {
                        types.push({ name: text.substring(0, 60), url: href });
                    }
                });
                return types;
            }''')

            return types
        except:
            return []

    async def search_gsccca(
        self,
        party_name: Optional[str] = None,
        search_type: str = "real_estate",  # "real_estate", "ucc", "court"
        county: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search GSCCCA records.

        Args:
            party_name: Name to search
            search_type: Type of records ("real_estate", "ucc", "court")
            county: County name (optional)
            limit: Maximum results

        Returns:
            List of record dictionaries

        Note: GSCCCA primarily provides real estate and UCC records.
        Court case records are limited.
        """
        try:
            print(f"[1] Loading GSCCCA search page...")
            await self.page.goto(self.GSCCCA_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            await self.page.screenshot(path="/tmp/georgia_gsccca_main.png")
            print("    Screenshot: /tmp/georgia_gsccca_main.png")

            # Find and click appropriate search link
            print(f"[2] Looking for {search_type} search...")

            search_links = await self.page.evaluate('''() => {
                const links = [];
                document.querySelectorAll('a').forEach(a => {
                    const text = (a.textContent || '').toLowerCase();
                    const href = a.href || '';
                    if (href.includes('search.gsccca.org')) {
                        links.push({
                            text: a.textContent?.trim() || '',
                            href: href,
                            isRealEstate: text.includes('real estate') || text.includes('deed'),
                            isUCC: text.includes('ucc') || text.includes('lien'),
                            isCourt: text.includes('court') || text.includes('criminal')
                        });
                    }
                });
                return links;
            }''')

            print(f"    Found {len(search_links)} search links")

            # Select appropriate link
            target_link = None
            for link in search_links:
                if search_type == "real_estate" and link['isRealEstate']:
                    target_link = link['href']
                    break
                elif search_type == "ucc" and link['isUCC']:
                    target_link = link['href']
                    break
                elif search_type == "court" and link['isCourt']:
                    target_link = link['href']
                    break

            if not target_link and search_links:
                # Default to first search with "name" in URL
                for link in search_links:
                    if 'name' in link['href'].lower():
                        target_link = link['href']
                        break
                if not target_link:
                    target_link = search_links[0]['href']

            if target_link:
                print(f"[3] Navigating to: {target_link[:60]}...")
                await self.page.goto(target_link, timeout=self.timeout)
                await self.page.wait_for_timeout(3000)

                await self.page.screenshot(path="/tmp/georgia_gsccca_search.png")
                print("    Screenshot: /tmp/georgia_gsccca_search.png")

            # Fill search form
            if party_name:
                print(f"[4] Searching for: {party_name}")

                name_input = await self.page.query_selector(
                    'input[name*="name" i], input[id*="name" i], '
                    'input[name*="party" i], input[id*="party" i]'
                )
                if name_input:
                    await name_input.fill(party_name)

                # Set county if available
                if county:
                    county_select = await self.page.query_selector(
                        'select[name*="county" i], select[id*="county" i]'
                    )
                    if county_select:
                        try:
                            await county_select.select_option(label=county)
                        except:
                            pass

                await self.page.screenshot(path="/tmp/georgia_gsccca_filled.png")

                # Submit
                print("[5] Submitting search...")
                submit = await self.page.query_selector(
                    'input[type="submit"], button:has-text("Search"), '
                    'input[value*="Search" i]'
                )
                if submit:
                    await submit.click()
                else:
                    await self.page.keyboard.press('Enter')

                await self.page.wait_for_timeout(6000)
                await self.page.screenshot(path="/tmp/georgia_gsccca_results.png")
                print("    Screenshot: /tmp/georgia_gsccca_results.png")

            # Save HTML
            html = await self.page.content()
            with open("/tmp/georgia_gsccca_results.html", "w") as f:
                f.write(html)

            # Extract results
            print("[6] Extracting results...")
            results = await self._extract_gsccca_results()

            print(f"    Found {len(results)} results")
            return results[:limit]

        except Exception as e:
            print(f"    Error: {e}")
            import traceback
            traceback.print_exc()
            await self.page.screenshot(path="/tmp/georgia_gsccca_error.png")
            return []

    async def _extract_gsccca_results(self) -> List[Dict[str, Any]]:
        """Extract results from GSCCCA search."""
        results = await self.page.evaluate('''() => {
            const records = [];

            document.querySelectorAll('table tbody tr, .search-result').forEach(row => {
                const cells = row.querySelectorAll('td');
                const rowText = row.innerText?.trim() || '';
                const link = row.querySelector('a');

                if (cells.length >= 2) {
                    records.push({
                        name: cells[0]?.innerText?.trim() || '',
                        document_type: cells[1]?.innerText?.trim() || '',
                        county: cells[2]?.innerText?.trim() || '',
                        date: cells[3]?.innerText?.trim() || '',
                        book_page: cells[4]?.innerText?.trim() || '',
                        url: link?.href || '',
                        raw: rowText.substring(0, 300)
                    });
                }
            });

            return records;
        }''')

        parsed = []
        for r in results:
            record = {
                'name': r.get('name', ''),
                'document_type': r.get('document_type', ''),
                'county': r.get('county', ''),
                'date': r.get('date', ''),
                'book_page': r.get('book_page', ''),
                'url': r.get('url', ''),
                'raw': r.get('raw', ''),
                'state': 'GA',
                'source': 'GSCCCA',
                'source_url': self.GSCCCA_URL
            }
            parsed.append(record)

        return parsed

    async def search_county(
        self,
        county: str,
        party_name: Optional[str] = None,
        case_number: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search a specific Georgia county court system.

        Args:
            county: County name (Fulton, Cobb, DeKalb, Gwinnett)
            party_name: Party name to search
            case_number: Case number
            limit: Maximum results

        Returns:
            List of case dictionaries
        """
        if county not in self.COUNTY_URLS:
            print(f"County '{county}' not supported. Available: {list(self.COUNTY_URLS.keys())}")
            return []

        url = self.COUNTY_URLS[county]

        try:
            print(f"[1] Loading {county} County court system...")
            await self.page.goto(url, timeout=self.timeout)
            await self.page.wait_for_timeout(4000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            await self.page.screenshot(path=f"/tmp/georgia_{county.lower()}_main.png")
            print(f"    Screenshot: /tmp/georgia_{county.lower()}_main.png")

            # Look for case search links
            search_link = await self.page.query_selector(
                'a:has-text("Case Search"), a:has-text("Search Cases"), '
                'a:has-text("Public Access"), a[href*="search" i]'
            )

            if search_link:
                await search_link.click()
                await self.page.wait_for_timeout(3000)
                await self.page.screenshot(path=f"/tmp/georgia_{county.lower()}_search.png")

            # Fill search
            if party_name:
                print(f"[2] Searching for: {party_name}")
                name_input = await self.page.query_selector(
                    'input[name*="name" i], input[id*="name" i], '
                    'input[placeholder*="name" i]'
                )
                if name_input:
                    await name_input.fill(party_name)

            # Submit
            submit = await self.page.query_selector(
                'button:has-text("Search"), input[type="submit"]'
            )
            if submit:
                await submit.click()
                await self.page.wait_for_timeout(5000)

            await self.page.screenshot(path=f"/tmp/georgia_{county.lower()}_results.png")

            # Extract results
            results = await self._extract_county_results(county)
            return results[:limit]

        except Exception as e:
            print(f"    Error: {e}")
            await self.page.screenshot(path=f"/tmp/georgia_{county.lower()}_error.png")
            return []

    async def _extract_county_results(self, county: str) -> List[Dict[str, Any]]:
        """Extract results from county court search."""
        results = await self.page.evaluate('''() => {
            const cases = [];

            document.querySelectorAll('table tbody tr, .case-row, .result').forEach(row => {
                const cells = row.querySelectorAll('td');
                const rowText = row.innerText?.trim() || '';
                const link = row.querySelector('a');

                if (cells.length >= 2 || rowText.length > 20) {
                    cases.push({
                        case_number: cells[0]?.innerText?.trim() || '',
                        party_name: cells[1]?.innerText?.trim() || '',
                        case_type: cells[2]?.innerText?.trim() || '',
                        filed_date: cells[3]?.innerText?.trim() || '',
                        status: cells[4]?.innerText?.trim() || '',
                        url: link?.href || '',
                        raw: rowText.substring(0, 300)
                    });
                }
            });

            return cases;
        }''')

        parsed = []
        for r in results:
            case = {
                'case_number': r.get('case_number', ''),
                'party_name': r.get('party_name', ''),
                'case_type': r.get('case_type', ''),
                'filed_date': r.get('filed_date', ''),
                'status': r.get('status', ''),
                'url': r.get('url', ''),
                'raw': r.get('raw', ''),
                'county': county,
                'state': 'GA',
                'source_url': self.COUNTY_URLS.get(county, '')
            }
            parsed.append(case)

        return parsed


async def main():
    parser = argparse.ArgumentParser(description='Georgia Courts Search')
    parser.add_argument('--name', '-n', help='Party name to search')
    parser.add_argument('--county', help='County name (Fulton, Cobb, DeKalb, Gwinnett)')
    parser.add_argument('--search-type', choices=['real_estate', 'ucc', 'court'],
                        default='real_estate', help='GSCCCA search type')
    parser.add_argument('--list-types', action='store_true',
                        help='List available GSCCCA search types')
    parser.add_argument('--limit', '-l', type=int, default=25, help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True, help='Headless mode')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    print("=" * 70)
    print("GEORGIA COURTS SEARCH")
    print("=" * 70)

    async with GeorgiaScraper(headless=args.headless) as scraper:
        # List search types
        if args.list_types:
            print("Fetching GSCCCA search types...")
            types = await scraper.get_gsccca_search_types()
            if types:
                print(f"\nFound {len(types)} search types:")
                for t in types[:20]:
                    print(f"  - {t['name']}")
            return

        # Default search
        if not args.name:
            args.name = "Smith"

        print(f"Search parameters:")
        print(f"  Party name: {args.name}")
        print(f"  County: {args.county or 'Statewide (GSCCCA)'}")
        print(f"  Search type: {args.search_type}")
        print(f"  Limit: {args.limit}")
        print("")
        print("NOTE: Georgia does not have unified statewide court search.")
        print("      GSCCCA provides real estate/UCC records statewide.")
        print("      Court cases require county-specific searches.")
        print("=" * 70)

        if args.county:
            results = await scraper.search_county(
                county=args.county,
                party_name=args.name,
                limit=args.limit
            )
        else:
            results = await scraper.search_gsccca(
                party_name=args.name,
                search_type=args.search_type,
                limit=args.limit
            )

        if results:
            print(f"\nFound {len(results)} results:")
            for i, r in enumerate(results[:15], 1):
                print(f"\n[{i}]")
                if r.get('case_number'):
                    print(f"    Case: {r['case_number']}")
                if r.get('name'):
                    print(f"    Name: {r['name'][:50]}")
                if r.get('party_name'):
                    print(f"    Party: {r['party_name'][:50]}")
                if r.get('document_type'):
                    print(f"    Type: {r['document_type']}")
                if r.get('county'):
                    print(f"    County: {r['county']}")
                if r.get('date') or r.get('filed_date'):
                    print(f"    Date: {r.get('date') or r.get('filed_date')}")

            if args.output:
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                print(f"\nResults saved to {args.output}")
        else:
            print("\nNo results found. Check /tmp/georgia_*.png for screenshots.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
