#!/usr/bin/env python3
"""
RECAP/CourtListener Client

Fetches complaint documents and docket information from the RECAP Archive
via the CourtListener API.

https://www.courtlistener.com/api/rest/v4/

API Token: Get one at https://www.courtlistener.com/profile/
Free accounts: 5,000 requests/day
"""
import os
import re
import json
import requests
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from datetime import datetime

# CourtListener API v4 endpoints
API_BASE = "https://www.courtlistener.com/api/rest/v4"
DOCKETS_URL = f"{API_BASE}/dockets/"
DOCKET_ENTRIES_URL = f"{API_BASE}/docket-entries/"
RECAP_DOCS_URL = f"{API_BASE}/recap-documents/"
SEARCH_URL = f"{API_BASE}/search/"

# Court ID mappings (PACER court codes to CourtListener IDs)
# See: https://www.courtlistener.com/api/rest/v4/courts/
COURT_MAPPINGS = {
    # District courts (partial list - expand as needed)
    'cacd': 'cacd',  # Central District of California
    'cand': 'cand',  # Northern District of California
    'casd': 'casd',  # Southern District of California
    'caed': 'caed',  # Eastern District of California
    'nysd': 'nysd',  # Southern District of New York
    'nyed': 'nyed',  # Eastern District of New York
    'nynd': 'nynd',  # Northern District of New York
    'nywd': 'nywd',  # Western District of New York
    'ilnd': 'ilnd',  # Northern District of Illinois
    'txsd': 'txsd',  # Southern District of Texas
    'txnd': 'txnd',  # Northern District of Texas
    'txed': 'txed',  # Eastern District of Texas
    'txwd': 'txwd',  # Western District of Texas
    'mad': 'mad',    # District of Massachusetts
    'njd': 'njd',    # District of New Jersey
    'paed': 'paed',  # Eastern District of Pennsylvania
    'pawd': 'pawd',  # Western District of Pennsylvania
    'dcd': 'dcd',    # District of Columbia
    'flsd': 'flsd',  # Southern District of Florida
    'flmd': 'flmd',  # Middle District of Florida
    'flnd': 'flnd',  # Northern District of Florida
    'gasd': 'gasd',  # Southern District of Georgia
    'gand': 'gand',  # Northern District of Georgia
    'moed': 'mowd',  # Western District of Missouri
    'mowd': 'mowd',  # Western District of Missouri
}


@dataclass
class DocketEntry:
    """A single docket entry."""
    entry_number: int
    date_filed: str
    description: str
    document_url: Optional[str] = None
    pdf_url: Optional[str] = None
    page_count: Optional[int] = None
    is_complaint: bool = False
    is_available: bool = False


@dataclass
class DocketInfo:
    """Docket information from RECAP."""
    case_number: str
    case_name: str
    court: str
    court_id: str
    date_filed: Optional[str] = None
    date_terminated: Optional[str] = None
    nature_of_suit: Optional[str] = None
    cause: Optional[str] = None
    jury_demand: Optional[str] = None
    docket_url: Optional[str] = None
    pacer_case_id: Optional[str] = None
    entries: List[DocketEntry] = None
    complaint_entry: Optional[DocketEntry] = None

    def __post_init__(self):
        if self.entries is None:
            self.entries = []


class RECAPClient:
    """Client for the CourtListener/RECAP API."""

    def __init__(self, api_token: str = None):
        """
        Initialize RECAP client.

        Args:
            api_token: CourtListener API token. If not provided, will try
                      to read from COURTLISTENER_API_TOKEN env var.
        """
        self.api_token = api_token or os.environ.get('COURTLISTENER_API_TOKEN', '')
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SettlementWatch/1.0 (https://github.com/publicvector/settlement-watch)',
        })
        if self.api_token:
            self.session.headers['Authorization'] = f'Token {self.api_token}'

    def _get(self, url: str, params: dict = None) -> Optional[dict]:
        """Make GET request to API."""
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"API error: {e}")
            return None

    def parse_case_number(self, case_number: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse a federal case number to extract court and docket number.

        Examples:
            "2:21-cv-06775-JS-SIL" -> ("nyed", "2:21-cv-06775")
            "1:19-cv-00332-SRB" -> ("mowd", "1:19-cv-00332")
            "19-CV-00332-SRB" -> (None, "19-cv-00332")

        Returns:
            (court_id, docket_number) tuple
        """
        if not case_number:
            return None, None

        # Normalize
        case_number = case_number.strip().lower()

        # Pattern: division:year-type-number(-judge)
        # e.g., 2:21-cv-06775-JS-SIL
        match = re.match(r'(\d+):(\d{2})-([a-z]{2,3})-(\d+)(?:-[a-z]+)*', case_number, re.IGNORECASE)
        if match:
            division, year, case_type, number = match.groups()
            docket_number = f"{division}:{year}-{case_type}-{number}"
            return None, docket_number  # Court needs to be determined separately

        # Pattern without division: year-type-number
        match = re.match(r'(\d{2})-([a-z]{2,3})-(\d+)(?:-[a-z]+)*', case_number, re.IGNORECASE)
        if match:
            year, case_type, number = match.groups()
            docket_number = f"{year}-{case_type}-{number}"
            return None, docket_number

        return None, case_number

    def search_docket(self, case_number: str, court: str = None) -> List[dict]:
        """
        Search for a docket by case number.

        Args:
            case_number: Federal case number (e.g., "2:21-cv-06775")
            court: Optional court ID to narrow search

        Returns:
            List of matching docket results
        """
        # Clean up case number
        _, docket_num = self.parse_case_number(case_number)
        if not docket_num:
            docket_num = case_number

        params = {
            'type': 'r',  # RECAP type
            'docket_number': docket_num,
        }
        if court:
            params['court'] = court

        data = self._get(SEARCH_URL, params)
        if data and 'results' in data:
            return data['results']
        return []

    def get_docket(self, docket_id: int) -> Optional[DocketInfo]:
        """
        Get full docket information by ID.

        Args:
            docket_id: CourtListener docket ID

        Returns:
            DocketInfo with entries, or None if not found
        """
        url = f"{DOCKETS_URL}{docket_id}/"
        data = self._get(url)
        if not data:
            return None

        # Parse docket info
        docket = DocketInfo(
            case_number=data.get('docket_number', ''),
            case_name=data.get('case_name', ''),
            court=data.get('court', ''),
            court_id=data.get('court_id', ''),
            date_filed=data.get('date_filed'),
            date_terminated=data.get('date_terminated'),
            nature_of_suit=data.get('nature_of_suit'),
            cause=data.get('cause'),
            jury_demand=data.get('jury_demand'),
            docket_url=f"https://www.courtlistener.com/docket/{docket_id}/",
            pacer_case_id=data.get('pacer_case_id'),
        )

        # Get docket entries
        entries_url = data.get('docket_entries', f"{DOCKET_ENTRIES_URL}?docket={docket_id}")
        entries_data = self._get(entries_url if isinstance(entries_url, str) else f"{DOCKET_ENTRIES_URL}?docket={docket_id}")

        if entries_data and 'results' in entries_data:
            for entry in entries_data['results']:
                docket_entry = self._parse_entry(entry)
                docket.entries.append(docket_entry)

                # Check if this is the complaint
                if docket_entry.is_complaint and not docket.complaint_entry:
                    docket.complaint_entry = docket_entry

        return docket

    def _parse_entry(self, entry: dict) -> DocketEntry:
        """Parse a docket entry from API response."""
        description = entry.get('description', '')
        entry_num = entry.get('entry_number', 0)

        # Check if this looks like a complaint
        desc_lower = description.lower()
        is_complaint = (
            entry_num == 1 or
            'complaint' in desc_lower or
            'petition' in desc_lower or
            'initial filing' in desc_lower
        ) and 'amended' not in desc_lower

        # Check for available documents
        recap_docs = entry.get('recap_documents', [])
        doc_url = None
        pdf_url = None
        page_count = None
        is_available = False

        for doc in recap_docs:
            if doc.get('is_available'):
                is_available = True
                doc_url = doc.get('absolute_url')
                if doc_url and not doc_url.startswith('http'):
                    doc_url = f"https://www.courtlistener.com{doc_url}"
                # PDF URL
                filepath = doc.get('filepath_local')
                if filepath:
                    pdf_url = f"https://storage.courtlistener.com/{filepath}"
                page_count = doc.get('page_count')
                break

        return DocketEntry(
            entry_number=entry_num,
            date_filed=entry.get('date_filed', ''),
            description=description,
            document_url=doc_url,
            pdf_url=pdf_url,
            page_count=page_count,
            is_complaint=is_complaint,
            is_available=is_available,
        )

    def find_complaint(self, case_number: str, court: str = None) -> Optional[DocketEntry]:
        """
        Find the complaint document for a case.

        Args:
            case_number: Federal case number
            court: Optional court ID

        Returns:
            DocketEntry for the complaint, or None
        """
        # Search for the docket
        results = self.search_docket(case_number, court)
        if not results:
            print(f"  No docket found for {case_number}")
            return None

        # Get the first matching docket
        docket_id = results[0].get('docket_id')
        if not docket_id:
            return None

        # Get full docket with entries
        docket = self.get_docket(docket_id)
        if not docket:
            return None

        # Return the complaint entry
        if docket.complaint_entry:
            return docket.complaint_entry

        # If no explicit complaint found, look through entries
        for entry in docket.entries[:10]:  # Check first 10 entries
            if entry.is_complaint and entry.is_available:
                return entry

        return None

    def get_complaints_for_cases(self, case_numbers: List[str]) -> Dict[str, DocketEntry]:
        """
        Batch lookup complaints for multiple cases.

        Args:
            case_numbers: List of federal case numbers

        Returns:
            Dict mapping case_number -> DocketEntry
        """
        results = {}
        for case_num in case_numbers:
            print(f"  Looking up: {case_num}")
            complaint = self.find_complaint(case_num)
            if complaint:
                results[case_num] = complaint
                print(f"    Found: {complaint.description[:60]}...")
                if complaint.pdf_url:
                    print(f"    PDF: {complaint.pdf_url}")
            else:
                print(f"    Not found in RECAP")
        return results


@dataclass
class RECAPSearchResult:
    """Result from RECAP search (no auth required)."""
    case_number: str
    case_name: str
    court: str
    date_filed: str
    docket_id: int
    docket_url: str
    nature_of_suit: Optional[str] = None
    cause: Optional[str] = None


def search_recap_for_cases(case_numbers: List[str]) -> Dict[str, RECAPSearchResult]:
    """
    Search RECAP for cases (no auth required).

    Returns basic docket info and CourtListener URLs.
    """
    client = RECAPClient()
    results = {}

    for case_num in case_numbers:
        print(f"  Searching RECAP: {case_num}")
        search_results = client.search_docket(case_num)

        if search_results:
            r = search_results[0]  # Take first match
            docket_id = r.get('docket_id')
            result = RECAPSearchResult(
                case_number=r.get('docketNumber', case_num),
                case_name=r.get('caseName', ''),
                court=r.get('court', ''),
                date_filed=r.get('dateFiled', ''),
                docket_id=docket_id,
                docket_url=f"https://www.courtlistener.com/docket/{docket_id}/",
                nature_of_suit=r.get('suitNature'),
                cause=r.get('cause'),
            )
            results[case_num] = result
            print(f"    Found: {result.case_name[:50]}...")
            print(f"    URL: {result.docket_url}")
        else:
            print(f"    Not found in RECAP")

    return results


def enrich_case_outcomes_with_recap(update_db: bool = False):
    """
    Enrich case_outcomes table with RECAP docket URLs and info.
    Works without API token (uses search results).

    Args:
        update_db: If True, actually update the database
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from db.database import get_db

    db = get_db()

    # Get outcomes with case numbers
    outcomes = db.get_case_outcomes(limit=100)
    to_lookup = [o for o in outcomes if o.get('case_number')]

    if not to_lookup:
        print("No cases with case numbers to look up")
        return 0

    case_numbers = [o['case_number'] for o in to_lookup]
    print(f"Looking up {len(case_numbers)} cases in RECAP...")

    # Search RECAP
    recap_results = search_recap_for_cases(case_numbers)

    print(f"\nFound {len(recap_results)} cases in RECAP")

    # Update database and show results
    updated = 0
    for case_num, result in recap_results.items():
        print(f"\n  {case_num}:")
        print(f"    Case: {result.case_name}")
        print(f"    Court: {result.court}")
        print(f"    Filed: {result.date_filed}")
        print(f"    URL: {result.docket_url}")
        if result.nature_of_suit:
            print(f"    Nature: {result.nature_of_suit}")

        if update_db:
            # Build update data
            updates = {
                'complaint_url': result.docket_url,  # Link to full docket (contains complaint)
            }
            if result.date_filed:
                updates['complaint_date'] = result.date_filed
            if result.nature_of_suit:
                updates['nature_of_suit'] = result.nature_of_suit
            if result.court:
                updates['court'] = result.court
            if result.case_name:
                updates['case_title'] = result.case_name

            if db.update_case_outcome_by_case_number(case_num, updates):
                print(f"    -> Updated in database")
                updated += 1
            else:
                print(f"    -> Update failed")

    if update_db:
        print(f"\nUpdated {updated} cases in database")

    return len(recap_results)


def enrich_case_outcomes_with_complaints():
    """
    Enrich case_outcomes table with complaint documents from RECAP.
    Requires API token for full document access.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from db.database import get_db

    db = get_db()
    client = RECAPClient()

    if not client.api_token:
        print("Warning: No API token set. Set COURTLISTENER_API_TOKEN for full access.")
        print("Falling back to search-only mode...")
        return enrich_case_outcomes_with_recap()

    # Get outcomes with case numbers but no complaint URLs
    outcomes = db.get_case_outcomes(limit=100)
    to_update = [o for o in outcomes if o.get('case_number') and not o.get('complaint_url')]

    print(f"Found {len(to_update)} cases to look up in RECAP")

    updated = 0
    for outcome in to_update:
        case_num = outcome['case_number']
        print(f"\nLooking up: {case_num}")

        complaint = client.find_complaint(case_num)
        if complaint and (complaint.document_url or complaint.pdf_url):
            # Update the outcome with complaint info
            update_data = {
                'complaint_url': complaint.document_url,
                'complaint_pdf_url': complaint.pdf_url,
            }
            if complaint.date_filed:
                update_data['complaint_date'] = complaint.date_filed

            print(f"  Found complaint: {complaint.description[:50]}...")
            print(f"  URL: {complaint.document_url or complaint.pdf_url}")
            updated += 1

    print(f"\nEnriched {updated} cases with complaint documents")
    return updated


def main():
    import argparse

    parser = argparse.ArgumentParser(description='RECAP/CourtListener Client')
    parser.add_argument('--case', '-c', help='Look up a specific case number')
    parser.add_argument('--enrich', action='store_true', help='Enrich case_outcomes with RECAP data')
    parser.add_argument('--update', action='store_true', help='Actually update the database (with --enrich)')
    parser.add_argument('--token', '-t', help='CourtListener API token')

    args = parser.parse_args()

    print("=" * 70)
    print("RECAP/COURTLISTENER CLIENT")
    print("=" * 70)

    if args.token:
        os.environ['COURTLISTENER_API_TOKEN'] = args.token

    client = RECAPClient()

    if args.case:
        print(f"\nLooking up case: {args.case}")
        results = client.search_docket(args.case)

        if results:
            print(f"\nFound {len(results)} matching dockets:")
            for r in results[:5]:
                case_name = r.get('caseName', 'Unknown')
                docket_num = r.get('docketNumber', 'N/A')
                court = r.get('court', 'N/A')
                date_filed = r.get('dateFiled', 'N/A')
                docket_id = r.get('docket_id')

                print(f"\n  {case_name}")
                print(f"  Docket: {docket_num}")
                print(f"  Court: {court}")
                print(f"  Filed: {date_filed}")
                print(f"  CourtListener URL: https://www.courtlistener.com/docket/{docket_id}/")

                # Try to get full docket (requires auth)
                if docket_id and client.api_token:
                    docket = client.get_docket(docket_id)
                    if docket and docket.complaint_entry:
                        print(f"\n  COMPLAINT FOUND:")
                        print(f"    Entry #{docket.complaint_entry.entry_number}: {docket.complaint_entry.description[:60]}...")
                        if docket.complaint_entry.pdf_url:
                            print(f"    PDF: {docket.complaint_entry.pdf_url}")
                        elif docket.complaint_entry.document_url:
                            print(f"    URL: {docket.complaint_entry.document_url}")
                elif docket_id:
                    print(f"  (Full docket details require API token)")
                    print(f"  Get token at: https://www.courtlistener.com/sign-in/")
        else:
            print("  No results found")

    elif args.enrich:
        if client.api_token:
            enrich_case_outcomes_with_complaints()
        else:
            enrich_case_outcomes_with_recap(update_db=args.update)

    else:
        # Demo with some example case numbers
        print("\nDemo: Looking up sample cases...")

        demo_cases = [
            "2:21-cv-06775",  # Earth Rated (EDNY)
            "4:19-cv-00332",  # NAR case (MOWD)
        ]

        for case_num in demo_cases:
            print(f"\n{'='*50}")
            print(f"Case: {case_num}")
            print("=" * 50)

            complaint = client.find_complaint(case_num)
            if complaint:
                print(f"  Entry #{complaint.entry_number}")
                print(f"  Date: {complaint.date_filed}")
                print(f"  Description: {complaint.description[:80]}...")
                if complaint.pdf_url:
                    print(f"  PDF: {complaint.pdf_url}")
                elif complaint.document_url:
                    print(f"  URL: {complaint.document_url}")
                print(f"  Available: {complaint.is_available}")
            else:
                print("  Complaint not found in RECAP")


if __name__ == "__main__":
    main()
