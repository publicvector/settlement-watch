"""
ONNX-based predictor for lightweight deployment.

Uses onnxruntime (~60MB) instead of scikit-learn (~150MB+)
for Vercel-compatible deployment.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from pathlib import Path
import json

import numpy as np

# Lazy import onnxruntime
_ort = None
def get_ort():
    global _ort
    if _ort is None:
        import onnxruntime as ort
        _ort = ort
    return _ort


@dataclass
class ONNXPredictionOutput:
    """Prediction output format."""
    dismissal_probability: float = 0.0
    dismissal_confidence: List[float] = field(default_factory=lambda: [0.0, 0.0])
    value_estimate: Dict[str, float] = field(default_factory=lambda: {'low': 0, 'mid': 0, 'high': 0})
    predicted_outcome: str = "unknown"
    outcome_probabilities: Dict[str, float] = field(default_factory=dict)
    predicted_duration_days: Dict[str, float] = field(default_factory=lambda: {'low': 0, 'mid': 0, 'high': 0})
    confidence: str = "medium"
    model_version: str = "v1.0.0"

    def to_dict(self) -> Dict:
        return asdict(self)


class ONNXPredictor:
    """
    Lightweight predictor using ONNX models.
    """

    def __init__(self, model_dir: Optional[Path] = None):
        """Initialize with model directory."""
        if model_dir is None:
            from ..config import CURRENT_MODEL_DIR
            model_dir = CURRENT_MODEL_DIR

        self.model_dir = Path(model_dir)
        self.onnx_dir = self.model_dir / "onnx"

        # Lazy-loaded sessions
        self._sessions: Dict[str, Any] = {}

        # Load metadata
        self._load_metadata()

    def _load_metadata(self):
        """Load model metadata."""
        metadata_path = self.model_dir / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path) as f:
                self.metadata = json.load(f)
        else:
            self.metadata = {"version": "v1.0.0", "feature_count": 18}

    def _get_session(self, model_name: str):
        """Get or create ONNX inference session."""
        if model_name not in self._sessions:
            model_path = self.onnx_dir / f"{model_name}.onnx"
            if model_path.exists():
                ort = get_ort()
                self._sessions[model_name] = ort.InferenceSession(
                    str(model_path),
                    providers=['CPUExecutionProvider']
                )
            else:
                return None
        return self._sessions[model_name]

    def _extract_features(
        self,
        court: Optional[str] = None,
        nos: Optional[str] = None,
        defendant: Optional[str] = None,
        class_action: bool = False,
        **kwargs
    ) -> np.ndarray:
        """
        Extract features for prediction.

        Simplified feature extraction that matches training pipeline.
        """
        # Simple feature encoding (matches structured + historical features)
        features = np.zeros(18, dtype=np.float32)

        # Court encoding (simplified)
        court_map = {
            'cacd': 0, 'cand': 1, 'casd': 2, 'caed': 3,
            'nysd': 4, 'nyed': 5, 'nynd': 6,
            'txsd': 7, 'txed': 8, 'txnd': 9, 'txwd': 10,
            'ilnd': 11, 'flsd': 12, 'paed': 13, 'njd': 14, 'dcd': 15, 'mad': 16
        }
        features[0] = court_map.get((court or '').lower(), 0)

        # NOS encoding (simplified - use code if numeric)
        if nos and nos.isdigit():
            features[1] = float(nos) / 1000  # Normalize
        else:
            features[1] = 0.5

        # NOS category (derived)
        features[2] = features[1]

        # Defendant type (simplified detection)
        defendant_lower = (defendant or '').lower()
        if any(x in defendant_lower for x in ['google', 'apple', 'meta', 'amazon', 'microsoft']):
            features[3] = 0  # Fortune 500
        elif any(x in defendant_lower for x in ['inc', 'corp', 'llc', 'ltd']):
            features[3] = 1  # Large corp
        else:
            features[3] = 2  # Other

        # Binary features
        features[4] = 1.0 if class_action else 0.0
        features[5] = 0.0  # pro_se
        features[6] = 0.0  # mdl

        # Historical features (defaults)
        features[7] = 1.0   # jurisdiction_multiplier
        features[8] = 8.0   # court_median_settlement_log
        features[9] = 50.0  # court_case_count
        features[10] = 0.3  # court_dismissal_rate
        features[11] = 0.0  # defendant_total_paid_log
        features[12] = 0.0  # defendant_avg_settlement_log
        features[13] = 0.0  # defendant_case_count
        features[14] = 8.0  # nos_median_settlement_log
        features[15] = 50.0 # nos_case_count
        features[16] = 0.5  # judge_mtd_rate
        features[17] = 0.0  # judge_case_count

        return features.reshape(1, -1)

    def predict_dismissal(self, **kwargs) -> Dict[str, Any]:
        """Predict dismissal probability."""
        session = self._get_session("dismissal")
        if session is None:
            return {"error": "Dismissal model not available"}

        X = self._extract_features(**kwargs)

        # Run inference
        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: X})

        # Get probability (output is [labels, probabilities])
        if len(output) > 1:
            probs = output[1]
            prob = float(probs[0][1]) if probs.shape[1] > 1 else float(probs[0][0])
        else:
            prob = float(output[0][0])

        return {
            "probability": prob,
            "confidence_interval_95": [max(0, prob - 0.1), min(1, prob + 0.1)],
        }

    def predict_value(self, **kwargs) -> Dict[str, Any]:
        """Predict settlement value range."""
        X = self._extract_features(**kwargs)

        results = {}
        for quantile, name in [(25, 'low'), (50, 'mid'), (75, 'high')]:
            session = self._get_session(f"value_q{quantile}")
            if session:
                input_name = session.get_inputs()[0].name
                output = session.run(None, {input_name: X})
                # Inverse log transform
                log_value = float(output[0][0])
                results[name] = np.expm1(log_value)

        if not results:
            return {"error": "Value model not available"}

        return results

    def predict_resolution(self, **kwargs) -> Dict[str, Any]:
        """Predict resolution path."""
        session = self._get_session("resolution")
        if session is None:
            return {"error": "Resolution model not available"}

        X = self._extract_features(**kwargs)

        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: X})

        # Get class and probabilities
        classes = ['dismissal', 'settlement']  # Simplified
        if len(output) > 1:
            probs = output[1][0]
            pred_idx = int(output[0][0])
        else:
            pred_idx = int(output[0][0])
            probs = [0.0, 1.0] if pred_idx == 1 else [1.0, 0.0]

        return {
            "predicted_outcome": classes[pred_idx] if pred_idx < len(classes) else "settlement",
            "probabilities": {c: float(probs[i]) if i < len(probs) else 0.0 for i, c in enumerate(classes)},
        }

    def predict_duration(self, **kwargs) -> Dict[str, Any]:
        """Predict case duration."""
        X = self._extract_features(**kwargs)

        results = {}
        for quantile, name in [(25, 'low'), (50, 'mid'), (75, 'high')]:
            session = self._get_session(f"duration_q{quantile}")
            if session:
                input_name = session.get_inputs()[0].name
                output = session.run(None, {input_name: X})
                results[name] = float(output[0][0])

        if not results:
            return {"error": "Duration model not available"}

        return results

    def predict(self, **kwargs) -> ONNXPredictionOutput:
        """Full prediction using all models."""
        output = ONNXPredictionOutput(model_version=self.metadata.get("version", "v1.0.0"))

        # Dismissal
        dismissal = self.predict_dismissal(**kwargs)
        if "probability" in dismissal:
            output.dismissal_probability = dismissal["probability"]
            output.dismissal_confidence = dismissal.get("confidence_interval_95", [0, 0])

        # Value
        value = self.predict_value(**kwargs)
        if "error" not in value:
            output.value_estimate = value

        # Resolution
        resolution = self.predict_resolution(**kwargs)
        if "predicted_outcome" in resolution:
            output.predicted_outcome = resolution["predicted_outcome"]
            output.outcome_probabilities = resolution.get("probabilities", {})

        # Duration
        duration = self.predict_duration(**kwargs)
        if "error" not in duration:
            output.predicted_duration_days = duration

        return output

    def is_available(self) -> Dict[str, bool]:
        """Check which ONNX models are available."""
        return {
            'dismissal': (self.onnx_dir / "dismissal.onnx").exists(),
            'value': (self.onnx_dir / "value_q50.onnx").exists(),
            'resolution': (self.onnx_dir / "resolution.onnx").exists(),
            'duration': (self.onnx_dir / "duration_q50.onnx").exists(),
        }
