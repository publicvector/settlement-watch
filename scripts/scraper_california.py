#!/usr/bin/env python3
"""
California Courts Scraper

This scraper provides access to California court records.

California has three court levels:
1. Supreme Court
2. Courts of Appeal (6 districts)
3. Superior Courts (58 counties)

Available systems:
- Appellate Courts Case Information: https://appellatecases.courtinfo.ca.gov/
- Individual County Superior Courts

Note: California does not have a unified statewide trial court search.
Each of the 58 county Superior Courts maintains separate systems.

Major county systems:
- Los Angeles: https://www.lacourt.org/
- San Diego: https://www.sdcourt.ca.gov/
- Orange: https://www.occourts.org/
- San Francisco: https://www.sfsuperiorcourt.org/

Usage:
    python scraper_california.py --appellate --name "Smith"
    python scraper_california.py --county "Los Angeles" --name "Smith"
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


class CaliforniaScraper:
    """Scraper for California Court Records."""

    APPELLATE_URL = "https://appellatecases.courtinfo.ca.gov/"
    MAIN_URL = "https://www.courts.ca.gov/"

    # Major county URLs
    COUNTY_URLS = {
        "Los Angeles": "https://www.lacourt.org/casesummary/ui/index.aspx",
        "San Diego": "https://www.sdcourt.ca.gov/",
        "Orange": "https://www.occourts.org/online-services/case-access",
        "San Francisco": "https://www.sfsuperiorcourt.org/online-services/case-information",
        "Santa Clara": "https://www.scscourt.org/online_services/case_info.shtml",
        "Alameda": "https://www.alameda.courts.ca.gov/",
        "Sacramento": "https://www.saccourt.ca.gov/",
        "Riverside": "https://www.riverside.courts.ca.gov/",
        "San Bernardino": "https://www.sb-court.org/"
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

    async def search_appellate(
        self,
        party_name: Optional[str] = None,
        case_number: Optional[str] = None,
        court: Optional[str] = None,  # "Supreme", "1st", "2nd", etc.
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search California Appellate Courts.

        Args:
            party_name: Party name to search
            case_number: Case number
            court: Court level (Supreme, 1st-6th District)
            limit: Maximum results

        Returns:
            List of case dictionaries
        """
        try:
            print(f"[1] Loading California Appellate Courts...")
            await self.page.goto(self.APPELLATE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(4000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Check if blocked
            content = await self.page.content()
            if 'rejected' in content.lower() or 'blocked' in content.lower():
                print("    WARNING: Access may be blocked")
                await self.page.screenshot(path="/tmp/california_appellate_blocked.png")

            await self.page.screenshot(path="/tmp/california_appellate_main.png")
            print("    Screenshot: /tmp/california_appellate_main.png")

            # Look for search form
            print("[2] Looking for search form...")

            # Analyze page structure
            page_info = await self.page.evaluate('''() => {
                const info = { inputs: [], selects: [], links: [] };
                document.querySelectorAll('input[type="text"]').forEach(i => {
                    if (i.offsetParent !== null) {
                        info.inputs.push({
                            name: i.name || i.id,
                            placeholder: i.placeholder || ''
                        });
                    }
                });
                document.querySelectorAll('select').forEach(s => {
                    if (s.offsetParent !== null) {
                        info.selects.push({
                            name: s.name || s.id,
                            options: Array.from(s.options).slice(0, 5).map(o => o.text)
                        });
                    }
                });
                document.querySelectorAll('a').forEach(a => {
                    const text = (a.textContent || '').toLowerCase();
                    if (text.includes('search') || text.includes('case') || text.includes('party')) {
                        info.links.push({ text: a.textContent?.trim(), href: a.href });
                    }
                });
                return info;
            }''')

            print(f"    Inputs: {[i['name'] for i in page_info['inputs']]}")
            print(f"    Search links: {len(page_info['links'])}")

            # Try to find and click search link
            search_link = await self.page.query_selector(
                'a:has-text("Search"), a:has-text("Case Search"), '
                'a:has-text("Party Search")'
            )
            if search_link:
                await search_link.click()
                await self.page.wait_for_timeout(3000)
                await self.page.screenshot(path="/tmp/california_appellate_search.png")

            # Fill search form
            if party_name:
                print(f"[3] Searching for: {party_name}")
                name_input = await self.page.query_selector(
                    'input[name*="party" i], input[name*="name" i], '
                    'input[id*="party" i], input[id*="name" i]'
                )
                if name_input:
                    await name_input.fill(party_name)

            elif case_number:
                print(f"[3] Searching case: {case_number}")
                case_input = await self.page.query_selector(
                    'input[name*="case" i], input[id*="case" i]'
                )
                if case_input:
                    await case_input.fill(case_number)

            # Select court if specified
            if court:
                court_select = await self.page.query_selector(
                    'select[name*="court" i], select[id*="court" i]'
                )
                if court_select:
                    try:
                        await court_select.select_option(label=court)
                    except:
                        pass

            await self.page.screenshot(path="/tmp/california_appellate_filled.png")

            # Submit search
            print("[4] Submitting search...")
            submit = await self.page.query_selector(
                'input[type="submit"], button:has-text("Search"), '
                'input[value*="Search" i]'
            )
            if submit:
                await submit.click()
            else:
                await self.page.keyboard.press('Enter')

            await self.page.wait_for_timeout(6000)
            await self.page.screenshot(path="/tmp/california_appellate_results.png")
            print("    Screenshot: /tmp/california_appellate_results.png")

            # Save HTML
            html = await self.page.content()
            with open("/tmp/california_appellate_results.html", "w") as f:
                f.write(html)

            # Extract results
            print("[5] Extracting results...")
            results = await self._extract_appellate_results()

            print(f"    Found {len(results)} results")
            return results[:limit]

        except Exception as e:
            print(f"    Error: {e}")
            import traceback
            traceback.print_exc()
            await self.page.screenshot(path="/tmp/california_appellate_error.png")
            return []

    async def _extract_appellate_results(self) -> List[Dict[str, Any]]:
        """Extract results from appellate court search."""
        results = await self.page.evaluate('''() => {
            const cases = [];

            document.querySelectorAll('table tbody tr, .case-row, .result').forEach(row => {
                const cells = row.querySelectorAll('td');
                const rowText = row.innerText?.trim() || '';
                const link = row.querySelector('a');

                // CA appellate case numbers: S123456, A123456, etc.
                const caseMatch = rowText.match(/[A-Z]\\d{6}/);

                if (cells.length >= 2 || caseMatch) {
                    cases.push({
                        case_number: caseMatch ? caseMatch[0] : (cells[0]?.innerText?.trim() || ''),
                        party_name: cells[1]?.innerText?.trim() || '',
                        court: cells[2]?.innerText?.trim() || '',
                        case_type: cells[3]?.innerText?.trim() || '',
                        filed_date: cells[4]?.innerText?.trim() || '',
                        status: cells[5]?.innerText?.trim() || '',
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
                'court': r.get('court', 'California Appellate Courts'),
                'case_type': r.get('case_type', ''),
                'filed_date': r.get('filed_date', ''),
                'status': r.get('status', ''),
                'url': r.get('url', ''),
                'raw': r.get('raw', ''),
                'state': 'CA',
                'source_url': self.APPELLATE_URL
            }
            parsed.append(case)

        return parsed

    async def search_county(
        self,
        county: str,
        party_name: Optional[str] = None,
        case_number: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search a specific California county Superior Court.

        Args:
            county: County name
            party_name: Party name
            case_number: Case number
            limit: Maximum results

        Returns:
            List of case dictionaries
        """
        if county not in self.COUNTY_URLS:
            print(f"County '{county}' not in known list.")
            print(f"Available: {list(self.COUNTY_URLS.keys())}")
            return []

        url = self.COUNTY_URLS[county]

        try:
            print(f"[1] Loading {county} County Superior Court...")
            await self.page.goto(url, timeout=self.timeout)
            await self.page.wait_for_timeout(4000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            await self.page.screenshot(path=f"/tmp/california_{county.lower().replace(' ', '_')}_main.png")

            # Look for case search link
            case_search = await self.page.query_selector(
                'a:has-text("Case Search"), a:has-text("Case Information"), '
                'a:has-text("Case Access"), a:has-text("Public Access")'
            )
            if case_search:
                await case_search.click()
                await self.page.wait_for_timeout(3000)

            await self.page.screenshot(path=f"/tmp/california_{county.lower().replace(' ', '_')}_search.png")

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

            await self.page.screenshot(path=f"/tmp/california_{county.lower().replace(' ', '_')}_results.png")

            # Save HTML
            html = await self.page.content()
            with open(f"/tmp/california_{county.lower().replace(' ', '_')}_results.html", "w") as f:
                f.write(html)

            # Extract results
            results = await self._extract_county_results(county)
            return results[:limit]

        except Exception as e:
            print(f"    Error: {e}")
            await self.page.screenshot(path=f"/tmp/california_{county.lower().replace(' ', '_')}_error.png")
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
                'court': f'{county} County Superior Court',
                'state': 'CA',
                'source_url': self.COUNTY_URLS.get(county, '')
            }
            parsed.append(case)

        return parsed


async def main():
    parser = argparse.ArgumentParser(description='California Courts Search')
    parser.add_argument('--name', '-n', help='Party name to search')
    parser.add_argument('--case-number', '-c', help='Case number')
    parser.add_argument('--appellate', action='store_true',
                        help='Search appellate courts')
    parser.add_argument('--county', help='County name for Superior Court search')
    parser.add_argument('--list-counties', action='store_true',
                        help='List available counties')
    parser.add_argument('--limit', '-l', type=int, default=25, help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True, help='Headless mode')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    print("=" * 70)
    print("CALIFORNIA COURTS SEARCH")
    print("=" * 70)

    async with CaliforniaScraper(headless=args.headless) as scraper:
        # List counties
        if args.list_counties:
            print("Available county Superior Courts:")
            for county in scraper.COUNTY_URLS.keys():
                print(f"  - {county}")
            return

        # Default search
        if not args.name and not args.case_number:
            args.name = "Smith"

        print(f"Search parameters:")
        print(f"  Party name: {args.name or 'N/A'}")
        print(f"  Case number: {args.case_number or 'N/A'}")
        print(f"  Court: {'Appellate' if args.appellate else (args.county or 'Not specified')}")
        print(f"  Limit: {args.limit}")
        print("")
        print("NOTE: California has no unified trial court search.")
        print("      Each county maintains separate systems.")
        print("=" * 70)

        if args.appellate:
            results = await scraper.search_appellate(
                party_name=args.name,
                case_number=args.case_number,
                limit=args.limit
            )
        elif args.county:
            results = await scraper.search_county(
                county=args.county,
                party_name=args.name,
                case_number=args.case_number,
                limit=args.limit
            )
        else:
            print("\nPlease specify --appellate or --county")
            print("Use --list-counties to see available counties")
            return

        if results:
            print(f"\nFound {len(results)} results:")
            for i, r in enumerate(results[:15], 1):
                print(f"\n[{i}]")
                if r.get('case_number'):
                    print(f"    Case: {r['case_number']}")
                if r.get('party_name'):
                    print(f"    Party: {r['party_name'][:50]}")
                if r.get('court'):
                    print(f"    Court: {r['court']}")
                if r.get('case_type'):
                    print(f"    Type: {r['case_type']}")
                if r.get('filed_date'):
                    print(f"    Filed: {r['filed_date']}")

            if args.output:
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                print(f"\nResults saved to {args.output}")
        else:
            print("\nNo results found. Check /tmp/california_*.png for screenshots.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
