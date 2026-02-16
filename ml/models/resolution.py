"""
Resolution path classification model.

Predicts the likely outcome path: dismissal, settlement, judgment, or trial.
"""
from typing import Dict, List, Optional
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import LabelEncoder
from pathlib import Path

from .base import BaseModel, ModelMetadata, PredictionResult
from ..config import ResolutionModelConfig, MODEL_VERSION


class ResolutionModel(BaseModel):
    """
    Model to predict case resolution path.

    Uses RandomForestClassifier to predict multiclass outcome:
    - dismissal: Case dismissed (with or without prejudice)
    - settlement: Case settled
    - judgment: Judgment entered (summary or default)
    - trial: Case went to trial
    """

    def __init__(self, config: Optional[ResolutionModelConfig] = None):
        """
        Initialize the resolution model.

        Args:
            config: Model configuration
        """
        super().__init__()
        self.config = config or ResolutionModelConfig()
        self.label_encoder = LabelEncoder()
        self.model = None
        self._class_names = None

    @property
    def name(self) -> str:
        return "resolution"

    def _create_model(self) -> RandomForestClassifier:
        """Create the random forest classifier."""
        return RandomForestClassifier(
            n_estimators=self.config.n_estimators,
            max_depth=self.config.max_depth,
            min_samples_split=self.config.min_samples_split,
            min_samples_leaf=self.config.min_samples_leaf,
            class_weight=self.config.class_weight,
            random_state=42,
            n_jobs=-1,
        )

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: Optional[List[str]] = None,
        cv_folds: int = 5,
        **kwargs
    ) -> 'ResolutionModel':
        """
        Train the resolution model.

        Args:
            X: Feature matrix
            y: Resolution labels (strings or encoded)
            feature_names: Names of features
            cv_folds: Number of CV folds
            **kwargs: Additional arguments

        Returns:
            self for method chaining
        """
        # Encode labels if strings
        if y.dtype == object or isinstance(y[0], str):
            y_encoded = self.label_encoder.fit_transform(y)
            self._class_names = list(self.label_encoder.classes_)
        else:
            y_encoded = y
            self._class_names = self.config.classes

        # Create and train model
        self.model = self._create_model()
        self.model.fit(X, y_encoded)

        # Compute metrics
        cv_scores = cross_val_score(
            self._create_model(),
            X, y_encoded,
            cv=min(cv_folds, len(y) // 2),
            scoring='accuracy'
        )

        # Class distribution
        unique, counts = np.unique(y_encoded, return_counts=True)
        class_distribution = {
            self._class_names[int(u)] if int(u) < len(self._class_names) else str(u): int(c)
            for u, c in zip(unique, counts)
        }

        # Create metadata
        self.metadata = self._create_metadata(
            model_type=self.config.model_type,
            version=MODEL_VERSION,
            training_samples=len(y),
            feature_names=feature_names or [],
            hyperparameters={
                'n_estimators': self.config.n_estimators,
                'max_depth': self.config.max_depth,
                'class_weight': self.config.class_weight,
                'classes': self._class_names,
            },
            metrics={
                'cv_accuracy_mean': float(cv_scores.mean()),
                'cv_accuracy_std': float(cv_scores.std()),
                'n_classes': len(self._class_names),
                'class_distribution': class_distribution,
            },
            description="Case resolution path classifier"
        )

        self._fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Predict resolution class.

        Args:
            X: Feature matrix

        Returns:
            Array of predicted class labels (strings)
        """
        if not self.is_fitted():
            raise ValueError("Model must be fitted before prediction")

        y_pred = self.model.predict(X)

        # Decode back to class names
        if self._class_names:
            return np.array([self._class_names[int(p)] for p in y_pred])
        return y_pred

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """
        Predict class probabilities.

        Args:
            X: Feature matrix

        Returns:
            Array of shape (n_samples, n_classes) with probabilities
        """
        if not self.is_fitted():
            raise ValueError("Model must be fitted before prediction")

        return self.model.predict_proba(X)

    def predict_with_confidence(
        self,
        X: np.ndarray,
        feature_names: Optional[List[str]] = None
    ) -> List[PredictionResult]:
        """
        Predict with confidence and class probabilities.

        Args:
            X: Feature matrix
            feature_names: Names of features

        Returns:
            List of PredictionResult objects
        """
        if not self.is_fitted():
            raise ValueError("Model must be fitted before prediction")

        # Get predictions and probabilities
        y_pred = self.predict(X)
        y_proba = self.predict_proba(X)

        # Get feature importance
        top_features = self.get_top_features(5)

        results = []
        for i in range(len(y_pred)):
            # Get probability distribution
            probs = y_proba[i]
            prob_dict = {
                self._class_names[j]: float(probs[j])
                for j in range(len(probs))
            }

            # Confidence is the probability of the predicted class
            predicted_class = y_pred[i]
            confidence = prob_dict.get(predicted_class, 0.0)

            # Create key factors
            key_factors = []
            if feature_names:
                sample = X[i] if len(X.shape) > 1 else X
                for feat_name, importance in top_features[:3]:
                    if feat_name in feature_names:
                        idx = feature_names.index(feat_name)
                        key_factors.append(f"{feat_name}: {sample[idx]:.2f}")

            results.append(PredictionResult(
                prediction=predicted_class,
                confidence=confidence,
                probabilities=prob_dict,
                key_factors=key_factors,
                model_name=self.name,
                model_version=self.metadata.version if self.metadata else MODEL_VERSION,
            ))

        return results

    def get_class_names(self) -> List[str]:
        """Get the class names in order."""
        return self._class_names or self.config.classes

    def save(self, path: Path):
        """Save model and label encoder."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        import joblib
        model_data = {
            'model': self.model,
            'label_encoder': self.label_encoder,
            'class_names': self._class_names,
            'config': self.config,
        }
        joblib.dump(model_data, path)

        if self.metadata:
            metadata_path = path.with_suffix('.json')
            self.metadata.save(metadata_path)

    def load(self, path: Path) -> 'ResolutionModel':
        """Load model and label encoder."""
        path = Path(path)

        import joblib
        model_data = joblib.load(path)

        self.model = model_data.get('model')
        self.label_encoder = model_data.get('label_encoder', LabelEncoder())
        self._class_names = model_data.get('class_names', self.config.classes)
        self.config = model_data.get('config', ResolutionModelConfig())
        self._fitted = self.model is not None

        metadata_path = path.with_suffix('.json')
        if metadata_path.exists():
            self.metadata = ModelMetadata.load(metadata_path)

        return self
