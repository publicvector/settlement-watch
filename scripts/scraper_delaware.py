#!/usr/bin/env python3
"""
Delaware CourtConnect Scraper

URL: https://courtconnect.courts.delaware.gov/cc/cconnect/ck_public_qry_main.cp_main_idx

Features:
- No CAPTCHA required
- No login required (just accept disclaimer via POST)
- Simple HTTP requests (no browser needed)
- Covers Superior Court, Court of Common Pleas, and Justice of the Peace
- Search by person name, business name, or case type
- Search by case ID for docket reports

Usage:
    python scraper_delaware.py --name "Smith"
    python scraper_delaware.py --name "Smith" --first "John"
    python scraper_delaware.py --case-id "N22C-09-501"
"""

import argparse
import json
import re
import requests
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional


# Case types available in Delaware CourtConnect
DELAWARE_CASE_TYPES = {
    'ALL': 'All Case Types',
    '2A': 'Civil Actions (Chancery)',
    '3A': 'Superior Court Civil Appeals',
    '3C': 'Superior Court Civil Complaints',
    '3J': 'Superior Court Judgments',
    '4A': 'Debt (CCP)',
    '4B': 'Breach of Contract (CCP)',
    '4E': 'Personal Injury (CCP)',
    '60': 'JP Debt Action',
    '61': 'JP Landlord Tenant',
    'CR': 'Personal Injury Auto',
    'CS': 'Personal Injury',
    'CF': 'Debt/Breach of Contract',
    'CU': 'Products Liability',
    'IC': 'Involuntary Commitments',
    'LM': 'Mortgage',
}


@dataclass
class DelawareCase:
    """Represents a Delaware court case."""
    party_id: str
    party_name: str
    address: str
    case_id: str
    case_title: str
    party_type: str
    party_end_date: str
    filing_date: str
    case_status: str
    case_url: str

    @property
    def formatted_date(self) -> str:
        """Convert date from DD-MON-YYYY to standard format."""
        try:
            from datetime import datetime
            return datetime.strptime(self.filing_date, '%d-%b-%Y').strftime('%Y-%m-%d')
        except:
            return self.filing_date


def parse_delaware_results(html: str, base_url: str) -> List[DelawareCase]:
    """Parse Delaware CourtConnect search results using regex."""
    cases = []

    # Find all table rows with results
    # Pattern: each result row has ID, Name, Address+Case, Party Type, End Date, Filing Date, Status
    row_pattern = re.compile(
        r'<tr\s+align="left">\s*'
        r'<td[^>]*>([^<]*)</td>\s*'  # Party ID
        r'<td>([^<]*)</td>\s*'       # Party Name
        r'<td>(.*?)</td>\s*'         # Address + Case (complex cell)
        r'<td[^>]*>([^<]*)</td>\s*'  # Party Type
        r'<td>([^<]*)</td>\s*'       # Party End Date
        r'<td[^>]*>([^<]*)</td>\s*'  # Filing Date
        r'<td[^>]*>([^<]*)</td>',    # Case Status
        re.DOTALL | re.IGNORECASE
    )

    for match in row_pattern.finditer(html):
        party_id = match.group(1).strip()
        party_name = match.group(2).strip()
        address_case_cell = match.group(3)
        party_type = match.group(4).strip()
        party_end_date = match.group(5).strip()
        filing_date = match.group(6).strip()
        case_status = match.group(7).strip()

        # Parse the address + case cell
        # Look for case link
        case_link_match = re.search(
            r'href="([^"]*cp_dktrpt[^"]*)"[^>]*>([^<]+)</a>\s*([^<]*)',
            address_case_cell
        )

        case_url = ""
        case_id = ""
        case_title = ""
        address = ""

        if case_link_match:
            case_url = base_url + "/" + case_link_match.group(1)
            case_id = case_link_match.group(2).strip()
            case_title = case_link_match.group(3).strip()

        # Extract address (text before Case: or unavailable)
        address_match = re.search(r'^([^<]*?)(?:<b>Case:|<i>unavailable)', address_case_cell)
        if address_match:
            address = re.sub(r'<[^>]+>', ' ', address_match.group(1)).strip()
            address = re.sub(r'\s+', ' ', address)

        if case_id:
            # Clean HTML entities from all fields
            def clean_html(text):
                text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
                text = text.replace('&lt;', '<').replace('&gt;', '>')
                return re.sub(r'\s+', ' ', text).strip()

            cases.append(DelawareCase(
                party_id=party_id,
                party_name=clean_html(party_name),
                address=clean_html(address) if address and address != 'unavailable' else '',
                case_id=case_id,
                case_title=clean_html(case_title),
                party_type=clean_html(party_type),
                party_end_date=clean_html(party_end_date),
                filing_date=clean_html(filing_date),
                case_status=clean_html(case_status),
                case_url=case_url
            ))

    return cases


class DelawareScraper:
    """Scraper for Delaware CourtConnect."""

    BASE_URL = "https://courtconnect.courts.delaware.gov/cc/cconnect"
    SEARCH_URL = "https://courtconnect.courts.delaware.gov/cc/cconnect/ck_public_qry_cpty.cp_personcase_srch_details"
    DOCKET_URL = "https://courtconnect.courts.delaware.gov/cc/cconnect/ck_public_qry_doct.cp_dktrpt_docket_report"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })
        self._accepted_disclaimer = False

    def _accept_disclaimer(self):
        """Accept the CourtConnect disclaimer."""
        if self._accepted_disclaimer:
            return

        # Navigate to disclaimer page and accept
        disclaimer_url = f"{self.BASE_URL}/ck_public_qry_main.cp_main_disclaimer?search_option=party"
        self.session.get(disclaimer_url, timeout=self.timeout)

        # Accept disclaimer (POST to setup)
        accept_url = f"{self.BASE_URL}/ck_public_qry_cpty.cp_personcase_setup_idx"
        self.session.post(accept_url, timeout=self.timeout)

        self._accepted_disclaimer = True

    def search(
        self,
        last_name: str,
        first_name: Optional[str] = None,
        middle_name: Optional[str] = None,
        case_type: str = "ALL",
        begin_date: Optional[str] = None,
        end_date: Optional[str] = None,
        phonetic: bool = False,
        partial: bool = False,
        limit: int = 100
    ) -> List[DelawareCase]:
        """
        Search Delaware CourtConnect for cases.

        Args:
            last_name: Last name or company name (required)
            first_name: First name
            middle_name: Middle name
            case_type: Case type code (e.g., 'ALL', '60', 'CR')
            begin_date: Beginning filing date (DD-MON-YYYY format)
            end_date: Ending filing date (DD-MON-YYYY format)
            phonetic: Enable phonetic (soundex) search
            partial: Enable partial name matching
            limit: Maximum results to return

        Returns:
            List of DelawareCase objects
        """
        self._accept_disclaimer()

        params = {
            'backto': 'P',
            'soundex_ind': 'checked' if phonetic else '',
            'partial_ind': 'checked' if partial else '',
            'last_name': last_name,
            'first_name': first_name or '',
            'middle_name': middle_name or '',
            'begin_date': begin_date or '',
            'end_date': end_date or '',
            'case_type': case_type,
            'id_code': '',
            'PageNo': '1'
        }

        print(f"[1] Searching Delaware CourtConnect for: {last_name}")

        all_cases = []
        page = 1

        while len(all_cases) < limit:
            params['PageNo'] = str(page)

            try:
                response = self.session.get(
                    self.SEARCH_URL,
                    params=params,
                    timeout=self.timeout
                )
                response.raise_for_status()

                print(f"[2] Page {page}: {response.status_code}, {len(response.text)} bytes")

                # Parse results
                page_cases = parse_delaware_results(response.text, self.BASE_URL)

                if not page_cases:
                    break

                all_cases.extend(page_cases)
                print(f"    Found {len(page_cases)} cases on page {page}")

                # Check for next page
                if 'Next->' not in response.text:
                    break

                page += 1

                # Safety limit
                if page > 50:
                    break

            except requests.RequestException as e:
                print(f"Error searching CourtConnect: {e}")
                break

        cases = all_cases[:limit]
        print(f"[3] Total: {len(cases)} cases")

        return cases

    def get_docket_report(self, case_id: str) -> Dict[str, Any]:
        """
        Get docket report for a specific case.

        Args:
            case_id: The case ID (e.g., "N22C-09-501")

        Returns:
            Dictionary with case details
        """
        self._accept_disclaimer()

        params = {
            'backto': 'P',
            'case_id': case_id,
            'begin_date': '',
            'end_date': ''
        }

        try:
            response = self.session.get(
                self.DOCKET_URL,
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()

            # Parse case info
            result = {'case_id': case_id}

            # Extract case title
            title_match = re.search(r'Case ID:</b><td>&nbsp;(\S+)\s*-\s*([^<]+)', response.text)
            if title_match:
                result['case_title'] = title_match.group(2).strip()

            # Extract filing date
            filed_match = re.search(r'Filing Date:</b><td>&nbsp;([^<]+)', response.text)
            if filed_match:
                result['filing_date'] = filed_match.group(1).strip()

            # Extract case type
            type_match = re.search(r'Type:</b>\s*<td>&nbsp;([^<]+)', response.text)
            if type_match:
                result['case_type'] = type_match.group(1).strip()

            # Extract status
            status_match = re.search(r'Status:</b>\s*<td>&nbsp;([^<]+)', response.text)
            if status_match:
                result['status'] = status_match.group(1).strip()

            # Extract parties
            parties = []
            party_pattern = re.compile(
                r'<td>([A-Z]+)</td>\s*<td>[^<]*</td>\s*<td><b>([^<]+)</b>',
                re.IGNORECASE
            )
            for match in party_pattern.finditer(response.text):
                parties.append({
                    'type': match.group(1),
                    'name': match.group(2).strip()
                })
            result['parties'] = parties

            return result

        except requests.RequestException as e:
            print(f"Error getting docket report: {e}")
            return {}


def main():
    parser = argparse.ArgumentParser(
        description='Delaware CourtConnect Case Scraper',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Courts covered:
  - Superior Court
  - Court of Common Pleas
  - Justice of the Peace Court

Examples:
    %(prog)s --name Smith
    %(prog)s --name Smith --first John
    %(prog)s --name "ABC Corporation"
    %(prog)s --case-id "N22C-09-501"
    %(prog)s --name Smith --case-type 60  # JP Debt Actions only
        """
    )
    parser.add_argument('--name', '-n', help='Last name or company name to search')
    parser.add_argument('--first', '-f', help='First name')
    parser.add_argument('--middle', '-m', help='Middle name')
    parser.add_argument('--case-id', '-c', help='Case ID to lookup')
    parser.add_argument('--case-type', default='ALL',
                        help='Case type code (default: ALL)')
    parser.add_argument('--begin-date', help='Begin date (DD-MON-YYYY)')
    parser.add_argument('--end-date', help='End date (DD-MON-YYYY)')
    parser.add_argument('--phonetic', action='store_true',
                        help='Enable phonetic (soundex) search')
    parser.add_argument('--partial', action='store_true',
                        help='Enable partial name matching')
    parser.add_argument('--limit', '-l', type=int, default=50,
                        help='Maximum results (default: 50)')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    # Default search if no args
    if not args.name and not args.case_id:
        args.name = "Smith"

    print("=" * 70)
    print("DELAWARE COURTCONNECT SCRAPER")
    print("=" * 70)
    print(f"Courts: Superior Court, Court of Common Pleas, JP Court")
    print(f"Search parameters:")
    print(f"  Name: {args.name or 'N/A'}")
    print(f"  First name: {args.first or 'N/A'}")
    print(f"  Case ID: {args.case_id or 'N/A'}")
    print(f"  Case type: {args.case_type}")
    print(f"  Limit: {args.limit}")
    print("=" * 70)

    scraper = DelawareScraper()

    if args.case_id:
        # Lookup specific case
        print(f"\nFetching docket report for case: {args.case_id}")
        result = scraper.get_docket_report(args.case_id)

        if result:
            print(f"\nCase Details:")
            for key, value in result.items():
                if value and key != 'parties':
                    print(f"  {key}: {value}")
            if result.get('parties'):
                print(f"  Parties:")
                for p in result['parties'][:10]:
                    print(f"    - {p['name']} ({p['type']})")

            if args.output:
                with open(args.output, 'w') as f:
                    json.dump(result, f, indent=2)
                print(f"\nResults saved to {args.output}")
        else:
            print("\nCase not found.")

    else:
        # Search by name
        cases = scraper.search(
            last_name=args.name,
            first_name=args.first,
            middle_name=args.middle,
            case_type=args.case_type,
            begin_date=args.begin_date,
            end_date=args.end_date,
            phonetic=args.phonetic,
            partial=args.partial,
            limit=args.limit
        )

        if cases:
            # Group by party type
            by_type = {}
            for case in cases:
                by_type.setdefault(case.party_type, []).append(case)

            print(f"\nFound {len(cases)} cases:\n")

            print("Cases by party type:")
            for party_type, type_cases in sorted(by_type.items()):
                print(f"\n  {party_type}: {len(type_cases)} cases")
                for case in type_cases[:3]:
                    print(f"    {case.case_id} - {case.filing_date}")
                    print(f"      {case.case_title[:60]}...")
                    print(f"      Party: {case.party_name}")
                    print(f"      Status: {case.case_status}")

            # Save to JSON
            if args.output:
                output_data = [asdict(c) for c in cases]
                with open(args.output, 'w') as f:
                    json.dump(output_data, f, indent=2)
                print(f"\nResults saved to {args.output}")
            else:
                output_file = '/tmp/delaware_courtconnect_results.json'
                output_data = [asdict(c) for c in cases]
                with open(output_file, 'w') as f:
                    json.dump(output_data, f, indent=2)
                print(f"\nResults saved to {output_file}")

        else:
            print("\nNo results found.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
