"""
Training orchestration for ML models.

Handles data loading, feature extraction, model training, and saving.
"""
from typing import Dict, List, Optional, Tuple, Any
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
import json

from ..config import MLConfig, default_config, CURRENT_MODEL_DIR
from ..features.pipeline import FeaturePipeline
from ..models.dismissal import DismissalModel
from ..models.value import ValueModel
from ..models.resolution import ResolutionModel
from ..models.duration import DurationModel


class ModelTrainer:
    """
    Orchestrates training of all ML models.

    Loads data from the database, extracts features, trains models,
    and saves artifacts to the models directory.
    """

    def __init__(
        self,
        config: Optional[MLConfig] = None,
        db_path: Optional[Path] = None,
        output_dir: Optional[Path] = None,
    ):
        """
        Initialize the trainer.

        Args:
            config: ML configuration
            db_path: Path to database
            output_dir: Directory to save models
        """
        self.config = config or default_config
        self.db_path = db_path or self.config.db_path
        self.output_dir = output_dir or CURRENT_MODEL_DIR

        # Pipeline matches training config (no text features from training data)
        self.pipeline = FeaturePipeline(
            config=self.config,
            include_text=False,
            include_historical=True,
        )
        self._data_cache: Optional[pd.DataFrame] = None

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def load_training_data(self) -> pd.DataFrame:
        """
        Load training data from database.

        Returns:
            DataFrame with case outcomes
        """
        if self._data_cache is not None:
            return self._data_cache

        conn = self._get_connection()

        # Load case outcomes
        query = """
            SELECT
                id,
                case_number,
                case_title,
                court,
                jurisdiction,
                nature_of_suit,
                case_type,
                complaint_date,
                defendant,
                plaintiff,
                estimated_class_size,
                settlement_date,
                settlement_amount,
                days_to_resolution,
                source
            FROM case_outcomes
            WHERE settlement_amount > 0
        """

        df = pd.read_sql_query(query, conn)
        conn.close()

        self._data_cache = df
        return df

    def _prepare_features(
        self,
        df: pd.DataFrame,
        include_text: bool = False
    ) -> Tuple[np.ndarray, List[str]]:
        """
        Prepare feature matrix from dataframe.

        Args:
            df: DataFrame with case data
            include_text: Whether to include text features

        Returns:
            Tuple of (feature_matrix, feature_names)
        """
        records = []
        for _, row in df.iterrows():
            class_size = row.get('estimated_class_size')
            record = {
                'court': row.get('court'),
                'nos': row.get('nature_of_suit') or row.get('case_type'),
                'defendant': row.get('defendant'),
                'class_action': bool(class_size and class_size > 0),
                'judge': None,  # Would need to join with docket entries
            }
            records.append(record)

        # Use the trainer's pipeline (consistent configuration)
        X = self.pipeline.extract_batch(records)
        feature_names = self.pipeline.get_feature_names()

        return X, feature_names

    def _prepare_dismissal_target(self, df: pd.DataFrame) -> np.ndarray:
        """
        Prepare target for dismissal model.

        Uses proxy based on settlement outcomes - cases with low settlement
        relative to median suggest dismissal or weak case.
        """
        # Calculate median settlement for comparison
        amounts = df['settlement_amount'].dropna()
        median_settlement = amounts.median() if len(amounts) > 0 else 1_000_000

        y = np.zeros(len(df))

        for idx, (i, row) in enumerate(df.iterrows()):
            days = row.get('days_to_resolution') or 0
            amount = row.get('settlement_amount') or 0

            # Cases likely to have been dismissed or settled very low:
            # - Quick resolution (< 6 months) with amount < 25% of median
            # - Or very low absolute amount (< $50k)
            quick_low_settlement = days < 180 and amount < median_settlement * 0.25
            very_low_settlement = amount < 50000

            if quick_low_settlement or very_low_settlement:
                y[idx] = 1  # Likely dismissed or very weak case

        return y

    def _prepare_value_target(self, df: pd.DataFrame) -> np.ndarray:
        """Prepare target for value model."""
        return df['settlement_amount'].values.astype(float)

    def _prepare_resolution_target(self, df: pd.DataFrame) -> np.ndarray:
        """
        Prepare target for resolution model.

        Creates resolution categories based on available data.
        """
        y = []

        for _, row in df.iterrows():
            amount = row.get('settlement_amount', 0) or 0
            days = row.get('days_to_resolution', 0) or 0

            # Classify based on heuristics
            if amount == 0 or (days < 90 and amount < 50000):
                y.append('dismissal')
            elif days < 365:
                y.append('settlement')
            elif days < 730:
                y.append('judgment')
            else:
                y.append('trial')

        return np.array(y)

    def _prepare_duration_target(self, df: pd.DataFrame) -> np.ndarray:
        """Prepare target for duration model."""
        # Check if we have actual duration data
        valid_durations = df['days_to_resolution'].dropna()
        valid_durations = valid_durations[valid_durations > 0]

        if len(valid_durations) > 10:
            # Use actual data, fill missing with median
            median_duration = valid_durations.median()
            durations = df['days_to_resolution'].fillna(median_duration)
            return durations.values.astype(float)
        else:
            # Synthesize duration based on settlement amount
            # Larger settlements tend to take longer
            amounts = df['settlement_amount'].fillna(0).values
            median_amount = np.median(amounts[amounts > 0]) if np.any(amounts > 0) else 1_000_000

            # Base duration of 365 days, scaled by log of amount ratio
            durations = np.zeros(len(df))
            for i, amount in enumerate(amounts):
                if amount > 0:
                    # Log scale: $1M = ~365 days, $100M = ~730 days, $1B = ~1095 days
                    log_ratio = np.log10(amount / 1_000_000 + 1)
                    durations[i] = 365 + log_ratio * 200
                else:
                    durations[i] = 180  # Quick resolution for $0

            return np.clip(durations, 30, 3650).astype(float)

    def train_dismissal_model(
        self,
        df: Optional[pd.DataFrame] = None,
        save: bool = True,
    ) -> DismissalModel:
        """
        Train the dismissal probability model.

        Args:
            df: Training data (loads from DB if not provided)
            save: Whether to save the trained model

        Returns:
            Trained DismissalModel
        """
        if df is None:
            df = self.load_training_data()

        print(f"Training dismissal model on {len(df)} cases...")

        # Prepare features and target
        X, feature_names = self._prepare_features(df)
        y = self._prepare_dismissal_target(df)

        # Filter out NaN values
        valid_mask = ~(np.isnan(X).any(axis=1) | np.isnan(y))
        X = X[valid_mask]
        y = y[valid_mask]

        print(f"  Valid samples: {len(y)}")
        print(f"  Positive rate: {y.mean():.1%}")

        # Train model
        model = DismissalModel(self.config.dismissal)
        model.fit(X, y, feature_names=feature_names)

        print(f"  CV AUC: {model.metadata.metrics.get('cv_auc_mean', 0):.3f}")

        # Save
        if save:
            model_path = self.output_dir / "dismissal.pkl"
            model.save(model_path)
            print(f"  Saved to {model_path}")

        return model

    def train_value_model(
        self,
        df: Optional[pd.DataFrame] = None,
        save: bool = True,
    ) -> ValueModel:
        """
        Train the settlement value model.

        Args:
            df: Training data
            save: Whether to save

        Returns:
            Trained ValueModel
        """
        if df is None:
            df = self.load_training_data()

        print(f"Training value model on {len(df)} cases...")

        # Prepare features and target
        X, feature_names = self._prepare_features(df)
        y = self._prepare_value_target(df)

        # Filter out invalid values
        valid_mask = ~(np.isnan(X).any(axis=1) | np.isnan(y) | (y <= 0))
        X = X[valid_mask]
        y = y[valid_mask]

        print(f"  Valid samples: {len(y)}")
        print(f"  Mean settlement: ${y.mean()/1e6:.1f}M")
        print(f"  Median settlement: ${np.median(y)/1e6:.1f}M")

        # Train model
        model = ValueModel(self.config.value)
        model.fit(X, y, feature_names=feature_names)

        print(f"  CV MAE: ${model.metadata.metrics.get('cv_mae_mean', 0)/1e6:.2f}M")

        # Save
        if save:
            model_path = self.output_dir / "value.pkl"
            model.save(model_path)
            print(f"  Saved to {model_path}")

        return model

    def train_resolution_model(
        self,
        df: Optional[pd.DataFrame] = None,
        save: bool = True,
    ) -> ResolutionModel:
        """
        Train the resolution path model.

        Args:
            df: Training data
            save: Whether to save

        Returns:
            Trained ResolutionModel
        """
        if df is None:
            df = self.load_training_data()

        print(f"Training resolution model on {len(df)} cases...")

        # Prepare features and target
        X, feature_names = self._prepare_features(df)
        y = self._prepare_resolution_target(df)

        # Filter out NaN features
        valid_mask = ~np.isnan(X).any(axis=1)
        X = X[valid_mask]
        y = y[valid_mask]

        print(f"  Valid samples: {len(y)}")

        # Class distribution
        unique, counts = np.unique(y, return_counts=True)
        for u, c in zip(unique, counts):
            print(f"    {u}: {c} ({c/len(y):.1%})")

        # Train model
        model = ResolutionModel(self.config.resolution)
        model.fit(X, y, feature_names=feature_names)

        print(f"  CV Accuracy: {model.metadata.metrics.get('cv_accuracy_mean', 0):.1%}")

        # Save
        if save:
            model_path = self.output_dir / "resolution.pkl"
            model.save(model_path)
            print(f"  Saved to {model_path}")

        return model

    def train_duration_model(
        self,
        df: Optional[pd.DataFrame] = None,
        save: bool = True,
    ) -> DurationModel:
        """
        Train the duration prediction model.

        Args:
            df: Training data
            save: Whether to save

        Returns:
            Trained DurationModel
        """
        if df is None:
            df = self.load_training_data()

        print(f"Training duration model on {len(df)} cases...")

        # Prepare features and target
        X, feature_names = self._prepare_features(df)
        y = self._prepare_duration_target(df)

        # Filter out invalid values - but y should be valid from synthesis
        valid_mask = ~np.isnan(X).any(axis=1)
        if not np.isnan(y).all():
            valid_mask &= ~np.isnan(y) & (y > 0)
        X = X[valid_mask]
        y = y[valid_mask]

        if len(y) == 0:
            print("  No valid samples for duration model. Skipping.")
            return None

        print(f"  Valid samples: {len(y)}")
        print(f"  Mean duration: {y.mean():.0f} days")
        print(f"  Median duration: {np.median(y):.0f} days")

        # Train model
        model = DurationModel(self.config.duration)
        model.fit(X, y, feature_names=feature_names)

        print(f"  CV MAE: {model.metadata.metrics.get('cv_mae_mean', 0):.0f} days")

        # Save
        if save:
            model_path = self.output_dir / "duration.pkl"
            model.save(model_path)
            print(f"  Saved to {model_path}")

        return model

    def train_all(
        self,
        save: bool = True,
    ) -> Dict[str, Any]:
        """
        Train all models.

        Args:
            save: Whether to save models

        Returns:
            Dict of model_name: model
        """
        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Load data once
        df = self.load_training_data()
        print(f"Loaded {len(df)} training cases\n")

        # Train all models
        models = {}

        models['dismissal'] = self.train_dismissal_model(df, save=save)
        print()

        models['value'] = self.train_value_model(df, save=save)
        print()

        models['resolution'] = self.train_resolution_model(df, save=save)
        print()

        duration_model = self.train_duration_model(df, save=save)
        if duration_model:
            models['duration'] = duration_model
        print()

        # Save feature pipeline
        if save:
            pipeline_path = self.output_dir / "pipeline"
            self.pipeline.save(pipeline_path)

            # Save config
            self.config.save(self.output_dir / "config.json")

            # Save metadata
            metadata = {
                'version': self.config.model_version,
                'trained_at': datetime.now().isoformat(),
                'training_samples': len(df),
                'models': list(models.keys()),
                'feature_count': self.pipeline.get_feature_count(),
            }
            with open(self.output_dir / "metadata.json", 'w') as f:
                json.dump(metadata, f, indent=2)

            print(f"All models saved to {self.output_dir}")

        return models
