"""Document discovery service for automatic PACER document fetching.

Evaluates RSS items against configured triggers and queues matching
documents for download, respecting spending limits.
"""
import logging
import json
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

from .doc_discovery_config import DiscoveryConfig, DiscoveryTrigger, get_discovery_config
from .doc_queue import DocumentQueue, QueueItem, QueueItemStatus, get_document_queue

logger = logging.getLogger(__name__)


class DocumentDiscoveryService:
    """Service for discovering and fetching high-value PACER documents.

    This service:
    1. Evaluates RSS items against configured triggers
    2. Queues matching documents for download
    3. Processes the download queue with spending limits
    """

    def __init__(
        self,
        config: Optional[DiscoveryConfig] = None,
        queue: Optional[DocumentQueue] = None,
        pacer_client=None
    ):
        """Initialize the discovery service.

        Args:
            config: Discovery configuration (uses singleton if None)
            queue: Document queue (uses singleton if None)
            pacer_client: PacerClient instance (imports from pacer_auth if None)
        """
        self.config = config or get_discovery_config()
        self.queue = queue or get_document_queue(self.config.daily_limit)

        if pacer_client is None:
            from ..pacer_auth import pacer_client as default_client
            self.pacer_client = default_client
        else:
            self.pacer_client = pacer_client

        logger.info(
            f"DocumentDiscoveryService initialized: "
            f"enabled={self.config.enabled}, "
            f"triggers={len(self.config.triggers)}"
        )

    def evaluate_rss_item(self, item: Dict[str, Any]) -> Optional[Tuple[int, str]]:
        """Evaluate an RSS item against all triggers.

        Args:
            item: RSS item dict

        Returns:
            Tuple of (priority, trigger_name) if matched, None otherwise
        """
        if not self.config.enabled:
            return None

        matching_triggers = self.config.get_matching_triggers(item)
        if not matching_triggers:
            return None

        # Return highest priority trigger
        top_trigger = matching_triggers[0]
        logger.debug(
            f"Item matched trigger '{top_trigger.name}' "
            f"(priority={top_trigger.priority}): {item.get('case_number')}"
        )
        return (top_trigger.priority, top_trigger.name)

    def queue_from_rss_item(self, item: Dict[str, Any]) -> Optional[QueueItem]:
        """Evaluate and queue an RSS item if it matches triggers.

        Args:
            item: RSS item dict

        Returns:
            QueueItem if queued, None if not matched or no doc URL
        """
        match_result = self.evaluate_rss_item(item)
        if not match_result:
            return None

        priority, trigger_name = match_result

        # Check for doc1_url in metadata
        metadata = item.get('metadata_json') or '{}'
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}

        doc_url = metadata.get('doc1_url')
        if not doc_url:
            logger.debug(f"Matched item has no doc1_url: {item.get('case_number')}")
            return None

        return self.queue.enqueue_from_rss(item, priority, trigger_name)

    def evaluate_batch(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Evaluate a batch of RSS items and queue matching ones.

        Args:
            items: List of RSS item dicts

        Returns:
            Dict with evaluation statistics
        """
        if not self.config.enabled:
            return {"enabled": False, "queued": 0, "evaluated": 0}

        queued = 0
        matched = 0
        no_doc_url = 0

        for item in items:
            match_result = self.evaluate_rss_item(item)
            if match_result:
                matched += 1
                queue_item = self.queue_from_rss_item(item)
                if queue_item:
                    queued += 1
                else:
                    no_doc_url += 1

        logger.info(
            f"Evaluated {len(items)} items: "
            f"{matched} matched triggers, {queued} queued, {no_doc_url} no doc URL"
        )

        return {
            "enabled": True,
            "evaluated": len(items),
            "matched": matched,
            "queued": queued,
            "no_doc_url": no_doc_url,
            "pending_in_queue": self.queue.get_pending_count(),
        }

    def process_batch(self, max_count: Optional[int] = None) -> Dict[str, Any]:
        """Process a batch of queued documents.

        Downloads documents from the queue, respecting spending limits.

        Args:
            max_count: Maximum documents to process (defaults to config.batch_size)

        Returns:
            Dict with processing results
        """
        if not self.config.enabled:
            return {"enabled": False, "processed": 0}

        max_count = max_count or self.config.batch_size

        # Check if PACER is configured
        if not self.pacer_client.is_configured():
            logger.warning("PACER client not configured, skipping document processing")
            return {"enabled": True, "processed": 0, "error": "PACER not configured"}

        # Get batch within budget
        remaining_budget = self.queue.get_remaining_budget()
        if remaining_budget <= 0:
            logger.info("Daily budget exhausted, skipping processing")
            return {
                "enabled": True,
                "processed": 0,
                "reason": "budget_exhausted",
                "daily_spent": self.config.daily_limit - remaining_budget,
            }

        batch = self.queue.dequeue_batch(max_count, remaining_budget)
        if not batch:
            return {"enabled": True, "processed": 0, "reason": "queue_empty"}

        # Process each item
        results = {
            "enabled": True,
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "total_cost": 0.0,
            "documents": [],
        }

        for item in batch:
            result = self._download_document(item)
            results["processed"] += 1
            results["documents"].append(result)

            if result["success"]:
                results["succeeded"] += 1
                results["total_cost"] += result.get("cost", 0.0)
            else:
                results["failed"] += 1

        logger.info(
            f"Processed batch: {results['succeeded']}/{results['processed']} succeeded, "
            f"total cost ${results['total_cost']:.2f}"
        )

        return results

    def _download_document(self, item: QueueItem) -> Dict[str, Any]:
        """Download a single document from PACER.

        Args:
            item: QueueItem to download

        Returns:
            Dict with download result
        """
        try:
            # Check spending limits
            limits = self.pacer_client.check_spending_limits()
            if not limits.get('can_proceed'):
                self.queue.mark_failed(item.id, "Spending limit reached")
                return {
                    "success": False,
                    "item_id": item.id,
                    "error": "spending_limit",
                }

            # Fetch the document
            result = self.pacer_client.fetch_document(item.court_code, item.doc_url)

            if result and result.get('path'):
                actual_cost = result.get('amount_usd', 0.0)
                self.queue.mark_completed(item.id, actual_cost)

                return {
                    "success": True,
                    "item_id": item.id,
                    "court_code": item.court_code,
                    "case_number": item.case_number,
                    "path": result['path'],
                    "filename": result.get('filename'),
                    "cached": result.get('cached', False),
                    "cost": actual_cost,
                }
            else:
                self.queue.mark_failed(item.id, "Download returned no path")
                return {
                    "success": False,
                    "item_id": item.id,
                    "error": "download_failed",
                }

        except Exception as e:
            logger.error(f"Error downloading document: {e}", exc_info=True)
            self.queue.mark_failed(item.id, str(e))
            return {
                "success": False,
                "item_id": item.id,
                "error": str(e),
            }

    def get_status(self) -> Dict[str, Any]:
        """Get current service status.

        Returns:
            Dict with service status information
        """
        queue_stats = self.queue.get_stats()

        return {
            "enabled": self.config.enabled,
            "daily_limit": self.config.daily_limit,
            "batch_size": self.config.batch_size,
            "interval_minutes": self.config.interval_minutes,
            "triggers_count": len(self.config.triggers),
            "queue": queue_stats,
            "pacer_configured": self.pacer_client.is_configured() if self.pacer_client else False,
        }


# Module-level singleton
_service: Optional[DocumentDiscoveryService] = None


def get_discovery_service() -> DocumentDiscoveryService:
    """Get or create the discovery service singleton."""
    global _service
    if _service is None:
        _service = DocumentDiscoveryService()
    return _service
