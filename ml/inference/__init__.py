"""
Inference module for ML predictions.

Provides unified prediction interface.
"""
from .predictor import CasePredictor, PredictionOutput

__all__ = ['CasePredictor', 'PredictionOutput']
