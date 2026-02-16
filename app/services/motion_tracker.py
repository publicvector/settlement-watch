"""
Motion Tracking Service.

Detects motions and their outcomes from docket entries,
linking them to filing attorneys and firms for analytics.
"""

import re
import uuid
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime


# Motion type patterns (case-insensitive)
MOTION_PATTERNS = {
    'mtd': [
        r'motion to dismiss',
        r'motion for dismissal',
        r'12\(b\)\(\d+\) motion',
    ],
    'msj': [
        r'motion for summary judgment',
        r'summary judgment motion',
        r'cross.?motion for summary judgment',
    ],
    'msl': [
        r'motion to seal',
        r'motion for leave to seal',
    ],
    'mtc': [
        r'motion to compel',
        r'motion for order compelling',
    ],
    'mpi': [
        r'motion for preliminary injunction',
        r'motion for temporary restraining order',
        r'tro motion',
    ],
    'mtq': [
        r'motion to quash',
    ],
    'mts': [
        r'motion to strike',
    ],
    'mtl': [
        r'motion in limine',
        r'motions in limine',
    ],
    'mtv': [
        r'motion for new trial',
        r'motion to vacate',
        r'motion for reconsideration',
    ],
    'mca': [
        r'motion for class certification',
        r'motion to certify class',
    ],
    'mtr': [
        r'motion to remand',
    ],
    'mtt': [
        r'motion to transfer',
        r'motion for change of venue',
    ],
}

# Outcome patterns
OUTCOME_PATTERNS = {
    'granted': [
        r'\bgranted\b',
        r'\bsustained\b',
        r'\ballowed\b',
        r'\bgranting\b',
    ],
    'denied': [
        r'\bdenied\b',
        r'\boverruled\b',
        r'\brejected\b',
        r'\bdenying\b',
    ],
    'partial': [
        r'granted in part',
        r'denied in part',
        r'partially granted',
        r'partially denied',
    ],
    'moot': [
        r'\bmoot\b',
        r'\bwithdrawn\b',
        r'terminated as moot',
    ],
}

# Party type to filed_by mapping
PARTY_TYPE_MAP = {
    'plaintiff': 'plaintiff',
    'plaintiffs': 'plaintiff',
    'petitioner': 'plaintiff',
    'petitioners': 'plaintiff',
    'appellant': 'plaintiff',
    'appellants': 'plaintiff',
    'defendant': 'defendant',
    'defendants': 'defendant',
    'respondent': 'defendant',
    'respondents': 'defendant',
    'appellee': 'defendant',
    'appellees': 'defendant',
}


class MotionTracker:
    """
    Tracks motions and their outcomes from docket entries.

    Usage:
        tracker = MotionTracker()
        motions = tracker.extract_motions(entries, docket_info)
        motions_with_outcomes = tracker.match_outcomes(motions, entries)
    """

    def __init__(self):
        # Compile patterns for efficiency
        self._motion_patterns = {}
        for motion_type, patterns in MOTION_PATTERNS.items():
            self._motion_patterns[motion_type] = re.compile(
                '|'.join(patterns), re.IGNORECASE
            )

        self._outcome_patterns = {}
        for outcome, patterns in OUTCOME_PATTERNS.items():
            self._outcome_patterns[outcome] = re.compile(
                '|'.join(patterns), re.IGNORECASE
            )

    def detect_motion_type(self, text: str) -> Optional[str]:
        """Detect motion type from entry text."""
        if not text:
            return None

        text_lower = text.lower()

        # Check if this looks like a motion filing
        if 'motion' not in text_lower and 'tro' not in text_lower:
            return None

        for motion_type, pattern in self._motion_patterns.items():
            if pattern.search(text):
                return motion_type

        return None

    def detect_outcome(self, text: str) -> Optional[str]:
        """Detect motion outcome from order text."""
        if not text:
            return None

        text_lower = text.lower()

        # Look for order indicators
        order_indicators = ['order', 'ruling', 'decision', 'opinion', 'memorandum']
        if not any(ind in text_lower for ind in order_indicators):
            return None

        # Check for partial first (more specific)
        if self._outcome_patterns['partial'].search(text):
            return 'partial'

        # Then check other outcomes
        if self._outcome_patterns['moot'].search(text):
            return 'moot'
        if self._outcome_patterns['granted'].search(text):
            return 'granted'
        if self._outcome_patterns['denied'].search(text):
            return 'denied'

        return None

    def extract_motions(
        self,
        entries: List[Dict[str, Any]],
        docket_info: Dict[str, Any],
        attorneys: List[Dict[str, Any]] = None,
        parties: List[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Extract motion events from docket entries.

        Args:
            entries: List of docket entries with description and date
            docket_info: Docket metadata (court_code, case_name, etc.)
            attorneys: Optional list of attorneys on the case
            parties: Optional list of parties on the case

        Returns:
            List of motion event dicts ready for insertion
        """
        motions = []
        attorney_map = self._build_attorney_map(attorneys, parties)

        for entry in entries:
            description = entry.get('description') or entry.get('text_raw') or ''
            motion_type = self.detect_motion_type(description)

            if not motion_type:
                continue

            # Try to determine who filed the motion
            filed_by, filing_attorney, filing_firm = self._identify_filer(
                description, attorney_map, parties
            )

            motion = {
                'id': str(uuid.uuid5(uuid.NAMESPACE_URL,
                    f"{docket_info.get('id', '')}-{entry.get('id', '')}-{motion_type}")),
                'docket_id': docket_info.get('id'),
                'entry_id': entry.get('id'),
                'motion_type': motion_type,
                'filed_by': filed_by,
                'filing_attorney_id': filing_attorney.get('id') if filing_attorney else None,
                'filing_firm': filing_firm or (filing_attorney.get('firm') if filing_attorney else None),
                'filed_date': entry.get('date_filed') or entry.get('filed_on'),
                'outcome': None,
                'outcome_date': None,
                'outcome_entry_id': None,
                'court_code': docket_info.get('court_code'),
                'case_type': self._infer_case_type(docket_info),
                'case_name': docket_info.get('case_name'),
            }

            motions.append(motion)

        return motions

    def match_outcomes(
        self,
        motions: List[Dict[str, Any]],
        entries: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Match outcomes to pending motions from subsequent entries.

        Args:
            motions: List of motion events
            entries: All docket entries (ordered by date)

        Returns:
            Updated motions with outcomes where found
        """
        # Build entry index by date
        entries_by_date = sorted(entries, key=lambda e: e.get('date_filed') or e.get('filed_on') or '')

        for motion in motions:
            if motion.get('outcome'):
                continue  # Already has outcome

            motion_date = motion.get('filed_date') or ''
            motion_type = motion.get('motion_type')

            # Look for outcome in subsequent entries
            for entry in entries_by_date:
                entry_date = entry.get('date_filed') or entry.get('filed_on') or ''

                # Only look at entries after the motion was filed
                if entry_date <= motion_date:
                    continue

                description = entry.get('description') or entry.get('text_raw') or ''

                # Check if this entry references the motion type
                if not self._references_motion_type(description, motion_type):
                    continue

                # Check for outcome
                outcome = self.detect_outcome(description)
                if outcome:
                    motion['outcome'] = outcome
                    motion['outcome_date'] = entry_date
                    motion['outcome_entry_id'] = entry.get('id')
                    break

        return motions

    def _build_attorney_map(
        self,
        attorneys: List[Dict[str, Any]] = None,
        parties: List[Dict[str, Any]] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Build a map of party type to attorneys."""
        if not attorneys:
            return {}

        party_attorneys = {'plaintiff': [], 'defendant': []}

        # Map parties to their types
        party_type_map = {}
        if parties:
            for party in parties:
                party_id = party.get('id')
                party_type = (party.get('party_type') or '').lower()
                normalized = PARTY_TYPE_MAP.get(party_type, 'other')
                party_type_map[party_id] = normalized

        # Group attorneys by party type
        for attorney in attorneys:
            party_id = attorney.get('party_id')
            party_type = party_type_map.get(party_id, 'other')
            if party_type in party_attorneys:
                party_attorneys[party_type].append(attorney)

        return party_attorneys

    def _identify_filer(
        self,
        description: str,
        attorney_map: Dict[str, List[Dict[str, Any]]],
        parties: List[Dict[str, Any]] = None
    ) -> Tuple[Optional[str], Optional[Dict], Optional[str]]:
        """
        Identify who filed the motion from the description.

        Returns:
            Tuple of (filed_by, attorney_dict, firm_name)
        """
        description_lower = description.lower()

        # Check for explicit party mentions
        plaintiff_keywords = ['plaintiff', 'petitioner', 'appellant']
        defendant_keywords = ['defendant', 'respondent', 'appellee']

        is_plaintiff = any(kw in description_lower for kw in plaintiff_keywords)
        is_defendant = any(kw in description_lower for kw in defendant_keywords)

        filed_by = None
        if is_plaintiff and not is_defendant:
            filed_by = 'plaintiff'
        elif is_defendant and not is_plaintiff:
            filed_by = 'defendant'

        # Try to find specific attorney
        attorney = None
        firm = None

        if filed_by and attorney_map.get(filed_by):
            # Use the first attorney for this party type
            attorneys = attorney_map[filed_by]
            if attorneys:
                attorney = attorneys[0]
                firm = attorney.get('firm')

        return filed_by, attorney, firm

    def _references_motion_type(self, description: str, motion_type: str) -> bool:
        """Check if description references the given motion type."""
        if not description or not motion_type:
            return False

        # Get patterns for this motion type
        if motion_type in self._motion_patterns:
            return bool(self._motion_patterns[motion_type].search(description))

        return False

    def _infer_case_type(self, docket_info: Dict[str, Any]) -> Optional[str]:
        """Infer case type from docket info."""
        docket_number = docket_info.get('docket_number') or ''

        if '-cv-' in docket_number:
            return 'cv'
        elif '-cr-' in docket_number:
            return 'cr'
        elif '-bk-' in docket_number:
            return 'bk'
        elif '-ap-' in docket_number:
            return 'ap'
        elif '-mc-' in docket_number:
            return 'mc'

        return None


def extract_and_track_motions(
    docket_id: str,
    entries: List[Dict[str, Any]],
    docket_info: Dict[str, Any],
    attorneys: List[Dict[str, Any]] = None,
    parties: List[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Convenience function to extract motions and match outcomes.

    Args:
        docket_id: Docket ID
        entries: List of docket entries
        docket_info: Docket metadata
        attorneys: Optional attorneys list
        parties: Optional parties list

    Returns:
        List of motion events with outcomes where found
    """
    tracker = MotionTracker()

    # Ensure docket_id is in docket_info
    docket_info = {**docket_info, 'id': docket_id}

    # Extract motions
    motions = tracker.extract_motions(entries, docket_info, attorneys, parties)

    # Match outcomes
    motions = tracker.match_outcomes(motions, entries)

    return motions
