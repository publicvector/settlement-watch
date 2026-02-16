
import hashlib, uuid, datetime, re, json, os, logging
from typing import List, Dict, Any, Optional
from xml.etree import ElementTree as ET

import requests
from ..models.db import insert_rss_items, upsert_rss_source, update_rss_source_poll_time
from ..pacer_auth import pacer_client
from bs4 import BeautifulSoup
from .doc_discovery import get_discovery_service
from .doc_discovery_config import get_discovery_config

logger = logging.getLogger(__name__)

def fetch_rss(url: str) -> str:
    if url.startswith("file://"):
        path = url.replace("file://","")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text

def parse_case_number(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b\d+:\d{2}-(cv|cr|bk|ap|mc|md)-\d{3,6}\b", text, flags=re.IGNORECASE)
    return m.group(0) if m else None

def parse_case_number_parts(case_number: Optional[str]) -> Optional[dict]:
    if not case_number:
        return None
    # Supports optional defendant index suffix like -3 in criminal cases
    m = re.match(r"^(?:(\d+):)?(\d{2})-(cv|cr|bk|ap|mc|md)-(\d{3,6})(?:-(\d+))?$", case_number, flags=re.IGNORECASE)
    if not m:
        return None
    office, yy, ctype, seq, d_idx = m.groups()
    year = int("20" + yy) if len(yy) == 2 else int(yy)
    parts = {"office": office, "year": year, "type": ctype.lower(), "sequence": int(seq)}
    if office and office.isdigit():
        parts["division_number"] = int(office)
    if d_idx:
        parts["defendant_index"] = int(d_idx)
    return parts

def extract_case_type(case_number: Optional[str]) -> Optional[str]:
    """Extract case type from case number (cv, cr, bk, ap, mc, md)"""
    if not case_number:
        return None
    m = re.search(r"-(cv|cr|bk|ap|mc|md)-", case_number, flags=re.IGNORECASE)
    return m.group(1).lower() if m else None

def extract_judge_name(text: str) -> Optional[str]:
    """Extract judge name from title or summary"""
    if not text:
        return None

    # Common patterns for judge mentions
    patterns = [
        r"(?:Judge|J\.|Magistrate Judge|Chief Judge|District Judge)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        r"(?:Hon\.|Honorable)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        r"Before:\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        r"Assigned to:\s*(?:Judge|Magistrate Judge)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip()
            # Filter out common false positives
            if name and len(name) > 3 and name not in ["Court", "States", "United", "Federal"]:
                return name

    return None

def extract_nature_of_suit(summary: str) -> Optional[str]:
    """Extract nature of suit from summary (employment, contract, civil rights, etc)"""
    if not summary:
        return None

    # Common case categories
    categories = {
        "Civil Rights": r"civil rights|discrimination|§\s*1983|civil liberties",
        "Employment": r"employment|wrongful termination|EEOC|workplace|labor",
        "Contract": r"contract|breach|agreement|warranty",
        "Intellectual Property": r"patent|trademark|copyright|IP|infringement",
        "Securities": r"securities|SEC|stock|insider trading",
        "Bankruptcy": r"bankruptcy|debtor|creditor|chapter \d+",
        "Habeas Corpus": r"habeas corpus|writ|detention",
        "Immigration": r"immigration|deportation|asylum|visa",
        "Personal Injury": r"personal injury|negligence|tort|malpractice",
        "Real Property": r"real property|foreclosure|eviction|landlord",
        "Antitrust": r"antitrust|monopoly|price fixing",
        "Consumer": r"consumer|FDCPA|TCPA|fair credit",
    }

    for category, pattern in categories.items():
        if re.search(pattern, summary, re.IGNORECASE):
            return category

    return None

def parse_nos_code(text: str) -> Optional[dict]:
    if not text:
        return None
    m = re.search(r"Nature of Suit\s*:?\s*(\d{3})", text, re.IGNORECASE) or re.search(r"\bNOS\s*(\d{3})\b", text)
    if not m:
        return None
    code = m.group(1)
    labels = {
        "190": "Contract Other",
        "195": "Contract Product Liability",
        "315": "Airplane Product Liability",
        "365": "Personal Injury Product Liability",
        "440": "Civil Rights Other",
        "442": "Employment Civil Rights",
        "443": "Housing/Accommodations",
        "530": "Habeas Corpus",
        "820": "Copyright",
        "830": "Patent",
        "840": "Trademark",
        "850": "Securities/Commodities/Exchange",
    }
    return {"code": code, "label": labels.get(code)}

def parse_cause_of_action(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"Cause(?: of Action)?\s*:?\s*([0-9A-Za-z:./\- ]{3,20})", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"\b(\d{2}:\d{3,4})\b", text)
    return m2.group(1) if m2 else None

def parse_parties(title: str) -> Optional[dict]:
    if not title:
        return None
    # Strip leading case number (with optional defendant index) and trailing section after names
    t = re.sub(r"^\s*\d+:\d{2}-(cv|cr|bk|ap|mc|md)-\d{3,6}(?:-\d+)?\s+", "", title, flags=re.IGNORECASE)
    # Split on common vs patterns
    m = re.split(r"\s+(v\.?|vs\.?|versus)\s+", t, flags=re.IGNORECASE)
    if len(m) >= 3:
        left = m[0].split(" - ")[0].strip()
        right = " ".join(m[2:]).split(" - ")[0].strip()
        return {"plaintiffs": [left], "defendants": [right]}
    return None

def parse_doc_number(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\bDoc(?:ument)?\s*#\s*(\d+)\b", text, re.IGNORECASE) or re.search(r"\bdocument number\s*(\d+)\b", text, re.IGNORECASE)
    return int(m.group(1)) if m else None

def classify_event_type(text: str) -> Optional[str]:
    if not text:
        return None
    t = text.lower()
    if "complaint" in t or "case opened" in t or "indictment" in t:
        return "case_opening"
    if "assigned to" in t or "case assigned" in t:
        return "assignment"
    if "motion" in t:
        return "motion"
    if "order" in t:
        return "order"
    if "judgment" in t:
        return "judgment"
    if "notice" in t or "summons" in t:
        return "notice"
    if "transfer" in t:
        return "transfer"
    return "docket_event"

def parse_entry_number_from_link(link: str) -> Optional[int]:
    if not link:
        return None
    m = re.search(r"[#&]?entry[-_=](\d+)", link, re.IGNORECASE)
    return int(m.group(1)) if m else None

def extract_doc1_url(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"https?://[^\s\"']+/doc1/[^\s\"'<>]+", text)
    return m.group(0) if m else None

def should_fetch_outside() -> bool:
    return os.getenv("RSS_FETCH_OUTSIDE", "false").lower() == "true"

def is_pacer_login_response(resp_text: str, final_url: str) -> bool:
    url = (final_url or '').lower()
    if 'pacer.login.uscourts.gov' in url or 'login.jsf' in url:
        return True
    lt = (resp_text or '').lower()
    return ('pacer: login' in lt) or ('jakarta.faces' in lt and 'loginform' in lt)

def fetch_outside_excerpt(link: str) -> Optional[str]:
    if not link or not link.startswith('http'):
        return None
    # Only attempt known public outside pages
    if not re.search(r"outside\.pl|rss_outside\.pl", link, re.IGNORECASE):
        return None
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; CourtRSS/1.0)'
        }
        r = requests.get(link, headers=headers, timeout=10, allow_redirects=True)
        if is_pacer_login_response(r.text, r.url):
            return None
        soup = BeautifulSoup(r.text, 'html.parser')
        # Heuristic: grab first significant paragraph or table cell
        for sel in ['p', 'td', 'div']:
            el = soup.find(sel)
            if el and el.text and len(el.text.strip()) > 40:
                return el.text.strip()[:500]
    except Exception:
        return None
    return None

def parse_rss(xml_text: str, source_id: str, court_code: Optional[str]) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items = []

    channel = root.find("channel")
    if channel is not None:
        for it in channel.findall("item"):
            title = (it.findtext("title") or "").strip()
            summary = (it.findtext("description") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()
            cn = parse_case_number(title) or parse_case_number(summary) or None
            case_type = extract_case_type(cn)
            judge_name = extract_judge_name(title) or extract_judge_name(summary)
            nature_of_suit = extract_nature_of_suit(title + " " + summary)
            parts = parse_case_number_parts(cn)
            nos = parse_nos_code(summary)
            cause = parse_cause_of_action(summary)
            parties = parse_parties(title)
            doc_no = parse_doc_number(title + " " + summary)
            event_type = classify_event_type(title + " " + summary)
            entry_no = parse_entry_number_from_link(link)
            has_numbers = (doc_no is not None) or (entry_no is not None)
            is_new = bool((doc_no == 1) or (entry_no == 1) or (not has_numbers and event_type == "case_opening"))
            item_id = hashlib.sha256((title + link + pub).encode("utf-8")).hexdigest()
            meta = {
                "case_parts": parts,
                "nos": nos,
                "cause_of_action": cause,
                "parties": parties,
                "doc_number": doc_no,
                "event_type": event_type,
                "is_new_case": is_new,
                "docket_entry_number": entry_no,
                "doc1_url": extract_doc1_url(link or '') or extract_doc1_url(summary or ''),
            }
            items.append({
                "id": item_id,
                "source_id": source_id,
                "court_code": court_code,
                "case_number": cn,
                "case_type": case_type,
                "judge_name": judge_name,
                "nature_of_suit": nature_of_suit,
                "title": title,
                "summary": summary,
                "link": link,
                "published": pub,
                "created_at": datetime.datetime.utcnow().isoformat(),
                "metadata_json": json.dumps(meta, ensure_ascii=False)
            })
        return items

    # Atom fallback (rare for CM/ECF)
    ns = {"atom":"http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
        link_el = entry.find("atom:link", ns)
        link = link_el.get("href") if link_el is not None else ""
        pub = (entry.findtext("atom:updated", default="", namespaces=ns) or "").strip()
        cn = parse_case_number(title) or parse_case_number(summary) or None
        case_type = extract_case_type(cn)
        judge_name = extract_judge_name(title) or extract_judge_name(summary)
        nature_of_suit = extract_nature_of_suit(title + " " + summary)
        parts = parse_case_number_parts(cn)
        nos = parse_nos_code(summary)
        cause = parse_cause_of_action(summary)
        parties = parse_parties(title)
        doc_no = parse_doc_number(title + " " + summary)
        event_type = classify_event_type(title + " " + summary)
        entry_no = parse_entry_number_from_link(link)
        has_numbers = (doc_no is not None) or (entry_no is not None)
        is_new = bool((doc_no == 1) or (entry_no == 1) or (not has_numbers and event_type == "case_opening"))
        item_id = hashlib.sha256((title + link + pub).encode("utf-8")).hexdigest()
        meta = {
            "case_parts": parts,
            "nos": nos,
            "cause_of_action": cause,
            "parties": parties,
            "doc_number": doc_no,
            "event_type": event_type,
            "is_new_case": is_new,
            "docket_entry_number": entry_no,
            "doc1_url": extract_doc1_url(link or '') or extract_doc1_url(summary or ''),
        }
        items.append({
            "id": item_id,
            "source_id": source_id,
            "court_code": court_code,
            "case_number": cn,
            "case_type": case_type,
            "judge_name": judge_name,
            "nature_of_suit": nature_of_suit,
            "title": title,
            "summary": summary,
            "link": link,
            "published": pub,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "metadata_json": json.dumps(meta, ensure_ascii=False)
        })
    return items

def subscribe(court_code: str, url: str, label: str | None = None) -> Dict[str, Any]:
    sid = str(uuid.uuid5(uuid.NAMESPACE_URL, url))
    upsert_rss_source({"id": sid, "court_code": court_code, "url": url, "label": label, "last_polled": None})
    return {"id": sid, "court_code": court_code, "url": url, "label": label}

def poll(source: Dict[str, Any]) -> int:
    xml_text = fetch_rss(source["url"])
    items = parse_rss(xml_text, source["id"], source.get("court_code"))
    # Optional enrichment from public outside pages (no PACER login)
    if should_fetch_outside():
        for it in items:
            try:
                link = it.get('link')
                excerpt = fetch_outside_excerpt(link)
                if excerpt:
                    it['summary'] = excerpt
                    # Update metadata_json to reflect enrichment
                    meta = {}
                    try:
                        meta = json.loads(it.get('metadata_json') or '{}')
                    except Exception:
                        meta = {}
                    meta['outside_excerpt'] = True
                    it['metadata_json'] = json.dumps(meta, ensure_ascii=False)
            except Exception:
                pass
    # Document discovery: queue matching items for download (replaces legacy AUTOFETCH_DOCS)
    discovery_config = get_discovery_config()
    if discovery_config.enabled:
        try:
            discovery_service = get_discovery_service()
            eval_result = discovery_service.evaluate_batch(items)
            logger.info(
                f"Document discovery: {eval_result.get('queued', 0)} items queued, "
                f"{eval_result.get('pending_in_queue', 0)} total pending"
            )

            # Process queued items if there are any
            if eval_result.get('pending_in_queue', 0) > 0:
                process_result = discovery_service.process_batch()
                logger.info(
                    f"Document processing: {process_result.get('succeeded', 0)}/"
                    f"{process_result.get('processed', 0)} succeeded, "
                    f"cost ${process_result.get('total_cost', 0):.2f}"
                )

                # Update metadata for successfully fetched items
                for doc in process_result.get('documents', []):
                    if doc.get('success'):
                        for it in items:
                            if it.get('id') == doc.get('rss_item_id'):
                                try:
                                    meta = json.loads(it.get('metadata_json') or '{}')
                                    meta['doc_cached'] = True
                                    meta['doc_filename'] = doc.get('filename')
                                    it['metadata_json'] = json.dumps(meta, ensure_ascii=False)
                                except Exception:
                                    pass
                                break
        except Exception as e:
            logger.error(f"Document discovery error: {e}", exc_info=True)

    # Legacy AUTOFETCH_DOCS support (deprecated, use DOC_DISCOVERY_ENABLED instead)
    elif os.getenv('AUTOFETCH_DOCS', 'false').lower() == 'true' and pacer_client.is_configured():
        logger.warning("AUTOFETCH_DOCS is deprecated. Use DOC_DISCOVERY_ENABLED=true instead.")
        max_per_poll = int(os.getenv('AUTOFETCH_MAX_PER_POLL', '5'))
        only_doc1 = os.getenv('AUTOFETCH_ONLY_DOC1', 'true').lower() == 'true'
        allowlist = set([c.strip().lower() for c in os.getenv('AUTOFETCH_ALLOWED_COURTS', '').split(',') if c.strip()]) or None
        fetched = 0
        for it in items:
            if fetched >= max_per_poll:
                break
            try:
                # Decode metadata for decision
                meta = {}
                try:
                    meta = json.loads(it.get('metadata_json') or '{}')
                except Exception:
                    meta = {}
                doc_url = meta.get('doc1_url')
                if not doc_url:
                    continue
                # Court selection: prefer item's court_code, else derive from URL host
                court_code = (it.get('court_code') or '').lower()
                if not court_code:
                    mhost = re.search(r"ecf\.([a-z0-9]+)\.uscourts\.gov", doc_url, re.IGNORECASE)
                    court_code = (mhost.group(1).lower() if mhost else '')
                if allowlist is not None and court_code not in allowlist:
                    continue
                # Only fetch document #1 by default
                doc_no = meta.get('doc_number')
                entry_no = meta.get('docket_entry_number')
                if only_doc1 and not ((doc_no == 1) or (entry_no == 1)):
                    continue
                # Check spend limits before each fetch
                limits = pacer_client.check_spending_limits()
                if not limits.get('can_proceed'):
                    break
                result = pacer_client.fetch_document(court_code, doc_url)
                if result and result.get('path'):
                    meta['doc_cached'] = True
                    meta['doc_filename'] = result.get('filename')
                    it['metadata_json'] = json.dumps(meta, ensure_ascii=False)
                    fetched += 1
            except Exception:
                continue
    insert_rss_items(items)
    update_rss_source_poll_time(source["id"], datetime.datetime.utcnow().isoformat())
    return len(items)
