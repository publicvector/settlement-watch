#!/usr/bin/env python3
"""
Pennsylvania Unified Judicial System Case Scraper
https://ujsportal.pacourts.us/

Working scraper for PA court cases. Supports:
- Name search (Participant Name)
- Case number search
- Multiple case types (Criminal, Civil, etc.)
- Docket sheet PDF download and parsing

No CAPTCHA or login required.
"""
import asyncio
import os
import re
import subprocess
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright, Page, Browser
from playwright_stealth import Stealth


class PennsylvaniaScraper:
    """Scraper for Pennsylvania UJS Portal."""

    BASE_URL = "https://ujsportal.pacourts.us"
    SEARCH_URL = f"{BASE_URL}/CaseSearch"

    # County codes for filtering
    COUNTIES = [
        "Adams", "Allegheny", "Armstrong", "Beaver", "Bedford", "Berks",
        "Blair", "Bradford", "Bucks", "Butler", "Cambria", "Cameron",
        "Carbon", "Centre", "Chester", "Clarion", "Clearfield", "Clinton",
        "Columbia", "Crawford", "Cumberland", "Dauphin", "Delaware", "Elk",
        "Erie", "Fayette", "Forest", "Franklin", "Fulton", "Greene",
        "Huntingdon", "Indiana", "Jefferson", "Juniata", "Lackawanna",
        "Lancaster", "Lawrence", "Lebanon", "Lehigh", "Luzerne", "Lycoming",
        "McKean", "Mercer", "Mifflin", "Monroe", "Montgomery", "Montour",
        "Northampton", "Northumberland", "Perry", "Philadelphia", "Pike",
        "Potter", "Schuylkill", "Snyder", "Somerset", "Sullivan", "Susquehanna",
        "Tioga", "Union", "Venango", "Warren", "Washington", "Wayne",
        "Westmoreland", "Wyoming", "York"
    ]

    DOCKET_TYPES = ["Criminal", "Civil", "Family", "Orphans", "MDJ"]

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None

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
            accept_downloads=True,
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

    async def search_by_name(
        self,
        last_name: str,
        first_name: str,
        county: str = "",
        docket_type: str = "Criminal",
        limit: int = 25
    ) -> List[Dict[str, Any]]:
        """
        Search cases by participant name.

        Args:
            last_name: Participant's last name (required)
            first_name: Participant's first name (required)
            county: County to search in (optional, e.g., "Philadelphia")
            docket_type: Type of cases (Criminal, Civil, Family, etc.)
            limit: Maximum results to return

        Returns:
            List of case dictionaries
        """
        await self.page.goto(self.SEARCH_URL, timeout=60000)
        await self.page.wait_for_load_state('networkidle')
        await self.page.wait_for_timeout(2000)

        # Select "Participant Name" search type
        await self.page.evaluate('''() => {
            const select = document.querySelector('#SearchBy-Control select');
            if (select) {
                select.value = 'ParticipantName';
                select.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }''')
        await self.page.wait_for_timeout(1500)

        # Fill name fields (both are required)
        await self.page.fill('input[name="ParticipantLastName"]', last_name)
        await self.page.fill('input[name="ParticipantFirstName"]', first_name)

        # Select county if provided
        if county:
            await self.page.evaluate(f'''() => {{
                const select = document.querySelector('#County-Control select');
                if (select) {{
                    for (const opt of select.options) {{
                        if (opt.text.toLowerCase() === '{county.lower()}') {{
                            select.value = opt.value;
                            select.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            break;
                        }}
                    }}
                }}
            }}''')

        # Select docket type
        if docket_type:
            await self.page.evaluate(f'''() => {{
                const select = document.querySelector('#DocketType-Control select');
                if (select) {{
                    for (const opt of select.options) {{
                        if (opt.text === '{docket_type}') {{
                            select.value = opt.value;
                            select.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            break;
                        }}
                    }}
                }}
            }}''')

        # Submit search
        await self.page.click('#btnSearch')
        await self.page.wait_for_load_state('networkidle', timeout=60000)
        await self.page.wait_for_timeout(3000)

        # Extract results
        return await self._extract_search_results(limit)

    async def search_by_docket_number(
        self,
        docket_number: str
    ) -> List[Dict[str, Any]]:
        """
        Search by docket number.

        Args:
            docket_number: The docket number (e.g., "CP-51-CR-0001234-2024")

        Returns:
            List of matching cases (usually 1)
        """
        await self.page.goto(self.SEARCH_URL, timeout=60000)
        await self.page.wait_for_load_state('networkidle')
        await self.page.wait_for_timeout(2000)

        # Select "Docket Number" search type
        await self.page.evaluate('''() => {
            const select = document.querySelector('#SearchBy-Control select');
            if (select) {
                select.value = 'DocketNumber';
                select.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }''')
        await self.page.wait_for_timeout(1500)

        # Fill docket number
        await self.page.fill('input[name="DocketNumber"]', docket_number)

        # Submit search
        await self.page.click('#btnSearch')
        await self.page.wait_for_load_state('networkidle', timeout=60000)
        await self.page.wait_for_timeout(3000)

        return await self._extract_search_results(limit=10)

    async def _extract_search_results(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Extract case results from search results page."""
        results = await self.page.evaluate(f'''() => {{
            const cases = [];
            const rows = document.querySelectorAll('tr:not(.grid-view-header)');

            for (const row of rows) {{
                const docketCells = row.querySelectorAll('td[data-label="Docket Number"]');
                if (docketCells.length < 2) continue;

                const docketNumber = docketCells[1]?.textContent?.trim() || '';
                if (!docketNumber || !docketNumber.includes('-')) continue;

                const getField = (label) => {{
                    const cell = row.querySelector(`td[data-label="${{label}}"]`);
                    return cell?.textContent?.trim() || '';
                }};

                const docketLink = row.querySelector('a[href*="CpDocketSheet"], a[href*="MdjDocketSheet"]');

                cases.push({{
                    docket_number: docketNumber,
                    case_caption: getField('Case Caption'),
                    case_status: getField('Case Status'),
                    filing_date: getField('Filing Date'),
                    county: getField('County'),
                    primary_participant: getField('Primary Participant'),
                    docket_url: docketLink?.href || '',
                    state: 'PA'
                }});

                if (cases.length >= {limit}) break;
            }}
            return cases;
        }}''')

        return results

    async def get_case_detail(self, docket_url: str) -> Dict[str, Any]:
        """
        Get detailed case information by downloading and parsing docket sheet PDF.

        Args:
            docket_url: URL to the docket sheet PDF

        Returns:
            Dictionary with detailed case information
        """
        if not docket_url:
            return {}

        # Extract docket number from URL
        docket_match = re.search(r'docketNumber=([^&]+)', docket_url)
        docket_number = docket_match.group(1) if docket_match else ''

        # Click the link to trigger download
        async with self.page.expect_download(timeout=60000) as download_info:
            await self.page.click(f'a[href*="{docket_number}"]')

        download = await download_info.value

        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp_path = tmp.name

        await download.save_as(tmp_path)

        try:
            # Extract PDF text
            result = subprocess.run(
                ['pdftotext', '-layout', tmp_path, '-'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                return {'error': 'PDF extraction failed'}

            pdf_text = result.stdout
            return self._parse_docket_sheet(pdf_text, docket_number)

        except FileNotFoundError:
            return {'error': 'pdftotext not installed'}
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _parse_docket_sheet(self, text: str, case_id: str) -> Dict[str, Any]:
        """Parse docket sheet PDF text into structured data."""
        detail = {
            "case_number": case_id,
            "state": "PA",
        }

        # Extract case caption (defendant name)
        caption_match = re.search(r'v\.\s*\n\s*(.+?)(?:\n|CASE INFORMATION)', text, re.DOTALL)
        if caption_match:
            detail["case_style"] = f"Commonwealth v. {caption_match.group(1).strip()}"
            detail["defendant"] = caption_match.group(1).strip()

        # Extract court type from header
        if "MUNICIPAL COURT" in text:
            detail["court_type"] = "Municipal Court"
        elif "COMMON PLEAS" in text:
            detail["court_type"] = "Common Pleas"
        elif "MAGISTERIAL DISTRICT" in text:
            detail["court_type"] = "Magisterial District"

        # Extract fields using regex patterns
        patterns = {
            "judge": r"Judge Assigned:\s*(.+?)(?:\s{2,}|$)",
            "date_filed": r"Date Filed:\s*(\d{1,2}/\d{1,2}/\d{4})",
            "otn": r"OTN:\s*([A-Z]?\s?\d+-\d+)",
            "arresting_agency": r"Arresting Agency:\s*(.+?)(?:\s{2,}|$)",
            "arresting_officer": r"Arresting Officer:\s*(.+?)(?:\s{2,}|$)",
            "complaint_number": r"Complaint/Citation No\.?:\s*(.+?)(?:\s{2,}|$)",
            "incident_number": r"Incident Number:\s*(.+?)(?:\s{2,}|$)",
            "county": r"County:\s*(.+?)(?:\s{2,}|$)",
            "township": r"Township:\s*(.+?)(?:\s{2,}|$)",
            "case_status": r"Case Status:\s*(\w+)",
            "arrest_date": r"Arrest Date:\s*(\d{1,2}/\d{1,2}/\d{4})",
            "dob": r"Date Of Birth:\s*(\d{1,2}/\d{1,2}/\d{4})",
        }

        for field, pattern in patterns.items():
            match = re.search(pattern, text)
            if match:
                detail[field] = match.group(1).strip()

        # Extract charges
        detail["charges"] = self._extract_charges(text)

        # Extract bail info
        bail_match = re.search(r'Bail Type\s+.*?Amount\s*\n(.+?)(?:\n\s*\n|CHARGES)', text, re.DOTALL)
        if bail_match:
            bail_text = bail_match.group(1)
            amount_match = re.search(r'\$[\d,]+\.?\d*', bail_text)
            if amount_match:
                detail["bail_amount"] = amount_match.group(0)
            if "Monetary" in bail_text:
                detail["bail_type"] = "Monetary"

        return detail

    def _extract_charges(self, text: str) -> List[Dict[str, Any]]:
        """Extract charges from docket sheet text."""
        charges = []

        charges_match = re.search(r'CHARGES\s*\n(.+?)(?:DISPOSITION|COMMONWEALTH|ATTORNEY|$)', text, re.DOTALL)
        if not charges_match:
            return charges

        charges_text = charges_match.group(1)

        charge_pattern = r'(\d+)\s+\d+\s+(\w*)\s+(\d+\s*§\s*\d+[A-Za-z]*)\s+(.+?)\s+(\d{1,2}/\d{1,2}/\d{4})'
        for match in re.finditer(charge_pattern, charges_text):
            charges.append({
                "sequence": match.group(1),
                "grade": match.group(2) if match.group(2) else None,
                "statute": match.group(3).strip(),
                "description": match.group(4).strip(),
                "offense_date": match.group(5),
            })

        return charges


async def main():
    """Demo usage of the Pennsylvania scraper."""
    print("=" * 70)
    print("PENNSYLVANIA UJS PORTAL SCRAPER")
    print("=" * 70)

    async with PennsylvaniaScraper(headless=True) as scraper:
        # Search by name
        print("\n[1] Searching for 'Smith, Michael' in Philadelphia (Criminal)...")
        results = await scraper.search_by_name(
            last_name="Smith",
            first_name="Michael",
            county="Philadelphia",
            docket_type="Criminal",
            limit=10
        )

        print(f"\n    Found {len(results)} cases:")
        for i, case in enumerate(results[:5]):
            print(f"\n    [{i+1}] {case['docket_number']}")
            print(f"        Caption: {case['case_caption'][:50]}...")
            print(f"        Status: {case['case_status']}")
            print(f"        Filed: {case['filing_date']}")
            print(f"        County: {case['county']}")

        # Get detailed info for first case
        if results and results[0].get('docket_url'):
            print(f"\n[2] Getting detailed info for first case...")
            detail = await scraper.get_case_detail(results[0]['docket_url'])

            if detail:
                print("\n    === Case Details ===")
                for key in ['case_style', 'court_type', 'date_filed', 'judge',
                           'case_status', 'arresting_agency', 'bail_amount']:
                    if key in detail:
                        print(f"    {key}: {detail[key]}")

                if detail.get('charges'):
                    print(f"\n    Charges ({len(detail['charges'])}):")
                    for charge in detail['charges'][:3]:
                        print(f"      - {charge.get('statute')}: {charge.get('description', '')[:40]}")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
