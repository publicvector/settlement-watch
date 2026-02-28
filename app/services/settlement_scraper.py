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

def _scrape_topclassactions(session: requests.Session) -> List[Dict]:
    """Scrape TopClassActions.com open settlements."""
    results = []
    soup = _get(session, "https://topclassactions.com/category/lawsuit-settlements/open-lawsuit-settlements/")
    if not soup:
        return results
    for article in soup.find_all('article')[:30]:
        title_el = article.find(['h2', 'h3', 'a'])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        text = article.get_text(' ', strip=True)
        amount, formatted = extract_amount(text)
        link = article.find('a', href=True)
        href = link.get('href', '') if link else ''
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text else "",
            "category": categorize_settlement(title, text, "TopClassActions"),
            "source": "TopClassActions",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"tca-{href[-60:]}" if href else f"tca-{title[:40]}",
        })
    return results


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
    soup = _get(session, "https://www.sec.gov/litigation/litreleases.htm")
    if not soup:
        return results
    for link in soup.find_all('a', href=re.compile(r'/litigation/litreleases/'))[:30]:
        title = link.get_text(strip=True)
        href = link.get('href', '')
        if href and not href.startswith('http'):
            href = 'https://www.sec.gov' + href
        if any(t in title.lower() for t in ['settle', 'pay', 'million', 'penalty', 'judgment']):
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
    """Scrape DOJ press releases for settlement news."""
    results = []
    soup = _get(session, "https://www.justice.gov/news/press-releases")
    if not soup:
        return results
    for item in soup.find_all(['article', 'div', 'li'], class_=re.compile(r'press|news|item|row'))[:30]:
        title_el = item.find(['h2', 'h3', 'a'])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        text = item.get_text(' ', strip=True)
        if not any(kw in text.lower() for kw in ['settle', 'pay', 'penalty', 'million', 'billion', 'judgment', 'fine']):
            continue
        amount, formatted = extract_amount(text)
        link = item.find('a', href=True)
        href = link.get('href', '') if link else ''
        if href and not href.startswith('http'):
            href = 'https://www.justice.gov' + href
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text else "",
            "category": categorize_settlement(title, text, "DOJ"),
            "source": "DOJ",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"doj-{href[-60:]}" if href else f"doj-{title[:40]}",
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


def _scrape_epiq(session: requests.Session) -> List[Dict]:
    """Scrape Epiq Global case listings."""
    results = []
    soup = _get(session, "https://www.epiqglobal.com/en-us/cases")
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
            href = 'https://www.epiqglobal.com' + href
        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": href,
            "description": text[:300] if text else "",
            "category": categorize_settlement(title, text, "Epiq"),
            "source": "Epiq",
            "pub_date": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": f"epiq-{href[-60:]}" if href else f"epiq-{title[:40]}",
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
# Dorker – search engine queries via ddgs
# ---------------------------------------------------------------------------

QUICK_DORK_QUERIES = [
    '"class action settlement" "$" million 2026',
    '"class action settlement" "$" million 2025',
    'site:topclassactions.com "settlement" "open"',
    'site:classaction.org "settlement" "$" million',
    '"settlement" "$" "billion" 2026',
    '"settlement" "$" "billion" 2025',
    'site:justice.gov "settlement" "$" million 2026',
    'site:ftc.gov "settlement" "$"',
    'site:sec.gov "settlement" "$" million',
    '"data breach" "settlement" "$" million 2026',
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

    # If specific categories requested, pull from dorker templates
    if categories:
        try:
            from scripts.settlement_dorker import DORK_TEMPLATES
            for cat in categories:
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
    """Run all web scrapers and return settlement dicts."""
    session = _make_session()
    all_settlements: List[Dict] = []

    scrapers = [
        ("TopClassActions", _scrape_topclassactions),
        ("ClassAction.org", _scrape_classaction_org),
        ("FTC", _scrape_ftc),
        ("SEC", _scrape_sec),
        ("DOJ", _scrape_doj),
        ("CFPB", _scrape_cfpb),
        ("EPA", _scrape_epa),
        ("CA AG", _scrape_ca_ag),
        ("NY AG", _scrape_ny_ag),
        ("Epiq", _scrape_epiq),
        ("KCC", _scrape_kcc),
    ]

    for name, fn in scrapers:
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
