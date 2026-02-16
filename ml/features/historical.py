"""
Historical feature extraction from database.

Computes data-driven multipliers and statistics from historical case data
to replace hand-coded multipliers with empirically derived values.
"""
from typing import Dict, List, Optional, Tuple
import sqlite3
import numpy as np
from pathlib import Path
from functools import lru_cache

from .base import BaseFeatureExtractor, FeatureSet
from ..config import MLConfig, default_config, DB_PATH


class HistoricalFeatureExtractor(BaseFeatureExtractor):
    """
    Extract historical/statistical features from database.

    Features extracted:
    - jurisdiction_multiplier: Court median / national median
    - court_median_settlement: Median settlement for this court
    - court_case_count: Number of cases in this court
    - court_dismissal_rate: Court-level dismissal rate
    - defendant_payment_history: Defendant's historical payment amount
    - defendant_case_count: Number of cases involving defendant
    - nos_median_settlement: Median for this nature of suit
    - nos_case_count: Number of cases with this NOS
    - judge_mtd_rate: Judge's MTD grant rate (if available)
    - judge_case_count: Cases handled by judge
    """

    def __init__(
        self,
        config: Optional[MLConfig] = None,
        db_path: Optional[Path] = None
    ):
        """
        Initialize the historical feature extractor.

        Args:
            config: ML configuration
            db_path: Path to database (uses config default if not provided)
        """
        super().__init__()
        self.config = config or default_config
        self.db_path = db_path or self.config.db_path

        # Cache for computed statistics
        self._court_stats: Optional[Dict] = None
        self._nos_stats: Optional[Dict] = None
        self._defendant_stats: Optional[Dict] = None
        self._judge_stats: Optional[Dict] = None
        self._national_median: Optional[float] = None

        self._fitted = True

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @lru_cache(maxsize=1)
    def _compute_national_median(self) -> float:
        """Compute national median settlement amount."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT settlement_amount
            FROM case_outcomes
            WHERE settlement_amount > 0
            ORDER BY settlement_amount
        """)

        amounts = [row[0] for row in cursor.fetchall()]
        conn.close()

        if not amounts:
            return 1_000_000.0  # Default fallback

        return float(np.median(amounts))

    def _compute_court_stats(self) -> Dict[str, Dict]:
        """Compute statistics by court."""
        if self._court_stats is not None:
            return self._court_stats

        conn = self._get_connection()
        cursor = conn.cursor()

        # Get settlement stats by court
        cursor.execute("""
            SELECT
                court,
                COUNT(*) as case_count,
                AVG(settlement_amount) as avg_settlement,
                GROUP_CONCAT(settlement_amount) as amounts
            FROM case_outcomes
            WHERE settlement_amount > 0 AND court IS NOT NULL
            GROUP BY court
        """)

        stats = {}
        national_median = self._compute_national_median()

        for row in cursor.fetchall():
            court = row['court'].lower() if row['court'] else 'unknown'
            amounts = [float(a) for a in (row['amounts'] or '').split(',') if a]
            median = float(np.median(amounts)) if amounts else national_median

            stats[court] = {
                'case_count': row['case_count'],
                'avg_settlement': row['avg_settlement'] or 0,
                'median_settlement': median,
                'multiplier': median / national_median if national_median > 0 else 1.0,
            }

        conn.close()
        self._court_stats = stats
        return stats

    def _compute_nos_stats(self) -> Dict[str, Dict]:
        """Compute statistics by nature of suit."""
        if self._nos_stats is not None:
            return self._nos_stats

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                nature_of_suit,
                COUNT(*) as case_count,
                AVG(settlement_amount) as avg_settlement,
                GROUP_CONCAT(settlement_amount) as amounts
            FROM case_outcomes
            WHERE settlement_amount > 0 AND nature_of_suit IS NOT NULL
            GROUP BY nature_of_suit
        """)

        stats = {}
        national_median = self._compute_national_median()

        for row in cursor.fetchall():
            nos = row['nature_of_suit'].lower() if row['nature_of_suit'] else 'unknown'
            amounts = [float(a) for a in (row['amounts'] or '').split(',') if a]
            median = float(np.median(amounts)) if amounts else national_median

            stats[nos] = {
                'case_count': row['case_count'],
                'avg_settlement': row['avg_settlement'] or 0,
                'median_settlement': median,
            }

        conn.close()
        self._nos_stats = stats
        return stats

    def _compute_defendant_stats(self) -> Dict[str, Dict]:
        """Compute statistics by defendant."""
        if self._defendant_stats is not None:
            return self._defendant_stats

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                defendant,
                COUNT(*) as case_count,
                SUM(settlement_amount) as total_paid,
                AVG(settlement_amount) as avg_settlement
            FROM case_outcomes
            WHERE settlement_amount > 0 AND defendant IS NOT NULL
            GROUP BY defendant
        """)

        stats = {}
        for row in cursor.fetchall():
            defendant = row['defendant'].lower() if row['defendant'] else 'unknown'
            stats[defendant] = {
                'case_count': row['case_count'],
                'total_paid': row['total_paid'] or 0,
                'avg_settlement': row['avg_settlement'] or 0,
            }

        conn.close()
        self._defendant_stats = stats
        return stats

    def _compute_judge_stats(self) -> Dict[str, Dict]:
        """Compute statistics by judge (if judge data exists)."""
        if self._judge_stats is not None:
            return self._judge_stats

        conn = self._get_connection()
        cursor = conn.cursor()

        # Check if judge_profiles table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='judge_profiles'
        """)

        if not cursor.fetchone():
            conn.close()
            self._judge_stats = {}
            return {}

        # Get judge stats
        cursor.execute("""
            SELECT
                name,
                total_cases,
                mtd_granted,
                mtd_denied,
                sj_granted,
                sj_denied,
                dismissed_prejudice,
                settlement
            FROM judge_profiles
        """)

        stats = {}
        for row in cursor.fetchall():
            name = row['name'].lower() if row['name'] else 'unknown'
            total_mtd = (row['mtd_granted'] or 0) + (row['mtd_denied'] or 0)
            total_sj = (row['sj_granted'] or 0) + (row['sj_denied'] or 0)

            stats[name] = {
                'total_cases': row['total_cases'] or 0,
                'mtd_grant_rate': (row['mtd_granted'] or 0) / total_mtd if total_mtd > 0 else 0.5,
                'sj_grant_rate': (row['sj_granted'] or 0) / total_sj if total_sj > 0 else 0.5,
                'dismissal_rate': (row['dismissed_prejudice'] or 0) / row['total_cases'] if row['total_cases'] else 0,
                'settlement_rate': (row['settlement'] or 0) / row['total_cases'] if row['total_cases'] else 0,
            }

        conn.close()
        self._judge_stats = stats
        return stats

    def _find_defendant_match(self, defendant: str) -> Optional[Dict]:
        """
        Find best matching defendant in historical data.

        Args:
            defendant: Defendant name to search

        Returns:
            Stats dict if found, None otherwise
        """
        if not defendant:
            return None

        defendant_stats = self._compute_defendant_stats()
        defendant_lower = defendant.lower()

        # Exact match
        if defendant_lower in defendant_stats:
            return defendant_stats[defendant_lower]

        # Partial match - find defendants that contain search term or vice versa
        for db_defendant, stats in defendant_stats.items():
            if defendant_lower in db_defendant or db_defendant in defendant_lower:
                return stats

        return None

    def _find_judge_match(self, judge: str) -> Optional[Dict]:
        """
        Find best matching judge in historical data.

        Args:
            judge: Judge name to search

        Returns:
            Stats dict if found, None otherwise
        """
        if not judge:
            return None

        judge_stats = self._compute_judge_stats()
        judge_lower = judge.lower()

        # Exact match
        if judge_lower in judge_stats:
            return judge_stats[judge_lower]

        # Partial match on last name
        for db_judge, stats in judge_stats.items():
            # Check if last word (last name) matches
            search_parts = judge_lower.split()
            db_parts = db_judge.split()
            if search_parts and db_parts:
                if search_parts[-1] == db_parts[-1]:
                    return stats

        return None

    def extract(
        self,
        court: Optional[str] = None,
        nos: Optional[str] = None,
        defendant: Optional[str] = None,
        judge: Optional[str] = None,
        **kwargs
    ) -> FeatureSet:
        """
        Extract historical features from database.

        Args:
            court: Court code or name
            nos: Nature of suit
            defendant: Defendant name
            judge: Judge name (if available)
            **kwargs: Additional arguments (ignored)

        Returns:
            FeatureSet with historical features
        """
        # Get computed statistics
        court_stats = self._compute_court_stats()
        nos_stats = self._compute_nos_stats()
        national_median = self._compute_national_median()

        # Court features
        court_normalized = (court or '').lower()
        court_data = court_stats.get(court_normalized, {})
        jurisdiction_multiplier = court_data.get('multiplier', 1.0)
        court_median = court_data.get('median_settlement', national_median)
        court_case_count = court_data.get('case_count', 0)

        # Compute court dismissal rate (from case outcomes if available)
        # For now, use a default - could be computed from docket entries
        court_dismissal_rate = 0.3  # Default 30%

        # NOS features
        nos_normalized = (nos or '').lower()
        nos_data = nos_stats.get(nos_normalized, {})
        nos_median = nos_data.get('median_settlement', national_median)
        nos_case_count = nos_data.get('case_count', 0)

        # Defendant features
        defendant_data = self._find_defendant_match(defendant)
        defendant_total_paid = defendant_data.get('total_paid', 0) if defendant_data else 0
        defendant_avg = defendant_data.get('avg_settlement', 0) if defendant_data else 0
        defendant_case_count = defendant_data.get('case_count', 0) if defendant_data else 0

        # Judge features
        judge_data = self._find_judge_match(judge)
        judge_mtd_rate = judge_data.get('mtd_grant_rate', 0.5) if judge_data else 0.5
        judge_case_count = judge_data.get('total_cases', 0) if judge_data else 0

        # Build feature array
        features = np.array([
            jurisdiction_multiplier,
            np.log10(court_median + 1),  # Log scale
            float(court_case_count),
            court_dismissal_rate,
            np.log10(defendant_total_paid + 1) if defendant_total_paid > 0 else 0,
            np.log10(defendant_avg + 1) if defendant_avg > 0 else 0,
            float(defendant_case_count),
            np.log10(nos_median + 1),
            float(nos_case_count),
            judge_mtd_rate,
            float(judge_case_count),
        ])

        return FeatureSet(
            values=features,
            names=self.get_feature_names(),
            metadata={
                'court': court,
                'court_stats': court_data,
                'nos': nos,
                'nos_stats': nos_data,
                'defendant': defendant,
                'defendant_found': defendant_data is not None,
                'judge': judge,
                'judge_found': judge_data is not None,
                'national_median': national_median,
            }
        )

    def get_feature_names(self) -> List[str]:
        """Get list of feature names."""
        return [
            'jurisdiction_multiplier',
            'court_median_settlement_log',
            'court_case_count',
            'court_dismissal_rate',
            'defendant_total_paid_log',
            'defendant_avg_settlement_log',
            'defendant_case_count',
            'nos_median_settlement_log',
            'nos_case_count',
            'judge_mtd_rate',
            'judge_case_count',
        ]

    def get_court_multiplier(self, court: str) -> float:
        """Get jurisdiction multiplier for a specific court."""
        stats = self._compute_court_stats()
        court_data = stats.get(court.lower(), {})
        return court_data.get('multiplier', 1.0)

    def get_defendant_history(self, defendant: str) -> Optional[Dict]:
        """Get historical payment data for a defendant."""
        return self._find_defendant_match(defendant)

    def get_judge_rates(self, judge: str) -> Optional[Dict]:
        """Get judge's historical motion grant rates."""
        return self._find_judge_match(judge)

    def refresh_cache(self):
        """Clear cached statistics to force recomputation."""
        self._court_stats = None
        self._nos_stats = None
        self._defendant_stats = None
        self._judge_stats = None
        self._compute_national_median.cache_clear()
