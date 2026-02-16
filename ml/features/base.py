"""
Base classes for feature extraction.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
import numpy as np


@dataclass
class FeatureSet:
    """Container for extracted features with metadata."""
    # Feature values as numpy array
    values: np.ndarray

    # Feature names in order
    names: List[str]

    # Optional metadata about features
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure values and names are consistent."""
        if len(self.names) != len(self.values):
            raise ValueError(
                f"Feature names ({len(self.names)}) must match "
                f"values length ({len(self.values)})"
            )

    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary of name: value pairs."""
        return dict(zip(self.names, self.values))

    def get(self, name: str, default: float = 0.0) -> float:
        """Get a specific feature value by name."""
        try:
            idx = self.names.index(name)
            return self.values[idx]
        except ValueError:
            return default

    def __len__(self) -> int:
        return len(self.names)

    def __repr__(self) -> str:
        return f"FeatureSet({len(self.names)} features)"


class BaseFeatureExtractor(ABC):
    """
    Abstract base class for feature extractors.

    All feature extractors should inherit from this class and implement
    the extract() method.
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize the feature extractor.

        Args:
            config: Optional configuration dictionary
        """
        self.config = config or {}
        self._fitted = False

    @abstractmethod
    def extract(self, **kwargs) -> FeatureSet:
        """
        Extract features from input data.

        Args:
            **kwargs: Input data for feature extraction

        Returns:
            FeatureSet with extracted features
        """
        pass

    @abstractmethod
    def get_feature_names(self) -> List[str]:
        """
        Get list of feature names this extractor produces.

        Returns:
            List of feature name strings
        """
        pass

    def fit(self, data: List[Dict]) -> 'BaseFeatureExtractor':
        """
        Fit the extractor to training data (e.g., for encoders).

        Default implementation does nothing - override in subclasses
        that require fitting.

        Args:
            data: List of data records to fit on

        Returns:
            self for method chaining
        """
        self._fitted = True
        return self

    def is_fitted(self) -> bool:
        """Check if the extractor has been fitted."""
        return self._fitted


def combine_feature_sets(*feature_sets: FeatureSet) -> FeatureSet:
    """
    Combine multiple FeatureSets into one.

    Args:
        *feature_sets: FeatureSet objects to combine

    Returns:
        Combined FeatureSet
    """
    if not feature_sets:
        return FeatureSet(values=np.array([]), names=[])

    all_names = []
    all_values = []
    combined_metadata = {}

    for fs in feature_sets:
        all_names.extend(fs.names)
        all_values.extend(fs.values)
        combined_metadata.update(fs.metadata)

    return FeatureSet(
        values=np.array(all_values),
        names=all_names,
        metadata=combined_metadata
    )
