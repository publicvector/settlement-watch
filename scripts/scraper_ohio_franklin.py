#!/usr/bin/env python3
"""
Ohio Franklin County (Columbus) Court Search Scraper

This scraper accesses Franklin County Clerk of Courts Case Information Online.
URL: https://fcdcfcjs.co.franklin.oh.us/CaseInformationOnline/

Features:
- No CAPTCHA required
- No login required for basic searches
- Supports party name and case number searches
- Covers General Division (Civil, Criminal), Domestic Relations, and Appeals

Usage:
    python scraper_ohio_franklin.py --name "Smith"
    python scraper_ohio_franklin.py --name "Smith" --first "John"
    python scraper_ohio_franklin.py --case-number "24 CV 001234"
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


class OhioFranklinScraper:
    """Scraper for Franklin County (Columbus) Ohio Court Records."""

    BASE_URL = "https://fcdcfcjs.co.franklin.oh.us/CaseInformationOnline/"

    # Case type codes
    CASE_TYPES = {
        "AP": "Appeals",
        "CV": "Civil",
        "EX": "Executions",
        "CR": "Criminal",
        "DR": "Domestic Relations",
        "JG": "Judgments",
        "PR": "Probate"
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

    async def _accept_disclaimer(self):
        """Accept the terms of service disclaimer if present."""
        content = await self.page.evaluate('() => document.body.innerText.substring(0, 2000)')
        if 'accept' in content.lower() or 'agree' in content.lower() or 'disclaimer' in content.lower():
            accept_btn = await self.page.query_selector(
                'input[type="submit"][value*="Accept" i], '
                'button:has-text("Accept"), button:has-text("Agree"), '
                'input[type="button"][value*="Accept" i], a:has-text("Accept")'
            )
            if accept_btn:
                await accept_btn.click()
                await self.page.wait_for_timeout(2000)
                return True
        return False

    async def search(
        self,
        last_name: Optional[str] = None,
        first_name: Optional[str] = None,
        middle_initial: Optional[str] = None,
        case_number: Optional[str] = None,
        case_type: Optional[str] = None,  # "CV", "CR", "DR", etc.
        person_type: str = "Party",  # "Party" or "Attorney"
        results_per_page: int = 25,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search Franklin County court records.

        Args:
            last_name: Party last name (required for name search)
            first_name: Party first name (optional)
            middle_initial: Middle initial (optional)
            case_number: Case number to search (alternative to name)
            case_type: Filter by case type code (CV, CR, DR, etc.)
            person_type: Search for "Party" or "Attorney"
            results_per_page: Results per page (25, 50, 100)
            limit: Maximum total results to return

        Returns:
            List of case dictionaries
        """
        try:
            print(f"[1] Loading Franklin County Case Information Online...")
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(3000)

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Accept disclaimer
            if await self._accept_disclaimer():
                print("    Accepted disclaimer")

            # Fill search form
            if last_name:
                print(f"[2] Filling name search: {last_name}, {first_name or ''}")

                last_input = await self.page.query_selector('input[name="lname"]')
                if last_input:
                    await last_input.fill(last_name)

                if first_name:
                    first_input = await self.page.query_selector('input[name="fname"]')
                    if first_input:
                        await first_input.fill(first_name)

                if middle_initial:
                    mi_input = await self.page.query_selector('input[name="mint"]')
                    if mi_input:
                        await mi_input.fill(middle_initial)

            elif case_number:
                print(f"[2] Filling case number: {case_number}")
                # Parse case number format: "YY TT NNNNNN" or "YYTTNNNNN"
                parts = case_number.upper().replace("-", " ").split()
                if len(parts) >= 2:
                    year_input = await self.page.query_selector('input[name="caseYear"]')
                    if year_input:
                        await year_input.fill(parts[0])

                    # Case type dropdown
                    type_select = await self.page.query_selector('select[name="caseType"]')
                    if type_select and len(parts) >= 2:
                        try:
                            await type_select.select_option(value=parts[1])
                        except:
                            pass

                    if len(parts) >= 3:
                        seq_input = await self.page.query_selector('input[name="caseSeq"]')
                        if seq_input:
                            await seq_input.fill(parts[2])

            # Set case type filter if specified
            if case_type and last_name:
                type_select = await self.page.query_selector('select[name="selType"]')
                if type_select:
                    try:
                        type_name = self.CASE_TYPES.get(case_type.upper(), case_type)
                        await type_select.select_option(label=type_name)
                        print(f"    Filtered by case type: {type_name}")
                    except:
                        pass

            # Set person type
            if person_type:
                person_select = await self.page.query_selector('select[name="personType"]')
                if person_select:
                    try:
                        await person_select.select_option(label=person_type)
                    except:
                        pass

            # Set results per page
            recs_select = await self.page.query_selector('select[name="recs"]')
            if recs_select:
                try:
                    await recs_select.select_option(value=str(results_per_page))
                except:
                    pass

            # Submit search
            print("[3] Submitting search...")
            submit = await self.page.query_selector('input[type="submit"], input[value*="Search" i]')
            if submit:
                await submit.click()
            else:
                await self.page.keyboard.press('Enter')

            await self.page.wait_for_timeout(5000)

            # Check for results
            content = await self.page.evaluate('() => document.body.innerText')
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
            await self.page.screenshot(path="/tmp/ohio_franklin_error.png")
            return []

    async def _extract_results(self) -> List[Dict[str, Any]]:
        """Extract case results from the results page."""
        results = await self.page.evaluate('''() => {
            const cases = [];

            // Find case number buttons (submitLink class)
            document.querySelectorAll('input.submitLink[type="submit"]').forEach(btn => {
                const caseNum = btn.value?.trim() || '';
                // Case numbers match pattern: YY TT NNNNNN
                if (/^\\d{2}\\s*[A-Z]{2}\\s*\\d+/.test(caseNum)) {
                    const row = btn.closest('tr');
                    if (row) {
                        const cells = row.querySelectorAll('td');
                        cases.push({
                            case_number: caseNum.trim(),
                            case_type: cells[1]?.textContent?.trim() || '',
                            party_name: cells[2]?.textContent?.trim() || '',
                            itn_number: cells[3]?.textContent?.trim() || '',
                            gender: cells[4]?.textContent?.trim() || '',
                            role: cells[5]?.textContent?.trim() || '',  // P/D
                            dob: cells[6]?.textContent?.trim() || '',
                            description: cells[7]?.textContent?.trim() || '',
                            filed_date: cells[8]?.textContent?.trim() || '',
                            status: cells[9]?.textContent?.trim() || ''
                        });
                    }
                }
            });

            return cases;
        }''')

        # Parse and clean results
        parsed = []
        for r in results:
            case = {
                'case_number': r.get('case_number', ''),
                'case_type': r.get('case_type', ''),
                'party_name': r.get('party_name', ''),
                'description': r.get('description', ''),
                'filed_date': r.get('filed_date', ''),
                'status': r.get('status', ''),
                'role': 'Plaintiff' if r.get('role') == 'P' else 'Defendant' if r.get('role') == 'D' else r.get('role', ''),
                'court': 'Franklin County Common Pleas',
                'state': 'OH',
                'source_url': self.BASE_URL
            }

            # Add optional fields if present
            if r.get('dob'):
                case['dob'] = r['dob']
            if r.get('itn_number'):
                case['itn_number'] = r['itn_number']

            parsed.append(case)

        return parsed

    async def get_case_details(self, case_number: str) -> Dict[str, Any]:
        """Get detailed information for a specific case."""
        results = await self.search(case_number=case_number, limit=1)
        if results:
            return results[0]
        return {}


async def main():
    parser = argparse.ArgumentParser(description='Ohio Franklin County Court Search')
    parser.add_argument('--name', '-n', dest='last_name', help='Party last name')
    parser.add_argument('--first', '-f', dest='first_name', help='Party first name')
    parser.add_argument('--case-number', '-c', help='Case number to search')
    parser.add_argument('--case-type', '-t', choices=['CV', 'CR', 'DR', 'AP', 'JG', 'EX', 'PR'],
                        help='Case type filter')
    parser.add_argument('--limit', '-l', type=int, default=25, help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True,
                        help='Run in headless mode')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    # Default search
    if not args.last_name and not args.case_number:
        args.last_name = "Smith"

    print("=" * 70)
    print("OHIO FRANKLIN COUNTY COURT SEARCH")
    print("=" * 70)
    print(f"Search parameters:")
    print(f"  Last name: {args.last_name or 'N/A'}")
    print(f"  First name: {args.first_name or 'N/A'}")
    print(f"  Case number: {args.case_number or 'N/A'}")
    print(f"  Case type: {args.case_type or 'All'}")
    print(f"  Limit: {args.limit}")
    print("=" * 70)

    async with OhioFranklinScraper(headless=args.headless) as scraper:
        results = await scraper.search(
            last_name=args.last_name,
            first_name=args.first_name,
            case_number=args.case_number,
            case_type=args.case_type,
            limit=args.limit
        )

        if results:
            print(f"\nFound {len(results)} results:")
            for i, r in enumerate(results[:15], 1):
                print(f"\n[{i}] {r.get('case_number', 'N/A')}")
                print(f"    Type: {r.get('case_type', 'N/A')}")
                print(f"    Party: {r.get('party_name', 'N/A')}")
                print(f"    Description: {r.get('description', 'N/A')[:50]}")
                print(f"    Filed: {r.get('filed_date', 'N/A')}")
                print(f"    Status: {r.get('status', 'N/A')}")

            if args.output:
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                print(f"\nResults saved to {args.output}")
        else:
            print("\nNo results found.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
