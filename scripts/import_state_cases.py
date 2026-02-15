#!/usr/bin/env python3
"""
Import state court cases from scraper JSON outputs into the database.
Run after scrapers to populate the database with state court data.
"""
import json
import sys
from pathlib import Path
from glob import glob

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import Database, StateCase, get_db


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


def import_state_file(filepath: str, state: str, db: Database) -> int:
    """Import cases from a single JSON file."""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"  Error reading {filepath}: {e}")
        return 0

    # Handle both list and dict formats
    if isinstance(data, list):
        cases = data
    elif isinstance(data, dict):
        cases = data.get('cases', data.get('results', [data]))
    else:
        return 0

    count = 0
    for case_data in cases:
        if not isinstance(case_data, dict):
            continue

        try:
            case = parse_case_data(case_data, state)
            if case.case_number or case.case_title:
                db.add_state_case(case)
                count += 1
        except Exception as e:
            continue

    return count


def import_all_states():
    """Import cases from all available state scraper outputs."""
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
    print("IMPORTING STATE COURT CASES")
    print("=" * 60)

    total_imported = 0

    for state in STATE_SCRAPERS.keys():
        files = find_scraper_output(state, [str(d) for d in search_dirs if d.exists()])

        if files:
            state_count = 0
            for filepath in files:
                count = import_state_file(filepath, state, db)
                state_count += count
                if count > 0:
                    print(f"  {state}: Imported {count} cases from {Path(filepath).name}")

            total_imported += state_count

    print("=" * 60)
    print(f"TOTAL IMPORTED: {total_imported} state court cases")
    print("=" * 60)

    # Show stats
    stats = db.get_stats()
    print(f"\nDatabase now has:")
    print(f"  State cases: {stats['state_cases']}")
    print(f"  States: {stats['states']}")

    return total_imported


if __name__ == "__main__":
    import_all_states()
