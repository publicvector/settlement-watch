"""
Dismissal probability model.

Predicts the probability that a motion to dismiss will be granted
using GradientBoosting with isotonic calibration.
"""
from typing import Dict, List, Optional, Any
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import cross_val_score
from pathlib import Path

from .base import BaseModel, ModelMetadata, PredictionResult
from ..config import DismissalModelConfig, MODEL_VERSION


class DismissalModel(BaseModel):
    """
    Model to predict motion to dismiss grant probability.

    Uses GradientBoostingClassifier with isotonic calibration
    to produce well-calibrated probability estimates.
    """

    def __init__(self, config: Optional[DismissalModelConfig] = None):
        """
        Initialize the dismissal model.

        Args:
            config: Model configuration
        """
        super().__init__()
        self.config = config or DismissalModelConfig()
        self.base_model = None
        self.calibrated_model = None
        self.model = None  # Will be calibrated_model after training

    @property
    def name(self) -> str:
        return "dismissal"

    def _create_base_model(self) -> GradientBoostingClassifier:
        """Create the base gradient boosting model."""
        return GradientBoostingClassifier(
            n_estimators=self.config.n_estimators,
            learning_rate=self.config.learning_rate,
            max_depth=self.config.max_depth,
            min_samples_split=self.config.min_samples_split,
            min_samples_leaf=self.config.min_samples_leaf,
            subsample=self.config.subsample,
            random_state=42,
        )

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: Optional[List[str]] = None,
        cv_folds: int = 5,
        **kwargs
    ) -> 'DismissalModel':
        """
        Train the dismissal model.

        Args:
            X: Feature matrix
            y: Binary target (1 = granted, 0 = denied)
            feature_names: Names of features
            cv_folds: Number of CV folds for calibration
            **kwargs: Additional arguments

        Returns:
            self for method chaining
        """
        # Create base model
        self.base_model = self._create_base_model()

        # Fit base model
        self.base_model.fit(X, y)

        # Calibrate if requested and enough samples per class
        min_class_count = min(np.sum(y == 0), np.sum(y == 1))
        if self.config.calibrate and min_class_count >= cv_folds:
            # Use cross-validation based calibration
            self.calibrated_model = CalibratedClassifierCV(
                estimator=self._create_base_model(),
                method=self.config.calibration_method,
                cv=min(cv_folds, min_class_count),
            )
            self.calibrated_model.fit(X, y)
            self.model = self.calibrated_model
        else:
            # Skip calibration if not enough samples
            self.model = self.base_model

        # Compute metrics
        min_class_count = min(np.sum(y == 0), np.sum(y == 1))
        effective_cv = min(cv_folds, min_class_count, len(y) // 2)
        if effective_cv >= 2:
            cv_scores = cross_val_score(
                self._create_base_model(), X, y,
                cv=effective_cv,
                scoring='roc_auc'
            )
        else:
            # Not enough samples for CV, use training score
            cv_scores = np.array([0.5])

        # Create metadata
        self.metadata = self._create_metadata(
            model_type=self.config.model_type,
            version=MODEL_VERSION,
            training_samples=len(y),
            feature_names=feature_names or [],
            hyperparameters={
                'n_estimators': self.config.n_estimators,
                'learning_rate': self.config.learning_rate,
                'max_depth': self.config.max_depth,
                'calibrated': self.config.calibrate,
                'calibration_method': self.config.calibration_method,
            },
            metrics={
                'cv_auc_mean': float(cv_scores.mean()),
                'cv_auc_std': float(cv_scores.std()),
                'positive_rate': float(y.mean()),
            },
            description="Motion to dismiss grant probability model"
        )

        self._fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Predict dismissal probability.

        Args:
            X: Feature matrix

        Returns:
            Array of probabilities (P(granted))
        """
        if not self.is_fitted():
            raise ValueError("Model must be fitted before prediction")

        # Return probability of positive class (granted)
        return self.model.predict_proba(X)[:, 1]

    def predict_class(self, X: np.ndarray, threshold: float = 0.5) -> np.ndarray:
        """
        Predict binary class (granted/denied).

        Args:
            X: Feature matrix
            threshold: Decision threshold

        Returns:
            Array of binary predictions
        """
        probs = self.predict(X)
        return (probs >= threshold).astype(int)

    def predict_with_confidence(
        self,
        X: np.ndarray,
        feature_names: Optional[List[str]] = None
    ) -> List[PredictionResult]:
        """
        Predict with confidence intervals and explanations.

        Args:
            X: Feature matrix
            feature_names: Names of features

        Returns:
            List of PredictionResult objects
        """
        if not self.is_fitted():
            raise ValueError("Model must be fitted before prediction")

        # Get probabilities
        probs = self.predict(X)

        # Get feature importance for explanations
        feature_importance = self.get_feature_importance()
        top_features = self.get_top_features(5)

        results = []
        for i, prob in enumerate(probs):
            # Calculate confidence based on probability distance from 0.5
            # Predictions near 0 or 1 are more confident
            confidence = abs(prob - 0.5) * 2  # Scale to 0-1

            # Create key factors explanation
            key_factors = []
            if feature_names and feature_importance:
                # Get feature values for this sample
                sample = X[i] if len(X.shape) > 1 else X
                for feat_name, importance in top_features[:3]:
                    if feat_name in feature_names:
                        idx = feature_names.index(feat_name)
                        key_factors.append(f"{feat_name}: {sample[idx]:.2f}")

            results.append(PredictionResult(
                prediction=float(prob),
                confidence=confidence,
                probabilities={'granted': float(prob), 'denied': float(1 - prob)},
                key_factors=key_factors,
                model_name=self.name,
                model_version=self.metadata.version if self.metadata else MODEL_VERSION,
            ))

        return results

    def get_feature_importance(self) -> Optional[Dict[str, float]]:
        """Get feature importance from base model."""
        if not self.is_fitted():
            return None

        # Use base model's feature importance (calibrated model wraps it)
        if self.base_model is not None and hasattr(self.base_model, 'feature_importances_'):
            if self.metadata and self.metadata.feature_names:
                return dict(zip(
                    self.metadata.feature_names,
                    self.base_model.feature_importances_
                ))
        return None

    def save(self, path: Path):
        """Save model components."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Save as dict with both models
        model_data = {
            'base_model': self.base_model,
            'calibrated_model': self.calibrated_model,
            'config': self.config,
        }

        import joblib
        joblib.dump(model_data, path)

        # Save metadata
        if self.metadata:
            metadata_path = path.with_suffix('.json')
            self.metadata.save(metadata_path)

    def load(self, path: Path) -> 'DismissalModel':
        """Load model components."""
        path = Path(path)

        import joblib
        model_data = joblib.load(path)

        self.base_model = model_data.get('base_model')
        self.calibrated_model = model_data.get('calibrated_model')
        self.config = model_data.get('config', DismissalModelConfig())

        # Set active model
        if self.calibrated_model is not None:
            self.model = self.calibrated_model
        else:
            self.model = self.base_model

        self._fitted = True

        # Load metadata
        metadata_path = path.with_suffix('.json')
        if metadata_path.exists():
            self.metadata = ModelMetadata.load(metadata_path)

        return self
