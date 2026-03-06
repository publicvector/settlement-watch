"""
Settlement scraper service for FastAPI.

Adapted from scripts/settlement_bulk_scraper.py and scripts/settlement_dorker.py.
Uses only requests + BeautifulSoup (no Playwright).
"""
import re
import time
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword-based auto-categorization
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS = {
    "Data Breach": ["data breach", "cyber", "hack", "personal information", "identity theft", "privacy violation"],
    "Antitrust": ["antitrust", "price fixing", "price-fixing", "monopoly", "anticompetitive", "anti-competitive"],
    "Employment": ["wage", "overtime", "labor", "employment", "wrongful termination", "workplace", "discrimination",
                    "eeoc", "flsa", "fair labor"],
    "Consumer Protection": ["consumer", "deceptive", "unfair practices", "false advertising", "misleading",
                            "cfpb", "consumer financial"],
    "Securities": ["securities", "sec ", "investor", "stock", "insider trading", "shareholder"],
    "Environmental": ["epa", "environmental", "pollution", "clean water", "clean air", "toxic", "superfund",
                      "hazardous waste"],
    "Healthcare": ["healthcare", "health care", "pharmaceutical", "drug", "medical device", "medicare",
                   "medicaid", "opioid"],
    "Government": ["doj", "department of justice", "federal trade commission", "ftc", "government",
                   "attorney general"],
    "State AG": ["state attorney general", "attorney general", "ag office", "oag"],
    "Product Liability": ["product liability", "defect", "recall", "product safety"],
    "Class Action": ["class action", "class member", "class settlement", "class certification"],
}


def categorize_settlement(title: str, description: str = "", source: str = "") -> str:
    """Return a category string based on keyword matching against title/description/source."""
    text = f"{title} {description} {source}".lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return category
    return "Other"


# Amount extraction patterns
AMOUNT_PATTERNS = [
    (r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B)\b', 1_000_000_000),
    (r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M)\b', 1_000_000),
    (r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:thousand|K)\b', 1_000),
    (r'\$\s*([\d,]+(?:\.\d+)?)', 1),
]


def extract_amount(text: str) -> Tuple[Optional[float], Optional[str]]:
    """Extract settlement amount from text."""
    for pattern, multiplier in AMOUNT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                num_str = match.group(1).replace(',', '')
                amount = float(num_str) * multiplier
                if amount >= 1_000_000_000:
                    formatted = f"${amount / 1_000_000_000:.1f}B"
                elif amount >= 1_000_000:
                    formatted = f"${amount / 1_000_000:.1f}M"
                else:
                    formatted = f"${amount:,.0f}"
                return amount, formatted
            except Exception:
                pass
    return None, None


# ---------------------------------------------------------------------------
# Deadline + claim URL extraction helpers
# ---------------------------------------------------------------------------

DEADLINE_PATTERNS = [
    r'(?:deadline|expires?|due|must be (?:postmarked|submitted|received) by)[:\s]+([A-Z][a-z]+ \d{1,2},?\s*\d{4})',
    r'(?:deadline|expires?|due)[:\s]+(\d{1,2}/\d{1,2}/\d{4})',
    r'(\d{1,2}/\d{1,2}/\d{4})',
    r'([A-Z][a-z]+ \d{1,2},?\s*\d{4})',
]

DATE_FORMATS = [
    "%B %d, %Y",   # January 15, 2026
    "%B %d %Y",    # January 15 2026
    "%b %d, %Y",   # Jan 15, 2026
    "%b %d %Y",    # Jan 15 2026
    "%m/%d/%Y",    # 01/15/2026
]


def extract_deadline(text: str) -> Optional[str]:
    """Extract claim deadline from text and normalize to YYYY-MM-DD."""
    if not text:
        return None
    for pattern in DEADLINE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = match.group(1).strip()
            for fmt in DATE_FORMATS:
                try:
                    dt = datetime.strptime(raw, fmt)
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    continue
            return None  # matched text but couldn't parse date
    return None


CLAIM_LINK_TEXT = re.compile(
    r'file\s+(?:a\s+|your\s+)?claim|submit\s+(?:a\s+|your\s+)?claim|claim\s+form|'
    r'start\s+(?:your\s+)?claim|make\s+a\s+claim|begin\s+(?:your\s+)?claim',
    re.IGNORECASE,
)

# URL paths that indicate an actual claim form page (not just a homepage)
CLAIM_PATH_PATTERN = re.compile(
    r'/claim|/file[-_]?claim|/submit[-_]?claim|/claimform|/fileclaim|'
    r'/registration|/enroll|/sign[-_]?up.*claim',
    re.IGNORECASE,
)

# Domains that are ads/referrals, not real claim forms
SPAM_DOMAINS = re.compile(
    r'injuryclaims\.com|lawsuit\.com|lawsuitlegal|classactionlawyer|'
    r'findlaw\.com|avvo\.com|justia\.com',
    re.IGNORECASE,
)


def _is_real_claim_url(href: str) -> bool:
    """Check if a URL looks like an actual claim filing page, not a homepage or ad."""
    if not href or href == '#':
        return False
    if SPAM_DOMAINS.search(href):
        return False
    # Must have a path beyond just the domain root
    from urllib.parse import urlparse
    parsed = urlparse(href)
    path = parsed.path.rstrip('/')
    # Accept if path contains claim-related keywords
    if CLAIM_PATH_PATTERN.search(path):
        return True
    # Accept if it has query params suggesting a claim flow (e.g. ?step=1, ?page=claim)
    if parsed.query and re.search(r'claim|file|submit|step', parsed.query, re.IGNORECASE):
        return True
    return False


def extract_claim_url(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """Scan <a> tags for real claim-filing links. Returns only URLs that look like
    actual claim submission pages, not settlement homepages or ads."""
    from urllib.parse import urljoin

    # First pass: links with explicit claim-filing text AND a real claim path
    for a in soup.find_all('a', href=True):
        link_text = a.get_text(strip=True)
        if CLAIM_LINK_TEXT.search(link_text):
            href = a['href']
            if not href.startswith('http'):
                href = urljoin(base_url, href)
            if href.rstrip('/') == base_url.rstrip('/'):
                continue
            if _is_real_claim_url(href):
                return href

    return None


def _enrich_with_detail_page(session: requests.Session, settlements: List[Dict]) -> List[Dict]:
    """Fetch each settlement's URL to extract claim_url and claim_deadline.

    Two-hop enrichment: if the first page doesn't have a claim form link but links
    to a settlement-specific website, follows that site and looks for the form there.
    """
    for s in settlements:
        url = s.get("url")
        if not url:
            continue
        try:
            soup = _get(session, url, timeout=15)
            if not soup:
                continue
            page_text = soup.get_text(' ', strip=True)

            # Extract deadline from the page
            if not s.get("claim_deadline"):
                s["claim_deadline"] = extract_deadline(page_text)

            # Try to find claim URL on this page
            if not s.get("claim_url"):
                claim = extract_claim_url(soup, url)
                if claim:
                    s["claim_url"] = claim
                else:
                    # Second hop: if page links to a settlement-specific site,
                    # follow it and look for the claim form there
                    _try_second_hop(session, s, soup, url)
        except Exception as e:
            logger.debug("Enrichment failed for %s: %s", url, e)
        time.sleep(0.3)
    return settlements


def _try_second_hop(session: requests.Session, settlement: Dict, soup: BeautifulSoup, source_url: str):
    """Follow outbound links to settlement-specific sites and look for claim forms."""
    from urllib.parse import urlparse, urljoin

    source_domain = urlparse(source_url).netloc

    # Look for outbound links to settlement-specific domains
    for a in soup.find_all('a', href=True):
        href = a['href']
        if not href.startswith('http'):
            href = urljoin(source_url, href)
        parsed = urlparse(href)
        # Skip same-domain links, empty, anchors
        if not parsed.netloc or parsed.netloc == source_domain:
            continue
        if SPAM_DOMAINS.search(href):
            continue
        # Settlement admin sites or settlement-specific domains are good candidates
        domain = parsed.netloc.lower()
        if any(kw in domain for kw in ['settlement', 'claim', 'classaction', 'epiq', 'simpluris',
                                        'angeion', 'kccllc', 'rustconsulting', 'jndla']):
            try:
                time.sleep(0.3)
                hop_soup = _get(session, href, timeout=15)
                if not hop_soup:
                    continue
                claim = extract_claim_url(hop_soup, href)
                if claim:
                    settlement["claim_url"] = claim
                    # Also grab deadline from this page if we don't have one
                    if not settlement.get("claim_deadline"):
                        settlement["claim_deadline"] = extract_deadline(hop_soup.get_text(' ', strip=True))
                    return
            except Exception:
                continue
    return


def _get(session: requests.Session, url: str, timeout: int = 20) -> Optional[BeautifulSoup]:
    """Fetch and parse a URL."""
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        logger.debug("Failed to fetch %s: %s", url, e)
        return None


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    })
    return session


# ---------------------------------------------------------------------------
# Scraper functions – each returns a list of settlement dicts
# ---------------------------------------------------------------------------

def _scrape_classaction_org(session: requests.Session) -> List[Dict]:
    """Scrape ClassAction.org settlements."""
    results = []
    soup = _get(session, "https://www.classaction.org/settlements")
    if not soup:
        return results
    for item in soup.find_all(['article', 'div', 'li'], class_=re.compile(r'settlement|post|item'))[:30]:
        title_el = item.find(['h2', 'h3', 'a'])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        text = item.get_text(' ', strip=True)
        amount, formatted = extract_amount(text)
        link = item.find('a', href=True)
        href = link.get('href', '') if link else ''
        if href and not href.startswith('http'):
            href = 'https://www.classaction.org' + href
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text else "",
            "category": categorize_settlement(title, text, "ClassAction.org"),
            "source": "ClassAction.org",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"cao-{href[-60:]}" if href else f"cao-{title[:40]}",
        })
    return results


def _scrape_ftc(session: requests.Session) -> List[Dict]:
    """Scrape FTC enforcement actions."""
    results = []
    soup = _get(session, "https://www.ftc.gov/enforcement/cases-proceedings")
    if not soup:
        return results
    for item in soup.find_all(['article', 'div'], class_=re.compile(r'case|item|row'))[:30]:
        title_el = item.find(['h2', 'h3', 'a'])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        text = item.get_text(' ', strip=True)
        amount, formatted = extract_amount(text)
        link = item.find('a', href=True)
        href = link.get('href', '') if link else ''
        if href and not href.startswith('http'):
            href = 'https://www.ftc.gov' + href
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text else "",
            "category": categorize_settlement(title, text, "FTC"),
            "source": "FTC",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"ftc-{href[-60:]}" if href else f"ftc-{title[:40]}",
        })
    return results


def _scrape_sec(session: requests.Session) -> List[Dict]:
    """Scrape SEC litigation releases."""
    results = []
    soup = _get(session, "https://www.sec.gov/litigation/litreleases")
    if not soup:
        return results
    # The new SEC page uses broader markup; scan all links for litigation release paths
    for link in soup.find_all('a', href=True)[:200]:
        href = link.get('href', '')
        title = link.get_text(strip=True)
        if not title or len(title) < 10:
            continue
        # Match litigation release links (old and new URL patterns)
        if not re.search(r'/litreleases?/', href):
            continue
        if not any(t in title.lower() for t in ['settle', 'pay', 'million', 'penalty', 'judgment', 'charge', 'order']):
            continue
        if not href.startswith('http'):
            href = 'https://www.sec.gov' + href
        amount, formatted = extract_amount(title)
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": "",
            "category": categorize_settlement(title, "", "SEC"),
            "source": "SEC",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"sec-{href[-60:]}" if href else f"sec-{title[:40]}",
        })
    return results


# ---------------------------------------------------------------------------
# New scrapers – government agencies, settlement administrators
# ---------------------------------------------------------------------------

def _scrape_doj(session: requests.Session) -> List[Dict]:
    """Scrape DOJ press releases via RSS (HTML page is JS-rendered)."""
    from xml.etree import ElementTree as ET

    results = []
    try:
        resp = session.get("https://www.justice.gov/feeds/justice-news.xml", timeout=20)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception as e:
        logger.debug("DOJ RSS fetch failed: %s", e)
        return results

    channel = root.find("channel")
    if channel is None:
        return results
    for item in channel.findall("item")[:30]:
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        combined = f"{title} {description}"
        if not any(kw in combined.lower() for kw in ['settle', 'pay', 'penalty', 'million', 'billion',
                                                       'judgment', 'fine', 'guilty', 'fraud']):
            continue
        amount, formatted = extract_amount(combined)
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": link,
            "description": description[:300],
            "category": categorize_settlement(title, description, "DOJ"),
            "source": "DOJ",
            "pub_date": pub_date or datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"doj-{link[-60:]}" if link else f"doj-{title[:40]}",
        })
    return results


def _scrape_cfpb(session: requests.Session) -> List[Dict]:
    """Scrape CFPB enforcement actions."""
    results = []
    soup = _get(session, "https://www.consumerfinance.gov/enforcement/actions/")
    if not soup:
        return results
    for item in soup.find_all(['article', 'div', 'li'], class_=re.compile(r'action|item|post|row'))[:30]:
        title_el = item.find(['h2', 'h3', 'h4', 'a'])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        text = item.get_text(' ', strip=True)
        amount, formatted = extract_amount(text)
        link = item.find('a', href=True)
        href = link.get('href', '') if link else ''
        if href and not href.startswith('http'):
            href = 'https://www.consumerfinance.gov' + href
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text else "",
            "category": categorize_settlement(title, text, "CFPB"),
            "source": "CFPB",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"cfpb-{href[-60:]}" if href else f"cfpb-{title[:40]}",
        })
    return results


def _scrape_epa(session: requests.Session) -> List[Dict]:
    """Scrape EPA enforcement civil cases."""
    results = []
    soup = _get(session, "https://www.epa.gov/enforcement")
    if not soup:
        return results
    for item in soup.find_all(['article', 'div', 'li'], class_=re.compile(r'case|item|row|news'))[:30]:
        title_el = item.find(['h2', 'h3', 'h4', 'a'])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        text = item.get_text(' ', strip=True)
        if not any(kw in text.lower() for kw in ['settle', 'penalty', 'million', 'billion', 'fine', 'civil',
                                                   'consent', 'decree', 'order']):
            continue
        amount, formatted = extract_amount(text)
        link = item.find('a', href=True)
        href = link.get('href', '') if link else ''
        if href and not href.startswith('http'):
            href = 'https://www.epa.gov' + href
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text else "",
            "category": categorize_settlement(title, text, "EPA"),
            "source": "EPA",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"epa-{href[-60:]}" if href else f"epa-{title[:40]}",
        })
    return results


def _scrape_ca_ag(session: requests.Session) -> List[Dict]:
    """Scrape California Attorney General news for settlements."""
    results = []
    soup = _get(session, "https://oag.ca.gov/media/news")
    if not soup:
        return results
    for item in soup.find_all(['article', 'div', 'li'], class_=re.compile(r'news|item|row|press'))[:30]:
        title_el = item.find(['h2', 'h3', 'h4', 'a'])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        text = item.get_text(' ', strip=True)
        if not any(kw in text.lower() for kw in ['settle', 'pay', 'penalty', 'million', 'billion', 'judgment',
                                                   'fine', 'consent']):
            continue
        amount, formatted = extract_amount(text)
        link = item.find('a', href=True)
        href = link.get('href', '') if link else ''
        if href and not href.startswith('http'):
            href = 'https://oag.ca.gov' + href
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text else "",
            "category": categorize_settlement(title, text, "CA Attorney General"),
            "source": "CA AG",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"caag-{href[-60:]}" if href else f"caag-{title[:40]}",
        })
    return results


def _scrape_ny_ag(session: requests.Session) -> List[Dict]:
    """Scrape New York Attorney General press releases for settlements."""
    results = []
    soup = _get(session, "https://ag.ny.gov/press-releases")
    if not soup:
        return results
    for item in soup.find_all(['article', 'div', 'li'], class_=re.compile(r'press|news|item|row'))[:30]:
        title_el = item.find(['h2', 'h3', 'h4', 'a'])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        text = item.get_text(' ', strip=True)
        if not any(kw in text.lower() for kw in ['settle', 'pay', 'penalty', 'million', 'billion', 'judgment',
                                                   'fine', 'consent']):
            continue
        amount, formatted = extract_amount(text)
        link = item.find('a', href=True)
        href = link.get('href', '') if link else ''
        if href and not href.startswith('http'):
            href = 'https://ag.ny.gov' + href
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text else "",
            "category": categorize_settlement(title, text, "NY Attorney General"),
            "source": "NY AG",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"nyag-{href[-60:]}" if href else f"nyag-{title[:40]}",
        })
    return results


def _scrape_kcc(session: requests.Session) -> List[Dict]:
    """Scrape KCC Class Action Services case listings."""
    results = []
    soup = _get(session, "https://www.kccllc.com")
    if not soup:
        return results
    for item in soup.find_all(['article', 'div', 'li', 'tr'], class_=re.compile(r'case|item|row'))[:30]:
        title_el = item.find(['h2', 'h3', 'h4', 'a', 'td'])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if len(title) < 5:
            continue
        text = item.get_text(' ', strip=True)
        amount, formatted = extract_amount(text)
        link = item.find('a', href=True)
        href = link.get('href', '') if link else ''
        if href and not href.startswith('http'):
            href = 'https://www.kccllc.com' + href
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text else "",
            "category": categorize_settlement(title, text, "KCC"),
            "source": "KCC",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"kcc-{href[-60:]}" if href else f"kcc-{title[:40]}",
        })
    return results


# ---------------------------------------------------------------------------
# Claim signup page scrapers – aggregator sites with direct claim links
# ---------------------------------------------------------------------------

def _scrape_openclassactions(session: requests.Session) -> List[Dict]:
    """Scrape OpenClassActions.com settlement listings (claim signup pages)."""
    results = []
    soup = _get(session, "https://openclassactions.com/settlements.php")
    if not soup:
        return results
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        if '/settlements/' not in href or not href.endswith('.php'):
            continue
        title = link.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        if not href.startswith('http'):
            href = 'https://openclassactions.com' + href
        amount, formatted = extract_amount(title)
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": "",
            "category": categorize_settlement(title, "", "OpenClassActions"),
            "source": "OpenClassActions",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"oca-{href[-60:]}" if href else f"oca-{title[:40]}",
        })
    return results


def _scrape_classactionrebates(session: requests.Session) -> List[Dict]:
    """Scrape ClassActionRebates.com homepage settlement cards."""
    results = []
    seen_urls: set = set()
    soup = _get(session, "https://classactionrebates.com/")
    if not soup:
        return results
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        if '/settlements/' not in href:
            continue
        title = link.get_text(strip=True)
        # Skip generic button text like "file claim"
        if not title or len(title) < 8 or title.lower() in ('file claim', 'learn more', 'read more', 'view details'):
            continue
        if not href.startswith('http'):
            href = 'https://classactionrebates.com' + href
        if href in seen_urls:
            continue
        seen_urls.add(href)
        text = title
        parent = link.find_parent(['div', 'article', 'li'])
        if parent:
            text = parent.get_text(' ', strip=True)
        amount, formatted = extract_amount(text)
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text != title else "",
            "category": categorize_settlement(title, text, "ClassActionRebates"),
            "source": "ClassActionRebates",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"car-{href[-60:]}" if href else f"car-{title[:40]}",
        })
    return results


def _scrape_bigclassaction(session: requests.Session) -> List[Dict]:
    """Scrape BigClassAction.com settlement listings."""
    results = []
    soup = _get(session, "https://bigclassaction.com/settlements/")
    if not soup:
        return results
    container = soup.find('div', class_='full_posts') or soup
    for link in container.find_all('a', href=True):
        href = link.get('href', '')
        if '/settlement/' not in href:
            continue
        title = link.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        if not href.startswith('http'):
            href = 'https://bigclassaction.com' + href
        text = title
        parent = link.find_parent(['div', 'article', 'li'])
        if parent:
            text = parent.get_text(' ', strip=True)
        amount, formatted = extract_amount(text)
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text != title else "",
            "category": categorize_settlement(title, text, "BigClassAction"),
            "source": "BigClassAction",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"bca-{href[-60:]}" if href else f"bca-{title[:40]}",
        })
    return results


def _scrape_jnd(session: requests.Session) -> List[Dict]:
    """Scrape JND Legal Administration case listings.

    JND hosts each case on its own external domain. The /cases/* pages contain
    'Visit Case Website' links alongside case names in modal divs.
    """
    results = []
    seen_urls: set = set()
    pages = [
        "https://www.jndla.com/cases/class-action-administration",
        "https://www.jndla.com/cases/government-services",
    ]
    for page_url in pages:
        soup = _get(session, page_url)
        if not soup:
            continue
        for link in soup.find_all('a', href=True):
            if 'Visit Case Website' not in link.get_text():
                continue
            href = link.get('href', '').rstrip('/')
            if not href or not href.startswith('http'):
                continue
            if href in seen_urls:
                continue
            seen_urls.add(href)
            # Walk up to find the case name from parent container
            title = ""
            parent = link.parent
            for _ in range(5):
                if parent is None:
                    break
                text = parent.get_text(strip=True).replace('Visit Case Website', '').strip()
                if len(text) > 3:
                    title = text.split('\n')[0].strip()[:200]
                    break
                parent = parent.parent
            if not title:
                # Derive from domain name
                title = href.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
            amount, formatted = extract_amount(title)
            results.append({
                "title": title[:200],
                "amount": amount,
                "amount_formatted": formatted,
                "url": href,
                "description": "",
                "category": categorize_settlement(title, "", "JND Legal"),
                "source": "JND Legal",
                "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
                "guid": f"jnd-{href[-60:]}" if href else f"jnd-{title[:40]}",
            })
    return results


# ---------------------------------------------------------------------------
# Playwright-based scrapers – sites that block plain requests
# ---------------------------------------------------------------------------

def _scrape_topclassactions(session: requests.Session) -> List[Dict]:
    """Scrape TopClassActions.com open settlements using Playwright.

    The site returns 403 for plain requests but works with headless Chromium.
    Fetches the open-settlements listing, extracts article links, and follows
    each article to find claim form URLs via extract_claim_url().
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, skipping TopClassActions")
        return []

    results = []
    seen_urls: set = set()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            # Fetch the open settlements listing (URL structure changed over time)
            listing_urls = [
                "https://topclassactions.com/category/lawsuit-settlements/open-lawsuit-settlements/",
                "https://topclassactions.com/category/open-settlements/",
            ]
            listing_soup = None
            for listing_url in listing_urls:
                try:
                    page.goto(listing_url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(3000)
                    listing_html = page.content()
                    soup_candidate = BeautifulSoup(listing_html, "html.parser")
                    title_tag = soup_candidate.find("title")
                    if title_tag and "not found" in title_tag.get_text().lower():
                        continue
                    listing_soup = soup_candidate
                    break
                except Exception:
                    continue

            if not listing_soup:
                logger.warning("TopClassActions: could not load listing page")
                context.close()
                browser.close()
                return results

            # Extract article links — match settlement article paths
            article_links = []
            for a in listing_soup.find_all("a", href=True):
                href = a["href"]
                # Match article paths like /lawsuit-settlement/..., individual post URLs
                if not re.search(r"/lawsuit-settlement/|/settlement/|/open-lawsuit-settlement", href):
                    continue
                # Skip category/tag listing pages
                if re.search(r"/category/|/tag/|/page/\d", href):
                    continue
                title = a.get_text(strip=True)
                if not title or len(title) < 10:
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                if not href.startswith("http"):
                    href = "https://topclassactions.com" + href
                article_links.append((href, title))

            logger.info("TopClassActions: found %d article links", len(article_links))

            # Follow each article to extract claim URLs (limit to first 30)
            for article_url, title in article_links[:30]:
                text = title
                claim_url = None
                try:
                    page.goto(article_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2000)
                    article_html = page.content()
                    article_soup = BeautifulSoup(article_html, "html.parser")
                    text = article_soup.get_text(" ", strip=True)[:500]
                    claim_url = extract_claim_url(article_soup, article_url)
                except Exception as e:
                    logger.debug("TopClassActions article fetch failed for %s: %s", article_url, e)

                amount, formatted = extract_amount(text)
                deadline = extract_deadline(text)
                entry = {
                    "title": title[:200],
                    "amount": amount,
                    "amount_formatted": formatted,
                    "url": article_url,
                    "description": text[:300] if text != title else "",
                    "category": categorize_settlement(title, text, "TopClassActions"),
                    "source": "TopClassActions",
                    "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                    "guid": f"tca-{article_url[-60:]}" if article_url else f"tca-{title[:40]}",
                    "claim_deadline": deadline,
                }
                if claim_url:
                    entry["claim_url"] = claim_url
                results.append(entry)

            context.close()
            browser.close()
    except Exception as e:
        logger.warning("TopClassActions scraper failed: %s", e)

    return results


def _scrape_claimdepot(session: requests.Session) -> List[Dict]:
    """Scrape ClaimDepot.com settlement listings."""
    results = []
    seen_urls: set = set()
    soup = _get(session, "https://www.claimdepot.com/")
    if not soup:
        return results

    # Look for settlement cards/listings — try common container patterns
    containers = soup.find_all(
        ["article", "div", "li", "a"],
        class_=re.compile(r"settlement|claim|card|listing|post|item", re.IGNORECASE),
    )
    # Fallback: if no class-matched containers, scan all links
    if not containers:
        containers = soup.find_all("a", href=True)

    for item in containers[:50]:
        # Find the primary link
        if item.name == "a":
            link = item
        else:
            link = item.find("a", href=True)
        if not link:
            continue

        href = link.get("href", "")
        if not href or href == "/":
            continue
        if not href.startswith("http"):
            href = "https://www.claimdepot.com" + href

        # Skip nav/footer links
        if any(skip in href.lower() for skip in ["/about", "/contact", "/privacy", "/terms", "/faq", "/login"]):
            continue

        if href in seen_urls:
            continue
        seen_urls.add(href)

        title_el = item.find(["h2", "h3", "h4", "h5"]) if item.name != "a" else None
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
        if not title or len(title) < 5:
            continue

        text = item.get_text(" ", strip=True) if item.name != "a" else title
        amount, formatted = extract_amount(text)
        deadline = extract_deadline(text)

        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text != title else "",
            "category": categorize_settlement(title, text, "ClaimDepot"),
            "source": "ClaimDepot",
            "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
            "guid": f"cd-{href[-60:]}" if href else f"cd-{title[:40]}",
            "claim_deadline": deadline,
        })

    return results


# ---------------------------------------------------------------------------
# Additional settlement administrator + aggregator scrapers
# ---------------------------------------------------------------------------

def _scrape_lawyers_and_settlements(session: requests.Session) -> List[Dict]:
    """Scrape LawyersAndSettlements.com — paginated HTML settlement listings."""
    results = []
    seen_urls: set = set()

    for page_num in range(1, 6):  # first 5 pages
        url = f"https://www.lawyersandsettlements.com/settlements/all/?page={page_num}"
        soup = _get(session, url)
        if not soup:
            break

        found_any = False
        for li in soup.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            href = a.get("href", "")
            if "/settlements/" not in href or not href.endswith(".html"):
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            if not href.startswith("http"):
                href = "https://www.lawyersandsettlements.com" + href
            if href in seen_urls:
                continue
            seen_urls.add(href)
            found_any = True

            text = li.get_text(" ", strip=True)
            amount, formatted = extract_amount(text)
            # Extract date from <span class="date">
            date_span = li.find("span", class_="date")
            date_text = date_span.get_text(strip=True).strip("()") if date_span else ""

            results.append({
                "title": title[:200],
                "amount": amount,
                "amount_formatted": formatted,
                "url": href,
                "description": text[:300] if text != title else "",
                "category": categorize_settlement(title, text, "LawyersAndSettlements"),
                "source": "LawyersAndSettlements",
                "pub_date": date_text or datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                "guid": f"las-{href[-60:]}" if href else f"las-{title[:40]}",
            })

        if not found_any:
            break
        time.sleep(0.5)

    return results


def _scrape_simpluris(session: requests.Session) -> List[Dict]:
    """Scrape Simpluris open settlements via WP REST API and case search page."""
    results = []
    seen_titles: set = set()

    # 1. WP REST API for case studies
    try:
        resp = session.get(
            "https://www.simpluris.com/wp-json/wp/v2/case-study",
            params={"per_page": 100},
            timeout=20,
        )
        if resp.status_code == 200:
            for item in resp.json():
                title = BeautifulSoup(item.get("title", {}).get("rendered", ""), "html.parser").get_text(strip=True)
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                link = item.get("link", "")
                content = BeautifulSoup(item.get("content", {}).get("rendered", ""), "html.parser").get_text(" ", strip=True)
                amount, formatted = extract_amount(f"{title} {content}")
                results.append({
                    "title": title[:200],
                    "amount": amount,
                    "amount_formatted": formatted,
                    "url": link,
                    "description": content[:300],
                    "category": categorize_settlement(title, content, "Simpluris"),
                    "source": "Simpluris",
                    "pub_date": item.get("date", datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")),
                    "guid": f"simp-{link[-60:]}" if link else f"simp-{title[:40]}",
                })
    except Exception as e:
        logger.debug("Simpluris REST API failed: %s", e)

    # 2. Representative cases page — plain text listings
    soup = _get(session, "https://www.simpluris.com/representative-cases/")
    if soup:
        for div in soup.find_all("div", class_=re.compile(r"fl-rich-text")):
            for p in div.find_all("p"):
                text = p.get_text(" ", strip=True)
                # Each line is typically a case name
                for line in text.split("\n"):
                    line = line.strip()
                    if len(line) < 10 or line in seen_titles:
                        continue
                    # Skip category headers
                    if line.endswith(":") or line.isupper():
                        continue
                    seen_titles.add(line)
                    amount, formatted = extract_amount(line)
                    results.append({
                        "title": line[:200],
                        "amount": amount,
                        "amount_formatted": formatted,
                        "url": "https://www.simpluris.com/representative-cases/",
                        "description": "",
                        "category": categorize_settlement(line, "", "Simpluris"),
                        "source": "Simpluris",
                        "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                        "guid": f"simp-{line[:60]}",
                    })

    return results


def _scrape_angeion(session: requests.Session) -> List[Dict]:
    """Scrape Angeion Group homepage for active case cards."""
    results = []
    soup = _get(session, "https://www.angeiongroup.com/")
    if not soup:
        return results

    # Active mass tort cards
    for card in soup.find_all("div", class_=re.compile(r"active-mass-torts|card-01_content|case-card")):
        title_el = card.find(["h2", "h3", "h4", "h5", "a", "p"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 3:
            continue
        text = card.get_text(" ", strip=True)
        link = card.find("a", href=True)
        href = link.get("href", "") if link else ""
        if href and not href.startswith("http"):
            href = "https://www.angeiongroup.com" + href
        amount, formatted = extract_amount(text)
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href or "https://www.angeiongroup.com/",
            "description": text[:300] if text != title else "",
            "category": categorize_settlement(title, text, "Angeion Group"),
            "source": "Angeion Group",
            "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
            "guid": f"ang-{href[-60:]}" if href else f"ang-{title[:40]}",
        })

    # Also check /cases or /class-action for case listings
    for path in ["/class-action", "/cases"]:
        case_soup = _get(session, f"https://www.angeiongroup.com{path}")
        if not case_soup:
            continue
        for a in case_soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if len(text) < 10:
                continue
            # Look for links to external settlement sites
            if not href.startswith("http"):
                href = "https://www.angeiongroup.com" + href
            from urllib.parse import urlparse as _urlparse
            domain = _urlparse(href).netloc.lower()
            if "angeiongroup.com" in domain:
                continue  # skip internal nav links
            if any(kw in domain for kw in ["settlement", "claim", "classaction"]):
                amount, formatted = extract_amount(text)
                results.append({
                    "title": text[:200],
                    "amount": amount,
                    "amount_formatted": formatted,
                    "url": href,
                    "description": "",
                    "category": categorize_settlement(text, "", "Angeion Group"),
                    "source": "Angeion Group",
                    "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                    "guid": f"ang-{href[-60:]}",
                })

    return results


def _scrape_epiq(session: requests.Session) -> List[Dict]:
    """Scrape Epiq Global cases via their public API endpoints.

    The main search at dm.epiq11.com is an Angular SPA, but some API
    endpoints are accessible without auth.
    """
    results = []
    seen: set = set()
    base = "https://dm.epiq11.com"

    # Get list of industries to use as search facets
    industries = []
    try:
        resp = session.get(f"{base}/api/search/getindustry", timeout=15)
        if resp.status_code == 200:
            industries = resp.json()
    except Exception:
        pass

    # Try the public cases page — even though it's an SPA, the initial HTML
    # may have case data or we can try known project codes
    # Use Playwright if available to render the SPA
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug("Playwright not available for Epiq SPA rendering")
        return results

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(f"{base}/search/searchcases", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(5000)

            # Wait for case cards to render
            try:
                page.wait_for_selector(".case-card, .card, [class*='case']", timeout=10000)
            except Exception:
                pass

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            # Look for case cards/links
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)
                if not text or len(text) < 5:
                    continue
                # Match case detail links
                if not re.search(r"/case/|/cases/|projectCode=", href, re.IGNORECASE):
                    continue
                if text in seen:
                    continue
                seen.add(text)
                if not href.startswith("http"):
                    href = base + href
                amount, formatted = extract_amount(text)
                results.append({
                    "title": text[:200],
                    "amount": amount,
                    "amount_formatted": formatted,
                    "url": href,
                    "description": "",
                    "category": categorize_settlement(text, "", "Epiq"),
                    "source": "Epiq",
                    "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                    "guid": f"epiq-{href[-60:]}" if href else f"epiq-{text[:40]}",
                })

            context.close()
            browser.close()
    except Exception as e:
        logger.warning("Epiq scraper failed: %s", e)

    return results


# ---------------------------------------------------------------------------
# FTC Refunds + State AG scrapers
# ---------------------------------------------------------------------------

_SETTLE_KEYWORDS = ['settle', 'pay', 'penalty', 'million', 'billion', 'judgment',
                    'fine', 'consent', 'refund', 'restitution', 'fraud', 'consumer']


def _scrape_ftc_refunds(session: requests.Session) -> List[Dict]:
    """Scrape FTC active refund programs — these have direct claim/refund links."""
    results = []
    soup = _get(session, "https://www.ftc.gov/enforcement/refunds")
    if not soup:
        return results

    # Target the refund listing view
    container = soup.find("div", class_=re.compile(r"view-refund-index|view-content")) or soup
    for article in container.find_all(["article", "div", "li"], class_=re.compile(r"node|item|row|refund"))[:50]:
        title_el = article.find(["h2", "h3", "h4", "a"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        text = article.get_text(" ", strip=True)
        link = article.find("a", href=True)
        href = link.get("href", "") if link else ""
        if href and not href.startswith("http"):
            href = "https://www.ftc.gov" + href
        amount, formatted = extract_amount(text)
        deadline = extract_deadline(text)
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text != title else "",
            "category": "Consumer Protection",
            "source": "FTC Refunds",
            "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
            "guid": f"ftcr-{href[-60:]}" if href else f"ftcr-{title[:40]}",
            "claim_deadline": deadline,
        })
    return results


def _scrape_tx_ag(session: requests.Session) -> List[Dict]:
    """Scrape Texas Attorney General press releases (settlements category)."""
    results = []
    for url in [
        "https://www.texasattorneygeneral.gov/news/categories/settlements",
        "https://www.texasattorneygeneral.gov/news",
    ]:
        soup = _get(session, url)
        if not soup:
            continue
        for card in soup.find_all(["div", "article", "li"], class_=re.compile(r"card|news|item|press|post"))[:30]:
            title_el = card.find(["h2", "h3", "h4", "a"])
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            text = card.get_text(" ", strip=True)
            if not any(kw in text.lower() for kw in _SETTLE_KEYWORDS):
                continue
            link = card.find("a", href=True)
            href = link.get("href", "") if link else ""
            if href and not href.startswith("http"):
                href = "https://www.texasattorneygeneral.gov" + href
            amount, formatted = extract_amount(text)
            results.append({
                "title": title[:200],
                "amount": amount,
                "amount_formatted": formatted,
                "url": href,
                "description": text[:300] if text != title else "",
                "category": categorize_settlement(title, text, "TX Attorney General"),
                "source": "TX AG",
                "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                "guid": f"txag-{href[-60:]}" if href else f"txag-{title[:40]}",
            })
    return results


def _scrape_fl_ag(session: requests.Session) -> List[Dict]:
    """Scrape Florida Attorney General news releases."""
    results = []
    soup = _get(session, "https://www.myfloridalegal.com/newsreleases")
    if not soup:
        return results
    container = soup.find("section", class_=re.compile(r"news")) or soup
    for item in container.find_all(["article", "div", "li", "a"], class_=re.compile(r"news|item|release|press|card"))[:30]:
        if item.name == "a":
            link = item
        else:
            link = item.find("a", href=True)
        if not link:
            continue
        title = link.get_text(strip=True)
        if not title or len(title) < 10:
            continue
        text = item.get_text(" ", strip=True) if item.name != "a" else title
        if not any(kw in text.lower() for kw in _SETTLE_KEYWORDS):
            continue
        href = link.get("href", "")
        if href and not href.startswith("http"):
            href = "https://www.myfloridalegal.com" + href
        amount, formatted = extract_amount(text)
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text != title else "",
            "category": categorize_settlement(title, text, "FL Attorney General"),
            "source": "FL AG",
            "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
            "guid": f"flag-{href[-60:]}" if href else f"flag-{title[:40]}",
        })
    return results


def _scrape_wa_ag(session: requests.Session) -> List[Dict]:
    """Scrape Washington Attorney General news."""
    results = []
    soup = _get(session, "https://www.atg.wa.gov/news")
    if not soup:
        return results
    for item in soup.find_all(["article", "div", "li"], class_=re.compile(r"news|item|views-row|press"))[:30]:
        title_el = item.find(["h2", "h3", "h4", "a"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        text = item.get_text(" ", strip=True)
        if not any(kw in text.lower() for kw in _SETTLE_KEYWORDS):
            continue
        link = item.find("a", href=True)
        href = link.get("href", "") if link else ""
        if href and not href.startswith("http"):
            href = "https://www.atg.wa.gov" + href
        amount, formatted = extract_amount(text)
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text != title else "",
            "category": categorize_settlement(title, text, "WA Attorney General"),
            "source": "WA AG",
            "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
            "guid": f"waag-{href[-60:]}" if href else f"waag-{title[:40]}",
        })
    return results


def _scrape_oh_ag(session: requests.Session) -> List[Dict]:
    """Scrape Ohio Attorney General news releases."""
    results = []
    soup = _get(session, "https://www.ohioattorneygeneral.gov/Media/News-Releases")
    if not soup:
        return results
    for item in soup.find_all("div", class_=re.compile(r"ohio-news|news-item"))[:30]:
        title_el = item.find(["h2", "h3", "h4", "a"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        text = item.get_text(" ", strip=True)
        if not any(kw in text.lower() for kw in _SETTLE_KEYWORDS):
            continue
        link = item.find("a", href=True)
        href = link.get("href", "") if link else ""
        if href and not href.startswith("http"):
            href = "https://www.ohioattorneygeneral.gov" + href
        amount, formatted = extract_amount(text)
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text != title else "",
            "category": categorize_settlement(title, text, "OH Attorney General"),
            "source": "OH AG",
            "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
            "guid": f"ohag-{href[-60:]}" if href else f"ohag-{title[:40]}",
        })
    return results


def _scrape_nj_ag(session: requests.Session) -> List[Dict]:
    """Scrape New Jersey Attorney General news (WordPress/Divi)."""
    results = []
    seen_urls: set = set()
    # Scrape first 3 pages
    for page_num in range(1, 4):
        url = "https://www.njoag.gov/news/" if page_num == 1 else f"https://www.njoag.gov/news/page/{page_num}/"
        soup = _get(session, url)
        if not soup:
            break
        found_any = False
        for article in soup.find_all("article", class_=re.compile(r"et_pb_post|post"))[:15]:
            title_el = article.find(["h2", "h3", "h4"])
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            text = article.get_text(" ", strip=True)
            if not any(kw in text.lower() for kw in _SETTLE_KEYWORDS):
                continue
            link = title_el.find("a", href=True) or article.find("a", href=True)
            href = link.get("href", "") if link else ""
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)
            found_any = True
            amount, formatted = extract_amount(text)
            results.append({
                "title": title[:200],
                "amount": amount,
                "amount_formatted": formatted,
                "url": href,
                "description": text[:300] if text != title else "",
                "category": categorize_settlement(title, text, "NJ Attorney General"),
                "source": "NJ AG",
                "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                "guid": f"njag-{href[-60:]}" if href else f"njag-{title[:40]}",
            })
        if not found_any:
            break
        time.sleep(0.3)
    return results


def _scrape_ga_ag(session: requests.Session) -> List[Dict]:
    """Scrape Georgia Attorney General press releases + settlements page."""
    results = []
    for url, base in [
        ("https://law.georgia.gov/press-releases", "https://law.georgia.gov"),
        ("https://law.georgia.gov/resources/settlements", "https://law.georgia.gov"),
    ]:
        soup = _get(session, url)
        if not soup:
            continue
        for item in soup.find_all(["div", "article", "li"],
                                   class_=re.compile(r"news-teaser|press|item|row|card"))[:30]:
            title_el = item.find(["h2", "h3", "h4", "a"])
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            text = item.get_text(" ", strip=True)
            if not any(kw in text.lower() for kw in _SETTLE_KEYWORDS):
                continue
            link = item.find("a", href=True)
            href = link.get("href", "") if link else ""
            if href and not href.startswith("http"):
                href = base + href
            amount, formatted = extract_amount(text)
            results.append({
                "title": title[:200],
                "amount": amount,
                "amount_formatted": formatted,
                "url": href,
                "description": text[:300] if text != title else "",
                "category": categorize_settlement(title, text, "GA Attorney General"),
                "source": "GA AG",
                "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                "guid": f"gaag-{href[-60:]}" if href else f"gaag-{title[:40]}",
            })
    return results


def _scrape_krazy_coupon_lady(session: requests.Session) -> List[Dict]:
    """Scrape TheKrazyCouponLady class action settlements page.

    This aggregator curates consumer settlements and links to direct claim
    form URLs — many of which are simple HTML forms amenable to auto-fill.
    """
    results = []
    url = "https://thekrazycouponlady.com/tips/money/unclaimed-money-class-action-settlements"
    soup = _get(session, url)
    if not soup:
        return results

    # Look for settlement entries — typically <h2>/<h3> headings with links
    for heading in soup.find_all(["h2", "h3", "h4"]):
        title = heading.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        # Skip non-settlement headings
        title_lower = title.lower()
        if any(skip in title_lower for skip in ["table of contents", "how to", "what is", "faq", "related"]):
            continue

        # Gather links from the section following this heading
        claim_url = ""
        description = ""
        sibling = heading.find_next_sibling()
        section_text = ""
        for _ in range(5):  # scan up to 5 siblings
            if sibling is None or sibling.name in ("h2", "h3"):
                break
            section_text += " " + sibling.get_text(" ", strip=True)
            for a in sibling.find_all("a", href=True):
                href = a["href"]
                atext = a.get_text(strip=True).lower()
                if any(kw in atext for kw in ["file a claim", "claim form", "submit a claim", "file claim",
                                               "claim here", "submit claim", "file your claim"]):
                    claim_url = href
                    break
                if any(kw in href.lower() for kw in ["/claim", "submit-claim", "file-claim", "claimform"]):
                    claim_url = href
            sibling = sibling.find_next_sibling() if sibling else None

        if not claim_url:
            continue

        amount, formatted = extract_amount(title + " " + section_text)
        description = section_text.strip()[:300]
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": claim_url,
            "claim_url": claim_url,
            "description": description,
            "category": categorize_settlement(title, description),
            "source": "KrazyCouponLady",
            "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
            "guid": f"kcl-{claim_url[-60:]}",
        })
    return results


def _scrape_fileyourclaim(session: requests.Session) -> List[Dict]:
    """Scrape FileYourClaim.co — curated open settlements with claim links.

    Page uses ``s-card`` divs with ``s-card-company`` for name,
    ``s-card-desc`` for description, ``s-card-claim > a`` for claim URL.
    """
    results = []
    url = "https://fileyourclaim.co/"
    soup = _get(session, url)
    if not soup:
        return results

    seen_urls: set = set()
    for card in soup.find_all("div", class_=re.compile(r"settlement-item")):
        company_el = card.find(class_="s-card-company")
        if not company_el:
            continue
        title = company_el.get_text(strip=True)
        if not title or len(title) < 5:
            continue

        desc_el = card.find(class_="s-card-desc")
        description = desc_el.get_text(strip=True)[:300] if desc_el else ""

        claim_el = card.find(class_="s-card-claim")
        claim_link = claim_el.find("a", href=True) if claim_el else None
        claim_url = claim_link["href"] if claim_link else ""
        if not claim_url or claim_url in seen_urls:
            continue
        seen_urls.add(claim_url)

        text = card.get_text(" ", strip=True)
        amount, formatted = extract_amount(text)
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": claim_url,
            "claim_url": claim_url,
            "description": description,
            "category": categorize_settlement(title, description),
            "source": "FileYourClaim",
            "pub_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
            "guid": f"fyc-{claim_url[-60:]}",
        })
    return results


# ---------------------------------------------------------------------------
# Dorker – search engine queries via ddgs
# ---------------------------------------------------------------------------

QUICK_DORK_QUERIES = [
    # Claim signup / filing pages
    '"file a claim" "settlement" "deadline" 2026',
    '"file a claim" "settlement" "deadline" 2025',
    '"submit a claim" "class action" "settlement"',
    '"claim form" "settlement" "$" million',
    '"claim deadline" "settlement" 2026',
    '"claim deadline" "settlement" 2025',
    # Admin site claim portals
    'site:epiqglobal.com "file a claim"',
    'site:jndla.com "settlement" "claim"',
    'site:simpluris.com "file a claim"',
    'site:angeiongroup.com "submit" "claim"',
    'site:kccllc.com "settlement" "claim"',
    'site:rustconsulting.com "class action" "claim"',
    # Aggregator open settlement listings
    'site:classactionrebates.com "claim"',
    'site:openclassactions.com "settlement"',
    'site:topclassactions.com "open" "settlement" "file a claim"',
    'site:classaction.org "settlement" "file a claim"',
    # Data breach claim pages (high public interest)
    '"data breach" "file a claim" "$" million 2026',
    '"data breach" "settlement" "claim form" 2026',
    '"data breach" "settlement" "claim form" 2025',
    # Targeted queries for pages with actual fillable HTML forms
    '"file a claim" "first name" "last name" "email"',
    '"claim form" "submit" "name" "address" "email" settlement',
    '"file your claim" settlement 2026',
    '"online claim form" settlement',
    'inurl:claim "first name" "email" settlement',
]


def run_dorker(categories: Optional[List[str]] = None, max_per_query: int = 5) -> List[Dict]:
    """Run search engine dorking for settlements. Returns list of settlement dicts."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        try:
            from ddgs import DDGS
        except ImportError:
            logger.warning("No ddgs library available for dorking")
            return []

    queries = list(QUICK_DORK_QUERIES)

    # Always pull settlement_admin and aggregators tiers for claim-focused results
    try:
        from scripts.settlement_dorker import DORK_TEMPLATES
        queries.extend(DORK_TEMPLATES.get('settlement_admin', []))
        queries.extend(DORK_TEMPLATES.get('aggregators', []))
        # If specific categories requested, add those too
        if categories:
            for cat in categories:
                if cat not in ('settlement_admin', 'aggregators'):
                    queries.extend(DORK_TEMPLATES.get(cat, []))
    except ImportError:
        pass

    results = []
    seen_urls: set = set()
    ddgs = DDGS()

    for query in queries:
        try:
            ddg_results = ddgs.text(query, max_results=max_per_query)
            for r in ddg_results:
                url = r.get('href', '') or r.get('link', '')
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                title = r.get('title', '')
                snippet = r.get('body', '') or r.get('snippet', '')
                combined = title + ' ' + snippet
                amount, formatted = extract_amount(combined)
                results.append({
                    "title": title[:200],
                    "amount": amount,
                    "amount_formatted": formatted,
                    "url": url,
                    "description": snippet[:300],
                    "category": None,
                    "source": "DuckDuckGo Dork",
                    "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
                    "guid": f"ddg-{url[-80:]}",
                })
        except Exception as e:
            logger.debug("Dork query failed: %s – %s", query[:40], e)
        time.sleep(1)  # rate limit

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_scrape() -> List[Dict]:
    """Run all web scrapers and return settlement dicts.

    Aggregator scrapers (OCA, CAR, BCA) get enriched via detail page fetches
    to extract claim_url and claim_deadline. JND already provides claim_url.
    Government scrapers (FTC, DOJ, etc.) are left as-is.
    """
    session = _make_session()
    all_settlements: List[Dict] = []

    # Aggregator scrapers — will be enriched with detail page data
    enrichable_scrapers = [
        ("OpenClassActions", _scrape_openclassactions),
        ("ClassActionRebates", _scrape_classactionrebates),
        ("BigClassAction", _scrape_bigclassaction),
        ("JND Legal", _scrape_jnd),
        ("TopClassActions", _scrape_topclassactions),
        ("ClaimDepot", _scrape_claimdepot),
        ("LawyersAndSettlements", _scrape_lawyers_and_settlements),
        ("Angeion Group", _scrape_angeion),
        ("Epiq", _scrape_epiq),
        ("KrazyCouponLady", _scrape_krazy_coupon_lady),
        ("FileYourClaim", _scrape_fileyourclaim),
    ]

    # Direct scrapers — gov scrapers have no claim portals, Simpluris has case names only
    direct_scrapers = [
        ("ClassAction.org", _scrape_classaction_org),
        ("FTC", _scrape_ftc),
        ("FTC Refunds", _scrape_ftc_refunds),
        ("SEC", _scrape_sec),
        ("DOJ", _scrape_doj),
        ("CFPB", _scrape_cfpb),
        ("EPA", _scrape_epa),
        ("CA AG", _scrape_ca_ag),
        ("NY AG", _scrape_ny_ag),
        ("TX AG", _scrape_tx_ag),
        ("FL AG", _scrape_fl_ag),
        ("WA AG", _scrape_wa_ag),
        ("OH AG", _scrape_oh_ag),
        ("NJ AG", _scrape_nj_ag),
        ("GA AG", _scrape_ga_ag),
        ("KCC", _scrape_kcc),
        ("Simpluris", _scrape_simpluris),
    ]

    # Run enrichable scrapers and fetch detail pages for claim data
    for name, fn in enrichable_scrapers:
        try:
            results = fn(session)
            logger.info("Scraper %s found %d settlements, enriching...", name, len(results))
            results = _enrich_with_detail_page(session, results)
            all_settlements.extend(results)
        except Exception as e:
            logger.warning("Scraper %s failed: %s", name, e)
        time.sleep(0.5)

    # Run direct scrapers as-is
    for name, fn in direct_scrapers:
        try:
            results = fn(session)
            all_settlements.extend(results)
            logger.info("Scraper %s found %d settlements", name, len(results))
        except Exception as e:
            logger.warning("Scraper %s failed: %s", name, e)
        time.sleep(0.5)

    return all_settlements


def run_scrape_and_store() -> Dict:
    """Run scrapers and upsert results into the app database."""
    from ..models.db import upsert_settlements_batch

    settlements = run_scrape()
    upsert_settlements_batch(settlements)
    return {"scraped": len(settlements), "source": "bulk_scraper"}


def run_dorker_and_store(categories: Optional[List[str]] = None) -> Dict:
    """Run dorker and upsert results into the app database."""
    from ..models.db import upsert_settlements_batch

    settlements = run_dorker(categories=categories)
    upsert_settlements_batch(settlements)
    return {"scraped": len(settlements), "source": "dorker"}


def refresh_all() -> Dict:
    """Run scrapers, dorker, and RSS feeds, store results."""
    scrape_result = run_scrape_and_store()
    dorker_result = run_dorker_and_store()

    feeds_count = 0
    try:
        from .settlement_feeds import run_feeds_and_store
        feeds_result = run_feeds_and_store()
        feeds_count = feeds_result.get("stored", 0)
    except Exception as e:
        logger.warning("Feed ingestion failed: %s", e)

    return {
        "scraped": scrape_result["scraped"],
        "dorked": dorker_result["scraped"],
        "feeds": feeds_count,
        "total": scrape_result["scraped"] + dorker_result["scraped"] + feeds_count,
    }
