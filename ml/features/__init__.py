"""
Feature extraction module for ML predictions.

Provides feature extractors for:
- Structured features (court, NOS, defendant type)
- Text features (from complaint documents)
- Historical features (data-driven multipliers)
- Combined feature pipeline
"""
from .base import BaseFeatureExtractor, FeatureSet
from .structured import StructuredFeatureExtractor
from .text import TextFeatureExtractor
from .historical import HistoricalFeatureExtractor
from .pipeline import FeaturePipeline

__all__ = [
    'BaseFeatureExtractor',
    'FeatureSet',
    'StructuredFeatureExtractor',
    'TextFeatureExtractor',
    'HistoricalFeatureExtractor',
    'FeaturePipeline',
]
