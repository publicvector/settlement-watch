#!/usr/bin/env python3
"""Import existing settlements from feed.xml into database."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.importers import SettlementImporter
from db.database import get_db

FEED_PATH = Path(__file__).parent.parent / "docs" / "feed.xml"

if __name__ == "__main__":
    if FEED_PATH.exists():
        importer = SettlementImporter(get_db())
        count = importer.import_from_feed_xml(str(FEED_PATH))
        print(f"Imported {count} existing settlements from feed.xml")
    else:
        print(f"Feed file not found: {FEED_PATH}")
