"""Document discovery configuration and trigger definitions.

Configures which documents should be automatically fetched based on
case type, NOS codes, motion types, and keywords.
"""
import os
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Set, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryTrigger:
    """Defines conditions that trigger automatic document fetching.

    When an RSS item matches a trigger's conditions, the associated
    documents are queued for download.
    """
    name: str
    priority: int  # Higher priority = fetched first (1-10)
    enabled: bool = True

    # Matching criteria (any match triggers the trigger)
    motion_types: List[str] = field(default_factory=list)
    case_types: List[str] = field(default_factory=list)  # cv, cr, bk, etc.
    nos_codes: List[str] = field(default_factory=list)  # Nature of Suit codes
    keywords: List[str] = field(default_factory=list)  # Keywords to match in title/summary
    doc_numbers: List[int] = field(default_factory=list)  # Specific doc numbers (e.g., [1] for complaints)

    # Optional filters
    courts: Optional[List[str]] = None  # If set, only match these courts
    exclude_courts: Optional[List[str]] = None  # Courts to exclude
    min_amount: Optional[float] = None  # Minimum amount mentioned (if detected)

    def matches(self, item: Dict[str, Any]) -> bool:
        """Check if an RSS item matches this trigger.

        Args:
            item: RSS item dict with keys like case_number, case_type,
                  nature_of_suit, title, summary, metadata_json, court_code

        Returns:
            True if item matches this trigger's conditions
        """
        if not self.enabled:
            return False

        # Court filtering
        court_code = (item.get('court_code') or '').lower()
        if self.courts and court_code not in [c.lower() for c in self.courts]:
            return False
        if self.exclude_courts and court_code in [c.lower() for c in self.exclude_courts]:
            return False

        # Check case type
        item_case_type = (item.get('case_type') or '').lower()
        if self.case_types:
            if item_case_type in [ct.lower() for ct in self.case_types]:
                return True

        # Check NOS codes
        item_nos = item.get('nature_of_suit') or ''
        metadata = item.get('metadata_json') or '{}'
        if isinstance(metadata, str):
            import json
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}

        nos_info = metadata.get('nos', {}) or {}
        nos_code = nos_info.get('code') if isinstance(nos_info, dict) else None

        if self.nos_codes:
            if nos_code and nos_code in self.nos_codes:
                return True
            # Also check nature_of_suit text field for partial matches
            for code in self.nos_codes:
                if code in item_nos:
                    return True

        # Check keywords in title and summary
        if self.keywords:
            text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
            for keyword in self.keywords:
                if keyword.lower() in text:
                    return True

        # Check doc numbers
        if self.doc_numbers:
            doc_no = metadata.get('doc_number')
            entry_no = metadata.get('docket_entry_number')
            if doc_no in self.doc_numbers or entry_no in self.doc_numbers:
                return True

        # Check motion types
        if self.motion_types:
            event_type = metadata.get('event_type', '')
            text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
            for motion_type in self.motion_types:
                if motion_type.lower() in text or motion_type.lower() in event_type.lower():
                    return True

        return False

    def __str__(self) -> str:
        return f"DiscoveryTrigger({self.name}, priority={self.priority})"


class DiscoveryConfig:
    """Configuration for document discovery system.

    Loads configuration from environment variables and provides
    default high-value triggers.
    """

    def __init__(self):
        # Load from environment
        self.enabled = os.getenv('DOC_DISCOVERY_ENABLED', 'false').lower() == 'true'
        self.daily_limit = float(os.getenv('DOC_DISCOVERY_DAILY_LIMIT', '10.00'))
        self.batch_size = int(os.getenv('DOC_DISCOVERY_BATCH_SIZE', '10'))
        self.interval_minutes = int(os.getenv('DOC_DISCOVERY_INTERVAL', '15'))

        # Courts to include/exclude
        allowed = os.getenv('DOC_DISCOVERY_ALLOWED_COURTS', '')
        self.allowed_courts = [c.strip().lower() for c in allowed.split(',') if c.strip()] or None

        excluded = os.getenv('DOC_DISCOVERY_EXCLUDED_COURTS', '')
        self.excluded_courts = [c.strip().lower() for c in excluded.split(',') if c.strip()] or None

        # Initialize default triggers
        self.triggers: List[DiscoveryTrigger] = self._create_default_triggers()

        logger.info(
            f"DiscoveryConfig initialized: enabled={self.enabled}, "
            f"daily_limit=${self.daily_limit}, batch_size={self.batch_size}, "
            f"triggers={len(self.triggers)}"
        )

    def _create_default_triggers(self) -> List[DiscoveryTrigger]:
        """Create default high-value discovery triggers.

        These triggers target document types that are typically
        most valuable for legal research and monitoring.
        """
        return [
            # Personal injury complaints - NOS 360-368
            DiscoveryTrigger(
                name="personal_injury_complaints",
                priority=10,
                nos_codes=['360', '361', '362', '363', '364', '365', '366', '367', '368'],
                doc_numbers=[1],  # Complaints
                keywords=['personal injury', 'negligence', 'malpractice'],
            ),

            # Mass tort and MDL cases
            DiscoveryTrigger(
                name="mass_tort_cases",
                priority=9,
                keywords=['mass tort', 'mdl', 'multidistrict', 'multi-district'],
                doc_numbers=[1],
            ),

            # Consumer class actions
            DiscoveryTrigger(
                name="consumer_class_actions",
                priority=8,
                nos_codes=['480'],  # Consumer Credit
                keywords=['class action', 'consumer', 'tcpa', 'fdcpa', 'fcra'],
                doc_numbers=[1],
            ),

            # Product liability
            DiscoveryTrigger(
                name="product_liability",
                priority=8,
                nos_codes=['315', '365'],  # Airplane, Product Liability
                keywords=['product liability', 'defect', 'recall'],
                doc_numbers=[1],
            ),

            # Employment discrimination
            DiscoveryTrigger(
                name="employment_discrimination",
                priority=7,
                nos_codes=['442'],  # Employment Civil Rights
                keywords=['discrimination', 'wrongful termination', 'eeoc', 'title vii'],
                doc_numbers=[1],
            ),

            # Securities fraud
            DiscoveryTrigger(
                name="securities_fraud",
                priority=7,
                nos_codes=['850'],  # Securities/Commodities
                keywords=['securities fraud', 'sec', 'investor', 'stock'],
                doc_numbers=[1],
            ),

            # Patent cases
            DiscoveryTrigger(
                name="patent_cases",
                priority=6,
                nos_codes=['830'],  # Patent
                case_types=['cv'],
                keywords=['patent', 'infringement'],
                doc_numbers=[1],
            ),

            # Antitrust
            DiscoveryTrigger(
                name="antitrust",
                priority=6,
                nos_codes=['410'],  # Antitrust
                keywords=['antitrust', 'monopoly', 'price fixing', 'sherman act'],
                doc_numbers=[1],
            ),

            # Summary judgment motions (any civil case)
            DiscoveryTrigger(
                name="summary_judgment_motions",
                priority=5,
                case_types=['cv'],
                motion_types=['summary judgment'],
                keywords=['motion for summary judgment', 'msj'],
            ),

            # Preliminary injunctions
            DiscoveryTrigger(
                name="preliminary_injunctions",
                priority=5,
                motion_types=['preliminary injunction', 'temporary restraining order', 'tro'],
                keywords=['preliminary injunction', 'temporary restraining', 'tro'],
            ),
        ]

    def get_matching_triggers(self, item: Dict[str, Any]) -> List[DiscoveryTrigger]:
        """Get all triggers that match an RSS item.

        Args:
            item: RSS item dict

        Returns:
            List of matching triggers, sorted by priority (highest first)
        """
        matches = [t for t in self.triggers if t.matches(item)]
        return sorted(matches, key=lambda t: t.priority, reverse=True)

    def get_highest_priority(self, item: Dict[str, Any]) -> Optional[int]:
        """Get the highest priority among all matching triggers.

        Args:
            item: RSS item dict

        Returns:
            Highest priority value, or None if no triggers match
        """
        matches = self.get_matching_triggers(item)
        return matches[0].priority if matches else None

    def should_queue(self, item: Dict[str, Any]) -> bool:
        """Check if an item should be queued for download.

        Args:
            item: RSS item dict

        Returns:
            True if item matches any enabled trigger
        """
        if not self.enabled:
            return False
        return len(self.get_matching_triggers(item)) > 0

    def add_trigger(self, trigger: DiscoveryTrigger):
        """Add a custom trigger.

        Args:
            trigger: DiscoveryTrigger to add
        """
        self.triggers.append(trigger)
        logger.info(f"Added trigger: {trigger}")

    def remove_trigger(self, name: str) -> bool:
        """Remove a trigger by name.

        Args:
            name: Name of trigger to remove

        Returns:
            True if trigger was found and removed
        """
        original_len = len(self.triggers)
        self.triggers = [t for t in self.triggers if t.name != name]
        removed = len(self.triggers) < original_len
        if removed:
            logger.info(f"Removed trigger: {name}")
        return removed


# Module-level singleton
_config: Optional[DiscoveryConfig] = None


def get_discovery_config() -> DiscoveryConfig:
    """Get or create the discovery configuration singleton."""
    global _config
    if _config is None:
        _config = DiscoveryConfig()
    return _config
