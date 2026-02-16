"""
Settlement value prediction model.

Predicts settlement value ranges using quantile regression
to produce P25/P50/P75 estimates with confidence intervals.
"""
from typing import Dict, List, Optional, Tuple
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import cross_val_score
from pathlib import Path

from .base import BaseModel, ModelMetadata, PredictionResult
from ..config import ValueModelConfig, MODEL_VERSION


class ValueModel(BaseModel):
    """
    Model to predict settlement value ranges.

    Uses GradientBoostingRegressor with quantile loss for
    multi-quantile prediction (P25, P50, P75).
    """

    def __init__(self, config: Optional[ValueModelConfig] = None):
        """
        Initialize the value model.

        Args:
            config: Model configuration
        """
        super().__init__()
        self.config = config or ValueModelConfig()
        self.quantile_models: Dict[float, GradientBoostingRegressor] = {}
        self.model = None  # Will be the median model

    @property
    def name(self) -> str:
        return "value"

    def _create_quantile_model(self, quantile: float) -> GradientBoostingRegressor:
        """Create a gradient boosting model for a specific quantile."""
        return GradientBoostingRegressor(
            n_estimators=self.config.n_estimators,
            learning_rate=self.config.learning_rate,
            max_depth=self.config.max_depth,
            min_samples_split=self.config.min_samples_split,
            min_samples_leaf=self.config.min_samples_leaf,
            subsample=self.config.subsample,
            loss='quantile',
            alpha=quantile,
            random_state=42,
        )

    def _transform_target(self, y: np.ndarray) -> np.ndarray:
        """Apply log transform to target if configured."""
        if self.config.log_transform:
            return np.log1p(y)  # log(1 + y) to handle zeros
        return y

    def _inverse_transform_target(self, y: np.ndarray) -> np.ndarray:
        """Inverse log transform predictions."""
        if self.config.log_transform:
            return np.expm1(y)  # exp(y) - 1
        return y

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: Optional[List[str]] = None,
        cv_folds: int = 5,
        **kwargs
    ) -> 'ValueModel':
        """
        Train quantile regression models.

        Args:
            X: Feature matrix
            y: Settlement amounts (dollars)
            feature_names: Names of features
            cv_folds: Number of CV folds for evaluation
            **kwargs: Additional arguments

        Returns:
            self for method chaining
        """
        # Transform target
        y_transformed = self._transform_target(y)

        # Train model for each quantile
        for quantile in self.config.quantiles:
            model = self._create_quantile_model(quantile)
            model.fit(X, y_transformed)
            self.quantile_models[quantile] = model

        # Set main model to median
        self.model = self.quantile_models.get(0.5)

        # Compute metrics using median model
        cv_scores = cross_val_score(
            self._create_quantile_model(0.5),
            X, y_transformed,
            cv=min(cv_folds, len(y) // 2),
            scoring='neg_mean_absolute_error'
        )

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
                'quantiles': self.config.quantiles,
                'log_transform': self.config.log_transform,
            },
            metrics={
                'cv_mae_mean': float(-cv_scores.mean()),
                'cv_mae_std': float(cv_scores.std()),
                'mean_settlement': float(y.mean()),
                'median_settlement': float(np.median(y)),
            },
            description="Settlement value quantile regression model"
        )

        self._fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Predict median settlement value.

        Args:
            X: Feature matrix

        Returns:
            Array of predicted median values (dollars)
        """
        if not self.is_fitted():
            raise ValueError("Model must be fitted before prediction")

        if 0.5 not in self.quantile_models:
            raise ValueError("Median model not available")

        predictions = self.quantile_models[0.5].predict(X)
        return self._inverse_transform_target(predictions)

    def predict_quantiles(self, X: np.ndarray) -> Dict[float, np.ndarray]:
        """
        Predict all quantiles.

        Args:
            X: Feature matrix

        Returns:
            Dict of quantile: predictions
        """
        if not self.is_fitted():
            raise ValueError("Model must be fitted before prediction")

        results = {}
        for quantile, model in self.quantile_models.items():
            predictions = model.predict(X)
            results[quantile] = self._inverse_transform_target(predictions)

        return results

    def predict_range(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Predict low/mid/high settlement values.

        Args:
            X: Feature matrix

        Returns:
            Tuple of (low, mid, high) arrays (P25, P50, P75)
        """
        quantile_preds = self.predict_quantiles(X)

        low = quantile_preds.get(0.25, quantile_preds.get(min(quantile_preds.keys())))
        mid = quantile_preds.get(0.50)
        high = quantile_preds.get(0.75, quantile_preds.get(max(quantile_preds.keys())))

        return low, mid, high

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

        # Get quantile predictions
        low, mid, high = self.predict_range(X)

        # Get feature importance
        top_features = self.get_top_features(5)

        results = []
        for i in range(len(mid)):
            # Calculate confidence based on range width
            # Narrower ranges = higher confidence
            range_width = high[i] - low[i]
            if mid[i] > 0:
                cv = range_width / mid[i]  # Coefficient of variation
                confidence = max(0, 1 - min(cv, 1))  # Scale to 0-1
            else:
                confidence = 0.5

            # Create key factors
            key_factors = []
            if feature_names:
                sample = X[i] if len(X.shape) > 1 else X
                for feat_name, importance in top_features[:3]:
                    if feat_name in feature_names:
                        idx = feature_names.index(feat_name)
                        key_factors.append(f"{feat_name}: {sample[idx]:.2f}")

            results.append(PredictionResult(
                prediction=float(mid[i]),
                confidence=confidence,
                low=float(low[i]),
                mid=float(mid[i]),
                high=float(high[i]),
                key_factors=key_factors,
                model_name=self.name,
                model_version=self.metadata.version if self.metadata else MODEL_VERSION,
            ))

        return results

    def get_feature_importance(self) -> Optional[Dict[str, float]]:
        """Get feature importance from median model."""
        if not self.is_fitted() or 0.5 not in self.quantile_models:
            return None

        median_model = self.quantile_models[0.5]
        if hasattr(median_model, 'feature_importances_'):
            if self.metadata and self.metadata.feature_names:
                return dict(zip(
                    self.metadata.feature_names,
                    median_model.feature_importances_
                ))
        return None

    def format_prediction(self, value: float) -> str:
        """Format dollar value for display."""
        if value >= 1_000_000_000:
            return f"${value/1_000_000_000:.1f}B"
        elif value >= 1_000_000:
            return f"${value/1_000_000:.1f}M"
        elif value >= 1_000:
            return f"${value/1_000:.1f}K"
        return f"${value:,.0f}"

    def save(self, path: Path):
        """Save all quantile models."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        import joblib
        model_data = {
            'quantile_models': self.quantile_models,
            'config': self.config,
        }
        joblib.dump(model_data, path)

        if self.metadata:
            metadata_path = path.with_suffix('.json')
            self.metadata.save(metadata_path)

    def load(self, path: Path) -> 'ValueModel':
        """Load all quantile models."""
        path = Path(path)

        import joblib
        model_data = joblib.load(path)

        self.quantile_models = model_data.get('quantile_models', {})
        self.config = model_data.get('config', ValueModelConfig())
        self.model = self.quantile_models.get(0.5)
        self._fitted = bool(self.quantile_models)

        metadata_path = path.with_suffix('.json')
        if metadata_path.exists():
            self.metadata = ModelMetadata.load(metadata_path)

        return self
