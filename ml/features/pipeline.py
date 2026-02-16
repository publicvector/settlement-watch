"""
Combined feature pipeline for ML predictions.

Orchestrates all feature extractors and produces unified feature vectors.
"""
from typing import Dict, List, Optional, Any
import numpy as np
import joblib
from pathlib import Path

from .base import BaseFeatureExtractor, FeatureSet, combine_feature_sets
from .structured import StructuredFeatureExtractor
from .text import TextFeatureExtractor
from .historical import HistoricalFeatureExtractor
from ..config import MLConfig, default_config


class FeaturePipeline(BaseFeatureExtractor):
    """
    Combined feature extraction pipeline.

    Orchestrates:
    - StructuredFeatureExtractor: Court, NOS, defendant encoding
    - TextFeatureExtractor: Complaint document analysis
    - HistoricalFeatureExtractor: Data-driven multipliers

    The pipeline can be configured to use subsets of extractors
    based on available data.
    """

    def __init__(
        self,
        config: Optional[MLConfig] = None,
        include_text: bool = True,
        include_historical: bool = True,
    ):
        """
        Initialize the feature pipeline.

        Args:
            config: ML configuration
            include_text: Whether to include text features
            include_historical: Whether to include historical features
        """
        super().__init__()
        self.config = config or default_config

        # Initialize extractors
        self.structured = StructuredFeatureExtractor(config)
        self.include_text = include_text
        self.include_historical = include_historical

        if include_text:
            self.text = TextFeatureExtractor()
        else:
            self.text = None

        if include_historical:
            self.historical = HistoricalFeatureExtractor(config)
        else:
            self.historical = None

        self._fitted = True

    def extract(
        self,
        court: Optional[str] = None,
        nos: Optional[str] = None,
        defendant: Optional[str] = None,
        class_action: bool = False,
        pro_se: bool = False,
        mdl: bool = False,
        complaint_text: Optional[str] = None,
        complaint_features: Optional[Any] = None,
        judge: Optional[str] = None,
        **kwargs
    ) -> FeatureSet:
        """
        Extract all features from available inputs.

        Args:
            court: Court code or name
            nos: Nature of suit
            defendant: Defendant name
            class_action: Class action flag
            pro_se: Pro se flag
            mdl: MDL flag
            complaint_text: Raw complaint text
            complaint_features: Pre-analyzed complaint features
            judge: Judge name
            **kwargs: Additional arguments

        Returns:
            Combined FeatureSet from all extractors
        """
        feature_sets = []

        # Always extract structured features
        structured_features = self.structured.extract(
            court=court,
            nos=nos,
            defendant=defendant,
            class_action=class_action,
            pro_se=pro_se,
            mdl=mdl,
        )
        feature_sets.append(structured_features)

        # Extract text features if available
        if self.text is not None:
            if complaint_text or complaint_features:
                text_features = self.text.extract(
                    complaint_text=complaint_text,
                    complaint_features=complaint_features,
                )
            else:
                text_features = self.text._get_default_features()
            feature_sets.append(text_features)

        # Extract historical features if available
        if self.historical is not None:
            historical_features = self.historical.extract(
                court=court,
                nos=nos,
                defendant=defendant,
                judge=judge,
            )
            feature_sets.append(historical_features)

        # Combine all feature sets
        combined = combine_feature_sets(*feature_sets)

        # Merge metadata
        combined.metadata['extractors_used'] = ['structured']
        if self.text is not None:
            combined.metadata['extractors_used'].append('text')
        if self.historical is not None:
            combined.metadata['extractors_used'].append('historical')

        return combined

    def extract_batch(self, records: List[Dict]) -> np.ndarray:
        """
        Extract features for multiple records.

        Args:
            records: List of dicts with feature extraction inputs

        Returns:
            2D numpy array of shape (n_records, n_features)
        """
        feature_matrix = []

        for record in records:
            features = self.extract(**record)
            feature_matrix.append(features.values)

        return np.array(feature_matrix)

    def get_feature_names(self) -> List[str]:
        """Get list of all feature names in order."""
        names = list(self.structured.get_feature_names())

        if self.text is not None:
            names.extend(self.text.get_feature_names())

        if self.historical is not None:
            names.extend(self.historical.get_feature_names())

        return names

    def get_feature_count(self) -> int:
        """Get total number of features."""
        return len(self.get_feature_names())

    def save(self, path: Path):
        """
        Save pipeline state to directory.

        Args:
            path: Directory path to save to
        """
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)

        # Save structured encoder
        self.structured.save(path / "structured_encoders.pkl")

        # Save pipeline config
        config = {
            'include_text': self.include_text,
            'include_historical': self.include_historical,
            'feature_names': self.get_feature_names(),
            'feature_count': self.get_feature_count(),
        }
        joblib.dump(config, path / "pipeline_config.pkl")

    def load(self, path: Path):
        """
        Load pipeline state from directory.

        Args:
            path: Directory path to load from
        """
        path = Path(path)

        # Load structured encoder
        if (path / "structured_encoders.pkl").exists():
            self.structured.load(path / "structured_encoders.pkl")

        # Load pipeline config
        if (path / "pipeline_config.pkl").exists():
            config = joblib.load(path / "pipeline_config.pkl")
            self.include_text = config.get('include_text', True)
            self.include_historical = config.get('include_historical', True)

            # Reinitialize extractors based on config
            if self.include_text and self.text is None:
                self.text = TextFeatureExtractor()
            elif not self.include_text:
                self.text = None

            if self.include_historical and self.historical is None:
                self.historical = HistoricalFeatureExtractor(self.config)
            elif not self.include_historical:
                self.historical = None

        self._fitted = True


def create_feature_matrix(
    records: List[Dict],
    config: Optional[MLConfig] = None,
    include_text: bool = True,
    include_historical: bool = True,
) -> tuple:
    """
    Convenience function to create feature matrix from records.

    Args:
        records: List of record dictionaries
        config: ML configuration
        include_text: Whether to include text features
        include_historical: Whether to include historical features

    Returns:
        Tuple of (feature_matrix, feature_names)
    """
    pipeline = FeaturePipeline(
        config=config,
        include_text=include_text,
        include_historical=include_historical,
    )

    X = pipeline.extract_batch(records)
    feature_names = pipeline.get_feature_names()

    return X, feature_names
