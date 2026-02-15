#!/usr/bin/env python3
"""
Alaska CourtView Case Scraper
https://records.courts.alaska.gov/

Working scraper for Alaska court cases. Supports:
- Name search (Last Name, First Name required)
- Case number search
- Ticket/Citation search
- Multiple case types and party types

No CAPTCHA or login required.
"""
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page, Browser
from playwright_stealth import Stealth


class AlaskaScraper:
    """Scraper for Alaska CourtView Public Access."""

    BASE_URL = "https://records.courts.alaska.gov"

    # Case type options
    CASE_TYPES = [
        "All Cases",
        "Appeal from Administrative Agency",
        "Civil District Court",
        "Civil Protective Order",
        "Civil Superior Ct",
        "Criminal",
        "Domestic Relations",
        "Eviction Superior Court",
        "Felony DUI",
        "Minor Offense",
        "Petition For Review",
        "Probate",
        "Small Claims",
    ]

    # Case status options
    CASE_STATUSES = ["All Statuses", "Closed", "Open", "Reopened"]

    # Party type options
    PARTY_TYPES = [
        "All Party Types",
        "Absent Spouse", "Amicus Curiae", "Appellant", "Appellee",
        "Applicant", "Applicant/Petitioner", "Assignee", "Claimant",
        "Complainant", "Cross Appellee", "Cross Appellant", "Debtor",
        "Defendant", "Intervenor", "Minor", "Movant",
        "Other Interested Party", "Party", "Petitioner", "Plaintiff",
        "Plaintiff in Error", "Respondent", "Victim", "Witness",
    ]

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._playwright = None

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
        context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        )
        self.page = await context.new_page()
        self.page.set_default_timeout(60000)

        # Apply stealth
        stealth = Stealth()
        await stealth.apply_stealth_async(self.page)

    async def close(self):
        """Close browser."""
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _navigate_to_search(self):
        """Navigate to the search page."""
        await self.page.goto(self.BASE_URL, timeout=60000)
        await self.page.wait_for_load_state('networkidle')
        await self.page.wait_for_timeout(2000)

        # Click "Search Cases" button
        await self.page.click('a.anchorButton:has-text("Search Cases")')
        await self.page.wait_for_load_state('networkidle')
        await self.page.wait_for_timeout(2000)

    async def search_by_name(
        self,
        last_name: str,
        first_name: str,
        middle_name: str = "",
        case_type: str = "All Cases",
        case_status: str = "All Statuses",
        party_type: str = "All Party Types",
        limit: int = 25
    ) -> List[Dict[str, Any]]:
        """
        Search cases by party name.

        Args:
            last_name: Party's last name (required)
            first_name: Party's first name (required)
            middle_name: Party's middle name (optional)
            case_type: Type of cases to search
            case_status: Case status filter
            party_type: Party type filter
            limit: Maximum results to return

        Returns:
            List of case dictionaries
        """
        await self._navigate_to_search()

        # Click "Name" tab
        await self.page.click('text=Name')
        await self.page.wait_for_timeout(1500)

        # Fill name fields using exact attribute names
        await self.page.fill('input[name="lastName"]', last_name)
        await self.page.fill('input[name="firstName"]', first_name)

        if middle_name:
            await self.page.fill('input[name="middleName"]', middle_name)

        # Select case type if specified
        if case_type and case_type != "All Cases":
            await self._select_multi_option("Case Type", case_type)

        # Select case status if specified
        if case_status and case_status != "All Statuses":
            await self._select_multi_option("Case Status", case_status)

        # Select party type if specified
        if party_type and party_type != "All Party Types":
            await self._select_multi_option("Party Type", party_type)

        # Submit search
        await self.page.click('input[type="submit"][value="Search"], button:has-text("Search")')
        await self.page.wait_for_load_state('networkidle')
        await self.page.wait_for_timeout(3000)

        return await self._extract_search_results(limit)

    async def search_by_case_number(
        self,
        case_number: str
    ) -> List[Dict[str, Any]]:
        """
        Search by case number.

        Args:
            case_number: The case number (e.g., "3AN-21-12345CI")

        Returns:
            List of matching cases
        """
        await self._navigate_to_search()

        # Stay on "Case Number" tab (default)
        # Fill case number field
        case_input = await self.page.query_selector('input[id*="caseNumber" i], input[name*="caseNumber" i]')
        if case_input:
            await case_input.fill(case_number)

        # Submit search
        await self.page.click('input[type="submit"][value="Search"], button:has-text("Search")')
        await self.page.wait_for_load_state('networkidle')
        await self.page.wait_for_timeout(3000)

        return await self._extract_search_results(limit=10)

    async def search_by_ticket(
        self,
        ticket_number: str
    ) -> List[Dict[str, Any]]:
        """
        Search by ticket/citation number.

        Args:
            ticket_number: The ticket or citation number

        Returns:
            List of matching cases
        """
        await self._navigate_to_search()

        # Click "Ticket/Citation #" tab
        await self.page.click('text=Ticket/Citation #')
        await self.page.wait_for_timeout(1500)

        # Fill ticket number
        ticket_input = await self.page.query_selector('input[id*="ticket" i], input[name*="ticket" i]')
        if ticket_input:
            await ticket_input.fill(ticket_number)

        # Submit search
        await self.page.click('input[type="submit"][value="Search"], button:has-text("Search")')
        await self.page.wait_for_load_state('networkidle')
        await self.page.wait_for_timeout(3000)

        return await self._extract_search_results(limit=10)

    async def _select_multi_option(self, list_label: str, option_text: str):
        """Select an option from a multi-select list."""
        # Find and click the option in the listbox
        option = await self.page.query_selector(f'option:has-text("{option_text}")')
        if option:
            await option.click()

    async def _extract_search_results(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Extract case results from search results page."""
        import re

        # Get page content
        content = await self.page.evaluate('document.body.innerText')

        # Check for "no results" message
        if 'no cases found' in content.lower() or 'no results' in content.lower():
            return []

        # Check result count
        count_match = re.search(r'Returning (\d+) of (\d+) records', content)
        if count_match:
            print(f"    Found {count_match.group(2)} total records")

        # Results are tab-delimited in the page text
        # Parse them from the text content
        results = []
        lines = content.split('\n')

        # Find lines that look like case data (start with case number pattern)
        case_pattern = r'^(\d[A-Z]{2}-\d{2,4}-\d+[A-Z]*|\d[A-Z]{2}-[A-Z]\d{2}-[A-Z]?\d+)'

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Check if line starts with a case number pattern
            match = re.match(case_pattern, line)
            if match:
                # Split by tabs
                parts = line.split('\t')
                if len(parts) >= 4:
                    case_data = {
                        'case_number': parts[0].strip(),
                        'case_type': parts[1].strip() if len(parts) > 1 else '',
                        'file_date': parts[2].strip() if len(parts) > 2 else '',
                        'party_name': parts[3].strip() if len(parts) > 3 else '',
                        'party_type': parts[4].strip() if len(parts) > 4 else '',
                        'dob': parts[5].strip() if len(parts) > 5 else '',
                        'case_status': parts[6].strip() if len(parts) > 6 else '',
                        'ticket_number': parts[8].strip() if len(parts) > 8 else '',
                        'state': 'AK'
                    }
                    results.append(case_data)

                    if len(results) >= limit:
                        break

        return results

    async def get_case_detail(self, case_number: str) -> Dict[str, Any]:
        """
        Get detailed case information including docket entries.

        Args:
            case_number: The case number to retrieve

        Returns:
            Dictionary with detailed case information and docket entries
        """
        # Search for the case first
        results = await self.search_by_case_number(case_number)
        if not results:
            return {'error': 'Case not found'}

        case = results[0]

        # Navigate to case detail page
        # Try clicking on the case number link in results
        try:
            await self.page.click(f'a:has-text("{case_number}")')
            await self.page.wait_for_load_state('networkidle')
            await self.page.wait_for_timeout(2000)
        except:
            # If click fails, try direct URL construction
            if case.get('detail_url'):
                await self.page.goto(case['detail_url'], timeout=60000)
                await self.page.wait_for_load_state('networkidle')
                await self.page.wait_for_timeout(2000)

        # Extract detailed info and docket entries from case page
        detail = await self.page.evaluate('''() => {
            const detail = {};
            const content = document.body.innerText;

            // Extract common fields
            const fields = [
                'Case Number', 'Case Type', 'File Date', 'Case Status',
                'Judge', 'Court Location', 'Disposition', 'Disposition Date'
            ];

            for (const field of fields) {
                const regex = new RegExp(field + '[:\\\\s]+([^\\\\n]+)', 'i');
                const match = content.match(regex);
                if (match) {
                    const key = field.toLowerCase().replace(/\\\\s+/g, '_');
                    detail[key] = match[1].trim();
                }
            }

            // Extract parties
            const parties = [];
            document.querySelectorAll('table tr').forEach(row => {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 2) {
                    const name = cells[0]?.textContent?.trim();
                    const type = cells[1]?.textContent?.trim();
                    if (name && type && !name.toLowerCase().includes('name')) {
                        parties.push({ name, type });
                    }
                }
            });
            if (parties.length > 0) {
                detail.parties = parties;
            }

            // Extract docket entries
            const docketEntries = [];
            const opinionPatterns = ['opinion', 'decision', 'judgment', 'ruling', 'verdict', 'granted', 'denied', 'dismiss'];
            const orderPatterns = ['order', 'directive', 'mandate', 'injunction', 'sentenc'];

            // Look for docket/event tables
            document.querySelectorAll('table').forEach(table => {
                const headerText = table.textContent?.toLowerCase() || '';
                if (headerText.includes('docket') || headerText.includes('event') || headerText.includes('register of actions')) {
                    table.querySelectorAll('tr').forEach((row, idx) => {
                        const cells = row.querySelectorAll('td');
                        if (cells.length >= 2) {
                            const dateCell = cells[0]?.textContent?.trim() || '';
                            const textCell = cells[1]?.textContent?.trim() || '';

                            // Check if first cell looks like a date
                            if (dateCell.match(/\\d{1,2}[\\/-]\\d{1,2}[\\/-]\\d{2,4}/)) {
                                const entryTextLower = textCell.toLowerCase();
                                const isOpinion = opinionPatterns.some(p => entryTextLower.includes(p));
                                const isOrder = orderPatterns.some(p => entryTextLower.includes(p));

                                docketEntries.push({
                                    entry_number: idx,
                                    entry_date: dateCell,
                                    entry_text: textCell,
                                    is_opinion: isOpinion,
                                    is_order: isOrder,
                                    document_url: ''
                                });
                            }
                        }
                    });
                }
            });

            detail.docket_entries = docketEntries;
            detail.docket_count = docketEntries.length;
            detail.opinions = docketEntries.filter(e => e.is_opinion);
            detail.orders = docketEntries.filter(e => e.is_order);

            return detail;
        }''')

        return {**case, **detail, 'state': 'AK'}

    async def get_recent_filings(self, days: int = 7, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get recent docket entries from cases.

        Args:
            days: Number of days to look back
            limit: Maximum entries to return

        Returns:
            List of recent docket entries
        """
        from datetime import datetime, timedelta

        # Search for common names to get recent cases
        all_entries = []
        names = ["Smith", "Johnson", "Williams", "Brown", "Jones"]

        for name in names[:3]:  # Limit to avoid too many requests
            try:
                results = await self.search_by_name(
                    last_name=name,
                    first_name="",
                    case_type="All Cases",
                    case_status="Open",  # Focus on open cases for recent activity
                    limit=10
                )

                for case in results[:5]:  # Limit detail fetches per name
                    try:
                        detail = await self.get_case_detail(case.get('case_number', ''))
                        for entry in detail.get('docket_entries', []):
                            entry['case_number'] = case.get('case_number')
                            entry['case_title'] = case.get('party_name', '')
                            entry['state'] = 'AK'
                            all_entries.append(entry)
                    except Exception as e:
                        print(f"    Error getting detail: {e}")

                await asyncio.sleep(1)  # Rate limiting

            except Exception as e:
                print(f"  Error searching {name}: {e}")

        # Filter to recent entries and sort
        cutoff = datetime.now() - timedelta(days=days)
        recent = []
        for entry in all_entries:
            try:
                entry_date = datetime.strptime(entry.get('entry_date', ''), '%m/%d/%Y')
                if entry_date >= cutoff:
                    recent.append(entry)
            except:
                pass  # Skip entries with unparseable dates

        recent.sort(key=lambda x: x.get('entry_date', ''), reverse=True)
        return recent[:limit]


async def main():
    """Demo usage of the Alaska scraper."""
    print("=" * 70)
    print("ALASKA COURTVIEW PUBLIC ACCESS SCRAPER")
    print("=" * 70)

    async with AlaskaScraper(headless=True) as scraper:
        # Search by name
        print("\n[1] Searching for 'Smith, John'...")
        results = await scraper.search_by_name(
            last_name="Smith",
            first_name="John",
            case_type="All Cases",
            case_status="All Statuses",
            limit=15
        )

        print(f"\n    Found {len(results)} cases:")
        for i, case in enumerate(results[:10]):
            print(f"\n    [{i+1}] {case['case_number']}")
            print(f"        Type: {case['case_type']}")
            print(f"        Filed: {case['file_date']}")
            print(f"        Party: {case['party_name']}")
            print(f"        Status: {case['case_status']}")
            if case.get('ticket_number'):
                print(f"        Ticket #: {case['ticket_number']}")

        # Search by case number
        if results:
            print(f"\n[2] Searching for specific case: {results[0]['case_number']}...")
            detail_results = await scraper.search_by_case_number(results[0]['case_number'])
            if detail_results:
                print(f"    Found: {detail_results[0]}")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
