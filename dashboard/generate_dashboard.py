#!/usr/bin/env python3
"""
Dashboard Data Generator

Generates dashboard data files from the Settlement Watch database.
Run this whenever the database is updated to refresh the dashboard.
"""
import sqlite3
import json
from pathlib import Path
from datetime import datetime


def get_connection():
    return sqlite3.connect("db/settlement_watch.db")


def generate_overview_stats():
    """Generate overview KPI statistics."""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}

    # Total cases
    cursor.execute("SELECT COUNT(*) FROM case_outcomes")
    stats['total_cases'] = cursor.fetchone()[0]

    # Total value
    cursor.execute("SELECT SUM(settlement_amount) FROM case_outcomes WHERE settlement_amount > 0")
    stats['total_value'] = cursor.fetchone()[0] or 0

    # Average settlement
    cursor.execute("SELECT AVG(settlement_amount) FROM case_outcomes WHERE settlement_amount > 0")
    stats['avg_settlement'] = cursor.fetchone()[0] or 0

    # Median settlement
    cursor.execute("""
        SELECT settlement_amount FROM case_outcomes
        WHERE settlement_amount > 0
        ORDER BY settlement_amount
        LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM case_outcomes WHERE settlement_amount > 0)
    """)
    result = cursor.fetchone()
    stats['median_settlement'] = result[0] if result else 0

    # Unique courts
    cursor.execute("SELECT COUNT(DISTINCT court) FROM case_outcomes WHERE court IS NOT NULL AND court <> ''")
    stats['unique_courts'] = cursor.fetchone()[0]

    # Unique defendants
    cursor.execute("SELECT COUNT(DISTINCT defendant) FROM case_outcomes WHERE defendant IS NOT NULL AND defendant <> ''")
    stats['unique_defendants'] = cursor.fetchone()[0]

    # Cause categories
    cursor.execute("SELECT COUNT(DISTINCT nature_of_suit) FROM case_outcomes WHERE nature_of_suit IS NOT NULL AND nature_of_suit <> ''")
    stats['cause_categories'] = cursor.fetchone()[0]

    conn.close()
    return stats


def generate_size_distribution():
    """Generate settlement size distribution data."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            CASE
                WHEN settlement_amount >= 1e9 THEN 'Billion+'
                WHEN settlement_amount >= 100e6 THEN '$100M-$1B'
                WHEN settlement_amount >= 10e6 THEN '$10M-$100M'
                WHEN settlement_amount >= 1e6 THEN '$1M-$10M'
                ELSE 'Under $1M'
            END as size_bucket,
            COUNT(*) as count,
            SUM(settlement_amount) as total
        FROM case_outcomes
        WHERE settlement_amount > 0
        GROUP BY size_bucket
        ORDER BY MIN(settlement_amount) DESC
    """)

    distribution = []
    for row in cursor.fetchall():
        distribution.append({
            'bucket': row[0],
            'count': row[1],
            'total': row[2]
        })

    conn.close()
    return distribution


def generate_cause_data():
    """Generate cause of action statistics."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            nature_of_suit,
            COUNT(*) as count,
            SUM(settlement_amount) as total,
            AVG(settlement_amount) as avg_val,
            MIN(settlement_amount) as min_val,
            MAX(settlement_amount) as max_val
        FROM case_outcomes
        WHERE settlement_amount > 0
        AND nature_of_suit IS NOT NULL AND nature_of_suit <> ''
        GROUP BY nature_of_suit
        HAVING COUNT(*) >= 3
        ORDER BY SUM(settlement_amount) DESC
    """)

    causes = []
    for row in cursor.fetchall():
        causes.append({
            'name': row[0],
            'count': row[1],
            'total': row[2],
            'avg': row[3],
            'min': row[4],
            'max': row[5]
        })

    # Calculate percentiles for each
    for cause in causes:
        cursor.execute("""
            SELECT settlement_amount FROM case_outcomes
            WHERE nature_of_suit = ? AND settlement_amount > 0
            ORDER BY settlement_amount
        """, (cause['name'],))
        amounts = [r[0] for r in cursor.fetchall()]

        if len(amounts) >= 4:
            cause['p25'] = amounts[len(amounts) // 4]
            cause['median'] = amounts[len(amounts) // 2]
            cause['p75'] = amounts[3 * len(amounts) // 4]
        else:
            cause['p25'] = cause['min']
            cause['median'] = cause['avg']
            cause['p75'] = cause['max']

        # Confidence score
        n = cause['count']
        cause['confidence'] = min(1.0, 0.3 + (0.7 * (n / (n + 10))))

    conn.close()
    return causes


def generate_defendant_data():
    """Generate defendant statistics."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            defendant,
            COUNT(*) as count,
            SUM(settlement_amount) as total,
            AVG(settlement_amount) as avg_val
        FROM case_outcomes
        WHERE settlement_amount > 0
        AND defendant IS NOT NULL AND defendant <> ''
        GROUP BY defendant
        ORDER BY SUM(settlement_amount) DESC
        LIMIT 50
    """)

    defendants = []
    for row in cursor.fetchall():
        defendants.append({
            'name': row[0],
            'count': row[1],
            'total': row[2],
            'avg': row[3],
            'repeat_score': min(100, row[1] * 15)
        })

    conn.close()
    return defendants


def generate_court_data():
    """Generate court statistics."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            court,
            COUNT(*) as count,
            SUM(settlement_amount) as total,
            AVG(settlement_amount) as avg_val
        FROM case_outcomes
        WHERE settlement_amount > 0
        AND court IS NOT NULL AND court <> ''
        GROUP BY court
        HAVING COUNT(*) >= 2
        ORDER BY AVG(settlement_amount) DESC
        LIMIT 30
    """)

    courts = []
    for row in cursor.fetchall():
        courts.append({
            'name': row[0],
            'count': row[1],
            'total': row[2],
            'avg': row[3]
        })

    conn.close()
    return courts


def generate_source_data():
    """Generate source statistics."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            source,
            COUNT(*) as count,
            SUM(settlement_amount) as total,
            AVG(settlement_amount) as avg_val
        FROM case_outcomes
        WHERE settlement_amount > 0
        AND source IS NOT NULL
        GROUP BY source
        ORDER BY SUM(settlement_amount) DESC
        LIMIT 20
    """)

    sources = []
    for row in cursor.fetchall():
        sources.append({
            'name': row[0],
            'count': row[1],
            'total': row[2],
            'avg': row[3]
        })

    conn.close()
    return sources


def generate_recent_settlements():
    """Generate recent settlement data."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            case_title,
            settlement_amount,
            nature_of_suit,
            court,
            defendant,
            source,
            settlement_date
        FROM case_outcomes
        WHERE settlement_amount > 0
        ORDER BY created_at DESC
        LIMIT 25
    """)

    settlements = []
    for row in cursor.fetchall():
        settlements.append({
            'title': row[0],
            'amount': row[1],
            'cause': row[2],
            'court': row[3],
            'defendant': row[4],
            'source': row[5],
            'date': row[6]
        })

    conn.close()
    return settlements


def format_currency(amount):
    """Format amount for display."""
    if amount >= 1e9:
        return f"${amount/1e9:.1f}B"
    elif amount >= 1e6:
        return f"${amount/1e6:.1f}M"
    elif amount >= 1e3:
        return f"${amount/1e3:.1f}K"
    return f"${amount:,.0f}"


def generate_all_data():
    """Generate all dashboard data."""
    data = {
        'generated_at': datetime.now().isoformat(),
        'overview': generate_overview_stats(),
        'size_distribution': generate_size_distribution(),
        'causes': generate_cause_data(),
        'defendants': generate_defendant_data(),
        'courts': generate_court_data(),
        'sources': generate_source_data(),
        'recent': generate_recent_settlements()
    }

    # Save to JSON
    output_path = Path("dashboard/data.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Dashboard data generated: {output_path}")
    print(f"  Total cases: {data['overview']['total_cases']}")
    print(f"  Total value: {format_currency(data['overview']['total_value'])}")
    print(f"  Causes tracked: {len(data['causes'])}")
    print(f"  Defendants tracked: {len(data['defendants'])}")

    return data


if __name__ == "__main__":
    generate_all_data()
