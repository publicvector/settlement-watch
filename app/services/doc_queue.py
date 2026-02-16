"""Document download queue with priority and cost awareness.

Provides a thread-safe priority queue for PACER document downloads
with spending limit enforcement and database persistence.
"""
import time
import threading
import logging
import hashlib
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from enum import Enum

logger = logging.getLogger(__name__)


class QueueItemStatus(Enum):
    """Status of a queued document download."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # Skipped due to spending limits


@dataclass(order=True)
class QueueItem:
    """A document queued for download.

    Ordered by priority (descending) then by created_at (ascending).
    """
    # Sorting fields (negative priority so higher priority sorts first)
    sort_priority: int = field(init=False, repr=False)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Actual fields
    id: str = field(compare=False, default="")
    court_code: str = field(compare=False, default="")
    case_number: str = field(compare=False, default="")
    doc_url: str = field(compare=False, default="")
    priority: int = field(compare=False, default=5)
    estimated_cost: float = field(compare=False, default=0.10)
    trigger_name: str = field(compare=False, default="")
    rss_item_id: str = field(compare=False, default="")
    status: QueueItemStatus = field(compare=False, default=QueueItemStatus.PENDING)
    error_message: Optional[str] = field(compare=False, default=None)
    completed_at: Optional[str] = field(compare=False, default=None)
    actual_cost: Optional[float] = field(compare=False, default=None)

    def __post_init__(self):
        self.sort_priority = -self.priority
        if not self.id:
            # Generate ID from doc_url
            self.id = hashlib.sha256(self.doc_url.encode()).hexdigest()[:16]


class DocumentQueue:
    """Thread-safe priority queue for document downloads.

    Features:
    - Priority-based ordering (higher priority = fetched first)
    - Cost awareness with spending limit enforcement
    - Database persistence for queue state
    - Deduplication by document URL
    """

    def __init__(self, daily_limit: float = 10.0):
        """Initialize the document queue.

        Args:
            daily_limit: Maximum daily spending in USD
        """
        self.daily_limit = daily_limit
        self._queue: List[QueueItem] = []
        self._lock = threading.Lock()
        self._url_set: set = set()  # For deduplication
        self._daily_spent: float = 0.0
        self._last_reset_date: str = date.today().isoformat()

        logger.info(f"DocumentQueue initialized with daily_limit=${daily_limit}")

    def _reset_daily_if_needed(self):
        """Reset daily spending counter if it's a new day."""
        today = date.today().isoformat()
        if today != self._last_reset_date:
            logger.info(f"New day, resetting daily spent from ${self._daily_spent:.2f} to $0.00")
            self._daily_spent = 0.0
            self._last_reset_date = today

    def enqueue(self, item: QueueItem) -> bool:
        """Add an item to the queue.

        Args:
            item: QueueItem to add

        Returns:
            True if item was added, False if duplicate
        """
        with self._lock:
            # Check for duplicate
            if item.doc_url in self._url_set:
                logger.debug(f"Skipping duplicate URL: {item.doc_url[:60]}...")
                return False

            self._url_set.add(item.doc_url)
            self._queue.append(item)
            self._queue.sort()  # Maintain priority order

            logger.info(
                f"Queued document: {item.court_code}/{item.case_number} "
                f"priority={item.priority} est_cost=${item.estimated_cost:.2f}"
            )
            return True

    def enqueue_from_rss(
        self,
        rss_item: Dict[str, Any],
        priority: int,
        trigger_name: str
    ) -> Optional[QueueItem]:
        """Create and enqueue a QueueItem from an RSS item.

        Args:
            rss_item: RSS item dict with metadata_json containing doc1_url
            priority: Priority for this download (1-10)
            trigger_name: Name of the trigger that matched

        Returns:
            QueueItem if successfully queued, None if skipped
        """
        import json

        metadata = rss_item.get('metadata_json') or '{}'
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}

        doc_url = metadata.get('doc1_url')
        if not doc_url:
            return None

        item = QueueItem(
            court_code=rss_item.get('court_code', ''),
            case_number=rss_item.get('case_number', ''),
            doc_url=doc_url,
            priority=priority,
            estimated_cost=0.10,  # Default estimate
            trigger_name=trigger_name,
            rss_item_id=rss_item.get('id', ''),
        )

        if self.enqueue(item):
            return item
        return None

    def dequeue(self) -> Optional[QueueItem]:
        """Get the next item from the queue.

        Returns:
            Next QueueItem, or None if queue is empty
        """
        with self._lock:
            self._reset_daily_if_needed()

            # Find first pending item within budget
            for i, item in enumerate(self._queue):
                if item.status == QueueItemStatus.PENDING:
                    if self._daily_spent + item.estimated_cost <= self.daily_limit:
                        item.status = QueueItemStatus.IN_PROGRESS
                        return item
                    else:
                        logger.debug(
                            f"Item would exceed daily limit: "
                            f"${self._daily_spent:.2f} + ${item.estimated_cost:.2f} > ${self.daily_limit:.2f}"
                        )
            return None

    def dequeue_batch(self, max_count: int, cost_budget: Optional[float] = None) -> List[QueueItem]:
        """Get a batch of items from the queue within cost budget.

        Args:
            max_count: Maximum number of items to return
            cost_budget: Maximum total cost for batch (defaults to remaining daily limit)

        Returns:
            List of QueueItems within budget
        """
        with self._lock:
            self._reset_daily_if_needed()

            if cost_budget is None:
                cost_budget = self.daily_limit - self._daily_spent

            batch = []
            batch_cost = 0.0

            for item in self._queue:
                if len(batch) >= max_count:
                    break

                if item.status != QueueItemStatus.PENDING:
                    continue

                item_cost = item.estimated_cost
                if batch_cost + item_cost <= cost_budget:
                    item.status = QueueItemStatus.IN_PROGRESS
                    batch.append(item)
                    batch_cost += item_cost

            logger.info(
                f"Dequeued batch of {len(batch)} items "
                f"with estimated cost ${batch_cost:.2f}"
            )
            return batch

    def mark_completed(self, item_id: str, actual_cost: float):
        """Mark an item as completed and record actual cost.

        Args:
            item_id: ID of the completed item
            actual_cost: Actual cost charged
        """
        with self._lock:
            for item in self._queue:
                if item.id == item_id:
                    item.status = QueueItemStatus.COMPLETED
                    item.actual_cost = actual_cost
                    item.completed_at = datetime.utcnow().isoformat()
                    self._daily_spent += actual_cost
                    logger.info(
                        f"Completed: {item.court_code}/{item.case_number} "
                        f"cost=${actual_cost:.2f}, daily_total=${self._daily_spent:.2f}"
                    )
                    break

    def mark_failed(self, item_id: str, error_message: str):
        """Mark an item as failed.

        Args:
            item_id: ID of the failed item
            error_message: Error description
        """
        with self._lock:
            for item in self._queue:
                if item.id == item_id:
                    item.status = QueueItemStatus.FAILED
                    item.error_message = error_message
                    item.completed_at = datetime.utcnow().isoformat()
                    logger.warning(f"Failed: {item.court_code}/{item.case_number} - {error_message}")
                    break

    def get_remaining_budget(self) -> float:
        """Get remaining daily budget.

        Returns:
            Remaining budget in USD
        """
        with self._lock:
            self._reset_daily_if_needed()
            return max(0.0, self.daily_limit - self._daily_spent)

    def get_pending_count(self) -> int:
        """Get count of pending items."""
        with self._lock:
            return sum(1 for item in self._queue if item.status == QueueItemStatus.PENDING)

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics.

        Returns:
            Dict with queue stats
        """
        with self._lock:
            self._reset_daily_if_needed()

            status_counts = {s.value: 0 for s in QueueItemStatus}
            for item in self._queue:
                status_counts[item.status.value] += 1

            return {
                "total_items": len(self._queue),
                "pending": status_counts["pending"],
                "in_progress": status_counts["in_progress"],
                "completed": status_counts["completed"],
                "failed": status_counts["failed"],
                "skipped": status_counts["skipped"],
                "daily_spent": self._daily_spent,
                "daily_limit": self.daily_limit,
                "remaining_budget": self.daily_limit - self._daily_spent,
            }

    def clear_completed(self):
        """Remove completed and failed items from queue."""
        with self._lock:
            terminal_statuses = {QueueItemStatus.COMPLETED, QueueItemStatus.FAILED, QueueItemStatus.SKIPPED}
            removed_urls = [
                item.doc_url for item in self._queue
                if item.status in terminal_statuses
            ]
            self._queue = [item for item in self._queue if item.status not in terminal_statuses]
            for url in removed_urls:
                self._url_set.discard(url)

            logger.info(f"Cleared {len(removed_urls)} completed/failed items from queue")

    def persist_to_db(self, conn):
        """Persist queue state to database.

        Args:
            conn: Database connection
        """
        with self._lock:
            for item in self._queue:
                conn.execute("""
                    INSERT OR REPLACE INTO doc_download_queue
                    (id, court_code, case_number, doc_url, priority, estimated_cost,
                     trigger_name, rss_item_id, status, error_message, created_at,
                     completed_at, actual_cost)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.id,
                    item.court_code,
                    item.case_number,
                    item.doc_url,
                    item.priority,
                    item.estimated_cost,
                    item.trigger_name,
                    item.rss_item_id,
                    item.status.value,
                    item.error_message,
                    item.created_at,
                    item.completed_at,
                    item.actual_cost,
                ))
            logger.debug(f"Persisted {len(self._queue)} queue items to database")

    def load_from_db(self, conn):
        """Load queue state from database.

        Args:
            conn: Database connection
        """
        with self._lock:
            cursor = conn.execute("""
                SELECT id, court_code, case_number, doc_url, priority, estimated_cost,
                       trigger_name, rss_item_id, status, error_message, created_at,
                       completed_at, actual_cost
                FROM doc_download_queue
                WHERE status IN ('pending', 'in_progress')
                ORDER BY priority DESC, created_at ASC
            """)

            self._queue.clear()
            self._url_set.clear()

            for row in cursor.fetchall():
                item = QueueItem(
                    id=row[0],
                    court_code=row[1],
                    case_number=row[2],
                    doc_url=row[3],
                    priority=row[4],
                    estimated_cost=row[5],
                    trigger_name=row[6],
                    rss_item_id=row[7],
                    status=QueueItemStatus(row[8]),
                    error_message=row[9],
                    created_at=row[10],
                    completed_at=row[11],
                    actual_cost=row[12],
                )
                self._queue.append(item)
                self._url_set.add(item.doc_url)

            self._queue.sort()
            logger.info(f"Loaded {len(self._queue)} pending items from database")


# Module-level singleton
_queue: Optional[DocumentQueue] = None


def get_document_queue(daily_limit: Optional[float] = None) -> DocumentQueue:
    """Get or create the document queue singleton.

    Args:
        daily_limit: Optional daily spending limit (only used on first call)
    """
    global _queue
    if _queue is None:
        import os
        limit = daily_limit or float(os.getenv('DOC_DISCOVERY_DAILY_LIMIT', '10.00'))
        _queue = DocumentQueue(daily_limit=limit)
    return _queue
