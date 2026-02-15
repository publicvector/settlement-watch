#!/usr/bin/env python3
"""
Database manager for Settlement Watch
Handles storage and retrieval of settlements and court cases.
"""
import sqlite3
import json
import os
import re
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


@dataclass
class DocketEntry:
    case_number: str
    entry_date: str
    case_source: str = 'state'  # 'state' or 'federal'
    state: Optional[str] = None
    case_id: Optional[int] = None
    entry_number: Optional[int] = None
    entry_text: Optional[str] = None
    entry_type: Optional[str] = None  # 'filing', 'order', 'opinion', 'hearing', 'minute'
    is_opinion: bool = False
    is_order: bool = False
    document_url: Optional[str] = None
    filed_by: Optional[str] = None
    judge: Optional[str] = None
    guid: Optional[str] = None


# Patterns for detecting opinions/orders
OPINION_PATTERNS = [
    r'\bopinion\b', r'\bdecision\b', r'\bjudgment\b', r'\bruling\b',
    r'\bverdict\b', r'\bfindings?\b', r'\bconclusions?\b',
    r'\bmemorandum\s+decision\b', r'\bfinal\s+order\b',
    r'\bsummary\s+judgment\b', r'\bdismiss', r'\bgranted\b', r'\bdenied\b'
]

ORDER_PATTERNS = [
    r'\border\b', r'\bdirective\b', r'\bmandate\b', r'\binjunction\b',
    r'\bstay\b', r'\bremand\b', r'\bsentenc', r'\bjudge.*order',
    r'\bcourt\s+order', r'\bscheduling\s+order\b', r'\bprotective\s+order\b'
]


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

    def get_case_types(self) -> List[str]:
        """Get list of distinct case types."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT case_type FROM state_cases
            WHERE case_type IS NOT NULL AND case_type != ''
            ORDER BY case_type
        """)
        rows = cursor.fetchall()
        conn.close()
        return [row['case_type'] for row in rows]

    def get_state_cases_by_type(self, case_type: str, limit: int = 100) -> List[Dict]:
        """Get state cases filtered by case type."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM state_cases
            WHERE case_type = ?
            ORDER BY filing_date DESC
            LIMIT ?
        """, (case_type, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

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

    # === Docket Entries ===

    def add_docket_entry(self, entry: DocketEntry) -> int:
        """Add a docket entry."""
        import re
        conn = self._get_conn()
        cursor = conn.cursor()

        # Auto-detect opinion/order from text
        entry_text_lower = (entry.entry_text or '').lower()
        is_opinion = entry.is_opinion or any(
            re.search(p, entry_text_lower) for p in OPINION_PATTERNS
        )
        is_order = entry.is_order or any(
            re.search(p, entry_text_lower) for p in ORDER_PATTERNS
        )

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO docket_entries
                (case_id, case_source, state, case_number, entry_number, entry_date,
                 entry_text, entry_type, is_opinion, is_order, document_url, filed_by, judge, guid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.case_id,
                entry.case_source,
                entry.state,
                entry.case_number,
                entry.entry_number,
                entry.entry_date,
                entry.entry_text,
                entry.entry_type,
                1 if is_opinion else 0,
                1 if is_order else 0,
                entry.document_url,
                entry.filed_by,
                entry.judge,
                entry.guid or f"{entry.case_number}-{entry.entry_number or entry.entry_date}"
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def add_docket_entries(self, entries: List[DocketEntry]) -> int:
        """Bulk add docket entries."""
        count = 0
        for e in entries:
            try:
                self.add_docket_entry(e)
                count += 1
            except Exception as ex:
                print(f"Error adding docket entry: {ex}")
        return count

    def get_recent_filings(self, days: int = 7, state: str = None, limit: int = 100) -> List[Dict]:
        """Get docket entries from the last N days."""
        conn = self._get_conn()
        cursor = conn.cursor()

        query = """
            SELECT * FROM docket_entries
            WHERE date(entry_date) >= date('now', ?)
        """
        params = [f'-{days} days']

        if state:
            query += " AND state = ?"
            params.append(state.upper())

        query += " ORDER BY entry_date DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_opinions(self, days: int = 30, state: str = None, limit: int = 100) -> List[Dict]:
        """Get judicial opinions/decisions."""
        conn = self._get_conn()
        cursor = conn.cursor()

        query = """
            SELECT * FROM docket_entries
            WHERE is_opinion = 1
        """
        params = []

        if days:
            query += " AND date(entry_date) >= date('now', ?)"
            params.append(f'-{days} days')

        if state:
            query += " AND state = ?"
            params.append(state.upper())

        query += " ORDER BY entry_date DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_orders(self, days: int = 30, state: str = None, limit: int = 100) -> List[Dict]:
        """Get court orders."""
        conn = self._get_conn()
        cursor = conn.cursor()

        query = """
            SELECT * FROM docket_entries
            WHERE is_order = 1
        """
        params = []

        if days:
            query += " AND date(entry_date) >= date('now', ?)"
            params.append(f'-{days} days')

        if state:
            query += " AND state = ?"
            params.append(state.upper())

        query += " ORDER BY entry_date DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
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

        # Docket entry stats
        try:
            cursor.execute("SELECT COUNT(*) as count FROM docket_entries")
            stats['docket_entries'] = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(*) as count FROM docket_entries WHERE is_opinion = 1")
            stats['opinions'] = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(*) as count FROM docket_entries WHERE is_order = 1")
            stats['orders'] = cursor.fetchone()['count']

            cursor.execute("""
                SELECT COUNT(*) as count FROM docket_entries
                WHERE date(entry_date) >= date('now', '-7 days')
            """)
            stats['recent_filings_7d'] = cursor.fetchone()['count']
        except:
            stats['docket_entries'] = 0
            stats['opinions'] = 0
            stats['orders'] = 0
            stats['recent_filings_7d'] = 0

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
