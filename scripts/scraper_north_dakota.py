#!/usr/bin/env python3
"""
North Dakota Courts Public Search Scraper

This scraper accesses the North Dakota Court System public search.
URL: https://publicsearch.ndcourts.gov/

Features:
- No CAPTCHA required (based on documentation)
- No login required
- Supports name, case number, and citation searches
- Covers Criminal, Traffic, and Civil case types
- Includes municipal court cases from certain areas

Usage:
    python scraper_north_dakota.py --name "Smith"
    python scraper_north_dakota.py --case-number "CV-2024-001234"
    python scraper_north_dakota.py --citation "ND123456"
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


class NorthDakotaCourtScraper:
    """Scraper for North Dakota Court System Public Search."""

    BASE_URL = "https://publicsearch.ndcourts.gov/"
    ALT_URL = "https://www.ndcourts.gov/public-access"

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
        citation_number: Optional[str] = None,
        case_type: Optional[str] = None,  # "criminal", "traffic", "civil"
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search North Dakota court records.

        Args:
            party_name: Name of party to search
            case_number: Case number to search
            citation_number: Citation number
            case_type: Filter by case type
            limit: Maximum results

        Returns:
            List of case dictionaries
        """
        try:
            # Try direct search URL first
            print(f"[1] Loading North Dakota Public Search...")
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Check for CAPTCHA
            content = await self.page.content()
            if 'captcha' in content.lower() or 'recaptcha' in content.lower():
                print("    WARNING: CAPTCHA detected!")
                await self.page.screenshot(path="/tmp/nd_captcha.png")
                return []

            await self.page.screenshot(path="/tmp/nd_main.png")
            print("    Screenshot: /tmp/nd_main.png")

            # Check for disclaimer/accept button
            print("[2] Checking for disclaimer...")
            accept_btn = await self.page.query_selector(
                'button:has-text("Accept"), button:has-text("Agree"), '
                'button:has-text("Continue"), button:has-text("Proceed"), '
                'input[type="submit"][value*="Accept" i], '
                'input[type="submit"][value*="Continue" i]'
            )
            if accept_btn:
                await accept_btn.click()
                print("    Accepted disclaimer")
                await self.page.wait_for_timeout(2000)
                await self.page.screenshot(path="/tmp/nd_after_accept.png")

            # Analyze form structure
            print("[3] Analyzing form structure...")
            form_info = await self.page.evaluate('''() => {
                const info = { inputs: [], selects: [], buttons: [], links: [] };

                document.querySelectorAll('input').forEach(i => {
                    if (i.offsetParent !== null) {
                        info.inputs.push({
                            type: i.type,
                            name: i.name || i.id,
                            placeholder: i.placeholder,
                            value: i.value
                        });
                    }
                });

                document.querySelectorAll('select').forEach(s => {
                    if (s.offsetParent !== null) {
                        const opts = Array.from(s.options).slice(0, 5).map(o => o.text);
                        info.selects.push({ name: s.name || s.id, options: opts });
                    }
                });

                document.querySelectorAll('button, input[type="submit"]').forEach(b => {
                    if (b.offsetParent !== null) {
                        info.buttons.push(b.value || b.textContent?.trim());
                    }
                });

                document.querySelectorAll('a').forEach(a => {
                    const text = (a.textContent || '').toLowerCase();
                    if (text.includes('search') || text.includes('case') || text.includes('name')) {
                        info.links.push({ text: a.textContent?.trim(), href: a.href });
                    }
                });

                return info;
            }''')

            print(f"    Inputs: {[(i['name'], i['type']) for i in form_info['inputs'][:10]]}")
            print(f"    Selects: {form_info['selects'][:3]}")
            print(f"    Buttons: {form_info['buttons'][:5]}")
            print(f"    Search links: {[l['text'] for l in form_info['links'][:5]]}")

            # Look for search tabs or mode selectors
            name_tab = await self.page.query_selector(
                'a:has-text("Name Search"), button:has-text("Name"), '
                'input[type="radio"][value*="name" i], tab:has-text("Name")'
            )
            if name_tab:
                await name_tab.click()
                print("    Clicked Name Search tab")
                await self.page.wait_for_timeout(1000)

            # Fill search criteria
            if party_name:
                print(f"[4] Filling party name: {party_name}...")
                name_input = await self.page.query_selector(
                    'input[name*="name" i]:not([type="hidden"]), '
                    'input[id*="name" i]:not([type="hidden"]), '
                    'input[placeholder*="name" i], '
                    'input[name*="party" i], '
                    'input[type="text"]:visible'
                )

                if name_input:
                    await name_input.fill(party_name)
                    print("    Filled name input")
                else:
                    # Try first visible text input
                    text_inputs = await self.page.query_selector_all('input[type="text"]')
                    for inp in text_inputs:
                        if await inp.is_visible():
                            await inp.fill(party_name)
                            print("    Filled first visible text input")
                            break

            if case_number:
                print(f"[4] Filling case number: {case_number}...")
                case_input = await self.page.query_selector(
                    'input[name*="case" i], input[id*="case" i], '
                    'input[placeholder*="case" i]'
                )
                if case_input:
                    await case_input.fill(case_number)

            if citation_number:
                print(f"[4] Filling citation number: {citation_number}...")
                cite_input = await self.page.query_selector(
                    'input[name*="citation" i], input[id*="citation" i]'
                )
                if cite_input:
                    await cite_input.fill(citation_number)

            if case_type:
                print(f"[4] Selecting case type: {case_type}...")
                type_select = await self.page.query_selector(
                    'select[name*="type" i], select[id*="type" i]'
                )
                if type_select:
                    await type_select.select_option(label=case_type.capitalize())

            await self.page.wait_for_timeout(500)
            await self.page.screenshot(path="/tmp/nd_filled.png")

            # Submit search
            print("[5] Submitting search...")
            search_btn = await self.page.query_selector(
                'button:has-text("Search"), input[type="submit"], '
                'button[type="submit"], input[value*="Search" i]'
            )
            if search_btn:
                await search_btn.click()
                print("    Clicked search button")
            else:
                await self.page.keyboard.press('Enter')
                print("    Pressed Enter")

            # Wait for results
            print("    Waiting for results...")
            await self.page.wait_for_timeout(5000)

            # Try to wait for results table
            try:
                await self.page.wait_for_selector(
                    'table, .results, .search-results, [class*="result"]',
                    timeout=15000
                )
            except Exception:
                pass

            await self.page.screenshot(path="/tmp/nd_results.png")
            print("    Screenshot: /tmp/nd_results.png")

            # Save HTML
            html = await self.page.content()
            with open("/tmp/nd_results.html", "w") as f:
                f.write(html)

            # Check page content
            page_text = await self.page.evaluate('() => document.body.innerText')
            print(f"\n    Page text preview: {page_text[:500]}...")

            # Check for no results
            if 'no results' in page_text.lower() or 'no cases found' in page_text.lower():
                print("    No results found")
                return []

            # Extract results
            print("[6] Extracting results...")
            results = await self._extract_results()

            print(f"    Found {len(results)} results")
            return results[:limit]

        except Exception as e:
            print(f"    Error: {e}")
            import traceback
            traceback.print_exc()
            await self.page.screenshot(path="/tmp/nd_error.png")
            return []

    async def _extract_results(self) -> List[Dict[str, Any]]:
        """Extract case results from the page."""
        results = await self.page.evaluate('''() => {
            const items = [];

            // Try table rows
            document.querySelectorAll('table tr').forEach((row, idx) => {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 2) {
                    const rowText = row.innerText?.trim() || '';

                    // Skip header rows
                    if (row.querySelector('th') || rowText.includes('Case Number') && idx === 0) {
                        return;
                    }

                    items.push({
                        case_number: cells[0]?.innerText?.trim() || '',
                        party_name: cells[1]?.innerText?.trim() || '',
                        case_type: cells[2]?.innerText?.trim() || '',
                        court: cells[3]?.innerText?.trim() || '',
                        filed_date: cells[4]?.innerText?.trim() || '',
                        status: cells[5]?.innerText?.trim() || '',
                        raw: rowText.substring(0, 300),
                        source: 'North Dakota Courts'
                    });
                }
            });

            // Try div-based results
            if (items.length === 0) {
                document.querySelectorAll('.case-result, .search-result, .result-row').forEach(el => {
                    const text = el.innerText?.trim() || '';
                    const link = el.querySelector('a')?.href || '';
                    if (text.length > 20) {
                        items.push({
                            raw: text.substring(0, 300),
                            link: link,
                            source: 'North Dakota Courts'
                        });
                    }
                });
            }

            // Try case number links
            if (items.length === 0) {
                document.querySelectorAll('a').forEach(a => {
                    const text = a.innerText?.trim() || '';
                    const href = a.href || '';
                    // ND case numbers typically have format like "09-2024-CV-00123"
                    if (text.match(/\\d{2}-\\d{4}-[A-Z]{2,3}-\\d+/) ||
                        href.includes('case') || href.includes('docket')) {
                        const parent = a.closest('tr, div, li');
                        const context = parent?.innerText?.trim() || text;
                        items.push({
                            case_number: text,
                            link: href,
                            raw: context.substring(0, 300),
                            source: 'North Dakota Courts'
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

    async def search_via_alt_url(
        self,
        party_name: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Try alternative URL if main search is unavailable.
        """
        try:
            print(f"[1] Loading alternative URL: {self.ALT_URL}")
            await self.page.goto(self.ALT_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            # Click through to search
            proceed_link = await self.page.query_selector(
                'a:has-text("Proceed"), a:has-text("Click Here"), '
                'a:has-text("Case Search"), button:has-text("Continue")'
            )
            if proceed_link:
                await proceed_link.click()
                await self.page.wait_for_timeout(3000)

            # Now try regular search
            return await self.search(party_name=party_name, limit=limit)

        except Exception as e:
            print(f"    Alt URL error: {e}")
            return []


async def main():
    parser = argparse.ArgumentParser(description='North Dakota Court Search Scraper')
    parser.add_argument('--name', '-n', help='Party name to search')
    parser.add_argument('--case-number', '-c', help='Case number')
    parser.add_argument('--citation', help='Citation number')
    parser.add_argument('--case-type', choices=['criminal', 'traffic', 'civil'],
                        help='Case type filter')
    parser.add_argument('--limit', '-l', type=int, default=25,
                        help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True)
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    # Default search
    if not args.name and not args.case_number and not args.citation:
        args.name = "Smith"

    print("=" * 70)
    print("NORTH DAKOTA COURT SEARCH SCRAPER")
    print("=" * 70)
    print(f"Search parameters:")
    print(f"  Party name: {args.name or 'N/A'}")
    print(f"  Case number: {args.case_number or 'N/A'}")
    print(f"  Citation: {args.citation or 'N/A'}")
    print(f"  Case type: {args.case_type or 'All'}")
    print("=" * 70)

    async with NorthDakotaCourtScraper(headless=args.headless) as scraper:
        results = await scraper.search(
            party_name=args.name,
            case_number=args.case_number,
            citation_number=args.citation,
            case_type=args.case_type,
            limit=args.limit
        )

        if not results:
            print("\nTrying alternative URL...")
            results = await scraper.search_via_alt_url(
                party_name=args.name or "Smith",
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
            print("\nNo results found. Check /tmp/nd_*.png for screenshots.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
