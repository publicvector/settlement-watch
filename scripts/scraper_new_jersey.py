#!/usr/bin/env python3
"""
New Jersey Courts Scraper

This scraper provides access to various NJ Courts case search systems.
Main URL: https://www.njcourts.gov/

Available search portals:
- Civil and Foreclosure Public Access
- Criminal Case Search
- Tax Court Cases
- Judgment Liens

Note: The NJ eCourts portal (portal.njcourts.gov) is protected
by Incapsula WAF and may block automated access.

Usage:
    python scraper_new_jersey.py --name "Smith"
    python scraper_new_jersey.py --case-number "ESX-L-001234-24"
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


class NewJerseyScraper:
    """Scraper for New Jersey Courts."""

    BASE_URL = "https://www.njcourts.gov/"
    CIVIL_URL = "https://portal.njcourts.gov/webe5/CivilCourtCaseJacketWeb/"

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

    async def _check_waf_block(self) -> bool:
        """Check if blocked by WAF (Incapsula)."""
        content = await self.page.content()
        return 'incapsula' in content.lower() or 'incident' in content.lower()

    async def get_available_portals(self) -> List[Dict[str, str]]:
        """Get list of available case search portals."""
        try:
            await self.page.goto(self.BASE_URL + "public/find-a-case", timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            portals = await self.page.evaluate('''() => {
                const portals = [];
                document.querySelectorAll('a').forEach(a => {
                    const text = (a.textContent || '').toLowerCase();
                    if (text.includes('public access') || text.includes('case search') ||
                        text.includes('case lookup') || text.includes('judgment')) {
                        portals.push({
                            name: a.textContent?.trim() || '',
                            url: a.href
                        });
                    }
                });
                return portals;
            }''')

            return portals
        except:
            return []

    async def search(
        self,
        party_name: Optional[str] = None,
        case_number: Optional[str] = None,
        county: Optional[str] = None,
        case_type: str = "civil",  # "civil", "criminal", "tax"
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search NJ Courts records.

        Args:
            party_name: Party name to search
            case_number: Docket number (e.g., ESX-L-001234-24)
            county: County code (e.g., ESX for Essex)
            case_type: Type of case ("civil", "criminal", "tax")
            limit: Maximum results

        Returns:
            List of case dictionaries
        """
        try:
            print(f"[1] Loading NJ Courts {case_type} search...")

            # Try to access civil portal
            await self.page.goto(self.CIVIL_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(4000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Check for WAF block
            if await self._check_waf_block():
                print("    WARNING: Blocked by Incapsula WAF")
                print("    The NJ Courts portal blocks automated access.")
                await self.page.screenshot(path="/tmp/nj_blocked.png")
                print("    Screenshot: /tmp/nj_blocked.png")

                # Try alternative approach through main site
                print("\n[2] Trying alternative access through main site...")
                await self.page.goto(self.BASE_URL, timeout=self.timeout)
                await self.page.wait_for_timeout(3000)

                await self.page.screenshot(path="/tmp/nj_main.png")
                print("    Screenshot: /tmp/nj_main.png")

                # Look for case search links
                links = await self.page.evaluate('''() => {
                    const links = [];
                    document.querySelectorAll('a').forEach(a => {
                        const text = (a.textContent || '').toLowerCase();
                        if (text.includes('case') || text.includes('search') ||
                            text.includes('docket') || text.includes('lookup')) {
                            links.push({ text: a.textContent?.trim(), href: a.href });
                        }
                    });
                    return links;
                }''')

                print(f"    Found {len(links)} potential search links:")
                for link in links[:10]:
                    print(f"      - {link['text'][:40]}")

                return []

            await self.page.screenshot(path="/tmp/nj_form.png")
            print("    Screenshot: /tmp/nj_form.png")

            # Analyze form
            form_info = await self.page.evaluate('''() => {
                const info = { inputs: [], selects: [] };
                document.querySelectorAll('input[type="text"]').forEach(i => {
                    info.inputs.push({
                        name: i.name || i.id,
                        placeholder: i.placeholder || ''
                    });
                });
                document.querySelectorAll('select').forEach(s => {
                    info.selects.push({
                        name: s.name || s.id,
                        options: Array.from(s.options).slice(0, 5).map(o => o.text)
                    });
                });
                return info;
            }''')

            print(f"    Inputs: {[i['name'] for i in form_info['inputs']]}")
            print(f"    Selects: {[s['name'] for s in form_info['selects']]}")

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
                print(f"[3] Searching docket: {case_number}")
                docket_input = await self.page.query_selector(
                    'input[name*="docket" i], input[name*="case" i], '
                    'input[id*="docket" i]'
                )
                if docket_input:
                    await docket_input.fill(case_number)

            # Set county if available
            if county:
                county_select = await self.page.query_selector(
                    'select[name*="county" i], select[id*="county" i]'
                )
                if county_select:
                    try:
                        await county_select.select_option(value=county)
                    except:
                        try:
                            await county_select.select_option(label=county)
                        except:
                            pass

            await self.page.screenshot(path="/tmp/nj_filled.png")

            # Submit search
            print("[4] Submitting search...")
            submit = await self.page.query_selector(
                'button:has-text("Search"), input[type="submit"], '
                'input[value*="Search" i]'
            )
            if submit:
                await submit.click()
            else:
                await self.page.keyboard.press('Enter')

            await self.page.wait_for_timeout(6000)
            await self.page.screenshot(path="/tmp/nj_results.png")
            print("    Screenshot: /tmp/nj_results.png")

            # Save HTML
            html = await self.page.content()
            with open("/tmp/nj_results.html", "w") as f:
                f.write(html)

            # Check results
            content = await self.page.evaluate('() => document.body.innerText')

            if 'no records' in content.lower() or 'no results' in content.lower():
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
            await self.page.screenshot(path="/tmp/nj_error.png")
            return []

    async def _extract_results(self) -> List[Dict[str, Any]]:
        """Extract case results from the page."""
        results = await self.page.evaluate('''() => {
            const cases = [];

            // Try table results
            document.querySelectorAll('table tbody tr').forEach(row => {
                const cells = row.querySelectorAll('td');
                const rowText = row.innerText?.trim() || '';
                const link = row.querySelector('a');

                if (cells.length >= 2) {
                    // NJ docket format: XXX-X-NNNNNN-YY
                    const docketMatch = rowText.match(/[A-Z]{3}-[A-Z]-\\d{6}-\\d{2}/);

                    cases.push({
                        case_number: docketMatch ? docketMatch[0] : (cells[0]?.innerText?.trim() || ''),
                        party_name: cells[1]?.innerText?.trim() || '',
                        county: cells[2]?.innerText?.trim() || '',
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

        # Parse results
        parsed = []
        for r in results:
            case = {
                'case_number': r.get('case_number', ''),
                'party_name': r.get('party_name', ''),
                'county': r.get('county', ''),
                'case_type': r.get('case_type', ''),
                'filed_date': r.get('filed_date', ''),
                'status': r.get('status', ''),
                'url': r.get('url', ''),
                'raw': r.get('raw', ''),
                'court': 'New Jersey Superior Court',
                'state': 'NJ',
                'source_url': self.CIVIL_URL
            }
            parsed.append(case)

        return parsed


async def main():
    parser = argparse.ArgumentParser(description='New Jersey Courts Search')
    parser.add_argument('--name', '-n', help='Party name to search')
    parser.add_argument('--case-number', '-c', help='Docket number')
    parser.add_argument('--county', help='County code (e.g., ESX, BER, HUD)')
    parser.add_argument('--case-type', choices=['civil', 'criminal', 'tax'],
                        default='civil', help='Case type')
    parser.add_argument('--list-portals', action='store_true',
                        help='List available search portals')
    parser.add_argument('--limit', '-l', type=int, default=25, help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True, help='Headless mode')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    print("=" * 70)
    print("NEW JERSEY COURTS SEARCH")
    print("=" * 70)

    async with NewJerseyScraper(headless=args.headless) as scraper:
        # List portals if requested
        if args.list_portals:
            print("Fetching available search portals...")
            portals = await scraper.get_available_portals()
            if portals:
                print(f"\nFound {len(portals)} portals:")
                for p in portals:
                    print(f"  - {p['name']}")
                    print(f"    {p['url']}")
            else:
                print("Could not retrieve portal list")
            return

        # Default search
        if not args.name and not args.case_number:
            args.name = "Smith"

        print(f"Search parameters:")
        print(f"  Party name: {args.name or 'N/A'}")
        print(f"  Case number: {args.case_number or 'N/A'}")
        print(f"  County: {args.county or 'All'}")
        print(f"  Case type: {args.case_type}")
        print(f"  Limit: {args.limit}")
        print("")
        print("NOTE: NJ Courts portal may block automated access.")
        print("      Manual browser access may be required.")
        print("=" * 70)

        results = await scraper.search(
            party_name=args.name,
            case_number=args.case_number,
            county=args.county,
            case_type=args.case_type,
            limit=args.limit
        )

        if results:
            print(f"\nFound {len(results)} results:")
            for i, r in enumerate(results[:15], 1):
                print(f"\n[{i}]")
                if r.get('case_number'):
                    print(f"    Docket: {r['case_number']}")
                if r.get('party_name'):
                    print(f"    Party: {r['party_name'][:50]}")
                if r.get('county'):
                    print(f"    County: {r['county']}")
                if r.get('case_type'):
                    print(f"    Type: {r['case_type']}")
                if r.get('filed_date'):
                    print(f"    Filed: {r['filed_date']}")

            if args.output:
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                print(f"\nResults saved to {args.output}")
        else:
            print("\nNo results found.")
            print("The NJ Courts portal may be blocking automated access.")
            print("Try accessing directly: https://www.njcourts.gov/public/find-a-case")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
