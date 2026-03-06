"""
Inference module for ML predictions.

Provides unified prediction interface.
Imports are lazy to support lightweight ONNX-only deployment.
"""

# Lazy imports to avoid sklearn dependency when using ONNX only
def __getattr__(name):
    if name == 'CasePredictor':
        from .predictor import CasePredictor
        return CasePredictor
    elif name == 'PredictionOutput':
        from .predictor import PredictionOutput
        return PredictionOutput
    elif name == 'ONNXPredictor':
        from .onnx_predictor import ONNXPredictor
        return ONNXPredictor
    elif name == 'ONNXPredictionOutput':
        from .onnx_predictor import ONNXPredictionOutput
        return ONNXPredictionOutput
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ['CasePredictor', 'PredictionOutput', 'ONNXPredictor', 'ONNXPredictionOutput']
