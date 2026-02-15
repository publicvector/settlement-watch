#!/usr/bin/env python3
"""Import DocumentCloud documents into the database."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import get_db

DOCUMENTCLOUD_OUTPUT = "/tmp/documentcloud_results.json"


def import_documentcloud(filepath: str = DOCUMENTCLOUD_OUTPUT) -> int:
    """Import DocumentCloud documents from JSON file."""
    if not Path(filepath).exists():
        print(f"No DocumentCloud results found at {filepath}")
        return 0

    with open(filepath, 'r') as f:
        data = json.load(f)

    documents = data.get('documents', [])
    if not documents:
        print("No documents in file")
        return 0

    db = get_db()
    count = db.add_documentcloud_docs(documents)
    print(f"Imported {count} DocumentCloud documents")
    return count


if __name__ == "__main__":
    import_documentcloud()
