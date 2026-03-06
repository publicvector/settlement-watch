"""
RSS/Atom feed consumer for settlement-related government press releases.

Parses feeds from DOJ, FTC, SEC, CFPB, and EPA. Filters items using keyword
matching before including them. Uses xml.etree.ElementTree (no new deps).
"""
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feed definitions
# ---------------------------------------------------------------------------

SETTLEMENT_FEEDS = [
    {
        "name": "DOJ Press Releases",
        "url": "https://www.justice.gov/feeds/justice-news.xml",
        "source": "DOJ Feed",
        "default_category": "Government",
    },
    {
        "name": "CFPB Newsroom",
        "url": "https://www.consumerfinance.gov/about-us/newsroom/feed/",
        "source": "CFPB Feed",
        "default_category": "Consumer Protection",
    },
]

# Keywords that indicate settlement/enforcement relevance
_SETTLEMENT_KEYWORDS = [
    "settlement", "settle", "consent decree", "consent order",
    "penalty", "fine", "million", "billion",
    "enforcement", "judgment", "injunction",
    "restitution", "disgorgement", "class action",
    "pay", "paid", "forfeiture", "relief",
]


def _is_settlement_related(title: str, description: str = "") -> bool:
    """Check whether an RSS item is likely settlement-related."""
    text = f"{title} {description}".lower()
    return any(kw in text for kw in _SETTLEMENT_KEYWORDS)


def _fetch_xml(url: str, timeout: int = 20) -> Optional[str]:
    """Fetch raw XML from a URL."""
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SettlementBot/1.0)",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.debug("Failed to fetch feed %s: %s", url, e)
        return None


def _parse_feed(xml_text: str, source: str, default_category: str) -> List[Dict]:
    """Parse RSS 2.0 or Atom XML into settlement dicts."""
    from .settlement_scraper import extract_amount, categorize_settlement

    results: List[Dict] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.debug("XML parse error for %s: %s", source, e)
        return results

    # RSS 2.0: <rss><channel><item>…</item></channel></rss>
    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            description = (item.findtext("description") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            guid_el = item.findtext("guid") or ""

            if not _is_settlement_related(title, description):
                continue

            combined = f"{title} {description}"
            amount, formatted = extract_amount(combined)
            category = categorize_settlement(title, description, source)
            if category == "Other":
                category = default_category

            results.append({
                "title": title[:200],
                "amount": amount,
                "amount_formatted": formatted,
                "url": link,
                "description": description[:300],
                "category": category,
                "source": source,
                "pub_date": pub_date or datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
                "guid": guid_el.strip()[:120] if guid_el.strip() else f"{source}-{link[-60:]}" if link else f"{source}-{title[:40]}",
            })
        return results

    # Atom: <feed><entry>…</entry></feed>
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
        content = (entry.findtext("atom:content", default="", namespaces=ns) or "").strip()
        description = summary or content
        link_el = entry.find("atom:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        updated = (entry.findtext("atom:updated", default="", namespaces=ns) or "").strip()
        id_text = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()

        if not _is_settlement_related(title, description):
            continue

        combined = f"{title} {description}"
        amount, formatted = extract_amount(combined)
        category = categorize_settlement(title, description, source)
        if category == "Other":
            category = default_category

        results.append({
            "title": title[:200],
            "amount": amount,
            "amount_formatted": formatted,
            "url": link,
            "description": description[:300],
            "category": category,
            "source": source,
            "pub_date": updated or datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
            "guid": id_text[:120] if id_text else f"{source}-{link[-60:]}" if link else f"{source}-{title[:40]}",
        })

    # Also try without namespace prefix (some Atom feeds omit it)
    if not results:
        for entry in root.findall("entry"):
            title = (entry.findtext("title") or "").strip()
            summary = (entry.findtext("summary") or "").strip()
            content = (entry.findtext("content") or "").strip()
            description = summary or content
            link_el = entry.find("link")
            link = link_el.get("href", "") if link_el is not None else ""
            updated = (entry.findtext("updated") or "").strip()
            id_text = (entry.findtext("id") or "").strip()

            if not _is_settlement_related(title, description):
                continue

            combined = f"{title} {description}"
            amount, formatted = extract_amount(combined)
            category = categorize_settlement(title, description, source)
            if category == "Other":
                category = default_category

            results.append({
                "title": title[:200],
                "amount": amount,
                "amount_formatted": formatted,
                "url": link,
                "description": description[:300],
                "category": category,
                "source": source,
                "pub_date": updated or datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'),
                "guid": id_text[:120] if id_text else f"{source}-{link[-60:]}" if link else f"{source}-{title[:40]}",
            })

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_feeds() -> List[Dict]:
    """Fetch and parse all settlement RSS feeds. Returns list of settlement dicts."""
    all_items: List[Dict] = []

    for feed in SETTLEMENT_FEEDS:
        try:
            xml_text = _fetch_xml(feed["url"])
            if not xml_text:
                continue
            items = _parse_feed(xml_text, feed["source"], feed["default_category"])
            all_items.extend(items)
            logger.info("Feed %s yielded %d settlement items", feed["name"], len(items))
        except Exception as e:
            logger.warning("Feed %s failed: %s", feed["name"], e)

    return all_items


def run_feeds_and_store() -> Dict:
    """Fetch feeds and upsert results into the app database."""
    from ..models.db import upsert_settlements_batch

    items = run_feeds()
    upsert_settlements_batch(items)
    return {"stored": len(items), "source": "rss_feeds"}
