"""
Case Valuation Model for Settlement Watch.

Maps complaints and causes of action to expected settlement amounts
based on historical data analysis.
"""
import sqlite3
import json
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path


@dataclass
class ValuationResult:
    """Results from case valuation analysis."""
    cause_of_action: str
    sample_size: int

    # Core statistics (in dollars)
    median: float
    mean: float
    min_value: float
    max_value: float

    # Percentiles
    p25: float  # 25th percentile (conservative estimate)
    p75: float  # 75th percentile (aggressive estimate)

    # Adjusted estimates
    low_estimate: float      # Conservative: P25 adjusted
    mid_estimate: float      # Median-based
    high_estimate: float     # Aggressive: P75 adjusted

    # Confidence metrics
    confidence_score: float  # 0-1 based on sample size
    volatility: float        # Standard deviation / mean (coefficient of variation)

    # Multipliers applied
    multipliers: Dict[str, float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def format_currency(self, amount: float) -> str:
        """Format dollar amount for display."""
        if amount >= 1_000_000_000:
            return f"${amount/1_000_000_000:.1f}B"
        elif amount >= 1_000_000:
            return f"${amount/1_000_000:.1f}M"
        elif amount >= 1_000:
            return f"${amount/1_000:.1f}K"
        return f"${amount:,.0f}"

    def summary(self) -> str:
        """Generate a summary of the valuation."""
        return f"""
Case Valuation: {self.cause_of_action}
{'='*50}
Sample Size: {self.sample_size} cases
Confidence: {self.confidence_score:.0%}

Historical Range:
  Min: {self.format_currency(self.min_value)}
  Max: {self.format_currency(self.max_value)}

Expected Value Estimates:
  Conservative (P25): {self.format_currency(self.low_estimate)}
  Mid-Range (Median): {self.format_currency(self.mid_estimate)}
  Aggressive (P75):   {self.format_currency(self.high_estimate)}

Percentiles:
  25th: {self.format_currency(self.p25)}
  50th: {self.format_currency(self.median)}
  75th: {self.format_currency(self.p75)}
  Mean: {self.format_currency(self.mean)}
"""


class CaseValuator:
    """
    Estimates case settlement value based on cause of action and other factors.

    Uses historical settlement data to provide range estimates with
    confidence scores based on sample size and data quality.
    """

    # Jurisdiction multipliers (relative to national average)
    JURISDICTION_MULTIPLIERS = {
        # High-value jurisdictions
        'california': 1.25,
        'new york': 1.20,
        'texas': 1.10,
        'florida': 1.05,
        'illinois': 1.05,
        'pennsylvania': 1.00,
        'new jersey': 1.10,
        # Federal courts by circuit
        '9th circuit': 1.20,
        '2nd circuit': 1.15,
        '3rd circuit': 1.05,
        '5th circuit': 0.95,
        '11th circuit': 1.00,
        # Default
        'federal': 1.05,
        'state': 0.95,
    }

    # Defendant type multipliers
    DEFENDANT_MULTIPLIERS = {
        'fortune_100': 1.50,
        'fortune_500': 1.25,
        'large_corporation': 1.10,
        'mid_size_company': 1.00,
        'small_company': 0.75,
        'government': 0.90,
        'individual': 0.50,
    }

    # Class size multipliers (for class actions)
    CLASS_SIZE_MULTIPLIERS = {
        'mega': 1.30,       # > 1M class members
        'large': 1.15,      # 100K - 1M
        'medium': 1.00,     # 10K - 100K
        'small': 0.85,      # 1K - 10K
        'individual': 0.60,  # Single plaintiff
    }

    # Cause of action aliases for normalization
    CAUSE_ALIASES = {
        'product liability': ['products liability', 'defective product', 'product defect'],
        'securities': ['securities fraud', 'sec violation', '10b-5', 'stock fraud'],
        'data breach': ['data privacy', 'cyber breach', 'hacking', 'data theft'],
        'employment': ['employment discrimination', 'wrongful termination', 'labor'],
        'antitrust': ['anti-trust', 'price fixing', 'monopoly', 'competition'],
        'environmental': ['environmental contamination', 'pollution', 'toxic tort'],
        'consumer protection': ['consumer fraud', 'deceptive practices', 'unfair practices'],
        'civil rights': ['civil rights violation', 'discrimination', 'constitutional'],
        'bipa': ['biometric', 'biometric privacy', 'facial recognition'],
        'tcpa': ['telephone consumer protection', 'robocall', 'telemarketing'],
        'wage & hour': ['wage and hour', 'overtime', 'flsa', 'unpaid wages'],
        'medical malpractice': ['medical negligence', 'hospital negligence'],
        'premises liability': ['slip and fall', 'property liability'],
        'sexual abuse': ['sexual assault', 'sexual harassment', 'abuse'],
        'healthcare fraud': ['medicare fraud', 'medicaid fraud', 'false claims'],
    }

    def __init__(self, db_path: str = "db/settlement_watch.db"):
        self.db_path = db_path
        self._stats_cache = None

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _normalize_cause(self, cause: str) -> str:
        """Normalize cause of action to match database categories."""
        cause_lower = cause.lower().strip()

        # Check direct match first
        for canonical, aliases in self.CAUSE_ALIASES.items():
            if cause_lower == canonical:
                return canonical.title()
            if cause_lower in aliases:
                return canonical.title()

        # Return title-cased original if no alias found
        return cause.title()

    def _calculate_statistics(self) -> Dict[str, Dict[str, float]]:
        """Calculate statistics for all causes of action from database."""
        if self._stats_cache:
            return self._stats_cache

        conn = self._get_connection()
        cursor = conn.cursor()

        # Get all settlements grouped by cause
        cursor.execute("""
            SELECT nature_of_suit, settlement_amount
            FROM case_outcomes
            WHERE settlement_amount > 0
            AND nature_of_suit IS NOT NULL
            AND nature_of_suit != ''
            ORDER BY nature_of_suit, settlement_amount
        """)

        rows = cursor.fetchall()
        conn.close()

        # Group by cause
        causes = {}
        for cause, amount in rows:
            if cause not in causes:
                causes[cause] = []
            causes[cause].append(amount)

        # Calculate statistics for each cause
        stats = {}
        for cause, amounts in causes.items():
            amounts = sorted(amounts)
            n = len(amounts)
            if n < 1:
                continue

            mean_val = sum(amounts) / n
            min_val = amounts[0]
            max_val = amounts[-1]

            # Percentiles
            p25_idx = max(0, int(n * 0.25))
            p50_idx = max(0, int(n * 0.50))
            p75_idx = min(n-1, int(n * 0.75))

            p25 = amounts[p25_idx]
            median = amounts[p50_idx]
            p75 = amounts[p75_idx]

            # Standard deviation
            variance = sum((x - mean_val) ** 2 for x in amounts) / n
            std_dev = variance ** 0.5
            cv = std_dev / mean_val if mean_val > 0 else 0  # Coefficient of variation

            # Confidence score based on sample size
            # Uses logarithmic scaling: more samples = higher confidence
            confidence = min(1.0, 0.3 + (0.7 * (n / (n + 10))))

            stats[cause] = {
                'n': n,
                'mean': mean_val,
                'median': median,
                'min': min_val,
                'max': max_val,
                'p25': p25,
                'p75': p75,
                'std_dev': std_dev,
                'cv': cv,
                'confidence': confidence,
            }

        self._stats_cache = stats
        return stats

    def get_available_causes(self) -> List[Tuple[str, int, float]]:
        """Get list of available causes with sample sizes and median values."""
        stats = self._calculate_statistics()
        return sorted(
            [(cause, s['n'], s['median']) for cause, s in stats.items()],
            key=lambda x: x[2],
            reverse=True
        )

    def valuate(
        self,
        cause_of_action: str,
        jurisdiction: Optional[str] = None,
        defendant_type: Optional[str] = None,
        class_size_category: Optional[str] = None,
        custom_multiplier: float = 1.0,
        use_ml_multipliers: bool = False,
        court: Optional[str] = None,
        defendant: Optional[str] = None,
    ) -> Optional[ValuationResult]:
        """
        Estimate case value based on cause of action and factors.

        Args:
            cause_of_action: Type of legal claim (e.g., "Product Liability")
            jurisdiction: State or circuit (e.g., "california", "9th circuit")
            defendant_type: Size/type of defendant (e.g., "fortune_500")
            class_size_category: Size of class (e.g., "large", "medium")
            custom_multiplier: Additional multiplier for special circumstances
            use_ml_multipliers: Use data-driven multipliers from ML module
            court: Court code for ML multipliers (e.g., 'cacd')
            defendant: Defendant name for ML multipliers

        Returns:
            ValuationResult with estimates and confidence metrics,
            or None if cause not found.
        """
        stats = self._calculate_statistics()

        # Normalize and find matching cause
        normalized_cause = self._normalize_cause(cause_of_action)

        # Try exact match first, then fuzzy match
        matching_cause = None
        for db_cause in stats.keys():
            if db_cause.lower() == normalized_cause.lower():
                matching_cause = db_cause
                break

        if not matching_cause:
            # Try partial match
            for db_cause in stats.keys():
                if normalized_cause.lower() in db_cause.lower() or \
                   db_cause.lower() in normalized_cause.lower():
                    matching_cause = db_cause
                    break

        if not matching_cause:
            return None

        s = stats[matching_cause]

        # Calculate multipliers
        total_multiplier = custom_multiplier
        multipliers_applied = {'custom': custom_multiplier}

        # Try ML-based multipliers if requested
        if use_ml_multipliers and (court or defendant):
            try:
                from ml.features.historical import HistoricalFeatureExtractor
                hist = HistoricalFeatureExtractor()

                if court:
                    ml_jur_mult = hist.get_court_multiplier(court)
                    if ml_jur_mult != 1.0:
                        total_multiplier *= ml_jur_mult
                        multipliers_applied['jurisdiction_ml'] = ml_jur_mult

                if defendant:
                    def_history = hist.get_defendant_history(defendant)
                    if def_history and def_history.get('avg_settlement', 0) > 0:
                        # Scale multiplier based on defendant's historical payments
                        national_median = hist._compute_national_median()
                        if national_median > 0:
                            def_mult = def_history['avg_settlement'] / national_median
                            def_mult = max(0.5, min(2.0, def_mult))  # Clip to reasonable range
                            total_multiplier *= def_mult
                            multipliers_applied['defendant_ml'] = def_mult
            except ImportError:
                pass  # ML module not available, fall through to static multipliers

        if jurisdiction and 'jurisdiction_ml' not in multipliers_applied:
            jur_mult = self.JURISDICTION_MULTIPLIERS.get(
                jurisdiction.lower(), 1.0
            )
            total_multiplier *= jur_mult
            multipliers_applied['jurisdiction'] = jur_mult

        if defendant_type and 'defendant_ml' not in multipliers_applied:
            def_mult = self.DEFENDANT_MULTIPLIERS.get(
                defendant_type.lower(), 1.0
            )
            total_multiplier *= def_mult
            multipliers_applied['defendant'] = def_mult

        if class_size_category:
            class_mult = self.CLASS_SIZE_MULTIPLIERS.get(
                class_size_category.lower(), 1.0
            )
            total_multiplier *= class_mult
            multipliers_applied['class_size'] = class_mult

        # Calculate adjusted estimates
        low_estimate = s['p25'] * total_multiplier
        mid_estimate = s['median'] * total_multiplier
        high_estimate = s['p75'] * total_multiplier

        return ValuationResult(
            cause_of_action=matching_cause,
            sample_size=s['n'],
            median=s['median'],
            mean=s['mean'],
            min_value=s['min'],
            max_value=s['max'],
            p25=s['p25'],
            p75=s['p75'],
            low_estimate=low_estimate,
            mid_estimate=mid_estimate,
            high_estimate=high_estimate,
            confidence_score=s['confidence'],
            volatility=s['cv'],
            multipliers=multipliers_applied,
        )

    def compare_causes(self, causes: List[str]) -> List[ValuationResult]:
        """Compare valuations across multiple causes of action."""
        results = []
        for cause in causes:
            result = self.valuate(cause)
            if result:
                results.append(result)
        return sorted(results, key=lambda x: x.median, reverse=True)

    def generate_benchmark_report(self, min_sample_size: int = 5) -> str:
        """Generate a formatted benchmark report of all causes."""
        stats = self._calculate_statistics()

        lines = [
            "=" * 80,
            "CASE VALUATION BENCHMARKS - Settlement Watch",
            "=" * 80,
            "",
            f"{'Cause of Action':<30} {'N':>5} {'Median':>12} {'P25':>12} {'P75':>12} {'Conf':>6}",
            "-" * 80,
        ]

        # Sort by median value descending
        sorted_causes = sorted(
            [(c, s) for c, s in stats.items() if s['n'] >= min_sample_size],
            key=lambda x: x[1]['median'],
            reverse=True
        )

        for cause, s in sorted_causes:
            lines.append(
                f"{cause[:30]:<30} {s['n']:>5} "
                f"${s['median']/1e6:>10.1f}M "
                f"${s['p25']/1e6:>10.1f}M "
                f"${s['p75']/1e6:>10.1f}M "
                f"{s['confidence']:>5.0%}"
            )

        lines.extend([
            "-" * 80,
            "",
            "Confidence Score: Based on sample size (higher = more reliable)",
            "P25/P75: 25th and 75th percentile values for range estimation",
            "",
        ])

        return "\n".join(lines)

    def export_benchmarks_json(self, output_path: str = "analytics/benchmarks.json"):
        """Export benchmark data to JSON for frontend use."""
        stats = self._calculate_statistics()

        benchmarks = []
        for cause, s in stats.items():
            benchmarks.append({
                'cause': cause,
                'sample_size': s['n'],
                'median': s['median'],
                'mean': s['mean'],
                'min': s['min'],
                'max': s['max'],
                'p25': s['p25'],
                'p75': s['p75'],
                'confidence': s['confidence'],
                'volatility': s['cv'],
            })

        # Sort by median
        benchmarks.sort(key=lambda x: x['median'], reverse=True)

        output = {
            'generated_at': str(Path(self.db_path).stat().st_mtime),
            'total_causes': len(benchmarks),
            'benchmarks': benchmarks,
            'multipliers': {
                'jurisdiction': self.JURISDICTION_MULTIPLIERS,
                'defendant_type': self.DEFENDANT_MULTIPLIERS,
                'class_size': self.CLASS_SIZE_MULTIPLIERS,
            }
        }

        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)

        return output_path


def main():
    """CLI for case valuation."""
    import sys

    valuator = CaseValuator()

    if len(sys.argv) > 1:
        cause = " ".join(sys.argv[1:])
        result = valuator.valuate(cause)
        if result:
            print(result.summary())
        else:
            print(f"No data found for: {cause}")
            print("\nAvailable causes:")
            for cause, n, median in valuator.get_available_causes()[:20]:
                print(f"  - {cause} ({n} cases, median ${median/1e6:.1f}M)")
    else:
        print(valuator.generate_benchmark_report())


if __name__ == "__main__":
    main()
