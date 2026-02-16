"""Analytics and data extraction from PACER RSS data"""
import re
from typing import Dict, List, Any
from collections import Counter
from .models.db import get_conn

def extract_document_type(summary: str) -> str:
    """Extract document type from summary"""
    if not summary:
        return "Unknown"

    # Common document types
    patterns = {
        "Complaint": r"\[Complaint\]|Complaint filed",
        "Motion": r"\[Motion|MOTION",
        "Order": r"\[Order\]|ORDER",
        "Answer": r"\[Answer\]",
        "Brief": r"\[Brief\]|BRIEF",
        "Notice": r"\[Notice\]|NOTICE",
        "Petition": r"\[Petition\]",
        "Application": r"\[Application\]",
        "Stipulation": r"\[Stipulation\]",
        "Declaration": r"\[Declaration\]",
        "Affidavit": r"\[Affidavit\]",
        "Subpoena": r"\[Subpoena\]",
        "Summons": r"\[Summons\]",
        "Judgment": r"\[Judgment\]|JUDGMENT",
        "Opinion": r"\[Opinion\]",
        "Transcript": r"\[Transcript\]|-Transcript\]",
        "Certificate": r"\[Certificate",
        "Waiver": r"Waiver of Service",
        "Extension": r"Extension of Time",
    }

    for doc_type, pattern in patterns.items():
        if re.search(pattern, summary, re.IGNORECASE):
            return doc_type

    return "Other"

def extract_parties(title: str) -> Dict[str, List[str]]:
    """Extract plaintiff and defendant names from case title"""
    # Pattern: "Case# Plaintiff v. Defendant" or "Case# Party1 et al v. Party2 et al"
    match = re.search(r'(?:[\d:]+(?:-\w+-[\d]+)?)\s+(.+?)\s+v\.?\s+(.+?)(?:\s*$|\s*-)', title, re.IGNORECASE)

    if match:
        plaintiffs = [p.strip() for p in match.group(1).split(' et al')[0].split(',')]
        defendants = [d.strip() for d in match.group(2).split(' et al')[0].split(',')]
        return {
            "plaintiffs": plaintiffs,
            "defendants": defendants
        }

    return {"plaintiffs": [], "defendants": []}

def get_court_activity_stats():
    """Get activity statistics by court"""
    conn = get_conn()
    cur = conn.execute("""
        SELECT
            court_code,
            COUNT(*) as total_filings,
            COUNT(DISTINCT case_number) as unique_cases,
            SUM(CASE WHEN case_type = 'cv' THEN 1 ELSE 0 END) as civil,
            SUM(CASE WHEN case_type = 'cr' THEN 1 ELSE 0 END) as criminal,
            SUM(CASE WHEN case_type = 'bk' THEN 1 ELSE 0 END) as bankruptcy,
            MAX(published) as most_recent
        FROM rss_items
        WHERE court_code IS NOT NULL
        GROUP BY court_code
        ORDER BY total_filings DESC
    """)
    return [dict(row) for row in cur.fetchall()]

def get_document_type_stats():
    """Analyze document types from summaries"""
    conn = get_conn()
    cur = conn.execute("SELECT summary FROM rss_items WHERE summary IS NOT NULL AND summary != ''")

    doc_types = Counter()
    for row in cur.fetchall():
        doc_type = extract_document_type(row[0])
        doc_types[doc_type] += 1

    return [{"document_type": k, "count": v} for k, v in doc_types.most_common(20)]

def get_case_type_distribution():
    """Get distribution of case types"""
    conn = get_conn()
    cur = conn.execute("""
        SELECT
            case_type,
            COUNT(*) as count,
            COUNT(DISTINCT court_code) as courts_affected
        FROM rss_items
        WHERE case_type IS NOT NULL
        GROUP BY case_type
        ORDER BY count DESC
    """)
    return [dict(row) for row in cur.fetchall()]

def get_recent_activity_by_hour():
    """Get filing activity by hour for the last 24 hours"""
    conn = get_conn()
    cur = conn.execute("""
        SELECT
            substr(published, 1, 16) as hour_bucket,
            COUNT(*) as filings
        FROM rss_items
        WHERE published IS NOT NULL
        GROUP BY hour_bucket
        ORDER BY hour_bucket DESC
        LIMIT 24
    """)
    return [dict(row) for row in cur.fetchall()]

def search_cases(query: str, limit: int = 50):
    """Search cases by party name, case number, or keywords"""
    conn = get_conn()
    search_pattern = f"%{query}%"
    cur = conn.execute("""
        SELECT *
        FROM rss_items
        WHERE title LIKE ? OR summary LIKE ? OR case_number LIKE ?
        ORDER BY published DESC
        LIMIT ?
    """, (search_pattern, search_pattern, search_pattern, limit))
    return [dict(row) for row in cur.fetchall()]

def get_top_parties(limit: int = 20):
    """Extract most frequently appearing parties"""
    conn = get_conn()
    cur = conn.execute("SELECT title FROM rss_items WHERE title IS NOT NULL")

    party_counter = Counter()
    for row in cur.fetchall():
        parties = extract_parties(row[0])
        for plaintiff in parties.get('plaintiffs', []):
            if plaintiff and len(plaintiff) > 3:  # Filter out very short names
                party_counter[plaintiff] += 1
        for defendant in parties.get('defendants', []):
            if defendant and len(defendant) > 3:
                party_counter[defendant] += 1

    return [{"party": k, "appearances": v} for k, v in party_counter.most_common(limit)]


# --- Extended Analytics for Case Patterns ---

def get_nature_of_suit_stats(court_code: str = None, limit: int = 50):
    """Get filing statistics by nature of suit"""
    conn = get_conn()
    if court_code:
        cur = conn.execute("""
            SELECT
                nature_of_suit,
                COUNT(*) as total_filings,
                COUNT(DISTINCT case_number) as unique_cases,
                COUNT(DISTINCT court_code) as courts,
                MIN(published) as first_seen,
                MAX(published) as last_seen
            FROM rss_items
            WHERE nature_of_suit IS NOT NULL AND nature_of_suit != '' AND court_code = ?
            GROUP BY nature_of_suit
            ORDER BY total_filings DESC
            LIMIT ?
        """, (court_code, limit))
    else:
        cur = conn.execute("""
            SELECT
                nature_of_suit,
                COUNT(*) as total_filings,
                COUNT(DISTINCT case_number) as unique_cases,
                COUNT(DISTINCT court_code) as courts,
                MIN(published) as first_seen,
                MAX(published) as last_seen
            FROM rss_items
            WHERE nature_of_suit IS NOT NULL AND nature_of_suit != ''
            GROUP BY nature_of_suit
            ORDER BY total_filings DESC
            LIMIT ?
        """, (limit,))
    return [dict(row) for row in cur.fetchall()]


def get_filing_trends(days: int = 30, court_code: str = None, case_type: str = None):
    """Get filing trends over time (daily counts)"""
    conn = get_conn()

    conditions = ["published IS NOT NULL"]
    params = []

    if court_code:
        conditions.append("court_code = ?")
        params.append(court_code)
    if case_type:
        conditions.append("case_type = ?")
        params.append(case_type)

    where_clause = " AND ".join(conditions)
    params.append(days)

    cur = conn.execute(f"""
        SELECT
            substr(published, 1, 10) as date,
            COUNT(*) as total_filings,
            COUNT(DISTINCT case_number) as unique_cases,
            SUM(CASE WHEN case_type = 'cv' THEN 1 ELSE 0 END) as civil,
            SUM(CASE WHEN case_type = 'cr' THEN 1 ELSE 0 END) as criminal,
            SUM(CASE WHEN case_type = 'bk' THEN 1 ELSE 0 END) as bankruptcy
        FROM rss_items
        WHERE {where_clause}
        GROUP BY substr(published, 1, 10)
        ORDER BY date DESC
        LIMIT ?
    """, tuple(params))
    return [dict(row) for row in cur.fetchall()]


def get_court_comparison(courts: List[str] = None):
    """Compare filing activity across courts"""
    conn = get_conn()

    if courts:
        placeholders = ",".join(["?"] * len(courts))
        cur = conn.execute(f"""
            SELECT
                court_code,
                COUNT(*) as total_filings,
                COUNT(DISTINCT case_number) as unique_cases,
                SUM(CASE WHEN case_type = 'cv' THEN 1 ELSE 0 END) as civil,
                SUM(CASE WHEN case_type = 'cr' THEN 1 ELSE 0 END) as criminal,
                SUM(CASE WHEN case_type = 'bk' THEN 1 ELSE 0 END) as bankruptcy,
                COUNT(DISTINCT nature_of_suit) as nos_variety,
                MIN(published) as first_filing,
                MAX(published) as last_filing
            FROM rss_items
            WHERE court_code IN ({placeholders})
            GROUP BY court_code
            ORDER BY total_filings DESC
        """, tuple(courts))
    else:
        cur = conn.execute("""
            SELECT
                court_code,
                COUNT(*) as total_filings,
                COUNT(DISTINCT case_number) as unique_cases,
                SUM(CASE WHEN case_type = 'cv' THEN 1 ELSE 0 END) as civil,
                SUM(CASE WHEN case_type = 'cr' THEN 1 ELSE 0 END) as criminal,
                SUM(CASE WHEN case_type = 'bk' THEN 1 ELSE 0 END) as bankruptcy,
                COUNT(DISTINCT nature_of_suit) as nos_variety,
                MIN(published) as first_filing,
                MAX(published) as last_filing
            FROM rss_items
            WHERE court_code IS NOT NULL
            GROUP BY court_code
            ORDER BY total_filings DESC
        """)
    return [dict(row) for row in cur.fetchall()]


def get_new_cases_summary(days: int = 7, court_code: str = None):
    """Get summary of new cases filed recently"""
    conn = get_conn()
    import json

    conditions = ["metadata_json IS NOT NULL"]
    params = []

    if court_code:
        conditions.append("court_code = ?")
        params.append(court_code)

    where_clause = " AND ".join(conditions)
    params.append(days * 100)  # Fetch more to filter

    cur = conn.execute(f"""
        SELECT court_code, case_type, nature_of_suit, case_number, title, published, metadata_json
        FROM rss_items
        WHERE {where_clause}
        ORDER BY published DESC
        LIMIT ?
    """, tuple(params))

    new_cases = []
    for row in cur.fetchall():
        try:
            meta = json.loads(row["metadata_json"] or "{}")
            if meta.get("is_new_case"):
                new_cases.append({
                    "court_code": row["court_code"],
                    "case_type": row["case_type"],
                    "nature_of_suit": row["nature_of_suit"],
                    "case_number": row["case_number"],
                    "title": row["title"],
                    "published": row["published"]
                })
        except Exception:
            pass

    return new_cases[:100]  # Limit to 100


def get_case_type_by_court(case_type: str):
    """Get breakdown of a specific case type across all courts"""
    conn = get_conn()
    cur = conn.execute("""
        SELECT
            court_code,
            COUNT(*) as filings,
            COUNT(DISTINCT case_number) as cases,
            COUNT(DISTINCT nature_of_suit) as nos_variety,
            MIN(published) as first_seen,
            MAX(published) as last_seen
        FROM rss_items
        WHERE case_type = ?
        GROUP BY court_code
        ORDER BY filings DESC
    """, (case_type,))
    return [dict(row) for row in cur.fetchall()]


def get_filing_velocity(court_code: str = None, hours: int = 24):
    """Get filing rate (filings per hour) for recent activity"""
    conn = get_conn()

    if court_code:
        cur = conn.execute("""
            SELECT
                substr(published, 1, 13) as hour,
                COUNT(*) as filings
            FROM rss_items
            WHERE court_code = ? AND published IS NOT NULL
            GROUP BY substr(published, 1, 13)
            ORDER BY hour DESC
            LIMIT ?
        """, (court_code, hours))
    else:
        cur = conn.execute("""
            SELECT
                substr(published, 1, 13) as hour,
                COUNT(*) as filings
            FROM rss_items
            WHERE published IS NOT NULL
            GROUP BY substr(published, 1, 13)
            ORDER BY hour DESC
            LIMIT ?
        """, (hours,))

    results = [dict(row) for row in cur.fetchall()]
    if results:
        total = sum(r.get("filings", 0) for r in results)
        avg_per_hour = total / len(results) if results else 0
        return {
            "hours_analyzed": len(results),
            "total_filings": total,
            "avg_per_hour": round(avg_per_hour, 2),
            "hourly_breakdown": results
        }
    return {"hours_analyzed": 0, "total_filings": 0, "avg_per_hour": 0, "hourly_breakdown": []}


def get_judge_activity(court_code: str = None, limit: int = 20):
    """Get filing activity by judge"""
    conn = get_conn()

    if court_code:
        cur = conn.execute("""
            SELECT
                judge_name,
                COUNT(*) as filings,
                COUNT(DISTINCT case_number) as cases,
                SUM(CASE WHEN case_type = 'cv' THEN 1 ELSE 0 END) as civil,
                SUM(CASE WHEN case_type = 'cr' THEN 1 ELSE 0 END) as criminal
            FROM rss_items
            WHERE judge_name IS NOT NULL AND judge_name != '' AND court_code = ?
            GROUP BY judge_name
            ORDER BY filings DESC
            LIMIT ?
        """, (court_code, limit))
    else:
        cur = conn.execute("""
            SELECT
                judge_name,
                court_code,
                COUNT(*) as filings,
                COUNT(DISTINCT case_number) as cases,
                SUM(CASE WHEN case_type = 'cv' THEN 1 ELSE 0 END) as civil,
                SUM(CASE WHEN case_type = 'cr' THEN 1 ELSE 0 END) as criminal
            FROM rss_items
            WHERE judge_name IS NOT NULL AND judge_name != ''
            GROUP BY judge_name, court_code
            ORDER BY filings DESC
            LIMIT ?
        """, (limit,))
    return [dict(row) for row in cur.fetchall()]


def get_overall_stats():
    """Get overall system statistics"""
    conn = get_conn()

    stats = {}

    # Total filings
    cur = conn.execute("SELECT COUNT(*) as cnt FROM rss_items")
    row = cur.fetchone()
    stats["total_filings"] = row["cnt"] if row else 0

    # Total unique cases
    cur = conn.execute("SELECT COUNT(DISTINCT case_number) as cnt FROM rss_items WHERE case_number IS NOT NULL")
    row = cur.fetchone()
    stats["unique_cases"] = row["cnt"] if row else 0

    # Active courts
    cur = conn.execute("SELECT COUNT(DISTINCT court_code) as cnt FROM rss_items WHERE court_code IS NOT NULL")
    row = cur.fetchone()
    stats["active_courts"] = row["cnt"] if row else 0

    # Date range
    cur = conn.execute("SELECT MIN(published) as first, MAX(published) as last FROM rss_items WHERE published IS NOT NULL")
    row = cur.fetchone()
    if row:
        stats["first_filing"] = row["first"]
        stats["last_filing"] = row["last"]

    # Case type breakdown
    cur = conn.execute("""
        SELECT case_type, COUNT(*) as cnt
        FROM rss_items
        WHERE case_type IS NOT NULL
        GROUP BY case_type
        ORDER BY cnt DESC
    """)
    stats["case_types"] = {row["case_type"]: row["cnt"] for row in cur.fetchall()}

    return stats
