#!/usr/bin/env python3
"""
Florida Orange County (Orlando) Clerk of Courts Scraper

This scraper accesses the Orange County Clerk of Courts case search.
URL: https://myeclerk.myorangeclerk.com/Cases/Search

Features:
- Civil, criminal, family, and traffic case search
- Party name and case number search
- 100+ case types available

Note: CAPTCHA is required for anonymous users.
Registered users can bypass CAPTCHA.

Usage:
    python scraper_florida_orange.py --name "Smith"
    python scraper_florida_orange.py --case-number "2024-CA-001234"
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


class FloridaOrangeCountyScraper:
    """Scraper for Orange County (Orlando) Florida Court Records."""

    BASE_URL = "https://myeclerk.myorangeclerk.com/Cases/Search"

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

    async def _check_captcha(self) -> bool:
        """Check if CAPTCHA is present."""
        content = await self.page.content()
        return (
            'captcha' in content.lower() or
            'recaptcha' in content.lower() or
            'verification' in content.lower()
        )

    async def get_case_types(self) -> List[Dict[str, str]]:
        """Get available case types from the dropdown."""
        try:
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            case_types = await self.page.evaluate('''() => {
                const types = [];
                const select = document.querySelector('select[id*="CaseType"], select[name*="CaseType"]');
                if (select) {
                    for (let opt of select.options) {
                        if (opt.value) {
                            types.push({ value: opt.value, label: opt.text.trim() });
                        }
                    }
                }
                return types;
            }''')

            return case_types
        except:
            return []

    async def search(
        self,
        last_name: Optional[str] = None,
        first_name: Optional[str] = None,
        business_name: Optional[str] = None,
        case_number: Optional[str] = None,
        case_type: Optional[str] = None,
        date_from: Optional[str] = None,  # MM/DD/YYYY
        date_to: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search Orange County court records.

        Args:
            last_name: Party last name
            first_name: Party first name
            business_name: Business/organization name
            case_number: Case number (format: YYYY-XX-NNNNNN)
            case_type: Case type code
            date_from: Filing date from
            date_to: Filing date to
            limit: Maximum results to return

        Returns:
            List of case dictionaries

        Note: CAPTCHA is required for anonymous users.
        """
        try:
            print(f"[1] Loading Orange County case search...")
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            await self.page.screenshot(path="/tmp/orange_county_form.png")
            print("    Screenshot: /tmp/orange_county_form.png")

            # Check for CAPTCHA
            if await self._check_captcha():
                print("    WARNING: CAPTCHA verification required for anonymous users")
                print("    Registered users can bypass CAPTCHA")

            # Fill search form
            if last_name:
                print(f"[2] Filling party name: {last_name}, {first_name or ''}")

                last_input = await self.page.query_selector(
                    'input[id="LastName"], input[name="LastName"]'
                )
                if last_input:
                    await last_input.fill(last_name)

                if first_name:
                    first_input = await self.page.query_selector(
                        'input[id="FirstName"], input[name="FirstName"]'
                    )
                    if first_input:
                        await first_input.fill(first_name)

            elif business_name:
                print(f"[2] Filling business name: {business_name}")
                business_input = await self.page.query_selector(
                    'input[id="BusinessName"], input[name="BusinessName"]'
                )
                if business_input:
                    await business_input.fill(business_name)

            elif case_number:
                print(f"[2] Filling case number: {case_number}")
                case_input = await self.page.query_selector(
                    'input[id*="CaseNumber"], input[name*="CaseNumber"]'
                )
                if case_input:
                    await case_input.fill(case_number)

            # Set case type filter if provided
            if case_type:
                type_select = await self.page.query_selector(
                    'select[id*="CaseType"], select[name*="CaseType"]'
                )
                if type_select:
                    try:
                        await type_select.select_option(value=case_type)
                        print(f"    Case type: {case_type}")
                    except:
                        pass

            # Set date range if provided
            if date_from:
                from_input = await self.page.query_selector(
                    'input[id*="DateFrom"], input[name*="DateFrom"]'
                )
                if from_input:
                    await from_input.fill(date_from)

            if date_to:
                to_input = await self.page.query_selector(
                    'input[id*="DateTo"], input[name*="DateTo"]'
                )
                if to_input:
                    await to_input.fill(date_to)

            await self.page.screenshot(path="/tmp/orange_county_filled.png")

            # Check for CAPTCHA before submit
            captcha_present = await self._check_captcha()
            if captcha_present:
                print("\n" + "=" * 50)
                print("CAPTCHA REQUIRED")
                print("=" * 50)
                print("Orange County requires CAPTCHA for anonymous users.")
                print("")
                print("Options:")
                print("1. Use --no-headless to solve CAPTCHA manually")
                print("2. Create an account on myeclerk.myorangeclerk.com")
                print("3. Integrate with a CAPTCHA solving service")
                print("=" * 50 + "\n")

                if not self.headless:
                    print("Waiting 60 seconds for manual CAPTCHA solving...")
                    await self.page.wait_for_timeout(60000)

            # Submit search
            print("[3] Submitting search...")
            submit = await self.page.query_selector(
                'button[type="submit"], input[type="submit"], '
                'button:has-text("Search"), a:has-text("Search")'
            )
            if submit:
                await submit.click()
            else:
                await self.page.keyboard.press('Enter')

            await self.page.wait_for_timeout(6000)
            await self.page.screenshot(path="/tmp/orange_county_results.png")
            print("    Screenshot: /tmp/orange_county_results.png")

            # Save HTML
            html = await self.page.content()
            with open("/tmp/orange_county_results.html", "w") as f:
                f.write(html)

            # Check for errors
            content = await self.page.evaluate('() => document.body.innerText')

            if 'captcha' in content.lower():
                print("    CAPTCHA still blocking - search not completed")
                return []

            if 'no records' in content.lower() or 'no results' in content.lower():
                print("    No results found")
                return []

            # Extract results
            print("[4] Extracting results...")
            results = await self._extract_results()

            print(f"    Found {len(results)} results")
            return results[:limit]

        except Exception as e:
            print(f"    Error during search: {e}")
            import traceback
            traceback.print_exc()
            await self.page.screenshot(path="/tmp/orange_county_error.png")
            return []

    async def _extract_results(self) -> List[Dict[str, Any]]:
        """Extract case results from the results page."""
        results = await self.page.evaluate('''() => {
            const cases = [];

            // Try table rows
            document.querySelectorAll('table tbody tr, .search-result, [class*="result"]').forEach(row => {
                const cells = row.querySelectorAll('td');
                const rowText = row.innerText?.trim() || '';
                const link = row.querySelector('a');

                if (cells.length >= 2) {
                    // Orange County case numbers: YYYY-XX-NNNNNN
                    const caseMatch = rowText.match(/\\d{4}-[A-Z]{2}-\\d{4,}/i);

                    cases.push({
                        case_number: caseMatch ? caseMatch[0] : (cells[0]?.innerText?.trim() || ''),
                        party_name: cells[1]?.innerText?.trim() || '',
                        case_type: cells[2]?.innerText?.trim() || '',
                        status: cells[3]?.innerText?.trim() || '',
                        filed_date: cells[4]?.innerText?.trim() || '',
                        url: link?.href || '',
                        raw: rowText.substring(0, 300).replace(/\\s+/g, ' ')
                    });
                }
            });

            // Try card/div based results
            if (cases.length === 0) {
                document.querySelectorAll('.case-item, .case-row, [data-case]').forEach(item => {
                    const text = item.innerText?.trim();
                    const link = item.querySelector('a');
                    const caseMatch = text.match(/\\d{4}-[A-Z]{2}-\\d{4,}/i);

                    if (caseMatch || (text && text.length > 20)) {
                        cases.push({
                            case_number: caseMatch ? caseMatch[0] : '',
                            url: link?.href || '',
                            raw: text.substring(0, 300).replace(/\\s+/g, ' ')
                        });
                    }
                });
            }

            return cases;
        }''')

        # Parse results
        parsed = []
        for r in results:
            case = {
                'case_number': r.get('case_number', ''),
                'party_name': r.get('party_name', ''),
                'case_type': r.get('case_type', ''),
                'status': r.get('status', ''),
                'filed_date': r.get('filed_date', ''),
                'url': r.get('url', ''),
                'raw': r.get('raw', ''),
                'court': 'Orange County Circuit Court',
                'state': 'FL',
                'county': 'Orange',
                'source_url': self.BASE_URL
            }
            parsed.append(case)

        return parsed


async def main():
    parser = argparse.ArgumentParser(description='Florida Orange County Court Search')
    parser.add_argument('--name', '-n', dest='last_name', help='Party last name')
    parser.add_argument('--first', '-f', dest='first_name', help='Party first name')
    parser.add_argument('--business', '-b', help='Business name')
    parser.add_argument('--case-number', '-c', help='Case number')
    parser.add_argument('--case-type', '-t', help='Case type code')
    parser.add_argument('--list-types', action='store_true', help='List available case types')
    parser.add_argument('--limit', '-l', type=int, default=25, help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True, help='Headless mode')
    parser.add_argument('--no-headless', action='store_false', dest='headless',
                        help='Run with visible browser (for manual CAPTCHA)')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    print("=" * 70)
    print("FLORIDA ORANGE COUNTY (ORLANDO) COURT SEARCH")
    print("=" * 70)

    async with FloridaOrangeCountyScraper(headless=args.headless) as scraper:
        # List case types if requested
        if args.list_types:
            print("Fetching available case types...")
            types = await scraper.get_case_types()
            if types:
                print(f"\nFound {len(types)} case types:")
                for t in types[:30]:
                    print(f"  {t['value']}: {t['label'][:50]}")
                if len(types) > 30:
                    print(f"  ... and {len(types) - 30} more")
            return

        # Default search
        if not args.last_name and not args.business and not args.case_number:
            args.last_name = "Smith"

        print(f"Search parameters:")
        print(f"  Last name: {args.last_name or 'N/A'}")
        print(f"  First name: {args.first_name or 'N/A'}")
        print(f"  Business: {args.business or 'N/A'}")
        print(f"  Case number: {args.case_number or 'N/A'}")
        print(f"  Limit: {args.limit}")
        print(f"  Headless: {args.headless}")
        print("")
        print("NOTE: CAPTCHA required for anonymous users.")
        print("      Use --no-headless to solve manually.")
        print("=" * 70)

        results = await scraper.search(
            last_name=args.last_name,
            first_name=args.first_name,
            business_name=args.business,
            case_number=args.case_number,
            case_type=args.case_type,
            limit=args.limit
        )

        if results:
            print(f"\nFound {len(results)} results:")
            for i, r in enumerate(results[:15], 1):
                print(f"\n[{i}] {r.get('case_number', 'N/A')}")
                if r.get('party_name'):
                    print(f"    Party: {r['party_name'][:50]}")
                if r.get('case_type'):
                    print(f"    Type: {r['case_type']}")
                if r.get('filed_date'):
                    print(f"    Filed: {r['filed_date']}")
                if r.get('status'):
                    print(f"    Status: {r['status']}")

            if args.output:
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                print(f"\nResults saved to {args.output}")
        else:
            print("\nNo results found.")
            print("If CAPTCHA was the issue, try --no-headless")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
