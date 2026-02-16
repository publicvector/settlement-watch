"""
Structured feature extraction from case metadata.

Extracts features from:
- Court code
- Nature of suit
- Defendant type
- Class action status
- Pro se status
"""
from typing import Dict, List, Optional, Any
import numpy as np
from sklearn.preprocessing import LabelEncoder
import joblib
from pathlib import Path

from .base import BaseFeatureExtractor, FeatureSet
from ..config import MLConfig, default_config


class StructuredFeatureExtractor(BaseFeatureExtractor):
    """
    Extract features from structured case metadata.

    Features extracted:
    - court_encoded: Label-encoded court identifier
    - nos_encoded: Label-encoded nature of suit
    - nos_category_encoded: Label-encoded NOS category
    - defendant_type_encoded: Label-encoded defendant type
    - is_class_action: Binary flag
    - is_pro_se: Binary flag
    - is_mdl: Binary flag
    """

    # Canonical defendant types
    DEFENDANT_TYPES = [
        'fortune_500', 'large_corp', 'small_company',
        'government', 'healthcare', 'financial', 'individual', 'unknown'
    ]

    def __init__(self, config: Optional[MLConfig] = None):
        """
        Initialize the structured feature extractor.

        Args:
            config: ML configuration (uses default if not provided)
        """
        super().__init__()
        self.config = config or default_config

        # Initialize encoders
        self.court_encoder = LabelEncoder()
        self.nos_encoder = LabelEncoder()
        self.nos_category_encoder = LabelEncoder()
        self.defendant_type_encoder = LabelEncoder()

        # Pre-fit with known values
        self._initialize_encoders()

    def _initialize_encoders(self):
        """Initialize encoders with known values plus 'unknown'."""
        # Court encoder
        courts = list(self.config.features.court_codes) + ['unknown']
        self.court_encoder.fit(courts)

        # NOS encoder - use code strings
        nos_codes = list(self.config.features.nos_categories.keys()) + ['unknown']
        self.nos_encoder.fit(nos_codes)

        # NOS category encoder
        categories = list(set(self.config.features.nos_categories.values())) + ['unknown']
        self.nos_category_encoder.fit(categories)

        # Defendant type encoder
        self.defendant_type_encoder.fit(self.DEFENDANT_TYPES)

        self._fitted = True

    def _classify_defendant_type(self, defendant: str) -> str:
        """
        Classify defendant into a type based on name patterns.

        Args:
            defendant: Defendant name string

        Returns:
            Defendant type classification
        """
        if not defendant:
            return 'unknown'

        defendant_lower = defendant.lower()

        # Check each type's keywords
        keywords = self.config.features.defendant_type_keywords

        # Check Fortune 500 first (most specific)
        for keyword in keywords.get('fortune_500', []):
            if keyword.lower() in defendant_lower:
                return 'fortune_500'

        # Check government
        for keyword in keywords.get('government', []):
            if keyword.lower() in defendant_lower:
                return 'government'

        # Check healthcare
        for keyword in keywords.get('healthcare', []):
            if keyword.lower() in defendant_lower:
                return 'healthcare'

        # Check financial
        for keyword in keywords.get('financial', []):
            if keyword.lower() in defendant_lower:
                return 'financial'

        # Check large corp indicators
        for keyword in keywords.get('large_corp', []):
            if keyword.lower() in defendant_lower:
                return 'large_corp'

        # Default
        return 'small_company'

    def _normalize_court(self, court: str) -> str:
        """Normalize court code to lowercase standard form."""
        if not court:
            return 'unknown'

        court_lower = court.lower().strip()

        # Common court code normalization
        court_mappings = {
            'c.d. cal.': 'cacd',
            'c.d.cal.': 'cacd',
            'central district of california': 'cacd',
            'n.d. cal.': 'cand',
            'n.d.cal.': 'cand',
            'northern district of california': 'cand',
            's.d. cal.': 'casd',
            's.d.cal.': 'casd',
            'southern district of california': 'casd',
            'e.d. cal.': 'caed',
            'e.d.cal.': 'caed',
            'eastern district of california': 'caed',
            's.d.n.y.': 'nysd',
            's.d. n.y.': 'nysd',
            'southern district of new york': 'nysd',
            'e.d.n.y.': 'nyed',
            'e.d. n.y.': 'nyed',
            'eastern district of new york': 'nyed',
            'n.d.n.y.': 'nynd',
            'n.d. n.y.': 'nynd',
            'northern district of new york': 'nynd',
            's.d. tex.': 'txsd',
            'n.d. tex.': 'txnd',
            'e.d. tex.': 'txed',
            'w.d. tex.': 'txwd',
            'n.d. ill.': 'ilnd',
            's.d. fla.': 'flsd',
            'e.d. pa.': 'paed',
            'd.n.j.': 'njd',
            'd.d.c.': 'dcd',
            'd. mass.': 'mad',
        }

        # Check mappings
        if court_lower in court_mappings:
            return court_mappings[court_lower]

        # If already in standard form, use it
        if court_lower in self.config.features.court_codes:
            return court_lower

        # Extract just letters and try to match
        court_clean = ''.join(c for c in court_lower if c.isalpha())
        if court_clean in self.config.features.court_codes:
            return court_clean

        return 'unknown'

    def _normalize_nos(self, nos: str) -> tuple:
        """
        Normalize nature of suit to code and category.

        Args:
            nos: Nature of suit string (code or description)

        Returns:
            Tuple of (nos_code, nos_category)
        """
        if not nos:
            return 'unknown', 'unknown'

        nos_str = str(nos).strip()

        # If it's a 3-digit code, use directly
        if nos_str.isdigit() and len(nos_str) == 3:
            category = self.config.features.nos_categories.get(nos_str, 'unknown')
            return nos_str, category

        # Try to extract code from beginning
        nos_lower = nos_str.lower()
        code_match = None

        # Check if description contains a known category
        for code, category in self.config.features.nos_categories.items():
            if category.lower() in nos_lower or nos_lower in category.lower():
                code_match = code
                break

        # Common keyword mappings
        keyword_to_code = {
            'securities': '850',
            'antitrust': '410',
            'employment': '442',
            'civil rights': '440',
            'product liability': '365',
            'personal injury': '360',
            'fraud': '370',
            'contract': '190',
            'patent': '830',
            'trademark': '840',
            'copyright': '820',
            'environmental': '893',
            'labor': '710',
            'bankruptcy': '920',
            'data breach': '370',
            'privacy': '370',
            'consumer': '480',
            'insurance': '110',
        }

        for keyword, code in keyword_to_code.items():
            if keyword in nos_lower:
                code_match = code
                break

        if code_match:
            category = self.config.features.nos_categories.get(code_match, 'unknown')
            return code_match, category

        return 'unknown', 'unknown'

    def extract(
        self,
        court: Optional[str] = None,
        nos: Optional[str] = None,
        defendant: Optional[str] = None,
        class_action: bool = False,
        pro_se: bool = False,
        mdl: bool = False,
        **kwargs
    ) -> FeatureSet:
        """
        Extract structured features from case metadata.

        Args:
            court: Court code or name
            nos: Nature of suit code or description
            defendant: Defendant name
            class_action: Whether case is a class action
            pro_se: Whether plaintiff is pro se
            mdl: Whether case is part of MDL
            **kwargs: Additional arguments (ignored)

        Returns:
            FeatureSet with extracted features
        """
        # Normalize inputs
        court_normalized = self._normalize_court(court)
        nos_code, nos_category = self._normalize_nos(nos)
        defendant_type = self._classify_defendant_type(defendant)

        # Encode features
        try:
            court_encoded = self.court_encoder.transform([court_normalized])[0]
        except ValueError:
            court_encoded = self.court_encoder.transform(['unknown'])[0]

        try:
            nos_encoded = self.nos_encoder.transform([nos_code])[0]
        except ValueError:
            nos_encoded = self.nos_encoder.transform(['unknown'])[0]

        try:
            nos_cat_encoded = self.nos_category_encoder.transform([nos_category])[0]
        except ValueError:
            nos_cat_encoded = self.nos_category_encoder.transform(['unknown'])[0]

        try:
            defendant_encoded = self.defendant_type_encoder.transform([defendant_type])[0]
        except ValueError:
            defendant_encoded = self.defendant_type_encoder.transform(['unknown'])[0]

        # Build feature array
        features = np.array([
            float(court_encoded),
            float(nos_encoded),
            float(nos_cat_encoded),
            float(defendant_encoded),
            1.0 if class_action else 0.0,
            1.0 if pro_se else 0.0,
            1.0 if mdl else 0.0,
        ])

        return FeatureSet(
            values=features,
            names=self.get_feature_names(),
            metadata={
                'court_raw': court,
                'court_normalized': court_normalized,
                'nos_raw': nos,
                'nos_code': nos_code,
                'nos_category': nos_category,
                'defendant_raw': defendant,
                'defendant_type': defendant_type,
            }
        )

    def get_feature_names(self) -> List[str]:
        """Get list of feature names."""
        return [
            'court_encoded',
            'nos_encoded',
            'nos_category_encoded',
            'defendant_type_encoded',
            'is_class_action',
            'is_pro_se',
            'is_mdl',
        ]

    def save(self, path: Path):
        """Save encoders to file."""
        encoders = {
            'court': self.court_encoder,
            'nos': self.nos_encoder,
            'nos_category': self.nos_category_encoder,
            'defendant_type': self.defendant_type_encoder,
        }
        joblib.dump(encoders, path)

    def load(self, path: Path):
        """Load encoders from file."""
        encoders = joblib.load(path)
        self.court_encoder = encoders['court']
        self.nos_encoder = encoders['nos']
        self.nos_category_encoder = encoders['nos_category']
        self.defendant_type_encoder = encoders['defendant_type']
        self._fitted = True
