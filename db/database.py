#!/usr/bin/env python3
"""
Database manager for Settlement Watch
Handles storage and retrieval of settlements and court cases.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict
from pathlib import Path


DB_PATH = Path(__file__).parent / "settlement_watch.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@dataclass
class Settlement:
    title: str
    amount: Optional[float] = None
    amount_formatted: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = None
    pub_date: Optional[str] = None
    guid: Optional[str] = None


@dataclass
class StateCase:
    state: str
    case_number: Optional[str] = None
    case_title: Optional[str] = None
    case_type: Optional[str] = None
    filing_date: Optional[str] = None
    court: Optional[str] = None
    county: Optional[str] = None
    parties: Optional[str] = None
    charges: Optional[str] = None
    status: Optional[str] = None
    url: Optional[str] = None
    raw_data: Optional[Dict] = None
    guid: Optional[str] = None


@dataclass
class FederalCase:
    court: str
    case_number: Optional[str] = None
    case_title: Optional[str] = None
    case_type: Optional[str] = None
    filing_date: Optional[str] = None
    jurisdiction: Optional[str] = None
    nature_of_suit: Optional[str] = None
    parties: Optional[str] = None
    docket_entries: Optional[List] = None
    url: Optional[str] = None
    pacer_case_id: Optional[str] = None
    guid: Optional[str] = None


class Database:
    """SQLite database manager."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        self._init_db()

    def _init_db(self):
        """Initialize database with schema."""
        with open(SCHEMA_PATH, 'r') as f:
            schema = f.read()

        conn = sqlite3.connect(self.db_path)
        conn.executescript(schema)
        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # === Settlements ===

    def add_settlement(self, settlement: Settlement) -> int:
        """Add a settlement to the database."""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO settlements
                (title, amount, amount_formatted, url, description, category, source, pub_date, guid, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                settlement.title,
                settlement.amount,
                settlement.amount_formatted,
                settlement.url,
                settlement.description,
                settlement.category,
                settlement.source,
                settlement.pub_date,
                settlement.guid or f"{settlement.title[:30]}-{settlement.amount}",
                datetime.now().isoformat()
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def add_settlements(self, settlements: List[Settlement]) -> int:
        """Bulk add settlements."""
        count = 0
        for s in settlements:
            try:
                self.add_settlement(s)
                count += 1
            except Exception as e:
                print(f"Error adding settlement: {e}")
        return count

    def get_settlements(self, limit: int = 100, category: str = None) -> List[Dict]:
        """Get settlements, optionally filtered by category."""
        conn = self._get_conn()
        cursor = conn.cursor()

        if category:
            cursor.execute("""
                SELECT * FROM settlements
                WHERE category = ?
                ORDER BY pub_date DESC, amount DESC
                LIMIT ?
            """, (category, limit))
        else:
            cursor.execute("""
                SELECT * FROM settlements
                ORDER BY pub_date DESC, amount DESC
                LIMIT ?
            """, (limit,))

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_settlements_by_amount(self, min_amount: float = 0, limit: int = 100) -> List[Dict]:
        """Get settlements above a minimum amount."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM settlements
            WHERE amount >= ?
            ORDER BY amount DESC
            LIMIT ?
        """, (min_amount, limit))

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # === State Cases ===

    def add_state_case(self, case: StateCase) -> int:
        """Add a state court case."""
        conn = self._get_conn()
        cursor = conn.cursor()

        raw_data_json = json.dumps(case.raw_data) if case.raw_data else None

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO state_cases
                (state, case_number, case_title, case_type, filing_date, court, county,
                 parties, charges, status, url, raw_data, guid, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                case.state,
                case.case_number,
                case.case_title,
                case.case_type,
                case.filing_date,
                case.court,
                case.county,
                case.parties,
                case.charges,
                case.status,
                case.url,
                raw_data_json,
                case.guid or f"{case.state}-{case.case_number}",
                datetime.now().isoformat()
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def add_state_cases(self, cases: List[StateCase]) -> int:
        """Bulk add state cases."""
        count = 0
        for c in cases:
            try:
                self.add_state_case(c)
                count += 1
            except Exception as e:
                print(f"Error adding case: {e}")
        return count

    def get_state_cases(self, state: str = None, limit: int = 100) -> List[Dict]:
        """Get state cases, optionally filtered by state."""
        conn = self._get_conn()
        cursor = conn.cursor()

        if state:
            cursor.execute("""
                SELECT * FROM state_cases
                WHERE state = ?
                ORDER BY filing_date DESC
                LIMIT ?
            """, (state.upper(), limit))
        else:
            cursor.execute("""
                SELECT * FROM state_cases
                ORDER BY filing_date DESC
                LIMIT ?
            """, (limit,))

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_states(self) -> List[str]:
        """Get list of states with cases."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT state FROM state_cases ORDER BY state")
        rows = cursor.fetchall()
        conn.close()
        return [row['state'] for row in rows]

    # === Federal Cases ===

    def add_federal_case(self, case: FederalCase) -> int:
        """Add a federal court case."""
        conn = self._get_conn()
        cursor = conn.cursor()

        docket_json = json.dumps(case.docket_entries) if case.docket_entries else None

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO federal_cases
                (court, case_number, case_title, case_type, filing_date, jurisdiction,
                 nature_of_suit, parties, docket_entries, url, pacer_case_id, guid, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                case.court,
                case.case_number,
                case.case_title,
                case.case_type,
                case.filing_date,
                case.jurisdiction,
                case.nature_of_suit,
                case.parties,
                docket_json,
                case.url,
                case.pacer_case_id,
                case.guid or f"fed-{case.court}-{case.case_number}",
                datetime.now().isoformat()
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_federal_cases(self, court: str = None, limit: int = 100) -> List[Dict]:
        """Get federal cases, optionally filtered by court."""
        conn = self._get_conn()
        cursor = conn.cursor()

        if court:
            cursor.execute("""
                SELECT * FROM federal_cases
                WHERE court = ?
                ORDER BY filing_date DESC
                LIMIT ?
            """, (court, limit))
        else:
            cursor.execute("""
                SELECT * FROM federal_cases
                ORDER BY filing_date DESC
                LIMIT ?
            """, (limit,))

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # === Stats ===

    def get_stats(self) -> Dict:
        """Get database statistics."""
        conn = self._get_conn()
        cursor = conn.cursor()

        stats = {}

        cursor.execute("SELECT COUNT(*) as count FROM settlements")
        stats['settlements'] = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM state_cases")
        stats['state_cases'] = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(DISTINCT state) as count FROM state_cases")
        stats['states'] = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM federal_cases")
        stats['federal_cases'] = cursor.fetchone()['count']

        conn.close()
        return stats


# Convenience functions
def get_db() -> Database:
    """Get database instance."""
    return Database()


if __name__ == "__main__":
    # Test database
    db = Database()

    # Add test settlement
    test_settlement = Settlement(
        title="Test Settlement",
        amount=1000000,
        amount_formatted="$1M",
        category="Test",
        source="Test",
        pub_date=datetime.now().isoformat()
    )
    db.add_settlement(test_settlement)

    # Print stats
    print("Database Stats:")
    for k, v in db.get_stats().items():
        print(f"  {k}: {v}")
