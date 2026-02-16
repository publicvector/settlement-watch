import os
import time
import json
import logging
from typing import Optional, Dict, Any, List
import requests

logger = logging.getLogger(__name__)


def cso_authenticate(
    auth_base_url: Optional[str] = None,
    login_id: Optional[str] = None,
    password: Optional[str] = None,
    client_code: Optional[str] = None,
    otp_code: Optional[str] = None,
    redact_flag: Optional[int] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Call CSO Authentication API to obtain nextGenCSO token.

    Tries multiple authentication strategies in order, collecting errors
    from each failed attempt for debugging.

    Returns:
        Dict with keys:
            - ok: bool indicating success
            - token: str token if successful, None otherwise
            - raw: raw response data
            - error: str error message if failed
            - strategies_tried: list of strategies attempted
            - strategy_errors: list of error details from each strategy
    """
    auth_base_url = auth_base_url or os.getenv("PACER_AUTH_URL", "https://pacer.login.uscourts.gov")
    login_id = login_id or os.getenv("PACER_USERNAME")
    password = password or os.getenv("PACER_PASSWORD")
    client_code = client_code or os.getenv("PACER_CLIENT_CODE")
    otp_code = otp_code or os.getenv("PACER_OTP_CODE")

    # Track errors from each strategy
    strategy_errors: List[Dict[str, Any]] = []
    strategies_tried: List[str] = []

    if not (auth_base_url and login_id and password):
        return {
            "ok": False,
            "token": None,
            "error": "Missing PACER_AUTH_URL or PACER_USERNAME/PACER_PASSWORD",
            "strategies_tried": [],
            "strategy_errors": [],
        }

    # Strategy 1: Try simplified auth endpoint that returns token in header (X-NEXT-GEN-CSO)
    strategies_tried.append("header_auth")
    try:
        url1 = auth_base_url.rstrip("/") + "/services/auth"
        headers1 = {"Content-Type": "application/json", "Accept": "application/json"}
        body1 = {"username": login_id, "password": password}
        resp1 = requests.post(url1, headers=headers1, json=body1, timeout=timeout)

        # Token from headers (case-insensitive)
        token = None
        for k, v in resp1.headers.items():
            if k.lower() == "x-next-gen-cso" and v:
                token = v
                break

        if token:
            logger.info("CSO auth successful via header_auth strategy")
            return {
                "ok": True,
                "token": token,
                "raw": {"status": resp1.status_code},
                "error": None,
                "strategies_tried": strategies_tried,
                "strategy_errors": strategy_errors,
            }
        else:
            error_detail = {
                "strategy": "header_auth",
                "status_code": resp1.status_code,
                "error": "No X-NEXT-GEN-CSO header in response",
                "response_preview": resp1.text[:200] if resp1.text else None,
            }
            strategy_errors.append(error_detail)
            logger.debug(f"header_auth failed: {error_detail}")

    except requests.exceptions.Timeout as e:
        error_detail = {
            "strategy": "header_auth",
            "error": "Request timeout",
            "exception": str(e),
        }
        strategy_errors.append(error_detail)
        logger.warning(f"header_auth timeout: {e}")
    except requests.exceptions.ConnectionError as e:
        error_detail = {
            "strategy": "header_auth",
            "error": "Connection error",
            "exception": str(e),
        }
        strategy_errors.append(error_detail)
        logger.warning(f"header_auth connection error: {e}")
    except Exception as e:
        error_detail = {
            "strategy": "header_auth",
            "error": str(e),
            "exception_type": type(e).__name__,
        }
        strategy_errors.append(error_detail)
        logger.warning(f"header_auth exception: {e}")

    # Strategy 2: Try cso-auth endpoint returning token in body (JSON or XML)
    strategies_tried.append("cso_auth_body")
    url2 = auth_base_url.rstrip("/") + "/services/cso-auth"
    payload2 = {"loginId": login_id, "password": password}
    if client_code:
        payload2["clientCode"] = client_code
    if otp_code:
        payload2["otpCode"] = otp_code
    if redact_flag is not None:
        payload2["redactFlag"] = int(redact_flag)
    headers2 = {"Content-Type": "application/json", "Accept": "application/json, application/xml;q=0.9"}

    try:
        resp2 = requests.post(url2, headers=headers2, json=payload2, timeout=timeout)
        raw2 = resp2.text
        token = None

        # Check response headers as well (some variants also include header)
        for k, v in resp2.headers.items():
            if k.lower() == "x-next-gen-cso" and v:
                token = v
                break

        if not token and resp2.headers.get("Content-Type", "").lower().startswith("application/json"):
            try:
                data = resp2.json()
                token = data.get("nextGenCSO") or data.get("nextgencso")
                if token:
                    logger.info("CSO auth successful via cso_auth_body strategy (JSON)")
                    return {
                        "ok": True,
                        "token": token,
                        "raw": data,
                        "error": None,
                        "strategies_tried": strategies_tried,
                        "strategy_errors": strategy_errors,
                    }
                else:
                    error_detail = {
                        "strategy": "cso_auth_body",
                        "status_code": resp2.status_code,
                        "error": "No token in JSON response",
                        "response_keys": list(data.keys()) if isinstance(data, dict) else None,
                    }
                    strategy_errors.append(error_detail)
            except json.JSONDecodeError as e:
                error_detail = {
                    "strategy": "cso_auth_body",
                    "error": f"JSON decode error: {e}",
                    "response_preview": raw2[:200] if raw2 else None,
                }
                strategy_errors.append(error_detail)

        elif not token:
            # Try minimal XML parsing
            try:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(raw2)
                el = root.find("nextGenCSO")
                token = el.text if el is not None else None
                if token:
                    logger.info("CSO auth successful via cso_auth_body strategy (XML)")
                    return {
                        "ok": True,
                        "token": token,
                        "raw": raw2,
                        "error": None,
                        "strategies_tried": strategies_tried,
                        "strategy_errors": strategy_errors,
                    }
                else:
                    error_detail = {
                        "strategy": "cso_auth_body",
                        "status_code": resp2.status_code,
                        "error": "No token in XML response",
                        "response_preview": raw2[:200] if raw2 else None,
                    }
                    strategy_errors.append(error_detail)
            except Exception as xml_err:
                error_detail = {
                    "strategy": "cso_auth_body",
                    "error": f"XML parse error: {xml_err}",
                    "response_preview": raw2[:200] if raw2 else None,
                }
                strategy_errors.append(error_detail)
        else:
            logger.info("CSO auth successful via cso_auth_body strategy (header)")
            return {
                "ok": True,
                "token": token,
                "raw": {"status": resp2.status_code},
                "error": None,
                "strategies_tried": strategies_tried,
                "strategy_errors": strategy_errors,
            }

    except requests.exceptions.Timeout as e:
        error_detail = {
            "strategy": "cso_auth_body",
            "error": "Request timeout",
            "exception": str(e),
        }
        strategy_errors.append(error_detail)
        logger.warning(f"cso_auth_body timeout: {e}")
    except requests.exceptions.ConnectionError as e:
        error_detail = {
            "strategy": "cso_auth_body",
            "error": "Connection error",
            "exception": str(e),
        }
        strategy_errors.append(error_detail)
        logger.warning(f"cso_auth_body connection error: {e}")
    except Exception as e:
        error_detail = {
            "strategy": "cso_auth_body",
            "error": str(e),
            "exception_type": type(e).__name__,
        }
        strategy_errors.append(error_detail)
        logger.warning(f"cso_auth_body exception: {e}")

    # All strategies failed - return detailed error info
    all_errors = "; ".join(
        f"{err['strategy']}: {err.get('error', 'unknown')}"
        for err in strategy_errors
    )
    logger.error(f"CSO authentication failed. Strategies tried: {strategies_tried}. Errors: {all_errors}")

    return {
        "ok": False,
        "token": None,
        "raw": None,
        "error": f"All CSO auth strategies failed: {all_errors}",
        "strategies_tried": strategies_tried,
        "strategy_errors": strategy_errors,
    }
