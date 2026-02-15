#!/usr/bin/env python3
"""
Import state court cases and docket entries from scraper JSON outputs into the database.
Run after scrapers to populate the database with state court data.
"""
import json
import sys
from pathlib import Path
from glob import glob

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import Database, StateCase, DocketEntry, get_db


# Map of state codes to their scraper output patterns
STATE_SCRAPERS = {
    'AK': ['alaska_cases.json', 'courtview_*.json'],
    'AR': ['arkansas_cases.json'],
    'CO': ['colorado_cases.json'],
    'CT': ['connecticut_cases.json'],
    'DE': ['delaware_cases.json'],
    'IN': ['indiana_cases.json', 'mycase_*.json'],
    'LA': ['louisiana_cases.json'],
    'MT': ['montana_cases.json'],
    'NV': ['nevada_cases.json', 'nevada_appellate_*.json'],
    'ND': ['north_dakota_cases.json'],
    'OH': ['ohio_cases.json', 'franklin_*.json'],
    'OK': ['oklahoma_cases.json', 'oscn_*.json'],
    'PA': ['pennsylvania_cases.json', 'pa_ujs_*.json'],
    'VT': ['vermont_cases.json'],
    'WI': ['wisconsin_cases.json', 'ccap_*.json'],
}


def find_scraper_output(state: str, search_dirs: list) -> list:
    """Find scraper output files for a state."""
    patterns = STATE_SCRAPERS.get(state.upper(), [])
    found_files = []

    for search_dir in search_dirs:
        for pattern in patterns:
            matches = glob(str(Path(search_dir) / pattern))
            found_files.extend(matches)

        # Also check for generic pattern
        generic = glob(str(Path(search_dir) / f"{state.lower()}_*.json"))
        found_files.extend(generic)

    return list(set(found_files))


def parse_case_data(data: dict, state: str) -> StateCase:
    """Parse case data from various scraper formats."""
    # Handle different field names from different scrapers
    return StateCase(
        state=state.upper(),
        case_number=data.get('case_number') or data.get('caseNumber') or data.get('case_id') or data.get('docket'),
        case_title=data.get('case_title') or data.get('title') or data.get('caption') or data.get('parties'),
        case_type=data.get('case_type') or data.get('type') or data.get('category'),
        filing_date=data.get('filing_date') or data.get('filed') or data.get('date') or data.get('file_date'),
        court=data.get('court') or data.get('court_name'),
        county=data.get('county'),
        parties=data.get('parties') or data.get('plaintiff_defendant'),
        charges=data.get('charges') or data.get('offense') or data.get('description'),
        status=data.get('status') or data.get('disposition'),
        url=data.get('url') or data.get('link') or data.get('case_url'),
        raw_data=data,
        guid=f"{state.upper()}-{data.get('case_number') or data.get('caseNumber') or 'unknown'}"
    )


def normalize_date(date_str: str) -> str:
    """Convert various date formats to ISO format (YYYY-MM-DD)."""
    if not date_str:
        return ''

    import re
    from datetime import datetime

    date_str = date_str.strip()

    # Already ISO format
    if re.match(r'^\d{4}-\d{2}-\d{2}', date_str):
        return date_str[:10]

    # MM-DD-YYYY or MM/DD/YYYY
    match = re.match(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$', date_str)
    if match:
        month, day, year = match.groups()
        # Sanity check year
        if int(year) > 2030:
            year = '2025'  # Fix typos like 2075
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

    # Try parsing with dateutil
    try:
        from dateutil import parser
        dt = parser.parse(date_str)
        return dt.strftime('%Y-%m-%d')
    except:
        pass

    return ''  # Invalid date


def parse_docket_entry(entry_data: dict, state: str) -> DocketEntry:
    """Parse docket entry data from scraper output."""
    entry_date = normalize_date(entry_data.get('entry_date', ''))

    return DocketEntry(
        case_number=entry_data.get('case_number', ''),
        entry_date=entry_date,
        case_source='state',
        state=state.upper(),
        entry_number=entry_data.get('entry_number'),
        entry_text=entry_data.get('entry_text', ''),
        entry_type=entry_data.get('entry_type', 'filing'),
        is_opinion=entry_data.get('is_opinion', False),
        is_order=entry_data.get('is_order', False),
        document_url=entry_data.get('document_url', ''),
        filed_by=entry_data.get('party', '') or entry_data.get('filed_by', ''),
        guid=f"{state}-{entry_data.get('case_number', 'unk')}-{entry_data.get('entry_number', entry_date)}"
    )


def import_state_file(filepath: str, state: str, db: Database) -> tuple:
    """Import cases and docket entries from a single JSON file."""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"  Error reading {filepath}: {e}")
        return 0, 0

    # Handle both list and dict formats
    if isinstance(data, list):
        cases = data
        docket_entries = []
    elif isinstance(data, dict):
        cases = data.get('cases', data.get('results', [data]))
        docket_entries = data.get('docket_entries', [])
    else:
        return 0, 0

    case_count = 0
    for case_data in cases:
        if not isinstance(case_data, dict):
            continue

        try:
            case = parse_case_data(case_data, state)
            if case.case_number or case.case_title:
                db.add_state_case(case)
                case_count += 1
        except Exception as e:
            continue

    # Import docket entries
    entry_count = 0
    for entry_data in docket_entries:
        if not isinstance(entry_data, dict):
            continue

        try:
            entry = parse_docket_entry(entry_data, state)
            if entry.entry_date and entry.entry_text:
                db.add_docket_entry(entry)
                entry_count += 1
        except Exception as e:
            continue

    return case_count, entry_count


def import_all_states():
    """Import cases and docket entries from all available state scraper outputs."""
    db = get_db()

    # Directories to search for scraper outputs
    scripts_dir = Path(__file__).parent
    search_dirs = [
        scripts_dir,  # scripts/
        scripts_dir / 'output',  # scripts/output/ (main output dir)
        scripts_dir.parent / 'data',
        scripts_dir.parent / 'output',
        Path('/tmp'),
    ]

    # Create output dir if it doesn't exist
    (scripts_dir / 'output').mkdir(exist_ok=True)

    print("=" * 60)
    print("IMPORTING STATE COURT CASES & DOCKET ENTRIES")
    print("=" * 60)

    total_cases = 0
    total_entries = 0

    for state in STATE_SCRAPERS.keys():
        files = find_scraper_output(state, [str(d) for d in search_dirs if d.exists()])

        if files:
            state_cases = 0
            state_entries = 0
            for filepath in files:
                case_count, entry_count = import_state_file(filepath, state, db)
                state_cases += case_count
                state_entries += entry_count
                if case_count > 0 or entry_count > 0:
                    print(f"  {state}: Imported {case_count} cases, {entry_count} docket entries from {Path(filepath).name}")

            total_cases += state_cases
            total_entries += state_entries

    print("=" * 60)
    print(f"TOTAL IMPORTED:")
    print(f"  Cases: {total_cases}")
    print(f"  Docket Entries: {total_entries}")
    print("=" * 60)

    # Show stats
    stats = db.get_stats()
    print(f"\nDatabase now has:")
    print(f"  State cases: {stats['state_cases']}")
    print(f"  States: {stats['states']}")
    print(f"  Docket entries: {stats.get('docket_entries', 0)}")
    print(f"  Opinions: {stats.get('opinions', 0)}")
    print(f"  Orders: {stats.get('orders', 0)}")
    print(f"  Recent filings (7d): {stats.get('recent_filings_7d', 0)}")

    return total_cases


if __name__ == "__main__":
    import_all_states()
