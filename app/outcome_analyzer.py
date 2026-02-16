"""
Analyze docket text for case outcomes and motion rulings.

Extracts:
- Motion to dismiss outcomes (granted/denied)
- Summary judgment outcomes
- Motion to compel outcomes
- Dismissal types (with/without prejudice)
- Settlement indicators
"""
import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class MotionOutcome:
    motion_type: str
    outcome: str  # 'granted', 'denied', 'partial', 'moot'
    text_snippet: str


# Comprehensive patterns for motion outcomes
MOTION_PATTERNS = {
    # Motion to Dismiss
    'mtd': {
        'granted': [
            r'motion\s+to\s+dismiss.*?\b(granted|granting)\b',
            r'grant(?:s|ed|ing)?\s+(?:the\s+)?(?:defendant.{0,20})?motion\s+to\s+dismiss',
            r'dismiss(?:es|ed|ing)?\s+(?:the\s+)?(?:complaint|action|case|claim)',
            r'case\s+is\s+dismissed',
        ],
        'denied': [
            r'motion\s+to\s+dismiss.*?\b(denied|denying)\b',
            r'den(?:y|ies|ied|ying)\s+(?:the\s+)?(?:defendant.{0,20})?motion\s+to\s+dismiss',
        ],
        'partial': [
            r'motion\s+to\s+dismiss.*?\b(granted\s+in\s+part|partially\s+granted)\b',
            r'grant(?:ed|ing)?\s+in\s+part.*motion\s+to\s+dismiss',
        ],
    },

    # Summary Judgment
    'msj': {
        'granted': [
            r'(?:motion\s+for\s+)?summary\s+judgment.*?\b(granted|granting)\b',
            r'grant(?:s|ed|ing)?\s+(?:the\s+)?(?:defendant.{0,20}|plaintiff.{0,20})?(?:motion\s+for\s+)?summary\s+judgment',
            r'summary\s+judgment\s+(?:is\s+)?(?:hereby\s+)?granted',
        ],
        'denied': [
            r'(?:motion\s+for\s+)?summary\s+judgment.*?\b(denied|denying)\b',
            r'den(?:y|ies|ied|ying)\s+(?:the\s+)?(?:motion\s+for\s+)?summary\s+judgment',
        ],
        'partial': [
            r'summary\s+judgment.*?\b(granted\s+in\s+part|partially\s+granted)\b',
        ],
    },

    # Motion to Compel
    'mtc': {
        'granted': [
            r'motion\s+to\s+compel.*?\b(granted|granting)\b',
            r'grant(?:s|ed|ing)?\s+(?:the\s+)?motion\s+to\s+compel',
        ],
        'denied': [
            r'motion\s+to\s+compel.*?\b(denied|denying)\b',
            r'den(?:y|ies|ied|ying)\s+(?:the\s+)?motion\s+to\s+compel',
        ],
    },

    # Preliminary Injunction
    'pi': {
        'granted': [
            r'(?:motion\s+for\s+)?preliminary\s+injunction.*?\b(granted|granting)\b',
            r'grant(?:s|ed|ing)?\s+(?:the\s+)?(?:motion\s+for\s+)?preliminary\s+injunction',
        ],
        'denied': [
            r'(?:motion\s+for\s+)?preliminary\s+injunction.*?\b(denied|denying)\b',
        ],
    },

    # TRO
    'tro': {
        'granted': [
            r'(?:motion\s+for\s+)?(?:temporary\s+restraining\s+order|tro).*?\b(granted|granting)\b',
        ],
        'denied': [
            r'(?:motion\s+for\s+)?(?:temporary\s+restraining\s+order|tro).*?\b(denied|denying)\b',
        ],
    },

    # Class Certification
    'class_cert': {
        'granted': [
            r'(?:motion\s+for\s+)?class\s+certification.*?\b(granted|granting)\b',
            r'class\s+(?:is\s+)?(?:hereby\s+)?certified',
        ],
        'denied': [
            r'(?:motion\s+for\s+)?class\s+certification.*?\b(denied|denying)\b',
            r'class\s+certification\s+(?:is\s+)?denied',
        ],
    },

    # Motion for Sanctions
    'sanctions': {
        'granted': [
            r'motion\s+for\s+sanctions.*?\b(granted|granting)\b',
            r'sanctions\s+(?:are\s+)?(?:hereby\s+)?(?:imposed|awarded|granted)',
        ],
        'denied': [
            r'motion\s+for\s+sanctions.*?\b(denied|denying)\b',
        ],
    },

    # Remand
    'remand': {
        'granted': [
            r'motion\s+to\s+remand.*?\b(granted|granting)\b',
            r'case\s+(?:is\s+)?(?:hereby\s+)?remanded',
        ],
        'denied': [
            r'motion\s+to\s+remand.*?\b(denied|denying)\b',
        ],
    },
}

# Case outcome patterns
CASE_OUTCOME_PATTERNS = {
    'dismissed_with_prejudice': [
        r'dismiss(?:ed|es|ing)?\s+with\s+prejudice',
        r'with\s+prejudice.*dismiss',
        r'case\s+closed.*with\s+prejudice',
    ],
    'dismissed_without_prejudice': [
        r'dismiss(?:ed|es|ing)?\s+without\s+prejudice',
        r'without\s+prejudice.*dismiss',
    ],
    'voluntary_dismissal': [
        r'voluntary\s+dismiss',
        r'notice\s+of\s+(?:voluntary\s+)?dismissal',
        r'stipulat(?:ed|ion)?\s+(?:of\s+)?dismissal',
    ],
    'settlement': [
        r'\bsettl(?:ed|ement|ing)\b',
        r'stipulat(?:ed|ion)\s+(?:and\s+)?(?:order\s+)?(?:of\s+)?(?:settlement|compromise)',
        r'consent\s+(?:decree|judgment)',
    ],
    'default_judgment': [
        r'default\s+judgment',
        r'judgment\s+by\s+default',
    ],
    'jury_verdict': [
        r'jury\s+verdict',
        r'verdict\s+(?:for|in\s+favor\s+of)',
    ],
    'bench_trial': [
        r'bench\s+trial',
        r'findings\s+of\s+fact\s+and\s+conclusions\s+of\s+law',
    ],
}


def analyze_docket_text(text: str) -> Dict:
    """
    Analyze docket text for motion outcomes and case disposition.

    Returns dict with:
    - motions: list of MotionOutcome
    - case_outcome: str or None
    - indicators: dict of boolean flags
    """
    if not text:
        return {'motions': [], 'case_outcome': None, 'indicators': {}}

    text_lower = text.lower()
    results = {
        'motions': [],
        'case_outcome': None,
        'indicators': {}
    }

    # Check motion patterns
    for motion_type, outcomes in MOTION_PATTERNS.items():
        for outcome, patterns in outcomes.items():
            for pattern in patterns:
                match = re.search(pattern, text_lower)
                if match:
                    # Get surrounding context
                    start = max(0, match.start() - 20)
                    end = min(len(text), match.end() + 50)
                    snippet = text[start:end].strip()

                    results['motions'].append(MotionOutcome(
                        motion_type=motion_type,
                        outcome=outcome,
                        text_snippet=snippet
                    ))
                    break  # Only count first match per outcome type

    # Check case outcome patterns
    for outcome_type, patterns in CASE_OUTCOME_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                results['case_outcome'] = outcome_type
                results['indicators'][outcome_type] = True
                break

    return results


def extract_judge_from_text(text: str) -> Optional[str]:
    """Extract judge name from docket text."""
    if not text:
        return None

    patterns = [
        r'(?:Signed\s+by\s+)?(?:Senior\s+)?(?:Magistrate\s+)?(?:Chief\s+)?Judge\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-zA-Z\-\']+)',
        r'Honorable\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-zA-Z\-\']+)',
        r'\(([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-zA-Z\-\']+)\s*,?\s*(?:District|Magistrate)?\s*Judge\)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip()
            name = re.sub(r'\s+', ' ', name)
            if len(name) > 5:
                return name

    return None


def build_judge_outcome_profile(conn, judge_name: str = None, court_id: str = None) -> Dict:
    """
    Build outcome profile for a judge from PACER outcomes data.

    Returns aggregated statistics on motion grant rates, dismissal patterns, etc.
    """
    # Query PACER outcomes
    query = """
        SELECT court_code, case_number, outcome_type, key_entry_text
        FROM pacer_outcomes
        WHERE key_entry_text IS NOT NULL AND key_entry_text != ''
    """
    params = []

    if court_id:
        query += " AND court_code = ?"
        params.append(court_id)

    cur = conn.execute(query, params)

    # Aggregate outcomes
    profile = {
        'total_cases': 0,
        'motions': {
            'mtd': {'granted': 0, 'denied': 0, 'partial': 0},
            'msj': {'granted': 0, 'denied': 0, 'partial': 0},
            'mtc': {'granted': 0, 'denied': 0},
            'pi': {'granted': 0, 'denied': 0},
            'class_cert': {'granted': 0, 'denied': 0},
            'sanctions': {'granted': 0, 'denied': 0},
            'remand': {'granted': 0, 'denied': 0},
        },
        'case_outcomes': {
            'dismissed_with_prejudice': 0,
            'dismissed_without_prejudice': 0,
            'voluntary_dismissal': 0,
            'settlement': 0,
            'default_judgment': 0,
            'jury_verdict': 0,
        },
        'by_judge': {}
    }

    for row in cur.fetchall():
        text = row['key_entry_text']
        analysis = analyze_docket_text(text)

        # Extract judge if present
        judge = extract_judge_from_text(text)
        if judge_name and judge and judge_name.lower() not in judge.lower():
            continue

        profile['total_cases'] += 1

        # Count motions
        for motion in analysis['motions']:
            if motion.motion_type in profile['motions']:
                if motion.outcome in profile['motions'][motion.motion_type]:
                    profile['motions'][motion.motion_type][motion.outcome] += 1

        # Count case outcomes
        if analysis['case_outcome'] and analysis['case_outcome'] in profile['case_outcomes']:
            profile['case_outcomes'][analysis['case_outcome']] += 1

        # Track by judge
        if judge:
            if judge not in profile['by_judge']:
                profile['by_judge'][judge] = {
                    'cases': 0,
                    'mtd_granted': 0, 'mtd_denied': 0,
                    'msj_granted': 0, 'msj_denied': 0,
                    'dismissals': 0, 'settlements': 0
                }
            profile['by_judge'][judge]['cases'] += 1

            for motion in analysis['motions']:
                if motion.motion_type == 'mtd':
                    if motion.outcome == 'granted':
                        profile['by_judge'][judge]['mtd_granted'] += 1
                    elif motion.outcome == 'denied':
                        profile['by_judge'][judge]['mtd_denied'] += 1
                elif motion.motion_type == 'msj':
                    if motion.outcome == 'granted':
                        profile['by_judge'][judge]['msj_granted'] += 1
                    elif motion.outcome == 'denied':
                        profile['by_judge'][judge]['msj_denied'] += 1

            if analysis['case_outcome'] in ['dismissed_with_prejudice', 'dismissed_without_prejudice']:
                profile['by_judge'][judge]['dismissals'] += 1
            elif analysis['case_outcome'] in ['settlement', 'voluntary_dismissal']:
                profile['by_judge'][judge]['settlements'] += 1

    return profile


def calculate_grant_rates(profile: Dict) -> Dict:
    """Calculate grant rates from profile."""
    rates = {}

    for motion_type, outcomes in profile['motions'].items():
        granted = outcomes.get('granted', 0)
        denied = outcomes.get('denied', 0)
        partial = outcomes.get('partial', 0)
        total = granted + denied + partial

        if total > 0:
            rates[motion_type] = {
                'grant_rate': (granted + partial * 0.5) / total,
                'total': total,
                'granted': granted,
                'denied': denied,
                'partial': partial
            }

    return rates


if __name__ == '__main__':
    import sys
    sys.path.insert(0, '.')
    from app.models.db import get_conn, init_db
    init_db()
    conn = get_conn()

    print("=== OUTCOME ANALYSIS DEMO ===\n")

    # Test on sample text
    test_texts = [
        "ORDER granting 45 Motion to Dismiss. Case dismissed with prejudice. Signed by Judge John Smith.",
        "ORDER denying 23 Motion for Summary Judgment. The motion is DENIED. Signed by Magistrate Judge Jane Doe.",
        "STIPULATION of Dismissal by Plaintiff pursuant to settlement agreement.",
        "ORDER granting in part and denying in part 67 Motion to Compel Discovery.",
    ]

    print("--- Pattern Extraction Tests ---\n")
    for text in test_texts:
        print(f"Text: {text[:80]}...")
        result = analyze_docket_text(text)
        print(f"  Motions: {[(m.motion_type, m.outcome) for m in result['motions']]}")
        print(f"  Case outcome: {result['case_outcome']}")
        print()

    print("\n--- Full Profile from PACER Data ---\n")
    profile = build_judge_outcome_profile(conn)
    print(f"Total cases analyzed: {profile['total_cases']}")

    print("\nMotion outcomes:")
    for motion_type, outcomes in profile['motions'].items():
        total = sum(outcomes.values())
        if total > 0:
            print(f"  {motion_type.upper()}: granted={outcomes.get('granted',0)}, denied={outcomes.get('denied',0)}, partial={outcomes.get('partial',0)}")

    print("\nCase outcomes:")
    for outcome, count in profile['case_outcomes'].items():
        if count > 0:
            print(f"  {outcome}: {count}")

    print("\nBy judge:")
    for judge, stats in sorted(profile['by_judge'].items(), key=lambda x: -x[1]['cases']):
        if stats['cases'] >= 1:
            print(f"  {judge}: {stats['cases']} cases, MTD g/d: {stats['mtd_granted']}/{stats['mtd_denied']}, MSJ g/d: {stats['msj_granted']}/{stats['msj_denied']}")

    rates = calculate_grant_rates(profile)
    print("\nGrant rates:")
    for motion_type, data in rates.items():
        print(f"  {motion_type.upper()}: {data['grant_rate']*100:.0f}% ({data['total']} rulings)")
