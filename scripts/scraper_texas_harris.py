#!/usr/bin/env python3
"""
Texas Harris County (Houston) District Clerk Scraper

This scraper accesses the Harris County District Clerk's public records.
URL: https://www.hcdistrictclerk.com/edocs/public/search.aspx

Features:
- Civil and criminal case search
- Party name search with multiple search types
- Historical records search
- Docket search

Note: Requires CAPTCHA completion for all searches.
The site blocks automated programs with CAPTCHA verification.

Usage:
    python scraper_texas_harris.py --name "Smith"
    python scraper_texas_harris.py --case-number "202412345"

IMPORTANT: Due to CAPTCHA requirements, this scraper requires
manual CAPTCHA solving or integration with a CAPTCHA solving service.
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


class TexasHarrisCountyScraper:
    """Scraper for Harris County (Houston) Texas Court Records."""

    BASE_URL = "https://www.hcdistrictclerk.com/edocs/public/search.aspx"

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
        """Check if CAPTCHA is present and needs solving."""
        content = await self.page.content()
        captcha_present = (
            'captcha' in content.lower() or
            'verification' in content.lower() or
            'automated program' in content.lower()
        )
        return captcha_present

    async def search(
        self,
        party_name: Optional[str] = None,
        case_number: Optional[str] = None,
        search_type: str = "starts_with",  # "starts_with" or "contains"
        court_type: str = "Civil",  # "Civil", "Criminal", "Family"
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search Harris County court records.

        Args:
            party_name: Party name to search
            case_number: Case number to search
            search_type: "starts_with" or "contains"
            court_type: "Civil", "Criminal", or "Family"
            limit: Maximum results to return

        Returns:
            List of case dictionaries

        Note: This search requires manual CAPTCHA solving.
        """
        try:
            print(f"[1] Loading Harris County public search...")
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(4000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            await self.page.screenshot(path="/tmp/harris_county_form.png")
            print("    Screenshot: /tmp/harris_county_form.png")

            # Check for login requirement
            content = await self.page.evaluate('() => document.body.innerText')
            if 'login' in content.lower() and 'required' in content.lower():
                print("    WARNING: Login appears to be required for detailed searches")

            # Check for CAPTCHA
            if await self._check_captcha():
                print("    WARNING: CAPTCHA verification required")
                print("    This site requires manual CAPTCHA solving for automated searches")

            # Fill search form
            if party_name:
                print(f"[2] Filling party name: {party_name}")

                # Find party name input
                party_input = await self.page.query_selector(
                    'input[name*="txtPartyName"], input[id*="PartyName"]'
                )
                if party_input:
                    await party_input.fill(party_name)

                # Set search type
                search_select = await self.page.query_selector(
                    'select[name*="SearchType"], select[id*="SearchType"]'
                )
                if search_select:
                    try:
                        if search_type == "contains":
                            await search_select.select_option(label="Contains")
                        else:
                            await search_select.select_option(label="Starts With")
                    except:
                        pass

                # Set court type
                court_select = await self.page.query_selector(
                    'select[name*="CourtType"], select[id*="CourtType"]'
                )
                if court_select:
                    try:
                        await court_select.select_option(label=court_type)
                    except:
                        pass

            elif case_number:
                print(f"[2] Filling case number: {case_number}")
                case_input = await self.page.query_selector(
                    'input[name*="caseNumber"], input[id*="CaseNumber"]'
                )
                if case_input:
                    await case_input.fill(case_number)

            await self.page.screenshot(path="/tmp/harris_county_filled.png")

            # Check for CAPTCHA again
            if await self._check_captcha():
                print("\n" + "=" * 50)
                print("CAPTCHA REQUIRED")
                print("=" * 50)
                print("The Harris County District Clerk website requires")
                print("CAPTCHA verification for all searches.")
                print("")
                print("Options:")
                print("1. Use headless=False and solve CAPTCHA manually")
                print("2. Integrate with a CAPTCHA solving service")
                print("3. Use the website directly in a browser")
                print("=" * 50 + "\n")

                # If not headless, wait for user to solve CAPTCHA
                if not self.headless:
                    print("Waiting 60 seconds for manual CAPTCHA solving...")
                    await self.page.wait_for_timeout(60000)

            # Submit search
            print("[3] Submitting search...")
            submit = await self.page.query_selector(
                'input[type="submit"][value*="Search" i], '
                'input[type="button"][value*="Search" i], '
                'button:has-text("Search")'
            )
            if submit:
                await submit.click()
            else:
                await self.page.keyboard.press('Enter')

            await self.page.wait_for_timeout(6000)
            await self.page.screenshot(path="/tmp/harris_county_results.png")
            print("    Screenshot: /tmp/harris_county_results.png")

            # Save HTML
            html = await self.page.content()
            with open("/tmp/harris_county_results.html", "w") as f:
                f.write(html)

            # Check result
            content = await self.page.evaluate('() => document.body.innerText')
            if 'captcha' in content.lower() or 'verification' in content.lower():
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
            await self.page.screenshot(path="/tmp/harris_county_error.png")
            return []

    async def _extract_results(self) -> List[Dict[str, Any]]:
        """Extract case results from the results page."""
        results = await self.page.evaluate('''() => {
            const cases = [];

            // Try to find results table
            document.querySelectorAll('table tbody tr, .datagrid tr, .results tr').forEach(row => {
                const cells = row.querySelectorAll('td');
                const rowText = row.innerText?.trim() || '';

                // Skip header rows
                if (row.closest('thead')) return;
                if (cells.length < 2) return;

                // Harris County case numbers are usually numeric
                const caseMatch = rowText.match(/\\d{9,}/);

                cases.push({
                    case_number: caseMatch ? caseMatch[0] : (cells[0]?.innerText?.trim() || ''),
                    party_name: cells[1]?.innerText?.trim() || '',
                    case_type: cells[2]?.innerText?.trim() || '',
                    filed_date: cells[3]?.innerText?.trim() || '',
                    court: cells[4]?.innerText?.trim() || '',
                    status: cells[5]?.innerText?.trim() || '',
                    raw: rowText.substring(0, 300)
                });
            });

            // Try alternate selectors
            if (cases.length === 0) {
                document.querySelectorAll('[class*="result"], [class*="case"]').forEach(item => {
                    const text = item.innerText?.trim();
                    if (text && text.length > 20) {
                        cases.push({
                            raw: text.substring(0, 400)
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
                'filed_date': r.get('filed_date', ''),
                'court': r.get('court', 'Harris County District Court'),
                'status': r.get('status', ''),
                'raw': r.get('raw', ''),
                'state': 'TX',
                'county': 'Harris',
                'source_url': self.BASE_URL
            }
            parsed.append(case)

        return parsed


async def main():
    parser = argparse.ArgumentParser(description='Texas Harris County Court Search')
    parser.add_argument('--name', '-n', dest='party_name', help='Party name to search')
    parser.add_argument('--case-number', '-c', help='Case number to search')
    parser.add_argument('--search-type', choices=['starts_with', 'contains'],
                        default='starts_with', help='Name search type')
    parser.add_argument('--court-type', choices=['Civil', 'Criminal', 'Family'],
                        default='Civil', help='Court type filter')
    parser.add_argument('--limit', '-l', type=int, default=25, help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True, help='Headless mode')
    parser.add_argument('--no-headless', action='store_false', dest='headless',
                        help='Run with visible browser (for manual CAPTCHA solving)')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    # Default search
    if not args.party_name and not args.case_number:
        args.party_name = "Smith"

    print("=" * 70)
    print("TEXAS HARRIS COUNTY (HOUSTON) COURT SEARCH")
    print("=" * 70)
    print(f"Search parameters:")
    print(f"  Party name: {args.party_name or 'N/A'}")
    print(f"  Case number: {args.case_number or 'N/A'}")
    print(f"  Search type: {args.search_type}")
    print(f"  Court type: {args.court_type}")
    print(f"  Limit: {args.limit}")
    print(f"  Headless: {args.headless}")
    print("")
    print("NOTE: This site requires CAPTCHA verification.")
    print("      Use --no-headless to solve CAPTCHA manually.")
    print("=" * 70)

    async with TexasHarrisCountyScraper(headless=args.headless) as scraper:
        results = await scraper.search(
            party_name=args.party_name,
            case_number=args.case_number,
            search_type=args.search_type,
            court_type=args.court_type,
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
            print("If CAPTCHA was the issue, try running with --no-headless")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
