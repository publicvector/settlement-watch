#!/usr/bin/env python3
"""
Convert sklearn models to ONNX format for lightweight deployment.

ONNX models can be run with onnxruntime (~60MB) instead of
scikit-learn (~150MB+), making them suitable for Vercel deployment.
"""
import sys
from pathlib import Path
import numpy as np
import joblib

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType


def convert_model(model_path: Path, output_path: Path, n_features: int):
    """Convert a sklearn model to ONNX format."""
    print(f"Converting {model_path.name}...")

    # Load the model
    model_data = joblib.load(model_path)

    # Handle different model structures
    if isinstance(model_data, dict):
        # Models like dismissal have nested structure
        if 'base_model' in model_data:
            model = model_data['base_model']
        elif 'quantile_models' in model_data:
            # Quantile models - convert each separately
            for quantile, qmodel in model_data['quantile_models'].items():
                q_output = output_path.parent / f"{output_path.stem}_q{int(quantile*100)}.onnx"
                _convert_single_model(qmodel, q_output, n_features)
            return
        elif 'model' in model_data:
            model = model_data['model']
        else:
            print(f"  Unknown model structure: {list(model_data.keys())}")
            return
    else:
        model = model_data

    _convert_single_model(model, output_path, n_features)


def _convert_single_model(model, output_path: Path, n_features: int):
    """Convert a single sklearn model to ONNX."""
    # Define input type
    initial_type = [('float_input', FloatTensorType([None, n_features]))]

    try:
        # Convert to ONNX
        onnx_model = convert_sklearn(model, initial_types=initial_type)

        # Save
        with open(output_path, 'wb') as f:
            f.write(onnx_model.SerializeToString())

        # Check size
        size_kb = output_path.stat().st_size / 1024
        print(f"  Saved to {output_path.name} ({size_kb:.1f} KB)")

    except Exception as e:
        print(f"  Error converting: {e}")


def main():
    from ml.config import CURRENT_MODEL_DIR

    model_dir = CURRENT_MODEL_DIR
    onnx_dir = model_dir / "onnx"
    onnx_dir.mkdir(exist_ok=True)

    # Number of features (from training)
    n_features = 18

    # Convert each model
    models = ['dismissal', 'value', 'resolution', 'duration']

    for model_name in models:
        model_path = model_dir / f"{model_name}.pkl"
        if model_path.exists():
            output_path = onnx_dir / f"{model_name}.onnx"
            convert_model(model_path, output_path, n_features)
        else:
            print(f"Model not found: {model_path}")

    print(f"\nONNX models saved to {onnx_dir}")

    # Show sizes
    print("\nModel sizes:")
    for f in onnx_dir.glob("*.onnx"):
        size_kb = f.stat().st_size / 1024
        print(f"  {f.name}: {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
