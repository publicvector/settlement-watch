"""
Base model interface for legal ML models.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Union
from datetime import datetime
import numpy as np
import joblib
from pathlib import Path
import json


@dataclass
class ModelMetadata:
    """Metadata for trained models."""
    model_name: str
    model_type: str
    version: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    training_samples: int = 0
    feature_names: List[str] = field(default_factory=list)
    feature_count: int = 0
    hyperparameters: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    def save(self, path: Path):
        """Save metadata to JSON."""
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> 'ModelMetadata':
        """Load metadata from JSON."""
        with open(path, 'r') as f:
            data = json.load(f)
        return cls(**data)


@dataclass
class PredictionResult:
    """Standard prediction result format."""
    # Primary prediction
    prediction: Union[float, str, List[float]]

    # Confidence/probability
    confidence: float = 0.0

    # For range predictions
    low: Optional[float] = None
    mid: Optional[float] = None
    high: Optional[float] = None

    # For classification
    probabilities: Optional[Dict[str, float]] = None

    # Explanation
    key_factors: List[str] = field(default_factory=list)

    # Metadata
    model_name: str = ""
    model_version: str = ""

    def to_dict(self) -> Dict:
        result = {
            'prediction': self.prediction,
            'confidence': self.confidence,
        }
        if self.low is not None:
            result['low'] = self.low
        if self.mid is not None:
            result['mid'] = self.mid
        if self.high is not None:
            result['high'] = self.high
        if self.probabilities:
            result['probabilities'] = self.probabilities
        if self.key_factors:
            result['key_factors'] = self.key_factors
        result['model_name'] = self.model_name
        result['model_version'] = self.model_version
        return result


class BaseModel(ABC):
    """
    Abstract base class for legal ML models.

    All models should inherit from this class and implement
    the required methods.
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize the model.

        Args:
            config: Model configuration dictionary
        """
        self.config = config or {}
        self.model = None
        self.metadata = None
        self._fitted = False

    @property
    @abstractmethod
    def name(self) -> str:
        """Model name identifier."""
        pass

    @abstractmethod
    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: Optional[List[str]] = None,
        **kwargs
    ) -> 'BaseModel':
        """
        Train the model.

        Args:
            X: Feature matrix
            y: Target values
            feature_names: Names of features
            **kwargs: Additional training arguments

        Returns:
            self for method chaining
        """
        pass

    @abstractmethod
    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Make predictions.

        Args:
            X: Feature matrix

        Returns:
            Predictions array
        """
        pass

    @abstractmethod
    def predict_with_confidence(
        self,
        X: np.ndarray,
        feature_names: Optional[List[str]] = None
    ) -> List[PredictionResult]:
        """
        Make predictions with confidence scores and explanations.

        Args:
            X: Feature matrix
            feature_names: Names of features for explanation

        Returns:
            List of PredictionResult objects
        """
        pass

    def is_fitted(self) -> bool:
        """Check if model has been trained."""
        return self._fitted and self.model is not None

    def get_feature_importance(self) -> Optional[Dict[str, float]]:
        """
        Get feature importance scores.

        Returns:
            Dict of feature_name: importance, or None if not available
        """
        if not self.is_fitted():
            return None

        if not hasattr(self.model, 'feature_importances_'):
            return None

        if self.metadata and self.metadata.feature_names:
            return dict(zip(
                self.metadata.feature_names,
                self.model.feature_importances_
            ))

        return None

    def get_top_features(self, n: int = 10) -> List[tuple]:
        """
        Get top N most important features.

        Args:
            n: Number of features to return

        Returns:
            List of (feature_name, importance) tuples
        """
        importance = self.get_feature_importance()
        if importance is None:
            return []

        sorted_features = sorted(
            importance.items(),
            key=lambda x: x[1],
            reverse=True
        )
        return sorted_features[:n]

    def save(self, path: Path):
        """
        Save model to file.

        Args:
            path: Path to save model
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Save model
        joblib.dump(self.model, path)

        # Save metadata
        if self.metadata:
            metadata_path = path.with_suffix('.json')
            self.metadata.save(metadata_path)

    def load(self, path: Path) -> 'BaseModel':
        """
        Load model from file.

        Args:
            path: Path to load model from

        Returns:
            self for method chaining
        """
        path = Path(path)

        # Load model
        self.model = joblib.load(path)
        self._fitted = True

        # Load metadata if exists
        metadata_path = path.with_suffix('.json')
        if metadata_path.exists():
            self.metadata = ModelMetadata.load(metadata_path)

        return self

    def _create_metadata(
        self,
        model_type: str,
        version: str,
        training_samples: int,
        feature_names: List[str],
        hyperparameters: Dict,
        metrics: Dict[str, float],
        description: str = ""
    ) -> ModelMetadata:
        """Create model metadata."""
        return ModelMetadata(
            model_name=self.name,
            model_type=model_type,
            version=version,
            training_samples=training_samples,
            feature_names=feature_names,
            feature_count=len(feature_names),
            hyperparameters=hyperparameters,
            metrics=metrics,
            description=description,
        )
