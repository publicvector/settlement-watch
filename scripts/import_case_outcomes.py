#!/usr/bin/env python3
"""Import case outcomes from JSON file into database."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import get_db

OUTCOMES_OUTPUT = "/tmp/case_outcomes.json"


def import_case_outcomes(filepath: str = OUTCOMES_OUTPUT) -> int:
    """Import case outcomes from JSON file."""
    if not Path(filepath).exists():
        print(f"No case outcomes found at {filepath}")
        return 0

    with open(filepath, 'r') as f:
        outcomes = json.load(f)

    if not outcomes:
        print("No outcomes in file")
        return 0

    db = get_db()
    count = db.add_case_outcomes(outcomes)
    print(f"Imported {count} case outcomes")

    # Show stats
    stats = db.get_outcome_stats()
    print(f"\nOutcome Statistics:")
    print(f"  Total outcomes: {stats.get('total_outcomes', 0)}")
    if stats.get('avg_settlement'):
        print(f"  Avg settlement: ${stats['avg_settlement']:,.2f}")
    if stats.get('avg_days_to_resolution'):
        print(f"  Avg days to resolution: {stats['avg_days_to_resolution']:.0f}")

    return count


if __name__ == "__main__":
    import_case_outcomes()
