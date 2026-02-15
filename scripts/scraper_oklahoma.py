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


@dataclass
class DocketEntry:
    """Represents a docket entry within a case."""
    entry_date: str
    entry_text: str
    code: str = ""
    entry_number: int = 0
    amount: str = ""
    party: str = ""
    document_url: str = ""
    is_opinion: bool = False
    is_order: bool = False
    is_filing: bool = False
    is_hearing: bool = False
    entry_type: str = "filing"

    def __post_init__(self):
        """Auto-detect entry type from text and code."""
        text_lower = self.entry_text.lower()
        code_upper = self.code.upper()

        # Codes that indicate court-issued orders
        order_codes = {'OH', 'ORD', 'ORDR', 'EPOI', 'EPO', 'JO', 'CO', 'SO', 'CTFREE'}

        # Codes that indicate party filings
        filing_codes = {'MO', 'PET', 'ANS', 'BR', 'RESP', 'MEMO', 'AFF', 'NOT', 'EPOSV'}

        # Opinion patterns - judicial decisions
        opinion_patterns = [
            'opinion', 'judgment entered', 'verdict', 'findings of fact',
            'conclusions of law', 'memorandum decision', 'final judgment',
            'summary judgment granted', 'summary judgment denied',
            'case dismissed', 'sustained', 'overruled', 'adjudicated'
        ]

        # Order patterns - court issued orders (not just mentioning "order")
        order_start_patterns = [
            'order ', 'order:', 'ordered', 'court order', 'judge order',
            'it is ordered', 'the court orders', 'order granting',
            'order denying', 'order setting', 'order on', 'order to',
            'protective order issued', 'emergency protective order issued',
            'injunction', 'stay granted', 'remand', 'sentenc'
        ]

        # Filing patterns - party submissions
        filing_patterns = [
            'motion', 'petition', 'complaint', 'answer', 'response',
            'brief', 'memorandum', 'affidavit', 'notice of', 'subpoena',
            'summons', 'filed', 'document available', 'served'
        ]

        # Hearing patterns
        hearing_patterns = [
            'hearing', 'trial', 'conference', 'arraignment', 'docket call'
        ]

        # Detect types
        self.is_opinion = any(p in text_lower for p in opinion_patterns)

        # Order detection: must start with order pattern OR be an order code
        self.is_order = (
            code_upper in order_codes or
            any(text_lower.startswith(p) or f': {p}' in text_lower for p in order_start_patterns) or
            (text_lower.startswith('order') and 'motion' not in text_lower)
        )

        # Filing detection: party submissions
        self.is_filing = (
            code_upper in filing_codes or
            any(p in text_lower for p in filing_patterns)
        ) and not self.is_order

        self.is_hearing = any(p in text_lower for p in hearing_patterns)

        # Set entry type (priority: opinion > order > hearing > filing)
        if self.is_opinion:
            self.entry_type = 'opinion'
        elif self.is_order and not self.is_filing:
            self.entry_type = 'order'
        elif self.is_hearing:
            self.entry_type = 'hearing'
        elif self.is_filing:
            self.entry_type = 'filing'
        else:
            self.entry_type = 'other'


# Codes to filter out (fees and administrative entries)
FEE_CODES = {
    'DMFE', 'PFE7', 'OCISR', 'OCJC', 'OCASA', 'SSFCHSCPC', 'CCADMINCSF',
    'CCADMIN', 'SJFIS', 'DCADMIN', 'CCRMPF', 'INDEBT', 'LLF', 'CVFEE',
    'VJCF', 'CVMISC', 'REGFEE', 'TAXFEE', 'FINE', 'COST', 'BOND'
}

def is_fee_entry(code: str, description: str, amount: str) -> bool:
    """Check if a docket entry is a fee/administrative entry to filter out."""
    code_upper = code.upper()
    desc_lower = description.lower()

    # Skip if code starts with known fee prefixes
    if any(code_upper.startswith(prefix) for prefix in ['CCADMIN', 'DCADMIN', 'PFE', 'SSF']):
        return True

    # Skip if code is in fee codes set
    if code_upper in FEE_CODES:
        return True

    # Skip if it has an amount and description mentions fee/fund
    if amount and ('fee' in desc_lower or 'fund' in desc_lower):
        return True

    # Skip very long codes (usually administrative)
    if len(code) > 10:
        return True

    # Skip receipts and payment records
    if 'receipt #' in desc_lower or 'total amount paid' in desc_lower:
        return True

    # Skip OCIS system messages
    if 'ocis has automatically' in desc_lower:
        return True

    # Skip adjusting entries
    if desc_lower.startswith('adjusting entry'):
        return True

    return False


class OSCNCaseDetailParser(HTMLParser):
    """Parse OSCN case detail page including docket entries and events."""

    def __init__(self):
        super().__init__()
        self.case_info = {}
        self.in_case_style = False
        self.in_events_table = False
        self.in_docket_table = False
        self.in_row = False
        self.parties = []
        self.events = []  # Scheduled hearings
        self.docket_entries = []  # Docket filings
        self.current_text = ""
        self.current_row = []
        self.current_cell = ""
        self.current_link = ""
        self.current_table_type = None  # 'events' or 'docket'

    def handle_starttag(self, tag: str, attrs: List[tuple]):
        attrs_dict = dict(attrs)
        class_attr = attrs_dict.get('class', '')

        if tag == 'table':
            if 'caseStyle' in class_attr:
                self.in_case_style = True
            elif 'events_table' in class_attr:
                self.in_events_table = True
                self.current_table_type = 'events'
            elif 'docketlist' in class_attr:
                self.in_docket_table = True
                self.current_table_type = 'docket'

        elif tag == 'tr' and (self.in_events_table or self.in_docket_table):
            self.in_row = True
            self.current_row = []

        elif tag == 'td' and self.in_row:
            self.current_cell = ""
            self.current_link = ""

        elif tag == 'a' and self.in_row:
            href = attrs_dict.get('href', '')
            if href:
                self.current_link = href

    def handle_endtag(self, tag: str):
        if tag == 'table':
            self.in_case_style = False
            self.in_events_table = False
            self.in_docket_table = False
            self.current_table_type = None

        elif tag == 'tr' and self.in_row:
            self.in_row = False
            self._process_row()

        elif tag == 'td' and self.in_row:
            self.current_row.append({
                'text': self.current_cell.strip().replace('\xa0', ' ').replace('&nbsp;', ' '),
                'link': self.current_link
            })

    def _process_row(self):
        """Process a completed table row."""
        if len(self.current_row) < 2:
            return

        if self.current_table_type == 'events':
            self._process_events_row()
        elif self.current_table_type == 'docket':
            self._process_docket_row()

    def _process_events_row(self):
        """Process an events table row (scheduled hearings)."""
        # Events format: DateTime+Event, empty, Judge, empty
        if len(self.current_row) >= 1:
            event_text = self.current_row[0]['text'].strip()
            judge = self.current_row[2]['text'].strip() if len(self.current_row) > 2 else ''

            if event_text and ('hearing' in event_text.lower() or 'trial' in event_text.lower() or
                               'conference' in event_text.lower() or 'arraignment' in event_text.lower()):
                # Parse date from event text
                date_match = re.match(r'(\w+,\s+\w+\s+\d+,\s+\d+)', event_text)
                entry_date = date_match.group(1) if date_match else ''

                entry = DocketEntry(
                    entry_date=entry_date,
                    entry_text=event_text,
                    code='HEARING',
                    is_hearing=True,
                    entry_type='hearing'
                )
                if judge:
                    self.case_info['judge'] = judge
                self.events.append(entry)

    def _process_docket_row(self):
        """Process a docket list row."""
        # Docket format: Date, Code, Description, Count, empty, Amount
        if len(self.current_row) < 3:
            return

        entry_date = self.current_row[0]['text'].strip()
        code = self.current_row[1]['text'].strip()
        description = self.current_row[2]['text'].strip()
        doc_link = self.current_row[2].get('link', '')
        count = self.current_row[3]['text'].strip() if len(self.current_row) > 3 else ''
        amount = self.current_row[5]['text'].strip() if len(self.current_row) > 5 else ''

        # Skip empty or fee entries
        if not code or not description:
            return
        if is_fee_entry(code, description, amount):
            return

        # Create docket entry
        entry = DocketEntry(
            entry_date=entry_date,
            entry_text=description,
            code=code,
            amount=amount,
            document_url=doc_link if doc_link and 'GetDocument' in doc_link else ''
        )

        # Entry number from count field
        if count and count.isdigit():
            entry.entry_number = int(count)

        self.docket_entries.append(entry)

    def handle_data(self, data: str):
        text = data.strip()

        if self.in_row:
            self.current_cell += data

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
        Get detailed information for a specific case including docket entries.

        Args:
            case_number: The case number (e.g., "CF-2024-123")
            county: The county database (e.g., "oklahoma", "tulsa")

        Returns:
            Dictionary with case details and docket entries
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

            # Convert docket entries to dicts
            docket_entries = [asdict(e) for e in parser.docket_entries]

            return {
                'case_number': case_number,
                'county': county,
                'docket_entries': docket_entries,
                'docket_count': len(docket_entries),
                'opinions': [e for e in docket_entries if e.get('is_opinion')],
                'orders': [e for e in docket_entries if e.get('is_order')],
                **parser.case_info
            }

        except requests.RequestException as e:
            print(f"Error getting case details: {e}")
            return {}

    def get_recent_filings(self, days: int = 7, county: str = "all", limit: int = 50) -> List[Dict]:
        """
        Search for cases with recent filings.

        Args:
            days: Number of days to look back
            county: County to search
            limit: Maximum cases to check

        Returns:
            List of docket entries from recent cases
        """
        from datetime import datetime, timedelta

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # Search for recently filed cases
        cases = self.search(
            last_name="",  # Empty for all
            county=county,
            filed_after=start_date.strftime("%m/%d/%Y"),
            filed_before=end_date.strftime("%m/%d/%Y"),
            limit=limit
        )

        recent_entries = []
        for case in cases[:20]:  # Limit detail fetches
            try:
                details = self.get_case_details(case.case_number, case.county.lower())
                for entry in details.get('docket_entries', []):
                    entry['case_number'] = case.case_number
                    entry['case_style'] = case.style
                    entry['county'] = case.county
                    entry['state'] = 'OK'
                    recent_entries.append(entry)
            except Exception as e:
                print(f"  Error fetching details for {case.case_number}: {e}")

        # Sort by date descending
        recent_entries.sort(key=lambda x: x.get('entry_date', ''), reverse=True)
        return recent_entries[:limit]


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
