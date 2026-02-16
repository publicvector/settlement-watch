"""
Comprehensive Case Analytics System for Settlement Watch.

Analyzes complaints, courts, dockets, defendants, and outcomes to provide
full case lifecycle intelligence and value predictions.
"""
import sqlite3
import json
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
from pathlib import Path


@dataclass
class CourtProfile:
    """Analytics profile for a court/jurisdiction."""
    name: str
    case_count: int
    total_settlements: float
    avg_settlement: float
    median_settlement: float
    min_settlement: float
    max_settlement: float
    success_rate: float  # % of cases resulting in payment
    primary_case_types: List[str] = field(default_factory=list)


@dataclass
class DefendantProfile:
    """Analytics profile for a defendant entity."""
    name: str
    case_count: int
    total_paid: float
    avg_payment: float
    case_types: List[str] = field(default_factory=list)
    settlement_velocity: float = 0  # Avg days to settle
    repeat_offender_score: float = 0  # Higher = more repeat cases


@dataclass
class CaseScorecard:
    """Full analytics scorecard for case value prediction."""
    # Input factors
    cause_of_action: str
    court: str
    defendant: str
    source_type: str

    # Component scores (0-100)
    cause_score: float
    court_score: float
    defendant_score: float
    source_score: float

    # Weighted overall score
    overall_score: float

    # Value estimates
    low_estimate: float
    mid_estimate: float
    high_estimate: float

    # Confidence
    confidence: float

    # Supporting data
    comparable_cases: int
    factors: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        def fmt(amt):
            if amt >= 1e9: return f"${amt/1e9:.1f}B"
            if amt >= 1e6: return f"${amt/1e6:.1f}M"
            if amt >= 1e3: return f"${amt/1e3:.1f}K"
            return f"${amt:,.0f}"

        return f"""
╔══════════════════════════════════════════════════════════════════╗
║                    CASE ANALYTICS SCORECARD                       ║
╠══════════════════════════════════════════════════════════════════╣
║ Cause: {self.cause_of_action[:55]:<55} ║
║ Court: {self.court[:55]:<55} ║
║ Defendant: {self.defendant[:51]:<51} ║
╠══════════════════════════════════════════════════════════════════╣
║ COMPONENT SCORES                                                  ║
║   Cause of Action:  {self.cause_score:>5.0f}/100  {'█' * int(self.cause_score/5):<20} ║
║   Court/Venue:      {self.court_score:>5.0f}/100  {'█' * int(self.court_score/5):<20} ║
║   Defendant:        {self.defendant_score:>5.0f}/100  {'█' * int(self.defendant_score/5):<20} ║
║   Source Type:      {self.source_score:>5.0f}/100  {'█' * int(self.source_score/5):<20} ║
║   ─────────────────────────────────────────────────────           ║
║   OVERALL SCORE:    {self.overall_score:>5.0f}/100  {'█' * int(self.overall_score/5):<20} ║
╠══════════════════════════════════════════════════════════════════╣
║ VALUE ESTIMATES                                                   ║
║   Conservative: {fmt(self.low_estimate):>15}                                ║
║   Mid-Range:    {fmt(self.mid_estimate):>15}                                ║
║   Aggressive:   {fmt(self.high_estimate):>15}                                ║
╠══════════════════════════════════════════════════════════════════╣
║ Confidence: {self.confidence:.0%}  │  Comparable Cases: {self.comparable_cases:<5}           ║
╚══════════════════════════════════════════════════════════════════╝
"""


class CaseAnalytics:
    """
    Comprehensive case analytics engine.

    Analyzes all dimensions of a case to predict value and provide intelligence.
    """

    # Source type value multipliers
    SOURCE_MULTIPLIERS = {
        'doj': 1.30,           # DOJ settlements tend to be larger
        'sec': 1.25,           # SEC enforcement
        'ftc': 1.15,           # FTC consumer protection
        'cfpb': 1.10,          # CFPB financial protection
        'state ag': 1.05,      # State AG actions
        'class action': 1.00,  # Class actions (baseline)
        'mdi': 1.20,           # MDL
        'verdict': 0.90,       # Verdicts (often reduced on appeal)
        'arbitration': 0.70,   # Arbitration awards
    }

    # Court tier rankings (based on historical outcomes)
    COURT_TIERS = {
        'tier_1': ['n.d. california', 's.d. new york', 'e.d. pennsylvania',
                   'c.d. california', 'd. new jersey', 'e.d. new york'],
        'tier_2': ['d. delaware', 'n.d. illinois', 's.d. florida',
                   'e.d. texas', 'd. massachusetts', 'd. colorado'],
        'tier_3': ['california state', 'new york state', 'texas state'],
    }

    def __init__(self, db_path: str = "db/settlement_watch.db"):
        self.db_path = db_path
        self._court_cache = None
        self._defendant_cache = None
        self._cause_cache = None
        self._source_cache = None

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # =========================================================================
    # COURT ANALYSIS
    # =========================================================================

    def analyze_courts(self, min_cases: int = 2) -> List[CourtProfile]:
        """Analyze all courts and their settlement patterns."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                court,
                COUNT(*) as case_count,
                SUM(settlement_amount) as total,
                AVG(settlement_amount) as avg_val,
                MIN(settlement_amount) as min_val,
                MAX(settlement_amount) as max_val,
                GROUP_CONCAT(DISTINCT nature_of_suit) as case_types
            FROM case_outcomes
            WHERE court IS NOT NULL AND court <> ''
            AND settlement_amount > 0
            GROUP BY court
            HAVING COUNT(*) >= ?
            ORDER BY AVG(settlement_amount) DESC
        """, (min_cases,))

        profiles = []
        for row in cursor.fetchall():
            profiles.append(CourtProfile(
                name=row[0],
                case_count=row[1],
                total_settlements=row[2],
                avg_settlement=row[3],
                median_settlement=row[3],  # Approximation
                min_settlement=row[4],
                max_settlement=row[5],
                success_rate=1.0,  # All have settlements
                primary_case_types=(row[6] or '').split(',')[:5]
            ))

        conn.close()
        self._court_cache = {p.name.lower(): p for p in profiles}
        return profiles

    def get_court_score(self, court: str) -> Tuple[float, Dict]:
        """Get a score for a court based on historical outcomes."""
        if not self._court_cache:
            self.analyze_courts()

        court_lower = court.lower().strip()

        # Direct match
        if court_lower in self._court_cache:
            profile = self._court_cache[court_lower]
            # Score based on average settlement (log scale)
            import math
            avg_log = math.log10(max(profile.avg_settlement, 1))
            score = min(100, max(0, (avg_log - 5) * 20))  # Scale: $100K=0, $10B=100
            return score, {'profile': profile, 'match_type': 'exact'}

        # Partial match
        for key, profile in self._court_cache.items():
            if court_lower in key or key in court_lower:
                import math
                avg_log = math.log10(max(profile.avg_settlement, 1))
                score = min(100, max(0, (avg_log - 5) * 20))
                return score * 0.9, {'profile': profile, 'match_type': 'partial'}

        # Tier-based default
        for tier, courts in self.COURT_TIERS.items():
            for tier_court in courts:
                if tier_court in court_lower:
                    tier_scores = {'tier_1': 75, 'tier_2': 60, 'tier_3': 50}
                    return tier_scores.get(tier, 40), {'match_type': 'tier', 'tier': tier}

        # Default federal vs state
        if 'district' in court_lower or 'federal' in court_lower:
            return 55, {'match_type': 'federal_default'}
        return 40, {'match_type': 'state_default'}

    # =========================================================================
    # DEFENDANT ANALYSIS
    # =========================================================================

    def analyze_defendants(self, min_cases: int = 2) -> List[DefendantProfile]:
        """Analyze defendant payment patterns."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                defendant,
                COUNT(*) as case_count,
                SUM(settlement_amount) as total_paid,
                AVG(settlement_amount) as avg_paid,
                GROUP_CONCAT(DISTINCT nature_of_suit) as case_types
            FROM case_outcomes
            WHERE defendant IS NOT NULL AND defendant <> ''
            AND settlement_amount > 0
            GROUP BY defendant
            HAVING COUNT(*) >= ?
            ORDER BY SUM(settlement_amount) DESC
        """, (min_cases,))

        profiles = []
        for row in cursor.fetchall():
            # Calculate repeat offender score (more cases = higher score)
            repeat_score = min(100, row[1] * 15)

            profiles.append(DefendantProfile(
                name=row[0],
                case_count=row[1],
                total_paid=row[2],
                avg_payment=row[3],
                case_types=(row[4] or '').split(',')[:5],
                repeat_offender_score=repeat_score
            ))

        conn.close()
        self._defendant_cache = {p.name.lower(): p for p in profiles}
        return profiles

    def get_defendant_score(self, defendant: str) -> Tuple[float, Dict]:
        """Get a score for defendant's likelihood to pay large settlements."""
        if not self._defendant_cache:
            self.analyze_defendants()

        defendant_lower = defendant.lower().strip()

        # Check known defendants
        for key, profile in self._defendant_cache.items():
            if defendant_lower in key or key in defendant_lower:
                import math
                # Score based on historical average payment
                avg_log = math.log10(max(profile.avg_payment, 1))
                base_score = min(100, max(0, (avg_log - 5) * 20))
                # Bonus for repeat offender (more data)
                repeat_bonus = min(20, profile.case_count * 3)
                score = min(100, base_score + repeat_bonus)
                return score, {
                    'profile': profile,
                    'match_type': 'known',
                    'total_paid': profile.total_paid,
                    'avg_payment': profile.avg_payment
                }

        # Fortune 500 detection
        fortune_keywords = ['bank', 'pharma', 'insurance', 'motors', 'airlines',
                           'walmart', 'amazon', 'google', 'apple', 'meta', 'microsoft']
        for kw in fortune_keywords:
            if kw in defendant_lower:
                return 70, {'match_type': 'fortune_inferred'}

        # Government defendant
        if any(g in defendant_lower for g in ['city of', 'state of', 'county', 'department']):
            return 50, {'match_type': 'government'}

        return 45, {'match_type': 'unknown'}

    # =========================================================================
    # CAUSE OF ACTION ANALYSIS
    # =========================================================================

    def analyze_causes(self) -> Dict[str, Dict]:
        """Get cause of action statistics."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                nature_of_suit,
                COUNT(*) as case_count,
                AVG(settlement_amount) as avg_val,
                MIN(settlement_amount) as min_val,
                MAX(settlement_amount) as max_val
            FROM case_outcomes
            WHERE nature_of_suit IS NOT NULL AND nature_of_suit <> ''
            AND settlement_amount > 0
            GROUP BY nature_of_suit
            ORDER BY AVG(settlement_amount) DESC
        """)

        causes = {}
        all_avgs = []
        for row in cursor.fetchall():
            causes[row[0].lower()] = {
                'name': row[0],
                'count': row[1],
                'avg': row[2],
                'min': row[3],
                'max': row[4]
            }
            all_avgs.append(row[2])

        # Calculate percentile ranks
        sorted_avgs = sorted(all_avgs)
        for cause_data in causes.values():
            rank = sorted_avgs.index(cause_data['avg'])
            cause_data['percentile'] = (rank / len(sorted_avgs)) * 100

        conn.close()
        self._cause_cache = causes
        return causes

    def get_cause_score(self, cause: str) -> Tuple[float, Dict]:
        """Get a score for a cause of action based on historical value."""
        if not self._cause_cache:
            self.analyze_causes()

        cause_lower = cause.lower().strip()

        # Direct match
        if cause_lower in self._cause_cache:
            data = self._cause_cache[cause_lower]
            return data['percentile'], {'data': data, 'match_type': 'exact'}

        # Partial match
        for key, data in self._cause_cache.items():
            if cause_lower in key or key in cause_lower:
                return data['percentile'] * 0.9, {'data': data, 'match_type': 'partial'}

        return 50, {'match_type': 'unknown'}

    # =========================================================================
    # SOURCE ANALYSIS
    # =========================================================================

    def analyze_sources(self) -> Dict[str, Dict]:
        """Analyze settlement patterns by source."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                source,
                COUNT(*) as case_count,
                AVG(settlement_amount) as avg_val,
                SUM(settlement_amount) as total_val
            FROM case_outcomes
            WHERE source IS NOT NULL AND source <> ''
            AND settlement_amount > 0
            GROUP BY source
            ORDER BY AVG(settlement_amount) DESC
        """)

        sources = {}
        for row in cursor.fetchall():
            sources[row[0].lower()] = {
                'name': row[0],
                'count': row[1],
                'avg': row[2],
                'total': row[3]
            }

        conn.close()
        self._source_cache = sources
        return sources

    def get_source_score(self, source: str) -> Tuple[float, Dict]:
        """Get a score based on source type."""
        if not self._source_cache:
            self.analyze_sources()

        source_lower = source.lower().strip()

        # Check for known source types
        for source_type, multiplier in self.SOURCE_MULTIPLIERS.items():
            if source_type in source_lower:
                score = min(100, multiplier * 50)
                return score, {'source_type': source_type, 'multiplier': multiplier}

        # Check cache
        for key, data in self._source_cache.items():
            if source_lower in key or key in source_lower:
                import math
                avg_log = math.log10(max(data['avg'], 1))
                score = min(100, max(0, (avg_log - 5) * 20))
                return score, {'data': data, 'match_type': 'cached'}

        return 50, {'match_type': 'unknown'}

    # =========================================================================
    # COMPOSITE SCORING
    # =========================================================================

    def score_case(
        self,
        cause_of_action: str,
        court: str = "",
        defendant: str = "",
        source: str = "",
        custom_factors: Dict[str, float] = None
    ) -> CaseScorecard:
        """
        Generate a comprehensive scorecard for a case.

        Args:
            cause_of_action: Primary cause of action
            court: Court/jurisdiction
            defendant: Defendant name
            source: Source type (DOJ, class action, etc.)
            custom_factors: Additional multipliers

        Returns:
            CaseScorecard with scores and value estimates
        """
        # Get component scores
        cause_score, cause_info = self.get_cause_score(cause_of_action)
        court_score, court_info = self.get_court_score(court) if court else (50, {})
        defendant_score, defendant_info = self.get_defendant_score(defendant) if defendant else (50, {})
        source_score, source_info = self.get_source_score(source) if source else (50, {})

        # Weighted average (cause is most important)
        weights = {'cause': 0.40, 'court': 0.20, 'defendant': 0.25, 'source': 0.15}
        overall_score = (
            cause_score * weights['cause'] +
            court_score * weights['court'] +
            defendant_score * weights['defendant'] +
            source_score * weights['source']
        )

        # Get base values from cause analysis
        if not self._cause_cache:
            self.analyze_causes()

        cause_lower = cause_of_action.lower()
        base_values = None
        for key, data in self._cause_cache.items():
            if cause_lower in key or key in cause_lower:
                base_values = data
                break

        if base_values:
            # Adjust values based on overall score
            score_multiplier = 0.5 + (overall_score / 100)  # 0.5x to 1.5x
            low_estimate = base_values['min'] * score_multiplier
            mid_estimate = base_values['avg'] * score_multiplier
            high_estimate = base_values['max'] * score_multiplier * 0.5  # Cap high
            comparable_cases = base_values['count']
        else:
            # Defaults based on overall score
            low_estimate = 1_000_000 * (overall_score / 50)
            mid_estimate = 10_000_000 * (overall_score / 50)
            high_estimate = 100_000_000 * (overall_score / 50)
            comparable_cases = 0

        # Apply custom factors
        if custom_factors:
            for factor, mult in custom_factors.items():
                low_estimate *= mult
                mid_estimate *= mult
                high_estimate *= mult

        # Calculate confidence
        confidence = min(1.0, 0.3 + (comparable_cases / 50) * 0.5)
        if court_info.get('match_type') == 'exact':
            confidence += 0.1
        if defendant_info.get('match_type') == 'known':
            confidence += 0.1
        confidence = min(1.0, confidence)

        return CaseScorecard(
            cause_of_action=cause_of_action,
            court=court or "Not specified",
            defendant=defendant or "Not specified",
            source_type=source or "Not specified",
            cause_score=cause_score,
            court_score=court_score,
            defendant_score=defendant_score,
            source_score=source_score,
            overall_score=overall_score,
            low_estimate=low_estimate,
            mid_estimate=mid_estimate,
            high_estimate=high_estimate,
            confidence=confidence,
            comparable_cases=comparable_cases,
            factors={
                'cause_info': cause_info,
                'court_info': court_info,
                'defendant_info': defendant_info,
                'source_info': source_info,
                'weights': weights,
            }
        )

    # =========================================================================
    # REPORTS
    # =========================================================================

    def generate_court_report(self) -> str:
        """Generate a report of court performance."""
        profiles = self.analyze_courts(min_cases=2)

        lines = [
            "=" * 80,
            "COURT ANALYSIS REPORT",
            "=" * 80,
            "",
            f"{'Court':<45} {'Cases':>6} {'Avg Settlement':>15} {'Total':>15}",
            "-" * 80
        ]

        for p in profiles[:30]:
            lines.append(
                f"{p.name[:45]:<45} {p.case_count:>6} "
                f"${p.avg_settlement/1e6:>13.1f}M ${p.total_settlements/1e6:>13.1f}M"
            )

        return "\n".join(lines)

    def generate_defendant_report(self) -> str:
        """Generate a report of defendant payment patterns."""
        profiles = self.analyze_defendants(min_cases=2)

        lines = [
            "=" * 80,
            "DEFENDANT ANALYSIS REPORT - REPEAT PAYERS",
            "=" * 80,
            "",
            f"{'Defendant':<35} {'Cases':>6} {'Total Paid':>15} {'Avg Payment':>15}",
            "-" * 80
        ]

        for p in profiles[:30]:
            lines.append(
                f"{p.name[:35]:<35} {p.case_count:>6} "
                f"${p.total_paid/1e6:>13.1f}M ${p.avg_payment/1e6:>13.1f}M"
            )

        return "\n".join(lines)

    def generate_source_report(self) -> str:
        """Generate a report by settlement source."""
        sources = self.analyze_sources()

        lines = [
            "=" * 80,
            "SOURCE ANALYSIS REPORT",
            "=" * 80,
            "",
            f"{'Source':<35} {'Cases':>8} {'Avg Settlement':>15} {'Total':>18}",
            "-" * 80
        ]

        sorted_sources = sorted(sources.values(), key=lambda x: x['avg'], reverse=True)
        for s in sorted_sources[:25]:
            lines.append(
                f"{s['name'][:35]:<35} {s['count']:>8} "
                f"${s['avg']/1e6:>13.1f}M ${s['total']/1e6:>16.1f}M"
            )

        return "\n".join(lines)

    def export_analytics_json(self, output_path: str = "analytics/full_analytics.json"):
        """Export all analytics data to JSON."""
        courts = self.analyze_courts()
        defendants = self.analyze_defendants()
        causes = self.analyze_causes()
        sources = self.analyze_sources()

        output = {
            'generated_at': datetime.now().isoformat(),
            'courts': [
                {
                    'name': p.name,
                    'case_count': p.case_count,
                    'total_settlements': p.total_settlements,
                    'avg_settlement': p.avg_settlement,
                }
                for p in courts[:50]
            ],
            'defendants': [
                {
                    'name': p.name,
                    'case_count': p.case_count,
                    'total_paid': p.total_paid,
                    'avg_payment': p.avg_payment,
                    'repeat_score': p.repeat_offender_score,
                }
                for p in defendants[:50]
            ],
            'causes': list(causes.values()),
            'sources': list(sources.values()),
        }

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)

        return output_path


def main():
    """CLI for case analytics."""
    import sys

    analytics = CaseAnalytics()

    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()

        if cmd == 'courts':
            print(analytics.generate_court_report())
        elif cmd == 'defendants':
            print(analytics.generate_defendant_report())
        elif cmd == 'sources':
            print(analytics.generate_source_report())
        elif cmd == 'export':
            path = analytics.export_analytics_json()
            print(f"Exported to: {path}")
        elif cmd == 'score':
            # Score a specific case
            if len(sys.argv) >= 3:
                cause = sys.argv[2]
                court = sys.argv[3] if len(sys.argv) > 3 else ""
                defendant = sys.argv[4] if len(sys.argv) > 4 else ""
                source = sys.argv[5] if len(sys.argv) > 5 else ""

                scorecard = analytics.score_case(cause, court, defendant, source)
                print(scorecard.summary())
            else:
                print("Usage: python case_analytics.py score <cause> [court] [defendant] [source]")
        else:
            print(f"Unknown command: {cmd}")
            print("Commands: courts, defendants, sources, export, score")
    else:
        # Print all reports
        print(analytics.generate_court_report())
        print("\n")
        print(analytics.generate_defendant_report())
        print("\n")
        print(analytics.generate_source_report())


if __name__ == "__main__":
    main()
