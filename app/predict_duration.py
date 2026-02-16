"""
Predict case duration using judge and court data.

Uses historical patterns from CourtListener bulk data to estimate
time to resolution for federal cases.
"""
from typing import Optional, Dict, Any
from app.models.db import get_conn


def get_judge_stats(judge_name: str, court_id: str = None) -> Optional[Dict]:
    """Get historical statistics for a judge."""
    conn = get_conn()
    
    if court_id:
        cur = conn.execute("""
            SELECT assigned_to, court_id, 
                   COUNT(*) as total_cases,
                   SUM(CASE WHEN outcome_days IS NOT NULL THEN 1 ELSE 0 END) as resolved_cases,
                   ROUND(AVG(outcome_days), 0) as avg_days,
                   MIN(outcome_days) as min_days,
                   MAX(outcome_days) as max_days
            FROM docket_judges
            WHERE assigned_to LIKE ? AND court_id = ?
            GROUP BY assigned_to, court_id
        """, (f"%{judge_name}%", court_id))
    else:
        cur = conn.execute("""
            SELECT assigned_to, court_id,
                   COUNT(*) as total_cases,
                   SUM(CASE WHEN outcome_days IS NOT NULL THEN 1 ELSE 0 END) as resolved_cases,
                   ROUND(AVG(outcome_days), 0) as avg_days,
                   MIN(outcome_days) as min_days,
                   MAX(outcome_days) as max_days
            FROM docket_judges
            WHERE assigned_to LIKE ?
            GROUP BY assigned_to, court_id
            ORDER BY total_cases DESC
            LIMIT 1
        """, (f"%{judge_name}%",))
    
    row = cur.fetchone()
    if row:
        return {
            'judge_name': row['assigned_to'],
            'court': row['court_id'],
            'total_cases': row['total_cases'],
            'resolved_cases': row['resolved_cases'],
            'avg_days': row['avg_days'],
            'min_days': row['min_days'],
            'max_days': row['max_days']
        }
    return None


def get_court_stats(court_id: str) -> Optional[Dict]:
    """Get historical statistics for a court."""
    conn = get_conn()
    
    cur = conn.execute("""
        SELECT court_id,
               COUNT(*) as total_cases,
               COUNT(DISTINCT assigned_to) as judge_count,
               ROUND(AVG(outcome_days), 0) as avg_days,
               MIN(outcome_days) as min_days,
               MAX(outcome_days) as max_days
        FROM docket_judges
        WHERE court_id = ? AND outcome_days > 0 AND outcome_days < 3000
    """, (court_id,))
    
    row = cur.fetchone()
    if row and row['total_cases'] > 0:
        return {
            'court': row['court_id'],
            'total_cases': row['total_cases'],
            'judge_count': row['judge_count'],
            'avg_days': row['avg_days'],
            'min_days': row['min_days'],
            'max_days': row['max_days']
        }
    return None


def predict_duration(court_id: str, judge_name: str = None, 
                     nature_of_suit: str = None) -> Dict[str, Any]:
    """
    Predict case duration based on court, judge, and case type.
    
    Returns prediction with confidence factors.
    """
    conn = get_conn()
    result = {
        'predicted_days': None,
        'confidence': 'low',
        'factors': [],
        'court_avg': None,
        'judge_avg': None,
        'nos_avg': None
    }
    
    # Get court baseline
    court_stats = get_court_stats(court_id)
    if court_stats:
        result['court_avg'] = court_stats['avg_days']
        result['predicted_days'] = court_stats['avg_days']
        result['factors'].append(f"Court {court_id} avg: {court_stats['avg_days']:.0f} days")
        result['confidence'] = 'medium' if court_stats['total_cases'] >= 100 else 'low'
    
    # Adjust for judge if known
    if judge_name:
        judge_stats = get_judge_stats(judge_name, court_id)
        if judge_stats and judge_stats['avg_days']:
            result['judge_avg'] = judge_stats['avg_days']
            # Weight judge average more heavily if they have enough cases
            if judge_stats['resolved_cases'] >= 50:
                result['predicted_days'] = judge_stats['avg_days']
                result['factors'].append(f"Judge {judge_name} avg: {judge_stats['avg_days']:.0f} days ({judge_stats['resolved_cases']} cases)")
                result['confidence'] = 'high'
            elif judge_stats['resolved_cases'] >= 10:
                # Blend court and judge averages
                if result['court_avg']:
                    result['predicted_days'] = (result['court_avg'] + judge_stats['avg_days']) / 2
                result['factors'].append(f"Judge {judge_name} limited data: {judge_stats['avg_days']:.0f} days ({judge_stats['resolved_cases']} cases)")
    
    # Adjust for nature of suit
    if nature_of_suit:
        cur = conn.execute("""
            SELECT ROUND(AVG(outcome_days), 0) as avg_days, COUNT(*) as cnt
            FROM docket_judges
            WHERE nature_of_suit LIKE ? AND outcome_days > 0 AND outcome_days < 3000
        """, (f"%{nature_of_suit}%",))
        row = cur.fetchone()
        if row and row['cnt'] >= 20:
            result['nos_avg'] = row['avg_days']
            result['factors'].append(f"Nature of suit '{nature_of_suit}': {row['avg_days']:.0f} days avg")
            # Adjust prediction if we have NOS data
            if result['predicted_days'] and row['avg_days']:
                result['predicted_days'] = (result['predicted_days'] + row['avg_days']) / 2
    
    return result


def get_fastest_slowest_judges(court_id: str, min_cases: int = 50):
    """Get fastest and slowest judges for a court."""
    conn = get_conn()
    
    cur = conn.execute("""
        SELECT assigned_to, COUNT(*) as cases, ROUND(AVG(outcome_days), 0) as avg_days
        FROM docket_judges
        WHERE court_id = ? AND assigned_to IS NOT NULL AND assigned_to != ''
          AND outcome_days > 0 AND outcome_days < 3000
        GROUP BY assigned_to
        HAVING cases >= ?
        ORDER BY avg_days ASC
    """, (court_id, min_cases))
    
    rows = cur.fetchall()
    if not rows:
        return None, None
    
    fastest = [{'name': r['assigned_to'], 'cases': r['cases'], 'avg_days': r['avg_days']} 
               for r in rows[:5]]
    slowest = [{'name': r['assigned_to'], 'cases': r['cases'], 'avg_days': r['avg_days']} 
               for r in rows[-5:]]
    slowest.reverse()
    
    return fastest, slowest


if __name__ == '__main__':
    # Demo predictions
    from app.models.db import init_db
    init_db()
    
    print("=== DURATION PREDICTION DEMO ===\n")
    
    # Test cases
    test_cases = [
        ('cacd', 'M CASEY RODGERS', 'Copyright'),
        ('paed', None, 'Fair Labor Standards'),
        ('nysd', None, None),
        ('txsd', 'Kenneth M. Hoyt', None),
    ]
    
    for court, judge, nos in test_cases:
        print(f"Court: {court}, Judge: {judge or 'Unknown'}, NOS: {nos or 'Unknown'}")
        pred = predict_duration(court, judge, nos)
        print(f"  Predicted: {pred['predicted_days']:.0f} days" if pred['predicted_days'] else "  No prediction")
        print(f"  Confidence: {pred['confidence']}")
        for f in pred['factors']:
            print(f"    - {f}")
        print()
