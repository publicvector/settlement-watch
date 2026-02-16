"""
ML Models for legal case prediction.

Provides specialized models for:
- Dismissal probability (motion to dismiss)
- Settlement value estimation
- Resolution path classification
- Duration prediction
"""
from .base import BaseModel, ModelMetadata
from .dismissal import DismissalModel
from .value import ValueModel
from .resolution import ResolutionModel
from .duration import DurationModel

__all__ = [
    'BaseModel',
    'ModelMetadata',
    'DismissalModel',
    'ValueModel',
    'ResolutionModel',
    'DurationModel',
]
