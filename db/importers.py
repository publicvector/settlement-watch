#!/usr/bin/env python3
"""
Data importers for Settlement Watch
Import data from scrapers and dorker into the unified database.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import Database, Settlement, StateCase, FederalCase, get_db


class SettlementImporter:
    """Import settlements from dorker results."""

    def __init__(self, db: Database = None):
        self.db = db or get_db()

    def parse_amount(self, amount_str: str) -> Optional[float]:
        """Parse amount string to float."""
        if not amount_str:
            return None

        amount_str = amount_str.lower().replace(',', '').replace('$', '')
        try:
            if 'billion' in amount_str or 'b' in amount_str.split()[-1]:
                num = float(re.search(r'[\d.]+', amount_str).group())
                return num * 1e9
            elif 'million' in amount_str or 'm' in amount_str.split()[-1]:
                num = float(re.search(r'[\d.]+', amount_str).group())
                return num * 1e6
            else:
                return float(re.search(r'[\d.]+', amount_str).group())
        except:
            return None

    def import_from_json(self, json_path: str) -> int:
        """Import settlements from dorker JSON output."""
        with open(json_path, 'r') as f:
            data = json.load(f)

        count = 0
        for item in data:
            settlement = Settlement(
                title=item.get('title', ''),
                amount=self.parse_amount(item.get('amount', '')),
                amount_formatted=item.get('amount', ''),
                url=item.get('url', ''),
                description=item.get('snippet', ''),
                category=self._guess_category(item),
                source=self._extract_source(item.get('url', '')),
                pub_date=datetime.now().isoformat(),
                guid=item.get('url', '')
            )

            if settlement.title and settlement.url:
                self.db.add_settlement(settlement)
                count += 1

        return count

    def import_from_feed_xml(self, xml_path: str) -> int:
        """Import settlements from existing RSS feed XML."""
        import xml.etree.ElementTree as ET

        tree = ET.parse(xml_path)
        root = tree.getroot()

        count = 0
        for item in root.findall('.//item'):
            title = item.find('title').text if item.find('title') is not None else ''

            # Extract amount from title like "[$500M] Settlement Name"
            amount_match = re.search(r'\[\$([^\]]+)\]', title)
            amount_formatted = amount_match.group(1) if amount_match else None
            clean_title = re.sub(r'\[\$[^\]]+\]\s*', '', title)

            settlement = Settlement(
                title=clean_title,
                amount=self.parse_amount(amount_formatted) if amount_formatted else None,
                amount_formatted=f"${amount_formatted}" if amount_formatted else None,
                url=item.find('link').text if item.find('link') is not None else '',
                description=item.find('description').text if item.find('description') is not None else '',
                category=item.find('category').text if item.find('category') is not None else '',
                pub_date=item.find('pubDate').text if item.find('pubDate') is not None else '',
                guid=item.find('guid').text if item.find('guid') is not None else ''
            )

            if settlement.title:
                self.db.add_settlement(settlement)
                count += 1

        return count

    def _guess_category(self, item: Dict) -> str:
        """Guess category from content."""
        text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()

        categories = {
            'Data Breach': ['data breach', 'cybersecurity', 'hack', 'security incident'],
            'Privacy': ['privacy', 'gdpr', 'ccpa', 'tracking', 'biometric'],
            'Healthcare': ['healthcare', 'medical', 'hospital', 'pharma', 'drug'],
            'Antitrust': ['antitrust', 'monopoly', 'price fixing', 'competition'],
            'Securities': ['securities', 'sec', 'investor', 'shareholder', 'stock'],
            'Employment': ['wage', 'discrimination', 'eeoc', 'labor', 'overtime'],
            'Environmental': ['epa', 'environmental', 'pollution', 'superfund'],
            'Consumer': ['ftc', 'consumer', 'refund', 'false advertising'],
            'Opioid': ['opioid', 'oxycontin', 'fentanyl'],
        }

        for category, keywords in categories.items():
            if any(kw in text for kw in keywords):
                return category

        return 'Other'

    def _extract_source(self, url: str) -> str:
        """Extract source from URL."""
        if not url:
            return 'Unknown'

        domain_map = {
            'justice.gov': 'DOJ',
            'ftc.gov': 'FTC',
            'sec.gov': 'SEC',
            'epa.gov': 'EPA',
            'hhs.gov': 'HHS',
            'reuters.com': 'Reuters',
            'topclassactions.com': 'TopClassActions',
            'classaction.org': 'ClassAction.org',
        }

        for domain, source in domain_map.items():
            if domain in url:
                return source

        return 'Web'


class StateCaseImporter:
    """Import state court cases from scrapers."""

    def __init__(self, db: Database = None):
        self.db = db or get_db()

    def import_from_json(self, json_path: str, state: str) -> int:
        """Import state cases from JSON file."""
        with open(json_path, 'r') as f:
            data = json.load(f)

        cases = data if isinstance(data, list) else data.get('cases', [])
        count = 0

        for item in cases:
            case = StateCase(
                state=state.upper(),
                case_number=item.get('case_number') or item.get('caseNumber'),
                case_title=item.get('case_title') or item.get('title') or item.get('parties'),
                case_type=item.get('case_type') or item.get('type'),
                filing_date=item.get('filing_date') or item.get('filed') or item.get('date'),
                court=item.get('court'),
                county=item.get('county'),
                parties=item.get('parties'),
                charges=item.get('charges'),
                status=item.get('status'),
                url=item.get('url') or item.get('link'),
                raw_data=item,
                guid=f"{state}-{item.get('case_number', '')}"
            )

            if case.case_number or case.case_title:
                self.db.add_state_case(case)
                count += 1

        return count

    def import_alaska(self, json_path: str) -> int:
        """Import Alaska CourtView cases."""
        return self.import_from_json(json_path, 'AK')

    def import_wisconsin(self, json_path: str) -> int:
        """Import Wisconsin CCAP cases."""
        return self.import_from_json(json_path, 'WI')

    # Add more state-specific importers as needed


class FederalCaseImporter:
    """Import federal court cases from PACER."""

    def __init__(self, db: Database = None):
        self.db = db or get_db()

    def import_from_json(self, json_path: str) -> int:
        """Import federal cases from JSON file."""
        with open(json_path, 'r') as f:
            data = json.load(f)

        cases = data if isinstance(data, list) else data.get('cases', [])
        count = 0

        for item in cases:
            case = FederalCase(
                court=item.get('court'),
                case_number=item.get('case_number') or item.get('docket_number'),
                case_title=item.get('case_title') or item.get('title'),
                case_type=item.get('case_type'),
                filing_date=item.get('filing_date') or item.get('filed'),
                jurisdiction=item.get('jurisdiction'),
                nature_of_suit=item.get('nature_of_suit') or item.get('nos'),
                parties=item.get('parties'),
                docket_entries=item.get('docket_entries') or item.get('entries'),
                url=item.get('url'),
                pacer_case_id=item.get('pacer_case_id') or item.get('case_id'),
                guid=f"fed-{item.get('court', '')}-{item.get('case_number', '')}"
            )

            if case.case_number:
                self.db.add_federal_case(case)
                count += 1

        return count


def import_all():
    """Import data from all available sources."""
    db = get_db()
    base_path = Path(__file__).parent.parent

    print("=" * 60)
    print("IMPORTING DATA INTO DATABASE")
    print("=" * 60)

    # Import existing feed.xml
    feed_path = base_path / "docs" / "feed.xml"
    if feed_path.exists():
        importer = SettlementImporter(db)
        count = importer.import_from_feed_xml(str(feed_path))
        print(f"Imported {count} settlements from feed.xml")

    # Import dorker results if available
    dorker_path = Path("/tmp/settlement_dork_results.json")
    if dorker_path.exists():
        importer = SettlementImporter(db)
        count = importer.import_from_json(str(dorker_path))
        print(f"Imported {count} settlements from dorker results")

    # Import state court data
    # (Add paths to state scraper output files as needed)

    print("=" * 60)
    stats = db.get_stats()
    print(f"Database totals:")
    print(f"  Settlements: {stats['settlements']}")
    print(f"  State cases: {stats['state_cases']}")
    print(f"  Federal cases: {stats['federal_cases']}")
    print("=" * 60)


if __name__ == "__main__":
    import_all()
