#!/usr/bin/env python3
"""
Oklahoma State Courts Network (OSCN) Scraper

URL: https://www.oscn.net/dockets/Search.aspx

Features:
- No CAPTCHA required
- No login required
- Simple HTTP requests (no browser needed)
- Statewide search across all 77 counties + appellate courts
- Party name search (last, first, middle)
- Case number search
- Date range filtering
- Case type filtering

Usage:
    python scraper_oklahoma.py --name "Smith"
    python scraper_oklahoma.py --name "Smith" --first "John" --county tulsa
    python scraper_oklahoma.py --case-number "CF-2024-123"
"""

import argparse
import json
import re
import requests
from dataclasses import dataclass, asdict
from datetime import datetime
from html.parser import HTMLParser
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin


# Oklahoma counties available in OSCN
OKLAHOMA_COUNTIES = [
    'all', 'adair', 'alfalfa', 'atoka', 'beaver', 'beckham', 'blaine', 'bryan',
    'caddo', 'canadian', 'carter', 'cherokee', 'choctaw', 'cimarron', 'cleveland',
    'coal', 'comanche', 'cotton', 'craig', 'creek', 'custer', 'delaware', 'dewey',
    'ellis', 'garfield', 'garvin', 'grady', 'grant', 'greer', 'harmon', 'harper',
    'haskell', 'hughes', 'jackson', 'jefferson', 'johnston', 'kay', 'kingfisher',
    'kiowa', 'latimer', 'leflore', 'lincoln', 'logan', 'love', 'major', 'marshall',
    'mayes', 'mcclain', 'mccurtain', 'mcintosh', 'murray', 'muskogee', 'noble',
    'nowata', 'okfuskee', 'oklahoma', 'okmulgee', 'osage', 'ottawa', 'pawnee',
    'payne', 'pittsburg', 'pontotoc', 'pottawatomie', 'pushmataha', 'rogermills',
    'rogers', 'seminole', 'sequoyah', 'stephens', 'texas', 'tillman', 'tulsa',
    'wagoner', 'washington', 'washita', 'woods', 'woodward', 'appellate'
]

# Case type prefixes
CASE_TYPES = {
    'CF': 'Criminal Felony',
    'CM': 'Criminal Misdemeanor',
    'CJ': 'Civil',
    'CS': 'Civil Small Claims',
    'SC': 'Small Claims',
    'TR': 'Traffic',
    'PO': 'Protective Order',
    'FD': 'Family/Divorce',
    'JV': 'Juvenile',
    'PB': 'Probate',
    'MI': 'Miscellaneous',
    'ML': 'Marriage License',
    'AD': 'Adoption',
}


@dataclass
class OklahomaCase:
    """Represents an Oklahoma court case."""
    case_number: str
    date_filed: str
    style: str  # Case title (e.g., "STATE OF OKLAHOMA v. JOHN SMITH")
    found_party: str
    party_role: str  # e.g., "Defendant", "Plaintiff"
    county: str
    case_url: str
    case_type: str = ""

    def __post_init__(self):
        # Extract case type from case number
        if '-' in self.case_number:
            prefix = self.case_number.split('-')[0]
            self.case_type = CASE_TYPES.get(prefix, prefix)


class OSCNResultsParser(HTMLParser):
    """Parse OSCN search results HTML."""

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.cases = []
        self.current_county = ""
        self.in_table = False
        self.in_header = False
        self.in_row = False
        self.current_row = []
        self.current_cell = ""
        self.current_link = ""
        self.cell_index = 0

    def handle_starttag(self, tag: str, attrs: List[tuple]):
        attrs_dict = dict(attrs)

        if tag == 'table' and 'caseCourtTable' in attrs_dict.get('class', ''):
            self.in_table = True

        elif tag == 'caption' and 'caseCourtHeader' in attrs_dict.get('class', ''):
            self.in_header = True

        elif tag == 'tr' and 'resultTableRow' in attrs_dict.get('class', ''):
            self.in_row = True
            self.current_row = []
            self.cell_index = 0

        elif tag == 'td' and self.in_row:
            self.current_cell = ""
            self.current_link = ""

        elif tag == 'a' and self.in_row:
            href = attrs_dict.get('href', '')
            if 'GetCaseInformation' in href:
                self.current_link = urljoin(self.base_url, href)

    def handle_endtag(self, tag: str):
        if tag == 'table':
            self.in_table = False

        elif tag == 'caption':
            self.in_header = False

        elif tag == 'tr' and self.in_row:
            self.in_row = False
            if len(self.current_row) >= 4:
                # Parse the row data
                case_number = self.current_row[0].get('text', '').strip()
                date_filed = self.current_row[1].get('text', '').strip()
                style = self.current_row[2].get('text', '').strip()
                found_party_full = self.current_row[3].get('text', '').strip()
                case_url = self.current_row[0].get('link', '')

                # Parse party name and role
                party_match = re.match(r'^(.+?)\s*\((.+?)\)$', found_party_full)
                if party_match:
                    found_party = party_match.group(1).strip()
                    party_role = party_match.group(2).strip()
                else:
                    found_party = found_party_full
                    party_role = ""

                if case_number and re.match(r'^[A-Z]{2}-\d{4}', case_number):
                    self.cases.append(OklahomaCase(
                        case_number=case_number,
                        date_filed=date_filed,
                        style=style,
                        found_party=found_party,
                        party_role=party_role,
                        county=self.current_county,
                        case_url=case_url
                    ))

        elif tag == 'td' and self.in_row:
            self.current_row.append({
                'text': self.current_cell,
                'link': self.current_link
            })
            self.cell_index += 1

    def handle_data(self, data: str):
        if self.in_header:
            # Extract county name
            county_match = re.match(r'(\w+)\s*County', data.strip(), re.I)
            if county_match:
                self.current_county = county_match.group(1)
        elif self.in_row:
            self.current_cell += data


class OSCNCaseDetailParser(HTMLParser):
    """Parse OSCN case detail page."""

    def __init__(self):
        super().__init__()
        self.case_info = {}
        self.in_case_style = False
        self.in_section = ""
        self.parties = []
        self.events = []
        self.docket_entries = []
        self.current_text = ""

    def handle_starttag(self, tag: str, attrs: List[tuple]):
        attrs_dict = dict(attrs)

        if tag == 'table' and 'caseStyle' in attrs_dict.get('class', ''):
            self.in_case_style = True

        elif tag == 'h2':
            section_class = attrs_dict.get('class', '')
            if 'parties' in section_class:
                self.in_section = 'parties'
            elif 'events' in section_class:
                self.in_section = 'events'
            elif 'issues' in section_class:
                self.in_section = 'issues'

    def handle_data(self, data: str):
        text = data.strip()
        if self.in_case_style and text:
            self.current_text += text + " "
        if text:
            # Parse judge info
            if 'Judge:' in text:
                judge_match = re.search(r'Judge:\s*(.+)', text)
                if judge_match:
                    self.case_info['judge'] = judge_match.group(1).strip()
            # Parse filing date
            if 'Filed:' in text:
                filed_match = re.search(r'Filed:\s*(.+)', text)
                if filed_match:
                    self.case_info['filed_date'] = filed_match.group(1).strip()


class OklahomaScraper:
    """Scraper for Oklahoma State Courts Network (OSCN)."""

    BASE_URL = "https://www.oscn.net"
    SEARCH_URL = "https://www.oscn.net/dockets/Results.aspx"
    CASE_URL = "https://www.oscn.net/dockets/GetCaseInformation.aspx"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })

    def search(
        self,
        last_name: Optional[str] = None,
        first_name: Optional[str] = None,
        middle_name: Optional[str] = None,
        case_number: Optional[str] = None,
        county: str = "all",
        filed_after: Optional[str] = None,
        filed_before: Optional[str] = None,
        closed_after: Optional[str] = None,
        closed_before: Optional[str] = None,
        limit: int = 100
    ) -> List[OklahomaCase]:
        """
        Search Oklahoma OSCN for cases.

        Args:
            last_name: Last name of party
            first_name: First name of party
            middle_name: Middle name of party
            case_number: Case number to search
            county: County code (e.g., 'tulsa', 'oklahoma', 'all')
            filed_after: Cases filed after date (MM/DD/YYYY)
            filed_before: Cases filed before date (MM/DD/YYYY)
            closed_after: Cases closed after date (MM/DD/YYYY)
            closed_before: Cases closed before date (MM/DD/YYYY)
            limit: Maximum results to return

        Returns:
            List of OklahomaCase objects
        """
        params = {
            'db': county.lower()
        }

        if case_number:
            params['number'] = case_number
        if last_name:
            params['lname'] = last_name
        if first_name:
            params['fname'] = first_name
        if middle_name:
            params['mname'] = middle_name
        if filed_after:
            params['FiledDateL'] = filed_after
        if filed_before:
            params['FiledDateH'] = filed_before
        if closed_after:
            params['ClosedDateL'] = closed_after
        if closed_before:
            params['ClosedDateH'] = closed_before

        print(f"[1] Searching OSCN with params: {params}")

        try:
            response = self.session.get(
                self.SEARCH_URL,
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()

            print(f"[2] Response: {response.status_code}, {len(response.text)} bytes")

            # Parse results
            parser = OSCNResultsParser(self.BASE_URL)
            parser.feed(response.text)

            cases = parser.cases[:limit]
            print(f"[3] Found {len(cases)} cases")

            return cases

        except requests.RequestException as e:
            print(f"Error searching OSCN: {e}")
            return []

    def get_case_details(self, case_number: str, county: str) -> Dict[str, Any]:
        """
        Get detailed information for a specific case.

        Args:
            case_number: The case number (e.g., "CF-2024-123")
            county: The county database (e.g., "oklahoma", "tulsa")

        Returns:
            Dictionary with case details
        """
        params = {
            'db': county.lower(),
            'number': case_number
        }

        try:
            response = self.session.get(
                self.CASE_URL,
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()

            parser = OSCNCaseDetailParser()
            parser.feed(response.text)

            return {
                'case_number': case_number,
                'county': county,
                **parser.case_info
            }

        except requests.RequestException as e:
            print(f"Error getting case details: {e}")
            return {}


def main():
    parser = argparse.ArgumentParser(
        description='Oklahoma OSCN Court Case Scraper',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --name Smith
    %(prog)s --name Smith --first John --county tulsa
    %(prog)s --case-number "CF-2024-123" --county oklahoma
    %(prog)s --name Johnson --filed-after "01/01/2024"
        """
    )
    parser.add_argument('--name', '-n', help='Last name to search')
    parser.add_argument('--first', '-f', help='First name to search')
    parser.add_argument('--middle', '-m', help='Middle name to search')
    parser.add_argument('--case-number', '-c', help='Case number to search')
    parser.add_argument('--county', default='all',
                        choices=OKLAHOMA_COUNTIES,
                        help='County to search (default: all)')
    parser.add_argument('--filed-after', help='Filed after date (MM/DD/YYYY)')
    parser.add_argument('--filed-before', help='Filed before date (MM/DD/YYYY)')
    parser.add_argument('--limit', '-l', type=int, default=50,
                        help='Maximum results (default: 50)')
    parser.add_argument('--output', '-o', help='Output JSON file')
    parser.add_argument('--details', action='store_true',
                        help='Fetch full case details for each result')

    args = parser.parse_args()

    # Default search if no args
    if not args.name and not args.case_number:
        args.name = "Smith"

    print("=" * 70)
    print("OKLAHOMA STATE COURTS NETWORK (OSCN) SCRAPER")
    print("=" * 70)
    print(f"Search parameters:")
    print(f"  Last name: {args.name or 'N/A'}")
    print(f"  First name: {args.first or 'N/A'}")
    print(f"  Case number: {args.case_number or 'N/A'}")
    print(f"  County: {args.county}")
    print(f"  Limit: {args.limit}")
    print("=" * 70)

    scraper = OklahomaScraper()

    cases = scraper.search(
        last_name=args.name,
        first_name=args.first,
        middle_name=args.middle,
        case_number=args.case_number,
        county=args.county,
        filed_after=args.filed_after,
        filed_before=args.filed_before,
        limit=args.limit
    )

    if cases:
        # Group by case type
        by_type = {}
        for case in cases:
            by_type.setdefault(case.case_type, []).append(case)

        print(f"\nFound {len(cases)} cases:\n")

        print("Cases by type:")
        for case_type, type_cases in sorted(by_type.items()):
            print(f"\n  {case_type}: {len(type_cases)} cases")
            for case in type_cases[:3]:
                print(f"    {case.case_number} - {case.date_filed}")
                print(f"      {case.style[:60]}...")
                if case.found_party:
                    print(f"      Party: {case.found_party} ({case.party_role})")

        # Save to JSON
        if args.output:
            output_data = [asdict(c) for c in cases]
            with open(args.output, 'w') as f:
                json.dump(output_data, f, indent=2)
            print(f"\nResults saved to {args.output}")
        else:
            # Save to default location
            output_file = '/tmp/oklahoma_oscn_results.json'
            output_data = [asdict(c) for c in cases]
            with open(output_file, 'w') as f:
                json.dump(output_data, f, indent=2)
            print(f"\nResults saved to {output_file}")

    else:
        print("\nNo results found.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
