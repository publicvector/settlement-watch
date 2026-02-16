"""
Complaint Document Analyzer

Extracts case features from complaint text to inform valuation estimates.
"""
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


@dataclass
class ComplaintFeatures:
    """Features extracted from a complaint document."""
    # Basic info
    title: str = ""
    filing_date: str = ""

    # Parties
    plaintiffs: List[str] = field(default_factory=list)
    defendants: List[str] = field(default_factory=list)
    class_definition: str = ""
    estimated_class_size: int = 0

    # Claims
    causes_of_action: List[str] = field(default_factory=list)
    statutes_cited: List[str] = field(default_factory=list)
    claim_count: int = 0

    # Damages
    damages_claimed: float = 0
    damages_type: str = ""  # 'actual', 'statutory', 'punitive', 'treble'
    per_violation_amount: float = 0

    # Case characteristics
    is_class_action: bool = False
    is_mdl: bool = False
    is_derivative: bool = False
    is_qui_tam: bool = False
    is_securities: bool = False

    # Strength indicators
    documentary_evidence_mentioned: bool = False
    expert_witnesses_mentioned: bool = False
    prior_cases_cited: int = 0
    regulatory_findings_cited: bool = False

    # Calculated scores
    complexity_score: float = 0
    strength_score: float = 0
    value_multiplier: float = 1.0

    def summary(self) -> str:
        return f"""
COMPLAINT ANALYSIS
{'='*60}
Title: {self.title}
Filing Date: {self.filing_date}

PARTIES
  Plaintiffs: {', '.join(self.plaintiffs[:3])}{'...' if len(self.plaintiffs) > 3 else ''}
  Defendants: {', '.join(self.defendants[:3])}{'...' if len(self.defendants) > 3 else ''}
  Class Action: {'Yes' if self.is_class_action else 'No'}
  Est. Class Size: {self.estimated_class_size:,} members

CLAIMS ({self.claim_count} counts)
  Causes: {', '.join(self.causes_of_action[:5])}
  Statutes: {', '.join(self.statutes_cited[:5])}

DAMAGES
  Type: {self.damages_type}
  Amount Claimed: ${self.damages_claimed/1e6:.1f}M
  Per Violation: ${self.per_violation_amount:,.0f}

STRENGTH INDICATORS
  Documentary Evidence: {'✓' if self.documentary_evidence_mentioned else '✗'}
  Expert Witnesses: {'✓' if self.expert_witnesses_mentioned else '✗'}
  Prior Cases Cited: {self.prior_cases_cited}
  Regulatory Findings: {'✓' if self.regulatory_findings_cited else '✗'}

SCORES
  Complexity: {self.complexity_score:.0f}/100
  Strength: {self.strength_score:.0f}/100
  Value Multiplier: {self.value_multiplier:.2f}x
"""


class ComplaintAnalyzer:
    """
    Analyzes complaint documents to extract valuation-relevant features.
    """

    # Common causes of action patterns
    CAUSE_PATTERNS = {
        'securities fraud': [r'securities\s+fraud', r'10b-?5', r'section\s+10\(b\)',
                            r'securities\s+exchange\s+act'],
        'antitrust': [r'sherman\s+act', r'clayton\s+act', r'antitrust',
                     r'price[- ]fixing', r'monopol'],
        'consumer fraud': [r'consumer\s+fraud', r'unfair\s+business\s+practices',
                          r'false\s+advertising', r'deceptive\s+trade'],
        'data breach': [r'data\s+breach', r'unauthorized\s+access', r'personal\s+information',
                       r'cybersecurity', r'negligent\s+security'],
        'product liability': [r'product\s+liability', r'defective\s+product',
                             r'manufacturing\s+defect', r'design\s+defect', r'failure\s+to\s+warn'],
        'employment': [r'flsa', r'fair\s+labor', r'wage\s+and\s+hour', r'overtime',
                      r'discrimination', r'title\s+vii', r'wrongful\s+termination'],
        'TCPA': [r'tcpa', r'telephone\s+consumer\s+protection', r'robocall',
                r'autodialed', r'prerecorded'],
        'BIPA': [r'bipa', r'biometric\s+information', r'biometric\s+privacy',
                r'facial\s+recognition', r'fingerprint'],
        'ERISA': [r'erisa', r'employee\s+retirement', r'fiduciary\s+duty',
                 r'pension', r'401\(k\)'],
        'environmental': [r'cercla', r'superfund', r'clean\s+water', r'clean\s+air',
                         r'environmental\s+contamination', r'pollution'],
    }

    # Statute patterns with statutory damages
    STATUTORY_DAMAGES = {
        'TCPA': 500,  # Per violation
        'BIPA': 1000,  # Per violation
        'FCRA': 1000,  # Per violation
        'FDCPA': 1000,  # Per violation
        'CCPA': 750,  # Per incident
        'VPPA': 2500,  # Per violation
    }

    def __init__(self):
        self.features = ComplaintFeatures()

    def analyze(self, text: str, title: str = "") -> ComplaintFeatures:
        """
        Analyze complaint text and extract features.

        Args:
            text: Full text of the complaint document
            title: Case title if known

        Returns:
            ComplaintFeatures with extracted data
        """
        self.features = ComplaintFeatures(title=title)
        text_lower = text.lower()

        # Extract basic features
        self._extract_parties(text)
        self._extract_class_info(text, text_lower)
        self._extract_causes(text_lower)
        self._extract_damages(text, text_lower)
        self._extract_strength_indicators(text_lower)
        self._calculate_scores()

        return self.features

    def _extract_parties(self, text: str):
        """Extract plaintiff and defendant names."""
        # Look for plaintiff patterns
        plaintiff_match = re.search(
            r'(?:plaintiff[s]?|petitioner[s]?)[:\s]+([A-Z][^,\n]+)',
            text, re.IGNORECASE
        )
        if plaintiff_match:
            self.features.plaintiffs.append(plaintiff_match.group(1).strip())

        # Look for defendant patterns
        defendant_matches = re.findall(
            r'(?:defendant[s]?|respondent[s]?)[:\s]+([A-Z][A-Za-z0-9\s,&.]+?)(?:\n|,\s*(?:a|an|the))',
            text, re.IGNORECASE
        )
        for match in defendant_matches[:5]:
            self.features.defendants.append(match.strip())

        # Look for "v." pattern in title
        v_match = re.search(r'([^v]+)\s+v\.?\s+(.+)', text[:500], re.IGNORECASE)
        if v_match:
            if not self.features.plaintiffs:
                self.features.plaintiffs.append(v_match.group(1).strip())
            if not self.features.defendants:
                self.features.defendants.append(v_match.group(2).strip()[:100])

    def _extract_class_info(self, text: str, text_lower: str):
        """Extract class action information."""
        # Check if class action
        class_indicators = ['class action', 'on behalf of', 'similarly situated',
                           'class members', 'class representative']
        self.features.is_class_action = any(ind in text_lower for ind in class_indicators)

        # Extract class definition
        class_match = re.search(
            r'class\s+(?:is\s+)?defined\s+as[:\s]+([^.]+\.)',
            text, re.IGNORECASE
        )
        if class_match:
            self.features.class_definition = class_match.group(1).strip()

        # Extract estimated class size
        size_patterns = [
            r'(?:estimated|approximately|over|more than)\s+(\d[\d,]*)\s+(?:class\s+)?members',
            r'class\s+of\s+(?:over\s+)?(\d[\d,]*)',
            r'(\d[\d,]*)\s+(?:affected|impacted)\s+(?:individuals|consumers|customers)',
        ]
        for pattern in size_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                size_str = match.group(1).replace(',', '')
                try:
                    self.features.estimated_class_size = int(size_str)
                    break
                except ValueError:
                    pass

        # Check for MDL
        self.features.is_mdl = 'mdl' in text_lower or 'multidistrict' in text_lower

        # Check for qui tam
        self.features.is_qui_tam = 'qui tam' in text_lower or 'relator' in text_lower

        # Check for securities
        self.features.is_securities = any(
            term in text_lower for term in ['securities', '10b-5', 'exchange act']
        )

    def _extract_causes(self, text_lower: str):
        """Extract causes of action."""
        for cause, patterns in self.CAUSE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    if cause not in self.features.causes_of_action:
                        self.features.causes_of_action.append(cause)
                    break

        # Count claims/counts
        count_matches = re.findall(r'(?:count|claim|cause of action)\s+(?:\d+|[ivxlc]+)',
                                   text_lower)
        self.features.claim_count = max(len(count_matches), len(self.features.causes_of_action))

        # Extract statutes
        statute_patterns = [
            r'(\d+\s+u\.?s\.?c\.?\s+§?\s*\d+)',  # USC citations
            r'(section\s+\d+\([a-z]\))',  # Section citations
            r'(rule\s+10b-?5)',
            r'(sherman\s+act)',
            r'(clayton\s+act)',
            r'(tcpa|bipa|fcra|fdcpa|ccpa|erisa)',
        ]
        for pattern in statute_patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                if match.upper() not in [s.upper() for s in self.features.statutes_cited]:
                    self.features.statutes_cited.append(match.upper())

    def _extract_damages(self, text: str, text_lower: str):
        """Extract damages information."""
        # Look for specific damage amounts
        damage_patterns = [
            r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|billion|m|b)',
            r'(?:damages|losses|harm)\s+(?:of|exceeding|totaling)\s+\$\s*([\d,]+)',
            r'(?:seek|seeking|prayer)\s+.*?\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|billion)?',
        ]

        max_damages = 0
        for pattern in damage_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    amount = float(match.replace(',', ''))
                    # Detect magnitude
                    if 'billion' in text_lower[text_lower.find(match)-20:text_lower.find(match)+20]:
                        amount *= 1e9
                    elif 'million' in text_lower[text_lower.find(match)-20:text_lower.find(match)+20]:
                        amount *= 1e6
                    max_damages = max(max_damages, amount)
                except ValueError:
                    pass

        self.features.damages_claimed = max_damages

        # Determine damages type
        if 'treble' in text_lower or 'triple' in text_lower:
            self.features.damages_type = 'treble'
        elif 'punitive' in text_lower:
            self.features.damages_type = 'punitive'
        elif 'statutory' in text_lower:
            self.features.damages_type = 'statutory'
        else:
            self.features.damages_type = 'actual'

        # Check for statutory damages
        for statute, amount in self.STATUTORY_DAMAGES.items():
            if statute.lower() in text_lower:
                self.features.per_violation_amount = max(
                    self.features.per_violation_amount, amount
                )

    def _extract_strength_indicators(self, text_lower: str):
        """Extract indicators of case strength."""
        # Documentary evidence
        doc_indicators = ['document', 'email', 'memorandum', 'record', 'exhibit',
                         'evidence shows', 'evidence demonstrates']
        self.features.documentary_evidence_mentioned = any(
            ind in text_lower for ind in doc_indicators
        )

        # Expert witnesses
        expert_indicators = ['expert', 'economist', 'forensic', 'specialist']
        self.features.expert_witnesses_mentioned = any(
            ind in text_lower for ind in expert_indicators
        )

        # Prior cases
        prior_case_matches = re.findall(r'\d+\s+f\.\s*(?:supp|3d|2d)', text_lower)
        self.features.prior_cases_cited = len(prior_case_matches)

        # Regulatory findings
        reg_indicators = ['fda', 'sec', 'ftc', 'doj', 'investigation', 'enforcement',
                         'consent decree', 'regulatory']
        self.features.regulatory_findings_cited = any(
            ind in text_lower for ind in reg_indicators
        )

    def _calculate_scores(self):
        """Calculate complexity and strength scores."""
        # Complexity score
        complexity = 0
        complexity += min(30, self.features.claim_count * 5)
        complexity += min(20, len(self.features.defendants) * 5)
        complexity += 10 if self.features.is_class_action else 0
        complexity += 15 if self.features.is_mdl else 0
        complexity += 10 if self.features.is_securities else 0
        complexity += min(15, len(self.features.statutes_cited) * 3)
        self.features.complexity_score = min(100, complexity)

        # Strength score
        strength = 40  # Base score
        strength += 15 if self.features.documentary_evidence_mentioned else 0
        strength += 10 if self.features.expert_witnesses_mentioned else 0
        strength += min(15, self.features.prior_cases_cited * 3)
        strength += 15 if self.features.regulatory_findings_cited else 0
        strength += 5 if self.features.damages_claimed > 0 else 0
        self.features.strength_score = min(100, strength)

        # Value multiplier
        multiplier = 1.0
        if self.features.is_class_action:
            if self.features.estimated_class_size > 1_000_000:
                multiplier *= 1.3
            elif self.features.estimated_class_size > 100_000:
                multiplier *= 1.15
        if self.features.damages_type == 'treble':
            multiplier *= 1.5
        elif self.features.damages_type == 'punitive':
            multiplier *= 1.25
        if self.features.is_mdl:
            multiplier *= 1.2
        if self.features.regulatory_findings_cited:
            multiplier *= 1.1

        self.features.value_multiplier = multiplier


def analyze_complaint_file(filepath: str) -> ComplaintFeatures:
    """Convenience function to analyze a complaint from file."""
    with open(filepath, 'r') as f:
        text = f.read()

    analyzer = ComplaintAnalyzer()
    return analyzer.analyze(text, title=filepath)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        features = analyze_complaint_file(filepath)
        print(features.summary())
    else:
        # Demo with sample text
        sample = """
        IN THE UNITED STATES DISTRICT COURT
        FOR THE NORTHERN DISTRICT OF CALIFORNIA

        JOHN DOE, on behalf of himself and all others similarly situated,
            Plaintiff,
        v.
        TECH GIANT INC., a Delaware corporation,
            Defendant.

        CLASS ACTION COMPLAINT

        Plaintiff brings this class action on behalf of all persons in the
        United States who had their biometric information collected by
        Defendant without consent.

        The class is estimated to include over 500,000 members.

        COUNT I - VIOLATION OF BIPA
        COUNT II - NEGLIGENCE
        COUNT III - BREACH OF CONTRACT

        Plaintiff seeks statutory damages of $1,000 per violation under BIPA,
        totaling over $500 million for the class.
        """

        analyzer = ComplaintAnalyzer()
        features = analyzer.analyze(sample)
        print(features.summary())
