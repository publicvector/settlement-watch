#!/usr/bin/env python3
"""
Case Outcomes Scraper

Scrapes settlement administration sites to extract:
- Complaint documents and filing dates
- Settlement amounts and dates
- Case metadata (court, parties, case type)

Maps complaints to settlement outcomes for analysis.
"""
import argparse
import asyncio
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from pathlib import Path

# Try playwright, fall back to requests
try:
    from playwright.async_api import async_playwright, Page, Browser
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

import requests
from bs4 import BeautifulSoup


@dataclass
class CaseOutcome:
    """Represents a complaint-to-settlement outcome."""
    # Case identification
    case_number: Optional[str] = None
    case_title: Optional[str] = None
    court: Optional[str] = None
    jurisdiction: Optional[str] = None  # 'federal' or 'state'
    state: Optional[str] = None
    nature_of_suit: Optional[str] = None
    case_type: Optional[str] = None

    # Complaint info
    complaint_date: Optional[str] = None
    complaint_url: Optional[str] = None
    complaint_pdf_url: Optional[str] = None
    initial_demand: Optional[float] = None
    initial_demand_formatted: Optional[str] = None
    plaintiff: Optional[str] = None
    defendant: Optional[str] = None
    class_definition: Optional[str] = None
    estimated_class_size: Optional[int] = None

    # Settlement info
    settlement_date: Optional[str] = None
    settlement_amount: Optional[float] = None
    settlement_amount_formatted: Optional[str] = None
    settlement_url: Optional[str] = None
    settlement_pdf_url: Optional[str] = None
    attorney_fees: Optional[float] = None
    attorney_fees_formatted: Optional[str] = None
    actual_class_size: Optional[int] = None
    per_claimant_amount: Optional[float] = None
    claims_deadline: Optional[str] = None

    # Metadata
    source: str = ""
    raw_data: Optional[Dict] = None


# Patterns for extracting data
CASE_NUMBER_PATTERNS = [
    r'Case\s*(?:No\.?|Number|#)\s*:?\s*([A-Za-z0-9:-]+(?:\s*-\s*[A-Za-z0-9]+)*)',
    r'(\d{1,2}:\d{2}-cv-\d{4,6}(?:-[A-Z]+)?)',  # Federal: 1:23-cv-12345-ABC
    r'(\d{1,2}:\d{2}-md-\d{4,6}(?:-[A-Z]+)?)',  # MDL
    r'No\.\s*(\d{2,4}-[A-Z]{2,3}-\d{4,6})',
    r'Index\s*(?:No\.?|#)\s*:?\s*(\d+/\d{4})',  # NY state
]

COURT_PATTERNS = [
    r'United States District Court[,\s]+(?:for\s+)?(?:the\s+)?([^,\n]+(?:District|Division)[^,\n]*)',
    r'(?:U\.?S\.?\s+)?District\s+Court[,\s]+([^,\n]+)',
    r'(?:in\s+the\s+)?([^,\n]+(?:Superior|Circuit|District)\s+Court[^,\n]*)',
    r'(?:Northern|Southern|Eastern|Western|Central)\s+District\s+of\s+(\w+)',
]

AMOUNT_PATTERNS = [
    r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B)\b',
    r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M)\b',
    r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:thousand|K)\b',
    r'\$\s*([\d,]+(?:\.\d+)?)',
]

DATE_PATTERNS = [
    r'(?:filed|commenced|initiated)[:\s]+([A-Z][a-z]+ \d{1,2},? \d{4})',
    r'(?:filed|commenced|initiated)[:\s]+(\d{1,2}/\d{1,2}/\d{2,4})',
    r'(?:settlement|resolved|approved)[:\s]+([A-Z][a-z]+ \d{1,2},? \d{4})',
    r'(\d{1,2}/\d{1,2}/\d{2,4})',
    r'([A-Z][a-z]+ \d{1,2},? \d{4})',
]

DOCUMENT_LINK_PATTERNS = [
    r'complaint',
    r'initial\s*filing',
    r'class\s*action\s*complaint',
    r'amended\s*complaint',
    r'consolidated\s*complaint',
    r'petition',
]


def extract_amount(text: str) -> Tuple[Optional[float], Optional[str]]:
    """Extract settlement amount and return (numeric, formatted)."""
    if not text:
        return None, None

    for pattern in AMOUNT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount_str = match.group(0)
            num_str = match.group(1).replace(',', '')

            try:
                num = float(num_str)
                # Adjust for multiplier
                if 'billion' in text.lower() or amount_str.endswith('B'):
                    num *= 1_000_000_000
                elif 'million' in text.lower() or amount_str.endswith('M'):
                    num *= 1_000_000
                elif 'thousand' in text.lower() or amount_str.endswith('K'):
                    num *= 1_000

                return num, amount_str
            except ValueError:
                continue

    return None, None


def extract_case_number(text: str) -> Optional[str]:
    """Extract case number from text."""
    for pattern in CASE_NUMBER_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def extract_court(text: str) -> Optional[str]:
    """Extract court name from text."""
    for pattern in COURT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def extract_date(text: str) -> Optional[str]:
    """Extract and normalize date from text."""
    for pattern in DATE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            # Try to normalize to YYYY-MM-DD
            try:
                for fmt in ['%B %d, %Y', '%B %d %Y', '%m/%d/%Y', '%m/%d/%y']:
                    try:
                        dt = datetime.strptime(date_str, fmt)
                        return dt.strftime('%Y-%m-%d')
                    except ValueError:
                        continue
                return date_str
            except:
                return date_str
    return None


def extract_parties(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract plaintiff and defendant from case title."""
    # Common patterns: "Smith v. Company", "In re: Company Securities"
    vs_match = re.search(r'([^,\n]+?)\s+v\.?\s+([^,\n]+)', text, re.IGNORECASE)
    if vs_match:
        return vs_match.group(1).strip(), vs_match.group(2).strip()

    in_re_match = re.search(r'In\s+re:?\s+([^,\n]+)', text, re.IGNORECASE)
    if in_re_match:
        return None, in_re_match.group(1).strip()

    return None, None


def find_complaint_link(links: List[Dict], base_url: str = "") -> Optional[str]:
    """Find complaint document link from list of page links."""
    for link in links:
        href = link.get('href', '').lower()
        text = link.get('text', '').lower()

        for pattern in DOCUMENT_LINK_PATTERNS:
            if re.search(pattern, text) or re.search(pattern, href):
                url = link.get('href', '')
                if url and not url.startswith('http'):
                    url = base_url.rstrip('/') + '/' + url.lstrip('/')
                return url

    # Also check for PDF links that might be complaints
    for link in links:
        href = link.get('href', '').lower()
        if '.pdf' in href and 'complaint' in href:
            url = link.get('href', '')
            if url and not url.startswith('http'):
                url = base_url.rstrip('/') + '/' + url.lstrip('/')
            return url

    return None


class RequestsScraper:
    """Fast requests-based scraper (no browser needed)."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml',
        })
        self.outcomes: List[CaseOutcome] = []

    def _get_page(self, url: str) -> Optional[BeautifulSoup]:
        """Fetch and parse a page."""
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            print(f"      Error fetching {url}: {e}")
            return None

    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> List[Dict]:
        """Extract all links from page."""
        links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if not href.startswith('http'):
                href = base_url.rstrip('/') + '/' + href.lstrip('/')
            links.append({'href': href, 'text': a.get_text(strip=True)})
        return links

    def _parse_outcome_from_soup(self, soup: BeautifulSoup, url: str, source: str) -> CaseOutcome:
        """Parse BeautifulSoup into CaseOutcome."""
        text = soup.get_text(' ', strip=True)
        links = self._extract_links(soup, '/'.join(url.split('/')[:3]))

        # Extract amounts
        settlement_amount, settlement_formatted = extract_amount(text)

        # Extract case number
        case_number = extract_case_number(text)

        # Extract court
        court = extract_court(text)

        # Extract parties
        plaintiff, defendant = extract_parties(text[:1000])

        # Find complaint link
        complaint_url = find_complaint_link(links, '/'.join(url.split('/')[:3]))

        # Find settlement PDF
        settlement_pdf_url = None
        for link in links:
            href = link.get('href', '').lower()
            link_text = link.get('text', '').lower()
            if '.pdf' in href and ('settlement' in link_text or 'agreement' in link_text):
                settlement_pdf_url = link.get('href')
                break

        # Extract dates
        complaint_date = None
        settlement_date = None
        filed_match = re.search(r'(?:filed|commenced)[:\s]+([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})', text, re.IGNORECASE)
        if filed_match:
            complaint_date = extract_date(filed_match.group(1))

        # Extract deadline
        deadline_match = re.search(r'(?:deadline|claim\s+by)[:\s]+([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})', text, re.IGNORECASE)
        claims_deadline = extract_date(deadline_match.group(1)) if deadline_match else None

        # Title from page
        title_tag = soup.find('h1') or soup.find('title')
        title = title_tag.get_text(strip=True) if title_tag else None

        return CaseOutcome(
            case_number=case_number,
            case_title=title,
            court=court,
            jurisdiction='federal' if court and 'district' in court.lower() else 'state',
            complaint_date=complaint_date,
            complaint_url=complaint_url,
            plaintiff=plaintiff,
            defendant=defendant,
            settlement_amount=settlement_amount,
            settlement_amount_formatted=settlement_formatted,
            settlement_url=url,
            settlement_pdf_url=settlement_pdf_url,
            claims_deadline=claims_deadline,
            source=source,
        )

    def scrape_topclassactions(self, limit: int = 20) -> List[CaseOutcome]:
        """Scrape TopClassActions using requests."""
        outcomes = []
        base_url = "https://topclassactions.com"

        print(f"  Scraping TopClassActions (requests)...")

        # Get the main settlements page
        list_url = f"{base_url}/lawsuit-settlements/open-lawsuit-settlements/"
        soup = self._get_page(list_url)
        if not soup:
            return outcomes

        # Find individual settlement article links (not category pages)
        settlement_urls = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            # Skip category pages and list pages
            if '/category/' in href or 'open-lawsuit-settlements' in href:
                continue
            # Look for actual settlement article patterns
            if '/lawsuit-settlements/' in href and '-settlement' in href.lower():
                if not href.startswith('http'):
                    href = base_url + href
                if href not in settlement_urls:
                    settlement_urls.append(href)

        # Also try to find articles in the main content area
        articles = soup.find_all('article') or soup.find_all('div', class_=re.compile(r'post|entry|article'))
        for article in articles:
            for a in article.find_all('a', href=True):
                href = a['href']
                if '/category/' not in href and 'open-lawsuit-settlements' not in href:
                    if '/lawsuit-settlements/' in href or '-settlement' in href.lower():
                        if not href.startswith('http'):
                            href = base_url + href
                        if href not in settlement_urls:
                            settlement_urls.append(href)

        settlement_urls = settlement_urls[:limit]
        print(f"    Found {len(settlement_urls)} settlement pages")

        for i, url in enumerate(settlement_urls):
            print(f"    [{i+1}/{len(settlement_urls)}] {url[:60]}...")
            soup = self._get_page(url)
            if soup:
                outcome = self._parse_outcome_from_soup(soup, url, "TopClassActions")
                if outcome.settlement_amount or outcome.case_number or outcome.case_title:
                    outcomes.append(outcome)

        print(f"    Extracted {len(outcomes)} case outcomes")
        return outcomes

    def scrape_classaction_org(self, limit: int = 20) -> List[CaseOutcome]:
        """Scrape ClassAction.org using requests."""
        outcomes = []
        base_url = "https://www.classaction.org"

        print(f"  Scraping ClassAction.org (requests)...")

        # Try main settlements landing page
        list_url = f"{base_url}/lawsuits"
        soup = self._get_page(list_url)
        if not soup:
            # Try alternative URL
            list_url = f"{base_url}/settlements"
            soup = self._get_page(list_url)
        if not soup:
            return outcomes

        # Find lawsuit/settlement article links
        settlement_urls = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True).lower()
            # Look for specific settlement or lawsuit articles
            if ('/lawsuits/' in href or '/settlements/' in href) and '/category/' not in href:
                if 'settlement' in text or 'million' in text or 'lawsuit' in text or 'class action' in text:
                    if not href.startswith('http'):
                        href = base_url + href
                    if href not in settlement_urls:
                        settlement_urls.append(href)

        # Also look in article containers
        for article in soup.find_all(['article', 'div'], class_=re.compile(r'card|post|item|entry')):
            for a in article.find_all('a', href=True):
                href = a['href']
                if '/lawsuits/' in href or '-settlement' in href.lower():
                    if not href.startswith('http'):
                        href = base_url + href
                    if href not in settlement_urls:
                        settlement_urls.append(href)

        settlement_urls = settlement_urls[:limit]
        print(f"    Found {len(settlement_urls)} settlement pages")

        for i, url in enumerate(settlement_urls):
            print(f"    [{i+1}/{len(settlement_urls)}] {url[:60]}...")
            soup = self._get_page(url)
            if soup:
                outcome = self._parse_outcome_from_soup(soup, url, "ClassAction.org")
                if outcome.settlement_amount or outcome.case_number or outcome.case_title:
                    outcomes.append(outcome)

        print(f"    Extracted {len(outcomes)} case outcomes")
        return outcomes

    def scrape_all(self, limit_per_site: int = 15) -> List[CaseOutcome]:
        """Scrape all sites."""
        all_outcomes = []

        try:
            outcomes = self.scrape_topclassactions(limit=limit_per_site)
            all_outcomes.extend(outcomes)
        except Exception as e:
            print(f"  TopClassActions failed: {e}")

        try:
            outcomes = self.scrape_classaction_org(limit=limit_per_site)
            all_outcomes.extend(outcomes)
        except Exception as e:
            print(f"  ClassAction.org failed: {e}")

        # Dedupe
        seen = set()
        unique = []
        for o in all_outcomes:
            key = (o.case_number or o.case_title or o.settlement_url or '').lower()[:50]
            if key and key not in seen:
                seen.add(key)
                unique.append(o)

        self.outcomes = unique
        return unique

    def to_json(self) -> str:
        """Export to JSON."""
        return json.dumps([asdict(o) for o in self.outcomes], indent=2, default=str)


class CaseOutcomeScraper:
    """Scrapes settlement sites for complaint-to-outcome data."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.outcomes: List[CaseOutcome] = []
        self.browser = None

    async def _init_browser(self):
        """Initialize Playwright browser."""
        if not HAS_PLAYWRIGHT:
            return
        p = await async_playwright().start()
        self.browser = await p.chromium.launch(headless=True)

    async def _close_browser(self):
        """Close browser."""
        if self.browser:
            await self.browser.close()

    async def _get_page_data(self, page: Page, url: str) -> Dict:
        """Extract data from a settlement page."""
        await page.goto(url, timeout=self.timeout * 1000)
        await page.wait_for_load_state('networkidle', timeout=15000)

        # Get all text content
        text_content = await page.inner_text('body')

        # Get all links
        links = []
        link_elements = await page.query_selector_all('a[href]')
        for el in link_elements:
            try:
                href = await el.get_attribute('href')
                text = await el.inner_text()
                links.append({'href': href, 'text': text})
            except:
                continue

        return {
            'url': url,
            'text': text_content,
            'links': links,
        }

    def _parse_outcome(self, data: Dict, source: str) -> CaseOutcome:
        """Parse page data into CaseOutcome."""
        text = data.get('text', '')
        links = data.get('links', [])
        url = data.get('url', '')

        # Extract case number and court
        case_number = extract_case_number(text)
        court = extract_court(text)

        # Extract amounts
        settlement_amount, settlement_formatted = extract_amount(text)

        # Look for initial demand (often mentioned in complaints or summaries)
        demand_match = re.search(r'(?:seeking|demanded|claims?|damages?\s+of)\s+(\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion))?)', text, re.IGNORECASE)
        initial_demand = None
        initial_demand_formatted = None
        if demand_match:
            initial_demand, initial_demand_formatted = extract_amount(demand_match.group(1))

        # Extract parties from title or text
        plaintiff, defendant = extract_parties(text[:500])

        # Find complaint link
        base_url = '/'.join(url.split('/')[:3])
        complaint_url = find_complaint_link(links, base_url)

        # Find settlement document link
        settlement_pdf_url = None
        for link in links:
            href = link.get('href', '').lower()
            link_text = link.get('text', '').lower()
            if '.pdf' in href and ('settlement' in link_text or 'agreement' in link_text or 'settlement' in href):
                settlement_pdf_url = link.get('href', '')
                if settlement_pdf_url and not settlement_pdf_url.startswith('http'):
                    settlement_pdf_url = base_url + '/' + settlement_pdf_url.lstrip('/')
                break

        # Extract dates
        complaint_date = None
        settlement_date = None
        filed_match = re.search(r'(?:filed|commenced)[:\s]+([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})', text, re.IGNORECASE)
        if filed_match:
            complaint_date = extract_date(filed_match.group(1))

        settled_match = re.search(r'(?:settlement|approved|resolved)[:\s]+([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})', text, re.IGNORECASE)
        if settled_match:
            settlement_date = extract_date(settled_match.group(1))

        # Extract deadline
        deadline_match = re.search(r'(?:deadline|claim\s+by|expires?)[:\s]+([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})', text, re.IGNORECASE)
        claims_deadline = None
        if deadline_match:
            claims_deadline = extract_date(deadline_match.group(1))

        # Determine jurisdiction
        jurisdiction = 'federal' if court and ('district' in court.lower() or 'federal' in court.lower()) else 'state'

        # Extract class info
        class_match = re.search(r'(?:class\s+(?:of|includes?|consists?\s+of))[:\s]+([^.]+)', text, re.IGNORECASE)
        class_definition = class_match.group(1).strip()[:500] if class_match else None

        # Extract class size
        size_match = re.search(r'(\d{1,3}(?:,\d{3})*)\s+(?:class\s+members?|claimants?|affected)', text, re.IGNORECASE)
        estimated_class_size = int(size_match.group(1).replace(',', '')) if size_match else None

        # Extract attorney fees
        fees_match = re.search(r"attorney(?:'?s?)?\s+fees?[:\s]+(\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion))?)", text, re.IGNORECASE)
        attorney_fees = None
        attorney_fees_formatted = None
        if fees_match:
            attorney_fees, attorney_fees_formatted = extract_amount(fees_match.group(1))

        return CaseOutcome(
            case_number=case_number,
            case_title=defendant if defendant else None,
            court=court,
            jurisdiction=jurisdiction,
            nature_of_suit=None,  # Would need more parsing
            complaint_date=complaint_date,
            complaint_url=complaint_url,
            initial_demand=initial_demand,
            initial_demand_formatted=initial_demand_formatted,
            plaintiff=plaintiff,
            defendant=defendant,
            class_definition=class_definition,
            estimated_class_size=estimated_class_size,
            settlement_date=settlement_date,
            settlement_amount=settlement_amount,
            settlement_amount_formatted=settlement_formatted,
            settlement_url=url,
            settlement_pdf_url=settlement_pdf_url,
            attorney_fees=attorney_fees,
            attorney_fees_formatted=attorney_fees_formatted,
            claims_deadline=claims_deadline,
            source=source,
        )

    async def scrape_topclassactions(self, page: Page, limit: int = 20) -> List[CaseOutcome]:
        """Scrape TopClassActions settlement pages."""
        outcomes = []
        base_url = "https://topclassactions.com"
        list_url = f"{base_url}/lawsuit-settlements/open-lawsuit-settlements/"

        try:
            print(f"  Scraping TopClassActions...")
            await page.goto(list_url, timeout=60000, wait_until='domcontentloaded')
            await asyncio.sleep(3)  # Let page render

            # Get settlement links - try multiple selectors
            settlement_urls = []
            selectors = ['article a[href*="settlement"]', 'h2 a', '.entry-title a', 'a[href*="/lawsuit-settlements/"]']

            for selector in selectors:
                items = await page.query_selector_all(selector)
                for item in items:
                    try:
                        href = await item.get_attribute('href')
                        if href and href not in settlement_urls and '/open-lawsuit-settlements' not in href:
                            settlement_urls.append(href)
                    except:
                        continue
                if settlement_urls:
                    break

            # Dedupe
            settlement_urls = list(dict.fromkeys(settlement_urls))[:limit]
            print(f"    Found {len(settlement_urls)} settlement pages to scrape")

            # Visit each settlement page
            for i, url in enumerate(settlement_urls[:limit]):
                try:
                    print(f"    [{i+1}/{len(settlement_urls)}] {url[:60]}...")
                    data = await self._get_page_data(page, url)
                    outcome = self._parse_outcome(data, "TopClassActions")
                    if outcome.settlement_amount or outcome.case_number:
                        outcomes.append(outcome)
                    await asyncio.sleep(1)  # Be polite
                except Exception as e:
                    print(f"      Error: {e}")
                    continue

        except Exception as e:
            print(f"    Error scraping TopClassActions: {e}")

        print(f"    Extracted {len(outcomes)} case outcomes")
        return outcomes

    async def scrape_classaction_org(self, page: Page, limit: int = 20) -> List[CaseOutcome]:
        """Scrape ClassAction.org settlement pages."""
        outcomes = []
        base_url = "https://www.classaction.org"
        # Try the news/settlements page which has more detailed listings
        list_url = f"{base_url}/news/category/settlements"

        try:
            print(f"  Scraping ClassAction.org...")
            await page.goto(list_url, timeout=30000)
            await page.wait_for_load_state('networkidle', timeout=15000)

            # Get settlement article links - look for actual article URLs
            items = await page.query_selector_all('a[href*="/news/"]')
            settlement_urls = []
            seen = set()

            for item in items:
                try:
                    href = await item.get_attribute('href')
                    text = await item.inner_text()
                    # Filter for settlement-related articles
                    if href and '/news/' in href and 'settlement' in text.lower():
                        if href not in seen and '/category/' not in href:
                            if not href.startswith('http'):
                                href = base_url + href
                            seen.add(href)
                            settlement_urls.append(href)
                except:
                    continue

            print(f"    Found {len(settlement_urls)} settlement pages to scrape")

            # Visit each settlement page
            for i, url in enumerate(settlement_urls[:limit]):
                try:
                    print(f"    [{i+1}/{min(limit, len(settlement_urls))}] {url[:60]}...")
                    data = await self._get_page_data(page, url)
                    outcome = self._parse_outcome(data, "ClassAction.org")
                    if outcome.settlement_amount or outcome.case_number:
                        outcomes.append(outcome)
                    await asyncio.sleep(1)
                except Exception as e:
                    print(f"      Error: {e}")
                    continue

        except Exception as e:
            print(f"    Error scraping ClassAction.org: {e}")

        print(f"    Extracted {len(outcomes)} case outcomes")
        return outcomes

    async def scrape_all(self, limit_per_site: int = 15) -> List[CaseOutcome]:
        """Scrape all settlement sites."""
        if not HAS_PLAYWRIGHT:
            print("Playwright not available. Install with: pip install playwright && playwright install")
            return []

        await self._init_browser()
        page = await self.browser.new_page()
        await page.set_viewport_size({"width": 1280, "height": 800})

        all_outcomes = []

        # Run scrapers
        try:
            outcomes = await self.scrape_topclassactions(page, limit=limit_per_site)
            all_outcomes.extend(outcomes)
        except Exception as e:
            print(f"  TopClassActions scraper failed: {e}")

        await asyncio.sleep(2)

        try:
            outcomes = await self.scrape_classaction_org(page, limit=limit_per_site)
            all_outcomes.extend(outcomes)
        except Exception as e:
            print(f"  ClassAction.org scraper failed: {e}")

        await page.close()
        await self._close_browser()

        # Dedupe by case number or title
        seen = set()
        unique = []
        for o in all_outcomes:
            key = (o.case_number or o.case_title or o.settlement_url or '').lower()[:50]
            if key and key not in seen:
                seen.add(key)
                unique.append(o)

        self.outcomes = unique
        return unique

    def to_json(self) -> str:
        """Export outcomes to JSON."""
        return json.dumps([asdict(o) for o in self.outcomes], indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(description='Case Outcomes Scraper - Complaint to Settlement Mapping')
    parser.add_argument('--limit', '-l', type=int, default=10, help='Max settlements per site')
    parser.add_argument('--output', '-o', help='Output JSON file')
    parser.add_argument('--import-db', action='store_true', help='Import results to database')
    parser.add_argument('--use-browser', action='store_true', help='Use Playwright browser (slower but more reliable)')

    args = parser.parse_args()

    print("=" * 70)
    print("CASE OUTCOMES SCRAPER")
    print("Mapping Complaints to Settlement Values")
    print("=" * 70)

    if args.use_browser and HAS_PLAYWRIGHT:
        scraper = CaseOutcomeScraper()
        outcomes = asyncio.run(scraper.scrape_all(limit_per_site=args.limit))
    else:
        # Use faster requests-based scraper
        scraper = RequestsScraper()
        outcomes = scraper.scrape_all(limit_per_site=args.limit)

    print(f"\n{'=' * 70}")
    print(f"RESULTS: {len(outcomes)} case outcomes extracted")
    print("=" * 70)

    # Summary
    with_amounts = [o for o in outcomes if o.settlement_amount]
    with_complaints = [o for o in outcomes if o.complaint_url]
    with_case_numbers = [o for o in outcomes if o.case_number]

    print(f"\n  With settlement amounts: {len(with_amounts)}")
    print(f"  With complaint links: {len(with_complaints)}")
    print(f"  With case numbers: {len(with_case_numbers)}")

    # Show outcomes with both complaint and settlement data
    complete = [o for o in outcomes if o.complaint_url and o.settlement_amount]
    if complete:
        print(f"\n{'=' * 70}")
        print(f"COMPLETE OUTCOMES (Complaint + Settlement): {len(complete)}")
        print("=" * 70)
        for o in complete[:10]:
            print(f"\n  {o.case_title or o.defendant or 'Unknown'}:")
            if o.case_number:
                print(f"    Case: {o.case_number}")
            if o.court:
                print(f"    Court: {o.court}")
            if o.settlement_amount_formatted:
                print(f"    Settlement: {o.settlement_amount_formatted}")
            if o.initial_demand_formatted:
                print(f"    Initial Demand: {o.initial_demand_formatted}")
            if o.complaint_url:
                print(f"    Complaint: {o.complaint_url[:70]}...")

    # Show high-value settlements
    high_value = sorted([o for o in with_amounts if o.settlement_amount and o.settlement_amount >= 1_000_000],
                        key=lambda x: x.settlement_amount or 0, reverse=True)
    if high_value:
        print(f"\n{'=' * 70}")
        print(f"HIGH-VALUE SETTLEMENTS (>= $1M): {len(high_value)}")
        print("=" * 70)
        for o in high_value[:10]:
            print(f"\n  {o.case_title or o.defendant or 'Unknown'}:")
            print(f"    Amount: {o.settlement_amount_formatted}")
            if o.case_number:
                print(f"    Case: {o.case_number}")
            print(f"    Source: {o.source}")

    # Save results
    output_file = args.output or '/tmp/case_outcomes.json'
    with open(output_file, 'w') as f:
        f.write(scraper.to_json())
    print(f"\n\nSaved to {output_file}")

    # Import to database if requested
    if args.import_db:
        print("\nImporting to database...")
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from db.database import get_db

        db = get_db()
        count = db.add_case_outcomes([asdict(o) for o in outcomes])
        print(f"Imported {count} case outcomes to database")

    return outcomes


if __name__ == "__main__":
    main()
