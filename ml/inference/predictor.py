"""
Unified prediction interface for legal ML models.

Provides a single entry point for all prediction types.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Union
import numpy as np
from pathlib import Path
import json

from ..config import MLConfig, default_config, CURRENT_MODEL_DIR, MODEL_VERSION
from ..features.pipeline import FeaturePipeline
from ..models.dismissal import DismissalModel
from ..models.value import ValueModel
from ..models.resolution import ResolutionModel
from ..models.duration import DurationModel


@dataclass
class PredictionOutput:
    """Unified prediction output format."""
    # Dismissal prediction
    dismissal_probability: float = 0.0
    dismissal_confidence_interval_95: List[float] = field(default_factory=lambda: [0.0, 0.0])

    # Value prediction
    value_estimate: Dict[str, float] = field(default_factory=lambda: {
        'low': 0.0, 'mid': 0.0, 'high': 0.0
    })

    # Resolution prediction
    predicted_outcome: str = "unknown"
    outcome_probabilities: Dict[str, float] = field(default_factory=dict)

    # Duration prediction
    predicted_duration_days: Dict[str, float] = field(default_factory=lambda: {
        'low': 0, 'mid': 0, 'high': 0
    })

    # Explanation
    key_factors: List[str] = field(default_factory=list)

    # Metadata
    confidence: str = "low"  # low, medium, high
    model_version: str = MODEL_VERSION
    feature_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def format_value(self, amount: float) -> str:
        """Format dollar amount."""
        if amount >= 1_000_000_000:
            return f"${amount/1_000_000_000:.1f}B"
        elif amount >= 1_000_000:
            return f"${amount/1_000_000:.1f}M"
        elif amount >= 1_000:
            return f"${amount/1_000:.1f}K"
        return f"${amount:,.0f}"

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "=" * 50,
            "CASE PREDICTION ANALYSIS",
            "=" * 50,
            "",
            f"Model Version: {self.model_version}",
            f"Confidence: {self.confidence.upper()}",
            "",
            "DISMISSAL RISK",
            f"  Probability: {self.dismissal_probability:.1%}",
            f"  95% CI: [{self.dismissal_confidence_interval_95[0]:.1%}, {self.dismissal_confidence_interval_95[1]:.1%}]",
            "",
            "VALUE ESTIMATE",
            f"  Conservative (P25): {self.format_value(self.value_estimate['low'])}",
            f"  Expected (P50):     {self.format_value(self.value_estimate['mid'])}",
            f"  Aggressive (P75):   {self.format_value(self.value_estimate['high'])}",
            "",
            "PREDICTED OUTCOME",
            f"  Most Likely: {self.predicted_outcome.upper()}",
        ]

        if self.outcome_probabilities:
            lines.append("  Probabilities:")
            for outcome, prob in sorted(self.outcome_probabilities.items(), key=lambda x: -x[1]):
                lines.append(f"    {outcome}: {prob:.1%}")

        lines.extend([
            "",
            "PREDICTED DURATION",
            f"  Fast (P25):   {self.predicted_duration_days['low']:.0f} days",
            f"  Expected:     {self.predicted_duration_days['mid']:.0f} days",
            f"  Slow (P75):   {self.predicted_duration_days['high']:.0f} days",
        ])

        if self.key_factors:
            lines.extend([
                "",
                "KEY FACTORS",
            ])
            for factor in self.key_factors[:5]:
                lines.append(f"  - {factor}")

        lines.append("")

        return "\n".join(lines)


class CasePredictor:
    """
    Unified predictor for legal case outcomes.

    Loads trained models and provides prediction methods for:
    - Dismissal probability
    - Settlement value
    - Resolution path
    - Duration estimation
    """

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        config: Optional[MLConfig] = None,
    ):
        """
        Initialize the predictor.

        Args:
            model_dir: Directory containing trained models
            config: ML configuration
        """
        self.model_dir = Path(model_dir) if model_dir else CURRENT_MODEL_DIR
        self.config = config or default_config

        # Initialize models (load lazily)
        self._dismissal_model: Optional[DismissalModel] = None
        self._value_model: Optional[ValueModel] = None
        self._resolution_model: Optional[ResolutionModel] = None
        self._duration_model: Optional[DurationModel] = None

        # Feature pipeline
        self._pipeline: Optional[FeaturePipeline] = None

        # Model version
        self._version = MODEL_VERSION

    def _load_pipeline(self) -> FeaturePipeline:
        """Load or create feature pipeline."""
        if self._pipeline is not None:
            return self._pipeline

        pipeline_path = self.model_dir / "pipeline"
        if pipeline_path.exists():
            self._pipeline = FeaturePipeline(
                config=self.config,
                include_text=False,  # Match training config
                include_historical=True,
            )
            self._pipeline.load(pipeline_path)
        else:
            # Default to training config (no text features)
            self._pipeline = FeaturePipeline(
                config=self.config,
                include_text=False,
                include_historical=True,
            )

        return self._pipeline

    def _load_dismissal_model(self) -> Optional[DismissalModel]:
        """Load dismissal model."""
        if self._dismissal_model is not None:
            return self._dismissal_model

        model_path = self.model_dir / "dismissal.pkl"
        if model_path.exists():
            self._dismissal_model = DismissalModel(self.config.dismissal)
            self._dismissal_model.load(model_path)
            return self._dismissal_model
        return None

    def _load_value_model(self) -> Optional[ValueModel]:
        """Load value model."""
        if self._value_model is not None:
            return self._value_model

        model_path = self.model_dir / "value.pkl"
        if model_path.exists():
            self._value_model = ValueModel(self.config.value)
            self._value_model.load(model_path)
            return self._value_model
        return None

    def _load_resolution_model(self) -> Optional[ResolutionModel]:
        """Load resolution model."""
        if self._resolution_model is not None:
            return self._resolution_model

        model_path = self.model_dir / "resolution.pkl"
        if model_path.exists():
            self._resolution_model = ResolutionModel(self.config.resolution)
            self._resolution_model.load(model_path)
            return self._resolution_model
        return None

    def _load_duration_model(self) -> Optional[DurationModel]:
        """Load duration model."""
        if self._duration_model is not None:
            return self._duration_model

        model_path = self.model_dir / "duration.pkl"
        if model_path.exists():
            self._duration_model = DurationModel(self.config.duration)
            self._duration_model.load(model_path)
            return self._duration_model
        return None

    def _extract_features(
        self,
        court: Optional[str] = None,
        nos: Optional[str] = None,
        defendant: Optional[str] = None,
        class_action: bool = False,
        pro_se: bool = False,
        mdl: bool = False,
        complaint_text: Optional[str] = None,
        judge: Optional[str] = None,
        **kwargs
    ) -> tuple:
        """
        Extract features from inputs.

        Returns:
            Tuple of (feature_array, feature_names)
        """
        pipeline = self._load_pipeline()

        features = pipeline.extract(
            court=court,
            nos=nos,
            defendant=defendant,
            class_action=class_action,
            pro_se=pro_se,
            mdl=mdl,
            complaint_text=complaint_text,
            judge=judge,
        )

        return features.values.reshape(1, -1), features.names

    def predict_dismissal(
        self,
        court: Optional[str] = None,
        nos: Optional[str] = None,
        defendant: Optional[str] = None,
        judge: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Predict dismissal probability.

        Returns:
            Dict with probability and confidence interval
        """
        model = self._load_dismissal_model()
        if model is None:
            return {'error': 'Dismissal model not available'}

        X, feature_names = self._extract_features(
            court=court, nos=nos, defendant=defendant, judge=judge, **kwargs
        )

        results = model.predict_with_confidence(X, feature_names)
        result = results[0]

        # Calculate 95% CI (approximate)
        prob = result.prediction
        ci_width = (1 - result.confidence) * 0.2  # Scale by confidence
        ci_low = max(0, prob - ci_width)
        ci_high = min(1, prob + ci_width)

        return {
            'probability': prob,
            'confidence_interval_95': [ci_low, ci_high],
            'key_factors': result.key_factors,
        }

    def predict_value(
        self,
        court: Optional[str] = None,
        nos: Optional[str] = None,
        defendant: Optional[str] = None,
        class_action: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Predict settlement value.

        Returns:
            Dict with low/mid/high estimates
        """
        model = self._load_value_model()
        if model is None:
            return {'error': 'Value model not available'}

        X, feature_names = self._extract_features(
            court=court, nos=nos, defendant=defendant,
            class_action=class_action, **kwargs
        )

        results = model.predict_with_confidence(X, feature_names)
        result = results[0]

        return {
            'low': result.low,
            'mid': result.mid,
            'high': result.high,
            'confidence': result.confidence,
            'key_factors': result.key_factors,
        }

    def predict_resolution(
        self,
        court: Optional[str] = None,
        nos: Optional[str] = None,
        defendant: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Predict resolution path.

        Returns:
            Dict with predicted outcome and probabilities
        """
        model = self._load_resolution_model()
        if model is None:
            return {'error': 'Resolution model not available'}

        X, feature_names = self._extract_features(
            court=court, nos=nos, defendant=defendant, **kwargs
        )

        results = model.predict_with_confidence(X, feature_names)
        result = results[0]

        return {
            'predicted_outcome': result.prediction,
            'probabilities': result.probabilities,
            'confidence': result.confidence,
            'key_factors': result.key_factors,
        }

    def predict_duration(
        self,
        court: Optional[str] = None,
        nos: Optional[str] = None,
        defendant: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Predict case duration.

        Returns:
            Dict with low/mid/high duration estimates (days)
        """
        model = self._load_duration_model()
        if model is None:
            return {'error': 'Duration model not available'}

        X, feature_names = self._extract_features(
            court=court, nos=nos, defendant=defendant, **kwargs
        )

        results = model.predict_with_confidence(X, feature_names)
        result = results[0]

        return {
            'low': result.low,
            'mid': result.mid,
            'high': result.high,
            'confidence': result.confidence,
            'key_factors': result.key_factors,
        }

    def predict(
        self,
        court: Optional[str] = None,
        nos: Optional[str] = None,
        defendant: Optional[str] = None,
        class_action: bool = False,
        pro_se: bool = False,
        mdl: bool = False,
        complaint_text: Optional[str] = None,
        judge: Optional[str] = None,
        **kwargs
    ) -> PredictionOutput:
        """
        Full case prediction using all models.

        Args:
            court: Court code (e.g., 'cacd', 'nysd')
            nos: Nature of suit code or description
            defendant: Defendant name
            class_action: Is this a class action
            pro_se: Is plaintiff pro se
            mdl: Is this part of MDL
            complaint_text: Full complaint text (optional)
            judge: Judge name (optional)
            **kwargs: Additional arguments

        Returns:
            PredictionOutput with all predictions
        """
        # Extract features once
        X, feature_names = self._extract_features(
            court=court,
            nos=nos,
            defendant=defendant,
            class_action=class_action,
            pro_se=pro_se,
            mdl=mdl,
            complaint_text=complaint_text,
            judge=judge,
        )

        output = PredictionOutput(
            model_version=self._version,
            feature_count=len(feature_names),
        )

        all_key_factors = []
        confidences = []

        # Dismissal prediction
        dismissal_model = self._load_dismissal_model()
        if dismissal_model is not None:
            results = dismissal_model.predict_with_confidence(X, feature_names)
            result = results[0]
            output.dismissal_probability = result.prediction
            ci_width = (1 - result.confidence) * 0.2
            output.dismissal_confidence_interval_95 = [
                max(0, result.prediction - ci_width),
                min(1, result.prediction + ci_width)
            ]
            all_key_factors.extend(result.key_factors)
            confidences.append(result.confidence)

        # Value prediction
        value_model = self._load_value_model()
        if value_model is not None:
            results = value_model.predict_with_confidence(X, feature_names)
            result = results[0]
            output.value_estimate = {
                'low': result.low,
                'mid': result.mid,
                'high': result.high,
            }
            all_key_factors.extend(result.key_factors)
            confidences.append(result.confidence)

        # Resolution prediction
        resolution_model = self._load_resolution_model()
        if resolution_model is not None:
            results = resolution_model.predict_with_confidence(X, feature_names)
            result = results[0]
            output.predicted_outcome = result.prediction
            output.outcome_probabilities = result.probabilities or {}
            all_key_factors.extend(result.key_factors)
            confidences.append(result.confidence)

        # Duration prediction
        duration_model = self._load_duration_model()
        if duration_model is not None:
            results = duration_model.predict_with_confidence(X, feature_names)
            result = results[0]
            output.predicted_duration_days = {
                'low': result.low,
                'mid': result.mid,
                'high': result.high,
            }
            all_key_factors.extend(result.key_factors)
            confidences.append(result.confidence)

        # Aggregate key factors (deduplicate)
        seen = set()
        unique_factors = []
        for factor in all_key_factors:
            if factor not in seen:
                seen.add(factor)
                unique_factors.append(factor)
        output.key_factors = unique_factors[:10]

        # Overall confidence
        if confidences:
            avg_confidence = np.mean(confidences)
            if avg_confidence >= 0.7:
                output.confidence = "high"
            elif avg_confidence >= 0.4:
                output.confidence = "medium"
            else:
                output.confidence = "low"

        return output

    def is_available(self) -> Dict[str, bool]:
        """Check which models are available."""
        return {
            'dismissal': (self.model_dir / "dismissal.pkl").exists(),
            'value': (self.model_dir / "value.pkl").exists(),
            'resolution': (self.model_dir / "resolution.pkl").exists(),
            'duration': (self.model_dir / "duration.pkl").exists(),
        }
