"""
Feature extraction module for ML predictions.

Provides feature extractors for:
- Structured features (court, NOS, defendant type)
- Text features (from complaint documents)
- Historical features (data-driven multipliers)
- Combined feature pipeline

Imports are lazy to support lightweight ONNX-only deployment.
"""

# Lazy imports to avoid sklearn dependency when using ONNX only
def __getattr__(name):
    if name == 'BaseFeatureExtractor':
        from .base import BaseFeatureExtractor
        return BaseFeatureExtractor
    elif name == 'FeatureSet':
        from .base import FeatureSet
        return FeatureSet
    elif name == 'StructuredFeatureExtractor':
        from .structured import StructuredFeatureExtractor
        return StructuredFeatureExtractor
    elif name == 'TextFeatureExtractor':
        from .text import TextFeatureExtractor
        return TextFeatureExtractor
    elif name == 'HistoricalFeatureExtractor':
        from .historical import HistoricalFeatureExtractor
        return HistoricalFeatureExtractor
    elif name == 'FeaturePipeline':
        from .pipeline import FeaturePipeline
        return FeaturePipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'BaseFeatureExtractor',
    'FeatureSet',
    'StructuredFeatureExtractor',
    'TextFeatureExtractor',
    'HistoricalFeatureExtractor',
    'FeaturePipeline',
]
