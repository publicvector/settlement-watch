#!/usr/bin/env python3
"""
Nevada Appellate Courts Case Scraper

Scrapes case information from the Nevada Supreme Court and Court of Appeals
case search system at https://caseinfo.nvsupremecourt.us/public/caseSearch.do

This system does NOT require CAPTCHA and allows programmatic access.

Usage:
    python nevada_appellate_scraper.py --search "Smith"
    python nevada_appellate_scraper.py --case-number "92050"
    python nevada_appellate_scraper.py --last-name "Smith" --first-name "John"
"""

import argparse
import re
import sys
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


@dataclass
class NevadaCase:
    """Represents a Nevada appellate court case."""
    case_number: str
    case_id: str  # Internal ID for linking
    caption: str
    file_date: str
    case_type: str
    track: str
    origin: str
    status: str
    court: str
    detail_url: str


class NevadaAppellateScraper:
    """Scraper for Nevada Appellate Courts case information."""

    BASE_URL = "https://caseinfo.nvsupremecourt.us"
    SEARCH_URL = f"{BASE_URL}/public/caseSearch.do"
    PARTICIPANT_SEARCH_URL = f"{BASE_URL}/public/publicActorSearch.do"
    CASE_VIEW_URL = f"{BASE_URL}/public/caseView.do"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    def __init__(self, delay: float = 1.0):
        """
        Initialize the scraper.

        Args:
            delay: Delay between requests in seconds (be respectful)
        """
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.delay = delay
        self._last_request_time = 0

    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_time = time.time()

    def search_by_caption(
        self,
        caption: str,
        case_number: str = "",
        court_id: str = "-1",
        exclude_closed: bool = True,
        max_results: int = 50,
        start_row: int = 1,
    ) -> list[NevadaCase]:
        """
        Search cases by caption (case title/name).

        Args:
            caption: Text to search in case caption (e.g., party names)
            case_number: Specific case number to search for
            court_id: Court filter (-1=All, 10000=Court of Appeals, 10001=Supreme Court)
            exclude_closed: Whether to exclude closed cases
            max_results: Maximum number of results to return per page
            start_row: Starting row for pagination

        Returns:
            List of NevadaCase objects
        """
        self._rate_limit()

        data = {
            "action": "",
            "csNumber": case_number,
            "shortTitle": caption,
            "courtID": court_id,
            "startRow": str(start_row),
            "displayRows": str(max_results),
            "orderBy": "CsNumber",
            "orderDir": "DESC",
            "href": "/public/caseView.do",
            "submitValue": "Search",
        }

        if exclude_closed:
            data["exclude"] = "on"

        response = self.session.post(self.SEARCH_URL, data=data)
        response.raise_for_status()

        return self._parse_search_results(response.text)

    def search_by_case_number(self, case_number: str) -> list[NevadaCase]:
        """
        Search for a specific case by case number.

        Args:
            case_number: The 5-digit case number (e.g., "92050")

        Returns:
            List of NevadaCase objects (usually 1 or 0)
        """
        return self.search_by_caption(caption="", case_number=case_number)

    def search_by_participant(
        self,
        last_name: str,
        first_name: str = "",
        middle_name: str = "",
        court_id: str = "-1",
        exclude_closed: bool = True,
        max_results: int = 50,
        start_row: int = 1,
    ) -> list[NevadaCase]:
        """
        Search cases by participant name.

        Args:
            last_name: Participant's last name (required)
            first_name: Participant's first name (optional)
            middle_name: Participant's middle name (optional)
            court_id: Court filter (-1=All, 10000=Court of Appeals, 10001=Supreme Court)
            exclude_closed: Whether to exclude closed cases
            max_results: Maximum number of results per page
            start_row: Starting row for pagination

        Returns:
            List of NevadaCase objects
        """
        self._rate_limit()

        data = {
            "action": "",
            "lastNm": last_name,
            "firstNm": first_name,
            "middleNm": middle_name,
            "courtID": court_id,
            "startRow": str(start_row),
            "displayRows": str(max_results),
            "orderBy": "CsNumber",
            "orderDir": "DESC",
            "href": "/public/caseView.do",
            "submitValue": "Search",
        }

        if exclude_closed:
            data["exclude"] = "on"

        response = self.session.post(self.PARTICIPANT_SEARCH_URL, data=data)
        response.raise_for_status()

        return self._parse_search_results(response.text)

    def _parse_search_results(self, html: str) -> list[NevadaCase]:
        """
        Parse HTML search results into NevadaCase objects.

        Args:
            html: HTML content of the search results page

        Returns:
            List of NevadaCase objects
        """
        soup = BeautifulSoup(html, "html.parser")
        cases = []

        # Find all result rows - they have class "OddRow" or "EvenRow"
        for row in soup.find_all("tr", class_=re.compile(r"(OddRow|EvenRow)")):
            cells = row.find_all("td")
            if len(cells) < 8:
                continue

            # Look for the case link in the first cell
            link = cells[0].find("a", href=re.compile(r"caseView\.do"))
            if not link:
                continue

            # Extract case ID from the link
            href = link.get("href", "")
            case_id_match = re.search(r"csIID=(\d+)", href)
            case_id = case_id_match.group(1) if case_id_match else ""

            try:
                case = NevadaCase(
                    case_number=link.get_text(strip=True),
                    case_id=case_id,
                    caption=cells[1].get_text(strip=True) if len(cells) > 1 else "",
                    file_date=cells[2].get_text(strip=True) if len(cells) > 2 else "",
                    case_type=cells[3].get_text(strip=True) if len(cells) > 3 else "",
                    track=cells[4].get_text(strip=True) if len(cells) > 4 else "",
                    origin=cells[5].get_text(strip=True) if len(cells) > 5 else "",
                    status=cells[6].get_text(strip=True) if len(cells) > 6 else "",
                    court=cells[7].get_text(strip=True) if len(cells) > 7 else "",
                    detail_url=urljoin(self.BASE_URL, href) if href else "",
                )
                cases.append(case)
            except (IndexError, AttributeError):
                continue

        return cases

    def get_case_details(self, case_id: str) -> Optional[dict]:
        """
        Get detailed information about a specific case.

        Args:
            case_id: The internal case ID (csIID parameter)

        Returns:
            Dictionary with case details or None if not found
        """
        self._rate_limit()

        url = f"{self.CASE_VIEW_URL}?csIID={case_id}"
        response = self.session.get(url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        details = {
            "case_id": case_id,
            "url": url,
            "parties": [],
            "docket_entries": [],
        }

        # Parse case information tables
        for table in soup.find_all("table", class_="FormTable"):
            heading = table.find("tr", class_="TableHeading")
            if not heading:
                continue

            heading_text = heading.get_text(strip=True)

            if "Case Information" in heading_text:
                for row in table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        label = cells[0].get_text(strip=True).rstrip(":")
                        value = cells[1].get_text(strip=True)
                        if label:
                            details[label.lower().replace(" ", "_")] = value

            elif "Party" in heading_text or "Participant" in heading_text:
                for row in table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        party_type = cells[0].get_text(strip=True)
                        party_name = cells[1].get_text(strip=True)
                        if party_name and party_type:
                            details["parties"].append({
                                "type": party_type,
                                "name": party_name,
                            })

            elif "Docket" in heading_text:
                for row in table.find_all("tr")[1:]:  # Skip header
                    cells = row.find_all("td")
                    if len(cells) >= 3:
                        entry = {
                            "date": cells[0].get_text(strip=True) if len(cells) > 0 else "",
                            "description": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                            "filed_by": cells[2].get_text(strip=True) if len(cells) > 2 else "",
                        }
                        if entry["date"] or entry["description"]:
                            details["docket_entries"].append(entry)

        return details


def main():
    """Main entry point for the scraper."""
    parser = argparse.ArgumentParser(
        description="Scrape Nevada Appellate Courts case information",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Search by caption/party name
    python nevada_appellate_scraper.py --search "Smith"

    # Search by case number
    python nevada_appellate_scraper.py --case-number "92050"

    # Search by participant name
    python nevada_appellate_scraper.py --last-name "Smith" --first-name "John"

    # Get case details
    python nevada_appellate_scraper.py --case-id "73995"

    # Include closed cases
    python nevada_appellate_scraper.py --search "Smith" --include-closed
        """
    )

    parser.add_argument(
        "--search", "-s",
        help="Search term for case caption (party names, etc.)"
    )
    parser.add_argument(
        "--case-number", "-n",
        help="Specific case number to search for"
    )
    parser.add_argument(
        "--case-id", "-i",
        help="Internal case ID to get details for"
    )
    parser.add_argument(
        "--last-name", "-l",
        help="Participant last name for participant search"
    )
    parser.add_argument(
        "--first-name", "-f",
        help="Participant first name for participant search"
    )
    parser.add_argument(
        "--court",
        choices=["all", "supreme", "appeals"],
        default="all",
        help="Filter by court (default: all)"
    )
    parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Include closed cases in results"
    )
    parser.add_argument(
        "--max-results", "-m",
        type=int,
        default=50,
        help="Maximum number of results (default: 50)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between requests in seconds (default: 1.0)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )

    args = parser.parse_args()

    # Map court argument to court ID
    court_map = {
        "all": "-1",
        "supreme": "10001",
        "appeals": "10000",
    }
    court_id = court_map.get(args.court, "-1")

    scraper = NevadaAppellateScraper(delay=args.delay)

    if args.case_id:
        # Get case details
        details = scraper.get_case_details(args.case_id)
        if details:
            if args.json:
                import json
                print(json.dumps(details, indent=2))
            else:
                print(f"\nCase Details (ID: {args.case_id})")
                print("=" * 60)
                for key, value in details.items():
                    if key not in ["parties", "docket_entries"]:
                        print(f"{key}: {value}")

                if details.get("parties"):
                    print("\nParties:")
                    for party in details["parties"]:
                        print(f"  {party['type']}: {party['name']}")

                if details.get("docket_entries"):
                    print(f"\nDocket Entries ({len(details['docket_entries'])} total):")
                    for entry in details["docket_entries"][:10]:
                        print(f"  {entry['date']}: {entry['description']}")
                    if len(details["docket_entries"]) > 10:
                        print(f"  ... and {len(details['docket_entries']) - 10} more")
        else:
            print(f"No case found with ID: {args.case_id}", file=sys.stderr)
            sys.exit(1)

    elif args.case_number:
        # Search by case number
        cases = scraper.search_by_case_number(args.case_number)
        _output_cases(cases, args.json)

    elif args.last_name:
        # Search by participant
        cases = scraper.search_by_participant(
            last_name=args.last_name,
            first_name=args.first_name or "",
            court_id=court_id,
            exclude_closed=not args.include_closed,
            max_results=args.max_results,
        )
        _output_cases(cases, args.json)

    elif args.search:
        # Search by caption
        cases = scraper.search_by_caption(
            caption=args.search,
            court_id=court_id,
            exclude_closed=not args.include_closed,
            max_results=args.max_results,
        )
        _output_cases(cases, args.json)

    else:
        parser.print_help()
        sys.exit(1)


def _output_cases(cases: list[NevadaCase], as_json: bool = False):
    """Output cases in the requested format."""
    if as_json:
        import json
        from dataclasses import asdict
        print(json.dumps([asdict(c) for c in cases], indent=2))
    else:
        print(f"\nFound {len(cases)} case(s):")
        print("=" * 100)
        for case in cases:
            print(f"\nCase Number: {case.case_number}")
            print(f"Caption: {case.caption}")
            print(f"Filed: {case.file_date}")
            print(f"Type: {case.case_type}")
            print(f"Track: {case.track}")
            print(f"Origin: {case.origin}")
            print(f"Status: {case.status}")
            print(f"Court: {case.court}")
            print(f"URL: {case.detail_url}")
            print("-" * 100)


if __name__ == "__main__":
    main()
