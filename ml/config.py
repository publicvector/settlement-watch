"""
ML Configuration and Hyperparameters.

Centralizes all ML-related configuration including model hyperparameters,
file paths, feature definitions, and training settings.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import json


# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MODELS_DIR = DATA_DIR / "models"
DB_PATH = PROJECT_ROOT / "db" / "settlement_watch.db"

# Current model version
MODEL_VERSION = "v1.0.0"
CURRENT_MODEL_DIR = MODELS_DIR / MODEL_VERSION


@dataclass
class DismissalModelConfig:
    """Configuration for the dismissal probability model."""
    # Model type
    model_type: str = "GradientBoostingClassifier"

    # Hyperparameters
    n_estimators: int = 100
    learning_rate: float = 0.1
    max_depth: int = 5
    min_samples_split: int = 10
    min_samples_leaf: int = 5
    subsample: float = 0.8

    # Calibration
    calibrate: bool = True
    calibration_method: str = "isotonic"  # or "sigmoid"

    # Feature importance threshold
    feature_importance_threshold: float = 0.01


@dataclass
class ValueModelConfig:
    """Configuration for the settlement value model."""
    # Model type - using quantile regression
    model_type: str = "GradientBoostingRegressor"

    # Hyperparameters
    n_estimators: int = 150
    learning_rate: float = 0.1
    max_depth: int = 6
    min_samples_split: int = 10
    min_samples_leaf: int = 5
    subsample: float = 0.8

    # Quantiles to predict
    quantiles: List[float] = field(default_factory=lambda: [0.25, 0.50, 0.75])

    # Log-transform target (settlement amounts are log-normal)
    log_transform: bool = True


@dataclass
class ResolutionModelConfig:
    """Configuration for the resolution path classifier."""
    # Model type
    model_type: str = "RandomForestClassifier"

    # Hyperparameters
    n_estimators: int = 100
    max_depth: int = 10
    min_samples_split: int = 10
    min_samples_leaf: int = 5
    class_weight: str = "balanced"

    # Target classes
    classes: List[str] = field(default_factory=lambda: [
        "dismissal", "settlement", "judgment", "trial"
    ])


@dataclass
class DurationModelConfig:
    """Configuration for the duration prediction model."""
    # Model type - quantile regression
    model_type: str = "GradientBoostingRegressor"

    # Hyperparameters
    n_estimators: int = 100
    learning_rate: float = 0.1
    max_depth: int = 5
    min_samples_split: int = 10
    min_samples_leaf: int = 5

    # Quantiles for confidence intervals
    quantiles: List[float] = field(default_factory=lambda: [0.25, 0.50, 0.75])

    # Minimum/maximum duration bounds (days)
    min_duration: int = 30
    max_duration: int = 3650  # 10 years


@dataclass
class FeatureConfig:
    """Configuration for feature extraction."""

    # Structured features
    court_codes: List[str] = field(default_factory=lambda: [
        "cacd", "caed", "cand", "casd",  # California
        "nysd", "nyed", "nynd",  # New York
        "txsd", "txed", "txnd", "txwd",  # Texas
        "ilnd", "flsd", "paed", "njd", "dcd", "mad"
    ])

    # Nature of suit code mappings (3-digit codes)
    nos_categories: Dict[str, str] = field(default_factory=lambda: {
        "110": "insurance",
        "120": "marine",
        "130": "miller_act",
        "140": "negotiable_instrument",
        "150": "recovery_overpayment",
        "160": "stockholders_suits",
        "190": "contract_other",
        "210": "land_condemnation",
        "220": "foreclosure",
        "230": "rent_lease",
        "240": "torts_to_land",
        "245": "tort_product_liability",
        "290": "real_property_other",
        "310": "airplane",
        "315": "airplane_product_liability",
        "320": "assault_libel_slander",
        "330": "fed_employers_liability",
        "340": "marine",
        "345": "marine_product_liability",
        "350": "motor_vehicle",
        "355": "motor_vehicle_product_liability",
        "360": "personal_injury_other",
        "362": "medical_malpractice",
        "365": "product_liability",
        "367": "health_care",
        "368": "asbestos",
        "370": "fraud",
        "371": "truth_in_lending",
        "375": "false_claims",
        "380": "personal_property_other",
        "385": "property_damage",
        "422": "bankruptcy_appeal",
        "423": "bankruptcy_withdrawal",
        "440": "civil_rights_other",
        "441": "civil_rights_voting",
        "442": "civil_rights_employment",
        "443": "civil_rights_housing",
        "444": "civil_rights_welfare",
        "445": "civil_rights_ada_employment",
        "446": "civil_rights_ada_other",
        "448": "civil_rights_education",
        "462": "deportation",
        "463": "habeas_alien",
        "465": "immigration_other",
        "470": "rico",
        "480": "consumer_credit",
        "490": "cable_sat_tv",
        "510": "prisoner_vacate",
        "530": "prisoner_habeas",
        "535": "prisoner_death_penalty",
        "540": "prisoner_mandamus",
        "550": "prisoner_civil_rights",
        "555": "prisoner_condition",
        "560": "prisoner_other",
        "610": "agricultural",
        "620": "food_drug",
        "625": "drug_related",
        "630": "liquor",
        "640": "railroad",
        "650": "airline",
        "660": "occupational_safety",
        "690": "labor_other",
        "710": "fair_labor",
        "720": "labor_management",
        "730": "labor_reporting",
        "740": "railway_labor",
        "751": "family_medical_leave",
        "790": "employment_other",
        "791": "employee_retirement",
        "810": "selective_service",
        "820": "copyright",
        "830": "patent",
        "835": "patent_abbrev",
        "840": "trademark",
        "850": "securities",
        "860": "social_security",
        "861": "hra",
        "862": "black_lung",
        "863": "diwc",
        "864": "ssid",
        "865": "rsi",
        "870": "tax",
        "871": "irs_third_party",
        "875": "customer_challenge",
        "890": "statutory_other",
        "891": "agricultural_acts",
        "892": "economic_stabilization",
        "893": "environmental",
        "894": "energy_allocation",
        "895": "freedom_of_information",
        "896": "arbitration",
        "899": "administrative_procedure",
        "900": "constitutionality_state_statute",
        "910": "forfeiture_penalty",
        "920": "bankrupt",
        "930": "abstention",
        "940": "land_matters",
        "950": "constitutionality_state_statute",
        "990": "other_statutory_actions",
    })

    # Defendant type keywords for classification
    defendant_type_keywords: Dict[str, List[str]] = field(default_factory=lambda: {
        "fortune_500": [
            "apple", "google", "microsoft", "amazon", "meta", "facebook",
            "walmart", "johnson & johnson", "pfizer", "unitedhealth",
            "exxon", "chevron", "berkshire", "jpmorgan", "bank of america",
            "wells fargo", "citigroup", "verizon", "at&t", "comcast",
            "disney", "netflix", "tesla", "nvidia", "intel", "ibm",
            "coca-cola", "pepsico", "procter", "p&g", "merck", "abbvie"
        ],
        "large_corp": [
            "inc", "incorporated", "corporation", "corp", "llc", "ltd",
            "company", "co.", "enterprises", "group", "holdings"
        ],
        "government": [
            "united states", "state of", "county of", "city of",
            "department", "agency", "commission", "board", "authority"
        ],
        "healthcare": [
            "hospital", "medical", "health", "clinic", "pharmacy",
            "healthcare", "therapeutics", "pharmaceutical"
        ],
        "financial": [
            "bank", "credit", "financial", "insurance", "capital",
            "investment", "securities", "asset", "mortgage", "lending"
        ]
    })


@dataclass
class TrainingConfig:
    """Configuration for model training."""
    # Cross-validation
    cv_folds: int = 5
    stratify: bool = True

    # Train/test split
    test_size: float = 0.2
    random_state: int = 42

    # Early stopping (for iterative models)
    early_stopping_rounds: int = 10

    # Minimum samples required
    min_samples_train: int = 50

    # Target metrics
    target_auc: float = 0.70
    target_mape: float = 0.50


@dataclass
class MLConfig:
    """Master configuration for the ML system."""
    # Model configs
    dismissal: DismissalModelConfig = field(default_factory=DismissalModelConfig)
    value: ValueModelConfig = field(default_factory=ValueModelConfig)
    resolution: ResolutionModelConfig = field(default_factory=ResolutionModelConfig)
    duration: DurationModelConfig = field(default_factory=DurationModelConfig)

    # Feature config
    features: FeatureConfig = field(default_factory=FeatureConfig)

    # Training config
    training: TrainingConfig = field(default_factory=TrainingConfig)

    # Paths
    model_version: str = MODEL_VERSION
    models_dir: Path = CURRENT_MODEL_DIR
    db_path: Path = DB_PATH

    def to_dict(self) -> dict:
        """Convert config to dictionary."""
        return {
            "model_version": self.model_version,
            "dismissal": self.dismissal.__dict__,
            "value": self.value.__dict__,
            "resolution": self.resolution.__dict__,
            "duration": self.duration.__dict__,
            "training": self.training.__dict__,
        }

    def save(self, path: Optional[Path] = None):
        """Save configuration to JSON file."""
        if path is None:
            path = self.models_dir / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2, default=str)

    @classmethod
    def load(cls, path: Path) -> 'MLConfig':
        """Load configuration from JSON file."""
        with open(path, 'r') as f:
            data = json.load(f)

        config = cls()
        config.model_version = data.get("model_version", MODEL_VERSION)
        # Update nested configs from data
        for key, value in data.get("dismissal", {}).items():
            if hasattr(config.dismissal, key):
                setattr(config.dismissal, key, value)
        for key, value in data.get("value", {}).items():
            if hasattr(config.value, key):
                setattr(config.value, key, value)
        for key, value in data.get("resolution", {}).items():
            if hasattr(config.resolution, key):
                setattr(config.resolution, key, value)
        for key, value in data.get("duration", {}).items():
            if hasattr(config.duration, key):
                setattr(config.duration, key, value)
        for key, value in data.get("training", {}).items():
            if hasattr(config.training, key):
                setattr(config.training, key, value)
        return config


# Default configuration instance
default_config = MLConfig()
