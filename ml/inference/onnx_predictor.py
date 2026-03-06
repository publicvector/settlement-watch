"""
ONNX-based predictor for lightweight deployment.

Uses onnxruntime (~60MB) instead of scikit-learn (~150MB+)
for Vercel-compatible deployment.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from pathlib import Path
import json

import numpy as np

# Lazy import onnxruntime
_ort = None
def get_ort():
    global _ort
    if _ort is None:
        import onnxruntime as ort
        _ort = ort
    return _ort


@dataclass
class ONNXPredictionOutput:
    """Prediction output format with coherent multi-model predictions."""
    # Primary dismissal assessment
    dismissal_probability: float = 0.0
    dismissal_confidence: List[float] = field(default_factory=lambda: [0.0, 0.0])

    # Coherent outcome probabilities (must sum to 1.0)
    predicted_outcome: str = "unknown"
    outcome_probabilities: Dict[str, float] = field(default_factory=dict)

    # Value estimate (risk-adjusted)
    value_estimate: Dict[str, Any] = field(default_factory=lambda: {
        'low': 0, 'mid': 0, 'high': 0, 'if_survives': {'low': 0, 'mid': 0, 'high': 0}
    })

    # Duration estimate
    predicted_duration_days: Dict[str, float] = field(default_factory=lambda: {'low': 0, 'mid': 0, 'high': 0})

    # Confidence and metadata
    confidence: str = "medium"
    model_version: str = "v1.0.0"

    def to_dict(self) -> Dict:
        result = asdict(self)
        # Add derived fields for easier consumption
        result['survival_probability'] = round(1.0 - self.dismissal_probability, 4)
        result['expected_value'] = self.value_estimate.get('mid', 0)
        return result


class ONNXPredictor:
    """
    Lightweight predictor using ONNX models.
    """

    def __init__(self, model_dir: Optional[Path] = None):
        """Initialize with model directory."""
        if model_dir is None:
            from ..config import CURRENT_MODEL_DIR
            model_dir = CURRENT_MODEL_DIR

        self.model_dir = Path(model_dir)
        self.onnx_dir = self.model_dir / "onnx"

        # Lazy-loaded sessions
        self._sessions: Dict[str, Any] = {}

        # Load metadata
        self._load_metadata()

    def _load_metadata(self):
        """Load model metadata, encoder mappings, and historical stats."""
        metadata_path = self.model_dir / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path) as f:
                self.metadata = json.load(f)
        else:
            self.metadata = {"version": "v1.0.0", "feature_count": 18}

        # Load encoder mappings (exported from training pipeline)
        encoders_path = self.model_dir / "encoder_mappings.json"
        if encoders_path.exists():
            with open(encoders_path) as f:
                self.encoder_mappings = json.load(f)
        else:
            self.encoder_mappings = None

        # Load historical stats for realistic predictions
        stats_path = self.model_dir / "historical_stats.json"
        if stats_path.exists():
            with open(stats_path) as f:
                self.historical_stats = json.load(f)
        else:
            self.historical_stats = None

    def _get_session(self, model_name: str):
        """Get or create ONNX inference session."""
        if model_name not in self._sessions:
            model_path = self.onnx_dir / f"{model_name}.onnx"
            if model_path.exists():
                ort = get_ort()
                self._sessions[model_name] = ort.InferenceSession(
                    str(model_path),
                    providers=['CPUExecutionProvider']
                )
            else:
                return None
        return self._sessions[model_name]

    def _extract_features(
        self,
        court: Optional[str] = None,
        nos: Optional[str] = None,
        defendant: Optional[str] = None,
        class_action: bool = False,
        pro_se: bool = False,
        mdl: bool = False,
        **kwargs
    ) -> np.ndarray:
        """
        Extract features for prediction.

        Matches the training pipeline exactly:
        [0-6]  Structured features from encoders
        [7-17] Historical features from database stats
        """
        features = np.zeros(18, dtype=np.float32)

        # === STRUCTURED FEATURES (0-6) ===
        # These must match StructuredFeatureExtractor exactly

        # [0] court_encoded - normalize and encode
        court_normalized = self._normalize_court(court)
        if self.encoder_mappings:
            features[0] = float(self.encoder_mappings['court'].get(court_normalized,
                               self.encoder_mappings['court'].get('unknown', 0)))
        else:
            features[0] = 0.0

        # [1] nos_encoded - normalize to 3-digit code and encode
        nos_code, nos_category = self._normalize_nos(nos)
        if self.encoder_mappings:
            features[1] = float(self.encoder_mappings['nos'].get(nos_code,
                               self.encoder_mappings['nos'].get('unknown', 0)))
            # [2] nos_category_encoded
            features[2] = float(self.encoder_mappings['nos_category'].get(nos_category,
                               self.encoder_mappings['nos_category'].get('unknown', 0)))
        else:
            features[1] = 0.0
            features[2] = 0.0

        # [3] defendant_type_encoded
        defendant_type = self._classify_defendant_type(defendant)
        if self.encoder_mappings:
            features[3] = float(self.encoder_mappings['defendant_type'].get(defendant_type,
                               self.encoder_mappings['defendant_type'].get('unknown', 0)))
        else:
            features[3] = 0.0

        # [4-6] Binary features
        features[4] = 1.0 if class_action else 0.0
        features[5] = 1.0 if pro_se else 0.0
        features[6] = 1.0 if mdl else 0.0

        # === HISTORICAL FEATURES (7-17) ===
        # These match HistoricalFeatureExtractor
        court_lower = court_normalized
        national_median = 110_000_000.0

        if self.historical_stats:
            stats = self.historical_stats
            national_median = stats.get('national_median', national_median)

            # Court features [7-10]
            court_data = stats.get('court_stats', {}).get(court_lower, {})
            features[7] = court_data.get('multiplier', 1.0)  # jurisdiction_multiplier
            court_median = court_data.get('median_settlement', national_median)
            features[8] = np.log10(court_median + 1)  # court_median_settlement_log
            features[9] = float(court_data.get('case_count', 0))  # court_case_count
            features[10] = 0.3  # court_dismissal_rate (default)

            # Defendant features [11-13]
            defendant_data = self._find_defendant_stats(defendant)
            if defendant_data:
                features[11] = np.log10(defendant_data.get('total_paid', 0) + 1)
                features[12] = np.log10(defendant_data.get('avg_settlement', 0) + 1)
                features[13] = float(defendant_data.get('case_count', 0))
            else:
                features[11] = 0.0  # defendant_total_paid_log
                features[12] = 0.0  # defendant_avg_settlement_log
                features[13] = 0.0  # defendant_case_count

            # NOS features [14-15]
            nos_lower = (nos or '').lower()
            nos_data = stats.get('nos_stats', {}).get(nos_lower, {})
            if nos_data:
                features[14] = np.log10(nos_data.get('median_settlement', national_median) + 1)
                features[15] = float(nos_data.get('case_count', 0))
            else:
                features[14] = np.log10(national_median + 1)
                features[15] = 0.0

            # Judge features [16-17] - defaults since judge rarely provided
            features[16] = 0.5  # judge_mtd_rate (neutral default)
            features[17] = 0.0  # judge_case_count

        else:
            # Fallback defaults matching training distribution
            features[7] = 1.0   # jurisdiction_multiplier
            features[8] = np.log10(national_median + 1)  # ~8.04
            features[9] = 0.0
            features[10] = 0.3
            features[11] = 0.0
            features[12] = 0.0
            features[13] = 0.0
            features[14] = np.log10(national_median + 1)
            features[15] = 0.0
            features[16] = 0.5
            features[17] = 0.0

        return features.reshape(1, -1)

    def _normalize_court(self, court: Optional[str]) -> str:
        """Normalize court code to lowercase standard form."""
        if not court:
            return 'unknown'

        court_lower = court.lower().strip()

        # Common court code normalization
        court_mappings = {
            'c.d. cal.': 'cacd', 'c.d.cal.': 'cacd',
            'central district of california': 'cacd',
            'n.d. cal.': 'cand', 'n.d.cal.': 'cand',
            'northern district of california': 'cand',
            's.d. cal.': 'casd', 's.d.cal.': 'casd',
            'southern district of california': 'casd',
            'e.d. cal.': 'caed', 'e.d.cal.': 'caed',
            'eastern district of california': 'caed',
            's.d.n.y.': 'nysd', 's.d. n.y.': 'nysd',
            'southern district of new york': 'nysd',
            'e.d.n.y.': 'nyed', 'e.d. n.y.': 'nyed',
            'eastern district of new york': 'nyed',
            'n.d. tex.': 'txnd', 's.d. tex.': 'txsd',
            'e.d. tex.': 'txed', 'w.d. tex.': 'txwd',
            'n.d. ill.': 'ilnd', 's.d. fla.': 'flsd',
            'e.d. pa.': 'paed', 'd.n.j.': 'njd',
            'd.d.c.': 'dcd', 'd. mass.': 'mad',
        }

        if court_lower in court_mappings:
            return court_mappings[court_lower]

        # If already in standard form
        if self.encoder_mappings and court_lower in self.encoder_mappings.get('court', {}):
            return court_lower

        return 'unknown'

    def _normalize_nos(self, nos: Optional[str]) -> tuple:
        """Normalize nature of suit to code and category."""
        if not nos:
            return 'unknown', 'unknown'

        nos_str = str(nos).strip()

        # If it's a 3-digit code, use directly
        if nos_str.isdigit() and len(nos_str) == 3:
            # Map code to category using NOS categories
            nos_categories = {
                '110': 'insurance', '120': 'marine_contract', '130': 'miller_act',
                '140': 'negotiable_instrument', '150': 'overpayments_veterans',
                '190': 'contract_other', '210': 'land_condemnation', '220': 'foreclosure',
                '310': 'airplane', '315': 'airplane_product_liability',
                '320': 'assault_libel_slander', '330': 'fed_employers_liability',
                '340': 'marine', '345': 'marine_product_liability',
                '350': 'motor_vehicle', '355': 'motor_vehicle_product_liability',
                '360': 'personal_injury_other', '362': 'medical_malpractice',
                '365': 'product_liability', '367': 'health_care',
                '368': 'asbestos', '370': 'fraud', '371': 'truth_in_lending',
                '380': 'personal_property_other', '385': 'property_damage',
                '410': 'antitrust', '422': 'bankruptcy_appeals',
                '440': 'civil_rights_other', '441': 'voting', '442': 'jobs',
                '443': 'housing', '444': 'welfare', '445': 'ada_employment',
                '446': 'ada_other', '448': 'education',
                '480': 'consumer_credit', '490': 'cable_satellite_tv',
                '710': 'labor_fair_standards', '720': 'labor_mgmt_relations',
                '730': 'labor_railway', '740': 'labor_family_medical_leave',
                '751': 'erisa', '790': 'labor_other',
                '820': 'copyright', '830': 'patent', '840': 'trademark',
                '850': 'securities', '860': 'social_security',
                '890': 'statutory_other', '893': 'environmental',
                '895': 'freedom_of_info', '899': 'administrative_other',
                '950': 'constitutionality_state_statute',
            }
            category = nos_categories.get(nos_str, 'unknown')
            return nos_str, category

        # Map keyword to code
        nos_lower = nos_str.lower()
        keyword_to_code = {
            'securities': ('850', 'securities'),
            'antitrust': ('410', 'antitrust'),
            'employment': ('442', 'jobs'),
            'civil rights': ('440', 'civil_rights_other'),
            'product liability': ('365', 'product_liability'),
            'personal injury': ('360', 'personal_injury_other'),
            'fraud': ('370', 'fraud'),
            'contract': ('190', 'contract_other'),
            'patent': ('830', 'patent'),
            'trademark': ('840', 'trademark'),
            'copyright': ('820', 'copyright'),
            'environmental': ('893', 'environmental'),
            'labor': ('710', 'labor_fair_standards'),
            'erisa': ('751', 'erisa'),
            'data breach': ('370', 'fraud'),
            'privacy': ('370', 'fraud'),
            'consumer': ('480', 'consumer_credit'),
            'insurance': ('110', 'insurance'),
        }

        for keyword, (code, category) in keyword_to_code.items():
            if keyword in nos_lower:
                return code, category

        return 'unknown', 'unknown'

    def _classify_defendant_type(self, defendant: Optional[str]) -> str:
        """Classify defendant into a type based on name patterns."""
        if not defendant:
            return 'unknown'

        defendant_lower = defendant.lower()

        # Fortune 500 companies
        fortune_500 = [
            'google', 'apple', 'meta', 'amazon', 'microsoft', 'facebook',
            'walmart', 'johnson & johnson', 'pfizer', 'bank of america',
            'wells fargo', 'jpmorgan', 'goldman sachs', 'morgan stanley',
            'citibank', 'citigroup', 'verizon', 'at&t', 'exxon', 'chevron',
            'general motors', 'ford', 'boeing', 'united health', 'cvs',
            'anthem', 'aetna', 'comcast', 'disney', 'intel', 'ibm'
        ]
        if any(f in defendant_lower for f in fortune_500):
            return 'fortune_500'

        # Government entities
        government = ['united states', 'u.s.', 'federal', 'state of', 'city of',
                     'county of', 'government', 'agency', 'department', 'bureau']
        if any(g in defendant_lower for g in government):
            return 'government'

        # Healthcare
        healthcare = ['hospital', 'medical', 'healthcare', 'health care',
                     'clinic', 'pharma', 'pharmaceutical']
        if any(h in defendant_lower for h in healthcare):
            return 'healthcare'

        # Financial
        financial = ['bank', 'credit', 'financial', 'insurance', 'lending',
                    'mortgage', 'securities', 'investment', 'capital']
        if any(f in defendant_lower for f in financial):
            return 'financial'

        # Large corp indicators
        large_corp = ['inc', 'corp', 'corporation', 'llc', 'ltd', 'company',
                     'co.', 'enterprises', 'holdings', 'group']
        if any(l in defendant_lower for l in large_corp):
            return 'large_corp'

        return 'small_company'

    def _find_defendant_stats(self, defendant: Optional[str]) -> Optional[Dict]:
        """Find defendant in historical stats with fuzzy matching."""
        if not self.historical_stats or not defendant:
            return None

        defendant_lower = defendant.lower()
        defendant_stats = self.historical_stats.get('defendant_stats', {})

        # Exact match
        if defendant_lower in defendant_stats:
            return defendant_stats[defendant_lower]

        # Partial match - find defendants that contain search term or vice versa
        for db_name, stats in defendant_stats.items():
            if defendant_lower in db_name or db_name in defendant_lower:
                return stats

        return None

    def predict_dismissal(self, **kwargs) -> Dict[str, Any]:
        """Predict dismissal probability."""
        session = self._get_session("dismissal")
        if session is None:
            return {"error": "Dismissal model not available"}

        X = self._extract_features(**kwargs)

        # Run inference
        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: X})

        # Get probability - handle various ONNX output formats
        try:
            if len(output) > 1:
                probs = output[1]
                # ONNX classifiers return probabilities as 2D array: [[prob_0, prob_1]]
                if hasattr(probs, 'shape') and len(probs.shape) == 2:
                    prob = float(probs[0, 1]) if probs.shape[1] > 1 else float(probs[0, 0])
                elif hasattr(probs, '__len__') and len(probs) > 0:
                    first = probs[0]
                    if isinstance(first, dict):
                        prob = float(first.get(1, first.get('1', 0.5)))
                    elif hasattr(first, '__len__') and len(first) > 1:
                        prob = float(first[1])
                    else:
                        prob = float(first)
                else:
                    prob = 0.5
            else:
                prob = float(output[0].flat[0]) if hasattr(output[0], 'flat') else float(output[0][0])
        except Exception as e:
            return {"error": f"Error parsing ONNX output: {e}", "raw_output_types": [str(type(o)) for o in output]}

        return {
            "probability": prob,
            "confidence_interval_95": [max(0, prob - 0.1), min(1, prob + 0.1)],
        }

    def _extract_scalar(self, arr):
        """Safely extract a scalar from ONNX output."""
        if hasattr(arr, 'flat'):
            return float(arr.flat[0])
        elif hasattr(arr, '__len__'):
            if len(arr) > 0:
                first = arr[0]
                if hasattr(first, '__len__') and not isinstance(first, (str, bytes)):
                    return float(first[0]) if len(first) > 0 else 0.0
                return float(first)
            return 0.0
        return float(arr)

    def predict_value(self, **kwargs) -> Dict[str, Any]:
        """Predict settlement value range."""
        X = self._extract_features(**kwargs)

        results = {}
        for quantile, name in [(25, 'low'), (50, 'mid'), (75, 'high')]:
            session = self._get_session(f"value_q{quantile}")
            if session:
                input_name = session.get_inputs()[0].name
                output = session.run(None, {input_name: X})
                # Inverse log transform
                log_value = self._extract_scalar(output[0])
                results[name] = np.expm1(log_value)

        if not results:
            return {"error": "Value model not available"}

        return results

    def predict_resolution(self, **kwargs) -> Dict[str, Any]:
        """Predict resolution path."""
        session = self._get_session("resolution")
        if session is None:
            return {"error": "Resolution model not available"}

        X = self._extract_features(**kwargs)

        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: X})

        # Get class and probabilities
        classes = ['dismissal', 'settlement']  # Simplified
        try:
            pred_idx = int(self._extract_scalar(output[0]))
            if len(output) > 1:
                raw_probs = output[1]
                # Handle 2D array [[p0, p1, ...]]
                if hasattr(raw_probs, 'shape') and len(raw_probs.shape) == 2:
                    probs = [float(raw_probs[0, i]) for i in range(min(raw_probs.shape[1], len(classes)))]
                elif hasattr(raw_probs, '__len__') and len(raw_probs) > 0:
                    first = raw_probs[0]
                    if isinstance(first, dict):
                        probs = [float(first.get(i, first.get(str(i), 0.0))) for i in range(len(classes))]
                    elif hasattr(first, '__len__'):
                        probs = [float(first[i]) for i in range(min(len(first), len(classes)))]
                    else:
                        probs = [1.0, 0.0] if pred_idx == 0 else [0.0, 1.0]
                else:
                    probs = [1.0, 0.0] if pred_idx == 0 else [0.0, 1.0]
            else:
                probs = [1.0, 0.0] if pred_idx == 0 else [0.0, 1.0]
        except Exception as e:
            return {"error": f"Resolution prediction error: {e}"}

        return {
            "predicted_outcome": classes[pred_idx] if pred_idx < len(classes) else "settlement",
            "probabilities": {c: float(probs[i]) if i < len(probs) else 0.0 for i, c in enumerate(classes)},
        }

    def predict_duration(self, **kwargs) -> Dict[str, Any]:
        """Predict case duration."""
        X = self._extract_features(**kwargs)

        results = {}
        for quantile, name in [(25, 'low'), (50, 'mid'), (75, 'high')]:
            session = self._get_session(f"duration_q{quantile}")
            if session:
                input_name = session.get_inputs()[0].name
                output = session.run(None, {input_name: X})
                results[name] = self._extract_scalar(output[0])

        if not results:
            return {"error": "Duration model not available"}

        return results

    def predict(self, **kwargs) -> ONNXPredictionOutput:
        """
        Full prediction using coherent multi-model ensemble.

        Uses dismissal model as primary signal and derives other outcomes
        to ensure logical consistency across all predictions.
        """
        output = ONNXPredictionOutput(model_version=self.metadata.get("version", "v1.0.0"))

        # Extract case characteristics for conditioning
        court = (kwargs.get('court') or '').lower()
        nos = kwargs.get('nos') or ''
        defendant = (kwargs.get('defendant') or '').lower()
        class_action = kwargs.get('class_action', False)

        # === PRIMARY: Dismissal Probability ===
        dismissal = self.predict_dismissal(**kwargs)
        if "probability" in dismissal:
            dismissal_prob = dismissal["probability"]
            output.dismissal_probability = dismissal_prob
            output.dismissal_confidence = dismissal.get("confidence_interval_95", [0, 0])
        else:
            dismissal_prob = 0.3  # Default prior

        # === COHERENT OUTCOME PROBABILITIES ===
        # Derive from dismissal + case-specific conditional probabilities
        outcome_probs = self._compute_coherent_outcomes(
            dismissal_prob, court, nos, defendant, class_action
        )
        output.outcome_probabilities = outcome_probs

        # Predicted outcome is the most likely
        output.predicted_outcome = max(outcome_probs, key=outcome_probs.get)

        # === CONDITIONAL VALUE ESTIMATE ===
        # Only meaningful if case doesn't get dismissed
        if outcome_probs.get('settlement', 0) > 0.1:
            value = self.predict_value(**kwargs)
            if "error" not in value:
                # Adjust value based on dismissal risk (expected value)
                survival_prob = 1 - dismissal_prob
                output.value_estimate = {
                    'low': value['low'] * survival_prob,
                    'mid': value['mid'] * survival_prob,
                    'high': value['high'],  # Keep high as potential upside
                    'if_survives': value.copy()  # Raw values if case survives MTD
                }

        # === DURATION ===
        duration = self.predict_duration(**kwargs)
        if "error" not in duration:
            # Adjust duration based on likely outcome
            if dismissal_prob > 0.6:
                # High dismissal risk = shorter expected duration
                output.predicted_duration_days = {
                    'low': min(duration['low'], 180),
                    'mid': min(duration['mid'], 300),
                    'high': duration['mid']  # Capped if likely dismissed
                }
            else:
                output.predicted_duration_days = duration

        # === CONFIDENCE ASSESSMENT ===
        output.confidence = self._assess_confidence(kwargs, dismissal_prob, outcome_probs)

        return output

    def _compute_coherent_outcomes(
        self,
        dismissal_prob: float,
        court: str,
        nos: str,
        defendant: str,
        class_action: bool
    ) -> Dict[str, float]:
        """
        Compute outcome probabilities that are mathematically coherent.

        P(dismissed) + P(settlement) + P(judgment) + P(trial) = 1.0
        """
        # Base rates for cases that survive dismissal (from historical data)
        # These are P(outcome | not dismissed)
        base_settlement_rate = 0.85  # Most cases settle
        base_judgment_rate = 0.10    # Summary judgment or directed verdict
        base_trial_rate = 0.05       # Very few go to trial

        # Adjust based on case characteristics
        settlement_adj = 0.0

        # Class actions almost always settle if they survive
        if class_action:
            settlement_adj += 0.08
            base_trial_rate = 0.02

        # Securities cases have high settlement rates
        if nos in ['850', 'securities']:
            settlement_adj += 0.05

        # Fortune 500 defendants more likely to settle
        fortune_500 = ['google', 'apple', 'meta', 'amazon', 'microsoft', 'facebook',
                       'walmart', 'johnson', 'pfizer', 'bank of america', 'wells fargo']
        if any(f in defendant for f in fortune_500):
            settlement_adj += 0.05

        # Employment cases (442) have moderate trial rates
        if nos == '442':
            base_trial_rate = 0.08
            settlement_adj -= 0.03

        # Compute final conditional probabilities
        p_settlement_given_survives = min(0.95, base_settlement_rate + settlement_adj)
        remaining = 1.0 - p_settlement_given_survives
        p_judgment_given_survives = remaining * (base_judgment_rate / (base_judgment_rate + base_trial_rate))
        p_trial_given_survives = remaining - p_judgment_given_survives

        # Now compute unconditional probabilities
        survival_prob = 1.0 - dismissal_prob

        return {
            'dismissal': round(dismissal_prob, 4),
            'settlement': round(survival_prob * p_settlement_given_survives, 4),
            'judgment': round(survival_prob * p_judgment_given_survives, 4),
            'trial': round(survival_prob * p_trial_given_survives, 4),
        }

    def _assess_confidence(
        self,
        kwargs: Dict,
        dismissal_prob: float,
        outcome_probs: Dict[str, float]
    ) -> str:
        """
        Assess prediction confidence based on input quality and model certainty.
        """
        confidence_score = 0.5  # Start neutral

        # More inputs = higher confidence
        if kwargs.get('court'):
            confidence_score += 0.1
        if kwargs.get('nos'):
            confidence_score += 0.1
        if kwargs.get('defendant'):
            confidence_score += 0.1
        if kwargs.get('judge'):
            confidence_score += 0.15  # Judge is very predictive

        # Extreme probabilities = higher confidence
        max_prob = max(outcome_probs.values())
        if max_prob > 0.8:
            confidence_score += 0.1
        elif max_prob < 0.4:
            confidence_score -= 0.1  # Uncertain outcome

        # Dismissal probability near extremes = more confident
        if dismissal_prob > 0.7 or dismissal_prob < 0.2:
            confidence_score += 0.05

        if confidence_score >= 0.7:
            return "high"
        elif confidence_score >= 0.5:
            return "medium"
        else:
            return "low"

    def is_available(self) -> Dict[str, bool]:
        """Check which ONNX models are available."""
        return {
            'dismissal': (self.onnx_dir / "dismissal.onnx").exists(),
            'value': (self.onnx_dir / "value_q50.onnx").exists(),
            'resolution': (self.onnx_dir / "resolution.onnx").exists(),
            'duration': (self.onnx_dir / "duration_q50.onnx").exists(),
        }
