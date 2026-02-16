"""
Predictive Legal Analytics ML Module.

Provides ML-powered predictions for legal case outcomes including:
- Dismissal probability (motion to dismiss)
- Settlement value estimation with confidence intervals
- Resolution path classification (dismissal/settlement/judgment/trial)
- Duration prediction with quantile estimates

Usage:
    from ml import CasePredictor

    predictor = CasePredictor()
    result = predictor.predict(
        court='cacd',
        nos='442',
        defendant='Tech Corp',
        class_action=True
    )
"""

from .config import MLConfig

# Lazy import CasePredictor to avoid requiring numpy at import time
def get_predictor():
    """Get CasePredictor class (lazy import)."""
    from .inference.predictor import CasePredictor
    return CasePredictor

__all__ = ['MLConfig', 'get_predictor']
