"""
Text feature extraction from complaint documents.

Wraps the existing ComplaintAnalyzer to extract ML-ready features
from complaint text.
"""
from typing import Dict, List, Optional, Any
import numpy as np
import sys
from pathlib import Path

from .base import BaseFeatureExtractor, FeatureSet

# Add parent directory to path for analytics import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from analytics.complaint_analyzer import ComplaintAnalyzer, ComplaintFeatures


class TextFeatureExtractor(BaseFeatureExtractor):
    """
    Extract features from complaint text using ComplaintAnalyzer.

    Features extracted:
    - complexity_score: 0-100 complexity rating
    - strength_score: 0-100 evidence strength rating
    - cause_count: Number of causes of action
    - statute_count: Number of statutes cited
    - claim_count: Number of claims/counts
    - prior_cases_cited: Number of prior cases referenced
    - estimated_class_size_log: Log of class size (if class action)
    - damages_type_* : One-hot encoding of damages type
    - has_documentary_evidence: Binary
    - has_expert_witnesses: Binary
    - has_regulatory_findings: Binary
    - is_securities: Binary
    - is_qui_tam: Binary
    - value_multiplier: Calculated value multiplier
    """

    # Canonical damages types for one-hot encoding
    DAMAGES_TYPES = ['actual', 'statutory', 'punitive', 'treble', 'unknown']

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize the text feature extractor.

        Args:
            config: Optional configuration dictionary
        """
        super().__init__(config)
        self.analyzer = ComplaintAnalyzer()
        self._fitted = True  # No fitting required

    def _extract_from_features(self, features: ComplaintFeatures) -> FeatureSet:
        """
        Convert ComplaintFeatures to FeatureSet.

        Args:
            features: ComplaintFeatures from analyzer

        Returns:
            FeatureSet with numeric features
        """
        # Base numeric features
        values = [
            features.complexity_score,
            features.strength_score,
            float(len(features.causes_of_action)),
            float(len(features.statutes_cited)),
            float(features.claim_count),
            float(features.prior_cases_cited),
        ]

        # Log-transformed class size (add 1 to avoid log(0))
        class_size = max(1, features.estimated_class_size)
        values.append(np.log10(class_size))

        # One-hot encode damages type
        damages_type = features.damages_type.lower() if features.damages_type else 'unknown'
        for dt in self.DAMAGES_TYPES:
            values.append(1.0 if damages_type == dt else 0.0)

        # Binary features
        values.extend([
            1.0 if features.documentary_evidence_mentioned else 0.0,
            1.0 if features.expert_witnesses_mentioned else 0.0,
            1.0 if features.regulatory_findings_cited else 0.0,
            1.0 if features.is_securities else 0.0,
            1.0 if features.is_qui_tam else 0.0,
        ])

        # Value multiplier
        values.append(features.value_multiplier)

        return FeatureSet(
            values=np.array(values),
            names=self.get_feature_names(),
            metadata={
                'title': features.title,
                'causes_of_action': features.causes_of_action,
                'statutes_cited': features.statutes_cited,
                'plaintiffs': features.plaintiffs,
                'defendants': features.defendants,
                'damages_claimed': features.damages_claimed,
            }
        )

    def extract(
        self,
        complaint_text: Optional[str] = None,
        complaint_features: Optional[ComplaintFeatures] = None,
        title: str = "",
        **kwargs
    ) -> FeatureSet:
        """
        Extract features from complaint text or pre-analyzed features.

        Args:
            complaint_text: Raw complaint text (will be analyzed)
            complaint_features: Pre-extracted ComplaintFeatures
            title: Case title (used if analyzing text)
            **kwargs: Additional arguments (ignored)

        Returns:
            FeatureSet with extracted features
        """
        # Use pre-extracted features if provided
        if complaint_features is not None:
            return self._extract_from_features(complaint_features)

        # Analyze complaint text
        if complaint_text:
            features = self.analyzer.analyze(complaint_text, title=title)
            return self._extract_from_features(features)

        # Return default features if no input
        return self._get_default_features()

    def _get_default_features(self) -> FeatureSet:
        """Return default feature set when no text is available."""
        # Default values representing "unknown" state
        values = [
            50.0,   # complexity_score (neutral)
            50.0,   # strength_score (neutral)
            1.0,    # cause_count (minimum)
            0.0,    # statute_count
            1.0,    # claim_count
            0.0,    # prior_cases_cited
            0.0,    # estimated_class_size_log (log10(1) = 0)
        ]

        # Unknown damages type
        for dt in self.DAMAGES_TYPES:
            values.append(1.0 if dt == 'unknown' else 0.0)

        # Binary features (all unknown/false)
        values.extend([0.0, 0.0, 0.0, 0.0, 0.0])

        # Default multiplier
        values.append(1.0)

        return FeatureSet(
            values=np.array(values),
            names=self.get_feature_names(),
            metadata={'is_default': True}
        )

    def get_feature_names(self) -> List[str]:
        """Get list of feature names."""
        names = [
            'complexity_score',
            'strength_score',
            'cause_count',
            'statute_count',
            'claim_count',
            'prior_cases_cited',
            'estimated_class_size_log',
        ]

        # Damages type one-hot
        for dt in self.DAMAGES_TYPES:
            names.append(f'damages_type_{dt}')

        # Binary features
        names.extend([
            'has_documentary_evidence',
            'has_expert_witnesses',
            'has_regulatory_findings',
            'is_securities',
            'is_qui_tam',
        ])

        # Multiplier
        names.append('value_multiplier')

        return names

    def analyze_file(self, filepath: str) -> FeatureSet:
        """
        Analyze a complaint file and extract features.

        Args:
            filepath: Path to complaint text or PDF file

        Returns:
            FeatureSet with extracted features
        """
        path = Path(filepath)

        if not path.exists():
            raise FileNotFoundError(f"Complaint file not found: {filepath}")

        # Read file content
        # TODO: Add PDF extraction support
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()

        return self.extract(complaint_text=text, title=path.name)
