#!/usr/bin/env python3
"""
Michigan Courts (MiCourt) Scraper

This scraper accesses the Michigan Courts case search system.
URL: https://micourt.courts.michigan.gov/

Features:
- Statewide case search
- Multiple court levels (Supreme, Appeals, Circuit, District)
- Party name and case number search

Note: The MiCourt system is JavaScript-heavy and may require
special handling for navigation.

Usage:
    python scraper_michigan.py --name "Smith"
    python scraper_michigan.py --case-number "24-123456"
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


class MichiganCourtsScraper:
    """Scraper for Michigan Courts (MiCourt)."""

    BASE_URL = "https://micourt.courts.michigan.gov/"
    MAIN_SITE = "https://courts.michigan.gov/"

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

    async def _find_case_search_link(self) -> Optional[str]:
        """Find the case search link from the main courts page."""
        try:
            await self.page.goto(self.MAIN_SITE, timeout=self.timeout)
            await self.page.wait_for_timeout(4000)

            # Look for case search links
            links = await self.page.evaluate('''() => {
                const links = [];
                document.querySelectorAll('a').forEach(a => {
                    const text = (a.textContent || '').toLowerCase();
                    const href = a.href || '';
                    if (text.includes('case') && (text.includes('search') || text.includes('lookup') || text.includes('information'))) {
                        links.push({ text: a.textContent?.trim(), href: href });
                    }
                });
                return links;
            }''')

            if links:
                return links[0]['href']
            return None
        except:
            return None

    async def search(
        self,
        party_name: Optional[str] = None,
        case_number: Optional[str] = None,
        court_type: Optional[str] = None,  # "Supreme", "Appeals", "Circuit", "District"
        county: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search Michigan court records.

        Args:
            party_name: Party name to search
            case_number: Case number to search
            court_type: Type of court
            county: County name
            limit: Maximum results

        Returns:
            List of case dictionaries
        """
        try:
            print(f"[1] Loading Michigan Courts (MiCourt)...")
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(5000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Check for JavaScript requirement
            content = await self.page.content()
            if 'JavaScript' in content and 'enable' in content.lower():
                print("    Waiting for JavaScript to initialize...")
                await self.page.wait_for_timeout(5000)

            await self.page.screenshot(path="/tmp/michigan_main.png")
            print("    Screenshot: /tmp/michigan_main.png")

            # Look for search interface
            print("[2] Looking for search interface...")

            # Check for case search link
            case_search = await self.page.query_selector(
                'a:has-text("Case Search"), a:has-text("Search Cases"), '
                'a[href*="search" i], button:has-text("Search")'
            )

            if case_search:
                await case_search.click()
                await self.page.wait_for_timeout(4000)
                await self.page.screenshot(path="/tmp/michigan_search.png")
                print("    Screenshot: /tmp/michigan_search.png")

            # Analyze available inputs
            form_info = await self.page.evaluate('''() => {
                const info = { inputs: [], selects: [], buttons: [] };
                document.querySelectorAll('input[type="text"], input[type="search"]').forEach(i => {
                    if (i.offsetParent !== null) {  // visible
                        info.inputs.push({
                            name: i.name || i.id,
                            placeholder: i.placeholder || '',
                            label: document.querySelector('label[for="' + i.id + '"]')?.textContent || ''
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
                document.querySelectorAll('button, input[type="submit"]').forEach(b => {
                    info.buttons.push(b.textContent?.trim() || b.value);
                });
                return info;
            }''')

            print(f"    Inputs found: {[i['name'] or i['placeholder'] for i in form_info['inputs']]}")
            print(f"    Selects: {[s['name'] for s in form_info['selects']]}")

            # Try to fill search form
            if party_name:
                print(f"[3] Searching for: {party_name}")

                # Try various name input selectors
                name_input = await self.page.query_selector(
                    'input[name*="name" i], input[id*="name" i], '
                    'input[placeholder*="name" i], input[aria-label*="name" i]'
                )
                if name_input:
                    await name_input.fill(party_name)

            elif case_number:
                print(f"[3] Searching case number: {case_number}")
                case_input = await self.page.query_selector(
                    'input[name*="case" i], input[id*="case" i], '
                    'input[placeholder*="case" i]'
                )
                if case_input:
                    await case_input.fill(case_number)

            # Set court type if available
            if court_type:
                court_select = await self.page.query_selector(
                    'select[name*="court" i], select[id*="court" i]'
                )
                if court_select:
                    try:
                        await court_select.select_option(label=court_type)
                    except:
                        pass

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

            await self.page.screenshot(path="/tmp/michigan_filled.png")

            # Submit search
            print("[4] Submitting search...")
            submit = await self.page.query_selector(
                'button:has-text("Search"), input[type="submit"], '
                'button[type="submit"]'
            )
            if submit:
                await submit.click()
            else:
                await self.page.keyboard.press('Enter')

            await self.page.wait_for_timeout(6000)
            await self.page.screenshot(path="/tmp/michigan_results.png")
            print("    Screenshot: /tmp/michigan_results.png")

            # Save HTML
            html = await self.page.content()
            with open("/tmp/michigan_results.html", "w") as f:
                f.write(html)

            # Check for results
            content = await self.page.evaluate('() => document.body.innerText')

            if 'no results' in content.lower() or 'no records' in content.lower():
                print("    No results found")
                return []

            # Extract results
            print("[5] Extracting results...")
            results = await self._extract_results()

            print(f"    Found {len(results)} results")
            return results[:limit]

        except Exception as e:
            print(f"    Error during search: {e}")
            import traceback
            traceback.print_exc()
            await self.page.screenshot(path="/tmp/michigan_error.png")
            return []

    async def _extract_results(self) -> List[Dict[str, Any]]:
        """Extract case results from the page."""
        results = await self.page.evaluate('''() => {
            const cases = [];

            // Try multiple selectors
            const selectors = [
                'table tbody tr',
                '.case-row',
                '.search-result',
                '[class*="result"]',
                '.card'
            ];

            for (const selector of selectors) {
                const rows = document.querySelectorAll(selector);
                if (rows.length > 0) {
                    rows.forEach(row => {
                        const cells = row.querySelectorAll('td, .cell');
                        const rowText = row.innerText?.trim() || '';
                        const link = row.querySelector('a');

                        // Skip headers
                        if (row.closest('thead')) return;

                        // Case number patterns
                        const caseMatch = rowText.match(/\\d{2}-\\d{4,}/) ||
                                         rowText.match(/[A-Z]{2,}\\d{2}-\\d+/);

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
                    break;
                }
            }

            return cases;
        }''')

        # Parse results
        parsed = []
        for r in results:
            case = {
                'case_number': r.get('case_number', ''),
                'party_name': r.get('party_name', ''),
                'court': r.get('court', 'Michigan Courts'),
                'case_type': r.get('case_type', ''),
                'filed_date': r.get('filed_date', ''),
                'status': r.get('status', ''),
                'url': r.get('url', ''),
                'raw': r.get('raw', ''),
                'state': 'MI',
                'source_url': self.BASE_URL
            }
            parsed.append(case)

        return parsed


async def main():
    parser = argparse.ArgumentParser(description='Michigan Courts Search')
    parser.add_argument('--name', '-n', help='Party name to search')
    parser.add_argument('--case-number', '-c', help='Case number')
    parser.add_argument('--court-type', choices=['Supreme', 'Appeals', 'Circuit', 'District'],
                        help='Court type filter')
    parser.add_argument('--county', help='County name')
    parser.add_argument('--limit', '-l', type=int, default=25, help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True, help='Headless mode')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    # Default search
    if not args.name and not args.case_number:
        args.name = "Smith"

    print("=" * 70)
    print("MICHIGAN COURTS (MICOURT) SEARCH")
    print("=" * 70)
    print(f"Search parameters:")
    print(f"  Party name: {args.name or 'N/A'}")
    print(f"  Case number: {args.case_number or 'N/A'}")
    print(f"  Court type: {args.court_type or 'All'}")
    print(f"  County: {args.county or 'All'}")
    print(f"  Limit: {args.limit}")
    print("=" * 70)

    async with MichiganCourtsScraper(headless=args.headless) as scraper:
        results = await scraper.search(
            party_name=args.name,
            case_number=args.case_number,
            court_type=args.court_type,
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
            print("\nNo results found. Check /tmp/michigan_*.png for screenshots.")
            print("\nNote: Michigan's MiCourt system is JavaScript-heavy.")
            print("Some searches may require manual browser access.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
