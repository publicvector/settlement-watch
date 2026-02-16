#!/usr/bin/env python3
"""
Predictive Legal Analytics CLI Tool.

Provides command-line interface for:
- Case dismissal probability prediction
- Settlement value estimation
- Resolution path prediction
- Duration estimation
- Model training and evaluation

Usage:
    # Predict dismissal probability
    python predict_case.py dismissal --court cacd --nos 442 --judge "John Walter"

    # Predict case value
    python predict_case.py value --court nysd --nos securities --defendant "Bank of America" --class-action

    # Full case analysis
    python predict_case.py analyze --court cacd --nos 442 --defendant "Tech Corp"

    # Train models
    python predict_case.py train --all

    # Evaluate models
    python predict_case.py evaluate --model dismissal
"""
import argparse
import sys
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from ml.config import MLConfig, CURRENT_MODEL_DIR, MODEL_VERSION

# Lazy imports for ML components (require numpy/sklearn)
CasePredictor = None
ModelTrainer = None

def get_predictor():
    global CasePredictor
    if CasePredictor is None:
        from ml.inference.predictor import CasePredictor as _CasePredictor
        CasePredictor = _CasePredictor
    return CasePredictor()

def get_trainer():
    global ModelTrainer
    if ModelTrainer is None:
        from ml.training.trainer import ModelTrainer as _ModelTrainer
        ModelTrainer = _ModelTrainer
    return ModelTrainer()


def cmd_dismissal(args):
    """Predict dismissal probability."""
    predictor = get_predictor()

    if not predictor.is_available().get('dismissal'):
        print("Error: Dismissal model not trained. Run 'python predict_case.py train --all' first.")
        return 1

    result = predictor.predict_dismissal(
        court=args.court,
        nos=args.nos,
        defendant=args.defendant,
        judge=args.judge,
        class_action=args.class_action,
    )

    if 'error' in result:
        print(f"Error: {result['error']}")
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        prob = result['probability']
        ci = result['confidence_interval_95']
        print(f"\nDismissal Probability: {prob:.1%}")
        print(f"95% Confidence Interval: [{ci[0]:.1%}, {ci[1]:.1%}]")

        if result.get('key_factors'):
            print("\nKey Factors:")
            for factor in result['key_factors'][:5]:
                print(f"  - {factor}")

    return 0


def cmd_value(args):
    """Predict settlement value."""
    predictor = get_predictor()

    if not predictor.is_available().get('value'):
        print("Error: Value model not trained. Run 'python predict_case.py train --all' first.")
        return 1

    result = predictor.predict_value(
        court=args.court,
        nos=args.nos,
        defendant=args.defendant,
        class_action=args.class_action,
    )

    if 'error' in result:
        print(f"Error: {result['error']}")
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        def fmt(v):
            if v >= 1e9:
                return f"${v/1e9:.1f}B"
            elif v >= 1e6:
                return f"${v/1e6:.1f}M"
            elif v >= 1e3:
                return f"${v/1e3:.1f}K"
            return f"${v:,.0f}"

        print(f"\nSettlement Value Estimate:")
        print(f"  Conservative (P25): {fmt(result['low'])}")
        print(f"  Expected (P50):     {fmt(result['mid'])}")
        print(f"  Aggressive (P75):   {fmt(result['high'])}")

        if result.get('key_factors'):
            print("\nKey Factors:")
            for factor in result['key_factors'][:5]:
                print(f"  - {factor}")

    return 0


def cmd_resolution(args):
    """Predict resolution path."""
    predictor = get_predictor()

    if not predictor.is_available().get('resolution'):
        print("Error: Resolution model not trained. Run 'python predict_case.py train --all' first.")
        return 1

    result = predictor.predict_resolution(
        court=args.court,
        nos=args.nos,
        defendant=args.defendant,
        class_action=args.class_action,
    )

    if 'error' in result:
        print(f"Error: {result['error']}")
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\nPredicted Outcome: {result['predicted_outcome'].upper()}")
        print(f"Confidence: {result['confidence']:.1%}")

        if result.get('probabilities'):
            print("\nOutcome Probabilities:")
            for outcome, prob in sorted(result['probabilities'].items(), key=lambda x: -x[1]):
                print(f"  {outcome}: {prob:.1%}")

    return 0


def cmd_duration(args):
    """Predict case duration."""
    predictor = get_predictor()

    if not predictor.is_available().get('duration'):
        print("Error: Duration model not trained. Run 'python predict_case.py train --all' first.")
        return 1

    result = predictor.predict_duration(
        court=args.court,
        nos=args.nos,
        defendant=args.defendant,
    )

    if 'error' in result:
        print(f"Error: {result['error']}")
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        def fmt_days(d):
            if d >= 365:
                return f"{d/365:.1f} years ({int(d)} days)"
            elif d >= 30:
                return f"{d/30:.1f} months ({int(d)} days)"
            return f"{int(d)} days"

        print(f"\nPredicted Duration:")
        print(f"  Fast (P25):   {fmt_days(result['low'])}")
        print(f"  Expected:     {fmt_days(result['mid'])}")
        print(f"  Slow (P75):   {fmt_days(result['high'])}")

    return 0


def cmd_analyze(args):
    """Full case analysis."""
    predictor = get_predictor()

    available = predictor.is_available()
    if not any(available.values()):
        print("Error: No models trained. Run 'python predict_case.py train --all' first.")
        return 1

    # Read complaint if provided
    complaint_text = None
    if args.complaint:
        complaint_path = Path(args.complaint)
        if complaint_path.exists():
            with open(complaint_path, 'r', encoding='utf-8', errors='ignore') as f:
                complaint_text = f.read()
        else:
            print(f"Warning: Complaint file not found: {args.complaint}")

    result = predictor.predict(
        court=args.court,
        nos=args.nos,
        defendant=args.defendant,
        class_action=args.class_action,
        pro_se=args.pro_se,
        mdl=args.mdl,
        complaint_text=complaint_text,
        judge=args.judge,
    )

    if args.json:
        print(result.to_json())
    else:
        print(result.summary())

    return 0


def cmd_train(args):
    """Train ML models."""
    print(f"Training models...")
    print(f"Output directory: {CURRENT_MODEL_DIR}")
    print()

    trainer = get_trainer()

    if args.all:
        trainer.train_all(save=True)
    else:
        df = trainer.load_training_data()
        print(f"Loaded {len(df)} training cases\n")

        if args.model == 'dismissal':
            trainer.train_dismissal_model(df, save=True)
        elif args.model == 'value':
            trainer.train_value_model(df, save=True)
        elif args.model == 'resolution':
            trainer.train_resolution_model(df, save=True)
        elif args.model == 'duration':
            trainer.train_duration_model(df, save=True)
        else:
            print(f"Unknown model: {args.model}")
            return 1

    print("\nTraining complete!")
    return 0


def cmd_evaluate(args):
    """Evaluate trained models."""
    import numpy as np
    from ml.training.evaluate import evaluate_model
    predictor = get_predictor()
    available = predictor.is_available()

    if args.all:
        models_to_eval = [k for k, v in available.items() if v]
    elif args.model:
        if not available.get(args.model):
            print(f"Error: Model '{args.model}' not trained.")
            return 1
        models_to_eval = [args.model]
    else:
        models_to_eval = [k for k, v in available.items() if v]

    if not models_to_eval:
        print("No models available for evaluation.")
        return 1

    print(f"Evaluating models: {', '.join(models_to_eval)}")
    print()

    # Load test data
    trainer = get_trainer()
    df = trainer.load_training_data()

    # Prepare features
    X, feature_names = trainer._prepare_features(df)

    # Evaluate each model
    for model_name in models_to_eval:
        print(f"## {model_name.upper()} MODEL")
        print("-" * 40)

        if model_name == 'dismissal':
            model = predictor._load_dismissal_model()
            y = trainer._prepare_dismissal_target(df)
        elif model_name == 'value':
            model = predictor._load_value_model()
            y = trainer._prepare_value_target(df)
        elif model_name == 'resolution':
            model = predictor._load_resolution_model()
            y = trainer._prepare_resolution_target(df)
        elif model_name == 'duration':
            model = predictor._load_duration_model()
            y = trainer._prepare_duration_target(df)
        else:
            continue

        # Filter valid samples
        valid_mask = ~np.isnan(X).any(axis=1)
        if hasattr(y, 'dtype') and y.dtype in [np.float32, np.float64]:
            valid_mask &= ~np.isnan(y) & (y > 0 if model_name in ['value', 'duration'] else True)

        X_valid = X[valid_mask]
        y_valid = y[valid_mask]

        results = evaluate_model(model, X_valid, y_valid, feature_names)

        if 'error' in results:
            print(f"  Error: {results['error']}")
        else:
            metrics = results.get('metrics', {})
            for key, value in metrics.items():
                if key not in ['confusion_matrix', 'per_class', 'class_distribution']:
                    if isinstance(value, float):
                        print(f"  {key}: {value:.4f}")
                    else:
                        print(f"  {key}: {value}")

        print()

    return 0


def cmd_status(args):
    """Check model status."""
    print(f"Model Directory: {CURRENT_MODEL_DIR}")
    print(f"Model Version: {MODEL_VERSION}")
    print()

    # Check model files directly without loading numpy/sklearn
    models = ['dismissal', 'value', 'resolution', 'duration']
    print("Model Status:")
    for model in models:
        model_path = CURRENT_MODEL_DIR / f"{model}.pkl"
        status_str = "READY" if model_path.exists() else "NOT TRAINED"
        print(f"  {model}: {status_str}")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Predictive Legal Analytics CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python predict_case.py analyze --court cacd --nos 442 --defendant "Tech Corp"
  python predict_case.py dismissal --court nysd --nos securities --judge "Smith"
  python predict_case.py value --court cacd --nos "data breach" --class-action
  python predict_case.py train --all
  python predict_case.py evaluate --all
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Common arguments
    def add_case_args(subparser):
        subparser.add_argument('--court', type=str, help='Court code (e.g., cacd, nysd)')
        subparser.add_argument('--nos', type=str, help='Nature of suit code or description')
        subparser.add_argument('--defendant', type=str, help='Defendant name')
        subparser.add_argument('--judge', type=str, help='Judge name')
        subparser.add_argument('--class-action', action='store_true', help='Is class action')
        subparser.add_argument('--json', action='store_true', help='Output as JSON')

    # Dismissal command
    dismissal_parser = subparsers.add_parser('dismissal', help='Predict dismissal probability')
    add_case_args(dismissal_parser)
    dismissal_parser.set_defaults(func=cmd_dismissal)

    # Value command
    value_parser = subparsers.add_parser('value', help='Predict settlement value')
    add_case_args(value_parser)
    value_parser.set_defaults(func=cmd_value)

    # Resolution command
    resolution_parser = subparsers.add_parser('resolution', help='Predict resolution path')
    add_case_args(resolution_parser)
    resolution_parser.set_defaults(func=cmd_resolution)

    # Duration command
    duration_parser = subparsers.add_parser('duration', help='Predict case duration')
    add_case_args(duration_parser)
    duration_parser.set_defaults(func=cmd_duration)

    # Analyze command
    analyze_parser = subparsers.add_parser('analyze', help='Full case analysis')
    add_case_args(analyze_parser)
    analyze_parser.add_argument('--complaint', type=str, help='Path to complaint document')
    analyze_parser.add_argument('--pro-se', action='store_true', help='Is pro se plaintiff')
    analyze_parser.add_argument('--mdl', action='store_true', help='Is part of MDL')
    analyze_parser.set_defaults(func=cmd_analyze)

    # Train command
    train_parser = subparsers.add_parser('train', help='Train ML models')
    train_parser.add_argument('--all', action='store_true', help='Train all models')
    train_parser.add_argument('--model', type=str, choices=['dismissal', 'value', 'resolution', 'duration'],
                              help='Specific model to train')
    train_parser.set_defaults(func=cmd_train)

    # Evaluate command
    eval_parser = subparsers.add_parser('evaluate', help='Evaluate trained models')
    eval_parser.add_argument('--all', action='store_true', help='Evaluate all models')
    eval_parser.add_argument('--model', type=str, choices=['dismissal', 'value', 'resolution', 'duration'],
                             help='Specific model to evaluate')
    eval_parser.set_defaults(func=cmd_evaluate)

    # Status command
    status_parser = subparsers.add_parser('status', help='Check model status')
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
