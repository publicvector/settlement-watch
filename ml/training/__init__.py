"""
Training module for ML models.

Provides:
- ModelTrainer: Training orchestration
- evaluate: Model evaluation utilities
"""
from .trainer import ModelTrainer
from .evaluate import evaluate_model, cross_validate_model, generate_report

__all__ = [
    'ModelTrainer',
    'evaluate_model',
    'cross_validate_model',
    'generate_report',
]
