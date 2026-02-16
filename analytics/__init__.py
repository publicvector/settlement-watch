"""
Analytics module for Settlement Watch case valuation.

Components:
- CaseValuator: Cause-of-action based valuation with multipliers
- CaseAnalytics: Full case scoring across court, defendant, source
- ComplaintAnalyzer: Extract features from complaint documents

ML Integration:
- CasePredictor: ML-powered predictions for dismissal, value, resolution, duration
"""
from .case_valuation import CaseValuator, ValuationResult
from .case_analytics import CaseAnalytics, CaseScorecard, CourtProfile, DefendantProfile
from .complaint_analyzer import ComplaintAnalyzer, ComplaintFeatures

# ML integration (optional - may not be trained yet)
try:
    from ml.inference.predictor import CasePredictor
    ML_AVAILABLE = True
except ImportError:
    CasePredictor = None
    ML_AVAILABLE = False
