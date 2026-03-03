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
    ]

    # Direct scrapers — gov scrapers have no claim portals
    direct_scrapers = [
        ("ClassAction.org", _scrape_classaction_org),
        ("FTC", _scrape_ftc),
        ("SEC", _scrape_sec),
        ("DOJ", _scrape_doj),
        ("CFPB", _scrape_cfpb),
        ("EPA", _scrape_epa),
        ("CA AG", _scrape_ca_ag),
        ("NY AG", _scrape_ny_ag),
        ("KCC", _scrape_kcc),
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
