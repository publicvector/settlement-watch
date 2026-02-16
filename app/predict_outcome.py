"""
Predict case outcomes using available judge and court data.

Provides predictions for:
- Likely case disposition (dismissal, judgment, settlement)
- Dismissal type (with/without prejudice)
- Estimated duration to resolution

Based on:
- Historical court averages
- Judge-specific patterns (when available)
- Nature of suit patterns
"""
import sys
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass

sys.path.insert(0, '.')
from app.models.db import get_conn


@dataclass
class OutcomePrediction:
    """Predicted case outcome with confidence."""
    predicted_outcome: str  # 'dismissal', 'judgment', 'settlement', 'unknown'
    dismissal_type: Optional[str]  # 'with_prejudice', 'without_prejudice', 'voluntary'
    predicted_days: Optional[int]
    confidence: str  # 'high', 'medium', 'low'
    factors: List[str]
    probabilities: Dict[str, float]


# Baseline outcome rates from federal civil cases (empirical)
# Source: Administrative Office of US Courts statistics
BASELINE_RATES = {
    'dismissal': 0.35,  # ~35% dismissed (various reasons)
    'settlement': 0.45,  # ~45% settle before trial
    'judgment': 0.15,   # ~15% reach judgment
    'other': 0.05,      # ~5% other (transfer, remand, etc.)
}

# Dismissal type breakdown (of all dismissals)
DISMISSAL_TYPE_RATES = {
    'voluntary': 0.40,        # Plaintiff voluntary dismissal (often settlement)
    'with_prejudice': 0.35,   # Court dismissal with prejudice
    'without_prejudice': 0.25, # Court dismissal without prejudice
}

# Nature of suit adjustments (based on case type patterns)
NOS_ADJUSTMENTS = {
    # Contract cases - more likely to settle
    'Contract': {'settlement': 0.10, 'dismissal': -0.05},

    # Civil rights - more dismissals
    'Civil Rights': {'dismissal': 0.15, 'settlement': -0.10},
    '440 Civil rights other': {'dismissal': 0.20, 'settlement': -0.15},
    '442 Civil rights jobs': {'dismissal': 0.10, 'settlement': -0.05},
    '550 Prisoner': {'dismissal': 0.30, 'settlement': -0.25},
    'Prisoner': {'dismissal': 0.30, 'settlement': -0.25},

    # Employment - often settle
    'Labor': {'settlement': 0.15, 'dismissal': -0.10},
    '710 Fair Labor Standards': {'settlement': 0.20, 'dismissal': -0.10},
    '791 ERISA': {'settlement': 0.15, 'dismissal': -0.05},

    # IP cases - mix of outcomes
    '820 Copyright': {'settlement': 0.10, 'judgment': 0.05},
    '840 Trademark': {'settlement': 0.10, 'judgment': 0.05},
    '830 Patent': {'judgment': 0.10, 'settlement': 0.05},

    # Torts - high settlement rate
    'Personal Injury': {'settlement': 0.20, 'judgment': -0.10},
    '360 Personal injury product liability': {'settlement': 0.15, 'judgment': -0.05},
    '365 Personal injury': {'settlement': 0.15},

    # Default judgments common
    'Student Loans': {'judgment': 0.15, 'dismissal': -0.10},
    '152 Recovery of defaulted student loans': {'judgment': 0.20, 'dismissal': -0.15},
}

# Court-specific adjustment patterns
COURT_ADJUSTMENTS = {
    # Some courts have higher dismissal rates
    'txed': {'dismissal': 0.10},  # ED Texas - patent friendly
    'deld': {'dismissal': 0.05},  # Delaware - corporate
    'nysd': {'settlement': 0.05}, # SDNY - commercial
}


def get_fjc_court_rates(court_id: str) -> Optional[Dict]:
    """
    Get outcome rates from FJC Integrated Database for a court.

    Returns dict with dismissal/settlement/judgment rates based on
    actual federal court disposition data (10M+ cases).
    """
    conn = get_conn()

    # Check if fjc_outcomes table exists
    cur = conn.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='fjc_outcomes'
    """)
    if not cur.fetchone():
        return None

    cur = conn.execute("""
        SELECT outcome_bucket, COUNT(*) as cnt
        FROM fjc_outcomes
        WHERE court_code = ?
        GROUP BY outcome_bucket
    """, (court_id,))

    results = {}
    total = 0
    for row in cur.fetchall():
        bucket = row['outcome_bucket']
        cnt = row['cnt']
        results[bucket] = cnt
        total += cnt

    if total >= 100:  # Need meaningful sample
        rates = {k: v/total for k, v in results.items()}
        return {'rates': rates, 'total': total}
    return None


def get_fjc_nos_rates(nature_of_suit: str) -> Optional[Dict]:
    """
    Get outcome rates by nature of suit from FJC data.
    """
    conn = get_conn()

    cur = conn.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='fjc_outcomes'
    """)
    if not cur.fetchone():
        return None

    cur = conn.execute("""
        SELECT outcome_bucket, COUNT(*) as cnt
        FROM fjc_outcomes
        WHERE nature_of_suit LIKE ?
        GROUP BY outcome_bucket
    """, (f"%{nature_of_suit}%",))

    results = {}
    total = 0
    for row in cur.fetchall():
        results[row['outcome_bucket']] = row['cnt']
        total += row['cnt']

    if total >= 50:
        rates = {k: v/total for k, v in results.items()}
        return {'rates': rates, 'total': total}
    return None


def get_fjc_duration_stats(court_id: str = None, nature_of_suit: str = None) -> Optional[Dict]:
    """
    Get case duration statistics from FJC data.
    """
    conn = get_conn()

    cur = conn.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='fjc_outcomes'
    """)
    if not cur.fetchone():
        return None

    query = """
        SELECT outcome_bucket,
               AVG(duration_days) as avg_days,
               MIN(duration_days) as min_days,
               MAX(duration_days) as max_days,
               COUNT(*) as cnt
        FROM fjc_outcomes
        WHERE duration_days IS NOT NULL AND duration_days > 0
    """
    params = []

    if court_id:
        query += " AND court_code = ?"
        params.append(court_id)
    if nature_of_suit:
        query += " AND nature_of_suit LIKE ?"
        params.append(f"%{nature_of_suit}%")

    query += " GROUP BY outcome_bucket"

    cur = conn.execute(query, params)

    results = {}
    for row in cur.fetchall():
        if row['cnt'] >= 10:
            results[row['outcome_bucket']] = {
                'avg_days': row['avg_days'],
                'min_days': row['min_days'],
                'max_days': row['max_days'],
                'count': row['cnt']
            }

    return results if results else None


def get_fjc_plaintiff_win_rate(court_id: str = None, nature_of_suit: str = None) -> Optional[Dict]:
    """
    Get plaintiff vs defendant win rates from FJC judgment data.
    """
    conn = get_conn()

    cur = conn.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='fjc_outcomes'
    """)
    if not cur.fetchone():
        return None

    query = """
        SELECT judgment_for, COUNT(*) as cnt
        FROM fjc_outcomes
        WHERE judgment_for IS NOT NULL
    """
    params = []

    if court_id:
        query += " AND court_code = ?"
        params.append(court_id)
    if nature_of_suit:
        query += " AND nature_of_suit LIKE ?"
        params.append(f"%{nature_of_suit}%")

    query += " GROUP BY judgment_for"

    cur = conn.execute(query, params)

    results = {}
    total = 0
    for row in cur.fetchall():
        results[row['judgment_for']] = row['cnt']
        total += row['cnt']

    if total >= 20:
        rates = {k: v/total for k, v in results.items()}
        return {'rates': rates, 'total': total}
    return None


def get_court_outcome_stats(court_id: str) -> Optional[Dict]:
    """Get historical outcome statistics for a court from our data."""
    conn = get_conn()

    # Check pacer_outcomes table
    cur = conn.execute("""
        SELECT outcome_type, COUNT(*) as cnt
        FROM pacer_outcomes
        WHERE court_code = ? AND outcome_type IS NOT NULL
          AND outcome_type != 'Unknown' AND outcome_type != 'FETCH_FAILED'
        GROUP BY outcome_type
    """, (court_id,))

    results = {}
    total = 0
    for row in cur.fetchall():
        outcome = row['outcome_type']
        cnt = row['cnt']
        results[outcome] = cnt
        total += cnt

    if total > 0:
        return {'outcomes': results, 'total': total}
    return None


def get_judge_outcome_history(judge_name: str, court_id: str = None) -> Optional[Dict]:
    """Get historical outcome patterns for a specific judge from RECAP data."""
    conn = get_conn()

    # Query judge_motion_stats table
    if court_id:
        cur = conn.execute("""
            SELECT motion_type, granted, denied, partial, total, grant_rate
            FROM judge_motion_stats
            WHERE judge_name LIKE ? AND court_id = ?
        """, (f"%{judge_name}%", court_id))
    else:
        cur = conn.execute("""
            SELECT motion_type, granted, denied, partial, total, grant_rate, court_id
            FROM judge_motion_stats
            WHERE judge_name LIKE ?
            ORDER BY total DESC
        """, (f"%{judge_name}%",))

    results = {}
    for row in cur.fetchall():
        mtype = row['motion_type']
        results[mtype] = {
            'granted': row['granted'],
            'denied': row['denied'],
            'partial': row['partial'],
            'total': row['total'],
            'grant_rate': row['grant_rate']
        }

    return results if results else None


def get_court_mtd_rate(court_id: str) -> Optional[float]:
    """Get MTD grant rate for a specific court from RECAP data."""
    conn = get_conn()

    cur = conn.execute("""
        SELECT
            SUM(CASE WHEN outcome = 'granted' THEN 1 ELSE 0 END) as granted,
            SUM(CASE WHEN outcome = 'denied' THEN 1 ELSE 0 END) as denied,
            COUNT(*) as total
        FROM motion_outcomes
        WHERE court_id = ? AND motion_type = 'mtd'
    """, (court_id,))

    row = cur.fetchone()
    if row and row['total'] >= 5:
        grant_deny = row['granted'] + row['denied']
        if grant_deny > 0:
            return row['granted'] / grant_deny
    return None


def predict_mtd_outcome(court_id: str, judge_name: str = None) -> Dict:
    """
    Predict Motion to Dismiss outcome based on court and judge history.

    Returns dict with grant_rate, confidence, and factors.
    """
    result = {
        'grant_rate': 0.68,  # Baseline from RECAP data
        'confidence': 'low',
        'factors': ['Baseline MTD grant rate: 68%'],
        'judge_stats': None
    }

    # Check court-specific rate
    court_rate = get_court_mtd_rate(court_id)
    if court_rate is not None:
        result['grant_rate'] = court_rate
        result['confidence'] = 'medium'
        result['factors'] = [f"Court {court_id.upper()} MTD grant rate: {court_rate*100:.0f}%"]

    # Check judge-specific rate
    if judge_name:
        judge_history = get_judge_outcome_history(judge_name, court_id)
        if judge_history and 'mtd' in judge_history:
            stats = judge_history['mtd']
            if stats['total'] >= 3:
                result['grant_rate'] = stats['grant_rate']
                result['confidence'] = 'high' if stats['total'] >= 5 else 'medium'
                result['factors'].append(
                    f"Judge {judge_name} MTD: {stats['granted']}G/{stats['denied']}D/{stats['partial']}P "
                    f"({stats['grant_rate']*100:.0f}% grant rate, {stats['total']} rulings)"
                )
                result['judge_stats'] = stats

    return result


def predict_outcome(
    court_id: str,
    judge_name: str = None,
    nature_of_suit: str = None,
    case_age_days: int = None
) -> OutcomePrediction:
    """
    Predict likely case outcome.

    Args:
        court_id: Court code (e.g., 'cacd', 'nysd')
        judge_name: Assigned judge name if known
        nature_of_suit: Case type/NOS code
        case_age_days: How long case has been pending

    Returns:
        OutcomePrediction with probabilities and confidence
    """
    # Start with baseline rates
    probs = BASELINE_RATES.copy()
    factors = ["Baseline federal civil case rates"]
    confidence = 'low'

    # PRIORITY 1: Use FJC Integrated Database (10M+ cases)
    fjc_court_data = get_fjc_court_rates(court_id)
    if fjc_court_data and fjc_court_data['total'] >= 500:
        fjc_rates = fjc_court_data['rates']
        for outcome in ['dismissal', 'settlement', 'judgment', 'other']:
            if outcome in fjc_rates:
                probs[outcome] = fjc_rates[outcome]
        factors = [f"FJC data: {fjc_court_data['total']:,} {court_id.upper()} cases"]
        confidence = 'high' if fjc_court_data['total'] >= 5000 else 'medium'

    # PRIORITY 2: Adjust for nature of suit using FJC data
    if nature_of_suit:
        fjc_nos_data = get_fjc_nos_rates(nature_of_suit)
        if fjc_nos_data and fjc_nos_data['total'] >= 100:
            # Blend NOS rates with court rates
            nos_rates = fjc_nos_data['rates']
            for outcome in ['dismissal', 'settlement', 'judgment', 'other']:
                if outcome in nos_rates and outcome in probs:
                    probs[outcome] = (probs[outcome] + nos_rates[outcome]) / 2
            factors.append(f"FJC NOS data: {fjc_nos_data['total']:,} similar cases")
            confidence = 'high' if fjc_nos_data['total'] >= 500 else confidence
        else:
            # Fall back to heuristic NOS adjustments
            for nos_key, adjustments in NOS_ADJUSTMENTS.items():
                if nos_key.lower() in nature_of_suit.lower():
                    for outcome, adj in adjustments.items():
                        if outcome in probs:
                            probs[outcome] = max(0.01, min(0.99, probs[outcome] + adj))
                    factors.append(f"NOS heuristic for '{nos_key}'")
                    break

    # Fall back to heuristic court adjustments if no FJC data
    if not fjc_court_data and court_id in COURT_ADJUSTMENTS:
        for outcome, adj in COURT_ADJUSTMENTS[court_id].items():
            if outcome in probs:
                probs[outcome] = max(0.01, min(0.99, probs[outcome] + adj))
        factors.append(f"Court heuristic for {court_id.upper()}")
        confidence = 'medium'

    # Check our actual court data (PACER outcomes)
    court_stats = get_court_outcome_stats(court_id)
    if court_stats and court_stats['total'] >= 10:
        # Blend with empirical data
        empirical = {}
        for outcome, cnt in court_stats['outcomes'].items():
            # Map outcome types to our categories
            if 'Dismiss' in outcome or 'Terminated' in outcome:
                empirical['dismissal'] = empirical.get('dismissal', 0) + cnt
            elif 'Judgment' in outcome:
                empirical['judgment'] = empirical.get('judgment', 0) + cnt
            elif 'Voluntary' in outcome or 'Settle' in outcome:
                empirical['settlement'] = empirical.get('settlement', 0) + cnt

        total = court_stats['total']
        for outcome, cnt in empirical.items():
            if outcome in probs:
                emp_rate = cnt / total
                probs[outcome] = (probs[outcome] + emp_rate) / 2

        factors.append(f"Empirical data from {total} {court_id.upper()} cases")
        confidence = 'medium' if total >= 20 else 'low'

    # Adjust for case age (older cases more likely to settle or dismiss)
    if case_age_days:
        if case_age_days > 365:
            probs['settlement'] += 0.05
            probs['dismissal'] += 0.05
            probs['judgment'] -= 0.05
            factors.append(f"Case age ({case_age_days} days) - older cases favor resolution")
        elif case_age_days < 90:
            probs['dismissal'] += 0.10  # Early dismissals common
            probs['settlement'] -= 0.05
            factors.append(f"Case age ({case_age_days} days) - early stage favors dismissal")

    # Normalize probabilities
    total = sum(probs.values())
    probs = {k: v/total for k, v in probs.items()}

    # Determine predicted outcome
    predicted = max(probs, key=probs.get)

    # Determine dismissal type if dismissal predicted
    dismissal_type = None
    if predicted == 'dismissal':
        # Use baseline dismissal type rates
        dtype_probs = DISMISSAL_TYPE_RATES.copy()
        if nature_of_suit and 'prisoner' in nature_of_suit.lower():
            dtype_probs['with_prejudice'] += 0.10
            dtype_probs['voluntary'] -= 0.10
        dismissal_type = max(dtype_probs, key=dtype_probs.get)

    # Get duration prediction
    from app.predict_duration import predict_duration
    duration_pred = predict_duration(court_id, judge_name, nature_of_suit)
    predicted_days = int(duration_pred['predicted_days']) if duration_pred['predicted_days'] else None

    return OutcomePrediction(
        predicted_outcome=predicted,
        dismissal_type=dismissal_type,
        predicted_days=predicted_days,
        confidence=confidence,
        factors=factors,
        probabilities=probs
    )


def format_prediction(pred: OutcomePrediction) -> str:
    """Format prediction for display."""
    lines = []
    lines.append(f"Predicted Outcome: {pred.predicted_outcome.upper()}")
    if pred.dismissal_type:
        lines.append(f"  Dismissal Type: {pred.dismissal_type.replace('_', ' ')}")
    if pred.predicted_days:
        lines.append(f"  Est. Duration: {pred.predicted_days} days")
    lines.append(f"  Confidence: {pred.confidence}")
    lines.append(f"\nProbabilities:")
    for outcome, prob in sorted(pred.probabilities.items(), key=lambda x: -x[1]):
        bar = '█' * int(prob * 20)
        lines.append(f"  {outcome:<12} {prob*100:>5.1f}% {bar}")
    lines.append(f"\nFactors:")
    for f in pred.factors:
        lines.append(f"  • {f}")
    return '\n'.join(lines)


if __name__ == '__main__':
    from app.models.db import init_db
    init_db()

    print("=" * 60)
    print("CASE OUTCOME PREDICTION DEMO")
    print("=" * 60)

    # Test MTD predictions with specific judges
    print("\n" + "─" * 60)
    print("MOTION TO DISMISS PREDICTIONS")
    print("─" * 60)

    mtd_tests = [
        ('txsd', 'David Hittner'),
        ('njd', 'Jamel K. Semper'),
        ('azd', 'Krissa M Lanham'),
        ('ilnd', None),
        ('flsd', None),
        ('cacd', 'John F. Walter'),
    ]

    for court, judge in mtd_tests:
        mtd_pred = predict_mtd_outcome(court, judge)
        judge_str = judge if judge else "Unknown"
        print(f"\n{court.upper()} - Judge: {judge_str}")
        print(f"  MTD Grant Rate: {mtd_pred['grant_rate']*100:.0f}%")
        print(f"  Confidence: {mtd_pred['confidence']}")
        for f in mtd_pred['factors']:
            print(f"    • {f}")

    # Test case outcome predictions
    print("\n" + "=" * 60)
    print("CASE OUTCOME PREDICTIONS")
    print("=" * 60)

    test_cases = [
        ('cacd', None, 'Copyright', None),
        ('nysd', None, 'Civil Rights', None),
        ('txsd', 'David Hittner', '830 Patent', 180),
        ('flsd', None, '550 Prisoner civil rights', 90),
    ]

    for court, judge, nos, age in test_cases:
        print(f"\n{'─' * 60}")
        print(f"Case: {court.upper()} | Judge: {judge or 'Unknown'} | NOS: {nos}")
        print(f"{'─' * 60}")
        pred = predict_outcome(court, judge, nos, age)
        print(format_prediction(pred))
