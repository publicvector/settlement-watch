#!/usr/bin/env python3
"""
Montana Courts Scraper

This scraper accesses Montana court systems:
1. District Court Portal - https://dcportal.pubcourts.mt.gov/
2. Courts of Limited Jurisdiction Portal - https://coljportal.pubcourts.mt.gov/
3. Supreme Court Docket - https://supremecourtdocket.mt.gov/

Features:
- No CAPTCHA required
- No login required (select county to access)
- FullCourt Enterprise system

Usage:
    python scraper_montana.py --county "Yellowstone" --name "Smith"
    python scraper_montana.py --court supreme --name "Smith"
"""

import asyncio
import argparse
import json
from playwright.async_api import async_playwright
from typing import List, Dict, Any, Optional

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False


class MontanaCourtScraper:
    """Scraper for Montana Court Systems."""

    DISTRICT_COURT_URL = "https://dcportal.pubcourts.mt.gov/"
    LIMITED_JURISDICTION_URL = "https://coljportal.pubcourts.mt.gov/"
    SUPREME_COURT_URL = "https://supremecourtdocket.mt.gov/"

    # Common Montana counties
    COUNTIES = [
        "Yellowstone", "Missoula", "Gallatin", "Flathead", "Cascade",
        "Lewis and Clark", "Silver Bow", "Ravalli", "Lake", "Lincoln"
    ]

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

    async def get_available_counties(self, court_type: str = "district") -> List[str]:
        """Get list of available counties from portal."""
        url = self.DISTRICT_COURT_URL if court_type == "district" else self.LIMITED_JURISDICTION_URL

        try:
            await self.page.goto(url, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            counties = await self.page.evaluate('''() => {
                const options = [];
                document.querySelectorAll('select option, a[href*="court"], li').forEach(el => {
                    const text = el.innerText?.trim() || el.textContent?.trim() || '';
                    if (text && text.length > 2 && text.length < 50 &&
                        !text.includes('Select') && !text.includes('--')) {
                        options.push(text);
                    }
                });
                return [...new Set(options)];
            }''')

            return counties

        except Exception as e:
            print(f"Error getting counties: {e}")
            return self.COUNTIES

    async def search_district_court(
        self,
        county: str,
        party_name: Optional[str] = None,
        case_number: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search Montana District Court records.

        Args:
            county: County name (e.g., "Yellowstone")
            party_name: Name to search for
            case_number: Case number to search for
            limit: Maximum results

        Returns:
            List of case results
        """
        try:
            print(f"[1] Loading Montana District Court Portal...")
            await self.page.goto(self.DISTRICT_COURT_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Check for CAPTCHA
            content = await self.page.content()
            if 'captcha' in content.lower() or 'recaptcha' in content.lower():
                print("    ERROR: CAPTCHA detected!")
                return []

            await self.page.screenshot(path="/tmp/montana_dc_main.png")

            # Find and select county
            print(f"[2] Selecting county: {county}...")
            county_select = await self.page.query_selector('select')

            if county_select:
                # Get available options
                options = await self.page.evaluate('''() => {
                    const sel = document.querySelector('select');
                    if (!sel) return [];
                    return Array.from(sel.options).map(o => ({
                        text: o.text,
                        value: o.value
                    }));
                }''')

                # Find matching county
                matching = None
                for opt in options:
                    if county.lower() in opt['text'].lower():
                        matching = opt
                        break

                if matching:
                    await county_select.select_option(matching['value'])
                    print(f"    Selected: {matching['text']}")
                    await self.page.wait_for_timeout(2000)
                else:
                    # Select first non-empty option
                    for opt in options:
                        if opt['value']:
                            await county_select.select_option(opt['value'])
                            print(f"    Selected first available: {opt['text']}")
                            break
            else:
                # Try clicking on county link
                county_link = await self.page.query_selector(f'a:has-text("{county}")')
                if county_link:
                    await county_link.click()
                    print(f"    Clicked county link: {county}")
                    await self.page.wait_for_timeout(2000)

            await self.page.screenshot(path="/tmp/montana_dc_county.png")

            # Look for search form
            print("[3] Looking for search form...")
            form_info = await self.page.evaluate('''() => {
                const info = { inputs: [], buttons: [], links: [] };
                document.querySelectorAll('input[type="text"]').forEach(i => {
                    if (i.offsetParent !== null) {
                        info.inputs.push({
                            name: i.name || i.id,
                            placeholder: i.placeholder
                        });
                    }
                });
                document.querySelectorAll('button, input[type="submit"]').forEach(b => {
                    if (b.offsetParent !== null) {
                        info.buttons.push(b.value || b.textContent?.trim());
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

            print(f"    Inputs: {form_info['inputs']}")
            print(f"    Buttons: {form_info['buttons'][:5]}")
            print(f"    Search links: {[l['text'] for l in form_info['links'][:5]]}")

            # Try to find case search link
            case_search = await self.page.query_selector(
                'a:has-text("Case Search"), a:has-text("Search Cases"), '
                'a:has-text("Party Search"), button:has-text("Search")'
            )
            if case_search:
                await case_search.click()
                print("    Clicked case search link")
                await self.page.wait_for_timeout(2000)
                await self.page.screenshot(path="/tmp/montana_dc_search.png")

            # Fill search criteria
            if party_name:
                print(f"[4] Filling party name: {party_name}...")
                name_input = await self.page.query_selector(
                    'input[name*="party" i], input[id*="party" i], '
                    'input[name*="name" i], input[placeholder*="name" i], '
                    'input[type="text"]:visible'
                )
                if name_input:
                    await name_input.fill(party_name)
                    await self.page.wait_for_timeout(500)

            if case_number:
                print(f"[4] Filling case number: {case_number}...")
                case_input = await self.page.query_selector(
                    'input[name*="case" i], input[id*="case" i], '
                    'input[placeholder*="case" i]'
                )
                if case_input:
                    await case_input.fill(case_number)

            # Submit search
            print("[5] Submitting search...")
            search_btn = await self.page.query_selector(
                'button:has-text("Search"), input[type="submit"], '
                'button[type="submit"], input[value*="Search" i]'
            )
            if search_btn:
                await search_btn.click()
            else:
                await self.page.keyboard.press('Enter')

            await self.page.wait_for_timeout(5000)
            await self.page.screenshot(path="/tmp/montana_dc_results.png")

            # Save HTML for debugging
            html = await self.page.content()
            with open("/tmp/montana_dc_results.html", "w") as f:
                f.write(html)

            # Extract results
            print("[6] Extracting results...")
            results = await self._extract_results()

            print(f"    Found {len(results)} results")
            return results[:limit]

        except Exception as e:
            print(f"    Error: {e}")
            import traceback
            traceback.print_exc()
            await self.page.screenshot(path="/tmp/montana_dc_error.png")
            return []

    async def search_supreme_court(
        self,
        party_name: Optional[str] = None,
        case_number: Optional[str] = None,
        attorney_name: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search Montana Supreme Court docket.

        Args:
            party_name: Party name to search
            case_number: Case number
            attorney_name: Attorney name
            limit: Maximum results

        Returns:
            List of case results
        """
        try:
            print(f"[1] Loading Montana Supreme Court Docket...")
            await self.page.goto(self.SUPREME_COURT_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            content = await self.page.content()
            if 'captcha' in content.lower() or 'recaptcha' in content.lower():
                print("    ERROR: CAPTCHA detected!")
                return []

            await self.page.screenshot(path="/tmp/montana_sc_main.png")

            # Analyze form
            form_info = await self.page.evaluate('''() => {
                const info = { inputs: [], selects: [], buttons: [] };
                document.querySelectorAll('input').forEach(i => {
                    if (i.offsetParent !== null) {
                        info.inputs.push({
                            type: i.type,
                            name: i.name || i.id,
                            placeholder: i.placeholder
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
                document.querySelectorAll('button').forEach(b => {
                    if (b.offsetParent !== null) {
                        info.buttons.push(b.textContent?.trim());
                    }
                });
                return info;
            }''')

            print(f"    Inputs: {[(i['name'], i['type']) for i in form_info['inputs']]}")
            print(f"    Selects: {form_info['selects']}")
            print(f"    Buttons: {form_info['buttons'][:5]}")

            # Fill search criteria
            if party_name:
                print(f"[2] Filling party name: {party_name}...")
                name_input = await self.page.query_selector(
                    'input[name*="party" i], input[id*="party" i], '
                    'input[name*="name" i], input[placeholder*="name" i]'
                )
                if name_input:
                    await name_input.fill(party_name)

            if case_number:
                print(f"[2] Filling case number: {case_number}...")
                case_input = await self.page.query_selector(
                    'input[name*="case" i], input[id*="case" i]'
                )
                if case_input:
                    await case_input.fill(case_number)

            if attorney_name:
                print(f"[2] Filling attorney name: {attorney_name}...")
                atty_input = await self.page.query_selector(
                    'input[name*="attorney" i], input[id*="attorney" i]'
                )
                if atty_input:
                    await atty_input.fill(attorney_name)

            # Submit
            print("[3] Submitting search...")
            search_btn = await self.page.query_selector(
                'button:has-text("Search"), input[type="submit"], button[type="submit"]'
            )
            if search_btn:
                await search_btn.click()
            else:
                await self.page.keyboard.press('Enter')

            await self.page.wait_for_timeout(5000)
            await self.page.screenshot(path="/tmp/montana_sc_results.png")

            # Extract results
            print("[4] Extracting results...")
            results = await self._extract_results()

            print(f"    Found {len(results)} results")
            return results[:limit]

        except Exception as e:
            print(f"    Error: {e}")
            await self.page.screenshot(path="/tmp/montana_sc_error.png")
            return []

    async def _extract_results(self) -> List[Dict[str, Any]]:
        """Extract case results from the page."""
        results = await self.page.evaluate('''() => {
            const items = [];

            // Try table rows
            document.querySelectorAll('table tr').forEach(row => {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 2) {
                    const rowText = row.innerText?.trim() || '';
                    if (rowText.length > 20) {
                        items.push({
                            case_number: cells[0]?.innerText?.trim() || '',
                            caption: cells[1]?.innerText?.trim()?.substring(0, 200) || '',
                            court: cells[2]?.innerText?.trim() || '',
                            status: cells[3]?.innerText?.trim() || '',
                            filed_date: cells[4]?.innerText?.trim() || '',
                            raw: rowText.substring(0, 300),
                            source: 'Montana Courts'
                        });
                    }
                }
            });

            // Try div-based results
            if (items.length === 0) {
                document.querySelectorAll('.case, .result, [class*="case-item"]').forEach(el => {
                    const text = el.innerText?.trim() || '';
                    const link = el.querySelector('a')?.href || '';
                    if (text.length > 20) {
                        items.push({
                            raw: text.substring(0, 300),
                            link: link,
                            source: 'Montana Courts'
                        });
                    }
                });
            }

            // Try case links
            if (items.length === 0) {
                document.querySelectorAll('a[href*="case"], a[href*="docket"]').forEach(a => {
                    const text = a.innerText?.trim() || '';
                    if (text.length > 5) {
                        items.push({
                            case_number: text,
                            link: a.href,
                            source: 'Montana Courts'
                        });
                    }
                });
            }

            return items;
        }''')

        # Deduplicate
        seen = set()
        unique = []
        for r in results:
            key = r.get('case_number', '') or r.get('raw', '')[:50]
            if key and key not in seen:
                seen.add(key)
                unique.append(r)

        return unique


async def main():
    parser = argparse.ArgumentParser(description='Montana Courts Scraper')
    parser.add_argument('--court', '-c', choices=['district', 'supreme', 'limited'],
                        default='district', help='Court type')
    parser.add_argument('--county', help='County name (for district/limited)')
    parser.add_argument('--name', '-n', help='Party name to search')
    parser.add_argument('--case-number', help='Case number')
    parser.add_argument('--attorney', help='Attorney name (Supreme Court)')
    parser.add_argument('--list-counties', action='store_true',
                        help='List available counties')
    parser.add_argument('--limit', '-l', type=int, default=25,
                        help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True)
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    print("=" * 70)
    print("MONTANA COURTS SCRAPER")
    print("=" * 70)

    async with MontanaCourtScraper(headless=args.headless) as scraper:
        if args.list_counties:
            print("\nFetching available counties...")
            counties = await scraper.get_available_counties(args.court)
            print(f"\nAvailable counties ({len(counties)}):")
            for c in counties[:20]:
                print(f"  - {c}")
            return

        # Default values
        if not args.county and args.court in ['district', 'limited']:
            args.county = "Yellowstone"
        if not args.name and not args.case_number:
            args.name = "Smith"

        print(f"Search parameters:")
        print(f"  Court: {args.court}")
        print(f"  County: {args.county or 'N/A'}")
        print(f"  Party name: {args.name or 'N/A'}")
        print(f"  Case number: {args.case_number or 'N/A'}")

        results = []

        if args.court == 'district':
            results = await scraper.search_district_court(
                county=args.county,
                party_name=args.name,
                case_number=args.case_number,
                limit=args.limit
            )
        elif args.court == 'supreme':
            results = await scraper.search_supreme_court(
                party_name=args.name,
                case_number=args.case_number,
                attorney_name=args.attorney,
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
            print("\nNo results found. Check /tmp/montana_*.png for screenshots.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
