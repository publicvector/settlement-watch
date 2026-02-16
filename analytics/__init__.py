"""
Analytics module for Settlement Watch case valuation.

Components:
- CaseValuator: Cause-of-action based valuation with multipliers
- CaseAnalytics: Full case scoring across court, defendant, source
- ComplaintAnalyzer: Extract features from complaint documents
"""
from .case_valuation import CaseValuator, ValuationResult
from .case_analytics import CaseAnalytics, CaseScorecard, CourtProfile, DefendantProfile
from .complaint_analyzer import ComplaintAnalyzer, ComplaintFeatures
