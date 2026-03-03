"""
Claim form proxy service.

Fetches external claim forms, parses fields, maps profile data,
and proxies form submissions.
"""
import re
import logging
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Mapping from profile field names to common form field name patterns
FIELD_MAP = {
    "first_name": ["name_first", "firstname", "first_name", "fname", "first"],
    "last_name": ["name_last", "lastname", "last_name", "lname", "last"],
    "email": ["email", "emailaddress", "email_address", "e-mail", "e_mail"],
    "phone": ["phone", "telephone", "tel", "phonenumber", "phone_number", "mobile"],
    "address": ["address", "street", "address1", "address_line_1", "street_address", "streetaddress"],
    "address2": ["address2", "apt", "suite", "address_line_2", "unit", "addressline2"],
    "city": ["city", "town"],
    "state": ["state", "province", "region"],
    "zip": ["zip", "zipcode", "zip_code", "postal", "postalcode", "postal_code"],
}

# Build reverse lookup: normalized form field name -> profile key
_REVERSE_MAP: Dict[str, str] = {}
for profile_key, aliases in FIELD_MAP.items():
    for alias in aliases:
        _REVERSE_MAP[alias] = profile_key


def _normalize_field_name(name: str) -> str:
    """Normalize a form field name for fuzzy matching."""
    return re.sub(r"[\-_\s\[\]]+", "", name.lower()).strip()


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return session


def _find_label(field_tag, soup) -> str:
    """Try to find a label for a form field."""
    field_id = field_tag.get("id")
    if field_id:
        label = soup.find("label", attrs={"for": field_id})
        if label:
            return label.get_text(strip=True)
    # Check preceding label sibling
    prev = field_tag.find_previous("label")
    if prev:
        return prev.get_text(strip=True)
    return ""


# Keywords that indicate a link points to a claim/settlement form
_CLAIM_LINK_PATTERNS = re.compile(
    r"(file.{0,5}claim|submit.{0,5}claim|claim.{0,5}form|settlement.{0,10}(claim|form|submit)"
    r"|make.{0,5}claim|start.{0,5}claim|begin.{0,5}claim|file.{0,5}now|claim.{0,5}now"
    r"|submit.{0,5}now|online.{0,5}claim|claim.{0,5}portal)",
    re.IGNORECASE,
)

# URL path segments that suggest a claim form destination
_CLAIM_URL_PATTERNS = re.compile(
    r"/(claim|submit-claim|file-claim|claim-form|claimform|make-a-claim)",
    re.IGNORECASE,
)

# Domains that are article/aggregator sites (not settlement sites themselves)
_AGGREGATOR_DOMAINS = {
    "openclassactions.com", "www.openclassactions.com",
    "classactionrebates.com", "www.classactionrebates.com",
    "classaction.org", "www.classaction.org",
    "bigclassaction.com", "www.bigclassaction.com",
    "topclassactions.com", "www.topclassactions.com",
}


def _parse_form_from_soup(soup: BeautifulSoup, page_url: str) -> Optional[Dict[str, Any]]:
    """
    Extract form fields from a BeautifulSoup object.
    Returns parsed form dict or None if no form found.
    """
    form = soup.find("form")
    if not form:
        return None

    action = form.get("action", "")
    if action:
        action = urljoin(page_url, action)
    else:
        action = page_url
    method = (form.get("method") or "POST").upper()

    fields: List[Dict[str, Any]] = []
    hidden_fields: List[Dict[str, str]] = []

    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        input_type = (inp.get("type") or "text").lower()
        if input_type == "hidden":
            hidden_fields.append({"name": name, "value": inp.get("value", "")})
            continue
        if input_type == "submit":
            continue
        fields.append({
            "name": name,
            "type": input_type,
            "label": _find_label(inp, soup),
            "placeholder": inp.get("placeholder", ""),
            "required": inp.has_attr("required"),
            "value": inp.get("value", ""),
        })

    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        options = []
        for opt in sel.find_all("option"):
            options.append({
                "value": opt.get("value", ""),
                "text": opt.get_text(strip=True),
            })
        fields.append({
            "name": name,
            "type": "select",
            "label": _find_label(sel, soup),
            "placeholder": "",
            "required": sel.has_attr("required"),
            "value": sel.get("value", ""),
            "options": options,
        })

    for ta in form.find_all("textarea"):
        name = ta.get("name")
        if not name:
            continue
        fields.append({
            "name": name,
            "type": "textarea",
            "label": _find_label(ta, soup),
            "placeholder": ta.get("placeholder", ""),
            "required": ta.has_attr("required"),
            "value": ta.get_text() or "",
        })

    return {
        "action": action,
        "method": method,
        "fields": fields,
        "hidden_fields": hidden_fields,
    }


def _score_claim_link(href: str, text: str, source_domain: str) -> int:
    """Score a link by how likely it leads to a claim form. Higher = better."""
    score = 0
    href_lower = href.lower()
    text_lower = text.lower().strip()

    # Link text matches claim patterns
    if _CLAIM_LINK_PATTERNS.search(text_lower):
        score += 10

    # URL path matches claim patterns
    if _CLAIM_URL_PATTERNS.search(href_lower):
        score += 8

    # Links to a different domain (settlement site, not aggregator)
    try:
        link_domain = urlparse(href).netloc.lower()
    except Exception:
        return 0
    if link_domain and link_domain != source_domain and link_domain not in _AGGREGATOR_DOMAINS:
        score += 3
        # Domain contains "settlement" or "claim"
        if "settlement" in link_domain or "claim" in link_domain:
            score += 5

    # Penalize non-http links, anchors, mailto, etc.
    if not href_lower.startswith("http"):
        score -= 20

    return score


def _find_claim_links(soup: BeautifulSoup, page_url: str) -> List[str]:
    """
    Scan a page's links for ones likely pointing to a settlement claim form.
    Returns URLs sorted by relevance (best first), deduplicated.
    """
    source_domain = urlparse(page_url).netloc.lower()
    scored: List[tuple] = []
    seen: set = set()

    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        if href in seen:
            continue
        seen.add(href)
        text = a.get_text(strip=True)
        score = _score_claim_link(href, text, source_domain)
        if score > 0:
            scored.append((score, href, text))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [url for _, url, _ in scored[:8]]


def fetch_and_parse_form(claim_url: str) -> Dict[str, Any]:
    """
    Fetch a claim page and parse its form fields.

    If no form is found on the initial page, trawls links looking for
    a settlement claim form on linked pages (one hop).

    Returns dict with action, method, fields, hidden_fields, or error.
    """
    session = _make_session()
    try:
        resp = session.get(claim_url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to fetch claim form %s: %s", claim_url, e)
        return {"error": f"Could not fetch claim page: {e}"}

    soup = BeautifulSoup(resp.text, "html.parser")

    # Try parsing a form directly on this page
    result = _parse_form_from_soup(soup, claim_url)
    if result and len(result["fields"]) >= 2:
        return result

    # No usable form found (missing or trivial like a search bar) — trawl links
    logger.info("No usable form on %s, trawling links for claim form...", claim_url)
    candidate_urls = _find_claim_links(soup, claim_url)

    if not candidate_urls:
        return {"error": "No form found on page. The claim form may require JavaScript."}

    for candidate_url in candidate_urls:
        try:
            resp2 = session.get(candidate_url, timeout=15)
            resp2.raise_for_status()
        except requests.RequestException:
            continue
        soup2 = BeautifulSoup(resp2.text, "html.parser")
        result = _parse_form_from_soup(soup2, candidate_url)
        if result and len(result["fields"]) >= 2:
            logger.info("Found claim form via link: %s", candidate_url)
            result["followed_link"] = candidate_url
            return result

    return {
        "error": "No form found on page or linked pages. The claim form may require JavaScript.",
        "tried_links": len(candidate_urls),
    }


def map_profile_to_fields(
    fields: List[Dict[str, Any]], profile: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Map profile data onto form fields using fuzzy field-name matching.
    Returns the fields list with `value` populated where a match is found.
    """
    if not profile:
        return fields

    for field in fields:
        normalized = _normalize_field_name(field["name"])
        # Check direct match first
        matched_key = _REVERSE_MAP.get(normalized)
        if not matched_key:
            # Check if normalized name contains any alias
            for alias, pkey in _REVERSE_MAP.items():
                if alias in normalized or normalized in alias:
                    matched_key = pkey
                    break
        if matched_key and profile.get(matched_key):
            field["value"] = profile[matched_key]
            field["auto_filled"] = True
        else:
            field.setdefault("auto_filled", False)

    return fields


def submit_claim_form(
    action_url: str, method: str, form_data: Dict[str, str]
) -> Dict[str, Any]:
    """
    Proxy a form submission to the claim site.
    Returns status code and a snippet of the response body.
    """
    session = _make_session()
    try:
        if method.upper() == "GET":
            resp = session.get(action_url, params=form_data, timeout=30)
        else:
            resp = session.post(action_url, data=form_data, timeout=30)

        # Check for common success indicators
        body_lower = resp.text[:5000].lower()
        success_indicators = [
            "thank you", "successfully", "claim has been",
            "confirmation", "submitted", "received your",
        ]
        likely_success = any(ind in body_lower for ind in success_indicators)

        return {
            "status_code": resp.status_code,
            "success": resp.status_code < 400 and likely_success,
            "message": (
                "Claim submitted successfully!"
                if likely_success
                else f"Form submitted (HTTP {resp.status_code}). Check the claim site for confirmation."
            ),
            "redirect_url": resp.url if resp.url != action_url else None,
        }
    except requests.RequestException as e:
        logger.error("Failed to submit claim form to %s: %s", action_url, e)
        return {
            "status_code": 0,
            "success": False,
            "message": f"Submission failed: {e}",
        }
