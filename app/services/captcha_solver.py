"""CAPTCHA Solving Service Integration.

Integrates with 2Captcha (and compatible services) to solve CAPTCHAs
encountered on court websites.

Supported CAPTCHA types:
- Image CAPTCHAs (text recognition)
- reCAPTCHA v2 (checkbox and invisible)
- reCAPTCHA v3
- hCaptcha
- Cloudflare Turnstile

Setup:
1. Create account at https://2captcha.com
2. Add funds (~$3 for 1000 CAPTCHAs)
3. Get API key from dashboard
4. Set CAPTCHA_API_KEY in .env file

Usage:
    from app.services.captcha_solver import CaptchaSolver

    solver = CaptchaSolver()

    # For image CAPTCHA
    solution = solver.solve_image(image_bytes)

    # For reCAPTCHA v2
    token = solver.solve_recaptcha_v2(site_key, page_url)

    # For hCaptcha
    token = solver.solve_hcaptcha(site_key, page_url)

    # For Turnstile
    token = solver.solve_turnstile(site_key, page_url)
"""

import base64
import logging
import os
import time
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import 2captcha
try:
    from twocaptcha import TwoCaptcha
    TWOCAPTCHA_AVAILABLE = True
except ImportError:
    TWOCAPTCHA_AVAILABLE = False
    TwoCaptcha = None


class CaptchaSolver:
    """CAPTCHA solving service wrapper.

    Supports 2Captcha and compatible APIs (anti-captcha, etc.)
    """

    def __init__(
        self,
        api_key: str = None,
        service: str = "2captcha",
        timeout: int = 120,
        polling_interval: int = 5
    ):
        """Initialize CAPTCHA solver.

        Args:
            api_key: API key for the solving service. If None, reads from
                     CAPTCHA_API_KEY or TWOCAPTCHA_API_KEY env var.
            service: Service to use ("2captcha", "anti-captcha")
            timeout: Max time to wait for solution (seconds)
            polling_interval: How often to check for solution (seconds)
        """
        self.api_key = api_key or os.getenv("CAPTCHA_API_KEY") or os.getenv("TWOCAPTCHA_API_KEY")
        self.service = service
        self.timeout = timeout
        self.polling_interval = polling_interval

        self._solver = None
        self._stats = {
            "solved": 0,
            "failed": 0,
            "total_cost": 0.0,
        }

        if self.api_key and TWOCAPTCHA_AVAILABLE:
            self._solver = TwoCaptcha(self.api_key)
            self._solver.pollingInterval = polling_interval
            logger.info(f"CAPTCHA solver initialized with {service}")
        elif not TWOCAPTCHA_AVAILABLE:
            logger.warning("2captcha-python not installed. Run: pip install 2captcha-python")
        else:
            logger.warning("No CAPTCHA API key configured. Set CAPTCHA_API_KEY in .env")

    @property
    def is_configured(self) -> bool:
        """Check if solver is properly configured."""
        return self._solver is not None

    def get_balance(self) -> float:
        """Get current account balance."""
        if not self._solver:
            return 0.0
        try:
            return float(self._solver.balance())
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return 0.0

    def solve_image(
        self,
        image: bytes,
        case_sensitive: bool = False,
        numeric: bool = False,
        min_length: int = 0,
        max_length: int = 0
    ) -> Optional[str]:
        """Solve an image-based CAPTCHA.

        Args:
            image: CAPTCHA image as bytes
            case_sensitive: Whether solution is case-sensitive
            numeric: Whether CAPTCHA contains only numbers
            min_length: Minimum expected length
            max_length: Maximum expected length

        Returns:
            CAPTCHA solution text or None if failed
        """
        if not self._solver:
            logger.error("CAPTCHA solver not configured")
            return None

        try:
            # Convert to base64
            image_b64 = base64.b64encode(image).decode('utf-8')

            params = {
                "caseSensitive": case_sensitive,
            }
            if numeric:
                params["numeric"] = 1
            if min_length:
                params["minLength"] = min_length
            if max_length:
                params["maxLength"] = max_length

            result = self._solver.normal(image_b64, **params)

            if result and "code" in result:
                self._stats["solved"] += 1
                self._stats["total_cost"] += 0.002  # ~$0.002 per image CAPTCHA
                logger.info(f"Image CAPTCHA solved: {result['code'][:3]}...")
                return result["code"]

        except Exception as e:
            self._stats["failed"] += 1
            logger.error(f"Image CAPTCHA solving failed: {e}")

        return None

    def solve_recaptcha_v2(
        self,
        site_key: str,
        page_url: str,
        invisible: bool = False,
        data_s: str = None
    ) -> Optional[str]:
        """Solve reCAPTCHA v2.

        Args:
            site_key: The site key (data-sitekey attribute)
            page_url: URL of the page with CAPTCHA
            invisible: Whether it's invisible reCAPTCHA
            data_s: Optional data-s parameter for some sites

        Returns:
            reCAPTCHA token or None if failed
        """
        if not self._solver:
            logger.error("CAPTCHA solver not configured")
            return None

        try:
            params = {
                "sitekey": site_key,
                "url": page_url,
                "invisible": invisible,
            }
            if data_s:
                params["data_s"] = data_s

            result = self._solver.recaptcha(**params)

            if result and "code" in result:
                self._stats["solved"] += 1
                self._stats["total_cost"] += 0.003  # ~$0.003 per reCAPTCHA
                logger.info("reCAPTCHA v2 solved successfully")
                return result["code"]

        except Exception as e:
            self._stats["failed"] += 1
            logger.error(f"reCAPTCHA v2 solving failed: {e}")

        return None

    def solve_recaptcha_v3(
        self,
        site_key: str,
        page_url: str,
        action: str = "verify",
        min_score: float = 0.3
    ) -> Optional[str]:
        """Solve reCAPTCHA v3.

        Args:
            site_key: The site key
            page_url: URL of the page
            action: The action parameter
            min_score: Minimum required score

        Returns:
            reCAPTCHA token or None if failed
        """
        if not self._solver:
            logger.error("CAPTCHA solver not configured")
            return None

        try:
            result = self._solver.recaptcha(
                sitekey=site_key,
                url=page_url,
                version="v3",
                action=action,
                score=min_score
            )

            if result and "code" in result:
                self._stats["solved"] += 1
                self._stats["total_cost"] += 0.003
                logger.info("reCAPTCHA v3 solved successfully")
                return result["code"]

        except Exception as e:
            self._stats["failed"] += 1
            logger.error(f"reCAPTCHA v3 solving failed: {e}")

        return None

    def solve_hcaptcha(
        self,
        site_key: str,
        page_url: str
    ) -> Optional[str]:
        """Solve hCaptcha.

        Args:
            site_key: The site key (data-sitekey attribute)
            page_url: URL of the page with CAPTCHA

        Returns:
            hCaptcha token or None if failed
        """
        if not self._solver:
            logger.error("CAPTCHA solver not configured")
            return None

        try:
            result = self._solver.hcaptcha(
                sitekey=site_key,
                url=page_url
            )

            if result and "code" in result:
                self._stats["solved"] += 1
                self._stats["total_cost"] += 0.003
                logger.info("hCaptcha solved successfully")
                return result["code"]

        except Exception as e:
            self._stats["failed"] += 1
            logger.error(f"hCaptcha solving failed: {e}")

        return None

    def solve_turnstile(
        self,
        site_key: str,
        page_url: str
    ) -> Optional[str]:
        """Solve Cloudflare Turnstile.

        Args:
            site_key: The site key
            page_url: URL of the page

        Returns:
            Turnstile token or None if failed
        """
        if not self._solver:
            logger.error("CAPTCHA solver not configured")
            return None

        try:
            result = self._solver.turnstile(
                sitekey=site_key,
                url=page_url
            )

            if result and "code" in result:
                self._stats["solved"] += 1
                self._stats["total_cost"] += 0.003
                logger.info("Turnstile solved successfully")
                return result["code"]

        except Exception as e:
            self._stats["failed"] += 1
            logger.error(f"Turnstile solving failed: {e}")

        return None

    def solve_perimeterx(
        self,
        page_url: str,
        user_agent: str,
        cookies: dict = None
    ) -> Optional[dict]:
        """Solve PerimeterX challenge.

        Note: PerimeterX solving is complex and may require additional
        parameters depending on the specific implementation.

        Args:
            page_url: URL of the page with PerimeterX
            user_agent: Browser user agent string
            cookies: Optional dict of cookies from the page

        Returns:
            Dict with solution cookies or None if failed
        """
        if not self._solver:
            logger.error("CAPTCHA solver not configured")
            return None

        try:
            # PerimeterX uses a custom approach via 2captcha API
            # This requires sending specific data and getting cookies back
            import requests

            # Use 2captcha's API directly for PerimeterX
            payload = {
                "key": self.api_key,
                "method": "perimeterx",
                "pageurl": page_url,
                "userAgent": user_agent,
                "json": 1
            }

            # Add cookies if provided
            if cookies:
                # Include _px cookies
                px_cookies = {k: v for k, v in cookies.items() if '_px' in k.lower()}
                if px_cookies:
                    payload["cookies"] = ";".join(f"{k}={v}" for k, v in px_cookies.items())

            response = requests.post(
                "https://2captcha.com/in.php",
                data=payload,
                timeout=30
            )
            result = response.json()

            if result.get("status") != 1:
                logger.error(f"PerimeterX submit failed: {result.get('error_text', 'Unknown error')}")
                return None

            task_id = result.get("request")
            logger.info(f"PerimeterX task submitted: {task_id}")

            # Poll for result
            for _ in range(24):  # 2 minutes max
                time.sleep(5)
                check_response = requests.get(
                    f"https://2captcha.com/res.php?key={self.api_key}&action=get&id={task_id}&json=1",
                    timeout=30
                )
                check_result = check_response.json()

                if check_result.get("status") == 1:
                    self._stats["solved"] += 1
                    self._stats["total_cost"] += 0.01  # PerimeterX is more expensive
                    logger.info("PerimeterX solved successfully")
                    return check_result.get("request")

                if "CAPCHA_NOT_READY" not in str(check_result.get("request", "")):
                    logger.error(f"PerimeterX solving failed: {check_result}")
                    break

            self._stats["failed"] += 1
            logger.error("PerimeterX solving timed out")

        except Exception as e:
            self._stats["failed"] += 1
            logger.error(f"PerimeterX solving failed: {e}")

        return None

    def get_stats(self) -> dict:
        """Get solving statistics."""
        return self._stats.copy()


class PlaywrightCaptchaSolver:
    """CAPTCHA solver integrated with Playwright browser automation.

    Automatically detects and solves CAPTCHAs on pages.
    """

    def __init__(self, solver: CaptchaSolver = None):
        """Initialize with a CaptchaSolver instance."""
        self.solver = solver or CaptchaSolver()

    async def detect_captcha_type(self, page) -> Optional[dict]:
        """Detect what type of CAPTCHA is on the page.

        Returns dict with type and parameters, or None if no CAPTCHA.
        """
        content = await page.content()
        url = page.url

        # Check for reCAPTCHA
        recaptcha_match = await page.query_selector('[data-sitekey], .g-recaptcha')
        if recaptcha_match:
            site_key = await recaptcha_match.get_attribute('data-sitekey')
            if site_key:
                # Check if v3
                if 'recaptcha/api.js?render=' in content:
                    return {"type": "recaptcha_v3", "site_key": site_key, "url": url}
                else:
                    invisible = 'invisible' in (await recaptcha_match.get_attribute('data-size') or '')
                    return {"type": "recaptcha_v2", "site_key": site_key, "url": url, "invisible": invisible}

        # Check for hCaptcha
        hcaptcha_match = await page.query_selector('[data-sitekey].h-captcha, .h-captcha[data-sitekey]')
        if hcaptcha_match:
            site_key = await hcaptcha_match.get_attribute('data-sitekey')
            if site_key:
                return {"type": "hcaptcha", "site_key": site_key, "url": url}

        # Check for Turnstile
        turnstile_match = await page.query_selector('[data-sitekey].cf-turnstile, .cf-turnstile[data-sitekey]')
        if turnstile_match:
            site_key = await turnstile_match.get_attribute('data-sitekey')
            if site_key:
                return {"type": "turnstile", "site_key": site_key, "url": url}

        # Check for image CAPTCHA
        captcha_img = await page.query_selector('img[src*="captcha"], img[alt*="captcha"], #captchaImage')
        if captcha_img:
            return {"type": "image", "element": captcha_img, "url": url}

        # Check for PerimeterX
        if 'perimeterx.net' in content or '_pxCaptcha' in content:
            # Extract PerimeterX parameters
            import re
            px_data = {"type": "perimeterx", "url": url}

            # Try to get the captcha script URL
            px_script_match = re.search(r'(https://captcha\.perimeterx\.net/[^"\'>\s]+)', content)
            if px_script_match:
                px_data["script_url"] = px_script_match.group(1)

            # Try to get the app_id from the script URL or page
            app_id_match = re.search(r'/PX([A-Za-z0-9]+)/', content)
            if app_id_match:
                px_data["app_id"] = "PX" + app_id_match.group(1)

            return px_data

        # Check page content for CAPTCHA keywords
        if any(kw in content.lower() for kw in ['captcha', 'verify you are human', 'security check']):
            return {"type": "unknown", "url": url}

        return None

    async def solve_page_captcha(self, page) -> bool:
        """Detect and solve CAPTCHA on a page.

        Args:
            page: Playwright page object

        Returns:
            True if CAPTCHA was solved (or none present), False if failed
        """
        if not self.solver.is_configured:
            logger.warning("CAPTCHA solver not configured - cannot solve automatically")
            return False

        captcha_info = await self.detect_captcha_type(page)

        if not captcha_info:
            return True  # No CAPTCHA detected

        captcha_type = captcha_info["type"]
        logger.info(f"Detected {captcha_type} CAPTCHA on {captcha_info['url']}")

        if captcha_type == "recaptcha_v2":
            token = self.solver.solve_recaptcha_v2(
                captcha_info["site_key"],
                captcha_info["url"],
                invisible=captcha_info.get("invisible", False)
            )
            if token:
                return await self._inject_recaptcha_token(page, token)

        elif captcha_type == "recaptcha_v3":
            token = self.solver.solve_recaptcha_v3(
                captcha_info["site_key"],
                captcha_info["url"]
            )
            if token:
                return await self._inject_recaptcha_token(page, token)

        elif captcha_type == "hcaptcha":
            token = self.solver.solve_hcaptcha(
                captcha_info["site_key"],
                captcha_info["url"]
            )
            if token:
                return await self._inject_hcaptcha_token(page, token)

        elif captcha_type == "turnstile":
            token = self.solver.solve_turnstile(
                captcha_info["site_key"],
                captcha_info["url"]
            )
            if token:
                return await self._inject_turnstile_token(page, token)

        elif captcha_type == "perimeterx":
            return await self._solve_perimeterx_captcha(page, captcha_info)

        elif captcha_type == "image":
            return await self._solve_image_captcha(page, captcha_info["element"])

        return False

    async def _inject_recaptcha_token(self, page, token: str) -> bool:
        """Inject solved reCAPTCHA token into page."""
        try:
            await page.evaluate(f'''() => {{
                document.getElementById("g-recaptcha-response").innerHTML = "{token}";
                if (typeof grecaptcha !== 'undefined' && grecaptcha.getResponse) {{
                    // Trigger callback if exists
                    const callback = document.querySelector('[data-callback]');
                    if (callback) {{
                        const callbackName = callback.getAttribute('data-callback');
                        if (window[callbackName]) window[callbackName]('{token}');
                    }}
                }}
            }}''')
            logger.info("reCAPTCHA token injected")
            return True
        except Exception as e:
            logger.error(f"Failed to inject reCAPTCHA token: {e}")
            return False

    async def _inject_hcaptcha_token(self, page, token: str) -> bool:
        """Inject solved hCaptcha token into page."""
        try:
            await page.evaluate(f'''() => {{
                const textarea = document.querySelector('[name="h-captcha-response"], [name="g-recaptcha-response"]');
                if (textarea) textarea.innerHTML = "{token}";

                const iframe = document.querySelector('iframe[data-hcaptcha-response]');
                if (iframe) iframe.setAttribute('data-hcaptcha-response', '{token}');
            }}''')
            logger.info("hCaptcha token injected")
            return True
        except Exception as e:
            logger.error(f"Failed to inject hCaptcha token: {e}")
            return False

    async def _inject_turnstile_token(self, page, token: str) -> bool:
        """Inject solved Turnstile token into page."""
        try:
            await page.evaluate(f'''() => {{
                const input = document.querySelector('[name="cf-turnstile-response"]');
                if (input) input.value = "{token}";

                // Try to trigger form submission or callback
                if (typeof turnstile !== 'undefined' && turnstile.getResponse) {{
                    // Turnstile callback
                }}
            }}''')
            logger.info("Turnstile token injected")
            return True
        except Exception as e:
            logger.error(f"Failed to inject Turnstile token: {e}")
            return False

    async def _solve_perimeterx_captcha(self, page, captcha_info: dict) -> bool:
        """Solve PerimeterX challenge.

        PerimeterX is a bot detection service that requires solving a challenge
        and setting cookies. This method attempts to use 2captcha's API to solve it.
        """
        try:
            # Get user agent from the browser
            user_agent = await page.evaluate("navigator.userAgent")

            # Get cookies from the browser
            cookies = await page.context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies}

            logger.info("Attempting to solve PerimeterX challenge...")

            # Try to solve via 2captcha
            solution = self.solver.solve_perimeterx(
                page_url=captcha_info["url"],
                user_agent=user_agent,
                cookies=cookie_dict
            )

            if solution:
                # The solution should contain cookies to set
                if isinstance(solution, dict):
                    # Set the returned cookies
                    for name, value in solution.items():
                        await page.context.add_cookies([{
                            "name": name,
                            "value": value,
                            "domain": ".hillsclerk.com",
                            "path": "/"
                        }])

                # Refresh the page to apply cookies
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

                # Check if CAPTCHA is gone
                new_content = await page.content()
                if 'perimeterx' not in new_content.lower():
                    logger.info("PerimeterX challenge solved!")
                    return True

            logger.warning("PerimeterX solving did not work - may need manual intervention")
            return False

        except Exception as e:
            logger.error(f"Failed to solve PerimeterX: {e}")
            return False

    async def _solve_image_captcha(self, page, img_element) -> bool:
        """Solve an image CAPTCHA."""
        try:
            # Screenshot the CAPTCHA image
            img_bytes = await img_element.screenshot()

            # Solve it
            solution = self.solver.solve_image(img_bytes)

            if solution:
                # Find the input field near the CAPTCHA
                input_field = await page.query_selector(
                    'input[name*="captcha"], input[id*="captcha"], '
                    'input[type="text"]:near(img[src*="captcha"])'
                )
                if input_field:
                    await input_field.fill(solution)
                    logger.info(f"Image CAPTCHA solved and filled: {solution[:3]}...")
                    return True

        except Exception as e:
            logger.error(f"Failed to solve image CAPTCHA: {e}")

        return False


# Convenience function
def get_captcha_solver(api_key: str = None) -> CaptchaSolver:
    """Get a configured CAPTCHA solver instance."""
    return CaptchaSolver(api_key=api_key)
