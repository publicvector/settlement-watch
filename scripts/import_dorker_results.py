#!/usr/bin/env python3
"""Import settlement dorker results into database."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.importers import SettlementImporter
from db.database import get_db

DORKER_OUTPUT = "/tmp/settlement_dork_results.json"

if __name__ == "__main__":
    if Path(DORKER_OUTPUT).exists():
        importer = SettlementImporter(get_db())
        count = importer.import_from_json(DORKER_OUTPUT)
        print(f"Imported {count} settlements from dorker")
    else:
        print("No dorker results found")
