"""
Model evaluation utilities.

Provides metrics computation, cross-validation, and report generation.
"""
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
from sklearn.model_selection import cross_val_score, StratifiedKFold, KFold
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, mean_absolute_error, mean_squared_error,
    r2_score, confusion_matrix, classification_report
)
from pathlib import Path
import json
from datetime import datetime

from ..models.base import BaseModel
from ..models.dismissal import DismissalModel
from ..models.value import ValueModel
from ..models.resolution import ResolutionModel
from ..models.duration import DurationModel


def evaluate_classifier(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_proba: Optional[np.ndarray] = None,
    labels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Evaluate a classification model.

    Args:
        y_true: True labels
        y_pred: Predicted labels
        y_proba: Predicted probabilities (optional)
        labels: Class labels (optional)

    Returns:
        Dict of evaluation metrics
    """
    metrics = {
        'accuracy': float(accuracy_score(y_true, y_pred)),
        'precision_macro': float(precision_score(y_true, y_pred, average='macro', zero_division=0)),
        'recall_macro': float(recall_score(y_true, y_pred, average='macro', zero_division=0)),
        'f1_macro': float(f1_score(y_true, y_pred, average='macro', zero_division=0)),
    }

    # AUC for binary classification
    if y_proba is not None:
        if len(y_proba.shape) == 1 or y_proba.shape[1] == 2:
            # Binary
            proba = y_proba if len(y_proba.shape) == 1 else y_proba[:, 1]
            try:
                metrics['auc'] = float(roc_auc_score(y_true, proba))
            except ValueError:
                metrics['auc'] = None
        else:
            # Multiclass
            try:
                metrics['auc_ovr'] = float(roc_auc_score(
                    y_true, y_proba, multi_class='ovr', average='macro'
                ))
            except ValueError:
                metrics['auc_ovr'] = None

    # Confusion matrix
    cm = confusion_matrix(y_true, y_pred)
    metrics['confusion_matrix'] = cm.tolist()

    # Per-class metrics
    if labels:
        report = classification_report(y_true, y_pred, target_names=labels, output_dict=True, zero_division=0)
        metrics['per_class'] = {k: v for k, v in report.items() if k in labels}

    return metrics


def evaluate_regressor(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_pred_low: Optional[np.ndarray] = None,
    y_pred_high: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """
    Evaluate a regression model.

    Args:
        y_true: True values
        y_pred: Predicted values
        y_pred_low: Lower bound predictions (optional)
        y_pred_high: Upper bound predictions (optional)

    Returns:
        Dict of evaluation metrics
    """
    metrics = {
        'mae': float(mean_absolute_error(y_true, y_pred)),
        'rmse': float(np.sqrt(mean_squared_error(y_true, y_pred))),
        'r2': float(r2_score(y_true, y_pred)),
    }

    # MAPE (avoid division by zero)
    nonzero_mask = y_true != 0
    if nonzero_mask.any():
        mape = np.mean(np.abs((y_true[nonzero_mask] - y_pred[nonzero_mask]) / y_true[nonzero_mask]))
        metrics['mape'] = float(mape)
    else:
        metrics['mape'] = None

    # Median absolute error (more robust)
    metrics['median_ae'] = float(np.median(np.abs(y_true - y_pred)))

    # Coverage for quantile predictions
    if y_pred_low is not None and y_pred_high is not None:
        coverage = np.mean((y_true >= y_pred_low) & (y_true <= y_pred_high))
        metrics['interval_coverage'] = float(coverage)
        metrics['interval_width_mean'] = float(np.mean(y_pred_high - y_pred_low))

    return metrics


def cross_validate_model(
    model: BaseModel,
    X: np.ndarray,
    y: np.ndarray,
    cv: int = 5,
    scoring: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cross-validate a model.

    Args:
        model: Model to evaluate
        X: Feature matrix
        y: Target values
        cv: Number of folds
        scoring: Scoring metric

    Returns:
        Dict with CV results
    """
    # Determine model type and scoring
    if isinstance(model, (DismissalModel, ResolutionModel)):
        if scoring is None:
            scoring = 'accuracy' if isinstance(model, ResolutionModel) else 'roc_auc'
        kfold = StratifiedKFold(n_splits=cv, shuffle=True, random_state=42)
    else:
        if scoring is None:
            scoring = 'neg_mean_absolute_error'
        kfold = KFold(n_splits=cv, shuffle=True, random_state=42)

    # Cross-validate
    if model.model is not None:
        scores = cross_val_score(model.model, X, y, cv=kfold, scoring=scoring)
    else:
        # Model not trained yet - return empty
        return {'error': 'Model not trained'}

    # Compute statistics
    results = {
        'cv_folds': cv,
        'scoring': scoring,
        'scores': scores.tolist(),
        'mean': float(scores.mean()),
        'std': float(scores.std()),
        'min': float(scores.min()),
        'max': float(scores.max()),
    }

    return results


def evaluate_model(
    model: BaseModel,
    X_test: np.ndarray,
    y_test: np.ndarray,
    feature_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Evaluate a trained model on test data.

    Args:
        model: Trained model
        X_test: Test features
        y_test: Test targets
        feature_names: Feature names

    Returns:
        Dict of evaluation results
    """
    if not model.is_fitted():
        return {'error': 'Model not trained'}

    results = {
        'model_name': model.name,
        'test_samples': len(y_test),
        'evaluated_at': datetime.now().isoformat(),
    }

    if isinstance(model, DismissalModel):
        # Binary classification
        y_pred = model.predict_class(X_test)
        y_proba = model.predict(X_test)
        metrics = evaluate_classifier(y_test, y_pred, y_proba)
        results['metrics'] = metrics
        results['threshold'] = 0.5

    elif isinstance(model, ResolutionModel):
        # Multiclass classification
        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)
        metrics = evaluate_classifier(y_test, y_pred, y_proba, labels=model.get_class_names())
        results['metrics'] = metrics
        results['classes'] = model.get_class_names()

    elif isinstance(model, ValueModel):
        # Quantile regression
        low, mid, high = model.predict_range(X_test)
        metrics = evaluate_regressor(y_test, mid, low, high)
        results['metrics'] = metrics

    elif isinstance(model, DurationModel):
        # Quantile regression
        low, mid, high = model.predict_range(X_test)
        metrics = evaluate_regressor(y_test, mid, low, high)
        results['metrics'] = metrics

    else:
        # Generic
        y_pred = model.predict(X_test)
        if y_test.dtype in [np.float32, np.float64, float]:
            metrics = evaluate_regressor(y_test, y_pred)
        else:
            metrics = evaluate_classifier(y_test, y_pred)
        results['metrics'] = metrics

    # Feature importance
    if feature_names:
        top_features = model.get_top_features(10)
        results['top_features'] = [
            {'name': name, 'importance': float(imp)}
            for name, imp in top_features
        ]

    return results


def generate_report(
    models: Dict[str, BaseModel],
    X_test: np.ndarray,
    y_test: Dict[str, np.ndarray],
    feature_names: Optional[List[str]] = None,
    output_path: Optional[Path] = None,
) -> str:
    """
    Generate evaluation report for multiple models.

    Args:
        models: Dict of model_name: model
        X_test: Test features
        y_test: Dict of model_name: test_targets
        feature_names: Feature names
        output_path: Path to save JSON report

    Returns:
        Formatted report string
    """
    lines = [
        "=" * 60,
        "MODEL EVALUATION REPORT",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
        "",
    ]

    report_data = {
        'generated_at': datetime.now().isoformat(),
        'models': {},
    }

    for model_name, model in models.items():
        if model_name not in y_test:
            continue

        results = evaluate_model(
            model, X_test, y_test[model_name],
            feature_names=feature_names
        )

        report_data['models'][model_name] = results

        lines.append(f"## {model_name.upper()} MODEL")
        lines.append("-" * 40)

        if 'error' in results:
            lines.append(f"Error: {results['error']}")
        else:
            metrics = results.get('metrics', {})

            if isinstance(model, DismissalModel):
                lines.append(f"AUC:      {metrics.get('auc', 'N/A'):.3f}" if metrics.get('auc') else "AUC: N/A")
                lines.append(f"Accuracy: {metrics.get('accuracy', 0):.1%}")
                lines.append(f"F1:       {metrics.get('f1_macro', 0):.3f}")

            elif isinstance(model, ResolutionModel):
                lines.append(f"Accuracy: {metrics.get('accuracy', 0):.1%}")
                lines.append(f"F1:       {metrics.get('f1_macro', 0):.3f}")
                if 'per_class' in metrics:
                    lines.append("\nPer-class F1:")
                    for cls, cls_metrics in metrics.get('per_class', {}).items():
                        lines.append(f"  {cls}: {cls_metrics.get('f1-score', 0):.3f}")

            elif isinstance(model, (ValueModel, DurationModel)):
                lines.append(f"MAE:      {metrics.get('mae', 0):,.0f}")
                lines.append(f"MAPE:     {metrics.get('mape', 0):.1%}" if metrics.get('mape') else "MAPE: N/A")
                lines.append(f"R2:       {metrics.get('r2', 0):.3f}")
                if 'interval_coverage' in metrics:
                    lines.append(f"Coverage: {metrics.get('interval_coverage', 0):.1%}")

            # Top features
            if 'top_features' in results:
                lines.append("\nTop Features:")
                for feat in results['top_features'][:5]:
                    lines.append(f"  {feat['name']}: {feat['importance']:.3f}")

        lines.append("")

    report_text = "\n".join(lines)

    # Save JSON report
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(report_data, f, indent=2)

    return report_text
