"""Authentication utilities for PACER authentication.

Provides exponential backoff for retries and authentication validation.
"""
import time
import random
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, Any, Dict, List
from datetime import datetime

# Configure module logger
logger = logging.getLogger(__name__)


@dataclass
class AuthError:
    """Structured authentication error information."""
    strategy: str
    error_type: str
    message: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    details: Optional[Dict[str, Any]] = None


class ExponentialBackoff:
    """Exponential backoff calculator for retry delays.

    Implements exponential backoff with jitter to prevent thundering herd.
    """

    def __init__(
        self,
        initial_delay: float = 2.0,
        max_delay: float = 30.0,
        multiplier: float = 2.0,
        jitter: float = 0.1
    ):
        """Initialize backoff calculator.

        Args:
            initial_delay: Starting delay in seconds (default 2s)
            max_delay: Maximum delay in seconds (default 30s)
            multiplier: Delay multiplier per attempt (default 2x)
            jitter: Random jitter factor (0-1) to add variance (default 0.1)
        """
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.jitter = jitter
        self._attempt = 0

    def reset(self):
        """Reset attempt counter."""
        self._attempt = 0

    def next_delay(self) -> float:
        """Calculate next delay and increment attempt counter.

        Returns:
            Delay in seconds before next retry attempt
        """
        delay = min(
            self.initial_delay * (self.multiplier ** self._attempt),
            self.max_delay
        )

        # Add jitter
        jitter_range = delay * self.jitter
        delay += random.uniform(-jitter_range, jitter_range)
        delay = max(0.1, delay)  # Ensure minimum delay

        self._attempt += 1
        return delay

    @property
    def attempt(self) -> int:
        """Current attempt number (0-indexed)."""
        return self._attempt

    def wait(self):
        """Calculate delay and sleep for that duration.

        Returns:
            The delay that was waited
        """
        delay = self.next_delay()
        logger.debug(f"Backoff waiting {delay:.2f}s (attempt {self._attempt})")
        time.sleep(delay)
        return delay


class AuthValidator:
    """Validates PACER authentication by making test requests."""

    # URLs that require authentication and can be used for validation
    TEST_URLS = {
        'ecf': 'https://ecf.{court_code}.uscourts.gov/cgi-bin/DktRpt.pl',
        'pacer': 'https://pcl.uscourts.gov/pcl/pages/welcome.jsf',
    }

    # Indicators that we're still on a login page (auth failed)
    LOGIN_INDICATORS = [
        'pacer: login',
        'login.jsf',
        'loginform',
        'jakarta.faces',
        'please log in',
        'sign in to continue',
        'session has expired',
        'not authorized',
    ]

    # Indicators that we're authenticated
    AUTH_SUCCESS_INDICATORS = [
        'case number',
        'docket report',
        'search criteria',
        'case query',
        'welcome,',
        'my account',
    ]

    def __init__(self, session):
        """Initialize validator with a requests session.

        Args:
            session: requests.Session with potential auth cookies
        """
        self.session = session
        self.last_validation_time: Optional[float] = None
        self.last_validation_result: Optional[bool] = None

    def validate_with_test_request(
        self,
        court_code: str = 'nysd',
        timeout: int = 15
    ) -> Dict[str, Any]:
        """Validate authentication by making a test request.

        Makes a request to a known authenticated endpoint and checks
        whether we get a login page or actual content.

        Args:
            court_code: Court code to test against (default 'nysd')
            timeout: Request timeout in seconds

        Returns:
            Dict with keys:
                - valid: bool indicating if auth is valid
                - reason: str explaining result
                - response_url: final URL after redirects
                - indicators_found: list of auth indicators found
        """
        test_url = self.TEST_URLS['ecf'].format(court_code=court_code)

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; CourtRSS/1.0)',
                'Accept': 'text/html,application/xhtml+xml,*/*',
            }

            response = self.session.get(
                test_url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True
            )

            response_text = response.text.lower()
            final_url = response.url.lower()

            # Check for login page indicators
            login_found = []
            for indicator in self.LOGIN_INDICATORS:
                if indicator in response_text or indicator in final_url:
                    login_found.append(indicator)

            # Check for authenticated content indicators
            auth_found = []
            for indicator in self.AUTH_SUCCESS_INDICATORS:
                if indicator in response_text:
                    auth_found.append(indicator)

            # Determine validity
            if login_found and not auth_found:
                valid = False
                reason = f"Login page detected: {login_found[:3]}"
            elif auth_found:
                valid = True
                reason = f"Authenticated content found: {auth_found[:3]}"
            elif 'login.jsf' in final_url or 'pacer.login' in final_url:
                valid = False
                reason = "Redirected to login page"
            elif response.status_code == 200 and len(response_text) > 1000:
                # Got substantial content without login indicators
                valid = True
                reason = "Received authenticated response"
            else:
                valid = False
                reason = f"Unclear auth state (status={response.status_code})"

            self.last_validation_time = time.time()
            self.last_validation_result = valid

            return {
                'valid': valid,
                'reason': reason,
                'response_url': response.url,
                'status_code': response.status_code,
                'indicators_found': auth_found if valid else login_found,
            }

        except Exception as e:
            logger.error(f"Auth validation request failed: {e}")
            return {
                'valid': False,
                'reason': f"Request failed: {str(e)}",
                'response_url': None,
                'status_code': None,
                'indicators_found': [],
            }

    def is_recently_validated(self, max_age_seconds: float = 300) -> bool:
        """Check if we have a recent successful validation.

        Args:
            max_age_seconds: Maximum age of validation to consider recent

        Returns:
            True if validated successfully within max_age_seconds
        """
        if self.last_validation_time is None or self.last_validation_result is None:
            return False

        age = time.time() - self.last_validation_time
        return self.last_validation_result and age < max_age_seconds


class AuthErrorCollector:
    """Collects and reports authentication errors from multiple strategies."""

    def __init__(self):
        self.errors: List[AuthError] = []

    def add_error(
        self,
        strategy: str,
        error_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """Add an authentication error.

        Args:
            strategy: Name of the auth strategy that failed
            error_type: Type of error (e.g., 'network', 'credentials', 'token_expired')
            message: Human-readable error message
            details: Optional additional details
        """
        error = AuthError(
            strategy=strategy,
            error_type=error_type,
            message=message,
            details=details
        )
        self.errors.append(error)
        logger.warning(f"Auth error [{strategy}]: {error_type} - {message}")

    def has_errors(self) -> bool:
        """Check if any errors have been collected."""
        return len(self.errors) > 0

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all collected errors.

        Returns:
            Dict with error summary and details
        """
        return {
            'error_count': len(self.errors),
            'strategies_tried': list(set(e.strategy for e in self.errors)),
            'error_types': list(set(e.error_type for e in self.errors)),
            'errors': [
                {
                    'strategy': e.strategy,
                    'type': e.error_type,
                    'message': e.message,
                    'timestamp': e.timestamp,
                }
                for e in self.errors
            ],
            'last_error': self.errors[-1].message if self.errors else None,
        }

    def clear(self):
        """Clear all collected errors."""
        self.errors = []


def retry_with_backoff(
    func: Callable,
    max_retries: int = 3,
    backoff: Optional[ExponentialBackoff] = None,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
) -> Any:
    """Execute a function with automatic retry and exponential backoff.

    Args:
        func: Function to execute
        max_retries: Maximum number of retry attempts (default 3)
        backoff: ExponentialBackoff instance (creates default if None)
        on_retry: Optional callback called before each retry with (attempt, exception)

    Returns:
        Result of successful function call

    Raises:
        Last exception if all retries exhausted
    """
    if backoff is None:
        backoff = ExponentialBackoff()
    else:
        backoff.reset()

    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exception = e

            if attempt < max_retries:
                if on_retry:
                    on_retry(attempt, e)

                delay = backoff.wait()
                logger.info(
                    f"Retry {attempt + 1}/{max_retries} after {delay:.2f}s "
                    f"due to: {str(e)[:100]}"
                )
            else:
                logger.error(f"All {max_retries} retries exhausted: {e}")

    raise last_exception
