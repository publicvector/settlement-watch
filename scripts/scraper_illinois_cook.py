#!/usr/bin/env python3
"""
Illinois Cook County Circuit Court Search Scraper

This scraper accesses the Cook County Circuit Court Clerk's Office case search.
URL: https://casesearch.cookcountyclerkofcourt.org/

Features:
- Civil, Law, Chancery, Domestic Relations cases
- Party name and case number search
- No login required
- Uses ASP.NET WebForms (requires special postback handling)

Note: This site uses complex ASP.NET forms. Radio button switching
requires careful handling of postback events.

Usage:
    python scraper_illinois_cook.py --name "Smith"
    python scraper_illinois_cook.py --name "Smith" --first "John"
    python scraper_illinois_cook.py --case-number "2024L001234"
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


class IllinoisCookCountyScraper:
    """Scraper for Cook County (Chicago) Illinois Court Records."""

    BASE_URL = "https://casesearch.cookcountyclerkofcourt.org/"

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

    async def search_civil(
        self,
        last_name: Optional[str] = None,
        first_name: Optional[str] = None,
        case_number: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search Cook County Civil/Law/Chancery cases.

        Args:
            last_name: Party last name
            first_name: Party first name (recommended for better results)
            case_number: Case number to search
            limit: Maximum results to return

        Returns:
            List of case dictionaries
        """
        try:
            print(f"[1] Loading Cook County case search...")
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(4000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Check if blocked by WAF
            content = await self.page.evaluate('() => document.body.innerText')
            if 'rejected' in content.lower() or 'blocked' in content.lower():
                print("    BLOCKED by Web Application Firewall")
                await self.page.screenshot(path="/tmp/cook_county_blocked.png")
                return []

            # Click on Civil search
            print("[2] Navigating to Civil search...")
            civil_link = await self.page.query_selector(
                'a:has-text("Start Search"), a:has-text("Civil")'
            )
            if civil_link:
                await civil_link.click()
                await self.page.wait_for_timeout(3000)

            await self.page.screenshot(path="/tmp/cook_county_civil.png")

            if case_number:
                # Case number search
                print(f"[3] Searching by case number: {case_number}")
                case_input = await self.page.query_selector(
                    'input[id*="txtCaseNumber"], input[name*="CaseNumber"]'
                )
                if case_input:
                    await case_input.fill(case_number)
            else:
                # Name search - need to click radio button first
                print("[3] Switching to Name search mode...")

                # Click the Name search radio button directly
                name_radio = await self.page.query_selector(
                    'input[id*="rblSearchType_1"], input[value="Name"]'
                )
                if name_radio:
                    await name_radio.click()
                    await self.page.wait_for_timeout(3000)
                    await self.page.wait_for_load_state('networkidle')

                await self.page.screenshot(path="/tmp/cook_county_name_form.png")

                # Fill name fields
                print(f"[4] Filling name: {last_name}, {first_name or ''}")

                last_input = await self.page.query_selector('input[id*="txtLastName"]')
                if last_input:
                    await last_input.fill(last_name or "")

                first_input = await self.page.query_selector('input[id*="txtFirstName"]')
                if first_input:
                    # Use first name if provided, otherwise use common name for broader results
                    name_to_use = first_name or ""
                    await first_input.fill(name_to_use)

            await self.page.screenshot(path="/tmp/cook_county_filled.png")

            # Submit search
            print("[5] Submitting search...")
            submit = await self.page.query_selector(
                'input[type="submit"][value*="Search" i], input[id*="btnSearch"], '
                'button:has-text("Search")'
            )
            if submit:
                await submit.click()
            else:
                await self.page.keyboard.press('Enter')

            await self.page.wait_for_timeout(6000)
            await self.page.screenshot(path="/tmp/cook_county_results.png")
            print("    Screenshot: /tmp/cook_county_results.png")

            # Save HTML for debugging
            html = await self.page.content()
            with open("/tmp/cook_county_results.html", "w") as f:
                f.write(html)

            # Check for no results message
            content = await self.page.evaluate('() => document.body.innerText')
            if 'no results' in content.lower() or 'no records' in content.lower():
                print("    No results found")
                return []

            if 'captcha' in content.lower() or 'robot' in content.lower():
                print("    CAPTCHA detected!")
                return []

            # Extract results
            print("[6] Extracting results...")
            results = await self._extract_results()

            print(f"    Found {len(results)} results")
            return results[:limit]

        except Exception as e:
            print(f"    Error during search: {e}")
            import traceback
            traceback.print_exc()
            await self.page.screenshot(path="/tmp/cook_county_error.png")
            return []

    async def _extract_results(self) -> List[Dict[str, Any]]:
        """Extract case results from the results page."""
        results = await self.page.evaluate('''() => {
            const cases = [];

            // Look for table rows in results
            document.querySelectorAll('table tbody tr, .search-result, .case-row').forEach(row => {
                const cells = row.querySelectorAll('td, .cell');
                const rowText = row.innerText?.trim() || '';

                if (cells.length >= 2) {
                    const link = row.querySelector('a');

                    // Cook County case numbers are like "2024L001234"
                    const caseMatch = rowText.match(/\\d{4}[A-Z]{1,2}\\d{4,}/i);

                    cases.push({
                        case_number: caseMatch ? caseMatch[0] : (cells[0]?.innerText?.trim() || ''),
                        party_name: cells[1]?.innerText?.trim() || '',
                        case_type: cells[2]?.innerText?.trim() || '',
                        filing_date: cells[3]?.innerText?.trim() || '',
                        status: cells[4]?.innerText?.trim() || '',
                        url: link?.href || '',
                        raw: rowText.substring(0, 300)
                    });
                }
            });

            // Also try grid view
            if (cases.length === 0) {
                document.querySelectorAll('[class*="grid"] tr, .datagrid tr').forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        cases.push({
                            cells: Array.from(cells).map(c => c.innerText?.trim() || ''),
                            raw: row.innerText?.trim().substring(0, 300)
                        });
                    }
                });
            }

            return cases;
        }''')

        # Parse results
        parsed = []
        for r in results:
            if r.get('cells'):
                cells = r['cells']
                case = {
                    'case_number': cells[0] if cells else '',
                    'party_name': cells[1] if len(cells) > 1 else '',
                    'case_type': cells[2] if len(cells) > 2 else '',
                    'filing_date': cells[3] if len(cells) > 3 else '',
                    'status': cells[4] if len(cells) > 4 else '',
                    'raw': r.get('raw', '')
                }
            else:
                case = {
                    'case_number': r.get('case_number', ''),
                    'party_name': r.get('party_name', ''),
                    'case_type': r.get('case_type', ''),
                    'filing_date': r.get('filing_date', ''),
                    'status': r.get('status', ''),
                    'url': r.get('url', ''),
                    'raw': r.get('raw', '')
                }

            case['court'] = 'Cook County Circuit Court'
            case['state'] = 'IL'
            case['source_url'] = self.BASE_URL

            parsed.append(case)

        return parsed

    async def search(self, **kwargs) -> List[Dict[str, Any]]:
        """Alias for search_civil."""
        return await self.search_civil(**kwargs)


async def main():
    parser = argparse.ArgumentParser(description='Illinois Cook County Court Search')
    parser.add_argument('--name', '-n', dest='last_name', help='Party last name')
    parser.add_argument('--first', '-f', dest='first_name', help='Party first name')
    parser.add_argument('--case-number', '-c', help='Case number to search')
    parser.add_argument('--limit', '-l', type=int, default=25, help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True, help='Headless mode')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    # Default search
    if not args.last_name and not args.case_number:
        args.last_name = "Smith"

    print("=" * 70)
    print("ILLINOIS COOK COUNTY (CHICAGO) COURT SEARCH")
    print("=" * 70)
    print(f"Search parameters:")
    print(f"  Last name: {args.last_name or 'N/A'}")
    print(f"  First name: {args.first_name or 'N/A'}")
    print(f"  Case number: {args.case_number or 'N/A'}")
    print(f"  Limit: {args.limit}")
    print("=" * 70)

    async with IllinoisCookCountyScraper(headless=args.headless) as scraper:
        results = await scraper.search_civil(
            last_name=args.last_name,
            first_name=args.first_name,
            case_number=args.case_number,
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
                if r.get('filing_date'):
                    print(f"    Filed: {r['filing_date']}")
                if r.get('status'):
                    print(f"    Status: {r['status']}")
                if r.get('raw') and not r.get('case_number'):
                    print(f"    Raw: {r['raw'][:80]}...")

            if args.output:
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                print(f"\nResults saved to {args.output}")
        else:
            print("\nNo results found. Check /tmp/cook_county_*.png for screenshots.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
