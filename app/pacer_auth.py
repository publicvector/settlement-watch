"""PACER authentication and docket sheet retrieval"""
import os
import re
import hashlib
import time
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, date
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import math
from requests.cookies import create_cookie

from .models.db import get_conn, insert_charge
from .auth_utils import (
    ExponentialBackoff,
    AuthValidator,
    AuthErrorCollector,
    retry_with_backoff,
)

# Configure module logger
logger = logging.getLogger(__name__)

class PacerClient:
    """Client for authenticated PACER access"""

    def __init__(self):
        self.username = os.environ.get('PACER_USERNAME')
        self.password = os.environ.get('PACER_PASSWORD')
        self.enabled = os.environ.get('PACER_ENABLED', 'false').lower() == 'true'
        self.daily_limit = float(os.environ.get('PACER_DAILY_LIMIT', '10.00'))
        self.monthly_limit = float(os.environ.get('PACER_MONTHLY_LIMIT', '30.00'))
        self.session = requests.Session()
        self.authenticated = False
        # CSO API config
        self.use_cso_api = os.getenv('PACER_USE_CSO_API', 'false').lower() == 'true'
        self.cso_auth_url = os.getenv('PACER_AUTH_URL', 'https://pacer.login.uscourts.gov')
        self.cso_client_code = os.getenv('PACER_CLIENT_CODE')
        self.cso_otp_code = os.getenv('PACER_OTP_CODE')
        self.cso_token: Optional[str] = None
        self.cso_token_time: Optional[float] = None
        # Cache directory for downloaded PDFs
        self.cache_dir = Path(__file__).resolve().parent.parent / "docs"
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        # Default headers
        self._default_headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) CourtRSS/1.0 Safari/605.1.15',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.7',
        }
        self.session.headers.update(self._default_headers)
        # Cookie persistence
        self.cookie_file = Path(__file__).resolve().parent.parent / ".pacer_cookies.json"
        self._load_cookies()

    def is_configured(self) -> bool:
        """Check if PACER credentials are configured"""
        return bool(self.username and self.password and self.enabled)

    def validate_authentication(self, court_code: str = 'nysd') -> Dict[str, Any]:
        """Validate that current session is actually authenticated.

        Makes a test request to verify authentication works, rather than
        just checking for cookies or login response keywords.

        Args:
            court_code: Court to test against

        Returns:
            Dict with 'valid' bool and 'reason' explanation
        """
        validator = AuthValidator(self.session)
        result = validator.validate_with_test_request(court_code)

        if result['valid']:
            logger.info(f"Authentication validated: {result['reason']}")
            self.authenticated = True
        else:
            logger.warning(f"Authentication invalid: {result['reason']}")
            self.authenticated = False

        return result

    def _log_auth_error(
        self,
        strategy: str,
        error: Exception,
        context: Optional[Dict[str, Any]] = None
    ):
        """Log authentication errors in a structured format.

        Args:
            strategy: Authentication strategy that failed
            error: The exception that occurred
            context: Optional additional context
        """
        error_info = {
            'strategy': strategy,
            'error_type': type(error).__name__,
            'message': str(error),
            'timestamp': datetime.utcnow().isoformat(),
        }
        if context:
            error_info['context'] = context

        logger.error(f"Auth error [{strategy}]: {error_info}")

    def authenticate_with_priority(
        self,
        court_code: str,
        appurl: Optional[str] = None
    ) -> Dict[str, Any]:
        """Authenticate using strategies in priority order.

        Tries authentication methods in order of preference:
        1. CSO token (fastest, bypasses MFA)
        2. Stored cookies (if valid and not expired)
        3. Web login form
        4. Browser login (Playwright, if available)

        Args:
            court_code: Court code to authenticate for
            appurl: Optional return URL after auth

        Returns:
            Dict with 'success' bool and details about which strategy worked
        """
        error_collector = AuthErrorCollector()
        strategies_tried = []

        # Strategy 1: CSO Token
        if self.use_cso_api:
            strategies_tried.append('cso_token')
            try:
                if self._ensure_cso_token():
                    # Validate the token works
                    validation = self.validate_authentication(court_code)
                    if validation['valid']:
                        return {
                            'success': True,
                            'strategy': 'cso_token',
                            'message': 'Authenticated via CSO token',
                        }
                    else:
                        error_collector.add_error(
                            'cso_token',
                            'validation_failed',
                            f"CSO token obtained but validation failed: {validation['reason']}"
                        )
            except Exception as e:
                self._log_auth_error('cso_token', e)
                error_collector.add_error('cso_token', 'exception', str(e))

        # Strategy 2: Stored Cookies
        strategies_tried.append('stored_cookies')
        if self._has_valid_session_cookies():
            try:
                validation = self.validate_authentication(court_code)
                if validation['valid']:
                    return {
                        'success': True,
                        'strategy': 'stored_cookies',
                        'message': 'Authenticated via stored cookies',
                    }
                else:
                    error_collector.add_error(
                        'stored_cookies',
                        'validation_failed',
                        f"Cookies exist but validation failed: {validation['reason']}"
                    )
            except Exception as e:
                self._log_auth_error('stored_cookies', e)
                error_collector.add_error('stored_cookies', 'exception', str(e))

        # Strategy 3: Web Login with Retry
        strategies_tried.append('web_login')
        backoff = ExponentialBackoff(initial_delay=2.0, max_delay=30.0)

        for attempt in range(3):
            try:
                if self.authenticate(court_code, appurl):
                    validation = self.validate_authentication(court_code)
                    if validation['valid']:
                        return {
                            'success': True,
                            'strategy': 'web_login',
                            'message': f'Authenticated via web login (attempt {attempt + 1})',
                        }
                    else:
                        error_collector.add_error(
                            'web_login',
                            'validation_failed',
                            f"Login succeeded but validation failed (attempt {attempt + 1})"
                        )
                else:
                    error_collector.add_error(
                        'web_login',
                        'login_failed',
                        f"Login returned False (attempt {attempt + 1})"
                    )
            except Exception as e:
                self._log_auth_error('web_login', e, {'attempt': attempt + 1})
                error_collector.add_error('web_login', 'exception', str(e))

            if attempt < 2:  # Don't wait after last attempt
                backoff.wait()

        # Strategy 4: Browser Login (if available)
        strategies_tried.append('browser_login')
        try:
            from .pacer_browser_login import browser_login
            if browser_login(self, court_code):
                validation = self.validate_authentication(court_code)
                if validation['valid']:
                    return {
                        'success': True,
                        'strategy': 'browser_login',
                        'message': 'Authenticated via browser login',
                    }
        except ImportError:
            error_collector.add_error(
                'browser_login',
                'not_available',
                'Browser login module not available'
            )
        except Exception as e:
            self._log_auth_error('browser_login', e)
            error_collector.add_error('browser_login', 'exception', str(e))

        # All strategies failed
        return {
            'success': False,
            'strategy': None,
            'message': 'All authentication strategies failed',
            'strategies_tried': strategies_tried,
            'errors': error_collector.get_summary(),
        }

    def _has_valid_session_cookies(self) -> bool:
        """Check if session has valid, non-expired PACER cookies.

        Returns:
            True if valid session cookies exist
        """
        now = time.time()
        pacer_domains = ['.uscourts.gov', 'pacer.uscourts.gov']

        valid_cookies = 0
        for cookie in self.session.cookies:
            # Check domain
            if not any(d in (cookie.domain or '') for d in pacer_domains):
                continue

            # Check expiration
            if cookie.expires and cookie.expires < now:
                logger.debug(f"Cookie {cookie.name} expired at {cookie.expires}")
                continue

            # Count valid session-related cookies
            if cookie.name in ['NextGenCSO', 'PacerSession', 'JSESSIONID', 'PacerUser']:
                valid_cookies += 1

        return valid_cookies > 0

    def check_spending_limits(self) -> Dict[str, Any]:
        """Check current spending against limits"""
        conn = get_conn()

        # Today's spending
        today = date.today().isoformat()
        daily_cur = conn.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) as total FROM pacer_charges WHERE DATE(created_at) = ?",
            (today,)
        )
        daily_spent = daily_cur.fetchone()[0]

        # This month's spending
        month_start = date.today().replace(day=1).isoformat()
        monthly_cur = conn.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) as total FROM pacer_charges WHERE created_at >= ?",
            (month_start,)
        )
        monthly_spent = monthly_cur.fetchone()[0]

        return {
            "daily_spent": daily_spent,
            "daily_limit": self.daily_limit,
            "daily_remaining": self.daily_limit - daily_spent,
            "monthly_spent": monthly_spent,
            "monthly_limit": self.monthly_limit,
            "monthly_remaining": self.monthly_limit - monthly_spent,
            "can_proceed": daily_spent < self.daily_limit and monthly_spent < self.monthly_limit
        }

    def authenticate(self, court_code: str, appurl: Optional[str] = None) -> bool:
        """Authenticate with PACER for a specific court"""
        if not self.is_configured():
            return False

        try:
            # Build the return URL for court-specific authentication
            docket_url = appurl or f"https://ecf.{court_code}.uscourts.gov/cgi-bin/DktRpt.pl"
            base = court_code.upper()
            if base.endswith('D'):
                base = base[:-1]
            court_id = f"{base}DC" if court_code != "cacd" else "CACDC"

            # PACER login URL with court-specific return (include appurl if provided)
            from urllib.parse import quote
            login_url = f"https://pacer.login.uscourts.gov/csologin/login.jsf?pscCourtId={court_id}"
            if docket_url:
                login_url += f"&appurl={quote(docket_url, safe='') }"

            # Get login page
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; CourtRSS/1.0)',
            }
            response = self.session.get(login_url, headers=headers, allow_redirects=True)
            soup = BeautifulSoup(response.text, 'html.parser')

            # Find the login form
            form = soup.find('form')
            if not form:
                print("Could not find login form")
                return False

            # Extract form tokens and action URL
            form_data = {
                'login': self.username,
                'password': self.password,
            }

            # Find hidden form fields (including ViewState)
            for hidden in soup.find_all('input', type='hidden'):
                name = hidden.get('name')
                value = hidden.get('value')
                if name and value:
                    form_data[name] = value

            # Find the submit button name/value
            submit_button = soup.find('input', {'type': 'submit'})
            if submit_button and submit_button.get('name'):
                form_data[submit_button.get('name')] = submit_button.get('value', '')

            # Get form action URL
            action = form.get('action')
            if action and not action.startswith('http'):
                action = f"https://pacer.login.uscourts.gov{action}" if action.startswith('/') else login_url

            # Submit login form
            print(f"Logging in to PACER as {self.username}...")
            login_headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; CourtRSS/1.0)',
                'Origin': 'https://pacer.login.uscourts.gov',
                'Referer': login_url,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            login_response = self.session.post(action or login_url, data=form_data, headers=login_headers, allow_redirects=True)

            # Check if login was successful - use more robust validation
            response_text = login_response.text.lower()
            final_url = login_response.url.lower()

            # Check for login page indicators (meaning we're still on login page)
            login_indicators = ['pacer: login', 'login.jsf', 'loginform', 'sign in']
            still_on_login = any(ind in response_text or ind in final_url for ind in login_indicators)

            # Check for authenticated content indicators
            auth_indicators = ['logout', 'sign out', 'logged in', 'welcome', 'case number', 'docket']
            has_auth_content = any(ind in response_text for ind in auth_indicators)

            self.authenticated = has_auth_content and not still_on_login

            if not self.authenticated:
                logger.warning(f"Authentication failed for {court_code}")
                logger.debug(f"Response URL: {login_response.url}")
                logger.debug(f"Response preview: {login_response.text[:500]}")
            else:
                logger.info(f"Authentication successful for {court_code}")

            return self.authenticated

        except Exception as e:
            self._log_auth_error('web_login', e, {'court_code': court_code})
            logger.error(f"PACER authentication error: {e}", exc_info=True)
            return False

    def login_with_login_url(self, login_url: str) -> bool:
        """Perform CSO login using an explicit login.jsf URL (with pscCourtId and appurl)."""
        try:
            if not self.is_configured():
                return False
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; CourtRSS/1.0)'}
            r = self.session.get(login_url, headers=headers, allow_redirects=True)
            soup = BeautifulSoup(r.text, 'html.parser')
            form = soup.find('form')
            if not form:
                return False
            form_data = {}
            # Username/password fields
            username_field = (soup.find('input', {'id': 'loginForm:loginName'}) or
                              soup.find('input', attrs={'name': lambda x: x and 'loginName' in x}))
            password_field = (soup.find('input', {'id': 'loginForm:password'}) or
                              soup.find('input', attrs={'name': lambda x: x and 'password' in x and 'loginForm' in x}))
            if username_field and username_field.get('name'):
                form_data[username_field.get('name')] = self.username
            else:
                form_data['loginForm:loginName'] = self.username
            if password_field and password_field.get('name'):
                form_data[password_field.get('name')] = self.password
            else:
                form_data['loginForm:password'] = self.password
            # Hidden fields (e.g., ViewState)
            for hidden in soup.find_all('input', type='hidden'):
                name = hidden.get('name')
                value = hidden.get('value')
                if name and value is not None:
                    form_data[name] = value
            # Submit button
            form_data['loginForm:fbtnLogin'] = 'loginForm:fbtnLogin'
            action = form.get('action')
            if action and not action.startswith('http'):
                action = f"https://pacer.login.uscourts.gov{action}" if action.startswith('/') else login_url
            login_headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; CourtRSS/1.0)',
                'Origin': 'https://pacer.login.uscourts.gov',
                'Referer': login_url,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            resp = self.session.post(action or login_url, data=form_data, headers=login_headers, allow_redirects=True)
            self.authenticated = ('PACER: Login' not in resp.text)
            if self.authenticated:
                self._save_cookies()
            return self.authenticated
        except Exception as e:
            print(f"login_with_login_url error: {e}")
            return False

    def _lookup_pacer_case_id(self, court_code: str, case_number: str) -> Optional[str]:
        """Look up the PACER case ID from RSS items or by searching."""
        conn = get_conn()
        # Try to find case ID from existing RSS item links
        cur = conn.execute("""
            SELECT link FROM rss_items
            WHERE court_code = ? AND case_number = ?
            LIMIT 1
        """, (court_code, case_number))
        row = cur.fetchone()
        if row:
            link = row[0] if isinstance(row, tuple) else row['link']
            # Extract case ID from link like https://ecf.cand.uscourts.gov/cgi-bin/DktRpt.pl?428464
            match = re.search(r'DktRpt\.pl\?(\d+)', link)
            if match:
                return match.group(1)
        return None

    def _fetch_docket_with_cso(self, court_code: str, case_number: str) -> Optional[Dict[str, Any]]:
        """Fetch docket with attorney info using CSO token authentication."""
        try:
            # Look up PACER case ID
            case_id = self._lookup_pacer_case_id(court_code, case_number)
            if not case_id:
                print(f"Could not find PACER case ID for {court_code} {case_number}")
                return None

            # Set up session with CSO token and Referer header
            self._apply_cso_token(f"https://ecf.{court_code}.uscourts.gov")
            self.session.headers['Referer'] = 'https://external'  # Bypasses CSRF check

            # Step 1: Get the docket report form page
            form_url = f"https://ecf.{court_code}.uscourts.gov/cgi-bin/DktRpt.pl?{case_id}"
            resp1 = self.session.get(form_url)

            if resp1.status_code != 200:
                print(f"Failed to get docket form: HTTP {resp1.status_code}")
                return None

            # Step 2: Parse form and extract action URL
            soup1 = BeautifulSoup(resp1.text, 'html.parser')
            form = soup1.find('form')
            if not form:
                print("No form found on docket page")
                return None

            action = form.get('action', '')
            # Convert relative URL: ../cgi-bin/DktRpt.pl?xxx -> full URL
            if action.startswith('../'):
                action_url = f"https://ecf.{court_code}.uscourts.gov/{action[3:]}"
            elif action.startswith('/'):
                action_url = f"https://ecf.{court_code}.uscourts.gov{action}"
            else:
                action_url = action

            # Step 3: Submit form with parties and counsel option
            form_data = {
                'all_case_ids': case_id,
                f'CaseNum_{case_id}': 'on',
                'list_of_parties_and_counsel': 'on',  # Include attorneys
                'terminated_parties': 'on',
                'output_format': 'html',
                'sort1': 'oldest date first',
            }

            resp2 = self.session.post(action_url, data=form_data)

            if resp2.status_code != 200:
                print(f"Failed to get docket: HTTP {resp2.status_code}")
                return None

            # Step 4: Parse the docket sheet
            soup2 = BeautifulSoup(resp2.text, 'html.parser')
            case_info = self._parse_docket_html(soup2, court_code, case_number)

            # Step 5: Record the charge
            pages = self._estimate_pages(soup2)
            cost = min(pages * 0.10, 3.00)

            charge_id = hashlib.sha256(f"{court_code}-{case_number}-{datetime.utcnow().isoformat()}".encode()).hexdigest()
            insert_charge({
                "id": charge_id,
                "case_id": case_number,
                "court_code": court_code,
                "resource": "docket_sheet",
                "cmecf_url": form_url,
                "pages_billed": pages,
                "amount_usd": cost,
                "api_key_id": self.username,
                "triggered_by": "api_fetch_cso",
                "created_at": datetime.utcnow().isoformat()
            })

            return case_info

        except Exception as e:
            print(f"Error fetching docket with CSO: {e}")
            import traceback
            traceback.print_exc()
            return None

    def fetch_docket_sheet(self, court_code: str, case_number: str) -> Optional[Dict[str, Any]]:
        """
        Fetch docket sheet for a case with attorney information.

        Returns:
            Dict with case info, docket entries, and attorneys, or None if failed
        """
        if not self.is_configured():
            return None

        # Check spending limits
        limits = self.check_spending_limits()
        if not limits['can_proceed']:
            print(f"Spending limit reached. Daily: ${limits['daily_spent']:.2f}, Monthly: ${limits['monthly_spent']:.2f}")
            return None

        # Try CSO token authentication first (bypasses MFA)
        if self.use_cso_api:
            if self._ensure_cso_token():
                self.authenticated = True
                # Use CSO-based fetch which properly gets attorney info
                return self._fetch_docket_with_cso(court_code, case_number)

        # Fall back to web authentication if CSO not available
        if not self.authenticated:
            if not self.authenticate(court_code):
                return None

        try:
            # Parse case number to get case ID
            docket_url = f"https://ecf.{court_code}.uscourts.gov/cgi-bin/DktRpt.pl"

            # Apply CSO token if available
            if self.use_cso_api and self.cso_token:
                self._apply_cso_token(docket_url)

            # Make request
            response = self.session.get(docket_url, params={'caseid': case_number}, allow_redirects=False)

            # Check if we're being redirected to login
            if response.status_code in (301, 302, 303, 307, 308) or 'login.jsf' in response.text:
                print("Need to authenticate - following login redirect...")

                # Parse the redirect URL from JavaScript or Location header
                redirect_url = response.headers.get('Location')

                if not redirect_url:
                    # Try to extract from JavaScript redirect
                    import re
                    match = re.search(r'location\.assign\(["\']([^"\']+)["\']', response.text)
                    if match:
                        redirect_url = match.group(1)

                if redirect_url and 'login.jsf' in redirect_url:
                    # Perform authentication through the redirect
                    print(f"Following login redirect: {redirect_url[:100]}...")

                    # Get login page
                    login_response = self.session.get(redirect_url)
                    soup = BeautifulSoup(login_response.text, 'html.parser')

                    # Find login form
                    form = soup.find('form')
                    if not form:
                        print("Could not find login form in redirect")
                        return None

                    # Debug: Find all input fields
                    print("Login form fields:")
                    for input_field in soup.find_all('input'):
                        print(f"  - {input_field.get('name')}: type={input_field.get('type')}, id={input_field.get('id')}")

                    # Build form data - PACER uses specific field names
                    form_data = {}

                    # PACER uses Jakarta Faces with specific field names
                    # Look for loginForm:loginName and loginForm:password
                    username_field = (soup.find('input', {'id': 'loginForm:loginName'}) or
                                    soup.find('input', attrs={'name': lambda x: x and 'loginName' in x}))
                    password_field = (soup.find('input', {'id': 'loginForm:password'}) or
                                    soup.find('input', attrs={'name': lambda x: x and 'password' in x and 'loginForm' in x}))

                    if username_field and username_field.get('name'):
                        form_data[username_field.get('name')] = self.username
                        print(f"Using username field: {username_field.get('name')}")
                    else:
                        print("WARNING: Could not find username field!")
                        form_data['loginForm:loginName'] = self.username

                    if password_field and password_field.get('name'):
                        form_data[password_field.get('name')] = self.password
                        print(f"Using password field: {password_field.get('name')}")
                    else:
                        print("WARNING: Could not find password field!")
                        form_data['loginForm:password'] = self.password

                    # Extract hidden fields
                    for hidden in soup.find_all('input', type='hidden'):
                        name = hidden.get('name')
                        value = hidden.get('value')
                        if name and value:
                            form_data[name] = value

                    # Add the login submit button - PACER requires this specific field
                    # The login button is: <button type="submit" name="loginForm:fbtnLogin">Login</button>
                    form_data['loginForm:fbtnLogin'] = 'loginForm:fbtnLogin'

                    # Get form action
                    action = form.get('action')
                    if action and not action.startswith('http'):
                        action = f"https://pacer.login.uscourts.gov{action}" if action.startswith('/') else redirect_url

                    # Submit login and follow redirects back to docket
                    print(f"Submitting login credentials to: {action or redirect_url}")
                    print(f"Form data keys: {list(form_data.keys())}")
                    response = self.session.post(action or redirect_url, data=form_data, allow_redirects=True)

                    # Debug: Save response for inspection
                    print(f"\nLogin response status: {response.status_code}")
                    print(f"Login response URL: {response.url}")
                    print(f"Response length: {len(response.text)} chars")

                    # Check if login succeeded
                    if 'PACER: Login' in response.text:
                        # Still on login page - check for error messages
                        soup_error = BeautifulSoup(response.text, 'html.parser')
                        error_msgs = soup_error.find_all(class_=['error', 'ui-message-error', 'alert-danger', 'ui-messages-error'])

                        # Also check for message text
                        all_msgs = soup_error.find_all('li', class_='ui-messages-error-detail')

                        if error_msgs or all_msgs:
                            print(f"Login error messages found:")
                            for msg in (error_msgs + all_msgs):
                                print(f"  - {msg.text.strip()}")
                        else:
                            print("Login failed - credentials may be incorrect or account may need verification")
                            # Save response for debugging
                            with open('pacer_login_error.html', 'w') as f:
                                f.write(response.text)
                            print(f"  Full response saved to: pacer_login_error.html")
                            # Look for any visible text that might indicate the error
                            visible_text = ' '.join(soup_error.stripped_strings)
                            if 'invalid' in visible_text.lower() or 'incorrect' in visible_text.lower():
                                print(f"\n  Possible error in page: Found 'invalid' or 'incorrect' in visible text")
                        print("Please verify your PACER username and password at https://pacer.uscourts.gov")
                        return None

            self.authenticated = True
            if self.authenticated:
                self._save_cookies()

            if response.status_code != 200:
                print(f"PACER returned status {response.status_code}")
                return None

            # Debug: Print first 1000 chars of response
            print(f"PACER Response preview: {response.text[:1000]}")

            # Check if still getting login redirect
            if 'login.jsf' in response.text and 'location.assign' in response.text:
                print("Still getting login redirect - authentication may have failed")
                print("Please verify your PACER credentials are correct")
                return None

            # Parse docket sheet
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract case information
            case_info = self._parse_docket_html(soup, court_code, case_number)

            # Estimate and record cost (docket sheets are typically 1-3 pages)
            pages = self._estimate_pages(soup)
            cost = min(pages * 0.10, 3.00)  # Capped at $3

            # Record charge
            charge_id = hashlib.sha256(f"{court_code}-{case_number}-{datetime.utcnow().isoformat()}".encode()).hexdigest()
            insert_charge({
                "id": charge_id,
                "case_id": case_number,
                "court_code": court_code,
                "resource": "docket_sheet",
                "cmecf_url": docket_url,
                "pages_billed": pages,
                "amount_usd": cost,
                "api_key_id": self.username,
                "triggered_by": "api_fetch",
                "created_at": datetime.utcnow().isoformat()
            })

            # Save cookies post-fetch in case site set new tokens
            self._save_cookies()
            return case_info

        except Exception as e:
            print(f"Error fetching docket sheet: {e}")
            return None

    def fetch_docket_by_id(self, court_code: str, pacer_case_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch docket sheet using a known PACER case ID directly.

        This skips the case ID lookup step when you already have the ID
        (e.g., from an RSS link like DktRpt.pl?487965).

        Args:
            court_code: Court identifier (e.g., 'ilnd')
            pacer_case_id: Numeric PACER case ID (e.g., '487965')

        Returns:
            Dict with case info, docket entries, and attorneys, or None if failed
        """
        if not self.is_configured():
            return None

        # Check spending limits
        limits = self.check_spending_limits()
        if not limits['can_proceed']:
            print(f"Spending limit reached. Daily: ${limits['daily_spent']:.2f}, Monthly: ${limits['monthly_spent']:.2f}")
            return None

        # Ensure CSO token
        if self.use_cso_api:
            if not self._ensure_cso_token():
                print("Failed to get CSO token")
                return None

        try:
            # Set up session with CSO token
            self._apply_cso_token(f"https://ecf.{court_code}.uscourts.gov")
            self.session.headers['Referer'] = 'https://external'

            # Step 1: Get the docket report form page
            form_url = f"https://ecf.{court_code}.uscourts.gov/cgi-bin/DktRpt.pl?{pacer_case_id}"
            resp1 = self.session.get(form_url)

            if resp1.status_code != 200:
                print(f"Failed to get docket form: HTTP {resp1.status_code}")
                return None

            # Step 2: Parse form and extract action URL
            soup1 = BeautifulSoup(resp1.text, 'html.parser')
            form = soup1.find('form')
            if not form:
                print("No form found on docket page")
                return None

            action = form.get('action', '')
            if action.startswith('../'):
                action_url = f"https://ecf.{court_code}.uscourts.gov/{action[3:]}"
            elif action.startswith('/'):
                action_url = f"https://ecf.{court_code}.uscourts.gov{action}"
            else:
                action_url = action

            # Step 3: Submit form with parties and counsel option
            form_data = {
                'all_case_ids': pacer_case_id,
                f'CaseNum_{pacer_case_id}': 'on',
                'list_of_parties_and_counsel': 'on',
                'terminated_parties': 'on',
                'output_format': 'html',
                'sort1': 'oldest date first',
            }

            resp2 = self.session.post(action_url, data=form_data)

            if resp2.status_code != 200:
                print(f"Failed to get docket: HTTP {resp2.status_code}")
                return None

            # Step 4: Parse the docket sheet
            soup2 = BeautifulSoup(resp2.text, 'html.parser')
            case_info = self._parse_docket_html(soup2, court_code, pacer_case_id)

            # Step 5: Record the charge
            pages = self._estimate_pages(soup2)
            cost = min(pages * 0.10, 3.00)

            charge_id = hashlib.sha256(f"{court_code}-{pacer_case_id}-{datetime.utcnow().isoformat()}".encode()).hexdigest()
            insert_charge({
                "id": charge_id,
                "case_id": pacer_case_id,
                "court_code": court_code,
                "resource": "docket_sheet",
                "cmecf_url": form_url,
                "pages_billed": pages,
                "amount_usd": cost,
                "api_key_id": self.username,
                "triggered_by": "api_fetch_by_id",
                "created_at": datetime.utcnow().isoformat()
            })

            return case_info

        except Exception as e:
            print(f"Error fetching docket by ID: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _parse_docket_html(self, soup: BeautifulSoup, court_code: str, case_number: str) -> Dict[str, Any]:
        """Parse docket sheet HTML"""

        # Extract case title
        title_elem = soup.find('h2') or soup.find('h3')
        title = title_elem.text.strip() if title_elem else "Unknown"

        # Extract judge
        judge = None
        for text in soup.stripped_strings:
            if 'assigned to' in text.lower() or 'judge' in text.lower():
                match = re.search(r'(?:Judge|Magistrate Judge)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', text)
                if match:
                    judge = match.group(1)
                    break

        # Extract docket entries from table
        # Find the table with docket entries (has "Date Filed", "#", "Docket Text" headers)
        entries = []
        docket_table = None

        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            if rows:
                first_row = rows[0]
                cells = first_row.find_all(['td', 'th'])
                header_text = ' '.join(c.text.strip().lower() for c in cells)
                if 'date filed' in header_text and 'docket text' in header_text:
                    docket_table = table
                    break

        if docket_table:
            rows = docket_table.find_all('tr')[1:]  # Skip header
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 3:
                    # Columns are: Date Filed, #, Docket Text
                    filed_date = cols[0].text.strip()
                    entry_no = cols[1].text.strip()
                    description = cols[2].text.strip()

                    if filed_date and description:  # Skip empty rows
                        entries.append({
                            "entry_number": entry_no,
                            "filed_date": filed_date,
                            "text": description  # Use 'text' to match classify_docket_outcome
                        })

        # Extract attorneys and parties
        attorneys = self._extract_attorneys(soup)

        return {
            "court_code": court_code,
            "case_number": case_number,
            "title": title,
            "judge": judge,
            "entries": entries,
            "entry_count": len(entries),
            "attorneys": attorneys
        }

    def _extract_attorneys(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Extract attorney information from PACER docket sheet HTML.

        PACER docket sheets have attorney info in table cells where:
        - One cell contains 'represented by'
        - The next cell contains attorney name in <b> tags, followed by firm/contact info
        """
        attorneys = []
        current_party_type = None

        # Track party type from table structure
        for td in soup.find_all('td'):
            text = td.get_text().lower()
            # Track current party type
            if 'plaintiff' in text:
                current_party_type = 'Plaintiff'
            elif 'defendant' in text:
                current_party_type = 'Defendant'
            elif 'petitioner' in text:
                current_party_type = 'Petitioner'
            elif 'respondent' in text:
                current_party_type = 'Respondent'

            # Look for 'represented by' cells
            if 'represented' in text and 'by' in text:
                # Next sibling cell should have attorney info
                next_td = td.find_next_sibling('td')
                if next_td:
                    # Get attorney name from bold tag
                    bold = next_td.find('b')
                    name = bold.get_text().strip() if bold else None

                    if name:
                        # Clean up name (remove extra whitespace)
                        name = ' '.join(name.split())

                        # Get firm and email from cell content
                        full_text = next_td.get_text(separator='|').split('|')
                        firm = None
                        email = None
                        phone = None

                        for line in full_text[1:]:  # Skip first item (name)
                            line = line.strip()
                            if not line:
                                continue
                            if '@' in line:
                                email = line.replace('Email:', '').replace('&#064;', '@').strip()
                            elif any(x in line for x in ['LLP', 'LLC', 'PC', 'P.C.', 'Law', 'PLLC', 'L.L.C.', 'Firm', 'Office', '& ']):
                                if not firm:
                                    firm = line
                            elif re.match(r'^\d{3}[.-]?\d{3}[.-]?\d{4}', line):
                                phone = line

                        attorneys.append({
                            'name': name,
                            'firm': firm,
                            'party_name': None,
                            'party_type': current_party_type,
                            'email': email,
                            'phone': phone
                        })

        # Deduplicate by name
        seen_names = set()
        unique_attorneys = []
        for atty in attorneys:
            name = atty.get('name', '').lower().strip()
            if name and name not in seen_names:
                seen_names.add(name)
                unique_attorneys.append(atty)

        return unique_attorneys

    def _estimate_pages(self, soup: BeautifulSoup) -> int:
        """Estimate number of pages for billing"""
        # Count docket entries (roughly 20 per page)
        docket_table = soup.find('table')
        if docket_table:
            rows = len(docket_table.find_all('tr'))
            pages = max(1, (rows // 20) + 1)
            return min(pages, 30)  # Cap at $3 worth
        return 1

    def _estimate_pdf_pages_from_size(self, num_bytes: int) -> int:
        # Rough heuristic: ~50KB per page, cap at 30 pages ($3)
        if not num_bytes or num_bytes <= 0:
            return 1
        pages = max(1, math.ceil(num_bytes / 50_000))
        return min(pages, 30)

    def _ensure_cso_token(self) -> bool:
        if not self.use_cso_api:
            return False
        # Refresh if missing or older than 30 minutes (token expiry specifics vary)
        if self.cso_token and self.cso_token_time and (time.time() - self.cso_token_time) < 1800:
            return True
        try:
            from .cso_api import cso_authenticate
            res = cso_authenticate(
                auth_base_url=self.cso_auth_url,
                login_id=self.username,
                password=self.password,
                client_code=self.cso_client_code,
                otp_code=self.cso_otp_code,
                redact_flag=1,  # Acknowledge redaction rules to get token
            )
            if res.get('ok') and res.get('token'):
                self.cso_token = res['token']
                self.cso_token_time = time.time()
                print(f"CSO token obtained successfully")
                return True
            else:
                print(f"CSO auth failed: {res.get('error') or res.get('raw')}")
        except Exception as e:
            print(f"CSO auth exception: {e}")
        self.cso_token = None
        self.cso_token_time = None
        return False

    def _apply_cso_token(self, url: str):
        """Attach CSO token to session for both headers and cookies generically."""
        if not (self.use_cso_api and self.cso_token):
            return
        try:
            # Generic headers many services accept
            self.session.headers.update({
                'nextGenCSO': self.cso_token,
                'X-NextGenCSO': self.cso_token,
                'Authorization': f'Bearer {self.cso_token}',
            })
        except Exception:
            pass
        try:
            # Also set a cookie for broad *.uscourts.gov scope
            from requests.cookies import create_cookie
            ck = create_cookie(name='NextGenCSO', value=self.cso_token, domain='.uscourts.gov', path='/')
            self.session.cookies.set_cookie(ck)
            # Host-specific cookie
            host = urlparse(url).netloc
            ck2 = create_cookie(name='NextGenCSO', value=self.cso_token, domain=host, path='/')
            self.session.cookies.set_cookie(ck2)
        except Exception:
            pass

    def _parse_doc_url(self, doc_url: str) -> Dict[str, Optional[str]]:
        try:
            u = urlparse(doc_url)
            qs = parse_qs(u.query or "")
            path_parts = (u.path or "").strip("/").split("/")
            doc_id = path_parts[1] if len(path_parts) >= 2 and path_parts[0] == 'doc1' else None
            caseid = (qs.get('caseid') or [None])[0]
            de_seq_num = (qs.get('de_seq_num') or [None])[0]
            return {"host": u.netloc, "doc_id": doc_id, "caseid": caseid, "de_seq_num": de_seq_num}
        except Exception:
            return {"host": None, "doc_id": None, "caseid": None, "de_seq_num": None}

    def fetch_document(self, court_code: str, doc_url: str) -> Optional[Dict[str, Any]]:
        """Fetch a document PDF from a doc1 URL, cache locally, and record estimated cost.

        Returns a dict: { path, filename, cached, pages_billed, amount_usd }
        """
        if not self.is_configured():
            return None

        # Parse identifiers from URL
        parts = self._parse_doc_url(doc_url)
        # Prefer court code derived from doc_url host if input seems invalid
        host_cc = None
        host_str = None
        try:
            host_str = parts.get('host') or urlparse(doc_url).netloc
            if host_str and host_str.startswith('ecf.') and host_str.endswith('.uscourts.gov'):
                host_cc = host_str.split('.')[1]
        except Exception:
            host_cc = None
        if host_cc and (not court_code or not court_code.isalpha()):
            court_code = host_cc

        doc_id = parts.get('doc_id') or hashlib.sha256(doc_url.encode()).hexdigest()[:16]
        court_dir = self.cache_dir / court_code
        try:
            court_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        filename = f"{doc_id}.pdf"
        file_path = court_dir / filename

        # Serve from cache if present
        try:
            if file_path.exists() and file_path.stat().st_size > 0:
                # Validate cache is a real PDF; if not, remove and re-fetch
                is_pdf = False
                try:
                    with open(file_path, 'rb') as f:
                        sig = f.read(5)
                        is_pdf = sig == b'%PDF-'
                except Exception:
                    is_pdf = False
                if is_pdf:
                    return {
                        "path": str(file_path),
                        "filename": filename,
                        "cached": True,
                        "pages_billed": 0,
                        "amount_usd": 0.00,
                    }
                else:
                    try:
                        file_path.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            pass

        # Spending limits
        limits = self.check_spending_limits()
        if not limits['can_proceed']:
            print(f"Spending limit reached. Daily: ${limits['daily_spent']:.2f}, Monthly: ${limits['monthly_spent']:.2f}")
            return None

        def is_pdf_response(resp) -> bool:
            try:
                ctype = (resp.headers.get('Content-Type') or '').lower()
                if 'pdf' in ctype:
                    return True
            except Exception:
                pass
            return False

        def download_to(path_url: str):
            hdrs = {
                'User-Agent': 'Mozilla/5.0 (compatible; CourtRSS/1.0)',
                'Accept': 'application/pdf,application/octet-stream;q=0.9,*/*;q=0.8'
            }
            return self.session.get(path_url, headers=hdrs, stream=False, allow_redirects=True)

        try:
            # Build alternative candidate URLs (handles common intermediate flows)
            def add_param(url: str, key: str, value: str):
                from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
                u = urlparse(url)
                qs = parse_qs(u.query)
                if key not in qs:
                    qs[key] = [value]
                new_q = urlencode({k: v[0] if isinstance(v, list) and v else v for k, v in qs.items()})
                return urlunparse((u.scheme, u.netloc, u.path, u.params, new_q, u.fragment))

            candidates = []
            # 1) original
            candidates.append(doc_url)
            # 2) with download=1
            candidates.append(add_param(doc_url, 'download', '1'))
            # 3) with pdf_header=1
            candidates.append(add_param(doc_url, 'pdf_header', '1'))
            # 4) both
            candidates.append(add_param(add_param(doc_url, 'download', '1'), 'pdf_header', '1'))
            # 5) show_temp flow
            if host_str and parts.get('caseid') and parts.get('de_seq_num'):
                show_temp = f"https://{host_str}/show_temp.pl?caseid={parts['caseid']}&de_seq_num={parts['de_seq_num']}&pdf_header=1"
                candidates.append(show_temp)
                # 6) doc1 with both params explicitly
                if parts.get('doc_id'):
                    d1 = f"https://{host_str}/doc1/{parts['doc_id']}?caseid={parts['caseid']}&de_seq_num={parts['de_seq_num']}&download=1&pdf_header=1"
                    candidates.append(d1)

            resp = None
            for url in candidates:
                if self.use_cso_api:
                    # Ensure CSO token and attach to session before the request
                    self._ensure_cso_token()
                    self._apply_cso_token(url)
                resp = download_to(url)
                # If redirected to login, authenticate and retry once for this candidate
                if ('login.jsf' in resp.url) or ('pacer.login.uscourts.gov' in resp.url) or ('PACER: Login' in (resp.text[:500] if hasattr(resp, 'text') else '')):
                    if self.use_cso_api:
                        # Re-attach token and retry without interactive login
                        self._apply_cso_token(url)
                        resp = download_to(url)
                    else:
                        # Try legacy web login flow
                        login_url = None
                        try:
                            import re as _re
                            textfrag = resp.text or ''
                            m = _re.search(r"location\.assign\(['\"]([^'\"]+)['\"]\)", textfrag)
                            if m:
                                login_url = m.group(1)
                        except Exception:
                            login_url = None
                        if login_url:
                            if not self.login_with_login_url(login_url):
                                continue
                        else:
                            if not self.authenticate(court_code, appurl=url):
                                continue
                        resp = download_to(url)
                if resp.status_code == 200:
                    break
            if not resp or resp.status_code != 200:
                print("Document fetch failed after candidates")
                return None
            # If redirected to login, authenticate and retry once
            if ('login.jsf' in resp.url) or ('pacer.login.uscourts.gov' in resp.url) or ('PACER: Login' in (resp.text[:200] if hasattr(resp, 'text') else '')):
                if not self.authenticate(court_code):
                    return None
                resp = download_to(doc_url)

            if resp.status_code != 200:
                print(f"Document fetch failed: HTTP {resp.status_code}")
                return None

            # If not a PDF, try to discover a follow-up doc1 link from HTML
            if not is_pdf_response(resp):
                try:
                    html = resp.text
                    soup = BeautifulSoup(html, 'html.parser')
                    # Find an anchor to /doc1/ with case params
                    a = soup.find('a', href=lambda h: h and '/doc1/' in h)
                    target = None
                    if a and a.get('href'):
                        target = a.get('href')
                    if not target:
                        iframe = soup.find('iframe', src=lambda h: h and '/doc1/' in h)
                        if iframe and iframe.get('src'):
                            target = iframe.get('src')
                    if not target:
                        form = soup.find('form', action=lambda h: h and '/doc1/' in h)
                        if form and form.get('action'):
                            target = form.get('action')
                    if target:
                        if target.startswith('/') and host_str:
                            target = f"https://{host_str}{target}"
                        resp = download_to(target)
                except Exception:
                    pass

            # Save content to disk (non-stream mode)
            tmp_path = file_path.with_suffix('.part')
            with open(tmp_path, 'wb') as f:
                f.write(resp.content or b'')
            size = tmp_path.stat().st_size if tmp_path.exists() else 0

            # Verify PDF signature
            is_pdf = False
            try:
                with open(tmp_path, 'rb') as t:
                    sig = t.read(5)
                    is_pdf = sig == b'%PDF-'
            except Exception:
                is_pdf = False
            if not is_pdf:
                # Try to follow an embedded /doc1/ link from HTML content
                try:
                    html = (resp.text or '')
                    soup2 = BeautifulSoup(html, 'html.parser')
                    # Prefer iframe src, then anchor href, then form action
                    src = None
                    iframe = soup2.find('iframe', src=lambda h: h and '/doc1/' in h)
                    if iframe and iframe.get('src'):
                        src = iframe.get('src')
                    if not src:
                        a = soup2.find('a', href=lambda h: h and '/doc1/' in h)
                        if a and a.get('href'):
                            src = a.get('href')
                    if not src:
                        form = soup2.find('form', action=lambda h: h and '/doc1/' in h)
                        if form and form.get('action'):
                            src = form.get('action')
                    if src:
                        if src.startswith('/') and host_str:
                            src = f"https://{host_str}{src}"
                        resp2 = download_to(src)
                        with open(tmp_path, 'wb') as f:
                            f.write(resp2.content or b'')
                        size = tmp_path.stat().st_size if tmp_path.exists() else 0
                        with open(tmp_path, 'rb') as t:
                            sig = t.read(5)
                            is_pdf = sig == b'%PDF-'
                except Exception:
                    pass

            if not is_pdf:
                # Keep the HTML for debugging and do not cache as PDF
                debug_path = file_path.with_suffix('.html')
                try:
                    tmp_path.replace(debug_path)
                except Exception:
                    pass
                print("Downloaded content is not a PDF; saved HTML for inspection.")
                return None

            tmp_path.replace(file_path)

            # Estimate cost
            pages = self._estimate_pdf_pages_from_size(size)
            cost = min(pages * 0.10, 3.00)

            charge_id = hashlib.sha256(f"{court_code}-{doc_id}-{datetime.utcnow().isoformat()}".encode()).hexdigest()
            insert_charge({
                "id": charge_id,
                "case_id": parts.get('caseid'),
                "court_code": court_code,
                "resource": "document_pdf",
                "cmecf_url": doc_url,
                "pages_billed": pages,
                "amount_usd": cost,
                "api_key_id": self.username,
                "triggered_by": "api_fetch",
                "created_at": datetime.utcnow().isoformat()
            })

            return {
                "path": str(file_path),
                "filename": filename,
                "cached": False,
                "pages_billed": pages,
                "amount_usd": cost,
            }

        except Exception as e:
            print(f"Error fetching document: {e}")
            return None

    def _save_cookies(self):
        try:
            data = []
            for c in self.session.cookies:
                data.append({
                    'name': c.name,
                    'value': c.value,
                    'domain': c.domain,
                    'path': c.path,
                    'secure': bool(c.secure),
                    'expires': c.expires
                })
            self.cookie_file.write_text(json.dumps(data))
        except Exception:
            pass

    def _load_cookies(self):
        """Load cookies from persistent storage, skipping expired ones.

        Only loads cookies that:
        - Have not expired
        - Are from PACER-related domains

        Sets authenticated flag based on presence of valid session cookies.
        """
        try:
            if not self.cookie_file.exists():
                logger.debug("No cookie file found")
                return

            data = json.loads(self.cookie_file.read_text())
            now = time.time()
            loaded_count = 0
            skipped_expired = 0
            session_cookies_found = []

            for c in data:
                cookie_name = c.get('name', '')
                cookie_domain = c.get('domain', '')
                cookie_expires = c.get('expires')

                # Skip expired cookies
                if cookie_expires and cookie_expires < now:
                    skipped_expired += 1
                    logger.debug(f"Skipping expired cookie: {cookie_name}")
                    continue

                # Only load PACER-related cookies
                if not any(d in cookie_domain for d in ['.uscourts.gov', 'pacer.uscourts.gov']):
                    continue

                try:
                    ck = create_cookie(
                        name=cookie_name,
                        value=c.get('value'),
                        domain=cookie_domain,
                        path=c.get('path', '/'),
                        secure=c.get('secure', False),
                        expires=cookie_expires
                    )
                    self.session.cookies.set_cookie(ck)
                    loaded_count += 1

                    # Track session-related cookies
                    if cookie_name in ['NextGenCSO', 'PacerSession', 'JSESSIONID', 'PacerUser']:
                        session_cookies_found.append(cookie_name)
                except Exception as e:
                    logger.debug(f"Failed to load cookie {cookie_name}: {e}")

            # Only consider authenticated if we have actual session cookies
            self.authenticated = len(session_cookies_found) > 0

            logger.info(
                f"Loaded {loaded_count} cookies "
                f"(skipped {skipped_expired} expired), "
                f"session cookies: {session_cookies_found}"
            )

        except json.JSONDecodeError as e:
            logger.warning(f"Cookie file corrupted, ignoring: {e}")
        except Exception as e:
            logger.error(f"Failed to load cookies: {e}")

# Global client instance
pacer_client = PacerClient()
