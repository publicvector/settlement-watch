"""CourtListener/RECAP API client for enriching case data"""
import os
import requests
from typing import Optional, List, Dict, Any
from urllib.parse import urlencode

# Get token from environment
COURTLISTENER_TOKEN = (os.getenv("COURTLISTENER_TOKEN") or "").strip()


class CourtListenerClient:
    """Client for CourtListener REST API v4"""

    BASE_URL = "https://www.courtlistener.com/api/rest/v4"

    def __init__(self, token: str = None):
        self.token = token or COURTLISTENER_TOKEN
        self.session = requests.Session()
        if self.token:
            self.session.headers["Authorization"] = f"Token {self.token}"
        self.session.headers["User-Agent"] = "PACER-RSS-Demo/1.0"

    def _get(self, endpoint: str, params: dict = None) -> dict:
        """Make GET request to API"""
        url = f"{self.BASE_URL}/{endpoint}"
        if params:
            url = f"{url}?{urlencode(params)}"
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _get_paginated(self, endpoint: str, params: dict = None, max_results: int = 100) -> List[dict]:
        """Get all results from paginated endpoint"""
        results = []
        params = params or {}
        params["page_size"] = min(max_results, 100)

        while len(results) < max_results:
            data = self._get(endpoint, params)
            results.extend(data.get("results", []))

            # Check for next page
            next_url = data.get("next")
            if not next_url:
                break

            # Extract page number from next URL
            if "page=" in next_url:
                import re
                match = re.search(r'page=(\d+)', next_url)
                if match:
                    params["page"] = int(match.group(1))
                else:
                    break
            else:
                break

        return results[:max_results]

    # --- Docket Methods ---

    def search_dockets(
        self,
        court: str = None,
        docket_number: str = None,
        case_name: str = None,
        party_name: str = None,
        filed_after: str = None,
        filed_before: str = None,
        limit: int = 20
    ) -> List[dict]:
        """
        Search for dockets using the search API.

        Args:
            court: Court code (e.g., 'nysd', 'cacd')
            docket_number: Case number (e.g., '1:25-cv-00001')
            case_name: Search in case name
            party_name: Search for party name
            filed_after: ISO date string
            filed_before: ISO date string
            limit: Max results to return

        Returns:
            List of docket search results
        """
        params = {"type": "d"}

        if court:
            params["court"] = court
        if docket_number:
            params["docket_number"] = docket_number
        if case_name:
            params["case_name"] = case_name
        if party_name:
            params["party_name"] = party_name
        if filed_after:
            params["filed_after"] = filed_after
        if filed_before:
            params["filed_before"] = filed_before

        return self._get_paginated("search/", params, max_results=limit)

    def get_docket(self, docket_id: int) -> dict:
        """
        Get full docket details by CourtListener docket ID.

        Returns docket with case metadata, judge info, nature of suit, etc.
        """
        return self._get(f"dockets/{docket_id}/")

    def get_docket_by_court_and_number(self, court: str, docket_number: str) -> Optional[dict]:
        """
        Find and return a docket by court code and docket number.

        Args:
            court: Court code (e.g., 'nysd')
            docket_number: Case number (e.g., '1:25-cv-00001')

        Returns:
            Docket dict or None if not found
        """
        results = self.search_dockets(court=court, docket_number=docket_number, limit=1)
        if results:
            # Search returns minimal data, fetch full docket
            docket_id = results[0].get("docket_id")
            if docket_id:
                return self.get_docket(docket_id)
        return None

    # --- Docket Entry Methods ---

    def get_docket_entries(self, docket_id: int, limit: int = 500) -> List[dict]:
        """
        Get docket entries for a docket.

        Args:
            docket_id: CourtListener docket ID
            limit: Max entries to return

        Returns:
            List of docket entry objects
        """
        return self._get_paginated(
            "docket-entries/",
            {"docket": docket_id, "order_by": "date_filed"},
            max_results=limit
        )

    # --- Party Methods ---

    def get_parties(self, docket_id: int, limit: int = 100) -> List[dict]:
        """
        Get parties for a docket.

        Args:
            docket_id: CourtListener docket ID
            limit: Max parties to return

        Returns:
            List of party objects with type, name, attorneys
        """
        return self._get_paginated(
            "parties/",
            {"docket": docket_id},
            max_results=limit
        )

    def search_parties(self, name: str, court: str = None, limit: int = 50) -> List[dict]:
        """
        Search for parties by name.

        Args:
            name: Party name to search
            court: Optional court code to filter
            limit: Max results

        Returns:
            List of party objects
        """
        params = {"name__icontains": name}
        if court:
            params["docket__court"] = court
        return self._get_paginated("parties/", params, max_results=limit)

    # --- Attorney Methods ---

    def get_attorneys(self, docket_id: int = None, party_id: int = None, limit: int = 100) -> List[dict]:
        """
        Get attorneys, optionally filtered by docket or party.

        Args:
            docket_id: Filter by docket
            party_id: Filter by party
            limit: Max results

        Returns:
            List of attorney objects
        """
        params = {}
        if docket_id:
            params["parties__docket"] = docket_id
        if party_id:
            params["parties"] = party_id
        return self._get_paginated("attorneys/", params, max_results=limit)

    # --- Document Methods ---

    def get_recap_documents(self, docket_entry_id: int) -> List[dict]:
        """
        Get RECAP documents for a docket entry.

        Args:
            docket_entry_id: CourtListener docket entry ID

        Returns:
            List of document objects with availability, page count, etc.
        """
        return self._get_paginated(
            "recap-documents/",
            {"docket_entry": docket_entry_id},
            max_results=50
        )

    def get_document(self, document_id: int) -> dict:
        """
        Get single RECAP document metadata.

        Args:
            document_id: CourtListener document ID

        Returns:
            Document object with filepath_local, is_available, etc.
        """
        return self._get(f"recap-documents/{document_id}/")

    def download_document_pdf(self, document_id: int) -> Optional[bytes]:
        """
        Download PDF for a RECAP document if available.

        Args:
            document_id: CourtListener document ID

        Returns:
            PDF bytes or None if not available
        """
        doc = self.get_document(document_id)
        filepath = doc.get("filepath_local")
        if not filepath or not doc.get("is_available"):
            return None

        # filepath_local is relative path, construct full URL
        pdf_url = f"https://storage.courtlistener.com/{filepath}"
        resp = self.session.get(pdf_url, timeout=60)
        if resp.status_code == 200 and resp.content[:4] == b'%PDF':
            return resp.content
        return None

    # --- Utility Methods ---

    def is_configured(self) -> bool:
        """Check if API token is configured"""
        return bool(self.token)

    def test_connection(self) -> dict:
        """Test API connection and return account info"""
        try:
            # Try a simple search to verify token works
            self._get("search/", {"type": "d", "page_size": 1})
            return {"status": "ok", "message": "Connection successful"}
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                return {"status": "error", "message": "Invalid API token"}
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# Module-level client instance (lazy initialization)
_client: Optional[CourtListenerClient] = None


def get_client() -> CourtListenerClient:
    """Get or create the CourtListener client"""
    global _client
    if _client is None:
        _client = CourtListenerClient()
    return _client
