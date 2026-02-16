#!/usr/bin/env python3
"""
SEC EDGAR Litigation Disclosure Scraper

Scrapes SEC filings (10-K, 8-K) for litigation disclosures and settlements.
Companies must disclose material litigation in:
- 10-K Item 3: Legal Proceedings
- 8-K Item 8.01: Other Events (often used for settlement announcements)

Uses the free SEC EDGAR APIs:
- Full-text search: https://efts.sec.gov/LATEST/search-index
- Company filings: https://data.sec.gov/submissions/
- Filing documents: https://www.sec.gov/Archives/edgar/data/

Rate limit: 10 requests per second
"""
import argparse
import json
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import requests
from bs4 import BeautifulSoup

# SEC API endpoints
FULL_TEXT_SEARCH = "https://efts.sec.gov/LATEST/search-index"
COMPANY_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
FILING_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"

# Required headers for SEC API
SEC_HEADERS = {
    'User-Agent': 'SettlementWatch research@example.com',  # SEC requires identification
    'Accept': 'application/json',
}


@dataclass
class LitigationDisclosure:
    """A litigation/settlement disclosure from SEC filing."""
    company_name: str
    cik: str
    ticker: Optional[str] = None
    filing_type: str = ""  # 10-K, 8-K, etc.
    filing_date: str = ""
    accession_number: str = ""
    filing_url: str = ""

    # Extracted litigation info
    settlement_amount: Optional[float] = None
    settlement_amount_formatted: Optional[str] = None
    case_name: Optional[str] = None
    case_number: Optional[str] = None
    court: Optional[str] = None
    description: str = ""

    # Context
    section: str = ""  # "Item 3", "Item 8.01", etc.
    raw_text: str = ""

    # Metadata
    source: str = "SEC EDGAR"
    extracted_at: str = field(default_factory=lambda: datetime.now().isoformat())


# Patterns for extracting litigation data
SETTLEMENT_PATTERNS = [
    r'settle[d]?\s+(?:for|at|the\s+amount\s+of)\s+\$?([\d,]+(?:\.\d+)?)\s*(?:million|billion|thousand)?',
    r'settlement\s+(?:of|for|amount(?:ing)?\s+to)\s+\$?([\d,]+(?:\.\d+)?)\s*(?:million|billion)?',
    r'paid?\s+\$?([\d,]+(?:\.\d+)?)\s*(?:million|billion)?\s+to\s+settle',
    r'settlement\s+fund\s+(?:of|to\s+cover)[^$]*\$([\d,]+(?:\.\d+)?)\s*(?:million|billion)?',
    r'\$([\d,]+(?:\.\d+)?)\s*(?:million|billion)?\s*(?:escrowed\s+)?settlement\s+fund',
    r'\$?([\d,]+(?:\.\d+)?)\s*(?:million|billion)\s+settlement',
    r'agree[d]?\s+to\s+pay\s+\$?([\d,]+(?:\.\d+)?)\s*(?:million|billion)?',
    r'establishing\s+a\s+\$([\d,]+(?:\.\d+)?)\s*(?:million|billion)?',
    r'contribute[d]?\s+(?:approximately\s+)?\$([\d,]+(?:\.\d+)?)\s*(?:million|billion)?',
]

CASE_NAME_PATTERNS = [
    r'(?:In\s+re|In\s+the\s+Matter\s+of)[:\s]+([^,\n\.]+)',
    r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+v\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
    r'(?:case|matter)\s+(?:entitled|styled|captioned)\s+["\']?([^"\']+)["\']?',
]

CASE_NUMBER_PATTERNS = [
    r'Case\s*(?:No\.?|Number|#)\s*:?\s*([A-Za-z0-9:-]+)',
    r'(\d{1,2}:\d{2}-cv-\d{4,6})',
    r'Civil\s+Action\s+No\.\s*([A-Za-z0-9:-]+)',
    r'Docket\s+No\.\s*([A-Za-z0-9:-]+)',
]

# Keywords for filtering relevant filings
LITIGATION_KEYWORDS = [
    'settlement', 'litigation', 'lawsuit', 'legal proceedings',
    'class action', 'plaintiff', 'defendant', 'judgment',
    'consent decree', 'injunction', 'damages', 'penalty',
    'fine', 'restitution', 'disgorgement',
]


class SECEdgarScraper:
    """Scraper for SEC EDGAR litigation disclosures."""

    def __init__(self, email: str = "research@example.com"):
        """
        Initialize scraper.

        Args:
            email: Email for SEC User-Agent (required by SEC)
        """
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': f'SettlementWatch {email}',
            'Accept': 'application/json, text/html',
        })
        self.disclosures: List[LitigationDisclosure] = []
        self._last_request = 0

    def _rate_limit(self):
        """Enforce SEC rate limit (10 req/sec)."""
        elapsed = time.time() - self._last_request
        if elapsed < 0.1:  # 100ms between requests
            time.sleep(0.1 - elapsed)
        self._last_request = time.time()

    def _get(self, url: str, params: dict = None) -> Optional[requests.Response]:
        """Make rate-limited GET request."""
        self._rate_limit()
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            print(f"  Error fetching {url}: {e}")
            return None

    def search_filings(self, query: str, form_types: List[str] = None,
                       start_date: str = None, end_date: str = None,
                       limit: int = 100) -> List[Dict]:
        """
        Search SEC filings using full-text search.

        Args:
            query: Search query (e.g., "settlement", "litigation")
            form_types: List of form types (e.g., ["10-K", "8-K"])
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            limit: Max results

        Returns:
            List of filing metadata dicts
        """
        # Use the SEC full-text search API
        params = {
            'q': query,
            'dateRange': 'custom',
            'startdt': start_date or (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d'),
            'enddt': end_date or datetime.now().strftime('%Y-%m-%d'),
            'from': 0,
            'size': min(limit, 100),
        }

        if form_types:
            params['forms'] = ','.join(form_types)

        resp = self._get(FULL_TEXT_SEARCH, params)
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                hits = data.get('hits', {}).get('hits', [])

                # Transform to standard format
                filings = []
                seen_accessions = set()

                for hit in hits:
                    source = hit.get('_source', {})
                    accession = source.get('adsh', '')

                    # Dedupe by accession number
                    if accession in seen_accessions:
                        continue
                    seen_accessions.add(accession)

                    ciks = source.get('ciks', [])
                    display_names = source.get('display_names', [])

                    filings.append({
                        'cik': ciks[0] if ciks else '',
                        'companyName': display_names[0].split('(')[0].strip() if display_names else '',
                        'tickers': [n.split('(')[1].replace(')', '').strip()
                                   for n in display_names if '(' in n][:1],
                        'form': source.get('form', source.get('root_forms', [''])[0]),
                        'filingDate': source.get('file_date', ''),
                        'accessionNumber': accession,
                        'primaryDocument': hit.get('_id', '').split(':')[-1] if ':' in hit.get('_id', '') else '',
                    })

                return filings
            except Exception as e:
                print(f"  Error parsing search results: {e}")

        return []

    def get_company_filings(self, cik: str, form_types: List[str] = None,
                            limit: int = 20) -> List[Dict]:
        """
        Get recent filings for a company by CIK.

        Args:
            cik: SEC CIK number (with or without leading zeros)
            form_types: Filter by form types
            limit: Max filings to return

        Returns:
            List of filing metadata
        """
        # Pad CIK to 10 digits
        cik_padded = cik.zfill(10)
        url = COMPANY_SUBMISSIONS.format(cik=cik_padded)

        resp = self._get(url)
        if not resp:
            return []

        try:
            data = resp.json()
        except:
            return []

        filings = []
        recent = data.get('filings', {}).get('recent', {})

        forms = recent.get('form', [])
        dates = recent.get('filingDate', [])
        accessions = recent.get('accessionNumber', [])
        primary_docs = recent.get('primaryDocument', [])

        for i in range(min(len(forms), limit * 3)):  # Check more to filter
            if form_types and forms[i] not in form_types:
                continue

            filings.append({
                'form': forms[i],
                'filingDate': dates[i],
                'accessionNumber': accessions[i],
                'primaryDocument': primary_docs[i] if i < len(primary_docs) else '',
                'cik': cik_padded,
                'companyName': data.get('name', ''),
                'tickers': data.get('tickers', []),
            })

            if len(filings) >= limit:
                break

        return filings

    def get_filing_document(self, cik: str, accession: str, document: str = None) -> Optional[str]:
        """
        Get the text content of a filing document.

        Args:
            cik: Company CIK
            accession: Accession number (e.g., "0001193125-24-012345")
            document: Specific document filename, or None for main filing

        Returns:
            Document text content
        """
        cik_padded = cik.zfill(10)
        accession_clean = accession.replace('-', '')

        # Build URL
        if document:
            url = f"{FILING_ARCHIVE}/{cik_padded}/{accession_clean}/{document}"
        else:
            # Try to get the main filing document
            url = f"{FILING_ARCHIVE}/{cik_padded}/{accession_clean}"

        resp = self._get(url)
        if resp:
            return resp.text
        return None

    def extract_legal_proceedings(self, html_content: str) -> List[str]:
        """
        Extract "Legal Proceedings" sections from filing HTML.

        Args:
            html_content: Filing HTML content

        Returns:
            List of text sections containing legal proceedings
        """
        soup = BeautifulSoup(html_content, 'html.parser')

        # Remove scripts and styles
        for tag in soup(['script', 'style']):
            tag.decompose()

        text = soup.get_text(' ', strip=True)

        sections = []

        # Look for Item 3 (Legal Proceedings in 10-K)
        item3_patterns = [
            r'(?:ITEM\s*3[.\s:]+|Item\s*3[.\s:]+)LEGAL\s+PROCEEDINGS(.*?)(?:ITEM\s*4|Item\s*4|$)',
            r'LEGAL\s+PROCEEDINGS(.*?)(?:ITEM\s*4|Item\s*4|MINE\s+SAFETY|$)',
        ]

        for pattern in item3_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.DOTALL)
            sections.extend(matches)

        # Look for Item 8.01 (Other Events in 8-K, often used for settlements)
        item8_patterns = [
            r'(?:ITEM\s*8\.01[.\s:]+|Item\s*8\.01[.\s:]+)(?:OTHER\s+EVENTS)?(.*?)(?:ITEM\s*9|Item\s*9|SIGNATURE|$)',
        ]

        for pattern in item8_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.DOTALL)
            sections.extend(matches)

        # Also look for explicit settlement mentions
        settlement_patterns = [
            r'(?:settlement|litigation|lawsuit|legal\s+proceeding)[^.]*\$[\d,]+(?:\.\d+)?\s*(?:million|billion)?[^.]*\.',
        ]

        for pattern in settlement_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            sections.extend(matches)

        # Clean up sections
        cleaned = []
        for section in sections:
            section = re.sub(r'\s+', ' ', section).strip()
            if len(section) > 50:  # Skip very short matches
                cleaned.append(section[:5000])  # Limit length

        return cleaned

    def extract_settlement_amount(self, text: str) -> Tuple[Optional[float], Optional[str]]:
        """Extract settlement amount from text."""
        for pattern in SETTLEMENT_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                amount_str = match.group(1).replace(',', '')
                try:
                    amount = float(amount_str)
                    # Check for multiplier
                    full_match = match.group(0).lower()
                    if 'billion' in full_match:
                        amount *= 1_000_000_000
                    elif 'million' in full_match:
                        amount *= 1_000_000
                    elif 'thousand' in full_match:
                        amount *= 1_000

                    # Format
                    if amount >= 1_000_000_000:
                        formatted = f"${amount/1_000_000_000:.1f}B"
                    elif amount >= 1_000_000:
                        formatted = f"${amount/1_000_000:.1f}M"
                    else:
                        formatted = f"${amount:,.0f}"

                    return amount, formatted
                except ValueError:
                    continue

        return None, None

    def extract_case_info(self, text: str) -> Dict:
        """Extract case name and number from text."""
        info = {}

        for pattern in CASE_NAME_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info['case_name'] = match.group(0)[:100]
                break

        for pattern in CASE_NUMBER_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info['case_number'] = match.group(1)
                break

        return info

    def process_filing(self, filing: Dict) -> List[LitigationDisclosure]:
        """
        Process a single filing to extract litigation disclosures.

        Args:
            filing: Filing metadata dict

        Returns:
            List of extracted disclosures
        """
        cik = filing.get('cik', '').replace('0', '', 1) if filing.get('cik', '').startswith('000') else filing.get('cik', '')
        cik = cik.zfill(10)
        accession = filing.get('accessionNumber', '')
        primary_doc = filing.get('primaryDocument', '')

        if not accession or not cik:
            return []

        company = filing.get('companyName', 'Unknown')
        form_type = filing.get('form', '')

        print(f"    Processing {company[:30]} - {form_type} ({filing.get('filingDate')})...")

        # Get filing content - try primary doc first
        content = None
        if primary_doc and primary_doc.endswith('.htm'):
            content = self.get_filing_document(cik, accession, primary_doc)

        if not content:
            # Try index page to find main document
            accession_clean = accession.replace('-', '')
            index_url = f"{FILING_ARCHIVE}/{cik}/{accession_clean}/"
            resp = self._get(index_url)
            if resp:
                # Parse index to find .htm file
                soup = BeautifulSoup(resp.text, 'html.parser')
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    # Look for main filing document (usually largest .htm file)
                    if (href.endswith('.htm') and
                        'def' not in href.lower() and
                        'ex' not in href.lower()[:3]):
                        content = self.get_filing_document(cik, accession, href)
                        if content and len(content) > 10000:  # Meaningful content
                            break

        if not content:
            print(f"      Could not fetch filing content")
            return []

        # Extract legal proceedings sections
        sections = self.extract_legal_proceedings(content)

        disclosures = []
        for section in sections:
            # Check if this section mentions settlements/litigation
            has_litigation_keywords = any(kw in section.lower() for kw in LITIGATION_KEYWORDS)
            if not has_litigation_keywords:
                continue

            # Extract settlement amount
            amount, formatted = self.extract_settlement_amount(section)

            # Extract case info
            case_info = self.extract_case_info(section)

            # Only create disclosure if we found meaningful data
            if amount or case_info:
                disclosure = LitigationDisclosure(
                    company_name=filing.get('companyName', ''),
                    cik=cik,
                    ticker=filing.get('tickers', [None])[0] if filing.get('tickers') else None,
                    filing_type=filing.get('form', ''),
                    filing_date=filing.get('filingDate', ''),
                    accession_number=accession,
                    filing_url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/",
                    settlement_amount=amount,
                    settlement_amount_formatted=formatted,
                    case_name=case_info.get('case_name'),
                    case_number=case_info.get('case_number'),
                    description=section[:500],
                    raw_text=section,
                )
                disclosures.append(disclosure)

        return disclosures

    def search_recent_settlements(self, days: int = 30, limit: int = 50) -> List[LitigationDisclosure]:
        """
        Search for recent settlement disclosures.

        Args:
            days: Look back this many days
            limit: Max disclosures to return

        Returns:
            List of LitigationDisclosure objects
        """
        print(f"Searching SEC EDGAR for settlement disclosures (last {days} days)...")

        all_disclosures = []

        # Search for settlement-related filings
        search_terms = ['settlement', 'litigation settlement', 'class action settlement']

        for term in search_terms:
            print(f"\n  Searching: '{term}'")

            filings = self.search_filings(
                query=term,
                form_types=['10-K', '8-K', '10-Q'],
                start_date=(datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d'),
                end_date=datetime.now().strftime('%Y-%m-%d'),
                limit=20,
            )

            print(f"    Found {len(filings)} filings")

            for filing_hit in filings[:10]:
                filing = filing_hit.get('_source', filing_hit)
                disclosures = self.process_filing(filing)
                all_disclosures.extend(disclosures)

                if len(all_disclosures) >= limit:
                    break

            if len(all_disclosures) >= limit:
                break

        # Dedupe by accession number + amount
        seen = set()
        unique = []
        for d in all_disclosures:
            key = (d.accession_number, d.settlement_amount)
            if key not in seen:
                seen.add(key)
                unique.append(d)

        self.disclosures = unique[:limit]
        return self.disclosures

    def search_company_settlements(self, company: str, cik: str = None) -> List[LitigationDisclosure]:
        """
        Search for settlement disclosures by company.

        Args:
            company: Company name or ticker
            cik: Optional CIK number

        Returns:
            List of disclosures
        """
        if not cik:
            # Try to find CIK by searching
            print(f"Looking up CIK for {company}...")
            # This would require a company lookup - for now just return empty
            return []

        print(f"Searching filings for CIK {cik}...")

        filings = self.get_company_filings(cik, form_types=['10-K', '8-K', '10-Q'], limit=20)
        print(f"  Found {len(filings)} recent filings")

        all_disclosures = []
        for filing in filings:
            disclosures = self.process_filing(filing)
            all_disclosures.extend(disclosures)

        self.disclosures = all_disclosures
        return all_disclosures

    def to_json(self) -> str:
        """Export disclosures to JSON."""
        return json.dumps([asdict(d) for d in self.disclosures], indent=2, default=str)


def import_to_database(disclosures: List[LitigationDisclosure]) -> int:
    """Import SEC disclosures to database as case outcomes."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from db.database import get_db

    db = get_db()
    count = 0

    for d in disclosures:
        if not d.settlement_amount:
            continue

        outcome = {
            'case_number': d.case_number,
            'case_title': d.case_name or f"{d.company_name} Settlement",
            'court': d.court,
            'jurisdiction': 'federal',  # SEC filings are federal
            'settlement_amount': d.settlement_amount,
            'settlement_amount_formatted': d.settlement_amount_formatted,
            'settlement_url': d.filing_url,
            'defendant': d.company_name,
            'source': 'SEC EDGAR',
            'raw_data': {
                'cik': d.cik,
                'ticker': d.ticker,
                'filing_type': d.filing_type,
                'filing_date': d.filing_date,
                'accession_number': d.accession_number,
                'description': d.description,
            },
            'guid': f"sec-{d.accession_number}-{d.settlement_amount}",
        }

        try:
            db.add_case_outcome(outcome)
            count += 1
        except Exception as e:
            print(f"Error adding disclosure: {e}")

    return count


def main():
    parser = argparse.ArgumentParser(description='SEC EDGAR Litigation Disclosure Scraper')
    parser.add_argument('--days', '-d', type=int, default=30, help='Days to look back')
    parser.add_argument('--limit', '-l', type=int, default=20, help='Max disclosures')
    parser.add_argument('--company', '-c', help='Search specific company')
    parser.add_argument('--cik', help='Company CIK number')
    parser.add_argument('--output', '-o', help='Output JSON file')
    parser.add_argument('--import-db', action='store_true', help='Import to database')
    parser.add_argument('--email', '-e', default='research@example.com', help='Email for SEC User-Agent')

    args = parser.parse_args()

    print("=" * 70)
    print("SEC EDGAR LITIGATION DISCLOSURE SCRAPER")
    print("=" * 70)

    scraper = SECEdgarScraper(email=args.email)

    if args.company or args.cik:
        disclosures = scraper.search_company_settlements(args.company or '', args.cik)
    else:
        disclosures = scraper.search_recent_settlements(days=args.days, limit=args.limit)

    print(f"\n{'=' * 70}")
    print(f"RESULTS: {len(disclosures)} litigation disclosures found")
    print("=" * 70)

    # Show disclosures with amounts
    with_amounts = [d for d in disclosures if d.settlement_amount]
    if with_amounts:
        print(f"\nDISCLOSURES WITH SETTLEMENT AMOUNTS ({len(with_amounts)}):")
        print("-" * 50)

        for d in sorted(with_amounts, key=lambda x: x.settlement_amount or 0, reverse=True)[:15]:
            print(f"\n  {d.company_name} ({d.ticker or 'N/A'})")
            print(f"    Amount: {d.settlement_amount_formatted}")
            print(f"    Filing: {d.filing_type} ({d.filing_date})")
            if d.case_name:
                print(f"    Case: {d.case_name[:60]}...")
            print(f"    URL: {d.filing_url}")

    # Save results
    output_file = args.output or '/tmp/sec_disclosures.json'
    with open(output_file, 'w') as f:
        f.write(scraper.to_json())
    print(f"\nSaved to {output_file}")

    # Import to database
    if args.import_db:
        print("\nImporting to database...")
        count = import_to_database(disclosures)
        print(f"Imported {count} disclosures")

    return disclosures


if __name__ == "__main__":
    main()
