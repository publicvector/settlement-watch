#!/usr/bin/env python3
"""
Louisiana Courts Scraper

This scraper accesses Louisiana state court search systems:
1. Louisiana Supreme Court - https://www.lasc.org/Search
2. Louisiana Fifth Circuit Court of Appeal - https://www.fifthcircuit.org/searchcases.aspx

Features:
- No CAPTCHA required
- No login required
- Supports case number and party name searches

Usage:
    python scraper_louisiana.py --name "Smith"
    python scraper_louisiana.py --case-year 24 --case-number 123
    python scraper_louisiana.py --court supreme --query "insurance"
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


class LouisianaCourtScraper:
    """Scraper for Louisiana Court Systems."""

    SUPREME_COURT_URL = "https://www.lasc.org/Search"
    FIFTH_CIRCUIT_URL = "https://www.fifthcircuit.org/searchcases.aspx"

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

    async def search_supreme_court(
        self,
        query: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search Louisiana Supreme Court opinions and actions.

        Args:
            query: Search term (case name, topic, etc.)
            limit: Maximum results

        Returns:
            List of search results
        """
        try:
            print(f"[1] Loading Louisiana Supreme Court Search...")
            await self.page.goto(self.SUPREME_COURT_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Check for CAPTCHA
            content = await self.page.content()
            if 'captcha' in content.lower() or 'recaptcha' in content.lower():
                print("    ERROR: CAPTCHA detected!")
                return []

            # Find search input
            print(f"[2] Filling search query: {query}...")
            search_input = await self.page.query_selector(
                'input[type="text"], input[type="search"], '
                'input[name*="search" i], input[id*="search" i]'
            )

            if not search_input:
                print("    ERROR: Search input not found")
                await self.page.screenshot(path="/tmp/louisiana_sc_error.png")
                return []

            await search_input.fill(query)
            await self.page.wait_for_timeout(500)

            # Submit search
            print("[3] Submitting search...")
            search_btn = await self.page.query_selector(
                'button:has-text("Go"), button:has-text("Search"), '
                'input[type="submit"], button[type="submit"]'
            )
            if search_btn:
                await search_btn.click()
            else:
                await self.page.keyboard.press('Enter')

            await self.page.wait_for_timeout(5000)

            # Extract results
            print("[4] Extracting results...")
            results = await self._extract_supreme_court_results()

            print(f"    Found {len(results)} results")
            return results[:limit]

        except Exception as e:
            print(f"    Error: {e}")
            await self.page.screenshot(path="/tmp/louisiana_sc_error.png")
            return []

    async def _extract_supreme_court_results(self) -> List[Dict[str, Any]]:
        """Extract results from Supreme Court search."""
        results = await self.page.evaluate('''() => {
            const items = [];

            // Try search result selectors
            const selectors = [
                '.search-result',
                '.result-item',
                'article',
                '.views-row',
                'li.result'
            ];

            for (const selector of selectors) {
                const elements = document.querySelectorAll(selector);
                if (elements.length > 0) {
                    elements.forEach(el => {
                        const title = el.querySelector('h2, h3, .title, a')?.innerText?.trim() || '';
                        const link = el.querySelector('a')?.href || '';
                        const snippet = el.querySelector('p, .snippet, .description')?.innerText?.trim() || '';
                        const date = el.querySelector('.date, time, [class*="date"]')?.innerText?.trim() || '';

                        if (title || snippet) {
                            items.push({
                                title: title.substring(0, 200),
                                link: link,
                                snippet: snippet.substring(0, 300),
                                date: date,
                                source: 'Louisiana Supreme Court'
                            });
                        }
                    });
                    break;
                }
            }

            // Fallback: get all links that look like case references
            if (items.length === 0) {
                document.querySelectorAll('a').forEach(a => {
                    const text = a.innerText?.trim() || '';
                    const href = a.href || '';
                    if (text.length > 10 && (
                        text.match(/\\d{4}/) ||
                        href.includes('opinion') ||
                        href.includes('case') ||
                        href.includes('decision')
                    )) {
                        items.push({
                            title: text.substring(0, 200),
                            link: href,
                            source: 'Louisiana Supreme Court'
                        });
                    }
                });
            }

            return items;
        }''')

        return results

    async def search_fifth_circuit(
        self,
        case_year: Optional[str] = None,
        case_number: Optional[str] = None,
        litigant_name: Optional[str] = None,
        attorney_barroll: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search Louisiana Fifth Circuit Court of Appeal.

        Args:
            case_year: Two-digit year (e.g., "24" for 2024)
            case_number: Case number
            litigant_name: Name of litigant
            attorney_barroll: Attorney bar roll number
            limit: Maximum results

        Returns:
            List of case results
        """
        try:
            print(f"[1] Loading Louisiana Fifth Circuit Search...")
            await self.page.goto(self.FIFTH_CIRCUIT_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Check for CAPTCHA
            content = await self.page.content()
            if 'captcha' in content.lower() or 'recaptcha' in content.lower():
                print("    ERROR: CAPTCHA detected!")
                return []

            # Analyze form structure
            form_info = await self.page.evaluate('''() => {
                const info = { inputs: [], selects: [], radios: [] };
                document.querySelectorAll('input[type="text"]').forEach(i => {
                    info.inputs.push({
                        name: i.name || i.id,
                        visible: i.offsetParent !== null
                    });
                });
                document.querySelectorAll('select').forEach(s => {
                    info.selects.push({
                        name: s.name || s.id,
                        options: Array.from(s.options).slice(0, 5).map(o => o.value)
                    });
                });
                document.querySelectorAll('input[type="radio"]').forEach(r => {
                    info.radios.push({
                        name: r.name,
                        value: r.value,
                        id: r.id
                    });
                });
                return info;
            }''')

            print(f"    Form inputs: {[i['name'] for i in form_info['inputs']]}")

            # Select search mode based on what we have
            if litigant_name:
                # Search by litigant name
                print(f"[2] Setting search mode to Litigant Name...")
                name_radio = await self.page.query_selector(
                    'input[type="radio"][value*="name" i], '
                    'input[type="radio"][id*="litigant" i]'
                )
                if name_radio:
                    await name_radio.click()
                    await self.page.wait_for_timeout(500)

                name_input = await self.page.query_selector(
                    'input[name*="litigant" i], input[id*="litigant" i], '
                    'input[name*="name" i]:not([type="radio"])'
                )
                if name_input:
                    await name_input.fill(litigant_name)
                    print(f"    Filled litigant name: {litigant_name}")

            elif case_year and case_number:
                # Search by case number
                print(f"[2] Searching by case number...")
                year_select = await self.page.query_selector(
                    'select[name*="year" i], select[id*="year" i]'
                )
                if year_select:
                    await year_select.select_option(case_year)
                    print(f"    Selected year: {case_year}")

                num_input = await self.page.query_selector(
                    'input[name*="case" i], input[id*="case" i], '
                    'input[name*="number" i]'
                )
                if num_input:
                    await num_input.fill(case_number)
                    print(f"    Filled case number: {case_number}")

            else:
                print("    No search criteria provided!")
                return []

            await self.page.wait_for_timeout(500)
            await self.page.screenshot(path="/tmp/louisiana_5th_filled.png")

            # Submit search
            print("[3] Submitting search...")
            submit_btn = await self.page.query_selector(
                'input[type="submit"], button[type="submit"], '
                'button:has-text("Search"), input[value*="Search" i]'
            )
            if submit_btn:
                await submit_btn.click()
            else:
                await self.page.keyboard.press('Enter')

            await self.page.wait_for_timeout(5000)
            await self.page.screenshot(path="/tmp/louisiana_5th_results.png")

            # Extract results
            print("[4] Extracting results...")
            results = await self._extract_fifth_circuit_results()

            print(f"    Found {len(results)} results")
            return results[:limit]

        except Exception as e:
            print(f"    Error: {e}")
            import traceback
            traceback.print_exc()
            await self.page.screenshot(path="/tmp/louisiana_5th_error.png")
            return []

    async def _extract_fifth_circuit_results(self) -> List[Dict[str, Any]]:
        """Extract results from Fifth Circuit search."""
        results = await self.page.evaluate('''() => {
            const items = [];

            // Try table rows first
            document.querySelectorAll('table tr').forEach(row => {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 2) {
                    const caseNum = cells[0]?.innerText?.trim() || '';
                    const caption = cells[1]?.innerText?.trim() || '';
                    const status = cells[2]?.innerText?.trim() || '';

                    if (caseNum && caseNum !== 'Case Number') {
                        items.push({
                            case_number: caseNum,
                            caption: caption.substring(0, 200),
                            status: status,
                            source: 'Louisiana Fifth Circuit'
                        });
                    }
                }
            });

            // Also try div-based results
            if (items.length === 0) {
                document.querySelectorAll('.case-result, .result, [class*="case"]').forEach(el => {
                    const text = el.innerText?.trim() || '';
                    const link = el.querySelector('a')?.href || '';
                    if (text.length > 10) {
                        items.push({
                            raw: text.substring(0, 300),
                            link: link,
                            source: 'Louisiana Fifth Circuit'
                        });
                    }
                });
            }

            return items;
        }''')

        return results


async def main():
    parser = argparse.ArgumentParser(description='Louisiana Courts Scraper')
    parser.add_argument('--court', '-c', choices=['supreme', 'fifth', 'all'],
                        default='all', help='Court to search')
    parser.add_argument('--query', '-q', help='Search query (Supreme Court)')
    parser.add_argument('--name', '-n', help='Litigant name (Fifth Circuit)')
    parser.add_argument('--case-year', help='Two-digit case year (Fifth Circuit)')
    parser.add_argument('--case-number', help='Case number (Fifth Circuit)')
    parser.add_argument('--limit', '-l', type=int, default=25,
                        help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True,
                        help='Run headless')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    # Default search
    if not args.query and not args.name and not args.case_number:
        args.query = "Smith"
        args.name = "Smith"

    print("=" * 70)
    print("LOUISIANA COURTS SCRAPER")
    print("=" * 70)

    all_results = []

    async with LouisianaCourtScraper(headless=args.headless) as scraper:
        if args.court in ['supreme', 'all'] and args.query:
            print("\n--- Louisiana Supreme Court ---")
            results = await scraper.search_supreme_court(
                query=args.query,
                limit=args.limit
            )
            all_results.extend(results)

            if results:
                print(f"\nSupreme Court Results ({len(results)}):")
                for i, r in enumerate(results[:10], 1):
                    print(f"\n[{i}] {r.get('title', 'N/A')[:60]}")
                    if r.get('date'):
                        print(f"    Date: {r['date']}")
                    if r.get('link'):
                        print(f"    Link: {r['link'][:70]}")

        if args.court in ['fifth', 'all']:
            print("\n--- Louisiana Fifth Circuit ---")

            if args.name:
                results = await scraper.search_fifth_circuit(
                    litigant_name=args.name,
                    limit=args.limit
                )
            elif args.case_year and args.case_number:
                results = await scraper.search_fifth_circuit(
                    case_year=args.case_year,
                    case_number=args.case_number,
                    limit=args.limit
                )
            else:
                results = []

            all_results.extend(results)

            if results:
                print(f"\nFifth Circuit Results ({len(results)}):")
                for i, r in enumerate(results[:10], 1):
                    print(f"\n[{i}]")
                    for k, v in r.items():
                        if v and k != 'raw':
                            print(f"    {k}: {str(v)[:60]}")

    if args.output and all_results:
        with open(args.output, 'w') as f:
            json.dump(all_results, f, indent=2)
        print(f"\nResults saved to {args.output}")

    print("\n" + "=" * 70)
    print(f"Total results: {len(all_results)}")


if __name__ == "__main__":
    asyncio.run(main())
