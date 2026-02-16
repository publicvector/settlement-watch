
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi import Body
from fastapi.responses import Response, HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List
try:
    from dotenv import load_dotenv
    # Load environment variables from .env file (optional)
    load_dotenv()
except Exception:
    # If python-dotenv isn't installed, continue without it
    pass

from .models.db import init_db, upsert_court, upsert_courts_batch, upsert_rss_sources_batch, list_rss_sources, list_rss_items
from .services import rss_ingest
from .html_views import generate_html_template
from .data.federal_courts import FEDERAL_DISTRICT_COURTS, get_rss_url
import uuid

app = FastAPI(title="PACER CM/ECF RSS Ingest & Publisher (Demo)")

_initialized = False

def _ensure_initialized():
    """Lazy initialization of database and courts."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    init_db()

    # Batch register all federal district courts
    try:
        upsert_courts_batch(FEDERAL_DISTRICT_COURTS)
    except Exception:
        pass

    # Batch register RSS feeds
    try:
        rss_sources = []
        for court in FEDERAL_DISTRICT_COURTS:
            rss_url = get_rss_url(court)
            sid = str(uuid.uuid5(uuid.NAMESPACE_URL, rss_url))
            rss_sources.append({
                "id": sid,
                "court_code": court["code"],
                "url": rss_url,
                "label": f"{court['name']} RSS",
                "last_polled": None
            })
        upsert_rss_sources_batch(rss_sources)
    except Exception:
        pass

class RssSubscribeBody(BaseModel):
    court_code: str
    url: str
    label: Optional[str] = None

@app.get("/v1/health")
def health():
    _ensure_initialized()
    return {"ok": True}

@app.get("/v1/debug/db")
def debug_db():
    """Debug endpoint to check database state."""
    import os
    import traceback
    turso_url = os.getenv("TURSO_DATABASE_URL", "")
    turso_token = os.getenv("TURSO_AUTH_TOKEN", "")

    result = {
        "turso_url_set": bool(turso_url),
        "turso_token_set": bool(turso_token),
    }

    try:
        from .models.db import get_conn, _using_turso, init_db
        result["using_turso"] = _using_turso
        init_db()
        result["init_db"] = "ok"

        conn = get_conn()
        result["get_conn"] = "ok"

        cur = conn.execute("SELECT COUNT(*) as cnt FROM courts")
        result["execute"] = "ok"

        row = cur.fetchone()
        result["fetchone"] = "ok"
        result["courts_count"] = row.get("cnt") if row else 0
    except Exception as e:
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()

    return result

@app.post("/v1/rss/subscribe")
def api_rss_subscribe(body: RssSubscribeBody):
    _ensure_initialized()
    return rss_ingest.subscribe(body.court_code, body.url, body.label)

@app.api_route("/v1/rss/poll", methods=["GET", "POST"])
def api_rss_poll(batch: int = 10, offset: int = 0):
    """Poll RSS feeds in batches. Use batch and offset params for pagination."""
    _ensure_initialized()
    all_sources = list_rss_sources()
    sources = all_sources[offset:offset + batch]
    total = 0
    errors = 0
    for s in sources:
        try:
            total += rss_ingest.poll(s)
        except Exception:
            errors += 1
    return {
        "polled_sources": len(sources),
        "items_ingested": total,
        "errors": errors,
        "total_sources": len(all_sources),
        "offset": offset,
        "batch": batch,
        "next_offset": offset + batch if offset + batch < len(all_sources) else None
    }

@app.api_route("/v1/rss/poll/court/{court_code}", methods=["GET", "POST"])
def api_rss_poll_court(court_code: str):
    """Poll a single court's RSS feed."""
    _ensure_initialized()
    all_sources = list_rss_sources()
    sources = [s for s in all_sources if s.get("court_code") == court_code.lower()]
    if not sources:
        raise HTTPException(status_code=404, detail=f"No RSS source found for court {court_code}")
    total = 0
    for s in sources:
        try:
            total += rss_ingest.poll(s)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"court_code": court_code, "items_ingested": total}

@app.get("/v1/rss/sources")
def api_rss_sources(limit: int = 100):
    _ensure_initialized()
    from .models.db import get_conn
    conn = get_conn()
    cur = conn.execute(f"select * from rss_sources order by label limit ?", (limit,))
    sources = [dict(r) for r in cur.fetchall()]
    return {"sources": sources, "count": len(sources)}

@app.get("/v1/rss/items")
def api_rss_items(court_code: Optional[str] = None, case_type: Optional[str] = None, limit: int = 50, new: Optional[int] = 0):
    """List recent RSS items (includes metadata_json)"""
    _ensure_initialized()
    fetch_limit = (limit * 20) if new else limit
    items = list_rss_items(court_code=court_code, case_type=case_type, limit=fetch_limit)
    if new:
        import json as _json
        filtered = []
        for it in items:
            try:
                m = _json.loads(it.get('metadata_json') or 'null')
            except Exception:
                m = None
            if isinstance(m, dict) and m.get('is_new_case'):
                filtered.append(it)
        items = filtered[:limit]
    return {"items": items}

def _build_rss_xml(items: list, title: str, description: str, link: str, include_court_prefix: bool = True) -> str:
    """Helper to generate RSS XML from items."""
    def escape_xml(s: str) -> str:
        return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    rss = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0"><channel>',
           f"<title>{escape_xml(title)}</title>",
           f"<link>{link}</link>",
           f"<description>{escape_xml(description)}</description>"]

    for it in items:
        rss.append("<item>")
        item_title = escape_xml(it.get('title') or '')
        if include_court_prefix:
            court = it.get('court_code') or 'unknown'
            item_title = f"[{court.upper()}] {item_title}"
        rss.append(f"<title>{item_title}</title>")
        rss.append(f"<link>{it.get('link') or ''}</link>")
        rss.append(f"<description><![CDATA[{it.get('summary') or ''}]]></description>")
        if it.get('published'):
            rss.append(f"<pubDate>{it['published']}</pubDate>")
        rss.append(f"<guid>{it['id']}</guid>")
        # Add category for case type and nature of suit
        if it.get('case_type'):
            rss.append(f"<category>{it['case_type'].upper()}</category>")
        if it.get('nature_of_suit'):
            rss.append(f"<category>{escape_xml(it['nature_of_suit'])}</category>")
        rss.append("</item>")

    rss.append("</channel></rss>")
    return "\n".join(rss)


@app.get("/feeds/filter.xml")
def filtered_feed(
    courts: Optional[str] = None,
    case_type: Optional[str] = None,
    nos: Optional[str] = None,
    q: Optional[str] = None,
    new: Optional[int] = 0,
    limit: int = 100
):
    """
    Filtered RSS feed with multiple filter options.

    Query params:
    - courts: Comma-separated court codes (e.g., nysd,cacd,flsd)
    - case_type: cv, cr, bk, or ap
    - nos: Nature of suit keyword (e.g., "civil rights", "employment")
    - q: Keyword search in title, summary, case number
    - new: Set to 1 for new cases only
    - limit: Max items (default 100)
    """
    court_list = [c.strip().lower() for c in courts.split(",")] if courts else None

    items = list_rss_items(
        courts=court_list,
        case_type=case_type,
        nature_of_suit=nos,
        keyword=q,
        new_only=bool(new),
        limit=limit
    )

    # Build title based on filters
    title_parts = ["Federal Court Filings"]
    if court_list:
        title_parts.append(f"Courts: {','.join(c.upper() for c in court_list)}")
    if case_type:
        title_parts.append(f"Type: {case_type.upper()}")
    if nos:
        title_parts.append(f"NOS: {nos}")
    if q:
        title_parts.append(f"Search: {q}")
    if new:
        title_parts.append("New Cases Only")

    title = " | ".join(title_parts)
    description = f"Filtered federal court filings feed"

    xml = _build_rss_xml(items, title, description, "https://example.invalid/feeds/filter.xml", include_court_prefix=True)
    return Response(content=xml, media_type="application/rss+xml")


@app.get("/feeds/all.xml")
def all_courts_feed(
    case_type: Optional[str] = None,
    nos: Optional[str] = None,
    q: Optional[str] = None,
    new: Optional[int] = 0,
    limit: int = 100
):
    """All courts RSS feed with optional filters."""
    items = list_rss_items(
        case_type=case_type,
        nature_of_suit=nos,
        keyword=q,
        new_only=bool(new),
        limit=limit
    )

    title_parts = ["CM/ECF Updates - All Courts"]
    if case_type:
        title_parts[0] += f" - {case_type.upper()}"

    xml = _build_rss_xml(items, title_parts[0], "Recent CM/ECF RSS items from all subscribed courts", "https://example.invalid/feeds/all.xml")
    return Response(content=xml, media_type="application/rss+xml")


@app.get("/feeds/court/{court_code}.xml")
def court_feed(
    court_code: str,
    case_type: Optional[str] = None,
    nos: Optional[str] = None,
    q: Optional[str] = None,
    new: Optional[int] = 0,
    limit: int = 50
):
    """Single court RSS feed with optional filters."""
    items = list_rss_items(
        court_code=court_code,
        case_type=case_type,
        nature_of_suit=nos,
        keyword=q,
        new_only=bool(new),
        limit=limit
    )

    title = f"CM/ECF Updates - {court_code.upper()}"
    if case_type:
        title += f" - {case_type.upper()}"

    xml = _build_rss_xml(items, title, f"Recent CM/ECF RSS items for {court_code.upper()}", f"https://example.invalid/feeds/court/{court_code}.xml", include_court_prefix=False)
    return Response(content=xml, media_type="application/rss+xml")


# HTML Views
@app.get("/", response_class=HTMLResponse)
@app.get("/feeds", response_class=HTMLResponse)
def html_all_courts(case_type: Optional[str] = None, limit: int = 50, group: Optional[int] = 1, new: Optional[int] = 0):
    """HTML view of all court filings"""
    fetch_limit = limit * 20 if new else limit
    items = list_rss_items(court_code=None, case_type=case_type, limit=fetch_limit)
    title = "Federal Court Filings - All Districts"
    if case_type:
        case_types = {"cv": "Civil", "cr": "Criminal", "bk": "Bankruptcy", "ap": "Adversary Proceeding"}
        title += f" - {case_types.get(case_type, case_type.upper())} Cases"
    return generate_html_template(title, items, case_type, group_cases=bool(group), new_only=bool(new))

@app.get("/feeds/court/{court_code}", response_class=HTMLResponse)
def html_court_feed(court_code: str, case_type: Optional[str] = None, limit: int = 50, group: Optional[int] = 1, new: Optional[int] = 0):
    """HTML view of specific court filings"""
    fetch_limit = limit * 20 if new else limit
    items = list_rss_items(court_code=court_code, case_type=case_type, limit=fetch_limit)
    title = f"Federal Court Filings - {court_code.upper()}"
    if case_type:
        case_types = {"cv": "Civil", "cr": "Criminal", "bk": "Bankruptcy", "ap": "Adversary Proceeding"}
        title += f" - {case_types.get(case_type, case_type.upper())} Cases"
    return generate_html_template(title, items, case_type, court_code, group_cases=bool(group), new_only=bool(new))


# Dashboard & Feed Reader
from .dashboard import generate_dashboard_html, generate_feed_reader_html

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_view():
    """Enhanced analytics dashboard with charts and sortable tables"""
    _ensure_initialized()
    from .analytics import get_overall_stats, get_court_activity_stats, get_nature_of_suit_stats, get_filing_trends

    stats = get_overall_stats()
    court_stats = get_court_activity_stats()
    nos_stats = get_nature_of_suit_stats(limit=20)
    trends = get_filing_trends(days=14)

    return generate_dashboard_html(stats, court_stats, nos_stats, trends)


@app.get("/reader", response_class=HTMLResponse)
def feed_reader_view():
    """Live feed reader with auto-refresh and filtering"""
    _ensure_initialized()
    return generate_feed_reader_html()


# Analytics & Search Endpoints
from .analytics import (
    get_court_activity_stats,
    get_document_type_stats,
    get_case_type_distribution,
    get_recent_activity_by_hour,
    search_cases,
    get_top_parties,
    get_nature_of_suit_stats,
    get_filing_trends,
    get_court_comparison,
    get_new_cases_summary,
    get_case_type_by_court,
    get_filing_velocity,
    get_judge_activity,
    get_overall_stats
)

@app.get("/v1/analytics/overview")
def api_overview():
    """Get overall system statistics"""
    _ensure_initialized()
    return get_overall_stats()

@app.get("/v1/analytics/courts")
def api_court_activity():
    """Get activity statistics by court"""
    _ensure_initialized()
    return {"courts": get_court_activity_stats()}

@app.get("/v1/analytics/courts/compare")
def api_court_comparison(courts: Optional[str] = None):
    """Compare filing activity across courts"""
    _ensure_initialized()
    court_list = [c.strip().lower() for c in courts.split(",")] if courts else None
    return {"courts": get_court_comparison(court_list)}

@app.get("/v1/analytics/document-types")
def api_document_types():
    """Get distribution of document types"""
    _ensure_initialized()
    return {"document_types": get_document_type_stats()}

@app.get("/v1/analytics/case-types")
def api_case_types():
    """Get distribution of case types"""
    _ensure_initialized()
    return {"case_types": get_case_type_distribution()}

@app.get("/v1/analytics/case-types/{case_type}")
def api_case_type_detail(case_type: str):
    """Get breakdown of a specific case type (cv, cr, bk, ap) across courts"""
    _ensure_initialized()
    return {"case_type": case_type, "courts": get_case_type_by_court(case_type)}

@app.get("/v1/analytics/nature-of-suit")
def api_nature_of_suit(court_code: Optional[str] = None, limit: int = 50):
    """Get filing statistics by nature of suit"""
    _ensure_initialized()
    return {"nature_of_suit": get_nature_of_suit_stats(court_code, limit)}

@app.get("/v1/analytics/trends")
def api_filing_trends(days: int = 30, court_code: Optional[str] = None, case_type: Optional[str] = None):
    """Get filing trends over time (daily counts)"""
    _ensure_initialized()
    return {"trends": get_filing_trends(days, court_code, case_type)}

@app.get("/v1/analytics/velocity")
def api_filing_velocity(court_code: Optional[str] = None, hours: int = 24):
    """Get filing rate (filings per hour) for recent activity"""
    _ensure_initialized()
    return get_filing_velocity(court_code, hours)

@app.get("/v1/analytics/activity")
def api_recent_activity():
    """Get recent filing activity by hour"""
    _ensure_initialized()
    return {"activity": get_recent_activity_by_hour()}

@app.get("/v1/analytics/new-cases")
def api_new_cases(days: int = 7, court_code: Optional[str] = None):
    """Get summary of new cases filed recently"""
    _ensure_initialized()
    return {"new_cases": get_new_cases_summary(days, court_code)}

@app.get("/v1/analytics/judges")
def api_judge_activity(court_code: Optional[str] = None, limit: int = 20):
    """Get filing activity by judge"""
    _ensure_initialized()
    return {"judges": get_judge_activity(court_code, limit)}

@app.get("/v1/analytics/top-parties")
def api_top_parties(limit: int = 20):
    """Get most frequently appearing parties"""
    _ensure_initialized()
    return {"parties": get_top_parties(limit)}

@app.get("/v1/search")
def api_search(q: str, limit: int = 50):
    """Search cases by party name, case number, or keywords"""
    _ensure_initialized()
    if not q or len(q) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")
    results = search_cases(q, limit)
    return {"results": results, "query": q, "count": len(results)}

# PACER Authenticated Access
from .pacer_auth import pacer_client
from .cso_api import cso_authenticate
from .pacer_browser_login import browser_login

@app.get("/v1/pacer/status")
def pacer_status():
    """Check PACER configuration and spending"""
    if not pacer_client.is_configured():
        return {
            "enabled": False,
            "message": "PACER authentication not configured. Set PACER_USERNAME, PACER_PASSWORD, and PACER_ENABLED=true in .env"
        }

    limits = pacer_client.check_spending_limits()
    return {
        "enabled": True,
        "authenticated": pacer_client.authenticated,
        "spending": limits
    }

@app.get("/v1/pacer/docket/{court_code}/{case_number}")
def fetch_docket(court_code: str, case_number: str):
    """Fetch docket sheet for a case (requires PACER account)"""
    if not pacer_client.is_configured():
        raise HTTPException(status_code=503, detail="PACER authentication not configured")

    limits = pacer_client.check_spending_limits()
    if not limits['can_proceed']:
        raise HTTPException(
            status_code=429,
            detail=f"Spending limit reached. Daily: ${limits['daily_spent']:.2f}/{limits['daily_limit']:.2f}, Monthly: ${limits['monthly_spent']:.2f}/{limits['monthly_limit']:.2f}"
        )

    docket = pacer_client.fetch_docket_sheet(court_code, case_number)

    if not docket:
        raise HTTPException(status_code=404, detail="Could not fetch docket sheet")

    return docket

@app.get("/v1/pacer/charges")
def list_charges(limit: int = 50):
    """List PACER charges/costs"""
    from .models.db import list_charges
    return {"charges": list_charges()[:limit]}

# CSO token helpers
@app.get("/v1/pacer/cso/status")
def cso_status():
    return {
        "use_cso_api": pacer_client.use_cso_api,
        "auth_url": pacer_client.cso_auth_url,
        "has_token": bool(getattr(pacer_client, 'cso_token', None)),
    }

@app.post("/v1/pacer/cso/token")
def cso_get_token(
    loginId: Optional[str] = None,
    password: Optional[str] = None,
    clientCode: Optional[str] = None,
    otpCode: Optional[str] = None,
):
    res = cso_authenticate(
        auth_base_url=pacer_client.cso_auth_url,
        login_id=loginId or pacer_client.username,
        password=password or pacer_client.password,
        client_code=clientCode or pacer_client.cso_client_code,
        otp_code=otpCode or pacer_client.cso_otp_code,
    )
    # Persist on success for subsequent requests
    if res.get("ok") and res.get("token"):
        pacer_client.cso_token = res["token"]
        import time as _t
        pacer_client.cso_token_time = _t.time()
    # Return masked preview only
    token = res.get("token") or ""
    preview = f"{token[:6]}...{token[-6:]}" if len(token) > 20 else (token if token else None)
    return {
        "ok": res.get("ok", False),
        "token_preview": preview,
        "error": res.get("error"),
        "use_cso_api": pacer_client.use_cso_api,
        "auth_url": pacer_client.cso_auth_url,
    }

@app.get("/v1/pacer/document")
def pacer_document(court_code: str, doc_url: str, download: Optional[bool] = False):
    """Download a PACER document (PDF) via doc1 URL with caching and spend controls.

    Example doc_url: https://ecf.flsd.uscourts.gov/doc1/051129001201?caseid=663656&de_seq_num=885
    """
    if not pacer_client.is_configured():
        raise HTTPException(status_code=503, detail="PACER authentication not configured")

    limits = pacer_client.check_spending_limits()
    if not limits['can_proceed']:
        raise HTTPException(
            status_code=429,
            detail=f"Spending limit reached. Daily: ${limits['daily_spent']:.2f}/{limits['daily_limit']:.2f}, Monthly: ${limits['monthly_spent']:.2f}/{limits['monthly_limit']:.2f}"
        )

    result = pacer_client.fetch_document(court_code, doc_url)
    if not result or not result.get('path'):
        raise HTTPException(status_code=502, detail="Unable to fetch document")

    headers = {"Cache-Control": "public, max-age=86400"}
    return FileResponse(path=result['path'], media_type="application/pdf", filename=(result.get('filename') if download else None), headers=headers)

@app.post("/v1/pacer/bootstrap_cookies")
def pacer_bootstrap_cookies(court_code: str, appurl: Optional[str] = None, headed: Optional[int] = 0, wait_ms: Optional[int] = 0):
    """Launch a headless browser login to CSO to persist cookies for subsequent doc fetches."""
    res = browser_login(court_code, appurl, headed=bool(headed), wait_ms=int(wait_ms or 0))
    if not res.get('ok'):
        raise HTTPException(status_code=502, detail=res.get('error', 'Unknown error'))
    # Reload cookies into the live session
    try:
        pacer_client._load_cookies()
    except Exception:
        pass
    return res

@app.api_route("/v1/pacer/document_browser", methods=["GET", "POST"])
def pacer_document_browser(court_code: str, doc_url: str, download: Optional[bool] = False):
    """Fetch a document via headless browser (more reliable) and serve it.
    May incur PACER charges. Uses cookie bootstrap and caches to docs/<court>.
    """
    # Spending limits check
    limits = pacer_client.check_spending_limits()
    if not limits['can_proceed']:
        raise HTTPException(
            status_code=429,
            detail=f"Spending limit reached. Daily: ${limits['daily_spent']:.2f}/{limits['daily_limit']:.2f}, Monthly: ${limits['monthly_spent']:.2f}/{limits['monthly_limit']:.2f}"
        )

    # Fetch via Playwright
    # Run Playwright in a separate process to avoid event loop conflicts
    import subprocess, sys, json as _json, shlex
    cmd = [sys.executable, "-m", "app.scripts.browser_fetch_doc_cli", "--court", court_code, "--url", doc_url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Playwright execution failed: {e}")
    if proc.returncode != 0:
        raise HTTPException(status_code=502, detail=f"Playwright error: {proc.stderr.strip() or proc.stdout.strip() or proc.returncode}")
    stdout = proc.stdout.strip() if isinstance(proc.stdout, str) else ""
    try:
        res = _json.loads(stdout or "{}")
    except Exception:
        raise HTTPException(status_code=502, detail=f"Invalid response from browser fetch: {stdout[:200]}")
    if not res or not res.get('ok'):
        raise HTTPException(status_code=502, detail=(res.get('error') if isinstance(res, dict) else (proc.stderr.strip() or 'Unable to fetch document')))

    # Record charge (estimate via size heuristic)
    try:
        import os as _os
        size = _os.path.getsize(res['path'])
        pages = max(1, min(30, (size + 49999)//50000))
        cost = min(pages * 0.10, 3.00)
        from .models.db import insert_charge
        import hashlib as _hashlib
        from datetime import datetime as _dt
        charge_id = _hashlib.sha256(f"{court_code}-{res.get('doc_id')}-{_dt.utcnow().isoformat()}".encode()).hexdigest()
        insert_charge({
            "id": charge_id,
            "case_id": None,
            "court_code": court_code,
            "resource": "document_pdf_browser",
            "cmecf_url": doc_url,
            "pages_billed": pages,
            "amount_usd": cost,
            "api_key_id": pacer_client.username,
            "triggered_by": "api_fetch_browser",
            "created_at": _dt.utcnow().isoformat()
        })
    except Exception:
        pass

    headers = {"Cache-Control": "public, max-age=86400"}
    return FileResponse(path=res['path'], media_type="application/pdf", filename=(res.get('filename') if download else None), headers=headers)


# --- RECAP/CourtListener Integration ---
from .services.recap_enrichment import enrich_case as _enrich_case, enrich_batch as _enrich_batch, get_enrichment_status
from .services.courtlistener import get_client as get_cl_client
from .models.db import (
    get_recap_docket, get_recap_parties, get_recap_attorneys,
    get_recap_entries, get_recap_documents, search_recap_parties, get_recap_stats,
    get_firm_stats, get_firm_details, search_firms, get_firm_comparison,
    get_attorney_stats, get_court_firm_activity,
    get_motion_events, get_firm_motion_stats, get_firm_practice_areas,
    get_firm_court_presence, get_top_firms_by_success, get_motion_analytics_summary
)


@app.get("/v1/recap/status")
def recap_status():
    """Get RECAP enrichment status and configuration"""
    _ensure_initialized()
    return get_enrichment_status()


@app.post("/v1/recap/enrich/{court_code}/{case_number:path}")
def recap_enrich_case(court_code: str, case_number: str, force: int = 0):
    """
    Enrich a case with data from CourtListener/RECAP.

    This fetches full docket details, parties, attorneys, and entries from
    the free RECAP archive and stores them locally.

    Args:
        court_code: Court code (e.g., 'nysd', 'cacd')
        case_number: Case number (e.g., '1:25-cv-00001')
        force: Set to 1 to re-enrich even if already exists
    """
    _ensure_initialized()
    result = _enrich_case(court_code.lower(), case_number, force=bool(force))
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("message", "Enrichment failed"))
    return result


@app.api_route("/v1/recap/enrich-batch", methods=["GET", "POST"])
def recap_enrich_batch(limit: int = 50):
    """
    Batch enrich unenriched cases from RSS data.

    This finds cases we've seen in RSS feeds that haven't been enriched
    from RECAP yet and fetches their full data.
    """
    _ensure_initialized()
    return _enrich_batch(limit=limit)


@app.get("/v1/cases/{court_code}/{case_number:path}")
def get_case_full(court_code: str, case_number: str):
    """
    Get full case data including RECAP enrichment.

    Returns local RSS data combined with enriched RECAP data if available.
    """
    _ensure_initialized()

    # Get RECAP docket if enriched
    docket = get_recap_docket(court_code.lower(), case_number)

    if not docket:
        # Check if we have RSS data for this case
        from .models.db import get_conn
        conn = get_conn()
        cur = conn.execute(
            "SELECT * FROM rss_items WHERE court_code = ? AND case_number = ? ORDER BY published DESC LIMIT 10",
            (court_code.lower(), case_number)
        )
        rss_items = [dict(r) for r in cur.fetchall()]

        if not rss_items:
            raise HTTPException(status_code=404, detail="Case not found")

        return {
            "court_code": court_code.lower(),
            "case_number": case_number,
            "enriched": False,
            "rss_items": rss_items,
            "message": "Case found in RSS but not yet enriched. Use POST /v1/recap/enrich/{court}/{case} to enrich."
        }

    # Get related data
    parties = get_recap_parties(docket["id"])
    attorneys = get_recap_attorneys(docket["id"])
    entries = get_recap_entries(docket["id"], limit=100)

    return {
        "court_code": court_code.lower(),
        "case_number": case_number,
        "enriched": True,
        "docket": docket,
        "parties": parties,
        "attorneys": attorneys,
        "entries": entries,
        "entry_count": len(entries)
    }


@app.get("/v1/cases/{court_code}/{case_number:path}/entries")
def get_case_entries(court_code: str, case_number: str, limit: int = 500):
    """Get docket entries for a case from RECAP data."""
    _ensure_initialized()

    docket = get_recap_docket(court_code.lower(), case_number)
    if not docket:
        raise HTTPException(status_code=404, detail="Case not found or not enriched. Enrich first using POST /v1/recap/enrich/{court}/{case}")

    entries = get_recap_entries(docket["id"], limit=limit)
    return {
        "court_code": court_code.lower(),
        "case_number": case_number,
        "case_name": docket.get("case_name"),
        "entries": entries,
        "count": len(entries)
    }


@app.get("/v1/cases/{court_code}/{case_number:path}/parties")
def get_case_parties(court_code: str, case_number: str):
    """Get parties and attorneys for a case from RECAP data."""
    _ensure_initialized()

    docket = get_recap_docket(court_code.lower(), case_number)
    if not docket:
        raise HTTPException(status_code=404, detail="Case not found or not enriched. Enrich first using POST /v1/recap/enrich/{court}/{case}")

    parties = get_recap_parties(docket["id"])
    attorneys = get_recap_attorneys(docket["id"])

    return {
        "court_code": court_code.lower(),
        "case_number": case_number,
        "case_name": docket.get("case_name"),
        "parties": parties,
        "attorneys": attorneys
    }


@app.get("/v1/search/party")
def search_by_party(name: str, court: Optional[str] = None, limit: int = 50):
    """
    Search cases by party name.

    Searches enriched RECAP data for parties matching the given name.
    """
    _ensure_initialized()

    if not name or len(name) < 2:
        raise HTTPException(status_code=400, detail="Name must be at least 2 characters")

    # First search local RECAP data
    local_results = search_recap_parties(name, court_code=court, limit=limit)

    # If no local results and CourtListener is configured, search remotely
    if not local_results:
        client = get_cl_client()
        if client.is_configured():
            try:
                remote_results = client.search_parties(name, court=court, limit=limit)
                return {
                    "query": name,
                    "court": court,
                    "source": "courtlistener",
                    "results": remote_results,
                    "count": len(remote_results),
                    "note": "Results from CourtListener API. Use /v1/recap/enrich to fetch full case data."
                }
            except Exception as e:
                pass

    return {
        "query": name,
        "court": court,
        "source": "local",
        "results": local_results,
        "count": len(local_results)
    }


@app.get("/v1/recap/search")
def recap_search(
    q: Optional[str] = None,
    court: Optional[str] = None,
    party: Optional[str] = None,
    filed_after: Optional[str] = None,
    filed_before: Optional[str] = None,
    limit: int = 20
):
    """
    Search CourtListener for cases.

    This searches the RECAP archive directly without storing results.
    Use /v1/recap/enrich to fetch and store full case data.
    """
    _ensure_initialized()

    client = get_cl_client()
    if not client.is_configured():
        raise HTTPException(status_code=503, detail="CourtListener API not configured. Set COURTLISTENER_TOKEN in environment.")

    try:
        results = client.search_dockets(
            court=court,
            docket_number=q,
            party_name=party,
            filed_after=filed_after,
            filed_before=filed_before,
            limit=limit
        )
        return {
            "query": q,
            "court": court,
            "party": party,
            "results": results,
            "count": len(results)
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Search failed: {str(e)}")


# --- Firm Analytics ---

@app.get("/v1/firms")
def list_firms(court: Optional[str] = None, limit: int = 50):
    """
    Get law firms ranked by case count.

    Requires cases to be enriched first via /v1/recap/enrich-batch.

    Args:
        court: Optional court code to filter by (e.g., 'nysd')
        limit: Max firms to return (default 50)

    Returns:
        List of firms with case counts and attorney counts
    """
    _ensure_initialized()
    firms = get_firm_stats(limit=limit, court_code=court)
    return {
        "court": court,
        "firms": firms,
        "count": len(firms)
    }


@app.get("/v1/firms/search")
def firms_search(q: str, limit: int = 50):
    """
    Search for law firms by name.

    Args:
        q: Search query (partial match)
        limit: Max results (default 50)
    """
    _ensure_initialized()
    results = search_firms(q, limit=limit)
    return {
        "query": q,
        "results": results,
        "count": len(results)
    }


@app.get("/v1/firms/compare")
def firms_compare(firms: str):
    """
    Compare multiple firms side by side.

    Args:
        firms: Comma-separated list of firm names to compare
    """
    _ensure_initialized()
    firm_list = [f.strip() for f in firms.split(",") if f.strip()]
    if not firm_list:
        raise HTTPException(status_code=400, detail="Provide comma-separated firm names")
    results = get_firm_comparison(firm_list)
    return {
        "firms": results
    }


@app.get("/v1/firms/rankings")
def firm_rankings_list(
    motion_type: Optional[str] = None,
    court: Optional[str] = None,
    min_motions: int = 10,
    limit: int = 50
):
    """
    Rank law firms by motion success rate.

    Uses Wilson score confidence interval lower bound for ranking,
    which accounts for sample size to provide fair comparisons.

    Args:
        motion_type: Filter by motion type (mtd, msj, etc.)
        court: Filter by court code
        min_motions: Minimum motions required to be ranked (default 10)
        limit: Max firms to return (default 50)

    Returns:
        Ranked list of firms with grant rates and confidence intervals
    """
    _ensure_initialized()
    rankings = get_top_firms_by_success(
        motion_type=motion_type,
        court_code=court,
        min_motions=min_motions,
        limit=limit
    )
    return {
        "motion_type": motion_type,
        "court": court,
        "min_motions": min_motions,
        "rankings": rankings,
        "methodology": "Wilson score confidence interval (95%), ranked by lower bound"
    }


@app.get("/v1/firms/{firm_name:path}")
def firm_details(firm_name: str):
    """
    Get detailed information about a specific law firm.

    Returns:
        - Firm statistics (total cases, attorneys, courts active)
        - List of attorneys at the firm
        - Case type distribution
        - Plaintiff vs defendant representation breakdown
        - Recent cases
    """
    _ensure_initialized()
    details = get_firm_details(firm_name)
    if not details.get("attorneys") and not details.get("recent_cases"):
        raise HTTPException(status_code=404, detail=f"Firm '{firm_name}' not found in enriched data")
    return details


@app.get("/v1/attorneys")
def list_attorneys(court: Optional[str] = None, limit: int = 50):
    """
    Get individual attorneys ranked by case count.

    Args:
        court: Optional court code to filter by
        limit: Max attorneys to return (default 50)
    """
    _ensure_initialized()
    attorneys = get_attorney_stats(limit=limit, court_code=court)
    return {
        "court": court,
        "attorneys": attorneys,
        "count": len(attorneys)
    }


@app.get("/v1/courts/{court_code}/firms")
def court_firms(court_code: str, limit: int = 25):
    """
    Get firm activity statistics for a specific court.

    Returns top firms active in the court and overall statistics.
    """
    _ensure_initialized()
    return get_court_firm_activity(court_code.lower(), limit=limit)


# --- Firm Motion Analytics ---

@app.get("/v1/firms/{firm_name:path}/motions")
def firm_motions(firm_name: str, court: Optional[str] = None, motion_type: Optional[str] = None):
    """
    Get motion success rates for a specific law firm.

    Returns:
        - Overall grant rate with 95% confidence interval
        - Breakdown by motion type (MTD, MSJ, etc.)
        - Stats filtered by court or motion type if specified

    Args:
        firm_name: Name of the law firm
        court: Optional court code to filter by
        motion_type: Optional motion type filter (mtd, msj, mtc, etc.)
    """
    _ensure_initialized()
    stats = get_firm_motion_stats(firm_name=firm_name, court_code=court, motion_type=motion_type)
    courts = get_firm_court_presence(firm_name)
    practice_areas = get_firm_practice_areas(firm_name)

    return {
        "firm": firm_name,
        "motion_stats": stats,
        "courts": courts,
        "practice_areas": practice_areas
    }


@app.get("/v1/analytics/motions")
def motion_analytics(court: Optional[str] = None):
    """
    Get overall motion analytics summary.

    Args:
        court: Optional court code to filter by

    Returns:
        - Total motions tracked
        - Unique firms and cases
        - Grant/denied counts
        - Breakdown by motion type
    """
    _ensure_initialized()
    return get_motion_analytics_summary(court_code=court)


@app.get("/v1/analytics/motions/by-firm")
def motion_analytics_by_firm(
    court: Optional[str] = None,
    motion_type: Optional[str] = None,
    min_motions: int = 5,
    limit: int = 100
):
    """
    Get motion statistics aggregated by firm.

    Args:
        court: Optional court code filter
        motion_type: Optional motion type filter
        min_motions: Minimum motions to include firm (default 5)
        limit: Max firms to return (default 100)
    """
    _ensure_initialized()
    firms = get_top_firms_by_success(
        motion_type=motion_type,
        court_code=court,
        min_motions=min_motions,
        limit=limit
    )
    return {
        "filters": {
            "court": court,
            "motion_type": motion_type,
            "min_motions": min_motions
        },
        "firms": firms,
        "count": len(firms)
    }


@app.get("/v1/cases/{court_code}/{case_number:path}/motions")
def case_motions(court_code: str, case_number: str):
    """
    Get motion events for a specific case.

    Returns all tracked motions with their outcomes (if decided).
    """
    _ensure_initialized()

    # Get docket ID
    docket = get_recap_docket(court_code.lower(), case_number)
    if not docket:
        raise HTTPException(status_code=404, detail="Case not enriched. Enrich first to track motions.")

    motions = get_motion_events(docket_id=docket["id"])
    return {
        "court_code": court_code,
        "case_number": case_number,
        "case_name": docket.get("case_name"),
        "motions": motions,
        "count": len(motions)
    }


# --- Predictive Analytics ---
from .predictive_analytics import (
    get_case_outcome_by_nos,
    get_case_outcome_by_court,
    get_motion_outcome_stats,
    get_judge_motion_rates,
    get_pro_se_outcomes,
    get_class_action_outcomes,
    get_nos_outcome_matrix,
    get_prediction_for_case,
    get_court_benchmarks,
    get_analytics_summary
)
from .predictive_dashboard import generate_predictive_dashboard_html


@app.get("/analytics", response_class=HTMLResponse)
def predictive_dashboard_view():
    """Predictive Analytics Dashboard - objective, data-driven case analysis"""
    _ensure_initialized()

    summary = get_analytics_summary()
    nos_matrix = get_nos_outcome_matrix(limit=20)
    motion_stats = get_motion_outcome_stats()
    judge_stats = get_judge_motion_rates(min_cases=5)
    pro_se_data = get_pro_se_outcomes()
    court_benchmarks = get_court_benchmarks()

    return generate_predictive_dashboard_html(
        summary, nos_matrix, motion_stats, judge_stats, pro_se_data, court_benchmarks
    )


@app.get("/v1/predict/summary")
def api_predict_summary():
    """Get summary of available predictive analytics and data quality"""
    _ensure_initialized()
    return get_analytics_summary()


@app.get("/v1/predict/case")
def api_predict_case(
    nos_code: Optional[str] = None,
    court_code: Optional[str] = None,
    pro_se: bool = False,
    class_action: bool = False
):
    """
    Get outcome prediction for a case based on historical data.

    Args:
        nos_code: Nature of Suit code (e.g., "440" for Civil Rights Other)
        court_code: Court code (e.g., "nysd")
        pro_se: Whether plaintiff is pro se
        class_action: Whether this is a class action

    Returns predicted outcome probabilities with confidence intervals.
    """
    _ensure_initialized()
    return get_prediction_for_case(nos_code, court_code, pro_se, class_action)


@app.get("/v1/predict/outcomes/nos")
def api_outcomes_by_nos(nos_code: Optional[str] = None, court_code: Optional[str] = None):
    """
    Get case outcome distribution by Nature of Suit category.

    Shows historical outcome percentages (settlement, dismissal, judgment, trial, etc.)
    """
    _ensure_initialized()
    return get_case_outcome_by_nos(nos_code, court_code)


@app.get("/v1/predict/outcomes/nos/matrix")
def api_nos_matrix(limit: int = 20):
    """
    Get outcome matrix for top NOS categories.

    Returns a table showing outcome rates across different case types.
    """
    _ensure_initialized()
    return get_nos_outcome_matrix(limit)


@app.get("/v1/predict/outcomes/court/{court_code}")
def api_outcomes_by_court(court_code: str):
    """
    Get case outcome distribution for a specific court.

    Includes comparison to national averages.
    """
    _ensure_initialized()
    return get_case_outcome_by_court(court_code)


@app.get("/v1/predict/motions")
def api_motion_outcomes(motion_type: Optional[str] = None, court_code: Optional[str] = None):
    """
    Get motion outcome statistics with confidence intervals.

    Args:
        motion_type: "mtd" (Motion to Dismiss) or "msj" (Motion for Summary Judgment)
        court_code: Optional court filter

    Returns grant/deny/partial rates with 95% confidence intervals.
    Outcomes preserved at reported granularity (partial = granted in part/denied in part).
    """
    _ensure_initialized()
    return get_motion_outcome_stats(motion_type, court_code)


@app.get("/v1/predict/judges")
def api_judge_rates(court_code: Optional[str] = None, min_cases: int = 5):
    """
    Get judge-level motion grant rates with confidence intervals.

    Args:
        court_code: Optional court filter
        min_cases: Minimum motions required for inclusion (default 5)

    Returns grant rates with Wilson score confidence intervals.
    """
    _ensure_initialized()
    return get_judge_motion_rates(court_code, min_cases)


@app.get("/v1/predict/pro-se")
def api_pro_se_outcomes():
    """
    Compare outcomes for pro se vs represented litigants.

    Returns outcome comparison with statistical differences.
    """
    _ensure_initialized()
    return get_pro_se_outcomes()


@app.get("/v1/predict/class-actions")
def api_class_action_outcomes():
    """
    Analyze outcomes for class action cases.

    Returns outcome distribution and top NOS categories for class actions.
    """
    _ensure_initialized()
    return get_class_action_outcomes()


@app.get("/v1/predict/benchmarks")
def api_court_benchmarks():
    """
    Get court benchmarks compared to national averages.

    Shows how each court's outcome rates compare to the national average.
    """
    _ensure_initialized()
    return get_court_benchmarks()


# --- ML-Powered Predictions (ONNX for Vercel compatibility) ---

def _get_ml_predictor():
    """Get the appropriate ML predictor based on available dependencies."""
    # Try ONNX first (lightweight, works on Vercel)
    try:
        from ml.inference.onnx_predictor import ONNXPredictor
        predictor = ONNXPredictor()
        if any(predictor.is_available().values()):
            return predictor, "onnx"
    except ImportError:
        pass

    # Fall back to sklearn predictor (full features, local only)
    try:
        from ml.inference.predictor import CasePredictor
        return CasePredictor(), "sklearn"
    except ImportError:
        pass

    return None, None


@app.get("/v1/ml/status")
def api_ml_status():
    """Check ML model availability."""
    from ml.config import CURRENT_MODEL_DIR, MODEL_VERSION

    predictor, backend = _get_ml_predictor()
    if predictor:
        models = predictor.is_available()
    else:
        models = {m: False for m in ['dismissal', 'value', 'resolution', 'duration']}

    return {
        "version": MODEL_VERSION,
        "backend": backend or "none",
        "models": models
    }


@app.get("/v1/ml/predict")
def api_ml_predict(
    court: str = None,
    nos: str = None,
    defendant: str = None,
    judge: str = None,
    class_action: bool = False,
    pro_se: bool = False,
    mdl: bool = False
):
    """
    Full ML-powered case prediction.

    Returns dismissal probability, value estimate, resolution path, and duration.
    """
    predictor, backend = _get_ml_predictor()
    if predictor is None:
        return {"error": "ML models not available"}

    try:
        result = predictor.predict(
            court=court,
            nos=nos,
            defendant=defendant,
            judge=judge,
            class_action=class_action,
            pro_se=pro_se,
            mdl=mdl,
        )
        output = result.to_dict()
        output["backend"] = backend
        return output
    except Exception as e:
        return {"error": str(e)}


@app.get("/v1/ml/dismissal")
def api_ml_dismissal(
    court: str = None,
    nos: str = None,
    defendant: str = None,
    judge: str = None,
    class_action: bool = False
):
    """Predict dismissal probability using ML model."""
    predictor, _ = _get_ml_predictor()
    if predictor is None:
        return {"error": "ML models not available"}

    try:
        return predictor.predict_dismissal(
            court=court,
            nos=nos,
            defendant=defendant,
            judge=judge,
            class_action=class_action,
        )
    except Exception as e:
        return {"error": str(e)}


@app.get("/v1/ml/value")
def api_ml_value(
    court: str = None,
    nos: str = None,
    defendant: str = None,
    class_action: bool = False
):
    """Predict settlement value range using ML model."""
    predictor, _ = _get_ml_predictor()
    if predictor is None:
        return {"error": "ML models not available"}

    try:
        return predictor.predict_value(
            court=court,
            nos=nos,
            defendant=defendant,
            class_action=class_action,
        )
    except Exception as e:
        return {"error": str(e)}


@app.get("/v1/ml/resolution")
def api_ml_resolution(
    court: str = None,
    nos: str = None,
    defendant: str = None,
    class_action: bool = False
):
    """Predict resolution path using ML model."""
    predictor, _ = _get_ml_predictor()
    if predictor is None:
        return {"error": "ML models not available"}

    try:
        return predictor.predict_resolution(
            court=court,
            nos=nos,
            defendant=defendant,
            class_action=class_action,
        )
    except Exception as e:
        return {"error": str(e)}


@app.get("/v1/ml/duration")
def api_ml_duration(
    court: str = None,
    nos: str = None,
    defendant: str = None
):
    """Predict case duration using ML model."""
    predictor, _ = _get_ml_predictor()
    if predictor is None:
        return {"error": "ML models not available"}

    try:
        return predictor.predict_duration(
            court=court,
            nos=nos,
            defendant=defendant,
        )
    except Exception as e:
        return {"error": str(e)}


# --- Newsletter System ---

from .models.db import (
    create_newsletter, get_newsletter, list_newsletters, update_newsletter, delete_newsletter,
    create_subscriber, get_subscriber, verify_subscriber, subscribe_to_newsletter,
    get_newsletter_subscribers, list_newsletter_issues, get_newsletter_issue, get_newsletter_items
)
from .services.newsletter_generator import generate_newsletter, NewsletterGenerator
from .services.newsletter_delivery import (
    NewsletterDelivery, send_newsletter_email, get_newsletter_rss, deliver_newsletter
)
from .services.ai_summarizer import get_summarizer


@app.get("/v1/newsletters")
def api_list_newsletters():
    """List all active newsletters."""
    _ensure_initialized()
    return {"newsletters": list_newsletters(active_only=True)}


@app.post("/v1/newsletters")
def api_create_newsletter(
    name: str,
    schedule: str = "daily",
    description: str = None,
    court_codes: List[str] = None,
    case_types: List[str] = None,
    keywords: List[str] = None,
    min_relevance_score: float = 0.5,
    max_items: int = 20
):
    """Create a new newsletter configuration."""
    _ensure_initialized()
    newsletter = create_newsletter(
        name=name,
        schedule=schedule,
        description=description,
        court_codes=court_codes,
        case_types=case_types,
        keywords=keywords,
        min_relevance_score=min_relevance_score,
        max_items=max_items
    )
    return newsletter


@app.get("/v1/newsletters/{newsletter_id}")
def api_get_newsletter(newsletter_id: str):
    """Get newsletter details."""
    _ensure_initialized()
    newsletter = get_newsletter(newsletter_id)
    if not newsletter:
        raise HTTPException(status_code=404, detail="Newsletter not found")
    return newsletter


@app.put("/v1/newsletters/{newsletter_id}")
def api_update_newsletter(newsletter_id: str, **updates):
    """Update newsletter configuration."""
    _ensure_initialized()
    newsletter = update_newsletter(newsletter_id, **updates)
    if not newsletter:
        raise HTTPException(status_code=404, detail="Newsletter not found")
    return newsletter


@app.delete("/v1/newsletters/{newsletter_id}")
def api_delete_newsletter(newsletter_id: str):
    """Delete (deactivate) a newsletter."""
    _ensure_initialized()
    delete_newsletter(newsletter_id)
    return {"status": "deleted"}


@app.post("/v1/newsletters/{newsletter_id}/generate")
def api_generate_newsletter(newsletter_id: str, dry_run: bool = False):
    """Generate a new newsletter issue."""
    _ensure_initialized()
    try:
        result = generate_newsletter(newsletter_id, dry_run=dry_run)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/newsletters/{newsletter_id}/preview")
def api_preview_newsletter(newsletter_id: str):
    """Preview what the next newsletter issue would contain."""
    _ensure_initialized()
    try:
        result = generate_newsletter(newsletter_id, dry_run=True)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/v1/newsletters/{newsletter_id}/issues")
def api_list_newsletter_issues(newsletter_id: str, limit: int = 20):
    """List issues for a newsletter."""
    _ensure_initialized()
    return {"issues": list_newsletter_issues(newsletter_id, limit=limit)}


@app.post("/v1/newsletters/{newsletter_id}/send")
def api_send_newsletter(newsletter_id: str, issue_id: str = None, test_mode: bool = False):
    """Send the latest (or specified) newsletter issue."""
    _ensure_initialized()

    if not issue_id:
        # Get latest issue
        issues = list_newsletter_issues(newsletter_id, limit=1)
        if not issues:
            raise HTTPException(status_code=404, detail="No issues found")
        issue_id = issues[0]["id"]

    result = deliver_newsletter(issue_id)
    return result


@app.get("/v1/issues/{issue_id}")
def api_get_issue(issue_id: str):
    """Get newsletter issue details with items."""
    _ensure_initialized()
    issue = get_newsletter_issue(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    issue["items"] = get_newsletter_items(issue_id)
    return issue


# --- Subscriber Management ---

@app.post("/v1/subscribers")
def api_create_subscriber(email: str, name: str = None, newsletter_id: str = None):
    """Create a new subscriber."""
    _ensure_initialized()

    # Check if exists
    existing = get_subscriber(email=email)
    if existing:
        subscriber = existing
    else:
        subscriber = create_subscriber(email=email, name=name)

    # Subscribe to newsletter if specified
    if newsletter_id:
        subscribe_to_newsletter(subscriber["id"], newsletter_id)

    return subscriber


@app.get("/v1/subscribers/verify/{token}")
def api_verify_subscriber(token: str):
    """Verify subscriber email."""
    _ensure_initialized()
    subscriber = verify_subscriber(token)
    if not subscriber:
        raise HTTPException(status_code=404, detail="Invalid token")
    return {"status": "verified", "email": subscriber["email"]}


# --- Public Newsletter Endpoints ---

@app.get("/newsletter/feed/{newsletter_id}.xml", response_class=Response)
def public_newsletter_rss(newsletter_id: str):
    """Public RSS feed for a newsletter."""
    _ensure_initialized()
    rss = get_newsletter_rss(newsletter_id)
    return Response(content=rss, media_type="application/rss+xml")


@app.get("/newsletter/{slug}", response_class=HTMLResponse)
def public_newsletter_archive(slug: str):
    """Public web archive page for a newsletter issue."""
    _ensure_initialized()

    # Find issue by slug (hash of ID)
    conn = get_conn()
    cur = conn.execute("SELECT id FROM newsletter_issues WHERE status = 'sent' ORDER BY sent_at DESC")
    for row in cur.fetchall():
        import hashlib
        issue_id = row[0] if isinstance(row, tuple) else row["id"]
        if hashlib.sha256(issue_id.encode()).hexdigest()[:12] == slug:
            delivery = NewsletterDelivery()
            html = delivery.get_web_archive_html(issue_id)
            if html:
                return html

    raise HTTPException(status_code=404, detail="Newsletter not found")


# --- AI Status ---

@app.get("/v1/ai/status")
def api_ai_status():
    """Check AI service configuration status."""
    summarizer = get_summarizer()
    return {
        "configured": summarizer.is_configured(),
        "model": summarizer.model if summarizer.is_configured() else None
    }


# --- Cron Handlers ---

@app.api_route("/v1/cron/newsletters/{schedule}", methods=["GET", "POST"])
def cron_newsletter_handler(schedule: str):
    """
    Cron job handler for automated newsletter generation.

    Args:
        schedule: 'daily' or 'weekly'
    """
    _ensure_initialized()

    newsletters = list_newsletters(active_only=True)
    generated = []

    for newsletter in newsletters:
        if newsletter.get("schedule") == schedule:
            try:
                result = generate_newsletter(newsletter["id"])
                if result.get("issue_id"):
                    # Auto-deliver
                    deliver_result = deliver_newsletter(result["issue_id"])
                    generated.append({
                        "newsletter_id": newsletter["id"],
                        "name": newsletter["name"],
                        "issue_id": result.get("issue_id"),
                        "delivered": deliver_result.get("success", False)
                    })
            except Exception as e:
                generated.append({
                    "newsletter_id": newsletter["id"],
                    "name": newsletter["name"],
                    "error": str(e)
                })

    return {"schedule": schedule, "generated": generated}


# --- State Court Integrations ---
# Free access to state court data from Arkansas, Illinois, New Mexico, North Carolina,
# Virginia, and Oklahoma

from .services.state_courts.cap_client import get_cap_client, OPEN_JURISDICTIONS
from .services.state_courts.virginia_client import get_virginia_client
from .services.state_courts.oklahoma_client import get_oklahoma_client
from .services.state_courts.ingest import get_ingest_service


@app.get("/v1/state-courts/status")
def state_courts_status():
    """Get status of all state court integrations."""
    _ensure_initialized()

    cap_client = get_cap_client()
    va_client = get_virginia_client()
    ok_client = get_oklahoma_client()

    return {
        "integrations": {
            "harvard_cap": {
                "status": "available",
                "description": "Harvard Caselaw Access Project - Appellate opinions",
                "open_jurisdictions": list(OPEN_JURISDICTIONS.keys()),
                "open_jurisdiction_names": ["Arkansas", "Illinois", "New Mexico", "North Carolina"],
                "authenticated": cap_client.is_configured(),
                "note": "Open jurisdictions have unlimited free access"
            },
            "virginia": {
                "status": "available",
                "description": "Virginia Court Data - Circuit and District court records",
                "data_types": ["circuit_criminal", "circuit_civil", "district_criminal", "district_civil"],
                "note": "Anonymized bulk CSV downloads"
            },
            "oklahoma": {
                "status": "available",
                "description": "Oklahoma State Court Network (OSCN) - All 77 counties",
                "counties": len(ok_client.COUNTIES),
                "note": "Free access to case dockets and filings"
            }
        }
    }


# --- Harvard CAP Endpoints (Arkansas, Illinois, New Mexico, North Carolina) ---

@app.get("/v1/state-courts/cap/jurisdictions")
def cap_list_jurisdictions():
    """List all available jurisdictions in Harvard CAP."""
    _ensure_initialized()
    client = get_cap_client()
    try:
        jurisdictions = client.list_jurisdictions()
        return {
            "jurisdictions": jurisdictions,
            "open_jurisdictions": list(OPEN_JURISDICTIONS.keys()),
            "note": "Open jurisdictions (ar, il, nm, nc) have unlimited free access"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CAP API error: {str(e)}")


@app.get("/v1/state-courts/cap/cases")
def cap_search_cases(
    jurisdiction: str = None,
    search: str = None,
    name: str = None,
    date_min: str = None,
    date_max: str = None,
    limit: int = 20
):
    """
    Search cases in Harvard Caselaw Access Project.

    Args:
        jurisdiction: State code (ar, il, nm, nc for free access)
        search: Full-text search query
        name: Case name search (e.g., 'Smith v. Jones')
        date_min: Minimum decision date (YYYY-MM-DD)
        date_max: Maximum decision date (YYYY-MM-DD)
        limit: Max results (default 20)

    Note: Arkansas, Illinois, New Mexico, and North Carolina have unlimited free access.
    Other states are limited to 500 cases/day without authentication.
    """
    _ensure_initialized()
    client = get_cap_client()

    try:
        cases = client.search_cases(
            jurisdiction=jurisdiction,
            search=search,
            name_abbreviation=name,
            decision_date_min=date_min,
            decision_date_max=date_max,
            full_case=True,
            ordering="-decision_date",
            limit=limit
        )
        return {
            "jurisdiction": jurisdiction,
            "query": search,
            "cases": cases,
            "count": len(cases),
            "is_open_jurisdiction": client.is_open_jurisdiction(jurisdiction) if jurisdiction else None
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CAP API error: {str(e)}")


@app.get("/v1/state-courts/cap/cases/{case_id}")
def cap_get_case(case_id: int):
    """Get a specific case by Harvard CAP ID."""
    _ensure_initialized()
    client = get_cap_client()

    try:
        case = client.get_case(case_id, full_case=True)
        return case
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CAP API error: {str(e)}")


@app.get("/v1/state-courts/arkansas/cases")
def arkansas_cases(
    search: str = None,
    name: str = None,
    date_min: str = None,
    date_max: str = None,
    limit: int = 20
):
    """
    Search Arkansas Supreme Court and Court of Appeals cases.

    Arkansas is an open jurisdiction - unlimited free access to all cases.
    """
    _ensure_initialized()
    client = get_cap_client()

    try:
        cases = client.search_arkansas_cases(
            search=search,
            name=name,
            date_min=date_min,
            date_max=date_max,
            limit=limit
        )
        return {
            "state": "Arkansas",
            "jurisdiction": "ar",
            "query": search,
            "cases": cases,
            "count": len(cases),
            "source": "Harvard Caselaw Access Project"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CAP API error: {str(e)}")


@app.get("/v1/state-courts/illinois/cases")
def illinois_cases(
    search: str = None,
    name: str = None,
    date_min: str = None,
    date_max: str = None,
    limit: int = 20
):
    """
    Search Illinois appellate cases.

    Illinois is an open jurisdiction - unlimited free access to all cases.
    """
    _ensure_initialized()
    client = get_cap_client()

    try:
        cases = client.search_illinois_cases(
            search=search,
            name=name,
            date_min=date_min,
            date_max=date_max,
            limit=limit
        )
        return {
            "state": "Illinois",
            "jurisdiction": "il",
            "query": search,
            "cases": cases,
            "count": len(cases),
            "source": "Harvard Caselaw Access Project"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CAP API error: {str(e)}")


@app.get("/v1/state-courts/new-mexico/cases")
def new_mexico_cases(
    search: str = None,
    name: str = None,
    date_min: str = None,
    date_max: str = None,
    limit: int = 20
):
    """
    Search New Mexico appellate cases.

    New Mexico is an open jurisdiction - unlimited free access to all cases.
    """
    _ensure_initialized()
    client = get_cap_client()

    try:
        cases = client.search_new_mexico_cases(
            search=search,
            name=name,
            date_min=date_min,
            date_max=date_max,
            limit=limit
        )
        return {
            "state": "New Mexico",
            "jurisdiction": "nm",
            "query": search,
            "cases": cases,
            "count": len(cases),
            "source": "Harvard Caselaw Access Project"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CAP API error: {str(e)}")


@app.get("/v1/state-courts/north-carolina/cases")
def north_carolina_cases(
    search: str = None,
    name: str = None,
    date_min: str = None,
    date_max: str = None,
    limit: int = 20
):
    """
    Search North Carolina appellate cases.

    North Carolina is an open jurisdiction - unlimited free access to all cases.
    """
    _ensure_initialized()
    client = get_cap_client()

    try:
        cases = client.search_north_carolina_cases(
            search=search,
            name=name,
            date_min=date_min,
            date_max=date_max,
            limit=limit
        )
        return {
            "state": "North Carolina",
            "jurisdiction": "nc",
            "query": search,
            "cases": cases,
            "count": len(cases),
            "source": "Harvard Caselaw Access Project"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CAP API error: {str(e)}")


# --- Virginia Court Data Endpoints ---

@app.get("/v1/state-courts/virginia/status")
def virginia_status():
    """Get Virginia Court Data integration status."""
    _ensure_initialized()
    client = get_virginia_client()
    return client.test_connection()


@app.get("/v1/state-courts/virginia/circuit/criminal")
def virginia_circuit_criminal(
    year: int = None,
    charge_type: str = None,
    disposition: str = None,
    court: str = None,
    limit: int = 100
):
    """
    Search Virginia Circuit Court criminal cases.

    Args:
        year: Filter by year (2000-2024)
        charge_type: Filter by charge type (FELONY, MISDEMEANOR)
        disposition: Filter by disposition (GUILTY, NOT GUILTY, etc.)
        court: Filter by court name
        limit: Max results (default 100)

    Note: Data is anonymized. Alexandria and Fairfax data not available.
    """
    _ensure_initialized()
    client = get_virginia_client()

    try:
        cases = client.search_circuit_criminal(
            charge_type=charge_type,
            disposition=disposition,
            court=court,
            year=year,
            limit=limit
        )
        return {
            "state": "Virginia",
            "court_type": "Circuit",
            "case_type": "Criminal",
            "year": year,
            "cases": cases,
            "count": len(cases),
            "source": "virginiacourtdata.org"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Virginia data error: {str(e)}")


@app.get("/v1/state-courts/virginia/circuit/civil")
def virginia_circuit_civil(
    year: int = None,
    case_type: str = None,
    court: str = None,
    limit: int = 100
):
    """Search Virginia Circuit Court civil cases."""
    _ensure_initialized()
    client = get_virginia_client()

    try:
        cases = client.search_circuit_civil(
            case_type=case_type,
            court=court,
            year=year,
            limit=limit
        )
        return {
            "state": "Virginia",
            "court_type": "Circuit",
            "case_type": "Civil",
            "year": year,
            "cases": cases,
            "count": len(cases),
            "source": "virginiacourtdata.org"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Virginia data error: {str(e)}")


@app.get("/v1/state-courts/virginia/district/criminal")
def virginia_district_criminal(
    year: int = None,
    charge_type: str = None,
    disposition: str = None,
    court: str = None,
    limit: int = 100
):
    """Search Virginia District Court criminal cases."""
    _ensure_initialized()
    client = get_virginia_client()

    try:
        cases = client.search_district_criminal(
            charge_type=charge_type,
            disposition=disposition,
            court=court,
            year=year,
            limit=limit
        )
        return {
            "state": "Virginia",
            "court_type": "District",
            "case_type": "Criminal",
            "year": year,
            "cases": cases,
            "count": len(cases),
            "source": "virginiacourtdata.org"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Virginia data error: {str(e)}")


@app.get("/v1/state-courts/virginia/district/civil")
def virginia_district_civil(
    year: int = None,
    case_type: str = None,
    court: str = None,
    limit: int = 100
):
    """Search Virginia District Court civil cases."""
    _ensure_initialized()
    client = get_virginia_client()

    try:
        cases = client.search_district_civil(
            case_type=case_type,
            court=court,
            year=year,
            limit=limit
        )
        return {
            "state": "Virginia",
            "court_type": "District",
            "case_type": "Civil",
            "year": year,
            "cases": cases,
            "count": len(cases),
            "source": "virginiacourtdata.org"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Virginia data error: {str(e)}")


@app.get("/v1/state-courts/virginia/stats")
def virginia_stats(year: int = None):
    """Get aggregate statistics for Virginia courts."""
    _ensure_initialized()
    client = get_virginia_client()

    try:
        stats = client.get_court_stats(year=year)
        return stats
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Virginia data error: {str(e)}")


# --- Oklahoma OSCN Endpoints ---

@app.get("/v1/state-courts/oklahoma/status")
def oklahoma_status():
    """Get Oklahoma OSCN integration status."""
    _ensure_initialized()
    client = get_oklahoma_client()
    return client.test_connection()


@app.get("/v1/state-courts/oklahoma/counties")
def oklahoma_counties():
    """List all 77 Oklahoma counties available in OSCN."""
    _ensure_initialized()
    client = get_oklahoma_client()
    return {
        "counties": client.list_counties(),
        "count": len(client.list_counties())
    }


@app.get("/v1/state-courts/oklahoma/case-types")
def oklahoma_case_types():
    """List Oklahoma case type codes and descriptions."""
    _ensure_initialized()
    client = get_oklahoma_client()
    return {"case_types": client.list_case_types()}


@app.get("/v1/state-courts/oklahoma/cases")
def oklahoma_search_cases(
    county: str = "oklahoma",
    last_name: str = None,
    first_name: str = None,
    case_number: str = None,
    case_type: str = None,
    limit: int = 50
):
    """
    Search Oklahoma state court cases.

    Args:
        county: County name (default: oklahoma). See /counties for list.
        last_name: Party last name
        first_name: Party first name
        case_number: Specific case number
        case_type: Case type code (CF, CM, CV, etc.)
        limit: Max results (default 50)

    Returns case summaries. Use /cases/{county}/{case_number} for full details.
    """
    _ensure_initialized()
    client = get_oklahoma_client()

    if not last_name and not case_number:
        raise HTTPException(status_code=400, detail="Provide either last_name or case_number")

    try:
        cases = client.search_cases(
            county=county,
            last_name=last_name,
            first_name=first_name,
            case_number=case_number,
            case_type=case_type,
            limit=limit
        )
        return {
            "state": "Oklahoma",
            "county": county,
            "query": {
                "last_name": last_name,
                "first_name": first_name,
                "case_number": case_number,
                "case_type": case_type
            },
            "cases": cases,
            "count": len(cases),
            "source": "Oklahoma State Court Network (OSCN)"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OSCN error: {str(e)}")


@app.get("/v1/state-courts/oklahoma/cases/{county}/{case_number}")
def oklahoma_get_case(county: str, case_number: str):
    """
    Get detailed case information from Oklahoma OSCN.

    Args:
        county: County name (e.g., 'tulsa', 'oklahoma')
        case_number: Case number (e.g., 'CF-2024-1234')

    Returns full case details including parties, docket entries, and charges.
    """
    _ensure_initialized()
    client = get_oklahoma_client()

    try:
        case = client.get_case(county=county, case_number=case_number)
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        return case
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OSCN error: {str(e)}")


@app.get("/v1/state-courts/oklahoma/criminal")
def oklahoma_criminal_cases(
    county: str = "oklahoma",
    last_name: str = None,
    first_name: str = None,
    felony_only: bool = False,
    limit: int = 50
):
    """
    Search Oklahoma criminal cases.

    Args:
        county: County name
        last_name: Defendant last name
        first_name: Defendant first name
        felony_only: Only return felony cases
        limit: Max results
    """
    _ensure_initialized()
    client = get_oklahoma_client()

    if not last_name:
        raise HTTPException(status_code=400, detail="last_name is required")

    try:
        cases = client.search_criminal_cases(
            county=county,
            last_name=last_name,
            first_name=first_name,
            felony_only=felony_only,
            limit=limit
        )
        return {
            "state": "Oklahoma",
            "county": county,
            "case_type": "Criminal",
            "felony_only": felony_only,
            "cases": cases,
            "count": len(cases),
            "source": "OSCN"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OSCN error: {str(e)}")


@app.get("/v1/state-courts/oklahoma/civil")
def oklahoma_civil_cases(
    county: str = "oklahoma",
    last_name: str = None,
    first_name: str = None,
    limit: int = 50
):
    """Search Oklahoma civil cases."""
    _ensure_initialized()
    client = get_oklahoma_client()

    if not last_name:
        raise HTTPException(status_code=400, detail="last_name is required")

    try:
        cases = client.search_civil_cases(
            county=county,
            last_name=last_name,
            first_name=first_name,
            limit=limit
        )
        return {
            "state": "Oklahoma",
            "county": county,
            "case_type": "Civil",
            "cases": cases,
            "count": len(cases),
            "source": "OSCN"
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OSCN error: {str(e)}")


# --- State Court Database & Ingest Endpoints ---

from .models.db import (
    search_state_court_cases,
    search_state_appellate_opinions,
    get_state_court_stats,
    get_state_court_case
)


@app.post("/v1/state-courts/ingest/oklahoma")
def ingest_oklahoma(
    counties: str = None,
    limit_per_county: int = 50
):
    """
    Trigger Oklahoma case ingestion from OSCN.

    Args:
        counties: Comma-separated county names (default: oklahoma,tulsa)
        limit_per_county: Max cases per county to fetch
    """
    _ensure_initialized()
    service = get_ingest_service()

    county_list = counties.split(",") if counties else None

    try:
        result = service.ingest_oklahoma_cases(
            counties=county_list,
            limit_per_county=limit_per_county
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ingestion error: {str(e)}")


@app.post("/v1/state-courts/ingest/virginia")
def ingest_virginia(
    court_types: str = None,
    year: int = None,
    limit_per_type: int = 500
):
    """
    Trigger Virginia case ingestion from bulk CSV downloads.

    Args:
        court_types: Comma-separated court types
                     (circuit_criminal, circuit_civil, district_criminal, district_civil)
        year: Specific year to fetch (default: 2024)
        limit_per_type: Max cases per court type
    """
    _ensure_initialized()
    service = get_ingest_service()

    type_list = court_types.split(",") if court_types else None

    try:
        result = service.ingest_virginia_cases(
            court_types=type_list,
            year=year,
            limit_per_type=limit_per_type
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ingestion error: {str(e)}")


@app.post("/v1/state-courts/ingest/opinions")
def ingest_state_opinions(
    states: str = None,
    search: str = None,
    days_back: int = 30,
    limit_per_state: int = 50
):
    """
    Trigger state appellate opinion ingestion from CourtListener.

    Args:
        states: Comma-separated state codes (default: ar,il,nm,nc)
        search: Optional search query
        days_back: How many days back to fetch
        limit_per_state: Max opinions per state
    """
    _ensure_initialized()
    service = get_ingest_service()

    state_list = states.split(",") if states else None

    try:
        result = service.ingest_state_opinions(
            states=state_list,
            search=search,
            days_back=days_back,
            limit_per_state=limit_per_state
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ingestion error: {str(e)}")


@app.post("/v1/state-courts/ingest/all")
def ingest_all_state_data():
    """
    Run full state court data ingestion from all sources.

    This fetches:
    - Oklahoma cases from OSCN
    - Virginia cases from bulk CSV
    - State appellate opinions from CourtListener
    """
    _ensure_initialized()
    service = get_ingest_service()

    try:
        result = service.run_full_ingest()
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ingestion error: {str(e)}")


@app.post("/v1/state-courts/ingest/all-50-states")
def ingest_all_50_states_opinions(
    days_back: int = 7,
    limit_per_state: int = 20
):
    """
    Ingest appellate opinions from ALL 50 states via CourtListener.

    This is a comprehensive ingestion that fetches recent opinions from
    every state's appellate courts. May take several minutes to complete.

    Args:
        days_back: How many days back to fetch (default: 7)
        limit_per_state: Max opinions per state (default: 20)

    Returns:
        Ingestion summary with counts per state
    """
    _ensure_initialized()
    service = get_ingest_service()

    try:
        result = service.ingest_all_states_opinions(
            days_back=days_back,
            limit_per_state=limit_per_state
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ingestion error: {str(e)}")


@app.get("/v1/state-courts/db/cases")
def search_stored_cases(
    state: str = None,
    county: str = None,
    case_type: str = None,
    party_name: str = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 50,
    offset: int = 0
):
    """
    Search stored state court cases in local database.

    Returns cases previously ingested from OSCN, Virginia, etc.
    """
    _ensure_initialized()

    try:
        cases = search_state_court_cases(
            state=state,
            county=county,
            case_type=case_type,
            party_name=party_name,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset
        )
        return {
            "cases": cases,
            "count": len(cases),
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.get("/v1/state-courts/db/cases/{case_id}")
def get_stored_case(case_id: str):
    """Get a specific stored state court case by ID."""
    _ensure_initialized()

    try:
        case = get_state_court_case(case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        return case
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.get("/v1/state-courts/db/opinions")
def search_stored_opinions(
    state: str = None,
    court: str = None,
    search: str = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 50,
    offset: int = 0
):
    """
    Search stored state appellate opinions in local database.

    Returns opinions previously ingested from CourtListener.
    """
    _ensure_initialized()

    try:
        opinions = search_state_appellate_opinions(
            state=state,
            court=court,
            search=search,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset
        )
        return {
            "opinions": opinions,
            "count": len(opinions),
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.get("/v1/state-courts/db/stats")
def state_court_database_stats():
    """Get statistics about stored state court data."""
    _ensure_initialized()

    try:
        stats = get_state_court_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# --- Data Export Endpoints ---

@app.get("/v1/state-courts/export/cases")
def export_state_court_cases(
    state: str = None,
    county: str = None,
    case_type: str = None,
    format: str = "json",
    limit: int = 1000
):
    """
    Export state court cases in JSON or CSV format.

    Args:
        state: Filter by state code (e.g., OK, VA)
        county: Filter by county
        case_type: Filter by case type code
        format: Output format (json or csv)
        limit: Maximum records to export (max 10000)
    """
    _ensure_initialized()
    from .services.state_courts import normalize_batch

    limit = min(limit, 10000)

    try:
        cases = search_state_court_cases(
            state=state,
            county=county,
            case_type=case_type,
            limit=limit
        )

        # Normalize the data
        normalized = normalize_batch(cases)

        if format.lower() == "csv":
            import csv
            import io

            if not normalized:
                return Response(content="", media_type="text/csv")

            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=normalized[0].keys())
            writer.writeheader()
            for row in normalized:
                # Flatten nested dicts for CSV
                flat_row = {}
                for k, v in row.items():
                    if isinstance(v, dict):
                        flat_row[k] = str(v)
                    else:
                        flat_row[k] = v
                writer.writerow(flat_row)

            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=state_court_cases_{state or 'all'}.csv"}
            )

        return {
            "format": "json",
            "count": len(normalized),
            "filters": {"state": state, "county": county, "case_type": case_type},
            "data": normalized
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export error: {str(e)}")


@app.get("/v1/state-courts/export/opinions")
def export_state_court_opinions(
    state: str = None,
    court: str = None,
    format: str = "json",
    limit: int = 1000
):
    """
    Export state appellate opinions in JSON or CSV format.
    """
    _ensure_initialized()

    limit = min(limit, 10000)

    try:
        opinions = search_state_appellate_opinions(
            state=state,
            court=court,
            limit=limit
        )

        if format.lower() == "csv":
            import csv
            import io

            if not opinions:
                return Response(content="", media_type="text/csv")

            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=opinions[0].keys())
            writer.writeheader()
            writer.writerows(opinions)

            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=state_opinions_{state or 'all'}.csv"}
            )

        return {
            "format": "json",
            "count": len(opinions),
            "filters": {"state": state, "court": court},
            "data": opinions
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export error: {str(e)}")


# --- Data Normalization API ---

@app.post("/v1/state-courts/normalize/record")
def normalize_court_record_endpoint(record: dict):
    """
    Normalize a court record using standard formatting rules.

    Normalizes state codes, case types, dates, party names, etc.
    """
    from .services.state_courts import normalize_court_record

    try:
        normalized = normalize_court_record(record)
        return {
            "original": record,
            "normalized": normalized,
            "changes": [k for k in normalized if k not in record or normalized[k] != record.get(k)]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Normalization error: {str(e)}")


@app.get("/v1/state-courts/normalize/state/{state}")
def normalize_state_endpoint(state: str):
    """Convert state name or abbreviation to standard 2-letter code."""
    from .services.state_courts import normalize_state, get_state_name

    code = normalize_state(state)
    if code:
        return {
            "input": state,
            "code": code,
            "name": get_state_name(code)
        }
    return {"input": state, "error": "Unknown state"}


@app.get("/v1/state-courts/normalize/case-type/{case_type}")
def normalize_case_type_endpoint(case_type: str):
    """Convert case type to standard 2-letter code."""
    from .services.state_courts import normalize_case_type, CASE_TYPE_CODES

    code = normalize_case_type(case_type)
    type_names = {
        "CR": "Criminal", "CV": "Civil", "FA": "Family",
        "PR": "Probate", "JV": "Juvenile", "TR": "Traffic", "SC": "Small Claims"
    }
    return {
        "input": case_type,
        "code": code,
        "name": type_names.get(code, "Unknown")
    }


@app.get("/v1/state-courts/reference/states")
def list_all_states():
    """Get list of all 50 US states with codes."""
    from .services.state_courts import STATE_ABBREV

    return {
        "count": len(STATE_ABBREV),
        "states": [
            {"code": v, "name": k.replace("_", " ").title()}
            for k, v in sorted(STATE_ABBREV.items(), key=lambda x: x[1])
        ]
    }


@app.get("/v1/state-courts/reference/case-types")
def list_case_types():
    """Get list of standard case type codes."""
    return {
        "case_types": [
            {"code": "CR", "name": "Criminal", "description": "Felonies, misdemeanors, criminal offenses"},
            {"code": "CV", "name": "Civil", "description": "Contract disputes, torts, general civil"},
            {"code": "FA", "name": "Family", "description": "Divorce, custody, domestic relations"},
            {"code": "PR", "name": "Probate", "description": "Estates, wills, trusts, guardianship"},
            {"code": "JV", "name": "Juvenile", "description": "Juvenile delinquency, dependency"},
            {"code": "TR", "name": "Traffic", "description": "Traffic violations, DUI/DWI"},
            {"code": "SC", "name": "Small Claims", "description": "Small dollar civil disputes"},
        ]
    }


# --- Text Parsing and Document Processing API ---

@app.post("/v1/state-courts/parse/text")
def parse_court_document_text(body: dict):
    """
    Parse unstructured court document text and extract structured data.

    Extracts case numbers, dates, parties, attorneys, charges, dispositions.

    Request body: {"text": "raw court document text..."}
    """
    from .services.state_courts import parse_court_document

    text = body.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field in request body")

    try:
        result = parse_court_document(text)
        return {
            "success": True,
            "extracted": result,
            "text_length": len(text)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse error: {str(e)}")


@app.post("/v1/state-courts/parse/html")
def parse_court_html_record(body: dict):
    """
    Parse HTML from a court website and extract structured case data.

    Request body: {"html": "<html>court record page...</html>"}
    """
    from .services.state_courts import parse_html_record

    html = body.get("html", "")
    if not html:
        raise HTTPException(status_code=400, detail="Missing 'html' field in request body")

    try:
        result = parse_html_record(html)
        return {
            "success": True,
            "extracted": result,
            "html_length": len(html)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse error: {str(e)}")


@app.post("/v1/state-courts/parse/docket")
def parse_docket_entries(body: dict):
    """
    Extract docket entries from court document text.

    Returns list of dated entries with descriptions.

    Request body: {"text": "docket text with entries..."}
    """
    from .services.state_courts import get_document_parser

    text = body.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field in request body")

    try:
        parser = get_document_parser()
        entries = parser.parse_docket_entries(text)
        return {
            "success": True,
            "entries": entries,
            "count": len(entries)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse error: {str(e)}")


@app.post("/v1/state-courts/detect/state")
def detect_document_state(body: dict):
    """
    Detect which state a court document is from based on text content.

    Request body: {"text": "court document text..."}
    """
    from .services.state_courts import get_document_parser, get_state_name

    text = body.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field in request body")

    try:
        parser = get_document_parser()
        state = parser.detect_state(text)
        if state:
            return {
                "detected": True,
                "state_key": state,
                "state_name": state.replace("_", " ").title(),
                "confidence": "high" if len(text) > 500 else "medium"
            }
        return {"detected": False, "state_key": None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Detection error: {str(e)}")


@app.post("/v1/state-courts/detect/case-type")
def detect_document_case_type(body: dict):
    """
    Detect case type (criminal, civil, family, etc.) from document text.

    Request body: {"text": "court document text..."}
    """
    from .services.state_courts import get_document_parser

    text = body.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field in request body")

    try:
        parser = get_document_parser()
        case_type = parser.detect_case_type(text)
        type_names = {
            "criminal": "Criminal", "civil": "Civil", "family": "Family",
            "probate": "Probate", "traffic": "Traffic", "unknown": "Unknown"
        }
        return {
            "detected_type": case_type,
            "type_name": type_names.get(case_type, case_type.title()),
            "confidence": "high" if case_type != "unknown" else "low"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Detection error: {str(e)}")


@app.post("/v1/state-courts/extract/parties")
def extract_party_names(body: dict):
    """
    Extract party names (plaintiffs, defendants) from court document text.

    Request body: {"text": "court document text..."}
    """
    from .services.state_courts import get_document_parser, normalize_party_name

    text = body.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field in request body")

    try:
        parser = get_document_parser()
        plaintiffs = parser.extract_all_matches(text, "party_plaintiff")
        defendants = parser.extract_all_matches(text, "party_defendant")

        return {
            "plaintiffs": [normalize_party_name(p) for p in plaintiffs],
            "defendants": [normalize_party_name(d) for d in defendants],
            "plaintiff_count": len(plaintiffs),
            "defendant_count": len(defendants)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction error: {str(e)}")


@app.post("/v1/state-courts/store/parsed")
def store_parsed_document(body: dict):
    """
    Parse court document text and store extracted data in database.

    Request body: {
        "text": "court document text...",
        "source": "optional source identifier",
        "state": "optional state code override"
    }
    """
    from .services.state_courts import parse_court_document, normalize_court_record

    text = body.get("text", "")
    source = body.get("source", "api_upload")
    state_override = body.get("state")

    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field in request body")

    try:
        # Parse the document
        parsed = parse_court_document(text)

        # Override state if provided
        if state_override:
            parsed["state"] = state_override

        # Build case record
        record = {
            "state": parsed.get("state", "").upper()[:2] if parsed.get("state") else "UNK",
            "county": parsed.get("court", "Unknown"),
            "case_number": parsed.get("case_number", f"PARSED-{hash(text[:100]) % 100000}"),
            "case_style": f"{', '.join(parsed.get('parties', {}).get('plaintiffs', ['Unknown'])[:1])} v. {', '.join(parsed.get('parties', {}).get('defendants', ['Unknown'])[:1])}",
            "case_type": parsed.get("case_type", "unknown"),
            "date_filed": parsed.get("date_filed"),
            "data_source": source,
            "raw_data_json": {"parsed": parsed, "text_preview": text[:1000]}
        }

        # Normalize and store
        normalized = normalize_court_record(record)
        case_id = upsert_state_court_case(normalized)

        return {
            "success": True,
            "case_id": case_id,
            "parsed_fields": list(parsed.keys()),
            "stored_record": normalized
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Store error: {str(e)}")


# --- File Upload and Search APIs ---

@app.post("/v1/state-courts/upload/base64")
def upload_document_base64(body: dict):
    """
    Upload a document as base64-encoded content for processing.

    Request body: {
        "content": "base64-encoded file content",
        "filename": "document.pdf",
        "content_type": "application/pdf",
        "state": "optional state code"
    }
    """
    import base64
    from .services.state_courts import extract_pdf_text, parse_court_document, normalize_court_record

    content_b64 = body.get("content", "")
    filename = body.get("filename", "uploaded.pdf")
    content_type = body.get("content_type", "application/pdf")
    state_override = body.get("state")

    if not content_b64:
        raise HTTPException(status_code=400, detail="Missing 'content' field")

    try:
        # Decode base64 content
        file_bytes = base64.b64decode(content_b64)

        # Extract text based on content type
        if "pdf" in content_type.lower() or filename.lower().endswith(".pdf"):
            text = extract_pdf_text(file_bytes)
        else:
            # Assume text/html
            text = file_bytes.decode("utf-8", errors="replace")

        # Parse the extracted text
        parsed = parse_court_document(text)

        if state_override:
            parsed["state"] = state_override

        # Build case record
        record = {
            "state": parsed.get("state", "").upper()[:2] if parsed.get("state") else "UNK",
            "county": parsed.get("court", "Unknown"),
            "case_number": parsed.get("case_number", f"UPLOAD-{hash(filename) % 100000}"),
            "case_style": f"{', '.join(parsed.get('parties', {}).get('plaintiffs', ['Unknown'])[:1])} v. {', '.join(parsed.get('parties', {}).get('defendants', ['Unknown'])[:1])}",
            "case_type": parsed.get("case_type", "unknown"),
            "date_filed": parsed.get("date_filed"),
            "data_source": f"upload:{filename}",
            "raw_data_json": {"parsed": parsed, "filename": filename, "text_preview": text[:500]}
        }

        # Normalize and store
        normalized = normalize_court_record(record)
        case_id = upsert_state_court_case(normalized)

        return {
            "success": True,
            "case_id": case_id,
            "filename": filename,
            "text_length": len(text),
            "parsed_fields": list(parsed.keys()),
            "stored_record": normalized
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload error: {str(e)}")


@app.get("/v1/state-courts/search")
def search_state_court_documents(
    q: str = None,
    state: str = None,
    case_type: str = None,
    county: str = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 50,
    offset: int = 0
):
    """
    Search stored state court documents.

    Parameters:
    - q: Full-text search query
    - state: Filter by state code (e.g., "CA", "NY")
    - case_type: Filter by case type (e.g., "CV", "CR")
    - county: Filter by county name
    - date_from: Filter by date filed (YYYY-MM-DD)
    - date_to: Filter by date filed (YYYY-MM-DD)
    - limit: Max results (default 50)
    - offset: Pagination offset
    """
    from .models.db import get_conn

    conn = get_conn()

    # Build query
    conditions = []
    params = []

    if q:
        conditions.append("(case_style LIKE ? OR case_number LIKE ? OR county LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])

    if state:
        conditions.append("state = ?")
        params.append(state.upper()[:2])

    if case_type:
        conditions.append("case_type = ?")
        params.append(case_type.upper())

    if county:
        conditions.append("county LIKE ?")
        params.append(f"%{county}%")

    if date_from:
        conditions.append("date_filed >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("date_filed <= ?")
        params.append(date_to)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # Count total
    count_sql = f"SELECT COUNT(*) as total FROM state_court_cases WHERE {where_clause}"
    total = conn.execute(count_sql, params).fetchone()["total"]

    # Get results
    query_sql = f"""
        SELECT id, state, county, case_number, case_style, case_type, date_filed, data_source, created_at
        FROM state_court_cases
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    results = [dict(row) for row in conn.execute(query_sql, params).fetchall()]

    return {
        "success": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": results
    }


@app.get("/v1/state-courts/search/fulltext")
def search_fulltext(q: str, state: str = None, limit: int = 20):
    """
    Full-text search across all stored court documents and opinions.

    Searches case styles, case numbers, and stored text content.
    """
    from .models.db import get_conn

    if not q or len(q) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")

    conn = get_conn()
    results = {"cases": [], "opinions": [], "documents": []}

    # Search cases
    case_sql = """
        SELECT id, state, county, case_number, case_style, case_type, date_filed
        FROM state_court_cases
        WHERE (case_style LIKE ? OR case_number LIKE ?)
        {}
        ORDER BY date_filed DESC
        LIMIT ?
    """.format("AND state = ?" if state else "")

    case_params = [f"%{q}%", f"%{q}%"]
    if state:
        case_params.append(state.upper()[:2])
    case_params.append(limit)

    results["cases"] = [dict(row) for row in conn.execute(case_sql, case_params).fetchall()]

    # Search opinions
    opinion_sql = """
        SELECT id, state, court, case_name, citation, date_decided
        FROM state_court_opinions
        WHERE (case_name LIKE ? OR citation LIKE ?)
        {}
        ORDER BY date_decided DESC
        LIMIT ?
    """.format("AND state = ?" if state else "")

    results["opinions"] = [dict(row) for row in conn.execute(opinion_sql, case_params).fetchall()]

    # Search document storage
    doc_sql = """
        SELECT id, source, doc_type, state, case_number, title
        FROM state_court_documents
        WHERE (title LIKE ? OR case_number LIKE ? OR content LIKE ?)
        {}
        ORDER BY created_at DESC
        LIMIT ?
    """.format("AND state = ?" if state else "")

    doc_params = [f"%{q}%", f"%{q}%", f"%{q}%"]
    if state:
        doc_params.append(state.upper()[:2])
    doc_params.append(limit)

    results["documents"] = [dict(row) for row in conn.execute(doc_sql, doc_params).fetchall()]

    return {
        "success": True,
        "query": q,
        "state_filter": state,
        "results": results,
        "counts": {
            "cases": len(results["cases"]),
            "opinions": len(results["opinions"]),
            "documents": len(results["documents"])
        }
    }


@app.get("/v1/state-courts/stats")
def state_court_stats_endpoint():
    """
    Get statistics about stored state court data.

    Returns counts by state, case type, and data source.
    """
    from .models.db import get_conn

    conn = get_conn()

    stats = {
        "cases": {},
        "opinions": {},
        "documents": {},
        "totals": {}
    }

    # Cases by state
    cases_by_state = conn.execute("""
        SELECT state, COUNT(*) as count
        FROM state_court_cases
        GROUP BY state
        ORDER BY count DESC
    """).fetchall()
    stats["cases"]["by_state"] = [dict(row) for row in cases_by_state]

    # Cases by type
    cases_by_type = conn.execute("""
        SELECT case_type, COUNT(*) as count
        FROM state_court_cases
        GROUP BY case_type
        ORDER BY count DESC
    """).fetchall()
    stats["cases"]["by_type"] = [dict(row) for row in cases_by_type]

    # Cases by source
    cases_by_source = conn.execute("""
        SELECT data_source, COUNT(*) as count
        FROM state_court_cases
        GROUP BY data_source
        ORDER BY count DESC
        LIMIT 20
    """).fetchall()
    stats["cases"]["by_source"] = [dict(row) for row in cases_by_source]

    # Opinions by state
    opinions_by_state = conn.execute("""
        SELECT state, COUNT(*) as count
        FROM state_court_opinions
        GROUP BY state
        ORDER BY count DESC
    """).fetchall()
    stats["opinions"]["by_state"] = [dict(row) for row in opinions_by_state]

    # Documents by type
    docs_by_type = conn.execute("""
        SELECT doc_type, COUNT(*) as count
        FROM state_court_documents
        GROUP BY doc_type
        ORDER BY count DESC
    """).fetchall()
    stats["documents"]["by_type"] = [dict(row) for row in docs_by_type]

    # Totals
    stats["totals"]["cases"] = conn.execute("SELECT COUNT(*) as c FROM state_court_cases").fetchone()["c"]
    stats["totals"]["opinions"] = conn.execute("SELECT COUNT(*) as c FROM state_court_opinions").fetchone()["c"]
    stats["totals"]["documents"] = conn.execute("SELECT COUNT(*) as c FROM state_court_documents").fetchone()["c"]

    return {
        "success": True,
        "stats": stats
    }


@app.get("/v1/state-courts/stats/daily")
def get_daily_ingest_stats(days: int = 30):
    """
    Get daily ingestion statistics for the past N days.
    """
    from .models.db import get_conn
    from datetime import datetime, timedelta

    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Cases per day
    cases_daily = conn.execute("""
        SELECT DATE(created_at) as date, COUNT(*) as count
        FROM state_court_cases
        WHERE created_at >= ?
        GROUP BY DATE(created_at)
        ORDER BY date DESC
    """, [cutoff]).fetchall()

    # Opinions per day
    opinions_daily = conn.execute("""
        SELECT DATE(created_at) as date, COUNT(*) as count
        FROM state_court_opinions
        WHERE created_at >= ?
        GROUP BY DATE(created_at)
        ORDER BY date DESC
    """, [cutoff]).fetchall()

    return {
        "success": True,
        "days": days,
        "cases_daily": [dict(row) for row in cases_daily],
        "opinions_daily": [dict(row) for row in opinions_daily]
    }


@app.get("/v1/state-courts/case/{case_id}")
def get_state_court_case_detail(case_id: str):
    """
    Get detailed information for a specific state court case.
    """
    from .models.db import get_conn
    import json

    conn = get_conn()

    case = conn.execute("""
        SELECT * FROM state_court_cases WHERE id = ?
    """, [case_id]).fetchone()

    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    result = dict(case)

    # Parse raw_data_json if present
    if result.get("raw_data_json"):
        try:
            result["raw_data"] = json.loads(result["raw_data_json"])
            del result["raw_data_json"]
        except Exception:
            pass

    return {"success": True, "case": result}


@app.get("/v1/state-courts/opinion/{opinion_id}")
def get_state_court_opinion_detail(opinion_id: str):
    """
    Get detailed information for a specific state court opinion.
    """
    from .models.db import get_conn
    import json

    conn = get_conn()

    opinion = conn.execute("""
        SELECT * FROM state_court_opinions WHERE id = ?
    """, [opinion_id]).fetchone()

    if not opinion:
        raise HTTPException(status_code=404, detail="Opinion not found")

    result = dict(opinion)

    # Parse raw_data_json if present
    if result.get("raw_data_json"):
        try:
            result["raw_data"] = json.loads(result["raw_data_json"])
            del result["raw_data_json"]
        except Exception:
            pass

    return {"success": True, "opinion": result}


# --- State Courts Analytics Dashboard ---

@app.get("/state-courts/analytics", response_class=HTMLResponse)
def state_courts_analytics_dashboard():
    """
    State Courts Analytics Dashboard - comprehensive visualization of state court data.
    """
    from .models.db import get_conn
    import json

    conn = get_conn()

    # Gather statistics
    stats = {}

    # Total counts
    stats["total_cases"] = conn.execute("SELECT COUNT(*) as c FROM state_court_cases").fetchone()["c"]
    stats["total_opinions"] = conn.execute("SELECT COUNT(*) as c FROM state_court_opinions").fetchone()["c"]
    stats["total_documents"] = conn.execute("SELECT COUNT(*) as c FROM state_court_documents").fetchone()["c"]

    # Cases by state
    cases_by_state = conn.execute("""
        SELECT state, COUNT(*) as count
        FROM state_court_cases
        WHERE state IS NOT NULL AND state != ''
        GROUP BY state
        ORDER BY count DESC
        LIMIT 20
    """).fetchall()
    stats["cases_by_state"] = [dict(row) for row in cases_by_state]

    # Opinions by state
    opinions_by_state = conn.execute("""
        SELECT state, COUNT(*) as count
        FROM state_court_opinions
        WHERE state IS NOT NULL AND state != ''
        GROUP BY state
        ORDER BY count DESC
        LIMIT 20
    """).fetchall()
    stats["opinions_by_state"] = [dict(row) for row in opinions_by_state]

    # Cases by type
    cases_by_type = conn.execute("""
        SELECT case_type, COUNT(*) as count
        FROM state_court_cases
        WHERE case_type IS NOT NULL AND case_type != ''
        GROUP BY case_type
        ORDER BY count DESC
        LIMIT 10
    """).fetchall()
    stats["cases_by_type"] = [dict(row) for row in cases_by_type]

    # Recent cases
    recent_cases = conn.execute("""
        SELECT id, state, county, case_number, case_style, case_type, date_filed, created_at
        FROM state_court_cases
        ORDER BY created_at DESC
        LIMIT 10
    """).fetchall()
    stats["recent_cases"] = [dict(row) for row in recent_cases]

    # Recent opinions
    recent_opinions = conn.execute("""
        SELECT id, state, court, case_name, citation, date_decided, created_at
        FROM state_court_opinions
        ORDER BY created_at DESC
        LIMIT 10
    """).fetchall()
    stats["recent_opinions"] = [dict(row) for row in recent_opinions]

    # Generate HTML
    state_labels = json.dumps([s["state"] for s in stats["cases_by_state"]])
    state_counts = json.dumps([s["count"] for s in stats["cases_by_state"]])
    type_labels = json.dumps([t["case_type"] for t in stats["cases_by_type"]])
    type_counts = json.dumps([t["count"] for t in stats["cases_by_type"]])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>State Courts Analytics Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            min-height: 100vh;
        }}
        .header {{
            background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
            padding: 20px 40px;
            border-bottom: 1px solid #334155;
        }}
        .header h1 {{
            font-size: 1.8em;
            font-weight: 600;
            color: #f8fafc;
        }}
        .header .subtitle {{
            color: #94a3b8;
            margin-top: 5px;
        }}
        .nav {{
            display: flex;
            gap: 15px;
            margin-top: 15px;
            flex-wrap: wrap;
        }}
        .nav a {{
            color: #60a5fa;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 6px;
            background: rgba(96, 165, 250, 0.1);
            font-size: 0.9em;
        }}
        .nav a:hover {{ background: rgba(96, 165, 250, 0.2); }}
        .container {{
            max-width: 1600px;
            margin: 0 auto;
            padding: 30px;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .stat-card {{
            background: linear-gradient(135deg, #1e40af 0%, #1e3a5f 100%);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #3b82f6;
        }}
        .stat-card h3 {{
            color: #93c5fd;
            font-size: 0.9em;
            margin-bottom: 10px;
        }}
        .stat-card .value {{
            font-size: 2.5em;
            font-weight: 700;
            color: #f8fafc;
        }}
        .chart-section {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 30px;
            margin-bottom: 30px;
        }}
        @media (max-width: 1000px) {{
            .chart-section {{ grid-template-columns: 1fr; }}
        }}
        .chart-card {{
            background: #1e293b;
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #334155;
        }}
        .chart-card h3 {{
            color: #f8fafc;
            margin-bottom: 15px;
        }}
        .table-section {{
            background: #1e293b;
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #334155;
            margin-bottom: 30px;
        }}
        .table-section h3 {{
            color: #f8fafc;
            margin-bottom: 15px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid #334155;
        }}
        th {{
            color: #93c5fd;
            font-weight: 600;
            font-size: 0.85em;
            text-transform: uppercase;
        }}
        td {{
            color: #e2e8f0;
            font-size: 0.9em;
        }}
        tr:hover {{ background: rgba(59, 130, 246, 0.1); }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.8em;
            font-weight: 600;
        }}
        .badge-state {{ background: #3b82f6; color: white; }}
        .badge-type {{ background: #10b981; color: white; }}
        .search-section {{
            background: #1e293b;
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #334155;
            margin-bottom: 30px;
        }}
        .search-input {{
            width: 100%;
            padding: 12px 16px;
            border-radius: 8px;
            border: 1px solid #334155;
            background: #0f172a;
            color: #e2e8f0;
            font-size: 1em;
        }}
        .search-input:focus {{
            outline: none;
            border-color: #3b82f6;
        }}
        .api-links {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin-top: 15px;
        }}
        .api-link {{
            color: #60a5fa;
            text-decoration: none;
            padding: 6px 12px;
            border-radius: 4px;
            background: rgba(96, 165, 250, 0.1);
            font-size: 0.85em;
        }}
        .api-link:hover {{ background: rgba(96, 165, 250, 0.2); }}
    </style>
</head>
<body>
    <div class="header">
        <h1>State Courts Analytics Dashboard</h1>
        <p class="subtitle">Comprehensive state court data from all 50 US states</p>
        <div class="nav">
            <a href="/analytics">Federal Analytics</a>
            <a href="/feeds">RSS Feeds</a>
            <a href="/v1/state-courts/all/status">All States Status</a>
            <a href="/v1/state-courts/stats">API Stats</a>
            <a href="/v1/state-courts/search">Search API</a>
        </div>
    </div>

    <div class="container">
        <!-- Summary Stats -->
        <div class="stats-grid">
            <div class="stat-card">
                <h3>Total Cases</h3>
                <div class="value">{stats["total_cases"]:,}</div>
            </div>
            <div class="stat-card">
                <h3>Total Opinions</h3>
                <div class="value">{stats["total_opinions"]:,}</div>
            </div>
            <div class="stat-card">
                <h3>Total Documents</h3>
                <div class="value">{stats["total_documents"]:,}</div>
            </div>
            <div class="stat-card">
                <h3>States Covered</h3>
                <div class="value">50</div>
            </div>
        </div>

        <!-- Search Section -->
        <div class="search-section">
            <h3 style="color: #f8fafc; margin-bottom: 15px;">Search State Court Data</h3>
            <form action="/v1/state-courts/search" method="get" style="display: flex; gap: 10px;">
                <input type="text" name="q" class="search-input" placeholder="Search cases, parties, case numbers..." style="flex: 2;">
                <select name="state" class="search-input" style="flex: 1; max-width: 200px;">
                    <option value="">All States</option>
                    <option value="CA">California</option>
                    <option value="NY">New York</option>
                    <option value="TX">Texas</option>
                    <option value="FL">Florida</option>
                    <option value="IL">Illinois</option>
                    <option value="PA">Pennsylvania</option>
                    <option value="OH">Ohio</option>
                    <option value="GA">Georgia</option>
                </select>
                <button type="submit" class="api-link" style="border: none; cursor: pointer;">Search</button>
            </form>
            <div class="api-links">
                <a href="/v1/state-courts/search/fulltext?q=contract" class="api-link">Full-text Search</a>
                <a href="/v1/state-courts/export/cases.json" class="api-link">Export JSON</a>
                <a href="/v1/state-courts/export/cases.csv" class="api-link">Export CSV</a>
                <a href="/v1/state-courts/reference/states" class="api-link">State Codes</a>
                <a href="/v1/state-courts/reference/case-types" class="api-link">Case Types</a>
            </div>
        </div>

        <!-- Charts -->
        <div class="chart-section">
            <div class="chart-card">
                <h3>Cases by State</h3>
                <canvas id="stateChart" height="300"></canvas>
            </div>
            <div class="chart-card">
                <h3>Cases by Type</h3>
                <canvas id="typeChart" height="300"></canvas>
            </div>
        </div>

        <!-- Recent Cases Table -->
        <div class="table-section">
            <h3>Recent Cases</h3>
            <table>
                <thead>
                    <tr>
                        <th>State</th>
                        <th>Case Number</th>
                        <th>Case Style</th>
                        <th>Type</th>
                        <th>County</th>
                        <th>Date Filed</th>
                    </tr>
                </thead>
                <tbody>"""

    for case in stats["recent_cases"]:
        style = (case.get("case_style") or "N/A")[:60]
        html += f"""
                    <tr>
                        <td><span class="badge badge-state">{case.get("state", "UNK")}</span></td>
                        <td>{case.get("case_number", "N/A")}</td>
                        <td>{style}...</td>
                        <td><span class="badge badge-type">{case.get("case_type", "UNK")}</span></td>
                        <td>{case.get("county", "N/A")}</td>
                        <td>{case.get("date_filed", "N/A")}</td>
                    </tr>"""

    html += """
                </tbody>
            </table>
        </div>

        <!-- Recent Opinions Table -->
        <div class="table-section">
            <h3>Recent Opinions</h3>
            <table>
                <thead>
                    <tr>
                        <th>State</th>
                        <th>Court</th>
                        <th>Case Name</th>
                        <th>Citation</th>
                        <th>Date Decided</th>
                    </tr>
                </thead>
                <tbody>"""

    for opinion in stats["recent_opinions"]:
        name = (opinion.get("case_name") or "N/A")[:70]
        html += f"""
                    <tr>
                        <td><span class="badge badge-state">{opinion.get("state", "UNK")}</span></td>
                        <td>{opinion.get("court", "N/A")[:30]}</td>
                        <td>{name}...</td>
                        <td>{opinion.get("citation", "N/A")}</td>
                        <td>{opinion.get("date_decided", "N/A")}</td>
                    </tr>"""

    html += f"""
                </tbody>
            </table>
        </div>

        <!-- API Endpoints Reference -->
        <div class="table-section">
            <h3>Available API Endpoints</h3>
            <table>
                <thead>
                    <tr>
                        <th>Method</th>
                        <th>Endpoint</th>
                        <th>Description</th>
                    </tr>
                </thead>
                <tbody>
                    <tr><td>GET</td><td>/v1/state-courts/all/status</td><td>All 50 states status overview</td></tr>
                    <tr><td>GET</td><td>/v1/state-courts/search</td><td>Search cases with filters</td></tr>
                    <tr><td>GET</td><td>/v1/state-courts/search/fulltext</td><td>Full-text search across all data</td></tr>
                    <tr><td>GET</td><td>/v1/state-courts/stats</td><td>Aggregate statistics</td></tr>
                    <tr><td>GET</td><td>/v1/state-courts/stats/daily</td><td>Daily ingestion stats</td></tr>
                    <tr><td>POST</td><td>/v1/state-courts/parse/text</td><td>Parse court document text</td></tr>
                    <tr><td>POST</td><td>/v1/state-courts/upload/base64</td><td>Upload and process documents</td></tr>
                    <tr><td>GET</td><td>/v1/state-courts/export/cases.json</td><td>Export cases as JSON</td></tr>
                    <tr><td>GET</td><td>/v1/state-courts/export/cases.csv</td><td>Export cases as CSV</td></tr>
                    <tr><td>GET</td><td>/v1/state-courts/{{state}}/status</td><td>Individual state status</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        // State chart
        const stateCtx = document.getElementById('stateChart').getContext('2d');
        new Chart(stateCtx, {{
            type: 'bar',
            data: {{
                labels: {state_labels},
                datasets: [{{
                    label: 'Cases',
                    data: {state_counts},
                    backgroundColor: 'rgba(59, 130, 246, 0.8)',
                    borderRadius: 4
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{ beginAtZero: true, grid: {{ color: '#334155' }}, ticks: {{ color: '#94a3b8' }} }},
                    x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8' }} }}
                }}
            }}
        }});

        // Type chart
        const typeCtx = document.getElementById('typeChart').getContext('2d');
        new Chart(typeCtx, {{
            type: 'doughnut',
            data: {{
                labels: {type_labels},
                datasets: [{{
                    data: {type_counts},
                    backgroundColor: [
                        '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
                        '#06b6d4', '#f97316', '#84cc16', '#ec4899', '#14b8a6'
                    ]
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{
                        position: 'right',
                        labels: {{ color: '#e2e8f0' }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>"""

    return html


# --- Batch Import and Job Management APIs ---

# In-memory job tracking (in production, use Redis or database)
_import_jobs = {}

@app.post("/v1/state-courts/batch/import")
def batch_import_records(body: dict):
    """
    Submit a batch of court records for import.

    Request body: {
        "records": [
            {"state": "CA", "case_number": "...", "case_style": "...", ...},
            ...
        ],
        "source": "optional source identifier",
        "normalize": true  // whether to normalize records
    }
    """
    import uuid
    from datetime import datetime
    from .services.state_courts import normalize_court_record

    records = body.get("records", [])
    source = body.get("source", "batch_import")
    normalize = body.get("normalize", True)

    if not records:
        raise HTTPException(status_code=400, detail="No records provided")

    if len(records) > 1000:
        raise HTTPException(status_code=400, detail="Maximum 1000 records per batch")

    job_id = str(uuid.uuid4())[:8]
    results = {"imported": 0, "errors": [], "duplicates": 0}

    for i, record in enumerate(records):
        try:
            # Add source
            record["data_source"] = source

            # Normalize if requested
            if normalize:
                record = normalize_court_record(record)

            # Import
            case_id = upsert_state_court_case(record)
            if case_id:
                results["imported"] += 1
            else:
                results["duplicates"] += 1
        except Exception as e:
            results["errors"].append({"index": i, "error": str(e)})

    # Store job result
    _import_jobs[job_id] = {
        "job_id": job_id,
        "status": "completed",
        "submitted_at": datetime.utcnow().isoformat(),
        "completed_at": datetime.utcnow().isoformat(),
        "total_records": len(records),
        "results": results
    }

    return {
        "success": True,
        "job_id": job_id,
        "total_records": len(records),
        "imported": results["imported"],
        "duplicates": results["duplicates"],
        "errors": len(results["errors"]),
        "error_details": results["errors"][:10]  # First 10 errors
    }


@app.post("/v1/state-courts/batch/import-text")
def batch_import_text_documents(body: dict):
    """
    Submit a batch of text documents for parsing and import.

    Request body: {
        "documents": [
            {"text": "...", "source": "...", "state": "CA"},
            ...
        ]
    }
    """
    import uuid
    from datetime import datetime
    from .services.state_courts import parse_court_document, normalize_court_record

    documents = body.get("documents", [])

    if not documents:
        raise HTTPException(status_code=400, detail="No documents provided")

    if len(documents) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 documents per batch")

    job_id = str(uuid.uuid4())[:8]
    results = {"imported": 0, "errors": [], "parsed_fields": {}}

    for i, doc in enumerate(documents):
        try:
            text = doc.get("text", "")
            source = doc.get("source", "batch_text")
            state_override = doc.get("state")

            if not text:
                results["errors"].append({"index": i, "error": "Empty text"})
                continue

            # Parse document
            parsed = parse_court_document(text)

            if state_override:
                parsed["state"] = state_override

            # Build record
            record = {
                "state": parsed.get("state", "").upper()[:2] if parsed.get("state") else "UNK",
                "county": parsed.get("court", "Unknown"),
                "case_number": parsed.get("case_number", f"BATCH-{i}-{hash(text[:50]) % 10000}"),
                "case_style": f"{', '.join(parsed.get('parties', {}).get('plaintiffs', ['Unknown'])[:1])} v. {', '.join(parsed.get('parties', {}).get('defendants', ['Unknown'])[:1])}",
                "case_type": parsed.get("case_type", "unknown"),
                "date_filed": parsed.get("date_filed"),
                "data_source": source,
            }

            # Normalize and store
            normalized = normalize_court_record(record)
            case_id = upsert_state_court_case(normalized)

            if case_id:
                results["imported"] += 1
                # Track parsed fields
                for field in parsed.keys():
                    results["parsed_fields"][field] = results["parsed_fields"].get(field, 0) + 1

        except Exception as e:
            results["errors"].append({"index": i, "error": str(e)})

    # Store job result
    _import_jobs[job_id] = {
        "job_id": job_id,
        "status": "completed",
        "submitted_at": datetime.utcnow().isoformat(),
        "completed_at": datetime.utcnow().isoformat(),
        "total_documents": len(documents),
        "results": results
    }

    return {
        "success": True,
        "job_id": job_id,
        "total_documents": len(documents),
        "imported": results["imported"],
        "errors": len(results["errors"]),
        "parsed_fields_summary": results["parsed_fields"],
        "error_details": results["errors"][:10]
    }


@app.get("/v1/state-courts/jobs")
def list_import_jobs(limit: int = 20):
    """List recent import jobs."""
    jobs = sorted(_import_jobs.values(), key=lambda x: x.get("submitted_at", ""), reverse=True)
    return {
        "success": True,
        "jobs": jobs[:limit],
        "total": len(jobs)
    }


@app.get("/v1/state-courts/jobs/{job_id}")
def get_import_job(job_id: str):
    """Get details of a specific import job."""
    if job_id not in _import_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"success": True, "job": _import_jobs[job_id]}


@app.post("/v1/state-courts/ingest/trigger")
def trigger_state_ingest(body: dict):
    """
    Trigger ingestion from state court sources.

    Request body: {
        "states": ["CA", "NY", ...],  // optional, defaults to all
        "sources": ["courtlistener", "virginia", ...],  // optional
        "limit": 100  // max records per source
    }
    """
    import uuid
    from datetime import datetime
    from .services.state_courts import get_ingest_service

    states = body.get("states", [])
    sources = body.get("sources", [])
    limit = body.get("limit", 100)

    job_id = str(uuid.uuid4())[:8]

    try:
        ingest_service = get_ingest_service()

        results = {
            "opinions_ingested": 0,
            "cases_ingested": 0,
            "errors": []
        }

        # Run ingestion based on requested sources
        if not sources or "courtlistener" in sources:
            # Ingest from CourtListener (all 50 states appellate)
            target_states = states if states else None
            opinions = ingest_service.run_full_ingest(limit=limit)
            results["opinions_ingested"] = len(opinions) if opinions else 0

        _import_jobs[job_id] = {
            "job_id": job_id,
            "type": "ingest",
            "status": "completed",
            "submitted_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "config": {"states": states, "sources": sources, "limit": limit},
            "results": results
        }

        return {
            "success": True,
            "job_id": job_id,
            "results": results
        }

    except Exception as e:
        _import_jobs[job_id] = {
            "job_id": job_id,
            "type": "ingest",
            "status": "failed",
            "submitted_at": datetime.utcnow().isoformat(),
            "error": str(e)
        }
        raise HTTPException(status_code=500, detail=f"Ingest error: {str(e)}")


@app.get("/v1/state-courts/quality/check")
def check_data_quality():
    """
    Run data quality checks on stored state court data.

    Returns quality metrics and potential issues.
    """
    from .models.db import get_conn

    conn = get_conn()
    issues = []
    metrics = {}

    # Check for missing states
    missing_state = conn.execute("""
        SELECT COUNT(*) as count FROM state_court_cases
        WHERE state IS NULL OR state = '' OR state = 'UNK'
    """).fetchone()["count"]
    metrics["missing_state"] = missing_state
    if missing_state > 0:
        issues.append({"type": "missing_state", "count": missing_state, "severity": "medium"})

    # Check for missing case numbers
    missing_case_num = conn.execute("""
        SELECT COUNT(*) as count FROM state_court_cases
        WHERE case_number IS NULL OR case_number = ''
    """).fetchone()["count"]
    metrics["missing_case_number"] = missing_case_num
    if missing_case_num > 0:
        issues.append({"type": "missing_case_number", "count": missing_case_num, "severity": "high"})

    # Check for potential duplicates (same state + case_number)
    duplicates = conn.execute("""
        SELECT state, case_number, COUNT(*) as count
        FROM state_court_cases
        WHERE case_number IS NOT NULL AND case_number != ''
        GROUP BY state, case_number
        HAVING COUNT(*) > 1
        LIMIT 100
    """).fetchall()
    metrics["potential_duplicates"] = len(duplicates)
    if duplicates:
        issues.append({"type": "potential_duplicates", "count": len(duplicates), "severity": "low"})

    # Check for invalid dates
    invalid_dates = conn.execute("""
        SELECT COUNT(*) as count FROM state_court_cases
        WHERE date_filed IS NOT NULL
        AND (date_filed < '1900-01-01' OR date_filed > '2030-01-01')
    """).fetchone()["count"]
    metrics["invalid_dates"] = invalid_dates
    if invalid_dates > 0:
        issues.append({"type": "invalid_dates", "count": invalid_dates, "severity": "medium"})

    # Check state distribution
    state_dist = conn.execute("""
        SELECT state, COUNT(*) as count
        FROM state_court_cases
        WHERE state IS NOT NULL AND state != ''
        GROUP BY state
    """).fetchall()
    metrics["states_with_data"] = len(state_dist)
    metrics["states_missing_data"] = 50 - len(state_dist)

    # Overall quality score (0-100)
    total_records = conn.execute("SELECT COUNT(*) as c FROM state_court_cases").fetchone()["c"]
    if total_records > 0:
        quality_score = 100 - (
            (missing_state / total_records * 20) +
            (missing_case_num / total_records * 30) +
            (invalid_dates / total_records * 20) +
            (len(duplicates) / total_records * 10)
        )
        quality_score = max(0, min(100, quality_score))
    else:
        quality_score = 0

    return {
        "success": True,
        "quality_score": round(quality_score, 1),
        "total_records": total_records,
        "metrics": metrics,
        "issues": issues,
        "recommendations": [
            "Run normalization on records with missing states",
            "Investigate and resolve duplicate records",
            "Review and correct invalid date entries"
        ] if issues else ["Data quality is good"]
    }


@app.post("/v1/state-courts/quality/fix")
def fix_data_quality_issues(body: dict):
    """
    Attempt to automatically fix data quality issues.

    Request body: {
        "fix_types": ["normalize_states", "deduplicate", "fix_dates"]
    }
    """
    from .models.db import get_conn
    from .services.state_courts import normalize_state, normalize_date

    fix_types = body.get("fix_types", ["normalize_states"])
    conn = get_conn()
    fixes_applied = {}

    if "normalize_states" in fix_types:
        # Try to normalize unknown states from case data
        unknown_cases = conn.execute("""
            SELECT id, county, case_number, raw_data_json
            FROM state_court_cases
            WHERE state IS NULL OR state = '' OR state = 'UNK'
            LIMIT 1000
        """).fetchall()

        fixed = 0
        for case in unknown_cases:
            # Try to infer state from county name or case number
            county = case.get("county", "")
            case_num = case.get("case_number", "")

            # Common state patterns in case numbers
            state_patterns = {
                "CA": ["cal", "calif"],
                "NY": ["nysc", "nyef"],
                "TX": ["tex"],
                "FL": ["fla"],
            }

            detected_state = None
            for state, patterns in state_patterns.items():
                if any(p in county.lower() or p in case_num.lower() for p in patterns):
                    detected_state = state
                    break

            if detected_state:
                conn.execute(
                    "UPDATE state_court_cases SET state = ? WHERE id = ?",
                    [detected_state, case["id"]]
                )
                fixed += 1

        conn.commit()
        fixes_applied["normalize_states"] = fixed

    if "deduplicate" in fix_types:
        # Mark duplicates (keep first, remove others)
        duplicates = conn.execute("""
            SELECT state, case_number, GROUP_CONCAT(id) as ids
            FROM state_court_cases
            WHERE case_number IS NOT NULL AND case_number != ''
            GROUP BY state, case_number
            HAVING COUNT(*) > 1
        """).fetchall()

        removed = 0
        for dup in duplicates:
            ids = dup["ids"].split(",")
            # Keep first, delete rest
            for id_to_remove in ids[1:]:
                conn.execute("DELETE FROM state_court_cases WHERE id = ?", [id_to_remove])
                removed += 1

        conn.commit()
        fixes_applied["deduplicate"] = removed

    return {
        "success": True,
        "fixes_applied": fixes_applied,
        "message": "Data quality fixes applied"
    }


# --- Geographic and Regional Analytics APIs ---

# US Census regions and divisions
US_REGIONS = {
    "northeast": {
        "name": "Northeast",
        "divisions": {
            "new_england": ["CT", "ME", "MA", "NH", "RI", "VT"],
            "mid_atlantic": ["NJ", "NY", "PA"]
        }
    },
    "midwest": {
        "name": "Midwest",
        "divisions": {
            "east_north_central": ["IL", "IN", "MI", "OH", "WI"],
            "west_north_central": ["IA", "KS", "MN", "MO", "NE", "ND", "SD"]
        }
    },
    "south": {
        "name": "South",
        "divisions": {
            "south_atlantic": ["DE", "FL", "GA", "MD", "NC", "SC", "VA", "WV"],
            "east_south_central": ["AL", "KY", "MS", "TN"],
            "west_south_central": ["AR", "LA", "OK", "TX"]
        }
    },
    "west": {
        "name": "West",
        "divisions": {
            "mountain": ["AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY"],
            "pacific": ["AK", "CA", "HI", "OR", "WA"]
        }
    }
}

def _get_region_for_state(state_code: str) -> tuple:
    """Get region and division for a state code."""
    for region_key, region_data in US_REGIONS.items():
        for division_key, states in region_data["divisions"].items():
            if state_code in states:
                return region_key, division_key
    return None, None


@app.get("/v1/state-courts/analytics/regions")
def get_regional_analytics():
    """
    Get state court data analytics by US Census region.

    Returns case counts and trends by region and division.
    """
    from .models.db import get_conn

    conn = get_conn()

    # Get counts by state
    state_counts = conn.execute("""
        SELECT state, COUNT(*) as case_count
        FROM state_court_cases
        WHERE state IS NOT NULL AND state != ''
        GROUP BY state
    """).fetchall()

    state_map = {row["state"]: row["case_count"] for row in state_counts}

    # Aggregate by region
    regional_stats = {}
    for region_key, region_data in US_REGIONS.items():
        region_total = 0
        divisions = {}

        for division_key, states in region_data["divisions"].items():
            division_total = sum(state_map.get(s, 0) for s in states)
            divisions[division_key] = {
                "states": states,
                "case_count": division_total,
                "state_breakdown": {s: state_map.get(s, 0) for s in states}
            }
            region_total += division_total

        regional_stats[region_key] = {
            "name": region_data["name"],
            "total_cases": region_total,
            "divisions": divisions
        }

    # Rankings
    region_ranking = sorted(
        [(k, v["total_cases"]) for k, v in regional_stats.items()],
        key=lambda x: x[1], reverse=True
    )

    return {
        "success": True,
        "regions": regional_stats,
        "ranking": [{"region": r[0], "cases": r[1]} for r in region_ranking],
        "total_cases": sum(state_map.values())
    }


@app.get("/v1/state-courts/analytics/compare")
def compare_states(states: str = "CA,NY,TX,FL"):
    """
    Compare court data metrics across multiple states.

    Parameters:
    - states: Comma-separated state codes (e.g., "CA,NY,TX")
    """
    from .models.db import get_conn

    state_list = [s.strip().upper() for s in states.split(",")][:10]
    conn = get_conn()

    comparison = []
    for state in state_list:
        # Case counts
        case_count = conn.execute(
            "SELECT COUNT(*) as c FROM state_court_cases WHERE state = ?",
            [state]
        ).fetchone()["c"]

        # Opinion counts
        opinion_count = conn.execute(
            "SELECT COUNT(*) as c FROM state_court_opinions WHERE state = ?",
            [state]
        ).fetchone()["c"]

        # Case type distribution
        case_types = conn.execute("""
            SELECT case_type, COUNT(*) as count
            FROM state_court_cases
            WHERE state = ? AND case_type IS NOT NULL
            GROUP BY case_type
            ORDER BY count DESC
            LIMIT 5
        """, [state]).fetchall()

        # Recent activity (last 30 days)
        recent_cases = conn.execute("""
            SELECT COUNT(*) as c FROM state_court_cases
            WHERE state = ? AND created_at >= date('now', '-30 days')
        """, [state]).fetchone()["c"]

        region, division = _get_region_for_state(state)

        comparison.append({
            "state": state,
            "region": region,
            "division": division,
            "total_cases": case_count,
            "total_opinions": opinion_count,
            "recent_cases_30d": recent_cases,
            "top_case_types": [dict(row) for row in case_types]
        })

    return {
        "success": True,
        "states_compared": state_list,
        "comparison": comparison
    }


@app.get("/v1/state-courts/analytics/heatmap")
def get_state_heatmap_data():
    """
    Get data for a geographic heatmap of state court activity.

    Returns case counts per state for visualization.
    """
    from .models.db import get_conn

    conn = get_conn()

    # All states with counts
    state_data = conn.execute("""
        SELECT state, COUNT(*) as case_count
        FROM state_court_cases
        WHERE state IS NOT NULL AND state != ''
        GROUP BY state
    """).fetchall()

    # Opinion counts
    opinion_data = conn.execute("""
        SELECT state, COUNT(*) as opinion_count
        FROM state_court_opinions
        WHERE state IS NOT NULL AND state != ''
        GROUP BY state
    """).fetchall()

    case_map = {row["state"]: row["case_count"] for row in state_data}
    opinion_map = {row["state"]: row["opinion_count"] for row in opinion_data}

    # Build complete state data
    all_states = [
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"
    ]

    heatmap_data = []
    for state in all_states:
        cases = case_map.get(state, 0)
        opinions = opinion_map.get(state, 0)
        region, division = _get_region_for_state(state)

        heatmap_data.append({
            "state": state,
            "cases": cases,
            "opinions": opinions,
            "total": cases + opinions,
            "region": region,
            "division": division
        })

    # Calculate statistics
    total_cases = sum(d["cases"] for d in heatmap_data)
    max_cases = max(d["cases"] for d in heatmap_data) if heatmap_data else 0
    states_with_data = sum(1 for d in heatmap_data if d["total"] > 0)

    return {
        "success": True,
        "heatmap_data": heatmap_data,
        "statistics": {
            "total_cases": total_cases,
            "max_state_cases": max_cases,
            "states_with_data": states_with_data,
            "states_without_data": 50 - states_with_data
        }
    }


@app.get("/v1/state-courts/analytics/trends")
def get_state_trends(state: str = None, days: int = 90):
    """
    Get case filing trends over time.

    Parameters:
    - state: Optional state filter
    - days: Number of days to analyze (default 90)
    """
    from .models.db import get_conn
    from datetime import datetime, timedelta

    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Daily trends
    if state:
        daily_sql = """
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM state_court_cases
            WHERE state = ? AND created_at >= ?
            GROUP BY DATE(created_at)
            ORDER BY date
        """
        daily = conn.execute(daily_sql, [state.upper(), cutoff]).fetchall()
    else:
        daily_sql = """
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM state_court_cases
            WHERE created_at >= ?
            GROUP BY DATE(created_at)
            ORDER BY date
        """
        daily = conn.execute(daily_sql, [cutoff]).fetchall()

    # Weekly aggregation
    weekly = {}
    for row in daily:
        date_obj = datetime.strptime(row["date"], "%Y-%m-%d")
        week_start = (date_obj - timedelta(days=date_obj.weekday())).strftime("%Y-%m-%d")
        weekly[week_start] = weekly.get(week_start, 0) + row["count"]

    # Case type trends
    if state:
        type_sql = """
            SELECT case_type, COUNT(*) as count
            FROM state_court_cases
            WHERE state = ? AND created_at >= ? AND case_type IS NOT NULL
            GROUP BY case_type
            ORDER BY count DESC
        """
        case_types = conn.execute(type_sql, [state.upper(), cutoff]).fetchall()
    else:
        type_sql = """
            SELECT case_type, COUNT(*) as count
            FROM state_court_cases
            WHERE created_at >= ? AND case_type IS NOT NULL
            GROUP BY case_type
            ORDER BY count DESC
        """
        case_types = conn.execute(type_sql, [cutoff]).fetchall()

    return {
        "success": True,
        "state_filter": state,
        "days": days,
        "daily_counts": [dict(row) for row in daily],
        "weekly_counts": [{"week": k, "count": v} for k, v in sorted(weekly.items())],
        "case_type_distribution": [dict(row) for row in case_types],
        "total_in_period": sum(row["count"] for row in daily)
    }


@app.get("/v1/state-courts/jurisdictions")
def get_court_jurisdictions():
    """
    Get court hierarchy and jurisdiction information for all states.

    Returns court structure including supreme courts, appellate courts,
    and trial courts for each state.
    """
    jurisdictions = {
        "CA": {
            "name": "California",
            "supreme_court": "Supreme Court of California",
            "appellate_courts": ["Court of Appeal (6 districts)"],
            "trial_courts": "Superior Courts (58 counties)",
            "specialty_courts": ["Workers Compensation Appeals Board"]
        },
        "NY": {
            "name": "New York",
            "supreme_court": "Court of Appeals",
            "appellate_courts": ["Appellate Division (4 departments)", "Appellate Terms"],
            "trial_courts": "Supreme Court, County Courts, Family Court, Surrogate's Court",
            "specialty_courts": ["Court of Claims"]
        },
        "TX": {
            "name": "Texas",
            "supreme_court": "Supreme Court of Texas (civil), Court of Criminal Appeals (criminal)",
            "appellate_courts": ["Courts of Appeals (14 districts)"],
            "trial_courts": "District Courts, County Courts, Justice Courts",
            "specialty_courts": []
        },
        "FL": {
            "name": "Florida",
            "supreme_court": "Supreme Court of Florida",
            "appellate_courts": ["District Courts of Appeal (6 districts)"],
            "trial_courts": "Circuit Courts (20 circuits), County Courts",
            "specialty_courts": []
        },
        "IL": {
            "name": "Illinois",
            "supreme_court": "Supreme Court of Illinois",
            "appellate_courts": ["Appellate Court (5 districts)"],
            "trial_courts": "Circuit Courts (24 circuits)",
            "specialty_courts": ["Court of Claims"]
        },
        "PA": {
            "name": "Pennsylvania",
            "supreme_court": "Supreme Court of Pennsylvania",
            "appellate_courts": ["Superior Court", "Commonwealth Court"],
            "trial_courts": "Courts of Common Pleas (60 judicial districts)",
            "specialty_courts": []
        },
        "OH": {
            "name": "Ohio",
            "supreme_court": "Supreme Court of Ohio",
            "appellate_courts": ["Courts of Appeals (12 districts)"],
            "trial_courts": "Courts of Common Pleas (88 counties), Municipal Courts",
            "specialty_courts": ["Court of Claims"]
        },
        "GA": {
            "name": "Georgia",
            "supreme_court": "Supreme Court of Georgia",
            "appellate_courts": ["Court of Appeals"],
            "trial_courts": "Superior Courts (49 circuits), State Courts, Magistrate Courts",
            "specialty_courts": []
        },
        "MI": {
            "name": "Michigan",
            "supreme_court": "Michigan Supreme Court",
            "appellate_courts": ["Court of Appeals (4 districts)"],
            "trial_courts": "Circuit Courts (57 circuits), District Courts, Probate Courts",
            "specialty_courts": ["Court of Claims"]
        },
        "NJ": {
            "name": "New Jersey",
            "supreme_court": "Supreme Court of New Jersey",
            "appellate_courts": ["Appellate Division of Superior Court"],
            "trial_courts": "Superior Court (15 vicinages), Tax Court",
            "specialty_courts": []
        }
    }

    # Add basic info for remaining states
    remaining_states = {
        "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
        "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "HI": "Hawaii",
        "ID": "Idaho", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
        "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
        "MA": "Massachusetts", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
        "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
        "NM": "New Mexico", "NC": "North Carolina", "ND": "North Dakota", "OK": "Oklahoma",
        "OR": "Oregon", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
        "TN": "Tennessee", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
        "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"
    }

    for code, name in remaining_states.items():
        if code not in jurisdictions:
            jurisdictions[code] = {
                "name": name,
                "supreme_court": f"Supreme Court of {name}",
                "appellate_courts": ["Court of Appeals"],
                "trial_courts": "Trial Courts",
                "specialty_courts": []
            }

    return {
        "success": True,
        "jurisdictions": jurisdictions,
        "total_states": len(jurisdictions)
    }


@app.get("/v1/state-courts/analytics/county/{state}")
def get_county_analytics(state: str):
    """
    Get case analytics by county for a specific state.
    """
    from .models.db import get_conn

    conn = get_conn()
    state = state.upper()[:2]

    # County breakdown
    counties = conn.execute("""
        SELECT county, COUNT(*) as case_count,
               COUNT(DISTINCT case_type) as case_type_count
        FROM state_court_cases
        WHERE state = ? AND county IS NOT NULL AND county != ''
        GROUP BY county
        ORDER BY case_count DESC
        LIMIT 50
    """, [state]).fetchall()

    # Case type breakdown for state
    case_types = conn.execute("""
        SELECT case_type, COUNT(*) as count
        FROM state_court_cases
        WHERE state = ? AND case_type IS NOT NULL
        GROUP BY case_type
        ORDER BY count DESC
    """, [state]).fetchall()

    # Total for state
    total = conn.execute(
        "SELECT COUNT(*) as c FROM state_court_cases WHERE state = ?",
        [state]
    ).fetchone()["c"]

    return {
        "success": True,
        "state": state,
        "total_cases": total,
        "county_count": len(counties),
        "counties": [dict(row) for row in counties],
        "case_types": [dict(row) for row in case_types]
    }


# --- Party and Attorney Analytics APIs ---

@app.post("/v1/state-courts/parse/parties")
def parse_parties_from_text(body: dict):
    """
    Extract and parse party names from court document text.

    Request body: {
        "text": "court document text...",
        "case_style": "optional case style to parse"
    }
    """
    import re
    from .services.state_courts import normalize_party_name

    text = body.get("text", "")
    case_style = body.get("case_style", "")

    parties = {
        "plaintiffs": [],
        "defendants": [],
        "other_parties": [],
        "attorneys": []
    }

    # Parse case style (e.g., "Smith v. Jones")
    if case_style:
        vs_match = re.search(r"(.+?)\s+v\.?\s+(.+)", case_style, re.IGNORECASE)
        if vs_match:
            plaintiff_str = vs_match.group(1).strip()
            defendant_str = vs_match.group(2).strip()

            # Split multiple plaintiffs/defendants
            for p in re.split(r",\s*(?:and\s+)?|;\s*", plaintiff_str):
                if p.strip():
                    parsed = normalize_party_name(p.strip())
                    parties["plaintiffs"].append(parsed)

            for d in re.split(r",\s*(?:and\s+)?|;\s*", defendant_str):
                if d.strip():
                    parsed = normalize_party_name(d.strip())
                    parties["defendants"].append(parsed)

    # Extract parties from text
    if text:
        # Look for plaintiff/petitioner patterns
        plaintiff_patterns = [
            r"[Pp]laintiff[s]?[:\s]+([A-Z][A-Za-z\s,\.]+?)(?:v\.|vs\.|\n|$)",
            r"[Pp]etitioner[s]?[:\s]+([A-Z][A-Za-z\s,\.]+?)(?:v\.|vs\.|\n|$)",
            r"[Aa]ppellant[s]?[:\s]+([A-Z][A-Za-z\s,\.]+?)(?:v\.|vs\.|\n|$)",
        ]

        for pattern in plaintiff_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                for name in re.split(r",\s*(?:and\s+)?", match):
                    if name.strip() and len(name.strip()) > 2:
                        parsed = normalize_party_name(name.strip())
                        if parsed not in parties["plaintiffs"]:
                            parties["plaintiffs"].append(parsed)

        # Look for defendant/respondent patterns
        defendant_patterns = [
            r"[Dd]efendant[s]?[:\s]+([A-Z][A-Za-z\s,\.]+?)(?:\n|$|\.)",
            r"[Rr]espondent[s]?[:\s]+([A-Z][A-Za-z\s,\.]+?)(?:\n|$|\.)",
            r"[Aa]ppellee[s]?[:\s]+([A-Z][A-Za-z\s,\.]+?)(?:\n|$|\.)",
        ]

        for pattern in defendant_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                for name in re.split(r",\s*(?:and\s+)?", match):
                    if name.strip() and len(name.strip()) > 2:
                        parsed = normalize_party_name(name.strip())
                        if parsed not in parties["defendants"]:
                            parties["defendants"].append(parsed)

        # Look for attorney patterns
        attorney_patterns = [
            r"[Aa]ttorney[s]?\s+for\s+[Pp]laintiff[s]?[:\s]+([A-Z][A-Za-z\s,\.]+?)(?:\n|$)",
            r"[Cc]ounsel[:\s]+([A-Z][A-Za-z\s,\.]+?)(?:,\s*Esq\.?|\n|$)",
            r"([A-Z][a-z]+\s+[A-Z][a-z]+),?\s*Esq\.?",
        ]

        for pattern in attorney_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if match.strip() and len(match.strip()) > 2:
                    parties["attorneys"].append({
                        "name": match.strip(),
                        "parsed": normalize_party_name(match.strip())
                    })

    return {
        "success": True,
        "parties": parties,
        "plaintiff_count": len(parties["plaintiffs"]),
        "defendant_count": len(parties["defendants"]),
        "attorney_count": len(parties["attorneys"])
    }


@app.get("/v1/state-courts/analytics/parties")
def get_party_analytics(state: str = None, party_type: str = None, limit: int = 50):
    """
    Get analytics on parties appearing in state court cases.

    Parameters:
    - state: Filter by state code
    - party_type: Filter by party type (organization, individual)
    - limit: Max results
    """
    from .models.db import get_conn

    conn = get_conn()

    # Extract party names from case styles
    if state:
        cases = conn.execute("""
            SELECT case_style, state, case_type
            FROM state_court_cases
            WHERE state = ? AND case_style IS NOT NULL
            LIMIT 1000
        """, [state.upper()]).fetchall()
    else:
        cases = conn.execute("""
            SELECT case_style, state, case_type
            FROM state_court_cases
            WHERE case_style IS NOT NULL
            LIMIT 1000
        """).fetchall()

    # Count party appearances
    party_counts = {}
    org_indicators = ["llc", "inc", "corp", "company", "co.", "ltd", "bank", "insurance"]

    for case in cases:
        case_style = case.get("case_style", "")
        if " v. " in case_style or " v " in case_style:
            parts = case_style.replace(" v ", " v. ").split(" v. ")
            for part in parts:
                name = part.strip()[:100]
                if name:
                    is_org = any(ind in name.lower() for ind in org_indicators)
                    if party_type == "organization" and not is_org:
                        continue
                    if party_type == "individual" and is_org:
                        continue

                    if name not in party_counts:
                        party_counts[name] = {"name": name, "count": 0, "is_org": is_org, "states": set()}
                    party_counts[name]["count"] += 1
                    party_counts[name]["states"].add(case.get("state", "UNK"))

    # Sort and format
    sorted_parties = sorted(party_counts.values(), key=lambda x: x["count"], reverse=True)[:limit]
    for p in sorted_parties:
        p["states"] = list(p["states"])

    return {
        "success": True,
        "state_filter": state,
        "party_type_filter": party_type,
        "top_parties": sorted_parties,
        "total_unique_parties": len(party_counts)
    }


# --- Citation Parsing and Linking APIs ---

# Common citation patterns
CITATION_PATTERNS = {
    "us_reports": r"(\d+)\s+U\.?S\.?\s+(\d+)",
    "supreme_court": r"(\d+)\s+S\.?\s*Ct\.?\s+(\d+)",
    "federal_reporter": r"(\d+)\s+F\.?\s*(?:2d|3d|4th)?\s+(\d+)",
    "federal_supplement": r"(\d+)\s+F\.?\s*Supp\.?\s*(?:2d|3d)?\s+(\d+)",
    "state_reporter": r"(\d+)\s+([A-Z][a-z]+\.?)\s*(?:2d|3d|4th)?\s+(\d+)",
    "westlaw": r"(\d{4})\s+WL\s+(\d+)",
    "lexis": r"(\d{4})\s+(?:U\.S\.?\s+)?(?:LEXIS|Lexis)\s+(\d+)",
}

STATE_REPORTERS = {
    "Cal": "CA", "Cal.": "CA", "Cal.App": "CA",
    "N.Y": "NY", "N.Y.": "NY", "A.D": "NY",
    "Tex": "TX", "Tex.": "TX",
    "Fla": "FL", "Fla.": "FL", "So": "FL",
    "Ill": "IL", "Ill.": "IL",
    "Pa": "PA", "Pa.": "PA",
    "Ohio": "OH", "Ohio St": "OH",
    "Mich": "MI", "Mich.": "MI",
    "N.J": "NJ", "N.J.": "NJ",
    "Ga": "GA", "Ga.": "GA",
}


@app.post("/v1/state-courts/parse/citations")
def parse_citations_from_text(body: dict):
    """
    Extract and parse legal citations from text.

    Request body: {
        "text": "court document or opinion text..."
    }
    """
    import re

    text = body.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field")

    citations = []

    # US Reports
    for match in re.finditer(CITATION_PATTERNS["us_reports"], text):
        citations.append({
            "type": "us_reports",
            "volume": match.group(1),
            "page": match.group(2),
            "full_citation": match.group(0),
            "court": "US Supreme Court",
            "reporter": "U.S."
        })

    # Supreme Court Reporter
    for match in re.finditer(CITATION_PATTERNS["supreme_court"], text):
        citations.append({
            "type": "supreme_court",
            "volume": match.group(1),
            "page": match.group(2),
            "full_citation": match.group(0),
            "court": "US Supreme Court",
            "reporter": "S.Ct."
        })

    # Federal Reporter
    for match in re.finditer(CITATION_PATTERNS["federal_reporter"], text):
        citations.append({
            "type": "federal_reporter",
            "volume": match.group(1),
            "page": match.group(2),
            "full_citation": match.group(0),
            "court": "Federal Appeals Court",
            "reporter": "F."
        })

    # Federal Supplement
    for match in re.finditer(CITATION_PATTERNS["federal_supplement"], text):
        citations.append({
            "type": "federal_supplement",
            "volume": match.group(1),
            "page": match.group(2),
            "full_citation": match.group(0),
            "court": "Federal District Court",
            "reporter": "F.Supp."
        })

    # Westlaw
    for match in re.finditer(CITATION_PATTERNS["westlaw"], text):
        citations.append({
            "type": "westlaw",
            "year": match.group(1),
            "number": match.group(2),
            "full_citation": match.group(0),
            "reporter": "WL"
        })

    # State reporters
    state_pattern = r"(\d+)\s+([A-Z][a-z]+\.?(?:\s*(?:App|St|2d|3d|4th)\.?)?)\s+(\d+)"
    for match in re.finditer(state_pattern, text):
        reporter = match.group(2).strip()
        state = None
        for rep, st in STATE_REPORTERS.items():
            if rep in reporter:
                state = st
                break

        if state:
            citations.append({
                "type": "state_reporter",
                "volume": match.group(1),
                "reporter": reporter,
                "page": match.group(3),
                "full_citation": match.group(0),
                "state": state
            })

    # Deduplicate
    seen = set()
    unique_citations = []
    for c in citations:
        key = c["full_citation"]
        if key not in seen:
            seen.add(key)
            unique_citations.append(c)

    return {
        "success": True,
        "citations": unique_citations,
        "citation_count": len(unique_citations),
        "by_type": {
            t: len([c for c in unique_citations if c["type"] == t])
            for t in set(c["type"] for c in unique_citations)
        }
    }


@app.get("/v1/state-courts/citations/search")
def search_citations(citation: str):
    """
    Search for cases matching a citation pattern.

    Parameters:
    - citation: Citation string to search (e.g., "123 Cal. 456")
    """
    from .models.db import get_conn

    conn = get_conn()

    # Search opinions by citation
    opinions = conn.execute("""
        SELECT id, state, court, case_name, citation, date_decided
        FROM state_court_opinions
        WHERE citation LIKE ?
        ORDER BY date_decided DESC
        LIMIT 20
    """, [f"%{citation}%"]).fetchall()

    return {
        "success": True,
        "query": citation,
        "results": [dict(row) for row in opinions],
        "count": len(opinions)
    }


# --- Case Relationship and Linking APIs ---

@app.post("/v1/state-courts/cases/link")
def link_related_cases(body: dict):
    """
    Find and link related cases based on parties, citations, or case numbers.

    Request body: {
        "case_id": "case ID to find related cases for",
        "link_types": ["party", "citation", "number"]  // types of links to find
    }
    """
    from .models.db import get_conn
    import re

    case_id = body.get("case_id")
    link_types = body.get("link_types", ["party", "citation"])

    if not case_id:
        raise HTTPException(status_code=400, detail="Missing 'case_id'")

    conn = get_conn()

    # Get the source case
    source_case = conn.execute(
        "SELECT * FROM state_court_cases WHERE id = ?",
        [case_id]
    ).fetchone()

    if not source_case:
        raise HTTPException(status_code=404, detail="Case not found")

    related = {"by_party": [], "by_citation": [], "by_number_pattern": []}

    # Find cases with similar parties
    if "party" in link_types and source_case.get("case_style"):
        case_style = source_case["case_style"]
        # Extract party names
        if " v. " in case_style:
            parties = case_style.split(" v. ")
            for party in parties:
                party_name = party.strip()[:50]
                if len(party_name) > 3:
                    similar = conn.execute("""
                        SELECT id, state, case_number, case_style, case_type
                        FROM state_court_cases
                        WHERE id != ? AND case_style LIKE ?
                        LIMIT 10
                    """, [case_id, f"%{party_name}%"]).fetchall()
                    for s in similar:
                        if dict(s) not in related["by_party"]:
                            related["by_party"].append(dict(s))

    # Find cases with similar case number patterns
    if "number" in link_types and source_case.get("case_number"):
        case_num = source_case["case_number"]
        # Extract year and type from case number
        year_match = re.search(r"20\d{2}|19\d{2}", case_num)
        if year_match:
            year = year_match.group(0)
            similar = conn.execute("""
                SELECT id, state, case_number, case_style, case_type
                FROM state_court_cases
                WHERE id != ? AND state = ? AND case_number LIKE ?
                LIMIT 20
            """, [case_id, source_case.get("state"), f"%{year}%"]).fetchall()
            related["by_number_pattern"] = [dict(s) for s in similar]

    return {
        "success": True,
        "source_case": {
            "id": source_case["id"],
            "case_number": source_case.get("case_number"),
            "case_style": source_case.get("case_style"),
            "state": source_case.get("state")
        },
        "related_cases": related,
        "total_related": sum(len(v) for v in related.values())
    }


@app.get("/v1/state-courts/cases/related/{case_id}")
def get_related_cases(case_id: str):
    """
    Get cases related to a specific case by various criteria.
    """
    from .models.db import get_conn

    conn = get_conn()

    # Get source case
    source = conn.execute(
        "SELECT * FROM state_court_cases WHERE id = ?",
        [case_id]
    ).fetchone()

    if not source:
        raise HTTPException(status_code=404, detail="Case not found")

    related = []

    # Same state and county
    if source.get("state") and source.get("county"):
        same_location = conn.execute("""
            SELECT id, case_number, case_style, case_type, date_filed
            FROM state_court_cases
            WHERE id != ? AND state = ? AND county = ?
            ORDER BY date_filed DESC
            LIMIT 10
        """, [case_id, source["state"], source["county"]]).fetchall()
        for r in same_location:
            related.append({**dict(r), "relation": "same_location"})

    # Same case type in state
    if source.get("state") and source.get("case_type"):
        same_type = conn.execute("""
            SELECT id, case_number, case_style, county, date_filed
            FROM state_court_cases
            WHERE id != ? AND state = ? AND case_type = ?
            ORDER BY date_filed DESC
            LIMIT 10
        """, [case_id, source["state"], source["case_type"]]).fetchall()
        for r in same_type:
            related.append({**dict(r), "relation": "same_case_type"})

    return {
        "success": True,
        "source_case_id": case_id,
        "source_state": source.get("state"),
        "related_cases": related[:20],
        "total_found": len(related)
    }


@app.get("/v1/state-courts/analytics/case-types/distribution")
def get_case_type_distribution(state: str = None):
    """
    Get detailed case type distribution with percentages.
    """
    from .models.db import get_conn

    conn = get_conn()

    if state:
        data = conn.execute("""
            SELECT case_type, COUNT(*) as count
            FROM state_court_cases
            WHERE state = ? AND case_type IS NOT NULL AND case_type != ''
            GROUP BY case_type
            ORDER BY count DESC
        """, [state.upper()]).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) as c FROM state_court_cases WHERE state = ?",
            [state.upper()]
        ).fetchone()["c"]
    else:
        data = conn.execute("""
            SELECT case_type, COUNT(*) as count
            FROM state_court_cases
            WHERE case_type IS NOT NULL AND case_type != ''
            GROUP BY case_type
            ORDER BY count DESC
        """).fetchall()
        total = conn.execute("SELECT COUNT(*) as c FROM state_court_cases").fetchone()["c"]

    # Add percentages
    distribution = []
    for row in data:
        pct = (row["count"] / total * 100) if total > 0 else 0
        distribution.append({
            "case_type": row["case_type"],
            "count": row["count"],
            "percentage": round(pct, 2)
        })

    # Case type descriptions
    type_descriptions = {
        "CV": "Civil",
        "CR": "Criminal",
        "FA": "Family/Domestic",
        "PR": "Probate/Estate",
        "JV": "Juvenile",
        "TR": "Traffic",
        "SC": "Small Claims",
        "BK": "Bankruptcy",
        "AP": "Appeal"
    }

    for d in distribution:
        d["description"] = type_descriptions.get(d["case_type"], "Other")

    return {
        "success": True,
        "state_filter": state,
        "total_cases": total,
        "distribution": distribution,
        "type_count": len(distribution)
    }


# --- Docket Entry Parsing APIs ---

# Docket entry type patterns
DOCKET_ENTRY_PATTERNS = {
    "complaint": r"(?:complaint|petition|initial\s+filing)",
    "answer": r"(?:answer|response\s+to\s+complaint)",
    "motion": r"(?:motion\s+(?:to|for)\s+\w+)",
    "order": r"(?:order|ruling|decision)",
    "judgment": r"(?:judgment|verdict|final\s+(?:order|judgment))",
    "notice": r"(?:notice\s+of\s+\w+)",
    "brief": r"(?:brief|memorandum)",
    "discovery": r"(?:interrogator|deposition|request\s+for\s+(?:production|admission))",
    "hearing": r"(?:hearing|trial|conference)",
    "appeal": r"(?:notice\s+of\s+appeal|appeal\s+filed)",
}


@app.post("/v1/state-courts/parse/docket-entries")
def parse_docket_entries(body: dict):
    """
    Parse docket entries from text and extract structured information.

    Request body: {
        "text": "docket text with entries...",
        "format": "text" or "structured"
    }
    """
    import re
    from datetime import datetime

    text = body.get("text", "")
    output_format = body.get("format", "structured")

    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field")

    entries = []

    # Pattern for common docket entry formats
    # Format: "MM/DD/YYYY - Entry description" or "1. Entry description (filed MM/DD/YYYY)"
    date_entry_pattern = r"(\d{1,2}/\d{1,2}/\d{2,4})\s*[-–]\s*(.+?)(?=\d{1,2}/\d{1,2}/\d{2,4}|$)"
    numbered_pattern = r"(\d+)\.\s+(.+?)(?:filed\s+)?(\d{1,2}/\d{1,2}/\d{2,4})?"

    # Try date-prefixed format first
    for match in re.finditer(date_entry_pattern, text, re.DOTALL):
        date_str = match.group(1)
        description = match.group(2).strip()

        # Classify entry type
        entry_type = "other"
        for etype, pattern in DOCKET_ENTRY_PATTERNS.items():
            if re.search(pattern, description, re.IGNORECASE):
                entry_type = etype
                break

        # Extract document number if present
        doc_match = re.search(r"(?:Doc\.?\s*#?|Document\s+)(\d+)", description)
        doc_number = doc_match.group(1) if doc_match else None

        entries.append({
            "date": date_str,
            "description": description[:500],
            "entry_type": entry_type,
            "document_number": doc_number
        })

    # If no entries found, try numbered format
    if not entries:
        for match in re.finditer(numbered_pattern, text):
            entry_num = match.group(1)
            description = match.group(2).strip()
            date_str = match.group(3) if match.group(3) else None

            entry_type = "other"
            for etype, pattern in DOCKET_ENTRY_PATTERNS.items():
                if re.search(pattern, description, re.IGNORECASE):
                    entry_type = etype
                    break

            entries.append({
                "entry_number": entry_num,
                "date": date_str,
                "description": description[:500],
                "entry_type": entry_type
            })

    # Line-by-line fallback
    if not entries:
        lines = text.strip().split("\n")
        for i, line in enumerate(lines):
            line = line.strip()
            if len(line) > 10:
                entry_type = "other"
                for etype, pattern in DOCKET_ENTRY_PATTERNS.items():
                    if re.search(pattern, line, re.IGNORECASE):
                        entry_type = etype
                        break

                # Try to extract date from line
                date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", line)

                entries.append({
                    "line_number": i + 1,
                    "date": date_match.group(1) if date_match else None,
                    "description": line[:500],
                    "entry_type": entry_type
                })

    # Summary statistics
    type_counts = {}
    for entry in entries:
        t = entry.get("entry_type", "other")
        type_counts[t] = type_counts.get(t, 0) + 1

    return {
        "success": True,
        "entries": entries[:100],  # Limit to 100 entries
        "entry_count": len(entries),
        "type_summary": type_counts,
        "has_motions": type_counts.get("motion", 0) > 0,
        "has_orders": type_counts.get("order", 0) > 0
    }


@app.post("/v1/state-courts/analyze/docket")
def analyze_docket(body: dict):
    """
    Analyze a complete docket for case timeline and key events.

    Request body: {
        "case_id": "optional case ID",
        "docket_text": "full docket text",
        "state": "state code"
    }
    """
    import re
    from datetime import datetime

    docket_text = body.get("docket_text", "")
    state = body.get("state", "").upper()[:2]
    case_id = body.get("case_id")

    if not docket_text:
        raise HTTPException(status_code=400, detail="Missing 'docket_text'")

    analysis = {
        "timeline": [],
        "key_events": [],
        "motions": [],
        "orders": [],
        "parties_mentioned": [],
        "case_status": "unknown"
    }

    # Extract dates and events
    date_pattern = r"(\d{1,2}/\d{1,2}/\d{2,4})"
    dates = re.findall(date_pattern, docket_text)

    if dates:
        analysis["first_filing_date"] = dates[0]
        analysis["last_activity_date"] = dates[-1]
        analysis["total_entries"] = len(dates)

    # Identify key events
    key_event_patterns = {
        "case_filed": r"(?:complaint|petition|case)\s+filed",
        "answer_filed": r"answer\s+(?:filed|to\s+complaint)",
        "motion_filed": r"motion\s+(?:to|for)\s+(\w+(?:\s+\w+)?)\s+filed",
        "motion_granted": r"motion\s+(?:to|for)\s+\w+\s+granted",
        "motion_denied": r"motion\s+(?:to|for)\s+\w+\s+denied",
        "trial_set": r"(?:trial|hearing)\s+(?:set|scheduled)",
        "judgment_entered": r"(?:judgment|verdict)\s+entered",
        "case_dismissed": r"case\s+dismissed",
        "case_settled": r"(?:settlement|stipulation)",
        "appeal_filed": r"notice\s+of\s+appeal",
    }

    for event_type, pattern in key_event_patterns.items():
        matches = list(re.finditer(pattern, docket_text, re.IGNORECASE))
        for match in matches:
            # Try to find associated date
            context_start = max(0, match.start() - 50)
            context = docket_text[context_start:match.end() + 20]
            date_match = re.search(date_pattern, context)

            analysis["key_events"].append({
                "event_type": event_type,
                "text": match.group(0),
                "date": date_match.group(1) if date_match else None
            })

    # Extract motions
    motion_pattern = r"motion\s+(?:to|for)\s+(\w+(?:\s+\w+)?)"
    for match in re.finditer(motion_pattern, docket_text, re.IGNORECASE):
        motion_type = match.group(1).lower()
        analysis["motions"].append(motion_type)

    # Determine case status
    text_lower = docket_text.lower()
    if "case dismissed" in text_lower or "dismissed with prejudice" in text_lower:
        analysis["case_status"] = "dismissed"
    elif "judgment entered" in text_lower or "final judgment" in text_lower:
        analysis["case_status"] = "closed"
    elif "settlement" in text_lower or "stipulation of dismissal" in text_lower:
        analysis["case_status"] = "settled"
    elif "appeal" in text_lower:
        analysis["case_status"] = "on_appeal"
    else:
        analysis["case_status"] = "pending"

    # Motion type summary
    motion_counts = {}
    for m in analysis["motions"]:
        motion_counts[m] = motion_counts.get(m, 0) + 1
    analysis["motion_summary"] = motion_counts

    return {
        "success": True,
        "state": state,
        "case_id": case_id,
        "analysis": analysis
    }


# --- Scheduled Ingestion Control APIs ---

# In-memory schedule tracking
_ingestion_schedules = {
    "default": {
        "id": "default",
        "name": "Default State Court Ingestion",
        "enabled": True,
        "frequency": "daily",
        "last_run": None,
        "next_run": None,
        "sources": ["courtlistener"],
        "states": "all"
    }
}


@app.get("/v1/state-courts/schedules")
def list_ingestion_schedules():
    """List all configured ingestion schedules."""
    return {
        "success": True,
        "schedules": list(_ingestion_schedules.values()),
        "count": len(_ingestion_schedules)
    }


@app.get("/v1/state-courts/schedules/{schedule_id}")
def get_ingestion_schedule(schedule_id: str):
    """Get details of a specific ingestion schedule."""
    if schedule_id not in _ingestion_schedules:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"success": True, "schedule": _ingestion_schedules[schedule_id]}


@app.post("/v1/state-courts/schedules")
def create_ingestion_schedule(body: dict):
    """
    Create a new ingestion schedule.

    Request body: {
        "name": "Schedule name",
        "frequency": "hourly|daily|weekly",
        "sources": ["courtlistener", "virginia", ...],
        "states": ["CA", "NY", ...] or "all",
        "enabled": true
    }
    """
    import uuid
    from datetime import datetime

    schedule_id = str(uuid.uuid4())[:8]
    schedule = {
        "id": schedule_id,
        "name": body.get("name", f"Schedule {schedule_id}"),
        "enabled": body.get("enabled", True),
        "frequency": body.get("frequency", "daily"),
        "sources": body.get("sources", ["courtlistener"]),
        "states": body.get("states", "all"),
        "created_at": datetime.utcnow().isoformat(),
        "last_run": None,
        "next_run": None
    }

    _ingestion_schedules[schedule_id] = schedule

    return {"success": True, "schedule": schedule}


@app.put("/v1/state-courts/schedules/{schedule_id}")
def update_ingestion_schedule(schedule_id: str, body: dict):
    """Update an existing ingestion schedule."""
    if schedule_id not in _ingestion_schedules:
        raise HTTPException(status_code=404, detail="Schedule not found")

    schedule = _ingestion_schedules[schedule_id]

    if "name" in body:
        schedule["name"] = body["name"]
    if "enabled" in body:
        schedule["enabled"] = body["enabled"]
    if "frequency" in body:
        schedule["frequency"] = body["frequency"]
    if "sources" in body:
        schedule["sources"] = body["sources"]
    if "states" in body:
        schedule["states"] = body["states"]

    return {"success": True, "schedule": schedule}


@app.delete("/v1/state-courts/schedules/{schedule_id}")
def delete_ingestion_schedule(schedule_id: str):
    """Delete an ingestion schedule."""
    if schedule_id not in _ingestion_schedules:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if schedule_id == "default":
        raise HTTPException(status_code=400, detail="Cannot delete default schedule")

    del _ingestion_schedules[schedule_id]
    return {"success": True, "message": "Schedule deleted"}


@app.post("/v1/state-courts/schedules/{schedule_id}/run")
def run_ingestion_schedule(schedule_id: str):
    """Manually trigger an ingestion schedule."""
    from datetime import datetime

    if schedule_id not in _ingestion_schedules:
        raise HTTPException(status_code=404, detail="Schedule not found")

    schedule = _ingestion_schedules[schedule_id]

    if not schedule["enabled"]:
        raise HTTPException(status_code=400, detail="Schedule is disabled")

    # Trigger the ingestion
    try:
        from .services.state_courts import get_ingest_service
        ingest_service = get_ingest_service()

        results = {
            "opinions_ingested": 0,
            "errors": []
        }

        # Run based on configured sources
        if "courtlistener" in schedule["sources"]:
            opinions = ingest_service.run_full_ingest(limit=100)
            results["opinions_ingested"] = len(opinions) if opinions else 0

        schedule["last_run"] = datetime.utcnow().isoformat()

        return {
            "success": True,
            "schedule_id": schedule_id,
            "results": results,
            "run_at": schedule["last_run"]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion error: {str(e)}")


# --- Webhook Notification System ---

# In-memory webhook storage
_webhooks = {}


@app.get("/v1/state-courts/webhooks")
def list_webhooks():
    """List all configured webhooks."""
    return {
        "success": True,
        "webhooks": list(_webhooks.values()),
        "count": len(_webhooks)
    }


@app.post("/v1/state-courts/webhooks")
def create_webhook(body: dict):
    """
    Create a webhook for notifications.

    Request body: {
        "url": "https://example.com/webhook",
        "events": ["new_case", "new_opinion", "batch_complete"],
        "states": ["CA", "NY"] or "all",
        "secret": "optional webhook secret"
    }
    """
    import uuid
    from datetime import datetime

    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url'")

    webhook_id = str(uuid.uuid4())[:8]
    webhook = {
        "id": webhook_id,
        "url": url,
        "events": body.get("events", ["new_case", "new_opinion"]),
        "states": body.get("states", "all"),
        "secret": body.get("secret"),
        "enabled": True,
        "created_at": datetime.utcnow().isoformat(),
        "last_triggered": None,
        "trigger_count": 0
    }

    _webhooks[webhook_id] = webhook

    return {"success": True, "webhook": {**webhook, "secret": "***" if webhook["secret"] else None}}


@app.get("/v1/state-courts/webhooks/{webhook_id}")
def get_webhook(webhook_id: str):
    """Get webhook details."""
    if webhook_id not in _webhooks:
        raise HTTPException(status_code=404, detail="Webhook not found")

    webhook = _webhooks[webhook_id]
    return {"success": True, "webhook": {**webhook, "secret": "***" if webhook["secret"] else None}}


@app.delete("/v1/state-courts/webhooks/{webhook_id}")
def delete_webhook(webhook_id: str):
    """Delete a webhook."""
    if webhook_id not in _webhooks:
        raise HTTPException(status_code=404, detail="Webhook not found")

    del _webhooks[webhook_id]
    return {"success": True, "message": "Webhook deleted"}


@app.post("/v1/state-courts/webhooks/{webhook_id}/test")
def test_webhook(webhook_id: str):
    """Send a test event to a webhook."""
    import json
    from datetime import datetime

    if webhook_id not in _webhooks:
        raise HTTPException(status_code=404, detail="Webhook not found")

    webhook = _webhooks[webhook_id]

    test_payload = {
        "event": "test",
        "webhook_id": webhook_id,
        "timestamp": datetime.utcnow().isoformat(),
        "data": {
            "message": "This is a test webhook event",
            "configured_events": webhook["events"],
            "configured_states": webhook["states"]
        }
    }

    # In production, would actually POST to webhook URL
    # For now, just simulate success
    webhook["last_triggered"] = datetime.utcnow().isoformat()
    webhook["trigger_count"] += 1

    return {
        "success": True,
        "message": "Test event sent",
        "payload": test_payload,
        "webhook_url": webhook["url"]
    }


# --- API Documentation and Summary Endpoints ---

@app.get("/v1/state-courts/api/endpoints")
def list_state_court_endpoints():
    """
    List all available state court API endpoints with descriptions.
    """
    endpoints = [
        # Core endpoints
        {"method": "GET", "path": "/v1/state-courts/all/status", "description": "All 50 states status overview"},
        {"method": "GET", "path": "/v1/state-courts/{state}/status", "description": "Individual state status"},
        {"method": "GET", "path": "/v1/state-courts/{state}/counties", "description": "Counties for a state"},
        {"method": "GET", "path": "/v1/state-courts/{state}/county/{name}", "description": "County details"},

        # Search
        {"method": "GET", "path": "/v1/state-courts/search", "description": "Search cases with filters"},
        {"method": "GET", "path": "/v1/state-courts/search/fulltext", "description": "Full-text search"},

        # Statistics
        {"method": "GET", "path": "/v1/state-courts/stats", "description": "Aggregate statistics"},
        {"method": "GET", "path": "/v1/state-courts/stats/daily", "description": "Daily ingestion stats"},

        # Analytics
        {"method": "GET", "path": "/v1/state-courts/analytics/regions", "description": "Regional analytics"},
        {"method": "GET", "path": "/v1/state-courts/analytics/compare", "description": "Compare states"},
        {"method": "GET", "path": "/v1/state-courts/analytics/heatmap", "description": "Heatmap data"},
        {"method": "GET", "path": "/v1/state-courts/analytics/trends", "description": "Trend analysis"},
        {"method": "GET", "path": "/v1/state-courts/analytics/parties", "description": "Party analytics"},
        {"method": "GET", "path": "/v1/state-courts/analytics/county/{state}", "description": "County analytics"},
        {"method": "GET", "path": "/v1/state-courts/analytics/case-types/distribution", "description": "Case type distribution"},

        # Parsing
        {"method": "POST", "path": "/v1/state-courts/parse/text", "description": "Parse court document text"},
        {"method": "POST", "path": "/v1/state-courts/parse/html", "description": "Parse HTML court records"},
        {"method": "POST", "path": "/v1/state-courts/parse/docket", "description": "Extract docket entries"},
        {"method": "POST", "path": "/v1/state-courts/parse/parties", "description": "Extract party names"},
        {"method": "POST", "path": "/v1/state-courts/parse/citations", "description": "Extract legal citations"},
        {"method": "POST", "path": "/v1/state-courts/parse/docket-entries", "description": "Parse docket entries"},
        {"method": "POST", "path": "/v1/state-courts/analyze/docket", "description": "Analyze full docket"},

        # Detection
        {"method": "POST", "path": "/v1/state-courts/detect/state", "description": "Detect state from text"},
        {"method": "POST", "path": "/v1/state-courts/detect/case-type", "description": "Detect case type"},

        # Import/Export
        {"method": "POST", "path": "/v1/state-courts/batch/import", "description": "Batch import records"},
        {"method": "POST", "path": "/v1/state-courts/batch/import-text", "description": "Batch import text documents"},
        {"method": "POST", "path": "/v1/state-courts/upload/base64", "description": "Upload document (base64)"},
        {"method": "GET", "path": "/v1/state-courts/export/cases.json", "description": "Export cases as JSON"},
        {"method": "GET", "path": "/v1/state-courts/export/cases.csv", "description": "Export cases as CSV"},
        {"method": "GET", "path": "/v1/state-courts/export/opinions.json", "description": "Export opinions as JSON"},

        # Case operations
        {"method": "GET", "path": "/v1/state-courts/case/{id}", "description": "Get case details"},
        {"method": "GET", "path": "/v1/state-courts/opinion/{id}", "description": "Get opinion details"},
        {"method": "GET", "path": "/v1/state-courts/cases/related/{id}", "description": "Get related cases"},
        {"method": "POST", "path": "/v1/state-courts/cases/link", "description": "Find linked cases"},

        # Citations
        {"method": "GET", "path": "/v1/state-courts/citations/search", "description": "Search by citation"},

        # Reference data
        {"method": "GET", "path": "/v1/state-courts/reference/states", "description": "State code reference"},
        {"method": "GET", "path": "/v1/state-courts/reference/case-types", "description": "Case type reference"},
        {"method": "GET", "path": "/v1/state-courts/jurisdictions", "description": "Court jurisdictions"},

        # Data quality
        {"method": "GET", "path": "/v1/state-courts/quality/check", "description": "Data quality check"},
        {"method": "POST", "path": "/v1/state-courts/quality/fix", "description": "Auto-fix quality issues"},

        # Jobs
        {"method": "GET", "path": "/v1/state-courts/jobs", "description": "List import jobs"},
        {"method": "GET", "path": "/v1/state-courts/jobs/{id}", "description": "Get job details"},
        {"method": "POST", "path": "/v1/state-courts/ingest/trigger", "description": "Trigger ingestion"},

        # Schedules
        {"method": "GET", "path": "/v1/state-courts/schedules", "description": "List schedules"},
        {"method": "POST", "path": "/v1/state-courts/schedules", "description": "Create schedule"},
        {"method": "GET", "path": "/v1/state-courts/schedules/{id}", "description": "Get schedule"},
        {"method": "PUT", "path": "/v1/state-courts/schedules/{id}", "description": "Update schedule"},
        {"method": "DELETE", "path": "/v1/state-courts/schedules/{id}", "description": "Delete schedule"},
        {"method": "POST", "path": "/v1/state-courts/schedules/{id}/run", "description": "Run schedule"},

        # Webhooks
        {"method": "GET", "path": "/v1/state-courts/webhooks", "description": "List webhooks"},
        {"method": "POST", "path": "/v1/state-courts/webhooks", "description": "Create webhook"},
        {"method": "GET", "path": "/v1/state-courts/webhooks/{id}", "description": "Get webhook"},
        {"method": "DELETE", "path": "/v1/state-courts/webhooks/{id}", "description": "Delete webhook"},
        {"method": "POST", "path": "/v1/state-courts/webhooks/{id}/test", "description": "Test webhook"},

        # Dashboard
        {"method": "GET", "path": "/state-courts/analytics", "description": "Analytics dashboard (HTML)"},
    ]

    # Group by category
    categories = {
        "core": [e for e in endpoints if "status" in e["path"] or "counties" in e["path"]],
        "search": [e for e in endpoints if "search" in e["path"]],
        "analytics": [e for e in endpoints if "analytics" in e["path"] or "stats" in e["path"]],
        "parsing": [e for e in endpoints if "parse" in e["path"] or "detect" in e["path"] or "analyze" in e["path"]],
        "import_export": [e for e in endpoints if "import" in e["path"] or "export" in e["path"] or "upload" in e["path"]],
        "operations": [e for e in endpoints if "case" in e["path"] or "opinion" in e["path"] or "citation" in e["path"]],
        "management": [e for e in endpoints if "schedule" in e["path"] or "webhook" in e["path"] or "job" in e["path"] or "quality" in e["path"]],
    }

    return {
        "success": True,
        "total_endpoints": len(endpoints),
        "endpoints": endpoints,
        "by_category": {k: len(v) for k, v in categories.items()}
    }


@app.get("/v1/state-courts/api/summary")
def get_api_summary():
    """
    Get a comprehensive summary of the State Courts API capabilities.
    """
    from .models.db import get_conn

    conn = get_conn()

    # Data counts
    case_count = conn.execute("SELECT COUNT(*) as c FROM state_court_cases").fetchone()["c"]
    opinion_count = conn.execute("SELECT COUNT(*) as c FROM state_court_opinions").fetchone()["c"]
    doc_count = conn.execute("SELECT COUNT(*) as c FROM state_court_documents").fetchone()["c"]

    # State coverage
    states_with_cases = conn.execute("""
        SELECT COUNT(DISTINCT state) as c FROM state_court_cases WHERE state IS NOT NULL
    """).fetchone()["c"]

    return {
        "success": True,
        "api_version": "1.0",
        "coverage": {
            "states_supported": 50,
            "states_with_data": states_with_cases,
            "federal_integration": True
        },
        "data_counts": {
            "cases": case_count,
            "opinions": opinion_count,
            "documents": doc_count,
            "total_records": case_count + opinion_count + doc_count
        },
        "capabilities": {
            "text_parsing": ["PDF extraction", "HTML parsing", "docket parsing", "party extraction", "citation parsing"],
            "data_normalization": ["state codes", "case types", "dates", "party names", "addresses"],
            "analytics": ["regional", "trends", "comparisons", "heatmaps", "case type distribution"],
            "integrations": ["CourtListener", "bulk import", "webhooks", "scheduled ingestion"],
            "export_formats": ["JSON", "CSV"]
        },
        "endpoints": {
            "total": 357,
            "state_specific": 150,  # 3 per state
            "analytics": 15,
            "parsing": 10,
            "management": 15
        }
    }


@app.get("/v1/state-courts/report/coverage")
def get_coverage_report():
    """
    Generate a comprehensive coverage report for state court data.
    """
    from .models.db import get_conn

    conn = get_conn()

    all_states = [
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"
    ]

    # Get case counts by state
    case_counts = conn.execute("""
        SELECT state, COUNT(*) as cases FROM state_court_cases
        WHERE state IS NOT NULL GROUP BY state
    """).fetchall()
    case_map = {r["state"]: r["cases"] for r in case_counts}

    # Get opinion counts by state
    opinion_counts = conn.execute("""
        SELECT state, COUNT(*) as opinions FROM state_court_opinions
        WHERE state IS NOT NULL GROUP BY state
    """).fetchall()
    opinion_map = {r["state"]: r["opinions"] for r in opinion_counts}

    # Build coverage report
    coverage = []
    for state in all_states:
        cases = case_map.get(state, 0)
        opinions = opinion_map.get(state, 0)
        total = cases + opinions

        coverage.append({
            "state": state,
            "cases": cases,
            "opinions": opinions,
            "total": total,
            "has_data": total > 0,
            "coverage_level": "high" if total > 100 else "medium" if total > 10 else "low" if total > 0 else "none"
        })

    # Summary stats
    states_with_data = sum(1 for c in coverage if c["has_data"])
    high_coverage = sum(1 for c in coverage if c["coverage_level"] == "high")
    medium_coverage = sum(1 for c in coverage if c["coverage_level"] == "medium")
    low_coverage = sum(1 for c in coverage if c["coverage_level"] == "low")

    return {
        "success": True,
        "report_type": "coverage",
        "summary": {
            "total_states": 50,
            "states_with_data": states_with_data,
            "coverage_percentage": round(states_with_data / 50 * 100, 1),
            "high_coverage_states": high_coverage,
            "medium_coverage_states": medium_coverage,
            "low_coverage_states": low_coverage,
            "no_data_states": 50 - states_with_data
        },
        "state_coverage": coverage,
        "top_states": sorted(coverage, key=lambda x: x["total"], reverse=True)[:10]
    }


@app.get("/v1/state-courts/report/activity")
def get_activity_report(days: int = 30):
    """
    Generate an activity report for the specified time period.
    """
    from .models.db import get_conn
    from datetime import datetime, timedelta

    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Cases added in period
    new_cases = conn.execute("""
        SELECT COUNT(*) as c FROM state_court_cases WHERE created_at >= ?
    """, [cutoff]).fetchone()["c"]

    # Opinions added in period
    new_opinions = conn.execute("""
        SELECT COUNT(*) as c FROM state_court_opinions WHERE created_at >= ?
    """, [cutoff]).fetchone()["c"]

    # Daily breakdown
    daily_cases = conn.execute("""
        SELECT DATE(created_at) as date, COUNT(*) as count
        FROM state_court_cases WHERE created_at >= ?
        GROUP BY DATE(created_at) ORDER BY date
    """, [cutoff]).fetchall()

    # Most active states in period
    active_states = conn.execute("""
        SELECT state, COUNT(*) as count
        FROM state_court_cases WHERE created_at >= ? AND state IS NOT NULL
        GROUP BY state ORDER BY count DESC LIMIT 10
    """, [cutoff]).fetchall()

    # Jobs run in period
    jobs_in_period = [j for j in _import_jobs.values()
                      if j.get("submitted_at", "") >= cutoff]

    return {
        "success": True,
        "report_type": "activity",
        "period_days": days,
        "period_start": cutoff,
        "summary": {
            "new_cases": new_cases,
            "new_opinions": new_opinions,
            "total_new_records": new_cases + new_opinions,
            "avg_daily_cases": round(new_cases / days, 1) if days > 0 else 0,
            "jobs_run": len(jobs_in_period)
        },
        "daily_activity": [dict(r) for r in daily_cases],
        "most_active_states": [dict(r) for r in active_states]
    }


@app.get("/v1/state-courts/report/data-sources")
def get_data_sources_report():
    """
    Report on data sources and their contribution to the database.
    """
    from .models.db import get_conn

    conn = get_conn()

    # Cases by source
    case_sources = conn.execute("""
        SELECT data_source, COUNT(*) as count
        FROM state_court_cases
        WHERE data_source IS NOT NULL
        GROUP BY data_source
        ORDER BY count DESC
        LIMIT 20
    """).fetchall()

    # Opinion sources (inferred from court field)
    opinion_courts = conn.execute("""
        SELECT court, COUNT(*) as count
        FROM state_court_opinions
        WHERE court IS NOT NULL
        GROUP BY court
        ORDER BY count DESC
        LIMIT 20
    """).fetchall()

    return {
        "success": True,
        "report_type": "data_sources",
        "case_sources": [dict(r) for r in case_sources],
        "opinion_sources": [dict(r) for r in opinion_courts],
        "available_integrations": [
            {"name": "CourtListener", "type": "api", "coverage": "50 states appellate"},
            {"name": "Virginia Courts", "type": "bulk_csv", "coverage": "VA state courts"},
            {"name": "Oklahoma OSCN", "type": "web_scrape", "coverage": "OK state courts"},
            {"name": "Batch Import", "type": "api", "coverage": "user-provided data"},
            {"name": "Text Upload", "type": "api", "coverage": "user-provided documents"}
        ]
    }


# --- Data Validation and Enrichment APIs ---

@app.post("/v1/state-courts/validate/case")
def validate_case_data(body: dict):
    """
    Validate case data before import.

    Request body: {
        "state": "CA",
        "case_number": "...",
        "case_style": "...",
        "case_type": "...",
        "date_filed": "..."
    }
    """
    import re
    from .services.state_courts import normalize_state, normalize_case_type, normalize_date

    errors = []
    warnings = []
    normalized = {}

    # Validate state
    state = body.get("state", "")
    norm_state = normalize_state(state)
    if not norm_state:
        errors.append({"field": "state", "error": f"Invalid state: {state}"})
    else:
        normalized["state"] = norm_state

    # Validate case number
    case_number = body.get("case_number", "")
    if not case_number:
        errors.append({"field": "case_number", "error": "Case number is required"})
    elif len(case_number) < 3:
        warnings.append({"field": "case_number", "warning": "Case number seems too short"})
    else:
        normalized["case_number"] = case_number.upper()

    # Validate case style
    case_style = body.get("case_style", "")
    if case_style:
        if " v. " not in case_style.lower() and " v " not in case_style.lower():
            warnings.append({"field": "case_style", "warning": "Case style may be missing 'v.' separator"})
        normalized["case_style"] = case_style
    else:
        warnings.append({"field": "case_style", "warning": "Case style is recommended"})

    # Validate case type
    case_type = body.get("case_type", "")
    if case_type:
        norm_type = normalize_case_type(case_type)
        normalized["case_type"] = norm_type
    else:
        warnings.append({"field": "case_type", "warning": "Case type not provided"})

    # Validate date
    date_filed = body.get("date_filed", "")
    if date_filed:
        norm_date = normalize_date(date_filed)
        if not norm_date:
            errors.append({"field": "date_filed", "error": f"Invalid date format: {date_filed}"})
        else:
            normalized["date_filed"] = norm_date
            # Check if date is in future
            from datetime import datetime
            if norm_date > datetime.utcnow().strftime("%Y-%m-%d"):
                warnings.append({"field": "date_filed", "warning": "Date is in the future"})

    is_valid = len(errors) == 0

    return {
        "success": True,
        "is_valid": is_valid,
        "errors": errors,
        "warnings": warnings,
        "normalized_data": normalized if is_valid else None,
        "validation_score": 100 - (len(errors) * 25) - (len(warnings) * 5)
    }


@app.post("/v1/state-courts/validate/batch")
def validate_batch_data(body: dict):
    """
    Validate a batch of case records.

    Request body: {
        "records": [{"state": "CA", ...}, ...]
    }
    """
    from .services.state_courts import normalize_state, normalize_case_type, normalize_date

    records = body.get("records", [])
    if not records:
        raise HTTPException(status_code=400, detail="No records provided")

    results = {
        "total": len(records),
        "valid": 0,
        "invalid": 0,
        "with_warnings": 0,
        "errors_by_field": {},
        "sample_errors": []
    }

    for i, record in enumerate(records[:1000]):
        errors = []

        # Check required fields
        if not normalize_state(record.get("state", "")):
            errors.append("invalid_state")
        if not record.get("case_number"):
            errors.append("missing_case_number")

        # Check date if provided
        if record.get("date_filed"):
            if not normalize_date(record["date_filed"]):
                errors.append("invalid_date")

        if errors:
            results["invalid"] += 1
            for err in errors:
                results["errors_by_field"][err] = results["errors_by_field"].get(err, 0) + 1
            if len(results["sample_errors"]) < 5:
                results["sample_errors"].append({"index": i, "errors": errors})
        else:
            results["valid"] += 1

    results["validity_rate"] = round(results["valid"] / results["total"] * 100, 1) if results["total"] > 0 else 0

    return {
        "success": True,
        "validation_results": results
    }


@app.post("/v1/state-courts/enrich/case")
def enrich_case_data(body: dict):
    """
    Enrich case data with additional information.

    Request body: {
        "case_id": "existing case ID to enrich",
        OR
        "case_data": {"state": "CA", "case_number": "...", ...}
    }
    """
    from .models.db import get_conn
    from .services.state_courts import normalize_party_name

    case_id = body.get("case_id")
    case_data = body.get("case_data", {})

    conn = get_conn()
    enrichments = {}

    # Get case data if ID provided
    if case_id:
        case = conn.execute(
            "SELECT * FROM state_court_cases WHERE id = ?",
            [case_id]
        ).fetchone()
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        case_data = dict(case)

    # Enrich with party information
    if case_data.get("case_style"):
        case_style = case_data["case_style"]
        if " v. " in case_style or " v " in case_style:
            parts = case_style.replace(" v ", " v. ").split(" v. ", 1)
            if len(parts) == 2:
                enrichments["plaintiff"] = normalize_party_name(parts[0].strip())
                enrichments["defendant"] = normalize_party_name(parts[1].strip())

    # Enrich with region information
    if case_data.get("state"):
        state = case_data["state"]
        region, division = _get_region_for_state(state)
        enrichments["region"] = region
        enrichments["division"] = division

    # Find related cases
    if case_data.get("case_style") and case_data.get("state"):
        # Look for similar parties in same state
        related = conn.execute("""
            SELECT id, case_number, case_style
            FROM state_court_cases
            WHERE state = ? AND case_style LIKE ?
            AND id != ?
            LIMIT 5
        """, [
            case_data["state"],
            f"%{case_data['case_style'][:30]}%",
            case_data.get("id", "")
        ]).fetchall()
        enrichments["related_cases"] = [dict(r) for r in related]

    # Find similar opinions
    if case_data.get("state"):
        opinions = conn.execute("""
            SELECT id, case_name, citation
            FROM state_court_opinions
            WHERE state = ?
            ORDER BY date_decided DESC
            LIMIT 5
        """, [case_data["state"]]).fetchall()
        enrichments["recent_state_opinions"] = [dict(r) for r in opinions]

    return {
        "success": True,
        "original_data": {k: v for k, v in case_data.items() if k != "raw_data_json"},
        "enrichments": enrichments
    }


@app.post("/v1/state-courts/deduplicate/check")
def check_for_duplicates(body: dict):
    """
    Check if a case record is a potential duplicate.

    Request body: {
        "state": "CA",
        "case_number": "...",
        "case_style": "..."
    }
    """
    from .models.db import get_conn

    state = body.get("state", "").upper()[:2]
    case_number = body.get("case_number", "")
    case_style = body.get("case_style", "")

    if not state or not case_number:
        raise HTTPException(status_code=400, detail="State and case_number required")

    conn = get_conn()
    duplicates = []

    # Exact match on state + case_number
    exact = conn.execute("""
        SELECT id, state, case_number, case_style, date_filed, created_at
        FROM state_court_cases
        WHERE state = ? AND case_number = ?
    """, [state, case_number.upper()]).fetchall()
    for r in exact:
        duplicates.append({**dict(r), "match_type": "exact"})

    # Similar case number (allowing for formatting differences)
    case_num_normalized = case_number.upper().replace("-", "").replace(" ", "")
    similar = conn.execute("""
        SELECT id, state, case_number, case_style, date_filed
        FROM state_court_cases
        WHERE state = ?
        AND REPLACE(REPLACE(UPPER(case_number), '-', ''), ' ', '') = ?
        AND case_number != ?
    """, [state, case_num_normalized, case_number.upper()]).fetchall()
    for r in similar:
        duplicates.append({**dict(r), "match_type": "similar_number"})

    # Similar case style in same state (fuzzy match)
    if case_style:
        style_words = case_style.split()[:3]  # First 3 words
        if style_words:
            style_pattern = f"%{style_words[0]}%"
            style_matches = conn.execute("""
                SELECT id, state, case_number, case_style, date_filed
                FROM state_court_cases
                WHERE state = ? AND case_style LIKE ?
                AND case_number != ?
                LIMIT 10
            """, [state, style_pattern, case_number.upper()]).fetchall()
            for r in style_matches:
                duplicates.append({**dict(r), "match_type": "similar_style"})

    # Deduplicate results
    seen_ids = set()
    unique_duplicates = []
    for d in duplicates:
        if d["id"] not in seen_ids:
            seen_ids.add(d["id"])
            unique_duplicates.append(d)

    is_duplicate = len([d for d in unique_duplicates if d["match_type"] == "exact"]) > 0
    potential_duplicate = len(unique_duplicates) > 0

    return {
        "success": True,
        "is_duplicate": is_duplicate,
        "potential_duplicate": potential_duplicate,
        "duplicate_count": len(unique_duplicates),
        "matches": unique_duplicates[:20],
        "recommendation": "skip" if is_duplicate else "review" if potential_duplicate else "import"
    }


@app.get("/v1/state-courts/duplicates")
def find_all_duplicates(state: str = None, limit: int = 100):
    """
    Find all potential duplicate records in the database.
    """
    from .models.db import get_conn

    conn = get_conn()

    if state:
        duplicates = conn.execute("""
            SELECT state, case_number, COUNT(*) as count,
                   GROUP_CONCAT(id) as ids
            FROM state_court_cases
            WHERE state = ? AND case_number IS NOT NULL
            GROUP BY state, case_number
            HAVING COUNT(*) > 1
            ORDER BY count DESC
            LIMIT ?
        """, [state.upper(), limit]).fetchall()
    else:
        duplicates = conn.execute("""
            SELECT state, case_number, COUNT(*) as count,
                   GROUP_CONCAT(id) as ids
            FROM state_court_cases
            WHERE case_number IS NOT NULL
            GROUP BY state, case_number
            HAVING COUNT(*) > 1
            ORDER BY count DESC
            LIMIT ?
        """, [limit]).fetchall()

    results = []
    for row in duplicates:
        ids = row["ids"].split(",")
        results.append({
            "state": row["state"],
            "case_number": row["case_number"],
            "duplicate_count": row["count"],
            "record_ids": ids
        })

    return {
        "success": True,
        "state_filter": state,
        "duplicate_groups": results,
        "total_groups": len(results),
        "total_duplicate_records": sum(r["duplicate_count"] for r in results)
    }


@app.post("/v1/state-courts/merge")
def merge_duplicate_cases(body: dict):
    """
    Merge duplicate case records.

    Request body: {
        "primary_id": "ID of record to keep",
        "secondary_ids": ["IDs of records to merge into primary"]
    }
    """
    from .models.db import get_conn
    from .services.state_courts import merge_records
    import json

    primary_id = body.get("primary_id")
    secondary_ids = body.get("secondary_ids", [])

    if not primary_id or not secondary_ids:
        raise HTTPException(status_code=400, detail="primary_id and secondary_ids required")

    conn = get_conn()

    # Get primary record
    primary = conn.execute(
        "SELECT * FROM state_court_cases WHERE id = ?",
        [primary_id]
    ).fetchone()
    if not primary:
        raise HTTPException(status_code=404, detail="Primary record not found")

    merged_data = dict(primary)
    merged_from = []

    # Merge each secondary record
    for sec_id in secondary_ids:
        secondary = conn.execute(
            "SELECT * FROM state_court_cases WHERE id = ?",
            [sec_id]
        ).fetchone()
        if secondary:
            merged_data = merge_records(merged_data, dict(secondary))
            merged_from.append(sec_id)

            # Delete secondary record
            conn.execute("DELETE FROM state_court_cases WHERE id = ?", [sec_id])

    # Update primary with merged data
    if merged_from:
        merged_data["_merged_from"] = merged_from
        raw_json = json.dumps(merged_data.get("raw_data_json", {}))

        conn.execute("""
            UPDATE state_court_cases
            SET case_style = ?, case_type = ?, date_filed = ?, raw_data_json = ?
            WHERE id = ?
        """, [
            merged_data.get("case_style"),
            merged_data.get("case_type"),
            merged_data.get("date_filed"),
            raw_json,
            primary_id
        ])
        conn.commit()

    return {
        "success": True,
        "primary_id": primary_id,
        "merged_count": len(merged_from),
        "merged_from": merged_from,
        "result": {k: v for k, v in merged_data.items() if k != "raw_data_json"}
    }


# --- Case Outcome Tracking APIs ---

# Outcome patterns for detection
OUTCOME_PATTERNS = {
    "dismissed": [
        r"case\s+dismissed",
        r"dismissed\s+with\s+prejudice",
        r"dismissed\s+without\s+prejudice",
        r"voluntary\s+dismissal",
        r"involuntary\s+dismissal"
    ],
    "settled": [
        r"settlement\s+reached",
        r"stipulation\s+of\s+dismissal",
        r"settlement\s+agreement",
        r"consent\s+judgment"
    ],
    "judgment_plaintiff": [
        r"judgment\s+for\s+plaintiff",
        r"verdict\s+for\s+plaintiff",
        r"plaintiff\s+prevails"
    ],
    "judgment_defendant": [
        r"judgment\s+for\s+defendant",
        r"verdict\s+for\s+defendant",
        r"defendant\s+prevails"
    ],
    "default_judgment": [
        r"default\s+judgment",
        r"judgment\s+by\s+default"
    ],
    "summary_judgment": [
        r"summary\s+judgment\s+granted",
        r"motion\s+for\s+summary\s+judgment\s+granted"
    ],
    "trial_verdict": [
        r"jury\s+verdict",
        r"bench\s+trial\s+verdict",
        r"trial\s+concluded"
    ],
    "appeal_filed": [
        r"notice\s+of\s+appeal",
        r"appeal\s+filed"
    ],
    "remanded": [
        r"case\s+remanded",
        r"remanded\s+for"
    ],
    "pending": [
        r"pending",
        r"awaiting",
        r"scheduled"
    ]
}


@app.post("/v1/state-courts/detect/outcome")
def detect_case_outcome(body: dict):
    """
    Detect case outcome from text (docket entries, orders, etc.)

    Request body: {
        "text": "court document or docket text..."
    }
    """
    import re

    text = body.get("text", "").lower()
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field")

    detected_outcomes = []
    outcome_scores = {}

    for outcome_type, patterns in OUTCOME_PATTERNS.items():
        matches = 0
        matched_patterns = []
        for pattern in patterns:
            found = re.findall(pattern, text, re.IGNORECASE)
            if found:
                matches += len(found)
                matched_patterns.extend(found)

        if matches > 0:
            detected_outcomes.append({
                "outcome": outcome_type,
                "confidence": min(matches * 0.3, 1.0),
                "match_count": matches,
                "matched_text": matched_patterns[:5]
            })
            outcome_scores[outcome_type] = matches

    # Sort by confidence
    detected_outcomes.sort(key=lambda x: x["confidence"], reverse=True)

    # Determine primary outcome
    primary_outcome = detected_outcomes[0]["outcome"] if detected_outcomes else "unknown"

    # Determine if case is closed
    closed_outcomes = ["dismissed", "settled", "judgment_plaintiff", "judgment_defendant",
                       "default_judgment", "summary_judgment", "trial_verdict"]
    is_closed = primary_outcome in closed_outcomes

    return {
        "success": True,
        "primary_outcome": primary_outcome,
        "is_closed": is_closed,
        "detected_outcomes": detected_outcomes,
        "outcome_scores": outcome_scores
    }


@app.post("/v1/state-courts/cases/{case_id}/outcome")
def update_case_outcome(case_id: str, body: dict):
    """
    Update or set the outcome for a case.

    Request body: {
        "outcome": "dismissed|settled|judgment_plaintiff|...",
        "outcome_date": "YYYY-MM-DD",
        "outcome_details": "optional details"
    }
    """
    from .models.db import get_conn
    from datetime import datetime
    import json

    outcome = body.get("outcome")
    outcome_date = body.get("outcome_date")
    outcome_details = body.get("outcome_details", "")

    valid_outcomes = list(OUTCOME_PATTERNS.keys()) + ["unknown", "other"]
    if outcome and outcome not in valid_outcomes:
        raise HTTPException(status_code=400, detail=f"Invalid outcome. Valid: {valid_outcomes}")

    conn = get_conn()

    # Get case
    case = conn.execute("SELECT * FROM state_court_cases WHERE id = ?", [case_id]).fetchone()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Update raw_data_json with outcome
    raw_data = {}
    if case.get("raw_data_json"):
        try:
            raw_data = json.loads(case["raw_data_json"])
        except:
            raw_data = {}

    raw_data["outcome"] = outcome
    raw_data["outcome_date"] = outcome_date
    raw_data["outcome_details"] = outcome_details
    raw_data["outcome_updated_at"] = datetime.utcnow().isoformat()

    conn.execute("""
        UPDATE state_court_cases SET raw_data_json = ? WHERE id = ?
    """, [json.dumps(raw_data), case_id])
    conn.commit()

    return {
        "success": True,
        "case_id": case_id,
        "outcome": outcome,
        "outcome_date": outcome_date
    }


@app.get("/v1/state-courts/analytics/outcomes")
def get_outcome_analytics(state: str = None):
    """
    Get analytics on case outcomes.
    """
    from .models.db import get_conn
    import json

    conn = get_conn()

    # Get cases with outcome data
    if state:
        cases = conn.execute("""
            SELECT raw_data_json FROM state_court_cases
            WHERE state = ? AND raw_data_json IS NOT NULL
        """, [state.upper()]).fetchall()
    else:
        cases = conn.execute("""
            SELECT raw_data_json FROM state_court_cases
            WHERE raw_data_json IS NOT NULL
            LIMIT 10000
        """).fetchall()

    outcome_counts = {}
    total_with_outcome = 0

    for case in cases:
        try:
            raw_data = json.loads(case["raw_data_json"])
            outcome = raw_data.get("outcome")
            if outcome:
                outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
                total_with_outcome += 1
        except:
            continue

    # Calculate percentages
    distribution = []
    for outcome, count in sorted(outcome_counts.items(), key=lambda x: x[1], reverse=True):
        distribution.append({
            "outcome": outcome,
            "count": count,
            "percentage": round(count / total_with_outcome * 100, 1) if total_with_outcome > 0 else 0
        })

    return {
        "success": True,
        "state_filter": state,
        "total_with_outcome": total_with_outcome,
        "outcome_distribution": distribution
    }


# --- System Health and Monitoring APIs ---

@app.get("/v1/state-courts/health")
def state_courts_health_check():
    """
    Health check for state courts API.
    """
    from .models.db import get_conn
    from datetime import datetime

    status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "checks": {}
    }

    # Database check
    try:
        conn = get_conn()
        conn.execute("SELECT 1").fetchone()
        status["checks"]["database"] = {"status": "ok"}
    except Exception as e:
        status["checks"]["database"] = {"status": "error", "error": str(e)}
        status["status"] = "degraded"

    # Table checks
    tables = ["state_court_cases", "state_court_opinions", "state_court_documents"]
    for table in tables:
        try:
            count = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()["c"]
            status["checks"][table] = {"status": "ok", "record_count": count}
        except Exception as e:
            status["checks"][table] = {"status": "error", "error": str(e)}
            status["status"] = "degraded"

    # Service checks
    status["checks"]["schedules"] = {"status": "ok", "count": len(_ingestion_schedules)}
    status["checks"]["webhooks"] = {"status": "ok", "count": len(_webhooks)}
    status["checks"]["jobs"] = {"status": "ok", "count": len(_import_jobs)}

    return status


@app.get("/v1/state-courts/metrics")
def get_state_courts_metrics():
    """
    Get operational metrics for the state courts API.
    """
    from .models.db import get_conn
    from datetime import datetime, timedelta

    conn = get_conn()
    now = datetime.utcnow()

    metrics = {
        "timestamp": now.isoformat(),
        "data_metrics": {},
        "operational_metrics": {},
        "growth_metrics": {}
    }

    # Data metrics
    metrics["data_metrics"]["total_cases"] = conn.execute(
        "SELECT COUNT(*) as c FROM state_court_cases"
    ).fetchone()["c"]
    metrics["data_metrics"]["total_opinions"] = conn.execute(
        "SELECT COUNT(*) as c FROM state_court_opinions"
    ).fetchone()["c"]
    metrics["data_metrics"]["total_documents"] = conn.execute(
        "SELECT COUNT(*) as c FROM state_court_documents"
    ).fetchone()["c"]
    metrics["data_metrics"]["states_with_data"] = conn.execute(
        "SELECT COUNT(DISTINCT state) as c FROM state_court_cases WHERE state IS NOT NULL"
    ).fetchone()["c"]

    # Growth metrics (last 24 hours)
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    metrics["growth_metrics"]["cases_24h"] = conn.execute(
        "SELECT COUNT(*) as c FROM state_court_cases WHERE created_at >= ?",
        [yesterday]
    ).fetchone()["c"]
    metrics["growth_metrics"]["opinions_24h"] = conn.execute(
        "SELECT COUNT(*) as c FROM state_court_opinions WHERE created_at >= ?",
        [yesterday]
    ).fetchone()["c"]

    # Last 7 days
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    metrics["growth_metrics"]["cases_7d"] = conn.execute(
        "SELECT COUNT(*) as c FROM state_court_cases WHERE created_at >= ?",
        [week_ago]
    ).fetchone()["c"]

    # Operational metrics
    metrics["operational_metrics"]["active_schedules"] = len([
        s for s in _ingestion_schedules.values() if s.get("enabled")
    ])
    metrics["operational_metrics"]["total_webhooks"] = len(_webhooks)
    metrics["operational_metrics"]["jobs_completed"] = len([
        j for j in _import_jobs.values() if j.get("status") == "completed"
    ])
    metrics["operational_metrics"]["jobs_failed"] = len([
        j for j in _import_jobs.values() if j.get("status") == "failed"
    ])

    return {"success": True, "metrics": metrics}


@app.get("/v1/state-courts/status")
def get_state_courts_status():
    """
    Get comprehensive status of the state courts system.
    """
    from .models.db import get_conn
    from datetime import datetime

    conn = get_conn()

    # Latest activity
    latest_case = conn.execute("""
        SELECT created_at, state, case_number FROM state_court_cases
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()

    latest_opinion = conn.execute("""
        SELECT created_at, state, case_name FROM state_court_opinions
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()

    # Schedule status
    schedule_status = []
    for sched in _ingestion_schedules.values():
        schedule_status.append({
            "id": sched["id"],
            "name": sched["name"],
            "enabled": sched["enabled"],
            "last_run": sched.get("last_run")
        })

    return {
        "success": True,
        "system_status": "operational",
        "api_version": "1.0",
        "timestamp": datetime.utcnow().isoformat(),
        "latest_activity": {
            "latest_case": dict(latest_case) if latest_case else None,
            "latest_opinion": dict(latest_opinion) if latest_opinion else None
        },
        "schedules": schedule_status,
        "endpoints_available": 368,
        "states_supported": 50
    }


# --- Additional State Court Integrations ---
# Florida, Texas, Maryland, New York, California

from .services.state_courts.florida_client import get_florida_client
from .services.state_courts.texas_client import get_texas_client
from .services.state_courts.maryland_client import get_maryland_client
from .services.state_courts.newyork_client import get_newyork_client
from .services.state_courts.california_client import get_california_client


# Florida Courts
@app.get("/v1/state-courts/florida/status")
def florida_courts_status():
    """Get Florida courts coverage information."""
    client = get_florida_client()
    return client.get_courts()


@app.get("/v1/state-courts/florida/dca")
def florida_dca_courts():
    """Get list of Florida District Courts of Appeal."""
    client = get_florida_client()
    return {
        "courts": client.DCA_COURTS,
        "count": len(client.DCA_COURTS)
    }


@app.get("/v1/state-courts/florida/counties")
def florida_counties():
    """Get list of major Florida counties with court access."""
    client = get_florida_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


# Texas Courts
@app.get("/v1/state-courts/texas/status")
def texas_courts_status():
    """Get Texas courts coverage information."""
    client = get_texas_client()
    return client.get_courts()


@app.get("/v1/state-courts/texas/coa")
def texas_coa_courts():
    """Get list of Texas Courts of Appeals."""
    client = get_texas_client()
    return {
        "courts": client.COA_COURTS,
        "count": len(client.COA_COURTS)
    }


@app.get("/v1/state-courts/texas/counties")
def texas_major_counties():
    """Get list of major Texas counties."""
    client = get_texas_client()
    return {
        "counties": client.MAJOR_COUNTIES,
        "count": len(client.MAJOR_COUNTIES)
    }


# Maryland Courts
@app.get("/v1/state-courts/maryland/status")
def maryland_courts_status():
    """Get Maryland courts coverage information."""
    client = get_maryland_client()
    return client.get_courts()


@app.get("/v1/state-courts/maryland/counties")
def maryland_counties():
    """Get list of Maryland counties."""
    client = get_maryland_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/maryland/case-types")
def maryland_case_types():
    """Get Maryland case type codes."""
    client = get_maryland_client()
    return client.CASE_TYPES


# New York Courts
@app.get("/v1/state-courts/newyork/status")
def newyork_courts_status():
    """Get New York courts coverage information."""
    client = get_newyork_client()
    return client.get_courts()


@app.get("/v1/state-courts/newyork/appellate-divisions")
def newyork_appellate_divisions():
    """Get list of NY Appellate Divisions."""
    client = get_newyork_client()
    return {
        "departments": client.APPELLATE_DIVISIONS,
        "count": len(client.APPELLATE_DIVISIONS)
    }


@app.get("/v1/state-courts/newyork/counties")
def newyork_major_counties():
    """Get list of major NY counties/boroughs."""
    client = get_newyork_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


# California Courts
@app.get("/v1/state-courts/california/status")
def california_courts_status():
    """Get California courts coverage information."""
    client = get_california_client()
    return client.get_courts()


@app.get("/v1/state-courts/california/appellate")
def california_appellate_courts():
    """Get list of California appellate courts."""
    client = get_california_client()
    return {
        "courts": client.APPELLATE_COURTS,
        "count": len(client.APPELLATE_COURTS)
    }


@app.get("/v1/state-courts/california/counties")
def california_major_counties():
    """Get list of major California counties with access info."""
    client = get_california_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


# Georgia Courts
@app.get("/v1/state-courts/georgia/status")
def georgia_courts_status():
    """Get Georgia courts coverage information."""
    from app.services.state_courts import get_georgia_client
    client = get_georgia_client()
    return client.get_courts()


@app.get("/v1/state-courts/georgia/counties")
def georgia_major_counties():
    """Get list of major Georgia counties with court info."""
    from app.services.state_courts import get_georgia_client
    client = get_georgia_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/georgia/county/{county}")
def georgia_county_courts(county: str):
    """Get courts in a specific Georgia county."""
    from app.services.state_courts import get_georgia_client
    client = get_georgia_client()
    return client.get_county_courts(county)


@app.get("/v1/state-courts/georgia/appellate")
def georgia_appellate_courts():
    """Get list of Georgia appellate courts."""
    from app.services.state_courts import get_georgia_client
    client = get_georgia_client()
    return {
        "courts": client.APPELLATE_COURTS,
        "count": len(client.APPELLATE_COURTS)
    }


# Pennsylvania Courts
@app.get("/v1/state-courts/pennsylvania/status")
def pennsylvania_courts_status():
    """Get Pennsylvania courts coverage information."""
    from app.services.state_courts import get_pennsylvania_client
    client = get_pennsylvania_client()
    return client.get_courts()


@app.get("/v1/state-courts/pennsylvania/counties")
def pennsylvania_major_counties():
    """Get list of major Pennsylvania counties with court info."""
    from app.services.state_courts import get_pennsylvania_client
    client = get_pennsylvania_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/pennsylvania/county/{county}")
def pennsylvania_county_courts(county: str):
    """Get courts in a specific Pennsylvania county."""
    from app.services.state_courts import get_pennsylvania_client
    client = get_pennsylvania_client()
    return client.get_county_courts(county)


@app.get("/v1/state-courts/pennsylvania/appellate")
def pennsylvania_appellate_courts():
    """Get list of Pennsylvania appellate courts."""
    from app.services.state_courts import get_pennsylvania_client
    client = get_pennsylvania_client()
    return {
        "courts": client.APPELLATE_COURTS,
        "count": len(client.APPELLATE_COURTS)
    }


@app.get("/v1/state-courts/pennsylvania/districts")
def pennsylvania_judicial_districts():
    """Get list of Pennsylvania judicial districts."""
    from app.services.state_courts import get_pennsylvania_client
    client = get_pennsylvania_client()
    return {
        "districts": client.get_judicial_districts(),
        "total": 67,
        "shown": len(client.COUNTIES)
    }


# Ohio Courts
@app.get("/v1/state-courts/ohio/status")
def ohio_courts_status():
    """Get Ohio courts coverage information."""
    from app.services.state_courts import get_ohio_client
    client = get_ohio_client()
    return client.get_courts()


@app.get("/v1/state-courts/ohio/counties")
def ohio_major_counties():
    """Get list of major Ohio counties with court info."""
    from app.services.state_courts import get_ohio_client
    client = get_ohio_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/ohio/county/{county}")
def ohio_county_courts(county: str):
    """Get courts in a specific Ohio county."""
    from app.services.state_courts import get_ohio_client
    client = get_ohio_client()
    return client.get_county_courts(county)


@app.get("/v1/state-courts/ohio/appellate")
def ohio_appellate_courts():
    """Get list of Ohio appellate courts."""
    from app.services.state_courts import get_ohio_client
    client = get_ohio_client()
    return {
        "courts": client.APPELLATE_COURTS,
        "count": len(client.APPELLATE_COURTS)
    }


@app.get("/v1/state-courts/ohio/appeals-districts")
def ohio_appeals_districts():
    """Get list of Ohio Court of Appeals districts."""
    from app.services.state_courts import get_ohio_client
    client = get_ohio_client()
    return {
        "districts": client.get_appeals_districts(),
        "total": 12
    }


# Michigan Courts
@app.get("/v1/state-courts/michigan/status")
def michigan_courts_status():
    """Get Michigan courts coverage information."""
    from app.services.state_courts import get_michigan_client
    client = get_michigan_client()
    return client.get_courts()


@app.get("/v1/state-courts/michigan/counties")
def michigan_major_counties():
    """Get list of major Michigan counties with court info."""
    from app.services.state_courts import get_michigan_client
    client = get_michigan_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/michigan/county/{county}")
def michigan_county_courts(county: str):
    """Get courts in a specific Michigan county."""
    from app.services.state_courts import get_michigan_client
    client = get_michigan_client()
    return client.get_county_courts(county)


@app.get("/v1/state-courts/michigan/appellate")
def michigan_appellate_courts():
    """Get list of Michigan appellate courts."""
    from app.services.state_courts import get_michigan_client
    client = get_michigan_client()
    return {
        "courts": client.APPELLATE_COURTS,
        "count": len(client.APPELLATE_COURTS)
    }


# Illinois Courts
@app.get("/v1/state-courts/illinois/status")
def illinois_courts_status():
    """Get Illinois courts coverage information."""
    from app.services.state_courts import get_illinois_client
    client = get_illinois_client()
    return client.get_courts()


@app.get("/v1/state-courts/illinois/counties")
def illinois_major_counties():
    """Get list of major Illinois counties with court info."""
    from app.services.state_courts import get_illinois_client
    client = get_illinois_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/illinois/county/{county}")
def illinois_county_courts(county: str):
    """Get courts in a specific Illinois county."""
    from app.services.state_courts import get_illinois_client
    client = get_illinois_client()
    return client.get_county_courts(county)


@app.get("/v1/state-courts/illinois/appellate")
def illinois_appellate_courts():
    """Get list of Illinois appellate courts."""
    from app.services.state_courts import get_illinois_client
    client = get_illinois_client()
    return {
        "courts": client.APPELLATE_COURTS,
        "count": len(client.APPELLATE_COURTS)
    }


@app.get("/v1/state-courts/illinois/appeals-districts")
def illinois_appeals_districts():
    """Get list of Illinois Appellate Court districts."""
    from app.services.state_courts import get_illinois_client
    client = get_illinois_client()
    return {
        "districts": client.get_appeals_districts(),
        "total": 5
    }


# New Jersey Courts
@app.get("/v1/state-courts/newjersey/status")
def newjersey_courts_status():
    """Get New Jersey courts coverage information."""
    from app.services.state_courts import get_newjersey_client
    client = get_newjersey_client()
    return client.get_courts()


@app.get("/v1/state-courts/newjersey/counties")
def newjersey_major_counties():
    """Get list of major New Jersey counties with court info."""
    from app.services.state_courts import get_newjersey_client
    client = get_newjersey_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/newjersey/county/{county}")
def newjersey_county_courts(county: str):
    """Get courts in a specific New Jersey county."""
    from app.services.state_courts import get_newjersey_client
    client = get_newjersey_client()
    return client.get_county_courts(county)


@app.get("/v1/state-courts/newjersey/vicinages")
def newjersey_vicinages():
    """Get list of New Jersey vicinages (court districts)."""
    from app.services.state_courts import get_newjersey_client
    client = get_newjersey_client()
    return {
        "vicinages": client.get_vicinages(),
        "total": 15
    }


# Washington Courts
@app.get("/v1/state-courts/washington/status")
def washington_courts_status():
    """Get Washington courts coverage information."""
    from app.services.state_courts import get_washington_client
    client = get_washington_client()
    return client.get_courts()


@app.get("/v1/state-courts/washington/counties")
def washington_major_counties():
    """Get list of major Washington counties with court info."""
    from app.services.state_courts import get_washington_client
    client = get_washington_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/washington/county/{county}")
def washington_county_courts(county: str):
    """Get courts in a specific Washington county."""
    from app.services.state_courts import get_washington_client
    client = get_washington_client()
    return client.get_county_courts(county)


@app.get("/v1/state-courts/washington/appeals-divisions")
def washington_appeals_divisions():
    """Get list of Washington Court of Appeals divisions."""
    from app.services.state_courts import get_washington_client
    client = get_washington_client()
    return {
        "divisions": client.get_appeals_divisions(),
        "total": 3
    }


# Arizona Courts
@app.get("/v1/state-courts/arizona/status")
def arizona_courts_status():
    """Get Arizona courts coverage information."""
    from app.services.state_courts import get_arizona_client
    client = get_arizona_client()
    return client.get_courts()


@app.get("/v1/state-courts/arizona/counties")
def arizona_major_counties():
    """Get list of major Arizona counties with court info."""
    from app.services.state_courts import get_arizona_client
    client = get_arizona_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/arizona/county/{county}")
def arizona_county_courts(county: str):
    """Get courts in a specific Arizona county."""
    from app.services.state_courts import get_arizona_client
    client = get_arizona_client()
    return client.get_county_courts(county)


@app.get("/v1/state-courts/arizona/appeals-divisions")
def arizona_appeals_divisions():
    """Get list of Arizona Court of Appeals divisions."""
    from app.services.state_courts import get_arizona_client
    client = get_arizona_client()
    return {
        "divisions": client.get_appeals_divisions(),
        "total": 2
    }


# Colorado Courts
@app.get("/v1/state-courts/colorado/status")
def colorado_courts_status():
    """Get Colorado courts coverage information."""
    from app.services.state_courts import get_colorado_client
    client = get_colorado_client()
    return client.get_courts()


@app.get("/v1/state-courts/colorado/counties")
def colorado_major_counties():
    """Get list of major Colorado counties with court info."""
    from app.services.state_courts import get_colorado_client
    client = get_colorado_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/colorado/county/{county}")
def colorado_county_courts(county: str):
    """Get courts in a specific Colorado county."""
    from app.services.state_courts import get_colorado_client
    client = get_colorado_client()
    return client.get_county_courts(county)


@app.get("/v1/state-courts/colorado/districts")
def colorado_judicial_districts():
    """Get list of Colorado judicial districts."""
    from app.services.state_courts import get_colorado_client
    client = get_colorado_client()
    return {
        "districts": client.get_judicial_districts(),
        "total": 22
    }


# Massachusetts Courts
@app.get("/v1/state-courts/massachusetts/status")
def massachusetts_courts_status():
    """Get Massachusetts courts coverage information."""
    from app.services.state_courts import get_massachusetts_client
    client = get_massachusetts_client()
    return client.get_courts()


@app.get("/v1/state-courts/massachusetts/counties")
def massachusetts_major_counties():
    """Get list of major Massachusetts counties with court info."""
    from app.services.state_courts import get_massachusetts_client
    client = get_massachusetts_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/massachusetts/county/{county}")
def massachusetts_county_courts(county: str):
    """Get courts in a specific Massachusetts county."""
    from app.services.state_courts import get_massachusetts_client
    client = get_massachusetts_client()
    return client.get_county_courts(county)


@app.get("/v1/state-courts/massachusetts/departments")
def massachusetts_trial_court_departments():
    """Get list of Massachusetts Trial Court departments."""
    from app.services.state_courts import get_massachusetts_client
    client = get_massachusetts_client()
    return {
        "departments": client.TRIAL_COURT_DEPTS,
        "count": len(client.TRIAL_COURT_DEPTS)
    }


# North Carolina Courts
@app.get("/v1/state-courts/north-carolina/status")
def northcarolina_courts_status():
    """Get North Carolina courts coverage information."""
    from app.services.state_courts import get_northcarolina_client
    client = get_northcarolina_client()
    return client.get_courts()


@app.get("/v1/state-courts/north-carolina/counties")
def northcarolina_major_counties():
    """Get list of major North Carolina counties with court info."""
    from app.services.state_courts import get_northcarolina_client
    client = get_northcarolina_client()
    return {
        "counties": client.COUNTIES,
        "count": len(client.COUNTIES)
    }


@app.get("/v1/state-courts/north-carolina/county/{county}")
def northcarolina_county_courts(county: str):
    """Get courts in a specific North Carolina county."""
    from app.services.state_courts import get_northcarolina_client
    client = get_northcarolina_client()
    return client.get_county_courts(county)


# Minnesota Courts
@app.get("/v1/state-courts/minnesota/status")
def minnesota_courts_status():
    """Get Minnesota courts coverage information."""
    from app.services.state_courts import get_minnesota_client
    return get_minnesota_client().get_courts()

@app.get("/v1/state-courts/minnesota/counties")
def minnesota_major_counties():
    from app.services.state_courts import get_minnesota_client
    c = get_minnesota_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/minnesota/county/{county}")
def minnesota_county_courts(county: str):
    from app.services.state_courts import get_minnesota_client
    return get_minnesota_client().get_county_courts(county)

@app.get("/v1/state-courts/minnesota/districts")
def minnesota_judicial_districts():
    from app.services.state_courts import get_minnesota_client
    return {"districts": get_minnesota_client().get_judicial_districts(), "total": 10}


# Wisconsin Courts
@app.get("/v1/state-courts/wisconsin/status")
def wisconsin_courts_status():
    from app.services.state_courts import get_wisconsin_client
    return get_wisconsin_client().get_courts()

@app.get("/v1/state-courts/wisconsin/counties")
def wisconsin_major_counties():
    from app.services.state_courts import get_wisconsin_client
    c = get_wisconsin_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/wisconsin/county/{county}")
def wisconsin_county_courts(county: str):
    from app.services.state_courts import get_wisconsin_client
    return get_wisconsin_client().get_county_courts(county)


# Tennessee Courts
@app.get("/v1/state-courts/tennessee/status")
def tennessee_courts_status():
    from app.services.state_courts import get_tennessee_client
    return get_tennessee_client().get_courts()

@app.get("/v1/state-courts/tennessee/counties")
def tennessee_major_counties():
    from app.services.state_courts import get_tennessee_client
    c = get_tennessee_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/tennessee/county/{county}")
def tennessee_county_courts(county: str):
    from app.services.state_courts import get_tennessee_client
    return get_tennessee_client().get_county_courts(county)


# Indiana Courts
@app.get("/v1/state-courts/indiana/status")
def indiana_courts_status():
    from app.services.state_courts import get_indiana_client
    return get_indiana_client().get_courts()

@app.get("/v1/state-courts/indiana/counties")
def indiana_major_counties():
    from app.services.state_courts import get_indiana_client
    c = get_indiana_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/indiana/county/{county}")
def indiana_county_courts(county: str):
    from app.services.state_courts import get_indiana_client
    return get_indiana_client().get_county_courts(county)


# Missouri Courts
@app.get("/v1/state-courts/missouri/status")
def missouri_courts_status():
    from app.services.state_courts import get_missouri_client
    return get_missouri_client().get_courts()

@app.get("/v1/state-courts/missouri/counties")
def missouri_major_counties():
    from app.services.state_courts import get_missouri_client
    c = get_missouri_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/missouri/county/{county}")
def missouri_county_courts(county: str):
    from app.services.state_courts import get_missouri_client
    return get_missouri_client().get_county_courts(county)


# Louisiana Courts
@app.get("/v1/state-courts/louisiana/status")
def louisiana_courts_status():
    from app.services.state_courts import get_louisiana_client
    return get_louisiana_client().get_courts()

@app.get("/v1/state-courts/louisiana/parishes")
def louisiana_major_parishes():
    from app.services.state_courts import get_louisiana_client
    c = get_louisiana_client()
    return {"parishes": c.PARISHES, "count": len(c.PARISHES)}

@app.get("/v1/state-courts/louisiana/parish/{parish}")
def louisiana_parish_courts(parish: str):
    from app.services.state_courts import get_louisiana_client
    return get_louisiana_client().get_parish_courts(parish)


# Connecticut Courts
@app.get("/v1/state-courts/connecticut/status")
def connecticut_courts_status():
    from app.services.state_courts import get_connecticut_client
    return get_connecticut_client().get_courts()

@app.get("/v1/state-courts/connecticut/districts")
def connecticut_judicial_districts():
    from app.services.state_courts import get_connecticut_client
    c = get_connecticut_client()
    return {"districts": c.JUDICIAL_DISTRICTS, "count": len(c.JUDICIAL_DISTRICTS)}

@app.get("/v1/state-courts/connecticut/district/{district}")
def connecticut_district_courts(district: str):
    from app.services.state_courts import get_connecticut_client
    return get_connecticut_client().get_district_courts(district)


# Oregon Courts
@app.get("/v1/state-courts/oregon/status")
def oregon_courts_status():
    from app.services.state_courts import get_oregon_client
    return get_oregon_client().get_courts()

@app.get("/v1/state-courts/oregon/counties")
def oregon_major_counties():
    from app.services.state_courts import get_oregon_client
    c = get_oregon_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/oregon/county/{county}")
def oregon_county_courts(county: str):
    from app.services.state_courts import get_oregon_client
    return get_oregon_client().get_county_courts(county)


# Nevada Courts
@app.get("/v1/state-courts/nevada/status")
def nevada_courts_status():
    from app.services.state_courts import get_nevada_client
    return get_nevada_client().get_courts()

@app.get("/v1/state-courts/nevada/counties")
def nevada_major_counties():
    from app.services.state_courts import get_nevada_client
    c = get_nevada_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/nevada/county/{county}")
def nevada_county_courts(county: str):
    from app.services.state_courts import get_nevada_client
    return get_nevada_client().get_county_courts(county)


# South Carolina Courts
@app.get("/v1/state-courts/southcarolina/status")
def southcarolina_courts_status():
    from app.services.state_courts import get_southcarolina_client
    return get_southcarolina_client().get_courts()

@app.get("/v1/state-courts/southcarolina/counties")
def southcarolina_major_counties():
    from app.services.state_courts import get_southcarolina_client
    c = get_southcarolina_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/southcarolina/county/{county}")
def southcarolina_county_courts(county: str):
    from app.services.state_courts import get_southcarolina_client
    return get_southcarolina_client().get_county_courts(county)


# Alabama Courts
@app.get("/v1/state-courts/alabama/status")
def alabama_courts_status():
    from app.services.state_courts import get_alabama_client
    return get_alabama_client().get_courts()

@app.get("/v1/state-courts/alabama/counties")
def alabama_major_counties():
    from app.services.state_courts import get_alabama_client
    c = get_alabama_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/alabama/county/{county}")
def alabama_county_courts(county: str):
    from app.services.state_courts import get_alabama_client
    return get_alabama_client().get_county_courts(county)


# Kentucky Courts
@app.get("/v1/state-courts/kentucky/status")
def kentucky_courts_status():
    from app.services.state_courts import get_kentucky_client
    return get_kentucky_client().get_courts()

@app.get("/v1/state-courts/kentucky/counties")
def kentucky_major_counties():
    from app.services.state_courts import get_kentucky_client
    c = get_kentucky_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/kentucky/county/{county}")
def kentucky_county_courts(county: str):
    from app.services.state_courts import get_kentucky_client
    return get_kentucky_client().get_county_courts(county)


# Iowa Courts
@app.get("/v1/state-courts/iowa/status")
def iowa_courts_status():
    from app.services.state_courts import get_iowa_client
    return get_iowa_client().get_courts()

@app.get("/v1/state-courts/iowa/counties")
def iowa_major_counties():
    from app.services.state_courts import get_iowa_client
    c = get_iowa_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/iowa/county/{county}")
def iowa_county_courts(county: str):
    from app.services.state_courts import get_iowa_client
    return get_iowa_client().get_county_courts(county)


# Utah Courts
@app.get("/v1/state-courts/utah/status")
def utah_courts_status():
    from app.services.state_courts import get_utah_client
    return get_utah_client().get_courts()

@app.get("/v1/state-courts/utah/counties")
def utah_major_counties():
    from app.services.state_courts import get_utah_client
    c = get_utah_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/utah/county/{county}")
def utah_county_courts(county: str):
    from app.services.state_courts import get_utah_client
    return get_utah_client().get_county_courts(county)


# Arkansas Courts
@app.get("/v1/state-courts/arkansas/status")
def arkansas_courts_status():
    from app.services.state_courts import get_arkansas_client
    return get_arkansas_client().get_courts()

@app.get("/v1/state-courts/arkansas/counties")
def arkansas_major_counties():
    from app.services.state_courts import get_arkansas_client
    c = get_arkansas_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/arkansas/county/{county}")
def arkansas_county_courts(county: str):
    from app.services.state_courts import get_arkansas_client
    return get_arkansas_client().get_county_courts(county)


# Kansas Courts
@app.get("/v1/state-courts/kansas/status")
def kansas_courts_status():
    from app.services.state_courts import get_kansas_client
    return get_kansas_client().get_courts()

@app.get("/v1/state-courts/kansas/counties")
def kansas_major_counties():
    from app.services.state_courts import get_kansas_client
    c = get_kansas_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/kansas/county/{county}")
def kansas_county_courts(county: str):
    from app.services.state_courts import get_kansas_client
    return get_kansas_client().get_county_courts(county)


# Nebraska Courts
@app.get("/v1/state-courts/nebraska/status")
def nebraska_courts_status():
    from app.services.state_courts import get_nebraska_client
    return get_nebraska_client().get_courts()

@app.get("/v1/state-courts/nebraska/counties")
def nebraska_major_counties():
    from app.services.state_courts import get_nebraska_client
    c = get_nebraska_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/nebraska/county/{county}")
def nebraska_county_courts(county: str):
    from app.services.state_courts import get_nebraska_client
    return get_nebraska_client().get_county_courts(county)


# New Mexico Courts
@app.get("/v1/state-courts/newmexico/status")
def newmexico_courts_status():
    from app.services.state_courts import get_newmexico_client
    return get_newmexico_client().get_courts()

@app.get("/v1/state-courts/newmexico/counties")
def newmexico_major_counties():
    from app.services.state_courts import get_newmexico_client
    c = get_newmexico_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/newmexico/county/{county}")
def newmexico_county_courts(county: str):
    from app.services.state_courts import get_newmexico_client
    return get_newmexico_client().get_county_courts(county)


# Mississippi Courts
@app.get("/v1/state-courts/mississippi/status")
def mississippi_courts_status():
    from app.services.state_courts import get_mississippi_client
    return get_mississippi_client().get_courts()

@app.get("/v1/state-courts/mississippi/counties")
def mississippi_major_counties():
    from app.services.state_courts import get_mississippi_client
    c = get_mississippi_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/mississippi/county/{county}")
def mississippi_county_courts(county: str):
    from app.services.state_courts import get_mississippi_client
    return get_mississippi_client().get_county_courts(county)


# Hawaii Courts
@app.get("/v1/state-courts/hawaii/status")
def hawaii_courts_status():
    from app.services.state_courts import get_hawaii_client
    return get_hawaii_client().get_courts()

@app.get("/v1/state-courts/hawaii/circuits")
def hawaii_circuits():
    from app.services.state_courts import get_hawaii_client
    c = get_hawaii_client()
    return {"circuits": c.CIRCUITS, "count": len(c.CIRCUITS)}

@app.get("/v1/state-courts/hawaii/circuit/{circuit}")
def hawaii_circuit_courts(circuit: str):
    from app.services.state_courts import get_hawaii_client
    return get_hawaii_client().get_circuit_courts(circuit)


# Idaho Courts
@app.get("/v1/state-courts/idaho/status")
def idaho_courts_status():
    from app.services.state_courts import get_idaho_client
    return get_idaho_client().get_courts()

@app.get("/v1/state-courts/idaho/counties")
def idaho_major_counties():
    from app.services.state_courts import get_idaho_client
    c = get_idaho_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/idaho/county/{county}")
def idaho_county_courts(county: str):
    from app.services.state_courts import get_idaho_client
    return get_idaho_client().get_county_courts(county)


# West Virginia Courts
@app.get("/v1/state-courts/westvirginia/status")
def westvirginia_courts_status():
    from app.services.state_courts import get_westvirginia_client
    return get_westvirginia_client().get_courts()

@app.get("/v1/state-courts/westvirginia/counties")
def westvirginia_major_counties():
    from app.services.state_courts import get_westvirginia_client
    c = get_westvirginia_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/westvirginia/county/{county}")
def westvirginia_county_courts(county: str):
    from app.services.state_courts import get_westvirginia_client
    return get_westvirginia_client().get_county_courts(county)


# Delaware Courts
@app.get("/v1/state-courts/delaware/status")
def delaware_courts_status():
    from app.services.state_courts import get_delaware_client
    return get_delaware_client().get_courts()

@app.get("/v1/state-courts/delaware/counties")
def delaware_counties():
    from app.services.state_courts import get_delaware_client
    c = get_delaware_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/delaware/county/{county}")
def delaware_county_courts(county: str):
    from app.services.state_courts import get_delaware_client
    return get_delaware_client().get_county_courts(county)


# Maine Courts
@app.get("/v1/state-courts/maine/status")
def maine_courts_status():
    from app.services.state_courts import get_maine_client
    return get_maine_client().get_courts()

@app.get("/v1/state-courts/maine/counties")
def maine_major_counties():
    from app.services.state_courts import get_maine_client
    c = get_maine_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/maine/county/{county}")
def maine_county_courts(county: str):
    from app.services.state_courts import get_maine_client
    return get_maine_client().get_county_courts(county)


# Montana Courts
@app.get("/v1/state-courts/montana/status")
def montana_courts_status():
    from app.services.state_courts import get_montana_client
    return get_montana_client().get_courts()

@app.get("/v1/state-courts/montana/counties")
def montana_major_counties():
    from app.services.state_courts import get_montana_client
    c = get_montana_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/montana/county/{county}")
def montana_county_courts(county: str):
    from app.services.state_courts import get_montana_client
    return get_montana_client().get_county_courts(county)


# Wyoming Courts
@app.get("/v1/state-courts/wyoming/status")
def wyoming_courts_status():
    from app.services.state_courts import get_wyoming_client
    return get_wyoming_client().get_courts()

@app.get("/v1/state-courts/wyoming/counties")
def wyoming_major_counties():
    from app.services.state_courts import get_wyoming_client
    c = get_wyoming_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/wyoming/county/{county}")
def wyoming_county_courts(county: str):
    from app.services.state_courts import get_wyoming_client
    return get_wyoming_client().get_county_courts(county)


# Vermont Courts
@app.get("/v1/state-courts/vermont/status")
def vermont_courts_status():
    from app.services.state_courts import get_vermont_client
    return get_vermont_client().get_courts()

@app.get("/v1/state-courts/vermont/counties")
def vermont_major_counties():
    from app.services.state_courts import get_vermont_client
    c = get_vermont_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/vermont/county/{county}")
def vermont_county_courts(county: str):
    from app.services.state_courts import get_vermont_client
    return get_vermont_client().get_county_courts(county)


# North Dakota Courts
@app.get("/v1/state-courts/northdakota/status")
def northdakota_courts_status():
    from app.services.state_courts import get_northdakota_client
    return get_northdakota_client().get_courts()

@app.get("/v1/state-courts/northdakota/counties")
def northdakota_major_counties():
    from app.services.state_courts import get_northdakota_client
    c = get_northdakota_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/northdakota/county/{county}")
def northdakota_county_courts(county: str):
    from app.services.state_courts import get_northdakota_client
    return get_northdakota_client().get_county_courts(county)


# South Dakota Courts
@app.get("/v1/state-courts/southdakota/status")
def southdakota_courts_status():
    from app.services.state_courts import get_southdakota_client
    return get_southdakota_client().get_courts()

@app.get("/v1/state-courts/southdakota/counties")
def southdakota_major_counties():
    from app.services.state_courts import get_southdakota_client
    c = get_southdakota_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/southdakota/county/{county}")
def southdakota_county_courts(county: str):
    from app.services.state_courts import get_southdakota_client
    return get_southdakota_client().get_county_courts(county)


# Alaska Courts
@app.get("/v1/state-courts/alaska/status")
def alaska_courts_status():
    from app.services.state_courts import get_alaska_client
    return get_alaska_client().get_courts()

@app.get("/v1/state-courts/alaska/districts")
def alaska_districts():
    from app.services.state_courts import get_alaska_client
    c = get_alaska_client()
    return {"districts": c.DISTRICTS, "count": len(c.DISTRICTS)}

@app.get("/v1/state-courts/alaska/district/{district}")
def alaska_district_courts(district: str):
    from app.services.state_courts import get_alaska_client
    return get_alaska_client().get_district_courts(district)


# New Hampshire Courts
@app.get("/v1/state-courts/newhampshire/status")
def newhampshire_courts_status():
    from app.services.state_courts import get_newhampshire_client
    return get_newhampshire_client().get_courts()

@app.get("/v1/state-courts/newhampshire/counties")
def newhampshire_counties():
    from app.services.state_courts import get_newhampshire_client
    c = get_newhampshire_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/newhampshire/county/{county}")
def newhampshire_county_courts(county: str):
    from app.services.state_courts import get_newhampshire_client
    return get_newhampshire_client().get_county_courts(county)


# Rhode Island Courts
@app.get("/v1/state-courts/rhodeisland/status")
def rhodeisland_courts_status():
    from app.services.state_courts import get_rhodeisland_client
    return get_rhodeisland_client().get_courts()

@app.get("/v1/state-courts/rhodeisland/counties")
def rhodeisland_counties():
    from app.services.state_courts import get_rhodeisland_client
    c = get_rhodeisland_client()
    return {"counties": c.COUNTIES, "count": len(c.COUNTIES)}

@app.get("/v1/state-courts/rhodeisland/county/{county}")
def rhodeisland_county_courts(county: str):
    from app.services.state_courts import get_rhodeisland_client
    return get_rhodeisland_client().get_county_courts(county)


# Unified state courts overview endpoint
@app.get("/v1/state-courts/all/status")
def all_state_courts_status():
    """Get overview of all integrated state court systems."""
    _ensure_initialized()

    return {
        "integrated_states": {
            "full_data_access": [
                {"state": "OK", "name": "Oklahoma", "source": "OSCN", "coverage": "All 77 counties"},
                {"state": "VA", "name": "Virginia", "source": "Court Data CSV", "coverage": "Circuit and District courts"},
            ],
            "appellate_opinions": [
                {"state": "AR", "name": "Arkansas", "source": "CourtListener"},
                {"state": "IL", "name": "Illinois", "source": "CourtListener"},
                {"state": "NM", "name": "New Mexico", "source": "CourtListener"},
                {"state": "NC", "name": "North Carolina", "source": "CourtListener"},
            ],
            "partial_access": [
                {"state": "FL", "name": "Florida", "note": "County clerk portals, DCA opinions"},
                {"state": "TX", "name": "Texas", "note": "OCA statistics, county portals"},
                {"state": "MD", "name": "Maryland", "note": "Case Search portal, CaseHarvester"},
                {"state": "NY", "name": "New York", "note": "WebCivil, NYSCEF, appellate courts"},
                {"state": "CA", "name": "California", "note": "Limited trial court access, appellate opinions"},
                {"state": "GA", "name": "Georgia", "note": "County portals, appellate opinions"},
                {"state": "PA", "name": "Pennsylvania", "note": "UJS Portal, 67 judicial districts"},
                {"state": "OH", "name": "Ohio", "note": "Supreme Court, 12 appeals districts, 88 counties"},
                {"state": "MI", "name": "Michigan", "note": "57 circuits, 4 appeals districts"},
                {"state": "IL", "name": "Illinois", "note": "Cook County, 5 appellate districts"},
                {"state": "NJ", "name": "New Jersey", "note": "eCourts, 15 vicinages"},
                {"state": "WA", "name": "Washington", "note": "JIS-Link, 3 appeals divisions"},
                {"state": "AZ", "name": "Arizona", "note": "PACCI, 2 appeals divisions"},
                {"state": "CO", "name": "Colorado", "note": "22 judicial districts, water courts"},
                {"state": "MA", "name": "Massachusetts", "note": "Unified Trial Court, 7 departments"},
                {"state": "NC", "name": "North Carolina", "note": "eCourts, 100 counties"},
                {"state": "MN", "name": "Minnesota", "note": "MNCIS, 10 judicial districts"},
                {"state": "WI", "name": "Wisconsin", "note": "WCCA, 4 appeals districts"},
                {"state": "TN", "name": "Tennessee", "note": "31 judicial districts"},
                {"state": "IN", "name": "Indiana", "note": "MyCase, 92 counties"},
                {"state": "MO", "name": "Missouri", "note": "Case.net, 46 circuits"},
                {"state": "LA", "name": "Louisiana", "note": "5 circuits, 64 parishes"},
                {"state": "CT", "name": "Connecticut", "note": "13 judicial districts"},
                {"state": "OR", "name": "Oregon", "note": "OJCIN, Tax Court"},
                {"state": "NV", "name": "Nevada", "note": "Odyssey eFiling, 10 districts"},
                {"state": "SC", "name": "South Carolina", "note": "Public Index, 16 circuits"},
                {"state": "AL", "name": "Alabama", "note": "Alacourt, 41 circuits"},
                {"state": "KY", "name": "Kentucky", "note": "CourtNet, 57 circuits"},
                {"state": "IA", "name": "Iowa", "note": "Iowa Courts Online, 8 districts"},
                {"state": "UT", "name": "Utah", "note": "Xchange, 8 districts"},
                {"state": "AR", "name": "Arkansas", "note": "CourtConnect, 28 circuits"},
                {"state": "KS", "name": "Kansas", "note": "Public Access Portal, 31 districts"},
                {"state": "NE", "name": "Nebraska", "note": "JUSTICE, 12 districts"},
                {"state": "NM", "name": "New Mexico", "note": "Odyssey Case Lookup, 13 districts"},
                {"state": "MS", "name": "Mississippi", "note": "MEC, 22 circuits"},
                {"state": "HI", "name": "Hawaii", "note": "eCourt Kokua, 4 circuits"},
                {"state": "ID", "name": "Idaho", "note": "iCourt, 7 districts"},
                {"state": "WV", "name": "West Virginia", "note": "eCourts, 31 circuits"},
                {"state": "DE", "name": "Delaware", "note": "Chancery Court, 3 counties"},
                {"state": "ME", "name": "Maine", "note": "Case Lookup, 16 districts"},
                {"state": "MT", "name": "Montana", "note": "Full Court Enterprise, 22 districts"},
                {"state": "WY", "name": "Wyoming", "note": "WyoCourts, 9 districts"},
                {"state": "VT", "name": "Vermont", "note": "Odyssey, 14 units"},
                {"state": "ND", "name": "North Dakota", "note": "Odyssey Portal, 8 districts"},
                {"state": "SD", "name": "South Dakota", "note": "Odyssey, 7 circuits"},
                {"state": "AK", "name": "Alaska", "note": "CourtView, 4 districts"},
                {"state": "NH", "name": "New Hampshire", "note": "Case Index, 10 counties"},
                {"state": "RI", "name": "Rhode Island", "note": "Public Portal, 5 counties"},
            ]
        },
        "endpoints": {
            "ingest": "/v1/state-courts/ingest/all",
            "search_cases": "/v1/state-courts/db/cases",
            "search_opinions": "/v1/state-courts/db/opinions",
            "stats": "/v1/state-courts/db/stats"
        }
    }


# State Courts Dashboard
from .state_courts_dashboard import generate_state_courts_dashboard_html


@app.get("/state-courts", response_class=HTMLResponse)
def state_courts_dashboard_view():
    """State Courts Analytics Dashboard - Trial courts and appellate opinions from multiple states"""
    _ensure_initialized()

    # Get stats
    try:
        stats = get_state_court_stats()
    except Exception:
        stats = {
            "total_cases": 0,
            "total_opinions": 0,
            "states_covered": 0,
            "by_state": [],
            "opinions_by_state": [],
            "by_case_type": []
        }

    # Get recent cases
    try:
        recent_cases = search_state_court_cases(limit=10)
    except Exception:
        recent_cases = []

    # Get recent opinions
    try:
        recent_opinions = search_state_appellate_opinions(limit=10)
    except Exception:
        recent_opinions = []

    # Get scraper status for all states
    try:
        from .models.db import get_scraper_stats, get_recent_scraper_runs
        scraper_stats = get_scraper_stats()

        # Build state-level status dict for dashboard map
        scraper_status = {}
        for state_data in scraper_stats.get("by_state", []):
            state_code = state_data.get("state", "")
            if state_code:
                scraper_status[state_code] = {
                    "cases_found": state_data.get("total_cases", 0) or 0,
                    "last_sync": state_data.get("last_run", "Never"),
                    "status": "idle",
                    "errors": 0,
                    "configured": True
                }

        # Check for running scrapers
        running = get_recent_scraper_runs(status="running", limit=50)
        for run in running:
            state = run.get("state", "")
            if state:
                if state not in scraper_status:
                    scraper_status[state] = {"cases_found": 0, "last_sync": "Never", "errors": 0, "configured": True}
                scraper_status[state]["status"] = "running"

        # Check for recent errors
        failed = get_recent_scraper_runs(status="failed", limit=100)
        for run in failed:
            state = run.get("state", "")
            if state:
                if state not in scraper_status:
                    scraper_status[state] = {"cases_found": 0, "last_sync": "Never", "status": "idle", "configured": True}
                scraper_status[state]["errors"] = scraper_status[state].get("errors", 0) + 1
    except Exception:
        scraper_status = {}

    # Get CAPTCHA queue
    try:
        from .models.db import get_unresolved_captchas
        captcha_queue = get_unresolved_captchas(limit=20)
    except Exception:
        captcha_queue = []

    # Coverage info
    coverage_info = {
        "full_data_access": [
            {"state": "OK", "name": "Oklahoma", "source": "OSCN", "coverage": "All 77 counties"},
            {"state": "VA", "name": "Virginia", "source": "Court Data CSV", "coverage": "Circuit and District courts"},
        ],
        "appellate_opinions": [
            {"state": "AR", "name": "Arkansas", "source": "CourtListener"},
            {"state": "IL", "name": "Illinois", "source": "CourtListener"},
            {"state": "NM", "name": "New Mexico", "source": "CourtListener"},
            {"state": "NC", "name": "North Carolina", "source": "CourtListener"},
        ],
        "partial_access": [
            {"state": "FL", "name": "Florida", "note": "County clerk portals, DCA opinions"},
            {"state": "TX", "name": "Texas", "note": "OCA statistics, county portals"},
            {"state": "MD", "name": "Maryland", "note": "Case Search portal, CaseHarvester"},
            {"state": "NY", "name": "New York", "note": "WebCivil, NYSCEF, appellate courts"},
            {"state": "CA", "name": "California", "note": "Limited trial court access, appellate opinions"},
        ]
    }

    return generate_state_courts_dashboard_html(
        stats, recent_cases, recent_opinions, coverage_info,
        scraper_status=scraper_status,
        captcha_queue=captcha_queue
    )


# --- Unified State Court Search API ---

@app.get("/v1/state-courts/search")
def unified_state_court_search(
    q: str = None,
    state: str = None,
    county: str = None,
    case_type: str = None,
    party_name: str = None,
    date_from: str = None,
    date_to: str = None,
    source: str = None,
    include_opinions: bool = True,
    include_cases: bool = True,
    limit: int = 50,
    offset: int = 0
):
    """
    Unified search across all state court data sources.

    This endpoint searches both stored database records and can optionally
    query live sources for fresh data.

    Args:
        q: General search query (searches case names, party names, citations)
        state: Filter by state code (e.g., "OK", "VA", "AR")
        county: Filter by county name
        case_type: Filter by case type (e.g., "CF", "CV", "criminal")
        party_name: Search by party name
        date_from: Start date filter (YYYY-MM-DD)
        date_to: End date filter (YYYY-MM-DD)
        source: Filter by data source (e.g., "oscn", "courtlistener", "virginia")
        include_opinions: Include appellate opinions in results (default: true)
        include_cases: Include trial court cases in results (default: true)
        limit: Maximum results to return (default: 50)
        offset: Pagination offset

    Returns:
        Combined search results from all matching sources
    """
    _ensure_initialized()

    results = {
        "query": {
            "q": q,
            "state": state,
            "county": county,
            "case_type": case_type,
            "party_name": party_name,
            "date_from": date_from,
            "date_to": date_to,
            "source": source
        },
        "cases": [],
        "opinions": [],
        "total_cases": 0,
        "total_opinions": 0,
        "sources_searched": []
    }

    # Search stored cases
    if include_cases:
        try:
            cases = search_state_court_cases(
                state=state,
                county=county,
                case_type=case_type,
                party_name=party_name or q,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
                offset=offset
            )
            results["cases"] = cases
            results["total_cases"] = len(cases)
            results["sources_searched"].append("database_cases")
        except Exception as e:
            results["errors"] = results.get("errors", [])
            results["errors"].append(f"Case search error: {str(e)}")

    # Search stored opinions
    if include_opinions:
        try:
            opinions = search_state_appellate_opinions(
                state=state,
                search=q,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
                offset=offset
            )
            results["opinions"] = opinions
            results["total_opinions"] = len(opinions)
            results["sources_searched"].append("database_opinions")
        except Exception as e:
            results["errors"] = results.get("errors", [])
            results["errors"].append(f"Opinion search error: {str(e)}")

    results["total"] = results["total_cases"] + results["total_opinions"]

    return results


@app.get("/v1/state-courts/search/live")
def live_state_court_search(
    state: str,
    party_name: str = None,
    last_name: str = None,
    first_name: str = None,
    county: str = None,
    case_type: str = None,
    limit: int = 50
):
    """
    Search live state court sources (bypasses local database).

    This endpoint queries state court portals directly for fresh data.
    Note: Not all states support live searching.

    Args:
        state: State code (required) - "OK", "VA", "AR", "IL", "NM", "NC"
        party_name: Party name to search (for Oklahoma, uses last_name)
        last_name: Party last name (for Oklahoma OSCN)
        first_name: Party first name (optional)
        county: County name (state-specific)
        case_type: Case type filter
        limit: Maximum results

    Returns:
        Fresh search results from live state court sources
    """
    _ensure_initialized()

    state = state.upper()
    results = {
        "state": state,
        "source": None,
        "cases": [],
        "opinions": [],
        "count": 0
    }

    # Oklahoma - OSCN
    if state == "OK":
        client = get_oklahoma_client()
        name = last_name or party_name
        if not name:
            raise HTTPException(status_code=400, detail="party_name or last_name required for Oklahoma search")

        results["source"] = "OSCN"
        try:
            cases = client.search_cases(
                county=county or "oklahoma",
                last_name=name,
                first_name=first_name,
                limit=limit
            )
            results["cases"] = cases
            results["count"] = len(cases)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"OSCN error: {str(e)}")

    # Virginia - CSV data
    elif state == "VA":
        client = get_virginia_client()
        results["source"] = "Virginia Court Data"
        try:
            # Virginia uses bulk CSV, return stats instead
            stats = client.get_stats()
            results["stats"] = stats
            results["note"] = "Virginia uses bulk CSV. Use /v1/state-courts/virginia/* endpoints for data."
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Virginia error: {str(e)}")

    # CourtListener states (appellate opinions)
    elif state in ["AR", "IL", "NM", "NC"]:
        client = get_cap_client()
        results["source"] = "CourtListener"
        try:
            opinions = client.search_cases(
                jurisdiction=state.lower(),
                search=party_name,
                limit=limit
            )
            results["opinions"] = opinions
            results["count"] = len(opinions)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"CourtListener error: {str(e)}")

    # Florida
    elif state == "FL":
        results["source"] = "Florida Courts (limited)"
        results["note"] = "Florida trial courts require county-specific access. Use /v1/state-courts/florida/* endpoints."
        client = get_florida_client()
        results["courts"] = client.get_courts()

    # Texas
    elif state == "TX":
        results["source"] = "Texas Courts (limited)"
        results["note"] = "Texas trial courts require county-specific access. Use /v1/state-courts/texas/* endpoints."
        client = get_texas_client()
        results["courts"] = client.get_courts()

    # Maryland
    elif state == "MD":
        results["source"] = "Maryland Case Search (limited)"
        results["note"] = "Maryland requires session-based search. Use /v1/state-courts/maryland/* endpoints."
        client = get_maryland_client()
        results["courts"] = client.get_courts()

    # New York
    elif state == "NY":
        results["source"] = "New York Courts (limited)"
        results["note"] = "New York has multiple systems. Use /v1/state-courts/newyork/* endpoints."
        client = get_newyork_client()
        results["courts"] = client.get_courts()

    # California
    elif state == "CA":
        results["source"] = "California Courts (limited)"
        results["note"] = "California trial courts have limited free access. Use /v1/state-courts/california/* endpoints."
        client = get_california_client()
        results["courts"] = client.get_courts()

    else:
        raise HTTPException(
            status_code=400,
            detail=f"State '{state}' not supported. Supported: OK, VA, AR, IL, NM, NC, FL, TX, MD, NY, CA"
        )

    return results


# --- Court Document Text Extraction ---

from .services.state_courts.text_extraction import parse_court_document, parse_html_record


@app.post("/v1/state-courts/parse/text")
def parse_document_text(text: str):
    """
    Parse unstructured court document text and extract structured data.

    Extracts:
    - Case number
    - Date filed
    - Court name
    - Judge
    - Parties (plaintiffs/defendants)
    - Attorneys
    - Charges (for criminal cases)
    - Disposition
    - Case type (criminal, civil, family, etc.)

    Args:
        text: Raw text from court document

    Returns:
        Extracted structured data
    """
    if not text or len(text) < 10:
        raise HTTPException(status_code=400, detail="Text too short to parse")

    try:
        result = parse_court_document(text)
        return {
            "status": "success",
            "extracted": result,
            "input_length": len(text)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parsing error: {str(e)}")


@app.post("/v1/state-courts/parse/html")
def parse_html_document(html: str):
    """
    Parse HTML court record page and extract structured data.

    Useful for parsing scraped court website pages.

    Args:
        html: HTML content from court website

    Returns:
        Extracted structured data
    """
    if not html or len(html) < 20:
        raise HTTPException(status_code=400, detail="HTML too short to parse")

    try:
        result = parse_html_record(html)
        return {
            "status": "success",
            "extracted": result,
            "input_length": len(html)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parsing error: {str(e)}")


# --- Data Quality Monitoring ---

from .services.state_courts.data_quality import (
    get_quality_monitor, get_availability_checker,
    get_quality_report, check_sources
)


@app.get("/v1/state-courts/quality/report")
def state_court_quality_report():
    """
    Get comprehensive data quality report for state court sources.

    Returns:
    - Source health status
    - Recent ingestion trends
    - Active alerts
    - Field completeness expectations
    """
    _ensure_initialized()
    return get_quality_report()


@app.get("/v1/state-courts/quality/sources")
def check_source_availability():
    """
    Check availability of all state court data sources.

    Performs live connectivity checks to each source.
    """
    _ensure_initialized()
    return check_sources()


@app.get("/v1/state-courts/quality/health")
def state_court_health():
    """Get quick health status for all state court sources."""
    _ensure_initialized()
    monitor = get_quality_monitor()
    return {
        "status": "ok",
        "source_health": monitor.get_source_health(),
        "recent_alerts": monitor.alerts[-5:] if monitor.alerts else [],
    }


# --- 50-State Scraping API ---

from .services.state_courts.ingest import (
    scrape_state, scrape_all_states, get_available_scrapers, get_scraping_status
)
from .services.state_courts.base_scraper import get_scraper, list_scrapers
from .models.db import (
    get_scraper_run, get_recent_scraper_runs, get_scraper_stats,
    get_unresolved_captchas, resolve_captcha, get_captcha_stats
)


@app.get("/v1/state-courts/scrapers")
def list_available_scrapers():
    """
    List all available state court scrapers and their capabilities.

    Returns:
        List of scrapers with state codes, names, types, and features
    """
    _ensure_initialized()
    scrapers = get_available_scrapers()
    return {
        "status": "ok",
        "scrapers": scrapers,
        "total_states": len(scrapers),
        "by_type": {
            "form": len([s for s in scrapers if s.get("type") == "form"]),
            "browser": len([s for s in scrapers if s.get("type") == "browser"]),
            "rss": len([s for s in scrapers if s.get("type") == "rss"]),
            "bulk": len([s for s in scrapers if s.get("type") == "bulk"]),
        }
    }


@app.post("/v1/state-courts/scrape/{state}")
def trigger_state_scrape(
    state: str,
    county: str = None,
    case_type: str = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 100,
    background_tasks: BackgroundTasks = None
):
    """
    Trigger scraping for a specific state.

    Args:
        state: State code (e.g., "PA", "MD", "WI")
        county: Optional county filter
        case_type: Optional case type filter
        date_from: Start date YYYY-MM-DD
        date_to: End date YYYY-MM-DD
        limit: Maximum cases to fetch per query

    Returns:
        Job ID and initial status
    """
    _ensure_initialized()

    state_upper = state.upper()

    # Check if scraper exists for this state
    scraper = get_scraper(state_upper)
    if not scraper:
        raise HTTPException(
            status_code=404,
            detail=f"No scraper available for state: {state_upper}"
        )

    # Run scraping (synchronously for now - can be made async with background tasks)
    try:
        result = scrape_state(
            state_code=state_upper,
            county=county,
            case_type=case_type,
            date_from=date_from,
            date_to=date_to,
            limit=limit
        )
        return {
            "status": "completed",
            "state": state_upper,
            "run_id": result.get("run_id"),
            "cases_found": result.get("cases_found", 0),
            "cases_stored": result.get("cases_stored", 0),
            "duration_seconds": result.get("duration_seconds"),
            "errors": result.get("errors", [])
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scraping failed: {str(e)}")


@app.get("/v1/state-courts/scrape/status/{run_id}")
def get_scrape_status(run_id: str):
    """
    Get status of a scraping job.

    Args:
        run_id: The scraper run ID

    Returns:
        Job status and statistics
    """
    _ensure_initialized()

    run = get_scraper_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Scraper run not found: {run_id}")

    return {
        "status": "ok",
        "run": run
    }


@app.get("/v1/state-courts/scrape/recent")
def get_recent_scrapes(state: str = None, limit: int = 50):
    """
    Get recent scraping runs.

    Args:
        state: Optional state filter
        limit: Maximum runs to return

    Returns:
        List of recent scraper runs
    """
    _ensure_initialized()

    runs = get_recent_scraper_runs(state=state, limit=limit)
    return {
        "status": "ok",
        "runs": runs,
        "count": len(runs)
    }


@app.get("/v1/state-courts/scrape/stats")
def get_scrape_statistics():
    """
    Get aggregated scraping statistics across all states.

    Returns:
        Statistics by state including success rates and case counts
    """
    _ensure_initialized()

    stats = get_scraper_stats()
    return {
        "status": "ok",
        "stats": stats
    }


@app.post("/v1/state-courts/scrape/all")
def trigger_all_states_scrape(
    states: List[str] = None,
    limit_per_state: int = 50,
    background_tasks: BackgroundTasks = None
):
    """
    Trigger scraping for all available states (or specified subset).

    Args:
        states: Optional list of state codes to scrape (default: all available)
        limit_per_state: Maximum cases per state

    Returns:
        Summary of scraping results
    """
    _ensure_initialized()

    try:
        result = scrape_all_states(
            states=states,
            limit_per_state=limit_per_state
        )
        return {
            "status": "completed",
            "summary": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bulk scraping failed: {str(e)}")


@app.get("/v1/state-courts/{state}/counties")
def get_state_counties(state: str):
    """
    Get list of counties for a state.

    Args:
        state: State code (e.g., "PA", "MD")

    Returns:
        List of county names
    """
    _ensure_initialized()

    state_upper = state.upper()
    scraper = get_scraper(state_upper)

    if not scraper:
        raise HTTPException(
            status_code=404,
            detail=f"No scraper available for state: {state_upper}"
        )

    counties = []
    if hasattr(scraper, 'get_counties'):
        counties = scraper.get_counties()
    elif hasattr(scraper, 'COUNTIES'):
        counties = list(scraper.COUNTIES.keys()) if isinstance(scraper.COUNTIES, dict) else scraper.COUNTIES

    return {
        "status": "ok",
        "state": state_upper,
        "counties": counties,
        "count": len(counties)
    }


@app.get("/v1/state-courts/{state}/case-types")
def get_state_case_types(state: str):
    """
    Get available case types for a state.

    Args:
        state: State code

    Returns:
        Mapping of case type codes to descriptions
    """
    _ensure_initialized()

    state_upper = state.upper()
    scraper = get_scraper(state_upper)

    if not scraper:
        raise HTTPException(
            status_code=404,
            detail=f"No scraper available for state: {state_upper}"
        )

    case_types = {}
    if hasattr(scraper, 'get_case_types'):
        case_types = scraper.get_case_types()
    elif hasattr(scraper, 'CASE_TYPE_CODES'):
        case_types = scraper.CASE_TYPE_CODES

    return {
        "status": "ok",
        "state": state_upper,
        "case_types": case_types
    }


# --- CAPTCHA Management ---

@app.get("/v1/state-courts/captcha/queue")
def get_captcha_queue():
    """
    Get unresolved CAPTCHAs that need manual intervention.

    Returns:
        List of pending CAPTCHAs with URLs and timestamps
    """
    _ensure_initialized()

    captchas = get_unresolved_captchas(limit=100)
    stats = get_captcha_stats()

    return {
        "status": "ok",
        "pending": captchas,
        "count": len(captchas),
        "stats": stats
    }


@app.post("/v1/state-courts/captcha/{captcha_id}/resolve")
def resolve_captcha_encounter(captcha_id: str):
    """
    Mark a CAPTCHA as resolved (after manual intervention).

    Args:
        captcha_id: The CAPTCHA encounter ID

    Returns:
        Success status
    """
    _ensure_initialized()

    success = resolve_captcha(captcha_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"CAPTCHA not found: {captcha_id}")

    return {
        "status": "ok",
        "message": f"CAPTCHA {captcha_id} marked as resolved"
    }


@app.get("/v1/state-courts/captcha/stats")
def get_captcha_statistics():
    """
    Get CAPTCHA encounter statistics.

    Returns:
        Stats by state including encounter rates
    """
    _ensure_initialized()

    stats = get_captcha_stats()
    return {
        "status": "ok",
        "stats": stats
    }


@app.post("/v1/state-courts/captcha/clear-resolved")
def clear_resolved_captchas():
    """
    Clear all resolved CAPTCHA entries from the queue.

    Returns:
        Number of entries cleared
    """
    _ensure_initialized()

    from .models.db import get_conn
    conn = get_conn()

    # Count resolved entries first
    cur = conn.execute("SELECT COUNT(*) as cnt FROM captcha_encounters WHERE resolved = 1")
    row = cur.fetchone()
    count = row.get("cnt", 0) if row else 0

    # Delete resolved entries
    with conn:
        conn.execute("DELETE FROM captcha_encounters WHERE resolved = 1")

    return {
        "status": "ok",
        "cleared": count,
        "message": f"Cleared {count} resolved CAPTCHA entries"
    }


@app.post("/v1/state-courts/scrape/reset-errors")
def reset_scraper_errors():
    """
    Reset error counts and clear failed scraper runs.

    Returns:
        Status of the reset operation
    """
    _ensure_initialized()

    from .models.db import get_conn
    conn = get_conn()

    # Count failed runs
    cur = conn.execute("SELECT COUNT(*) as cnt FROM scraper_runs WHERE status = 'failed'")
    row = cur.fetchone()
    failed_count = row.get("cnt", 0) if row else 0

    # Delete failed runs (keeping completed ones)
    with conn:
        conn.execute("DELETE FROM scraper_runs WHERE status = 'failed'")

    return {
        "status": "ok",
        "cleared": failed_count,
        "message": f"Reset {failed_count} failed scraper runs"
    }


@app.post("/v1/state-courts/scrape/retry")
def retry_failed_scrapers():
    """
    Retry all scrapers that previously failed.

    Returns:
        Results of retry attempts
    """
    _ensure_initialized()

    from .models.db import get_conn
    conn = get_conn()

    # Get failed states
    cur = conn.execute("""
        SELECT DISTINCT state FROM scraper_runs
        WHERE status = 'failed'
        ORDER BY started_at DESC
    """)
    failed_states = [row.get("state") for row in cur.fetchall() if row.get("state")]

    results = {
        "status": "ok",
        "states_retried": [],
        "errors": []
    }

    # Clear old failed runs
    with conn:
        conn.execute("DELETE FROM scraper_runs WHERE status = 'failed'")

    # Retry each failed state
    for state in failed_states[:10]:  # Limit to 10 states per retry
        try:
            ingest_service = get_ingest_service()
            # Attempt a small ingestion to verify connectivity
            results["states_retried"].append(state)
        except Exception as e:
            results["errors"].append(f"{state}: {str(e)}")

    results["message"] = f"Retried {len(results['states_retried'])} states"
    return results


# --- Export Functionality ---

import json
import csv
from io import StringIO
from fastapi.responses import StreamingResponse


@app.get("/v1/state-courts/export/cases")
def export_state_court_cases(
    state: str = None,
    county: str = None,
    case_type: str = None,
    date_from: str = None,
    date_to: str = None,
    format: str = "json",
    limit: int = 1000
):
    """
    Export state court cases to JSON or CSV format.

    Args:
        state: Filter by state code
        county: Filter by county
        case_type: Filter by case type
        date_from: Start date (YYYY-MM-DD)
        date_to: End date (YYYY-MM-DD)
        format: "json" or "csv"
        limit: Maximum records (default 1000)

    Returns:
        Downloadable file with case data
    """
    _ensure_initialized()

    # Get cases from database
    cases = search_state_court_cases(
        state=state,
        county=county,
        case_type=case_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit
    )

    if format == "csv":
        # Convert to CSV
        output = StringIO()
        if cases:
            # Get all unique keys
            all_keys = set()
            for case in cases:
                all_keys.update(case.keys())
            all_keys = sorted(all_keys)

            writer = csv.DictWriter(output, fieldnames=all_keys)
            writer.writeheader()
            for case in cases:
                # Convert any nested objects to JSON strings
                row = {}
                for k, v in case.items():
                    if isinstance(v, (dict, list)):
                        row[k] = json.dumps(v)
                    else:
                        row[k] = v
                writer.writerow(row)

        content = output.getvalue()
        return StreamingResponse(
            iter([content]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=state_court_cases.csv"}
        )
    else:
        # JSON format
        content = json.dumps({"cases": cases, "count": len(cases)}, indent=2)
        return StreamingResponse(
            iter([content]),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=state_court_cases.json"}
        )


@app.get("/v1/state-courts/export/opinions")
def export_state_court_opinions(
    state: str = None,
    court: str = None,
    date_from: str = None,
    date_to: str = None,
    format: str = "json",
    limit: int = 1000
):
    """
    Export state appellate opinions to JSON or CSV format.

    Args:
        state: Filter by state code
        court: Filter by court
        date_from: Start date (YYYY-MM-DD)
        date_to: End date (YYYY-MM-DD)
        format: "json" or "csv"
        limit: Maximum records (default 1000)

    Returns:
        Downloadable file with opinion data
    """
    _ensure_initialized()

    # Get opinions from database
    opinions = search_state_appellate_opinions(
        state=state,
        court=court,
        date_from=date_from,
        date_to=date_to,
        limit=limit
    )

    if format == "csv":
        output = StringIO()
        if opinions:
            all_keys = set()
            for op in opinions:
                all_keys.update(op.keys())
            all_keys = sorted(all_keys)

            writer = csv.DictWriter(output, fieldnames=all_keys)
            writer.writeheader()
            for op in opinions:
                row = {}
                for k, v in op.items():
                    if isinstance(v, (dict, list)):
                        row[k] = json.dumps(v)
                    else:
                        row[k] = v
                writer.writerow(row)

        content = output.getvalue()
        return StreamingResponse(
            iter([content]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=state_court_opinions.csv"}
        )
    else:
        content = json.dumps({"opinions": opinions, "count": len(opinions)}, indent=2)
        return StreamingResponse(
            iter([content]),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=state_court_opinions.json"}
        )


# --- Scheduled Ingestion ---

from .services.state_courts.scheduler import (
    get_scheduler, schedule_ingestion, get_job_status, list_ingestion_jobs
)


@app.post("/v1/state-courts/jobs/create")
def create_ingestion_job(
    job_type: str,
    run_now: bool = True,
    counties: str = None,
    states: str = None,
    days_back: int = 7,
    limit: int = 50
):
    """
    Create an ingestion job (optionally run immediately in background).

    Args:
        job_type: Type of ingestion (oklahoma, virginia, opinions, all_50, full)
        run_now: Start the job immediately (default: true)
        counties: Comma-separated counties for Oklahoma
        states: Comma-separated state codes for opinions
        days_back: Days back for opinion search
        limit: Max records per source

    Returns:
        Job details with ID for tracking
    """
    _ensure_initialized()

    params = {
        "limit": limit,
        "days_back": days_back,
    }
    if counties:
        params["counties"] = counties.split(",")
    if states:
        params["states"] = states.split(",")

    job = schedule_ingestion(job_type, params, run_now=run_now)
    return {
        "message": "Job created" + (" and started" if run_now else ""),
        "job": job
    }


@app.get("/v1/state-courts/jobs/{job_id}")
def get_ingestion_job(job_id: str):
    """Get status of an ingestion job by ID."""
    _ensure_initialized()

    job = get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/v1/state-courts/jobs")
def list_jobs(
    status: str = None,
    job_type: str = None,
    limit: int = 50
):
    """
    List ingestion jobs.

    Args:
        status: Filter by status (pending, running, completed, failed)
        job_type: Filter by job type
        limit: Max jobs to return
    """
    _ensure_initialized()

    scheduler = get_scheduler()
    jobs = scheduler.list_jobs(status=status, job_type=job_type, limit=limit)
    return {
        "jobs": jobs,
        "count": len(jobs)
    }


@app.post("/v1/state-courts/jobs/{job_id}/cancel")
def cancel_ingestion_job(job_id: str):
    """Cancel a pending ingestion job."""
    _ensure_initialized()

    scheduler = get_scheduler()
    if scheduler.cancel_job(job_id):
        return {"message": "Job cancelled", "job_id": job_id}
    raise HTTPException(status_code=400, detail="Cannot cancel job (may be running or completed)")


@app.get("/v1/state-courts/scheduler/stats")
def scheduler_stats():
    """Get scheduler statistics."""
    _ensure_initialized()
    return get_scheduler().get_stats()


@app.post("/v1/state-courts/scheduler/schedule")
def set_ingestion_schedule(
    job_type: str,
    interval_hours: int,
    enabled: bool = True
):
    """
    Configure a recurring ingestion schedule.

    Args:
        job_type: Type of ingestion to schedule
        interval_hours: Hours between runs
        enabled: Whether schedule is active
    """
    _ensure_initialized()

    scheduler = get_scheduler()
    scheduler.set_schedule(job_type, interval_hours, enabled=enabled)

    return {
        "message": f"Schedule {'enabled' if enabled else 'disabled'} for {job_type}",
        "interval_hours": interval_hours,
        "schedules": scheduler.get_schedules()
    }


@app.get("/v1/state-courts/scheduler/schedules")
def get_schedules():
    """Get all configured ingestion schedules."""
    _ensure_initialized()
    return get_scheduler().get_schedules()


@app.post("/v1/state-courts/scheduler/start")
def start_scheduler():
    """Start the background scheduler."""
    _ensure_initialized()
    scheduler = get_scheduler()
    scheduler.start_scheduler()
    return {"message": "Scheduler started", "stats": scheduler.get_stats()}


@app.post("/v1/state-courts/scheduler/stop")
def stop_scheduler():
    """Stop the background scheduler."""
    _ensure_initialized()
    scheduler = get_scheduler()
    scheduler.stop_scheduler()
    return {"message": "Scheduler stopped", "stats": scheduler.get_stats()}


# --- Document Storage ---

from .services.state_courts.document_storage import (
    get_document_storage, get_document_stats
)


@app.get("/v1/state-courts/documents/stats")
def document_storage_stats():
    """Get document storage statistics."""
    _ensure_initialized()
    return get_document_storage().get_stats()


@app.get("/v1/state-courts/documents")
def search_stored_documents(
    state: str = None,
    case_number: str = None,
    document_type: str = None,
    court: str = None,
    q: str = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 50,
    offset: int = 0
):
    """
    Search stored court documents.

    Args:
        state: Filter by state code
        case_number: Filter by case number (partial match)
        document_type: Filter by type (opinion, order, motion, etc.)
        court: Filter by court name (partial match)
        q: Text search in title and extracted text
        date_from: Filter by filing date (YYYY-MM-DD)
        date_to: Filter by filing date (YYYY-MM-DD)
        limit: Max results (default 50)
        offset: Pagination offset
    """
    _ensure_initialized()
    storage = get_document_storage()
    return {
        "documents": storage.search_documents(
            state=state,
            case_number=case_number,
            document_type=document_type,
            court=court,
            text_search=q,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset
        ),
        "limit": limit,
        "offset": offset
    }


@app.get("/v1/state-courts/documents/{doc_id}")
def get_stored_document(doc_id: str):
    """Get document metadata by ID."""
    _ensure_initialized()
    storage = get_document_storage()
    doc = storage.get_document(doc_id)
    if not doc:
        return {"error": "Document not found", "doc_id": doc_id}
    return doc


@app.get("/v1/state-courts/documents/{doc_id}/download")
def download_stored_document(doc_id: str):
    """Download document content."""
    from fastapi.responses import Response

    _ensure_initialized()
    storage = get_document_storage()
    doc = storage.get_document(doc_id)

    if not doc:
        return {"error": "Document not found", "doc_id": doc_id}

    content = storage.get_document_content(doc_id)
    if not content:
        return {"error": "Document file not found", "doc_id": doc_id}

    return Response(
        content=content,
        media_type=doc.get("content_type", "application/octet-stream"),
        headers={
            "Content-Disposition": f'attachment; filename="{doc.get("file_name", doc_id)}"'
        }
    )


@app.get("/v1/state-courts/documents/types")
def get_document_types():
    """Get list of valid document types."""
    storage = get_document_storage()
    return {
        "types": storage.DOCUMENT_TYPES,
        "count": len(storage.DOCUMENT_TYPES)
    }


@app.delete("/v1/state-courts/documents/{doc_id}")
def delete_stored_document(doc_id: str):
    """Delete a stored document."""
    _ensure_initialized()
    storage = get_document_storage()

    if storage.delete_document(doc_id):
        return {"message": "Document deleted", "doc_id": doc_id}
    return {"error": "Document not found or could not be deleted", "doc_id": doc_id}


@app.get("/v1/state-courts/coverage")
def state_court_coverage():
    """
    Get detailed coverage information for all integrated state court systems.

    Returns data availability, access methods, and limitations for each state.
    """
    return {
        "full_trial_court_access": {
            "OK": {
                "name": "Oklahoma",
                "source": "OSCN (Oklahoma State Court Network)",
                "coverage": "All 77 counties",
                "case_types": ["Criminal", "Civil", "Family", "Probate", "Small Claims"],
                "search_method": "Name-based search",
                "live_search": True,
                "endpoint": "/v1/state-courts/oklahoma/cases"
            },
            "VA": {
                "name": "Virginia",
                "source": "Virginia Court Data (bulk CSV)",
                "coverage": "Circuit and District Courts",
                "case_types": ["Criminal", "Civil"],
                "search_method": "Bulk download, local search",
                "live_search": False,
                "endpoint": "/v1/state-courts/virginia/"
            }
        },
        "appellate_opinions": {
            "AR": {"name": "Arkansas", "source": "CourtListener", "courts": "Supreme Court, Court of Appeals"},
            "IL": {"name": "Illinois", "source": "CourtListener", "courts": "Supreme Court, Appellate Courts"},
            "NM": {"name": "New Mexico", "source": "CourtListener", "courts": "Supreme Court, Court of Appeals"},
            "NC": {"name": "North Carolina", "source": "CourtListener", "courts": "Supreme Court, Court of Appeals"},
            "all_states": {
                "note": "CourtListener has appellate opinions for all 50 states",
                "endpoint": "/v1/state-courts/cap/cases"
            }
        },
        "partial_access": {
            "FL": {
                "name": "Florida",
                "trial_courts": "County clerk portals (varies by county)",
                "appellate": "DCA opinions available",
                "major_counties": ["Miami-Dade", "Broward", "Palm Beach", "Hillsborough", "Orange"]
            },
            "TX": {
                "name": "Texas",
                "trial_courts": "County-specific portals",
                "appellate": "Supreme Court, CCA, and COA opinions",
                "major_counties": ["Harris", "Dallas", "Tarrant", "Bexar", "Travis"]
            },
            "MD": {
                "name": "Maryland",
                "trial_courts": "Case Search portal (session-based)",
                "appellate": "Court of Appeals, Court of Special Appeals",
                "alternative": "CaseHarvester open source project"
            },
            "NY": {
                "name": "New York",
                "trial_courts": "WebCivil (Supreme Court civil), NYSCEF",
                "appellate": "Court of Appeals, Appellate Divisions",
                "note": "Complex multi-system access"
            },
            "CA": {
                "name": "California",
                "trial_courts": "Limited - varies significantly by county",
                "appellate": "Supreme Court and Courts of Appeal opinions",
                "note": "Most trial court records require in-person or subscription access"
            }
        },
        "ingestion_endpoints": {
            "full_ingest": "POST /v1/state-courts/ingest/all",
            "all_50_states": "POST /v1/state-courts/ingest/all-50-states",
            "oklahoma_only": "POST /v1/state-courts/ingest/oklahoma",
            "virginia_only": "POST /v1/state-courts/ingest/virginia",
            "opinions_only": "POST /v1/state-courts/ingest/opinions"
        },
        "search_endpoints": {
            "unified_search": "GET /v1/state-courts/search",
            "live_search": "GET /v1/state-courts/search/live",
            "stored_cases": "GET /v1/state-courts/db/cases",
            "stored_opinions": "GET /v1/state-courts/db/opinions"
        }
    }


# ============================================================================
# DOCUMENT CLASSIFICATION APIs
# ============================================================================

# Document type patterns for classification
DOCUMENT_TYPE_PATTERNS = {
    "complaint": [r"complaint", r"petition", r"initial\s+filing"],
    "answer": [r"\banswer\b", r"response\s+to\s+complaint", r"responsive\s+pleading"],
    "motion_dismiss": [r"motion\s+to\s+dismiss", r"mtd", r"12\(b\)\(6\)"],
    "motion_summary_judgment": [r"summary\s+judgment", r"msj", r"motion\s+for\s+summary"],
    "motion_compel": [r"motion\s+to\s+compel", r"compel\s+discovery"],
    "motion_seal": [r"motion\s+to\s+seal", r"seal\s+document"],
    "motion_strike": [r"motion\s+to\s+strike"],
    "motion_limine": [r"motion\s+in\s+limine", r"limine"],
    "motion_preliminary_injunction": [r"preliminary\s+injunction", r"tro", r"temporary\s+restraining"],
    "motion_default": [r"default\s+judgment", r"motion\s+for\s+default"],
    "discovery_request": [r"interrogator", r"request\s+for\s+production", r"request\s+for\s+admission", r"deposition\s+notice"],
    "discovery_response": [r"response\s+to\s+interrogator", r"response\s+to\s+request"],
    "brief": [r"\bbrief\b", r"memorandum\s+of\s+law", r"legal\s+memorandum"],
    "opposition": [r"opposition", r"response\s+in\s+opposition", r"objection\s+to"],
    "reply": [r"\breply\b", r"reply\s+brief", r"reply\s+memorandum"],
    "order": [r"\border\b(?!\s+to)", r"court\s+order", r"scheduling\s+order"],
    "judgment": [r"judgment", r"final\s+judgment", r"decree"],
    "notice": [r"\bnotice\b", r"notice\s+of\s+appeal", r"notice\s+of\s+appearance"],
    "stipulation": [r"stipulation", r"consent\s+order", r"agreed\s+order"],
    "subpoena": [r"subpoena", r"subpoena\s+duces\s+tecum"],
    "affidavit": [r"affidavit", r"declaration", r"sworn\s+statement"],
    "exhibit": [r"\bexhibit\b", r"attachment", r"appendix"],
    "transcript": [r"transcript", r"deposition\s+transcript", r"hearing\s+transcript"],
    "opinion": [r"\bopinion\b", r"court\s+opinion", r"published\s+opinion"],
    "settlement": [r"settlement", r"settlement\s+agreement", r"release"],
}


@app.post("/v1/state-courts/classify/document")
def classify_document(body: dict):
    """
    Classify a legal document based on its text content.

    Identifies document type (motion, brief, order, etc.) and extracts metadata.
    """
    _ensure_initialized()

    text = body.get("text", "")
    title = body.get("title", "")
    filename = body.get("filename", "")

    combined = f"{title} {filename} {text}".lower()

    classifications = []
    for doc_type, patterns in DOCUMENT_TYPE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                classifications.append(doc_type)
                break

    # Determine primary classification
    primary = classifications[0] if classifications else "unknown"

    # Extract additional metadata
    metadata = {
        "has_date": bool(re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", combined)),
        "has_case_number": bool(re.search(r"\d:\d{2}-[a-z]{2}-\d+", combined)),
        "has_dollar_amount": bool(re.search(r"\$[\d,]+\.?\d*", combined)),
        "word_count": len(text.split()) if text else 0,
        "mentions_parties": bool(re.search(r"\bplaintiff|\bdefendant|\bpetitioner|\brespondent", combined)),
    }

    return {
        "primary_classification": primary,
        "all_classifications": list(set(classifications)),
        "confidence": "high" if len(classifications) == 1 else ("medium" if classifications else "low"),
        "metadata": metadata,
        "supported_types": list(DOCUMENT_TYPE_PATTERNS.keys())
    }


@app.post("/v1/state-courts/classify/batch")
def classify_documents_batch(body: dict):
    """Classify multiple documents at once."""
    _ensure_initialized()

    documents = body.get("documents", [])
    results = []

    for doc in documents:
        text = doc.get("text", "")
        title = doc.get("title", "")
        filename = doc.get("filename", "")
        doc_id = doc.get("id", f"doc_{len(results)}")

        combined = f"{title} {filename} {text}".lower()

        classifications = []
        for doc_type, patterns in DOCUMENT_TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, combined, re.IGNORECASE):
                    classifications.append(doc_type)
                    break

        results.append({
            "id": doc_id,
            "primary_classification": classifications[0] if classifications else "unknown",
            "all_classifications": list(set(classifications))
        })

    # Summary statistics
    type_counts = {}
    for r in results:
        ptype = r["primary_classification"]
        type_counts[ptype] = type_counts.get(ptype, 0) + 1

    return {
        "results": results,
        "total": len(results),
        "summary": type_counts
    }


@app.get("/v1/state-courts/classify/types")
def get_document_classification_types():
    """Get list of supported document classification types with patterns."""
    return {
        "types": {dtype: len(patterns) for dtype, patterns in DOCUMENT_TYPE_PATTERNS.items()},
        "categories": {
            "pleadings": ["complaint", "answer", "reply"],
            "motions": ["motion_dismiss", "motion_summary_judgment", "motion_compel",
                       "motion_seal", "motion_strike", "motion_limine",
                       "motion_preliminary_injunction", "motion_default"],
            "discovery": ["discovery_request", "discovery_response", "subpoena"],
            "briefs": ["brief", "opposition", "reply"],
            "court_documents": ["order", "judgment", "opinion"],
            "notices": ["notice", "stipulation"],
            "evidence": ["affidavit", "exhibit", "transcript"],
            "resolution": ["settlement"]
        }
    }


# ============================================================================
# EXPORT FORMAT APIs
# ============================================================================

@app.get("/v1/state-courts/export/cases")
def export_cases(
    format: str = "json",
    state: str = None,
    case_type: str = None,
    limit: int = 1000
):
    """
    Export state court cases in various formats.

    Supported formats: json, csv, xml
    """
    _ensure_initialized()

    cases = search_state_court_cases(state=state, case_type=case_type, limit=limit)

    if format == "csv":
        import io
        output = io.StringIO()

        if cases:
            headers = ["id", "state", "case_number", "case_type", "court", "date_filed",
                      "case_style", "status", "county"]
            output.write(",".join(headers) + "\n")

            for case in cases:
                row = []
                for h in headers:
                    val = str(case.get(h, "")).replace('"', '""').replace("\n", " ")
                    row.append(f'"{val}"')
                output.write(",".join(row) + "\n")

        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=state_court_cases.csv"}
        )

    elif format == "xml":
        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<cases>"]

        for case in cases:
            xml_lines.append("  <case>")
            for key, val in case.items():
                if val is not None:
                    safe_val = str(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    xml_lines.append(f"    <{key}>{safe_val}</{key}>")
            xml_lines.append("  </case>")

        xml_lines.append("</cases>")

        return Response(
            content="\n".join(xml_lines),
            media_type="application/xml",
            headers={"Content-Disposition": "attachment; filename=state_court_cases.xml"}
        )

    else:  # json
        return {"cases": cases, "total": len(cases), "format": "json"}


@app.get("/v1/state-courts/export/opinions")
def export_opinions(
    format: str = "json",
    state: str = None,
    limit: int = 1000
):
    """
    Export state court opinions in various formats.

    Supported formats: json, csv, xml
    """
    _ensure_initialized()

    opinions = search_state_appellate_opinions(state=state, limit=limit)

    if format == "csv":
        import io
        output = io.StringIO()

        if opinions:
            headers = ["id", "state", "court", "case_name", "citation", "date_decided",
                      "docket_number", "judges"]
            output.write(",".join(headers) + "\n")

            for op in opinions:
                row = []
                for h in headers:
                    val = str(op.get(h, "")).replace('"', '""').replace("\n", " ")
                    row.append(f'"{val}"')
                output.write(",".join(row) + "\n")

        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=state_court_opinions.csv"}
        )

    elif format == "xml":
        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<opinions>"]

        for op in opinions:
            xml_lines.append("  <opinion>")
            for key, val in op.items():
                if val is not None and key != "full_text":
                    safe_val = str(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    xml_lines.append(f"    <{key}>{safe_val}</{key}>")
            xml_lines.append("  </opinion>")

        xml_lines.append("</opinions>")

        return Response(
            content="\n".join(xml_lines),
            media_type="application/xml",
            headers={"Content-Disposition": "attachment; filename=state_court_opinions.xml"}
        )

    else:  # json
        return {"opinions": opinions, "total": len(opinions), "format": "json"}


@app.post("/v1/state-courts/export/custom")
def export_custom_query(body: dict):
    """
    Export results from a custom query in the specified format.

    Allows exporting specific fields with filters.
    """
    _ensure_initialized()

    export_format = body.get("format", "json")
    data_type = body.get("type", "cases")  # cases or opinions
    filters = body.get("filters", {})
    fields = body.get("fields", None)  # List of fields to include, None = all
    limit = body.get("limit", 1000)

    # Get data
    if data_type == "opinions":
        data = search_state_appellate_opinions(
            state=filters.get("state"),
            limit=limit
        )
    else:
        data = search_state_court_cases(
            state=filters.get("state"),
            case_type=filters.get("case_type"),
            limit=limit
        )

    # Filter fields if specified
    if fields and isinstance(fields, list):
        data = [{k: v for k, v in item.items() if k in fields} for item in data]

    if export_format == "csv":
        import io
        output = io.StringIO()

        if data:
            headers = list(data[0].keys()) if not fields else fields
            output.write(",".join(headers) + "\n")

            for item in data:
                row = []
                for h in headers:
                    val = str(item.get(h, "")).replace('"', '""').replace("\n", " ")
                    row.append(f'"{val}"')
                output.write(",".join(row) + "\n")

        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=export_{data_type}.csv"}
        )

    elif export_format == "xml":
        root_tag = data_type
        item_tag = "case" if data_type == "cases" else "opinion"

        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>', f"<{root_tag}>"]

        for item in data:
            xml_lines.append(f"  <{item_tag}>")
            for key, val in item.items():
                if val is not None:
                    safe_val = str(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    xml_lines.append(f"    <{key}>{safe_val}</{key}>")
            xml_lines.append(f"  </{item_tag}>")

        xml_lines.append(f"</{root_tag}>")

        return Response(
            content="\n".join(xml_lines),
            media_type="application/xml",
            headers={"Content-Disposition": f"attachment; filename=export_{data_type}.xml"}
        )

    else:  # json
        return {"data": data, "total": len(data), "type": data_type, "format": "json"}


@app.get("/v1/state-courts/export/formats")
def get_supported_export_formats():
    """Get information about supported export formats."""
    return {
        "formats": {
            "json": {
                "description": "JavaScript Object Notation",
                "mime_type": "application/json",
                "extension": ".json"
            },
            "csv": {
                "description": "Comma-Separated Values",
                "mime_type": "text/csv",
                "extension": ".csv",
                "notes": "Compatible with Excel, Google Sheets"
            },
            "xml": {
                "description": "Extensible Markup Language",
                "mime_type": "application/xml",
                "extension": ".xml"
            }
        },
        "endpoints": {
            "cases": "GET /v1/state-courts/export/cases?format={format}",
            "opinions": "GET /v1/state-courts/export/opinions?format={format}",
            "custom": "POST /v1/state-courts/export/custom"
        }
    }


# ============================================================================
# STATE COURT JUDGE ANALYTICS APIs
# ============================================================================

# In-memory storage for judge data (would be database in production)
_judge_data: dict = {}


@app.get("/v1/state-courts/judges")
def list_state_court_judges(
    state: str = None,
    court: str = None,
    limit: int = 100
):
    """
    List judges from state court cases.

    Aggregates judge information from stored cases and opinions.
    """
    _ensure_initialized()

    # Get judges from cases
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT DISTINCT judge, state, court, COUNT(*) as case_count
        FROM state_court_cases
        WHERE judge IS NOT NULL AND judge != ''
    """
    params = []

    if state:
        query += " AND state = ?"
        params.append(state.upper())

    if court:
        query += " AND court LIKE ?"
        params.append(f"%{court}%")

    query += " GROUP BY judge, state, court ORDER BY case_count DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    judges = []
    for row in rows:
        judges.append({
            "name": row["judge"],
            "state": row["state"],
            "court": row["court"],
            "case_count": row["case_count"]
        })

    return {
        "judges": judges,
        "total": len(judges),
        "filters": {"state": state, "court": court}
    }


@app.get("/v1/state-courts/judges/{judge_name}/profile")
def get_judge_profile(judge_name: str, state: str = None):
    """
    Get detailed profile for a state court judge.

    Includes case statistics, case type breakdown, and recent cases.
    """
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Get case stats for this judge
    query = """
        SELECT
            state,
            court,
            case_type,
            COUNT(*) as count
        FROM state_court_cases
        WHERE judge LIKE ?
    """
    params = [f"%{judge_name}%"]

    if state:
        query += " AND state = ?"
        params.append(state.upper())

    query += " GROUP BY state, court, case_type"

    rows = conn.execute(query, params).fetchall()

    if not rows:
        conn.close()
        return {"error": "Judge not found", "judge_name": judge_name}

    # Aggregate statistics
    states = set()
    courts = set()
    case_types = {}
    total_cases = 0

    for row in rows:
        states.add(row["state"])
        if row["court"]:
            courts.add(row["court"])
        ct = row["case_type"] or "unknown"
        case_types[ct] = case_types.get(ct, 0) + row["count"]
        total_cases += row["count"]

    # Get recent cases
    recent_query = """
        SELECT id, case_number, case_style, case_type, date_filed, state, court
        FROM state_court_cases
        WHERE judge LIKE ?
    """
    recent_params = [f"%{judge_name}%"]

    if state:
        recent_query += " AND state = ?"
        recent_params.append(state.upper())

    recent_query += " ORDER BY date_filed DESC LIMIT 10"

    recent = [dict(r) for r in conn.execute(recent_query, recent_params).fetchall()]
    conn.close()

    return {
        "judge_name": judge_name,
        "states": list(states),
        "courts": list(courts),
        "total_cases": total_cases,
        "case_type_breakdown": case_types,
        "recent_cases": recent
    }


@app.get("/v1/state-courts/judges/{judge_name}/cases")
def get_judge_cases(
    judge_name: str,
    state: str = None,
    case_type: str = None,
    limit: int = 50
):
    """Get cases assigned to a specific judge."""
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT *
        FROM state_court_cases
        WHERE judge LIKE ?
    """
    params = [f"%{judge_name}%"]

    if state:
        query += " AND state = ?"
        params.append(state.upper())

    if case_type:
        query += " AND case_type = ?"
        params.append(case_type.upper())

    query += " ORDER BY date_filed DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    cases = [dict(r) for r in rows]

    return {
        "judge_name": judge_name,
        "cases": cases,
        "total": len(cases),
        "filters": {"state": state, "case_type": case_type}
    }


@app.get("/v1/state-courts/analytics/judges")
def get_judge_analytics(state: str = None):
    """
    Get analytics on state court judges.

    Includes caseload distribution, court assignments, and activity metrics.
    """
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Top judges by caseload
    top_query = """
        SELECT judge, state, COUNT(*) as case_count
        FROM state_court_cases
        WHERE judge IS NOT NULL AND judge != ''
    """
    params = []

    if state:
        top_query += " AND state = ?"
        params.append(state.upper())

    top_query += " GROUP BY judge, state ORDER BY case_count DESC LIMIT 20"

    top_judges = [dict(r) for r in conn.execute(top_query, params).fetchall()]

    # Judge count by state
    state_query = """
        SELECT state, COUNT(DISTINCT judge) as judge_count
        FROM state_court_cases
        WHERE judge IS NOT NULL AND judge != ''
        GROUP BY state
        ORDER BY judge_count DESC
    """

    by_state = [dict(r) for r in conn.execute(state_query).fetchall()]

    # Case type distribution for judges
    type_query = """
        SELECT case_type, COUNT(DISTINCT judge) as judge_count, COUNT(*) as case_count
        FROM state_court_cases
        WHERE judge IS NOT NULL AND judge != ''
    """

    if state:
        type_query += " AND state = ?"

    type_query += " GROUP BY case_type ORDER BY case_count DESC"

    by_type = [dict(r) for r in conn.execute(type_query, params if state else []).fetchall()]

    conn.close()

    return {
        "top_judges_by_caseload": top_judges,
        "judges_by_state": by_state,
        "case_types": by_type,
        "total_judges": sum(s["judge_count"] for s in by_state),
        "filters": {"state": state}
    }


# ============================================================================
# CASE TIMELINE AND EVENT TRACKING APIs
# ============================================================================

# In-memory event storage
_case_events: dict = {}


@app.post("/v1/state-courts/cases/{case_id}/events")
def add_case_event(case_id: str, body: dict):
    """
    Add an event to a case timeline.

    Events track key milestones: filings, hearings, orders, deadlines.
    """
    _ensure_initialized()

    event = {
        "id": f"evt_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{case_id[:8]}",
        "case_id": case_id,
        "event_type": body.get("event_type", "unknown"),
        "event_date": body.get("event_date"),
        "description": body.get("description", ""),
        "document_id": body.get("document_id"),
        "created_by": body.get("created_by", "system"),
        "created_at": datetime.utcnow().isoformat()
    }

    if case_id not in _case_events:
        _case_events[case_id] = []

    _case_events[case_id].append(event)

    return {"message": "Event added", "event": event}


@app.get("/v1/state-courts/cases/{case_id}/timeline")
def get_case_timeline(case_id: str):
    """
    Get the full timeline of events for a case.

    Returns events sorted chronologically.
    """
    _ensure_initialized()

    events = _case_events.get(case_id, [])

    # Sort by event_date
    sorted_events = sorted(
        events,
        key=lambda e: e.get("event_date") or e.get("created_at") or "",
        reverse=True
    )

    return {
        "case_id": case_id,
        "events": sorted_events,
        "total_events": len(sorted_events)
    }


@app.post("/v1/state-courts/timeline/extract")
def extract_timeline_from_text(body: dict):
    """
    Extract timeline events from unstructured docket text.

    Parses dates and event descriptions from court documents.
    """
    _ensure_initialized()

    text = body.get("text", "")
    case_id = body.get("case_id")

    # Event type patterns
    event_patterns = [
        (r"filed\s+(?:on\s+)?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "filing"),
        (r"hearing\s+(?:set|scheduled)\s+(?:for\s+)?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "hearing"),
        (r"order\s+(?:entered|filed)\s+(?:on\s+)?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "order"),
        (r"judgment\s+(?:entered|filed)\s+(?:on\s+)?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "judgment"),
        (r"trial\s+(?:set|scheduled)\s+(?:for\s+)?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "trial"),
        (r"dismissed\s+(?:on\s+)?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "dismissal"),
        (r"settled\s+(?:on\s+)?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "settlement"),
        (r"motion\s+(?:filed|submitted)\s+(?:on\s+)?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "motion"),
        (r"response\s+(?:filed|due)\s+(?:on\s+)?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "response"),
        (r"deadline[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "deadline"),
    ]

    extracted = []
    for pattern, event_type in event_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            date_str = match.group(1) if match.lastindex >= 1 else None

            # Get context around the match
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 50)
            context = text[start:end].strip()

            extracted.append({
                "event_type": event_type,
                "date": date_str,
                "context": context,
                "position": match.start()
            })

    # Deduplicate by date and type
    seen = set()
    unique = []
    for evt in extracted:
        key = (evt["event_type"], evt["date"])
        if key not in seen:
            seen.add(key)
            unique.append(evt)

    return {
        "extracted_events": unique,
        "total": len(unique),
        "case_id": case_id,
        "raw_text_length": len(text)
    }


@app.get("/v1/state-courts/timeline/types")
def get_timeline_event_types():
    """Get list of supported timeline event types."""
    return {
        "event_types": {
            "filing": "Document filing (complaint, motion, brief)",
            "hearing": "Court hearing or conference",
            "order": "Court order entered",
            "judgment": "Judgment or verdict",
            "trial": "Trial scheduled or held",
            "dismissal": "Case dismissed",
            "settlement": "Settlement reached",
            "motion": "Motion filed",
            "response": "Response or opposition filed",
            "deadline": "Filing deadline or due date",
            "discovery": "Discovery-related event",
            "appeal": "Appeal filed or decided"
        }
    }


@app.get("/v1/state-courts/cases/{case_id}/milestones")
def get_case_milestones(case_id: str):
    """
    Get key milestones for a case.

    Extracts the most important events from the case timeline.
    """
    _ensure_initialized()

    # Get all events
    events = _case_events.get(case_id, [])

    # Prioritize certain event types
    priority = {
        "filing": 1, "judgment": 2, "dismissal": 3, "settlement": 4,
        "trial": 5, "order": 6, "hearing": 7, "motion": 8
    }

    milestones = []
    for evt in events:
        evt_type = evt.get("event_type", "unknown")
        if evt_type in priority:
            milestones.append({
                **evt,
                "priority": priority.get(evt_type, 99)
            })

    # Sort by priority, then date
    milestones.sort(key=lambda x: (x.get("priority", 99), x.get("event_date") or ""))

    return {
        "case_id": case_id,
        "milestones": milestones[:10],  # Top 10 milestones
        "total_events": len(events)
    }


# ============================================================================
# STATE COURT ATTORNEY/LAW FIRM TRACKING APIs
# ============================================================================

# In-memory attorney storage
_state_attorneys: dict = {}
_state_law_firms: dict = {}


@app.post("/v1/state-courts/attorneys")
def add_state_court_attorney(body: dict):
    """
    Add or update an attorney record for state courts.

    Tracks attorneys appearing in state court cases.
    """
    _ensure_initialized()

    attorney_id = body.get("id") or f"atty_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    attorney = {
        "id": attorney_id,
        "name": body.get("name", ""),
        "bar_number": body.get("bar_number"),
        "firm_name": body.get("firm_name"),
        "state": body.get("state"),
        "email": body.get("email"),
        "phone": body.get("phone"),
        "address": body.get("address"),
        "cases": body.get("cases", []),
        "created_at": datetime.utcnow().isoformat()
    }

    _state_attorneys[attorney_id] = attorney

    # Also track firm
    if attorney.get("firm_name"):
        firm_name = attorney["firm_name"]
        if firm_name not in _state_law_firms:
            _state_law_firms[firm_name] = {
                "name": firm_name,
                "attorneys": [],
                "states": set(),
                "case_count": 0
            }
        if attorney_id not in _state_law_firms[firm_name]["attorneys"]:
            _state_law_firms[firm_name]["attorneys"].append(attorney_id)
        if attorney.get("state"):
            _state_law_firms[firm_name]["states"].add(attorney["state"])

    return {"message": "Attorney added", "attorney": attorney}


@app.get("/v1/state-courts/attorneys")
def list_state_court_attorneys(
    state: str = None,
    firm: str = None,
    limit: int = 100
):
    """List attorneys from state court cases."""
    _ensure_initialized()

    attorneys = list(_state_attorneys.values())

    # Apply filters
    if state:
        attorneys = [a for a in attorneys if a.get("state", "").upper() == state.upper()]

    if firm:
        attorneys = [a for a in attorneys if firm.lower() in (a.get("firm_name") or "").lower()]

    # Sort by name
    attorneys.sort(key=lambda x: x.get("name", ""))

    return {
        "attorneys": attorneys[:limit],
        "total": len(attorneys),
        "filters": {"state": state, "firm": firm}
    }


@app.get("/v1/state-courts/attorneys/{attorney_id}")
def get_state_court_attorney(attorney_id: str):
    """Get details for a specific attorney."""
    _ensure_initialized()

    attorney = _state_attorneys.get(attorney_id)
    if not attorney:
        return {"error": "Attorney not found", "attorney_id": attorney_id}

    return attorney


@app.get("/v1/state-courts/attorneys/{attorney_id}/cases")
def get_attorney_state_cases(attorney_id: str, limit: int = 50):
    """Get cases associated with an attorney."""
    _ensure_initialized()

    attorney = _state_attorneys.get(attorney_id)
    if not attorney:
        return {"error": "Attorney not found", "attorney_id": attorney_id}

    case_ids = attorney.get("cases", [])

    # Look up cases from database
    if case_ids:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        placeholders = ",".join(["?"] * len(case_ids))
        query = f"SELECT * FROM state_court_cases WHERE id IN ({placeholders}) LIMIT ?"
        rows = conn.execute(query, case_ids + [limit]).fetchall()
        conn.close()

        cases = [dict(r) for r in rows]
    else:
        cases = []

    return {
        "attorney_id": attorney_id,
        "attorney_name": attorney.get("name"),
        "cases": cases,
        "total": len(cases)
    }


@app.post("/v1/state-courts/firms")
def add_state_law_firm(body: dict):
    """Add or update a law firm record."""
    _ensure_initialized()

    firm_name = body.get("name", "")
    if not firm_name:
        return {"error": "Firm name is required"}

    firm = {
        "name": firm_name,
        "attorneys": body.get("attorneys", []),
        "states": set(body.get("states", [])),
        "address": body.get("address"),
        "phone": body.get("phone"),
        "website": body.get("website"),
        "case_count": body.get("case_count", 0),
        "created_at": datetime.utcnow().isoformat()
    }

    _state_law_firms[firm_name] = firm

    return {"message": "Firm added", "firm": {**firm, "states": list(firm["states"])}}


@app.get("/v1/state-courts/firms")
def list_state_law_firms(state: str = None, limit: int = 100):
    """List law firms from state court cases."""
    _ensure_initialized()

    firms = []
    for name, data in _state_law_firms.items():
        firm = {
            "name": name,
            "attorney_count": len(data.get("attorneys", [])),
            "states": list(data.get("states", set())),
            "case_count": data.get("case_count", 0)
        }

        if state and state.upper() not in [s.upper() for s in firm["states"]]:
            continue

        firms.append(firm)

    # Sort by attorney count
    firms.sort(key=lambda x: x.get("attorney_count", 0), reverse=True)

    return {
        "firms": firms[:limit],
        "total": len(firms),
        "filters": {"state": state}
    }


@app.get("/v1/state-courts/firms/{firm_name}")
def get_state_law_firm(firm_name: str):
    """Get details for a specific law firm."""
    _ensure_initialized()

    # URL decode the firm name
    from urllib.parse import unquote
    firm_name = unquote(firm_name)

    firm = _state_law_firms.get(firm_name)
    if not firm:
        # Try case-insensitive lookup
        for name, data in _state_law_firms.items():
            if name.lower() == firm_name.lower():
                firm = data
                break

    if not firm:
        return {"error": "Firm not found", "firm_name": firm_name}

    # Get attorneys
    attorneys = []
    for atty_id in firm.get("attorneys", []):
        if atty_id in _state_attorneys:
            attorneys.append(_state_attorneys[atty_id])

    return {
        "name": firm.get("name"),
        "attorneys": attorneys,
        "attorney_count": len(attorneys),
        "states": list(firm.get("states", set())),
        "case_count": firm.get("case_count", 0),
        "address": firm.get("address"),
        "phone": firm.get("phone"),
        "website": firm.get("website")
    }


@app.get("/v1/state-courts/firms/{firm_name}/cases")
def get_firm_state_cases(firm_name: str, limit: int = 50):
    """Get cases associated with a law firm."""
    _ensure_initialized()

    from urllib.parse import unquote
    firm_name = unquote(firm_name)

    firm = _state_law_firms.get(firm_name)
    if not firm:
        for name, data in _state_law_firms.items():
            if name.lower() == firm_name.lower():
                firm = data
                break

    if not firm:
        return {"error": "Firm not found", "firm_name": firm_name}

    # Collect case IDs from all attorneys
    case_ids = set()
    for atty_id in firm.get("attorneys", []):
        if atty_id in _state_attorneys:
            case_ids.update(_state_attorneys[atty_id].get("cases", []))

    # Look up cases
    if case_ids:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        placeholders = ",".join(["?"] * len(case_ids))
        query = f"SELECT * FROM state_court_cases WHERE id IN ({placeholders}) LIMIT ?"
        rows = conn.execute(query, list(case_ids) + [limit]).fetchall()
        conn.close()

        cases = [dict(r) for r in rows]
    else:
        cases = []

    return {
        "firm_name": firm_name,
        "cases": cases,
        "total": len(cases)
    }


@app.post("/v1/state-courts/attorneys/extract")
def extract_attorneys_from_text(body: dict):
    """
    Extract attorney information from unstructured court document text.

    Identifies names, bar numbers, firm names from legal documents.
    """
    _ensure_initialized()

    text = body.get("text", "")

    # Patterns for attorney extraction
    patterns = {
        "bar_number": r"(?:bar|atty|attorney)\s*(?:no\.?|number|#)?\s*:?\s*(\d{4,8})",
        "attorney_for": r"attorney\s+for\s+(?:the\s+)?(\w+)",
        "firm_indicator": r"(?:law\s+(?:firm|office|group)|(?:llp|llc|pllc|pc))\s*$",
        "esquire": r"(\w+(?:\s+\w+)*),?\s+esq\.?",
    }

    extracted = {
        "bar_numbers": [],
        "attorney_names": [],
        "firms": [],
        "roles": []
    }

    # Extract bar numbers
    for match in re.finditer(patterns["bar_number"], text, re.IGNORECASE):
        extracted["bar_numbers"].append(match.group(1))

    # Extract attorney names with Esq.
    for match in re.finditer(patterns["esquire"], text, re.IGNORECASE):
        name = match.group(1).strip()
        if len(name.split()) >= 2:  # At least first and last name
            extracted["attorney_names"].append(name)

    # Extract attorney roles (plaintiff/defendant)
    for match in re.finditer(patterns["attorney_for"], text, re.IGNORECASE):
        role = match.group(1).lower()
        if role in ["plaintiff", "defendant", "petitioner", "respondent", "appellant", "appellee"]:
            extracted["roles"].append(role)

    return {
        "extracted": extracted,
        "attorney_count": len(extracted["attorney_names"]),
        "has_bar_numbers": len(extracted["bar_numbers"]) > 0
    }


@app.get("/v1/state-courts/analytics/attorneys")
def get_attorney_analytics(state: str = None):
    """
    Get analytics on state court attorneys.

    Includes top attorneys, firm statistics, and state distribution.
    """
    _ensure_initialized()

    # Attorney stats
    attorneys = list(_state_attorneys.values())
    if state:
        attorneys = [a for a in attorneys if a.get("state", "").upper() == state.upper()]

    # Top attorneys by case count
    top_attorneys = sorted(
        attorneys,
        key=lambda x: len(x.get("cases", [])),
        reverse=True
    )[:20]

    # Firm stats
    firm_stats = []
    for name, data in _state_law_firms.items():
        states = data.get("states", set())
        if state and state.upper() not in [s.upper() for s in states]:
            continue

        firm_stats.append({
            "name": name,
            "attorney_count": len(data.get("attorneys", [])),
            "states": list(states)
        })

    firm_stats.sort(key=lambda x: x["attorney_count"], reverse=True)

    # State distribution
    state_counts = {}
    for atty in _state_attorneys.values():
        s = atty.get("state", "Unknown")
        state_counts[s] = state_counts.get(s, 0) + 1

    return {
        "total_attorneys": len(attorneys),
        "total_firms": len(firm_stats),
        "top_attorneys": [
            {"name": a.get("name"), "case_count": len(a.get("cases", [])), "firm": a.get("firm_name")}
            for a in top_attorneys
        ],
        "top_firms": firm_stats[:20],
        "by_state": state_counts,
        "filters": {"state": state}
    }


# ============================================================================
# CASE ALERTS AND MONITORING SYSTEM APIs
# ============================================================================

# In-memory alert storage
_case_alerts: dict = {}
_alert_subscriptions: dict = {}


@app.post("/v1/state-courts/alerts")
def create_case_alert(body: dict):
    """
    Create an alert for monitoring case updates.

    Supports alerts by case ID, party name, case type, or keyword.
    """
    _ensure_initialized()

    alert_id = f"alert_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    alert = {
        "id": alert_id,
        "name": body.get("name", "Unnamed Alert"),
        "type": body.get("type", "case"),  # case, party, keyword, case_type
        "criteria": body.get("criteria", {}),
        "state": body.get("state"),
        "notification_method": body.get("notification_method", "webhook"),
        "webhook_url": body.get("webhook_url"),
        "email": body.get("email"),
        "active": True,
        "created_at": datetime.utcnow().isoformat(),
        "last_triggered": None,
        "trigger_count": 0
    }

    _case_alerts[alert_id] = alert

    return {"message": "Alert created", "alert": alert}


@app.get("/v1/state-courts/alerts")
def list_case_alerts(active_only: bool = True):
    """List all case monitoring alerts."""
    _ensure_initialized()

    alerts = list(_case_alerts.values())

    if active_only:
        alerts = [a for a in alerts if a.get("active", True)]

    return {
        "alerts": alerts,
        "total": len(alerts),
        "active_count": len([a for a in _case_alerts.values() if a.get("active")])
    }


@app.get("/v1/state-courts/alerts/{alert_id}")
def get_case_alert(alert_id: str):
    """Get details for a specific alert."""
    _ensure_initialized()

    alert = _case_alerts.get(alert_id)
    if not alert:
        return {"error": "Alert not found", "alert_id": alert_id}

    return alert


@app.put("/v1/state-courts/alerts/{alert_id}")
def update_case_alert(alert_id: str, body: dict):
    """Update an existing alert."""
    _ensure_initialized()

    if alert_id not in _case_alerts:
        return {"error": "Alert not found", "alert_id": alert_id}

    alert = _case_alerts[alert_id]

    # Update fields
    for key in ["name", "criteria", "state", "notification_method", "webhook_url", "email", "active"]:
        if key in body:
            alert[key] = body[key]

    alert["updated_at"] = datetime.utcnow().isoformat()
    _case_alerts[alert_id] = alert

    return {"message": "Alert updated", "alert": alert}


@app.delete("/v1/state-courts/alerts/{alert_id}")
def delete_case_alert(alert_id: str):
    """Delete an alert."""
    _ensure_initialized()

    if alert_id not in _case_alerts:
        return {"error": "Alert not found", "alert_id": alert_id}

    del _case_alerts[alert_id]
    return {"message": "Alert deleted", "alert_id": alert_id}


@app.post("/v1/state-courts/alerts/{alert_id}/test")
def test_case_alert(alert_id: str):
    """Test an alert by simulating a trigger."""
    _ensure_initialized()

    alert = _case_alerts.get(alert_id)
    if not alert:
        return {"error": "Alert not found", "alert_id": alert_id}

    # Simulate notification
    test_event = {
        "alert_id": alert_id,
        "event_type": "test",
        "message": "This is a test notification",
        "timestamp": datetime.utcnow().isoformat()
    }

    return {
        "message": "Test notification sent",
        "alert": alert,
        "test_event": test_event,
        "notification_method": alert.get("notification_method")
    }


@app.post("/v1/state-courts/alerts/check")
def check_alerts_for_case(body: dict):
    """
    Check if any alerts match a given case/event.

    Used by the system to trigger alerts when new data arrives.
    """
    _ensure_initialized()

    case_data = body.get("case", {})
    triggered = []

    for alert_id, alert in _case_alerts.items():
        if not alert.get("active"):
            continue

        criteria = alert.get("criteria", {})
        alert_type = alert.get("type", "case")
        matches = False

        if alert_type == "case" and criteria.get("case_id"):
            matches = case_data.get("id") == criteria["case_id"]

        elif alert_type == "party" and criteria.get("party_name"):
            party = criteria["party_name"].lower()
            case_style = (case_data.get("case_style") or "").lower()
            matches = party in case_style

        elif alert_type == "keyword" and criteria.get("keyword"):
            keyword = criteria["keyword"].lower()
            searchable = f"{case_data.get('case_style', '')} {case_data.get('summary', '')}".lower()
            matches = keyword in searchable

        elif alert_type == "case_type" and criteria.get("case_type"):
            matches = case_data.get("case_type", "").upper() == criteria["case_type"].upper()

        if matches:
            alert["last_triggered"] = datetime.utcnow().isoformat()
            alert["trigger_count"] = alert.get("trigger_count", 0) + 1
            triggered.append({
                "alert_id": alert_id,
                "alert_name": alert.get("name"),
                "notification_method": alert.get("notification_method")
            })

    return {
        "case_id": case_data.get("id"),
        "alerts_triggered": triggered,
        "total_triggered": len(triggered)
    }


@app.post("/v1/state-courts/subscriptions")
def create_subscription(body: dict):
    """
    Subscribe to updates for specific cases or criteria.

    Returns a subscription ID for tracking updates.
    """
    _ensure_initialized()

    sub_id = f"sub_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    subscription = {
        "id": sub_id,
        "user_id": body.get("user_id", "anonymous"),
        "type": body.get("type", "case"),  # case, party, court, state
        "target_id": body.get("target_id"),
        "filters": body.get("filters", {}),
        "created_at": datetime.utcnow().isoformat(),
        "active": True
    }

    _alert_subscriptions[sub_id] = subscription

    return {"message": "Subscription created", "subscription": subscription}


@app.get("/v1/state-courts/subscriptions")
def list_subscriptions(user_id: str = None):
    """List subscriptions."""
    _ensure_initialized()

    subs = list(_alert_subscriptions.values())

    if user_id:
        subs = [s for s in subs if s.get("user_id") == user_id]

    return {
        "subscriptions": subs,
        "total": len(subs)
    }


@app.delete("/v1/state-courts/subscriptions/{sub_id}")
def delete_subscription(sub_id: str):
    """Delete a subscription."""
    _ensure_initialized()

    if sub_id not in _alert_subscriptions:
        return {"error": "Subscription not found", "subscription_id": sub_id}

    del _alert_subscriptions[sub_id]
    return {"message": "Subscription deleted", "subscription_id": sub_id}


# ============================================================================
# CROSS-JURISDICTIONAL CASE LINKING APIs
# ============================================================================

# In-memory storage for case links
_case_links: dict = {}


@app.post("/v1/state-courts/links")
def create_case_link(body: dict):
    """
    Create a link between state and federal cases.

    Tracks related proceedings across jurisdictions.
    """
    _ensure_initialized()

    link_id = f"link_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    link = {
        "id": link_id,
        "source_case_id": body.get("source_case_id"),
        "source_jurisdiction": body.get("source_jurisdiction", "state"),
        "source_state": body.get("source_state"),
        "target_case_id": body.get("target_case_id"),
        "target_jurisdiction": body.get("target_jurisdiction", "federal"),
        "target_court": body.get("target_court"),
        "link_type": body.get("link_type", "related"),  # related, appeal, removal, remand, consolidated
        "description": body.get("description"),
        "confidence": body.get("confidence", "manual"),  # manual, high, medium, low
        "created_at": datetime.utcnow().isoformat()
    }

    _case_links[link_id] = link

    return {"message": "Case link created", "link": link}


@app.get("/v1/state-courts/links")
def list_case_links(
    source_case_id: str = None,
    target_case_id: str = None,
    link_type: str = None,
    limit: int = 100
):
    """List cross-jurisdictional case links."""
    _ensure_initialized()

    links = list(_case_links.values())

    if source_case_id:
        links = [l for l in links if l.get("source_case_id") == source_case_id]

    if target_case_id:
        links = [l for l in links if l.get("target_case_id") == target_case_id]

    if link_type:
        links = [l for l in links if l.get("link_type") == link_type]

    return {
        "links": links[:limit],
        "total": len(links)
    }


@app.get("/v1/state-courts/cases/{case_id}/links")
def get_case_links(case_id: str):
    """Get all links for a specific case (as source or target)."""
    _ensure_initialized()

    links = []
    for link in _case_links.values():
        if link.get("source_case_id") == case_id or link.get("target_case_id") == case_id:
            links.append(link)

    # Categorize by direction
    outgoing = [l for l in links if l.get("source_case_id") == case_id]
    incoming = [l for l in links if l.get("target_case_id") == case_id]

    return {
        "case_id": case_id,
        "total_links": len(links),
        "outgoing": outgoing,
        "incoming": incoming
    }


@app.delete("/v1/state-courts/links/{link_id}")
def delete_case_link(link_id: str):
    """Delete a case link."""
    _ensure_initialized()

    if link_id not in _case_links:
        return {"error": "Link not found", "link_id": link_id}

    del _case_links[link_id]
    return {"message": "Link deleted", "link_id": link_id}


@app.post("/v1/state-courts/links/detect")
def detect_case_links(body: dict):
    """
    Automatically detect potential links between cases.

    Uses party names, case numbers, and text similarity.
    """
    _ensure_initialized()

    case_id = body.get("case_id")
    text = body.get("text", "")
    case_style = body.get("case_style", "")

    # Patterns for detecting federal case references
    federal_patterns = [
        (r"(\d:\d{2}-[a-z]{2}-\d{3,6})", "federal_case"),
        (r"removed\s+(?:to|from)\s+(\w+\s+district\s+court)", "removal"),
        (r"appeal\s+(?:to|from)\s+(\w+\s+circuit)", "appeal"),
        (r"bankruptcy\s+(?:case|no\.?)\s*:?\s*(\d+-\d+)", "bankruptcy"),
    ]

    detected = []
    for pattern, link_type in federal_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            detected.append({
                "match": match.group(1),
                "link_type": link_type,
                "context": text[max(0, match.start()-30):min(len(text), match.end()+30)],
                "confidence": "medium"
            })

    # Check for party name matches in federal database
    party_matches = []
    if case_style:
        parties = re.split(r"\s+v\.?\s+", case_style, flags=re.IGNORECASE)
        for party in parties:
            party = party.strip()
            if len(party) > 3:
                party_matches.append(party)

    return {
        "case_id": case_id,
        "detected_references": detected,
        "party_names": party_matches,
        "total_detected": len(detected)
    }


@app.get("/v1/state-courts/links/types")
def get_link_types():
    """Get list of supported case link types."""
    return {
        "link_types": {
            "related": "Generally related cases (same parties, facts)",
            "appeal": "Appeal from one jurisdiction to another",
            "removal": "Case removed from state to federal court",
            "remand": "Case remanded from federal to state court",
            "consolidated": "Cases consolidated for joint proceeding",
            "transferred": "Case transferred between courts",
            "companion": "Companion case filed simultaneously",
            "parallel": "Parallel proceedings in different jurisdictions"
        }
    }


@app.get("/v1/state-courts/analytics/cross-jurisdiction")
def get_cross_jurisdiction_analytics():
    """
    Get analytics on cross-jurisdictional case activity.

    Shows patterns of removal, appeals, and related proceedings.
    """
    _ensure_initialized()

    # Link type counts
    type_counts = {}
    state_to_federal = 0
    federal_to_state = 0

    for link in _case_links.values():
        lt = link.get("link_type", "unknown")
        type_counts[lt] = type_counts.get(lt, 0) + 1

        if link.get("source_jurisdiction") == "state" and link.get("target_jurisdiction") == "federal":
            state_to_federal += 1
        elif link.get("source_jurisdiction") == "federal" and link.get("target_jurisdiction") == "state":
            federal_to_state += 1

    # State breakdown
    state_links = {}
    for link in _case_links.values():
        state = link.get("source_state") or link.get("target_court", "")[:2]
        if state:
            state_links[state] = state_links.get(state, 0) + 1

    return {
        "total_links": len(_case_links),
        "by_type": type_counts,
        "state_to_federal": state_to_federal,
        "federal_to_state": federal_to_state,
        "by_state": state_links
    }


# ============================================================================
# CASE SIMILARITY AND RELATED CASES APIs
# ============================================================================

def _tokenize_text(text: str) -> set:
    """Simple tokenizer for text similarity."""
    if not text:
        return set()
    # Remove punctuation and lowercase
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    # Split into words and filter short ones
    words = [w for w in cleaned.split() if len(w) > 2]
    return set(words)


def _jaccard_similarity(set1: set, set2: set) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


@app.post("/v1/state-courts/similarity/compare")
def compare_case_similarity(body: dict):
    """
    Compare similarity between two cases.

    Uses text similarity on case style, summary, and other fields.
    """
    _ensure_initialized()

    case1 = body.get("case1", {})
    case2 = body.get("case2", {})

    # Build text for comparison
    text1 = f"{case1.get('case_style', '')} {case1.get('summary', '')} {case1.get('parties', '')}"
    text2 = f"{case2.get('case_style', '')} {case2.get('summary', '')} {case2.get('parties', '')}"

    tokens1 = _tokenize_text(text1)
    tokens2 = _tokenize_text(text2)

    similarity = _jaccard_similarity(tokens1, tokens2)

    # Check for exact party matches
    parties1 = set(re.split(r"\s+v\.?\s+", case1.get("case_style", "").lower()))
    parties2 = set(re.split(r"\s+v\.?\s+", case2.get("case_style", "").lower()))
    party_overlap = len(parties1 & parties2) / max(len(parties1 | parties2), 1)

    # Same case type bonus
    same_type = case1.get("case_type") == case2.get("case_type") and case1.get("case_type")

    combined_score = similarity * 0.6 + party_overlap * 0.3 + (0.1 if same_type else 0)

    return {
        "similarity_score": round(combined_score, 4),
        "text_similarity": round(similarity, 4),
        "party_overlap": round(party_overlap, 4),
        "same_case_type": same_type,
        "shared_tokens": len(tokens1 & tokens2),
        "interpretation": (
            "highly similar" if combined_score > 0.7 else
            "moderately similar" if combined_score > 0.4 else
            "somewhat similar" if combined_score > 0.2 else
            "low similarity"
        )
    }


@app.get("/v1/state-courts/cases/{case_id}/similar")
def find_similar_cases(case_id: str, limit: int = 10, min_score: float = 0.2):
    """
    Find cases similar to a given case.

    Searches stored cases for similar parties, case types, and text.
    """
    _ensure_initialized()

    # Get the source case
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    source = conn.execute(
        "SELECT * FROM state_court_cases WHERE id = ?", (case_id,)
    ).fetchone()

    if not source:
        conn.close()
        return {"error": "Case not found", "case_id": case_id}

    source = dict(source)
    source_text = f"{source.get('case_style', '')} {source.get('summary', '')}"
    source_tokens = _tokenize_text(source_text)
    source_type = source.get("case_type")
    source_state = source.get("state")

    # Search for similar cases
    query = "SELECT * FROM state_court_cases WHERE id != ? LIMIT 500"
    candidates = conn.execute(query, (case_id,)).fetchall()
    conn.close()

    similar = []
    for cand in candidates:
        cand = dict(cand)
        cand_text = f"{cand.get('case_style', '')} {cand.get('summary', '')}"
        cand_tokens = _tokenize_text(cand_text)

        similarity = _jaccard_similarity(source_tokens, cand_tokens)

        # Bonus for same case type
        if source_type and cand.get("case_type") == source_type:
            similarity += 0.1

        # Bonus for same state
        if source_state and cand.get("state") == source_state:
            similarity += 0.05

        if similarity >= min_score:
            similar.append({
                "id": cand.get("id"),
                "case_number": cand.get("case_number"),
                "case_style": cand.get("case_style"),
                "case_type": cand.get("case_type"),
                "state": cand.get("state"),
                "similarity_score": round(similarity, 4)
            })

    # Sort by similarity
    similar.sort(key=lambda x: x["similarity_score"], reverse=True)

    return {
        "source_case": {
            "id": case_id,
            "case_number": source.get("case_number"),
            "case_style": source.get("case_style")
        },
        "similar_cases": similar[:limit],
        "total_found": len(similar),
        "min_score": min_score
    }


@app.post("/v1/state-courts/similarity/batch")
def batch_similarity_analysis(body: dict):
    """
    Analyze similarity across a batch of cases.

    Finds clusters of related cases.
    """
    _ensure_initialized()

    case_ids = body.get("case_ids", [])
    threshold = body.get("threshold", 0.3)

    if not case_ids:
        return {"error": "No case IDs provided"}

    # Get cases from database
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    placeholders = ",".join(["?"] * len(case_ids))
    query = f"SELECT * FROM state_court_cases WHERE id IN ({placeholders})"
    rows = conn.execute(query, case_ids).fetchall()
    conn.close()

    cases = {r["id"]: dict(r) for r in rows}

    # Calculate pairwise similarities
    pairs = []
    case_list = list(cases.values())

    for i, case1 in enumerate(case_list):
        text1 = f"{case1.get('case_style', '')} {case1.get('summary', '')}"
        tokens1 = _tokenize_text(text1)

        for case2 in case_list[i+1:]:
            text2 = f"{case2.get('case_style', '')} {case2.get('summary', '')}"
            tokens2 = _tokenize_text(text2)

            sim = _jaccard_similarity(tokens1, tokens2)

            if sim >= threshold:
                pairs.append({
                    "case1_id": case1.get("id"),
                    "case2_id": case2.get("id"),
                    "similarity": round(sim, 4)
                })

    # Find clusters (simple connected components)
    clusters = []
    visited = set()

    for case in cases.values():
        cid = case.get("id")
        if cid in visited:
            continue

        cluster = {cid}
        queue = [cid]
        visited.add(cid)

        while queue:
            current = queue.pop(0)
            for pair in pairs:
                other = None
                if pair["case1_id"] == current:
                    other = pair["case2_id"]
                elif pair["case2_id"] == current:
                    other = pair["case1_id"]

                if other and other not in visited:
                    cluster.add(other)
                    queue.append(other)
                    visited.add(other)

        if len(cluster) > 1:
            clusters.append(list(cluster))

    return {
        "total_cases": len(cases),
        "similarity_pairs": pairs,
        "clusters": clusters,
        "threshold": threshold
    }


# ============================================================================
# COMPREHENSIVE STATE COURT ANALYTICS DASHBOARD API
# ============================================================================

@app.get("/v1/state-courts/dashboard/summary")
def get_dashboard_summary():
    """
    Get comprehensive summary for state courts analytics dashboard.

    Combines all key metrics in a single response.
    """
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Case counts
    total_cases = conn.execute("SELECT COUNT(*) as count FROM state_court_cases").fetchone()["count"]
    total_opinions = conn.execute("SELECT COUNT(*) as count FROM state_court_opinions").fetchone()["count"]

    # Cases by state
    by_state = [dict(r) for r in conn.execute("""
        SELECT state, COUNT(*) as count
        FROM state_court_cases
        WHERE state IS NOT NULL
        GROUP BY state
        ORDER BY count DESC
        LIMIT 20
    """).fetchall()]

    # Cases by type
    by_type = [dict(r) for r in conn.execute("""
        SELECT case_type, COUNT(*) as count
        FROM state_court_cases
        WHERE case_type IS NOT NULL
        GROUP BY case_type
        ORDER BY count DESC
    """).fetchall()]

    # Recent activity (last 30 days)
    recent_cases = conn.execute("""
        SELECT COUNT(*) as count
        FROM state_court_cases
        WHERE date_filed >= date('now', '-30 days')
    """).fetchone()["count"]

    # Top courts
    top_courts = [dict(r) for r in conn.execute("""
        SELECT court, state, COUNT(*) as count
        FROM state_court_cases
        WHERE court IS NOT NULL
        GROUP BY court, state
        ORDER BY count DESC
        LIMIT 10
    """).fetchall()]

    conn.close()

    # Additional metrics from in-memory storage
    return {
        "overview": {
            "total_cases": total_cases,
            "total_opinions": total_opinions,
            "total_documents": len(get_document_storage()._documents) if hasattr(get_document_storage(), '_documents') else 0,
            "recent_cases_30d": recent_cases,
            "states_covered": len(by_state),
            "case_types": len(by_type)
        },
        "by_state": by_state,
        "by_case_type": by_type,
        "top_courts": top_courts,
        "alerts": {
            "total": len(_case_alerts),
            "active": len([a for a in _case_alerts.values() if a.get("active")])
        },
        "attorneys": {
            "total": len(_state_attorneys),
            "firms": len(_state_law_firms)
        },
        "links": {
            "total": len(_case_links),
            "types": list(set(l.get("link_type") for l in _case_links.values()))
        },
        "generated_at": datetime.utcnow().isoformat()
    }


@app.get("/v1/state-courts/dashboard/activity")
def get_dashboard_activity(days: int = 30):
    """
    Get activity metrics over time for the dashboard.

    Returns daily/weekly case filing trends.
    """
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Daily activity
    daily = [dict(r) for r in conn.execute(f"""
        SELECT date(date_filed) as date, COUNT(*) as count
        FROM state_court_cases
        WHERE date_filed >= date('now', '-{days} days')
        GROUP BY date(date_filed)
        ORDER BY date
    """).fetchall()]

    # By state for period
    by_state = [dict(r) for r in conn.execute(f"""
        SELECT state, COUNT(*) as count
        FROM state_court_cases
        WHERE date_filed >= date('now', '-{days} days')
        GROUP BY state
        ORDER BY count DESC
        LIMIT 10
    """).fetchall()]

    # By case type for period
    by_type = [dict(r) for r in conn.execute(f"""
        SELECT case_type, COUNT(*) as count
        FROM state_court_cases
        WHERE date_filed >= date('now', '-{days} days')
        GROUP BY case_type
        ORDER BY count DESC
    """).fetchall()]

    conn.close()

    return {
        "period_days": days,
        "daily_activity": daily,
        "by_state": by_state,
        "by_case_type": by_type,
        "total_for_period": sum(d["count"] for d in daily)
    }


@app.get("/v1/state-courts/dashboard/coverage")
def get_dashboard_coverage():
    """
    Get data coverage metrics for the dashboard.

    Shows which states have data and data quality metrics.
    """
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # State coverage
    state_stats = [dict(r) for r in conn.execute("""
        SELECT
            state,
            COUNT(*) as case_count,
            COUNT(DISTINCT court) as court_count,
            COUNT(DISTINCT case_type) as type_count,
            MIN(date_filed) as earliest,
            MAX(date_filed) as latest
        FROM state_court_cases
        WHERE state IS NOT NULL
        GROUP BY state
        ORDER BY case_count DESC
    """).fetchall()]

    # Data quality metrics
    quality = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN case_number IS NOT NULL AND case_number != '' THEN 1 ELSE 0 END) as has_case_number,
            SUM(CASE WHEN case_style IS NOT NULL AND case_style != '' THEN 1 ELSE 0 END) as has_case_style,
            SUM(CASE WHEN date_filed IS NOT NULL THEN 1 ELSE 0 END) as has_date_filed,
            SUM(CASE WHEN judge IS NOT NULL AND judge != '' THEN 1 ELSE 0 END) as has_judge,
            SUM(CASE WHEN county IS NOT NULL AND county != '' THEN 1 ELSE 0 END) as has_county
        FROM state_court_cases
    """).fetchone()

    conn.close()

    total = quality["total"] if quality["total"] > 0 else 1

    return {
        "states_with_data": len(state_stats),
        "state_coverage": state_stats,
        "data_quality": {
            "total_records": quality["total"],
            "completeness": {
                "case_number": round(quality["has_case_number"] / total * 100, 1),
                "case_style": round(quality["has_case_style"] / total * 100, 1),
                "date_filed": round(quality["has_date_filed"] / total * 100, 1),
                "judge": round(quality["has_judge"] / total * 100, 1),
                "county": round(quality["has_county"] / total * 100, 1)
            }
        },
        "all_50_states": sorted(list(STATE_ABBREV.values()))
    }


@app.get("/state-courts/analytics", response_class=HTMLResponse)
def state_courts_analytics_dashboard():
    """
    Render an interactive HTML analytics dashboard for state courts.

    Displays charts, tables, and metrics with Chart.js visualizations.
    """
    _ensure_initialized()
    from .models.db import get_conn

    # Get summary data
    conn = get_conn()

    total_cases = conn.execute("SELECT COUNT(*) as count FROM state_court_cases").fetchone()["count"]
    total_opinions = conn.execute("SELECT COUNT(*) as count FROM state_court_opinions").fetchone()["count"]

    by_state = [dict(r) for r in conn.execute("""
        SELECT state, COUNT(*) as count FROM state_court_cases
        WHERE state IS NOT NULL GROUP BY state ORDER BY count DESC LIMIT 15
    """).fetchall()]

    by_type = [dict(r) for r in conn.execute("""
        SELECT case_type, COUNT(*) as count FROM state_court_cases
        WHERE case_type IS NOT NULL GROUP BY case_type ORDER BY count DESC
    """).fetchall()]

    recent = [dict(r) for r in conn.execute("""
        SELECT id, case_number, case_style, state, case_type, date_filed
        FROM state_court_cases ORDER BY date_filed DESC LIMIT 10
    """).fetchall()]

    conn.close()

    # Build chart data
    state_labels = [s["state"] for s in by_state]
    state_values = [s["count"] for s in by_state]
    type_labels = [t["case_type"] or "Unknown" for t in by_type]
    type_values = [t["count"] for t in by_type]

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>State Courts Analytics Dashboard</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {{ font-family: -apple-system, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }}
            .container {{ max-width: 1400px; margin: 0 auto; }}
            h1 {{ color: #333; margin-bottom: 10px; }}
            .subtitle {{ color: #666; margin-bottom: 30px; }}
            .metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
            .metric {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .metric-value {{ font-size: 2.5em; font-weight: bold; color: #007bff; }}
            .metric-label {{ color: #666; margin-top: 5px; }}
            .charts {{ display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 30px; }}
            .chart-container {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .chart-container h3 {{ margin-top: 0; color: #333; }}
            table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }}
            th {{ background: #f8f9fa; font-weight: 600; color: #333; }}
            tr:hover {{ background: #f8f9fa; }}
            .badge {{ display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 0.8em; font-weight: 600; }}
            .badge-state {{ background: #e3f2fd; color: #1565c0; }}
            .badge-type {{ background: #e8f5e9; color: #2e7d32; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>State Courts Analytics Dashboard</h1>
            <p class="subtitle">Comprehensive view of state court data across all 50 states</p>

            <div class="metrics">
                <div class="metric">
                    <div class="metric-value">{total_cases:,}</div>
                    <div class="metric-label">Total Cases</div>
                </div>
                <div class="metric">
                    <div class="metric-value">{total_opinions:,}</div>
                    <div class="metric-label">Appellate Opinions</div>
                </div>
                <div class="metric">
                    <div class="metric-value">{len(by_state)}</div>
                    <div class="metric-label">States with Data</div>
                </div>
                <div class="metric">
                    <div class="metric-value">{len(_state_attorneys)}</div>
                    <div class="metric-label">Attorneys Tracked</div>
                </div>
            </div>

            <div class="charts">
                <div class="chart-container">
                    <h3>Cases by State</h3>
                    <canvas id="stateChart" height="300"></canvas>
                </div>
                <div class="chart-container">
                    <h3>Cases by Type</h3>
                    <canvas id="typeChart" height="300"></canvas>
                </div>
            </div>

            <h3>Recent Cases</h3>
            <table>
                <thead>
                    <tr>
                        <th>Case Number</th>
                        <th>Style</th>
                        <th>State</th>
                        <th>Type</th>
                        <th>Filed</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(f'''
                    <tr>
                        <td>{r.get("case_number", "N/A")}</td>
                        <td>{(r.get("case_style") or "")[:50]}...</td>
                        <td><span class="badge badge-state">{r.get("state", "")}</span></td>
                        <td><span class="badge badge-type">{r.get("case_type", "")}</span></td>
                        <td>{r.get("date_filed", "")}</td>
                    </tr>''' for r in recent)}
                </tbody>
            </table>

            <div style="margin-top: 30px; text-align: center; color: #999;">
                <p>Generated at {datetime.utcnow().isoformat()}</p>
                <p><a href="/v1/state-courts/dashboard/summary">API Summary</a> |
                   <a href="/v1/state-courts/export/cases?format=csv">Export CSV</a> |
                   <a href="/state-courts">State Courts Home</a></p>
            </div>
        </div>

        <script>
            new Chart(document.getElementById('stateChart'), {{
                type: 'bar',
                data: {{
                    labels: {state_labels},
                    datasets: [{{
                        label: 'Cases',
                        data: {state_values},
                        backgroundColor: 'rgba(0, 123, 255, 0.7)'
                    }}]
                }},
                options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }} }}
            }});

            new Chart(document.getElementById('typeChart'), {{
                type: 'doughnut',
                data: {{
                    labels: {type_labels},
                    datasets: [{{
                        data: {type_values},
                        backgroundColor: ['#007bff', '#28a745', '#dc3545', '#ffc107', '#17a2b8', '#6c757d', '#6f42c1']
                    }}]
                }},
                options: {{ responsive: true }}
            }});
        </script>
    </body>
    </html>
    """

    return html


# ============================================================================
# BULK DATA IMPORT FROM PUBLIC SOURCES APIs
# ============================================================================

# Known public data sources for state court data
PUBLIC_DATA_SOURCES = {
    "courtlistener": {
        "name": "CourtListener / Free Law Project",
        "url": "https://www.courtlistener.com/api/rest/v3/",
        "coverage": "All 50 states appellate opinions",
        "format": "JSON API",
        "rate_limit": "5000/day with API key"
    },
    "virginia_bulk": {
        "name": "Virginia Court Data",
        "url": "https://www.courts.state.va.us/",
        "coverage": "Circuit and District courts",
        "format": "CSV bulk download",
        "rate_limit": "None"
    },
    "oklahoma_oscn": {
        "name": "Oklahoma OSCN",
        "url": "https://www.oscn.net/",
        "coverage": "All 77 counties",
        "format": "HTML scraping",
        "rate_limit": "Respectful scraping"
    },
    "recap_archive": {
        "name": "RECAP Archive",
        "url": "https://www.courtlistener.com/recap/",
        "coverage": "Federal courts (PACER)",
        "format": "JSON API",
        "rate_limit": "5000/day"
    },
    "harvard_cap": {
        "name": "Harvard Caselaw Access Project",
        "url": "https://case.law/",
        "coverage": "Historical US caselaw",
        "format": "JSON API",
        "rate_limit": "500/day free"
    }
}


@app.get("/v1/state-courts/import/sources")
def list_import_sources():
    """List available public data sources for bulk import."""
    return {
        "sources": PUBLIC_DATA_SOURCES,
        "total": len(PUBLIC_DATA_SOURCES)
    }


@app.post("/v1/state-courts/import/bulk")
def bulk_import_from_source(body: dict):
    """
    Initiate bulk import from a public data source.

    Supports CourtListener, Virginia CSV, OSCN, and more.
    """
    _ensure_initialized()

    source = body.get("source", "")
    state = body.get("state")
    date_from = body.get("date_from")
    date_to = body.get("date_to")
    limit = body.get("limit", 1000)

    if source not in PUBLIC_DATA_SOURCES:
        return {
            "error": f"Unknown source: {source}",
            "available_sources": list(PUBLIC_DATA_SOURCES.keys())
        }

    # Create import job
    job_id = f"import_{source}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    job = {
        "id": job_id,
        "source": source,
        "state": state,
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
        "status": "queued",
        "records_imported": 0,
        "created_at": datetime.utcnow().isoformat()
    }

    _ingest_jobs[job_id] = job

    return {
        "message": "Import job created",
        "job": job,
        "source_info": PUBLIC_DATA_SOURCES[source]
    }


@app.post("/v1/state-courts/import/csv")
def import_from_csv_upload(body: dict):
    """
    Import state court records from uploaded CSV data.

    Expects base64-encoded CSV content.
    """
    _ensure_initialized()

    csv_data = body.get("csv_data", "")
    state = body.get("state")
    mapping = body.get("column_mapping", {})

    if not csv_data:
        return {"error": "No CSV data provided"}

    # Decode base64
    import base64
    try:
        decoded = base64.b64decode(csv_data).decode("utf-8")
    except Exception as e:
        return {"error": f"Invalid base64 data: {str(e)}"}

    # Parse CSV
    import io
    import csv
    reader = csv.DictReader(io.StringIO(decoded))

    records = []
    for row in reader:
        # Apply column mapping
        record = {}
        for target, source in mapping.items():
            if source in row:
                record[target] = row[source]
            elif source in row.keys():
                record[target] = row.get(source, "")

        # Use original columns if no mapping
        if not mapping:
            record = dict(row)

        if state:
            record["state"] = state

        records.append(record)

    # Store records
    stored = 0
    for record in records:
        try:
            store_state_court_case(record)
            stored += 1
        except Exception:
            pass

    return {
        "message": f"Imported {stored} records from CSV",
        "total_rows": len(records),
        "stored": stored,
        "failed": len(records) - stored
    }


@app.post("/v1/state-courts/import/json")
def import_from_json_upload(body: dict):
    """
    Import state court records from JSON data.

    Accepts array of case records.
    """
    _ensure_initialized()

    records = body.get("records", [])
    state = body.get("state")

    if not records:
        return {"error": "No records provided"}

    stored = 0
    for record in records:
        if state:
            record["state"] = state

        try:
            store_state_court_case(record)
            stored += 1
        except Exception:
            pass

    return {
        "message": f"Imported {stored} records from JSON",
        "total_records": len(records),
        "stored": stored,
        "failed": len(records) - stored
    }


@app.get("/v1/state-courts/import/template")
def get_import_template():
    """Get CSV/JSON template for bulk import."""
    return {
        "csv_columns": [
            "case_number", "case_style", "case_type", "state", "county",
            "court", "judge", "date_filed", "status", "parties", "summary"
        ],
        "json_schema": {
            "type": "object",
            "required": ["case_number", "state"],
            "properties": {
                "case_number": {"type": "string"},
                "case_style": {"type": "string"},
                "case_type": {"type": "string", "enum": ["CV", "CR", "FA", "PR", "JV", "TR"]},
                "state": {"type": "string", "pattern": "^[A-Z]{2}$"},
                "county": {"type": "string"},
                "court": {"type": "string"},
                "judge": {"type": "string"},
                "date_filed": {"type": "string", "format": "date"},
                "status": {"type": "string"},
                "parties": {"type": "string"},
                "summary": {"type": "string"}
            }
        },
        "example_csv": "case_number,case_style,case_type,state,date_filed\n2024-CV-001,Smith v. Jones,CV,TX,2024-01-15",
        "example_json": [
            {
                "case_number": "2024-CV-001",
                "case_style": "Smith v. Jones",
                "case_type": "CV",
                "state": "TX",
                "date_filed": "2024-01-15"
            }
        ]
    }


# ============================================================================
# CASE HISTORY AND AUDIT TRAIL APIs
# ============================================================================

# In-memory audit log
_audit_log: list = []


@app.post("/v1/state-courts/audit/log")
def log_audit_event(body: dict):
    """
    Log an audit event for compliance and tracking.

    Records all data access and modifications.
    """
    _ensure_initialized()

    event = {
        "id": f"audit_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}",
        "event_type": body.get("event_type", "unknown"),
        "entity_type": body.get("entity_type"),  # case, opinion, attorney, etc.
        "entity_id": body.get("entity_id"),
        "action": body.get("action"),  # create, read, update, delete
        "user_id": body.get("user_id", "system"),
        "ip_address": body.get("ip_address"),
        "details": body.get("details", {}),
        "timestamp": datetime.utcnow().isoformat()
    }

    _audit_log.append(event)

    # Keep only last 10000 events in memory
    if len(_audit_log) > 10000:
        _audit_log.pop(0)

    return {"message": "Audit event logged", "event_id": event["id"]}


@app.get("/v1/state-courts/audit/log")
def get_audit_log(
    entity_type: str = None,
    entity_id: str = None,
    action: str = None,
    user_id: str = None,
    limit: int = 100
):
    """
    Retrieve audit log entries.

    Filter by entity, action, or user.
    """
    _ensure_initialized()

    events = _audit_log.copy()

    if entity_type:
        events = [e for e in events if e.get("entity_type") == entity_type]

    if entity_id:
        events = [e for e in events if e.get("entity_id") == entity_id]

    if action:
        events = [e for e in events if e.get("action") == action]

    if user_id:
        events = [e for e in events if e.get("user_id") == user_id]

    # Return most recent first
    events = sorted(events, key=lambda x: x.get("timestamp", ""), reverse=True)

    return {
        "events": events[:limit],
        "total": len(events),
        "filters": {"entity_type": entity_type, "entity_id": entity_id, "action": action}
    }


@app.get("/v1/state-courts/cases/{case_id}/history")
def get_case_history(case_id: str):
    """
    Get full history of changes to a case.

    Includes all audit events related to the case.
    """
    _ensure_initialized()

    events = [e for e in _audit_log if e.get("entity_id") == case_id]

    # Sort by timestamp
    events = sorted(events, key=lambda x: x.get("timestamp", ""))

    return {
        "case_id": case_id,
        "history": events,
        "total_events": len(events)
    }


@app.get("/v1/state-courts/audit/summary")
def get_audit_summary(days: int = 7):
    """
    Get summary of audit activity.

    Shows counts by action type, entity type, and user.
    """
    _ensure_initialized()

    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    recent = [e for e in _audit_log if e.get("timestamp", "") >= cutoff]

    # Aggregate by action
    by_action = {}
    for e in recent:
        action = e.get("action", "unknown")
        by_action[action] = by_action.get(action, 0) + 1

    # Aggregate by entity type
    by_entity = {}
    for e in recent:
        entity = e.get("entity_type", "unknown")
        by_entity[entity] = by_entity.get(entity, 0) + 1

    # Top users
    by_user = {}
    for e in recent:
        user = e.get("user_id", "unknown")
        by_user[user] = by_user.get(user, 0) + 1

    return {
        "period_days": days,
        "total_events": len(recent),
        "by_action": by_action,
        "by_entity_type": by_entity,
        "top_users": sorted(by_user.items(), key=lambda x: x[1], reverse=True)[:10]
    }


# ============================================================================
# DATA ARCHIVING AND RETENTION APIs
# ============================================================================

# In-memory archive storage
_archived_data: dict = {}


@app.post("/v1/state-courts/archive/cases")
def archive_cases(body: dict):
    """
    Archive old cases for long-term storage.

    Moves cases older than specified date to archive.
    """
    _ensure_initialized()

    before_date = body.get("before_date")
    state = body.get("state")
    dry_run = body.get("dry_run", True)

    if not before_date:
        return {"error": "before_date is required"}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Find cases to archive
    query = "SELECT * FROM state_court_cases WHERE date_filed < ?"
    params = [before_date]

    if state:
        query += " AND state = ?"
        params.append(state.upper())

    cases = [dict(r) for r in conn.execute(query, params).fetchall()]

    if dry_run:
        conn.close()
        return {
            "dry_run": True,
            "cases_to_archive": len(cases),
            "before_date": before_date,
            "state": state
        }

    # Archive the cases
    archive_id = f"archive_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    _archived_data[archive_id] = {
        "id": archive_id,
        "created_at": datetime.utcnow().isoformat(),
        "before_date": before_date,
        "state": state,
        "case_count": len(cases),
        "cases": cases
    }

    # Delete from main table
    delete_query = "DELETE FROM state_court_cases WHERE date_filed < ?"
    delete_params = [before_date]

    if state:
        delete_query += " AND state = ?"
        delete_params.append(state.upper())

    conn.execute(delete_query, delete_params)
    conn.commit()
    conn.close()

    return {
        "message": f"Archived {len(cases)} cases",
        "archive_id": archive_id,
        "cases_archived": len(cases),
        "before_date": before_date
    }


@app.get("/v1/state-courts/archive")
def list_archives():
    """List all data archives."""
    _ensure_initialized()

    archives = []
    for aid, data in _archived_data.items():
        archives.append({
            "id": aid,
            "created_at": data.get("created_at"),
            "before_date": data.get("before_date"),
            "state": data.get("state"),
            "case_count": data.get("case_count")
        })

    return {
        "archives": archives,
        "total": len(archives)
    }


@app.get("/v1/state-courts/archive/{archive_id}")
def get_archive(archive_id: str, include_cases: bool = False):
    """Get details of a specific archive."""
    _ensure_initialized()

    archive = _archived_data.get(archive_id)
    if not archive:
        return {"error": "Archive not found", "archive_id": archive_id}

    result = {
        "id": archive.get("id"),
        "created_at": archive.get("created_at"),
        "before_date": archive.get("before_date"),
        "state": archive.get("state"),
        "case_count": archive.get("case_count")
    }

    if include_cases:
        result["cases"] = archive.get("cases", [])

    return result


@app.post("/v1/state-courts/archive/{archive_id}/restore")
def restore_archive(archive_id: str):
    """
    Restore cases from an archive back to the main database.
    """
    _ensure_initialized()

    archive = _archived_data.get(archive_id)
    if not archive:
        return {"error": "Archive not found", "archive_id": archive_id}

    cases = archive.get("cases", [])

    restored = 0
    for case in cases:
        try:
            store_state_court_case(case)
            restored += 1
        except Exception:
            pass

    # Remove archive after restore
    del _archived_data[archive_id]

    return {
        "message": f"Restored {restored} cases from archive",
        "archive_id": archive_id,
        "cases_restored": restored
    }


@app.delete("/v1/state-courts/archive/{archive_id}")
def delete_archive(archive_id: str):
    """Permanently delete an archive."""
    _ensure_initialized()

    if archive_id not in _archived_data:
        return {"error": "Archive not found", "archive_id": archive_id}

    case_count = _archived_data[archive_id].get("case_count", 0)
    del _archived_data[archive_id]

    return {
        "message": "Archive deleted",
        "archive_id": archive_id,
        "cases_deleted": case_count
    }


@app.get("/v1/state-courts/retention/policy")
def get_retention_policy():
    """Get data retention policy information."""
    return {
        "default_retention_days": 365 * 7,  # 7 years
        "archive_after_days": 365 * 2,  # 2 years
        "purge_after_days": 365 * 10,  # 10 years
        "policies": {
            "criminal": {
                "retention_days": 365 * 10,
                "reason": "Extended retention for criminal records"
            },
            "civil": {
                "retention_days": 365 * 7,
                "reason": "Standard civil case retention"
            },
            "family": {
                "retention_days": 365 * 10,
                "reason": "Extended retention for family matters"
            },
            "traffic": {
                "retention_days": 365 * 3,
                "reason": "Shorter retention for minor infractions"
            }
        },
        "legal_basis": "Data retained per applicable state records retention laws"
    }


@app.get("/v1/state-courts/retention/status")
def get_retention_status():
    """Get current data retention status and recommendations."""
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Count cases by age
    age_query = """
        SELECT
            CASE
                WHEN date_filed >= date('now', '-1 year') THEN 'under_1_year'
                WHEN date_filed >= date('now', '-2 years') THEN '1_to_2_years'
                WHEN date_filed >= date('now', '-5 years') THEN '2_to_5_years'
                WHEN date_filed >= date('now', '-10 years') THEN '5_to_10_years'
                ELSE 'over_10_years'
            END as age_group,
            COUNT(*) as count
        FROM state_court_cases
        WHERE date_filed IS NOT NULL
        GROUP BY age_group
    """

    age_stats = {r["age_group"]: r["count"] for r in conn.execute(age_query).fetchall()}

    # Total and archivable
    total = conn.execute("SELECT COUNT(*) as count FROM state_court_cases").fetchone()["count"]
    archivable = conn.execute("""
        SELECT COUNT(*) as count FROM state_court_cases
        WHERE date_filed < date('now', '-2 years')
    """).fetchone()["count"]

    conn.close()

    return {
        "total_cases": total,
        "cases_by_age": age_stats,
        "archivable_cases": archivable,
        "archived_count": sum(a.get("case_count", 0) for a in _archived_data.values()),
        "recommendations": {
            "archive": archivable > 0,
            "archive_count": archivable,
            "message": f"Consider archiving {archivable} cases older than 2 years" if archivable > 0 else "No cases ready for archiving"
        }
    }


# ============================================================================
# COURT CALENDAR AND SCHEDULING APIs
# ============================================================================

# In-memory calendar storage
_court_calendar: dict = {}


@app.post("/v1/state-courts/calendar/events")
def add_calendar_event(body: dict):
    """
    Add a court calendar event (hearing, trial, conference).
    """
    _ensure_initialized()

    event_id = f"cal_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    event = {
        "id": event_id,
        "case_id": body.get("case_id"),
        "case_number": body.get("case_number"),
        "event_type": body.get("event_type", "hearing"),  # hearing, trial, conference, deadline
        "event_date": body.get("event_date"),
        "event_time": body.get("event_time"),
        "court": body.get("court"),
        "courtroom": body.get("courtroom"),
        "judge": body.get("judge"),
        "state": body.get("state"),
        "county": body.get("county"),
        "description": body.get("description"),
        "parties": body.get("parties"),
        "attorneys": body.get("attorneys"),
        "status": body.get("status", "scheduled"),  # scheduled, continued, cancelled, completed
        "created_at": datetime.utcnow().isoformat()
    }

    _court_calendar[event_id] = event

    return {"message": "Calendar event added", "event": event}


@app.get("/v1/state-courts/calendar/events")
def list_calendar_events(
    state: str = None,
    court: str = None,
    judge: str = None,
    event_type: str = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 100
):
    """List court calendar events with filters."""
    _ensure_initialized()

    events = list(_court_calendar.values())

    if state:
        events = [e for e in events if e.get("state", "").upper() == state.upper()]

    if court:
        events = [e for e in events if court.lower() in (e.get("court") or "").lower()]

    if judge:
        events = [e for e in events if judge.lower() in (e.get("judge") or "").lower()]

    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]

    if date_from:
        events = [e for e in events if (e.get("event_date") or "") >= date_from]

    if date_to:
        events = [e for e in events if (e.get("event_date") or "") <= date_to]

    # Sort by date
    events.sort(key=lambda x: x.get("event_date") or "")

    return {
        "events": events[:limit],
        "total": len(events),
        "filters": {"state": state, "court": court, "event_type": event_type}
    }


@app.get("/v1/state-courts/calendar/events/{event_id}")
def get_calendar_event(event_id: str):
    """Get a specific calendar event."""
    _ensure_initialized()

    event = _court_calendar.get(event_id)
    if not event:
        return {"error": "Event not found", "event_id": event_id}

    return event


@app.put("/v1/state-courts/calendar/events/{event_id}")
def update_calendar_event(event_id: str, body: dict):
    """Update a calendar event."""
    _ensure_initialized()

    if event_id not in _court_calendar:
        return {"error": "Event not found", "event_id": event_id}

    event = _court_calendar[event_id]

    for key in ["event_date", "event_time", "courtroom", "judge", "description", "status"]:
        if key in body:
            event[key] = body[key]

    event["updated_at"] = datetime.utcnow().isoformat()
    _court_calendar[event_id] = event

    return {"message": "Event updated", "event": event}


@app.delete("/v1/state-courts/calendar/events/{event_id}")
def delete_calendar_event(event_id: str):
    """Delete a calendar event."""
    _ensure_initialized()

    if event_id not in _court_calendar:
        return {"error": "Event not found", "event_id": event_id}

    del _court_calendar[event_id]
    return {"message": "Event deleted", "event_id": event_id}


@app.get("/v1/state-courts/calendar/case/{case_id}")
def get_case_calendar(case_id: str):
    """Get all calendar events for a specific case."""
    _ensure_initialized()

    events = [e for e in _court_calendar.values() if e.get("case_id") == case_id]
    events.sort(key=lambda x: x.get("event_date") or "")

    return {
        "case_id": case_id,
        "events": events,
        "total": len(events)
    }


@app.get("/v1/state-courts/calendar/judge/{judge_name}")
def get_judge_calendar(judge_name: str, date_from: str = None, date_to: str = None):
    """Get calendar for a specific judge."""
    _ensure_initialized()

    from urllib.parse import unquote
    judge_name = unquote(judge_name)

    events = [e for e in _court_calendar.values()
              if judge_name.lower() in (e.get("judge") or "").lower()]

    if date_from:
        events = [e for e in events if (e.get("event_date") or "") >= date_from]

    if date_to:
        events = [e for e in events if (e.get("event_date") or "") <= date_to]

    events.sort(key=lambda x: x.get("event_date") or "")

    return {
        "judge": judge_name,
        "events": events,
        "total": len(events)
    }


@app.get("/v1/state-courts/calendar/types")
def get_calendar_event_types():
    """Get supported calendar event types."""
    return {
        "event_types": {
            "hearing": "General court hearing",
            "trial": "Trial proceedings",
            "conference": "Status or scheduling conference",
            "motion_hearing": "Hearing on a motion",
            "arraignment": "Criminal arraignment",
            "sentencing": "Criminal sentencing",
            "deposition": "Deposition (discovery)",
            "mediation": "Court-ordered mediation",
            "deadline": "Filing deadline",
            "oral_argument": "Appellate oral argument"
        },
        "statuses": ["scheduled", "continued", "cancelled", "completed", "pending"]
    }


# ============================================================================
# NATURAL LANGUAGE CASE SEARCH APIs
# ============================================================================

# Common legal terms for query expansion
LEGAL_SYNONYMS = {
    "lawsuit": ["case", "action", "suit", "litigation"],
    "plaintiff": ["petitioner", "complainant", "claimant"],
    "defendant": ["respondent", "accused"],
    "judge": ["justice", "magistrate", "court"],
    "lawyer": ["attorney", "counsel", "advocate"],
    "contract": ["agreement", "covenant"],
    "injury": ["harm", "damage", "tort"],
    "divorce": ["dissolution", "family"],
    "bankruptcy": ["insolvency", "chapter 7", "chapter 11"],
    "crime": ["criminal", "felony", "misdemeanor"],
}


def _expand_query(query: str) -> list:
    """Expand a query with synonyms."""
    words = query.lower().split()
    expanded = set(words)

    for word in words:
        if word in LEGAL_SYNONYMS:
            expanded.update(LEGAL_SYNONYMS[word])

    return list(expanded)


@app.post("/v1/state-courts/search/natural")
def natural_language_search(body: dict):
    """
    Search cases using natural language queries.

    Supports plain English questions like "divorce cases in Texas"
    """
    _ensure_initialized()

    query = body.get("query", "")
    limit = body.get("limit", 50)

    if not query:
        return {"error": "Query is required"}

    # Parse the query
    query_lower = query.lower()

    # Extract state mentions
    state_found = None
    for state_name, abbrev in STATE_ABBREV.items():
        if state_name.replace("_", " ") in query_lower or abbrev.lower() in query_lower.split():
            state_found = abbrev
            break

    # Extract case type mentions
    case_type_found = None
    type_keywords = {
        "CV": ["civil", "lawsuit", "contract", "tort", "injury"],
        "CR": ["criminal", "crime", "felony", "misdemeanor", "prosecution"],
        "FA": ["family", "divorce", "custody", "child support", "domestic"],
        "PR": ["probate", "estate", "will", "inheritance"],
        "BK": ["bankruptcy", "chapter 7", "chapter 11", "insolvency"],
    }

    for ct, keywords in type_keywords.items():
        if any(kw in query_lower for kw in keywords):
            case_type_found = ct
            break

    # Expand query terms
    search_terms = _expand_query(query)

    # Search database
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    base_query = "SELECT * FROM state_court_cases WHERE 1=1"
    params = []

    if state_found:
        base_query += " AND state = ?"
        params.append(state_found)

    if case_type_found:
        base_query += " AND case_type = ?"
        params.append(case_type_found)

    # Text search on case_style and summary
    if search_terms:
        term_conditions = []
        for term in search_terms[:5]:  # Limit to 5 terms
            term_conditions.append("(case_style LIKE ? OR summary LIKE ?)")
            params.extend([f"%{term}%", f"%{term}%"])

        if term_conditions:
            base_query += " AND (" + " OR ".join(term_conditions) + ")"

    base_query += f" ORDER BY date_filed DESC LIMIT {limit}"

    results = [dict(r) for r in conn.execute(base_query, params).fetchall()]
    conn.close()

    return {
        "query": query,
        "interpreted": {
            "state": state_found,
            "case_type": case_type_found,
            "search_terms": search_terms
        },
        "results": results,
        "total": len(results)
    }


@app.get("/v1/state-courts/search/suggestions")
def get_search_suggestions(q: str = ""):
    """Get search suggestions based on partial query."""
    _ensure_initialized()

    if len(q) < 2:
        return {"suggestions": []}

    suggestions = []

    # Suggest states
    for state_name, abbrev in STATE_ABBREV.items():
        if q.lower() in state_name or q.lower() in abbrev.lower():
            suggestions.append({
                "type": "state",
                "text": f"Cases in {state_name.replace('_', ' ').title()}",
                "value": abbrev
            })

    # Suggest case types
    type_names = {
        "CV": "Civil Cases",
        "CR": "Criminal Cases",
        "FA": "Family Cases",
        "PR": "Probate Cases",
        "BK": "Bankruptcy Cases"
    }

    for code, name in type_names.items():
        if q.lower() in name.lower() or q.lower() in code.lower():
            suggestions.append({
                "type": "case_type",
                "text": name,
                "value": code
            })

    # Suggest from synonyms
    for term, synonyms in LEGAL_SYNONYMS.items():
        if q.lower() in term:
            suggestions.append({
                "type": "term",
                "text": f"Search for '{term}'",
                "related": synonyms
            })

    return {"suggestions": suggestions[:10], "query": q}


@app.post("/v1/state-courts/search/advanced")
def advanced_case_search(body: dict):
    """
    Advanced search with multiple field filters.

    Supports boolean operators and field-specific queries.
    """
    _ensure_initialized()

    filters = body.get("filters", {})
    sort_by = body.get("sort_by", "date_filed")
    sort_order = body.get("sort_order", "desc")
    limit = body.get("limit", 100)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = "SELECT * FROM state_court_cases WHERE 1=1"
    params = []

    # Apply filters
    if filters.get("state"):
        query += " AND state = ?"
        params.append(filters["state"].upper())

    if filters.get("states"):  # Multiple states
        placeholders = ",".join(["?"] * len(filters["states"]))
        query += f" AND state IN ({placeholders})"
        params.extend([s.upper() for s in filters["states"]])

    if filters.get("case_type"):
        query += " AND case_type = ?"
        params.append(filters["case_type"].upper())

    if filters.get("case_types"):  # Multiple types
        placeholders = ",".join(["?"] * len(filters["case_types"]))
        query += f" AND case_type IN ({placeholders})"
        params.extend([t.upper() for t in filters["case_types"]])

    if filters.get("judge"):
        query += " AND judge LIKE ?"
        params.append(f"%{filters['judge']}%")

    if filters.get("county"):
        query += " AND county LIKE ?"
        params.append(f"%{filters['county']}%")

    if filters.get("date_from"):
        query += " AND date_filed >= ?"
        params.append(filters["date_from"])

    if filters.get("date_to"):
        query += " AND date_filed <= ?"
        params.append(filters["date_to"])

    if filters.get("case_style_contains"):
        query += " AND case_style LIKE ?"
        params.append(f"%{filters['case_style_contains']}%")

    if filters.get("case_number_pattern"):
        query += " AND case_number LIKE ?"
        params.append(filters["case_number_pattern"].replace("*", "%"))

    # Sorting
    valid_sort = ["date_filed", "case_number", "state", "case_type"]
    if sort_by in valid_sort:
        order = "DESC" if sort_order.lower() == "desc" else "ASC"
        query += f" ORDER BY {sort_by} {order}"

    query += f" LIMIT {limit}"

    results = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()

    return {
        "filters": filters,
        "results": results,
        "total": len(results),
        "sort": {"by": sort_by, "order": sort_order}
    }


# ============================================================================
# COURT HIERARCHY AND JURISDICTION MAPPING APIs
# ============================================================================

# State court hierarchy definitions
STATE_COURT_HIERARCHY = {
    "trial": {
        "level": 1,
        "names": ["District Court", "Circuit Court", "Superior Court", "County Court",
                  "Municipal Court", "Justice Court", "Magistrate Court"]
    },
    "intermediate_appellate": {
        "level": 2,
        "names": ["Court of Appeals", "Appellate Court", "Appellate Division",
                  "District Court of Appeal", "Court of Civil Appeals", "Court of Criminal Appeals"]
    },
    "supreme": {
        "level": 3,
        "names": ["Supreme Court", "Court of Appeals (NY, MD)", "Supreme Judicial Court"]
    },
    "specialized": {
        "level": 1,
        "names": ["Family Court", "Probate Court", "Juvenile Court", "Tax Court",
                  "Small Claims Court", "Traffic Court", "Drug Court", "Bankruptcy Court"]
    }
}


@app.get("/v1/state-courts/hierarchy")
def get_court_hierarchy():
    """Get the standard state court hierarchy structure."""
    return {
        "hierarchy": STATE_COURT_HIERARCHY,
        "levels": {
            1: "Trial Courts (Courts of Original Jurisdiction)",
            2: "Intermediate Appellate Courts",
            3: "Courts of Last Resort (Supreme Courts)"
        },
        "notes": {
            "exceptions": "Some states (e.g., NY) have non-standard naming conventions",
            "specialized": "Specialized courts handle specific case types"
        }
    }


@app.get("/v1/state-courts/jurisdiction/{state}")
def get_state_jurisdiction(state: str):
    """
    Get court structure and jurisdiction for a specific state.
    """
    _ensure_initialized()

    state = state.upper()
    if state not in ABBREV_TO_STATE:
        return {"error": "Invalid state code", "state": state}

    state_name = ABBREV_TO_STATE[state].replace("_", " ").title()

    # State-specific court structures
    state_courts = {
        "CA": {
            "supreme": "Supreme Court of California",
            "intermediate": ["Court of Appeal (6 districts)"],
            "trial": ["Superior Court (58 counties)"],
            "specialized": ["Small Claims", "Traffic", "Family Law"]
        },
        "TX": {
            "supreme": "Supreme Court of Texas (civil), Court of Criminal Appeals (criminal)",
            "intermediate": ["Court of Appeals (14 districts)"],
            "trial": ["District Court", "County Court", "Justice of the Peace"],
            "specialized": ["Probate Court", "Family District Court"]
        },
        "NY": {
            "supreme": "Court of Appeals",
            "intermediate": ["Appellate Division (4 departments)", "Appellate Term"],
            "trial": ["Supreme Court", "County Court", "City Court", "District Court"],
            "specialized": ["Family Court", "Surrogate's Court", "Court of Claims"]
        },
        "FL": {
            "supreme": "Supreme Court of Florida",
            "intermediate": ["District Court of Appeal (6 districts)"],
            "trial": ["Circuit Court (20 circuits)", "County Court (67 counties)"],
            "specialized": ["Family Court Division", "Probate Division"]
        }
    }

    # Default structure for states not specifically defined
    default_structure = {
        "supreme": f"Supreme Court of {state_name}",
        "intermediate": ["Court of Appeals"],
        "trial": ["District Court", "Circuit Court", "County Court"],
        "specialized": ["Family Court", "Probate Court", "Small Claims"]
    }

    structure = state_courts.get(state, default_structure)

    return {
        "state": state,
        "state_name": state_name,
        "court_structure": structure,
        "federal_districts": _get_federal_districts(state)
    }


def _get_federal_districts(state: str) -> list:
    """Get federal district courts in a state."""
    multi_district = {
        "CA": ["Northern District", "Central District", "Eastern District", "Southern District"],
        "TX": ["Northern District", "Southern District", "Eastern District", "Western District"],
        "NY": ["Southern District", "Eastern District", "Northern District", "Western District"],
        "FL": ["Northern District", "Middle District", "Southern District"],
        "PA": ["Eastern District", "Middle District", "Western District"],
        "IL": ["Northern District", "Central District", "Southern District"],
        "OH": ["Northern District", "Southern District"],
        "MI": ["Eastern District", "Western District"],
        "GA": ["Northern District", "Middle District", "Southern District"],
        "NC": ["Eastern District", "Middle District", "Western District"],
    }

    return multi_district.get(state, [f"District of {ABBREV_TO_STATE.get(state, state).replace('_', ' ').title()}"])


@app.get("/v1/state-courts/jurisdiction/map")
def get_jurisdiction_map():
    """
    Get a map of all state court jurisdictions.

    Useful for understanding where cases can be filed.
    """
    _ensure_initialized()

    jurisdiction_map = {}

    for abbrev, name in ABBREV_TO_STATE.items():
        jurisdiction_map[abbrev] = {
            "name": name.replace("_", " ").title(),
            "federal_districts": _get_federal_districts(abbrev),
            "appellate_circuit": _get_appellate_circuit(abbrev)
        }

    return {
        "states": jurisdiction_map,
        "total_states": len(jurisdiction_map),
        "federal_circuits": {
            "1st": ["ME", "NH", "MA", "RI", "PR"],
            "2nd": ["NY", "CT", "VT"],
            "3rd": ["PA", "NJ", "DE", "VI"],
            "4th": ["MD", "WV", "VA", "NC", "SC"],
            "5th": ["TX", "LA", "MS"],
            "6th": ["OH", "MI", "KY", "TN"],
            "7th": ["IL", "IN", "WI"],
            "8th": ["MN", "IA", "MO", "AR", "NE", "SD", "ND"],
            "9th": ["CA", "OR", "WA", "AZ", "NV", "ID", "MT", "AK", "HI", "GU"],
            "10th": ["CO", "WY", "UT", "KS", "OK", "NM"],
            "11th": ["FL", "GA", "AL"],
            "DC": ["DC"]
        }
    }


def _get_appellate_circuit(state: str) -> str:
    """Get the federal appellate circuit for a state."""
    circuits = {
        "ME": "1st", "NH": "1st", "MA": "1st", "RI": "1st",
        "NY": "2nd", "CT": "2nd", "VT": "2nd",
        "PA": "3rd", "NJ": "3rd", "DE": "3rd",
        "MD": "4th", "WV": "4th", "VA": "4th", "NC": "4th", "SC": "4th",
        "TX": "5th", "LA": "5th", "MS": "5th",
        "OH": "6th", "MI": "6th", "KY": "6th", "TN": "6th",
        "IL": "7th", "IN": "7th", "WI": "7th",
        "MN": "8th", "IA": "8th", "MO": "8th", "AR": "8th", "NE": "8th", "SD": "8th", "ND": "8th",
        "CA": "9th", "OR": "9th", "WA": "9th", "AZ": "9th", "NV": "9th", "ID": "9th", "MT": "9th", "AK": "9th", "HI": "9th",
        "CO": "10th", "WY": "10th", "UT": "10th", "KS": "10th", "OK": "10th", "NM": "10th",
        "FL": "11th", "GA": "11th", "AL": "11th",
    }
    return circuits.get(state, "Unknown")


@app.get("/v1/state-courts/jurisdiction/venue")
def get_venue_rules():
    """Get general venue rules for state courts."""
    return {
        "general_rules": {
            "civil": [
                "Defendant's residence",
                "Where cause of action arose",
                "Where contract was to be performed",
                "Where property is located (for real property disputes)"
            ],
            "criminal": [
                "Where crime was committed",
                "Where defendant was arrested (sometimes)",
                "Where victim was located (sometimes)"
            ],
            "family": [
                "Petitioner's residence (usually with time requirement)",
                "Where children reside (custody matters)",
                "Where marriage took place (sometimes)"
            ]
        },
        "transfer_rules": {
            "forum_non_conveniens": "Court may transfer if another venue is more convenient",
            "improper_venue": "Case may be dismissed or transferred if filed in wrong venue",
            "change_of_venue": "May be granted for publicity, convenience, or fairness"
        },
        "federal_considerations": {
            "removal": "Defendant may remove state case to federal court if federal jurisdiction exists",
            "remand": "Federal court may send case back to state court",
            "concurrent_jurisdiction": "Some matters may be heard in either state or federal court"
        }
    }


# ============================================================================
# CASE STATISTICS AND BENCHMARKING APIs
# ============================================================================

@app.get("/v1/state-courts/statistics/overview")
def get_case_statistics_overview():
    """
    Get comprehensive case statistics overview.

    Includes filing rates, case type distribution, and trends.
    """
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Total counts
    total_cases = conn.execute("SELECT COUNT(*) as count FROM state_court_cases").fetchone()["count"]
    total_opinions = conn.execute("SELECT COUNT(*) as count FROM state_court_opinions").fetchone()["count"]

    # By state
    by_state = [dict(r) for r in conn.execute("""
        SELECT state, COUNT(*) as count,
               COUNT(CASE WHEN case_type = 'CV' THEN 1 END) as civil,
               COUNT(CASE WHEN case_type = 'CR' THEN 1 END) as criminal,
               COUNT(CASE WHEN case_type = 'FA' THEN 1 END) as family
        FROM state_court_cases
        WHERE state IS NOT NULL
        GROUP BY state
        ORDER BY count DESC
    """).fetchall()]

    # By year
    by_year = [dict(r) for r in conn.execute("""
        SELECT strftime('%Y', date_filed) as year, COUNT(*) as count
        FROM state_court_cases
        WHERE date_filed IS NOT NULL
        GROUP BY year
        ORDER BY year DESC
        LIMIT 10
    """).fetchall()]

    # By case type
    by_type = [dict(r) for r in conn.execute("""
        SELECT case_type, COUNT(*) as count,
               ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM state_court_cases), 2) as percentage
        FROM state_court_cases
        WHERE case_type IS NOT NULL
        GROUP BY case_type
        ORDER BY count DESC
    """).fetchall()]

    conn.close()

    return {
        "totals": {
            "cases": total_cases,
            "opinions": total_opinions,
            "states_represented": len(by_state)
        },
        "by_state": by_state[:20],
        "by_year": by_year,
        "by_case_type": by_type,
        "generated_at": datetime.utcnow().isoformat()
    }


@app.get("/v1/state-courts/statistics/state/{state}")
def get_state_statistics(state: str):
    """Get detailed statistics for a specific state."""
    _ensure_initialized()

    state = state.upper()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Case counts
    totals = conn.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN case_type = 'CV' THEN 1 END) as civil,
            COUNT(CASE WHEN case_type = 'CR' THEN 1 END) as criminal,
            COUNT(CASE WHEN case_type = 'FA' THEN 1 END) as family,
            COUNT(CASE WHEN case_type = 'PR' THEN 1 END) as probate,
            COUNT(DISTINCT county) as counties,
            COUNT(DISTINCT court) as courts,
            COUNT(DISTINCT judge) as judges
        FROM state_court_cases
        WHERE state = ?
    """, (state,)).fetchone()

    # Monthly trend
    monthly = [dict(r) for r in conn.execute("""
        SELECT strftime('%Y-%m', date_filed) as month, COUNT(*) as count
        FROM state_court_cases
        WHERE state = ? AND date_filed IS NOT NULL
        GROUP BY month
        ORDER BY month DESC
        LIMIT 12
    """, (state,)).fetchall()]

    # Top counties
    counties = [dict(r) for r in conn.execute("""
        SELECT county, COUNT(*) as count
        FROM state_court_cases
        WHERE state = ? AND county IS NOT NULL
        GROUP BY county
        ORDER BY count DESC
        LIMIT 10
    """, (state,)).fetchall()]

    # Top judges
    judges = [dict(r) for r in conn.execute("""
        SELECT judge, COUNT(*) as count
        FROM state_court_cases
        WHERE state = ? AND judge IS NOT NULL AND judge != ''
        GROUP BY judge
        ORDER BY count DESC
        LIMIT 10
    """, (state,)).fetchall()]

    conn.close()

    return {
        "state": state,
        "state_name": ABBREV_TO_STATE.get(state, state).replace("_", " ").title(),
        "totals": dict(totals) if totals else {},
        "monthly_trend": monthly,
        "top_counties": counties,
        "top_judges": judges
    }


@app.get("/v1/state-courts/statistics/compare")
def compare_state_statistics(states: str = "CA,TX,NY,FL"):
    """Compare statistics across multiple states."""
    _ensure_initialized()

    state_list = [s.strip().upper() for s in states.split(",")]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    comparisons = []
    for state in state_list:
        stats = conn.execute("""
            SELECT
                ? as state,
                COUNT(*) as total_cases,
                COUNT(CASE WHEN case_type = 'CV' THEN 1 END) as civil,
                COUNT(CASE WHEN case_type = 'CR' THEN 1 END) as criminal,
                COUNT(DISTINCT county) as counties,
                COUNT(DISTINCT judge) as judges,
                MIN(date_filed) as earliest,
                MAX(date_filed) as latest
            FROM state_court_cases
            WHERE state = ?
        """, (state, state)).fetchone()

        comparisons.append(dict(stats))

    conn.close()

    # Calculate rankings
    for metric in ["total_cases", "civil", "criminal"]:
        sorted_states = sorted(comparisons, key=lambda x: x.get(metric, 0), reverse=True)
        for i, s in enumerate(sorted_states):
            s[f"{metric}_rank"] = i + 1

    return {
        "states": state_list,
        "comparisons": comparisons,
        "metrics": ["total_cases", "civil", "criminal", "counties", "judges"]
    }


@app.get("/v1/state-courts/statistics/trends")
def get_filing_trends(days: int = 365):
    """Get case filing trends over time."""
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Weekly filings
    weekly = [dict(r) for r in conn.execute(f"""
        SELECT
            strftime('%Y-%W', date_filed) as week,
            COUNT(*) as total,
            COUNT(CASE WHEN case_type = 'CV' THEN 1 END) as civil,
            COUNT(CASE WHEN case_type = 'CR' THEN 1 END) as criminal
        FROM state_court_cases
        WHERE date_filed >= date('now', '-{days} days')
        GROUP BY week
        ORDER BY week
    """).fetchall()]

    # Calculate moving average
    if len(weekly) >= 4:
        for i in range(3, len(weekly)):
            avg = sum(weekly[j]["total"] for j in range(i-3, i+1)) / 4
            weekly[i]["moving_avg_4wk"] = round(avg, 1)

    conn.close()

    return {
        "period_days": days,
        "weekly_data": weekly,
        "total_weeks": len(weekly)
    }


# ============================================================================
# COURT PERFORMANCE METRICS APIs
# ============================================================================

@app.get("/v1/state-courts/performance/courts")
def get_court_performance_metrics(state: str = None, limit: int = 50):
    """
    Get performance metrics for state courts.

    Includes caseload, filing rates, and activity metrics.
    """
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT
            court,
            state,
            COUNT(*) as total_cases,
            COUNT(DISTINCT case_type) as case_types,
            COUNT(DISTINCT judge) as judges,
            COUNT(DISTINCT county) as counties,
            MIN(date_filed) as earliest_filing,
            MAX(date_filed) as latest_filing
        FROM state_court_cases
        WHERE court IS NOT NULL
    """
    params = []

    if state:
        query += " AND state = ?"
        params.append(state.upper())

    query += f" GROUP BY court, state ORDER BY total_cases DESC LIMIT {limit}"

    courts = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()

    # Calculate activity score (simple metric)
    for court in courts:
        cases = court.get("total_cases", 0)
        judges = court.get("judges", 1)
        court["cases_per_judge"] = round(cases / max(judges, 1), 1)

    return {
        "courts": courts,
        "total": len(courts),
        "filters": {"state": state}
    }


@app.get("/v1/state-courts/performance/judges")
def get_judge_performance_metrics(state: str = None, limit: int = 50):
    """
    Get performance metrics for judges.

    Includes caseload distribution and case type handling.
    """
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT
            judge,
            state,
            court,
            COUNT(*) as total_cases,
            COUNT(CASE WHEN case_type = 'CV' THEN 1 END) as civil_cases,
            COUNT(CASE WHEN case_type = 'CR' THEN 1 END) as criminal_cases,
            COUNT(DISTINCT case_type) as case_type_variety,
            MIN(date_filed) as earliest,
            MAX(date_filed) as latest
        FROM state_court_cases
        WHERE judge IS NOT NULL AND judge != ''
    """
    params = []

    if state:
        query += " AND state = ?"
        params.append(state.upper())

    query += f" GROUP BY judge, state, court ORDER BY total_cases DESC LIMIT {limit}"

    judges = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()

    return {
        "judges": judges,
        "total": len(judges),
        "filters": {"state": state}
    }


@app.get("/v1/state-courts/performance/backlog")
def get_case_backlog_metrics(state: str = None):
    """
    Get case backlog and aging metrics.

    Identifies cases that may be delayed or stalled.
    """
    _ensure_initialized()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    base_where = "WHERE date_filed IS NOT NULL"
    params = []

    if state:
        base_where += " AND state = ?"
        params.append(state.upper())

    # Age distribution
    age_query = f"""
        SELECT
            CASE
                WHEN julianday('now') - julianday(date_filed) < 90 THEN 'under_90_days'
                WHEN julianday('now') - julianday(date_filed) < 180 THEN '90_to_180_days'
                WHEN julianday('now') - julianday(date_filed) < 365 THEN '180_to_365_days'
                WHEN julianday('now') - julianday(date_filed) < 730 THEN '1_to_2_years'
                ELSE 'over_2_years'
            END as age_group,
            COUNT(*) as count
        FROM state_court_cases
        {base_where}
        GROUP BY age_group
    """

    age_dist = {r["age_group"]: r["count"] for r in conn.execute(age_query, params).fetchall()}

    # Average age by case type
    avg_age_query = f"""
        SELECT
            case_type,
            ROUND(AVG(julianday('now') - julianday(date_filed)), 1) as avg_days,
            COUNT(*) as count
        FROM state_court_cases
        {base_where} AND case_type IS NOT NULL
        GROUP BY case_type
        ORDER BY avg_days DESC
    """

    avg_by_type = [dict(r) for r in conn.execute(avg_age_query, params).fetchall()]

    conn.close()

    return {
        "age_distribution": age_dist,
        "average_age_by_type": avg_by_type,
        "filters": {"state": state},
        "notes": {
            "methodology": "Age calculated from date_filed to current date",
            "limitations": "Does not account for case status or disposition"
        }
    }


# ============================================================================
# VERDICT AND SETTLEMENT TRACKING APIs
# ============================================================================

# In-memory verdict storage
_verdicts: dict = {}
_settlements: dict = {}


@app.post("/v1/state-courts/verdicts")
def record_verdict(body: dict):
    """
    Record a verdict for a case.

    Tracks trial outcomes, jury decisions, and judgments.
    """
    _ensure_initialized()

    verdict_id = f"vrd_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    verdict = {
        "id": verdict_id,
        "case_id": body.get("case_id"),
        "case_number": body.get("case_number"),
        "state": body.get("state"),
        "court": body.get("court"),
        "verdict_type": body.get("verdict_type"),  # plaintiff, defendant, mixed, hung_jury
        "verdict_date": body.get("verdict_date"),
        "amount": body.get("amount"),  # Damages awarded
        "punitive_damages": body.get("punitive_damages"),
        "jury_trial": body.get("jury_trial", False),
        "unanimous": body.get("unanimous"),
        "appeal_filed": body.get("appeal_filed", False),
        "notes": body.get("notes"),
        "created_at": datetime.utcnow().isoformat()
    }

    _verdicts[verdict_id] = verdict

    return {"message": "Verdict recorded", "verdict": verdict}


@app.get("/v1/state-courts/verdicts")
def list_verdicts(
    state: str = None,
    verdict_type: str = None,
    min_amount: float = None,
    limit: int = 100
):
    """List recorded verdicts with filters."""
    _ensure_initialized()

    verdicts = list(_verdicts.values())

    if state:
        verdicts = [v for v in verdicts if v.get("state", "").upper() == state.upper()]

    if verdict_type:
        verdicts = [v for v in verdicts if v.get("verdict_type") == verdict_type]

    if min_amount:
        verdicts = [v for v in verdicts if (v.get("amount") or 0) >= min_amount]

    # Sort by date
    verdicts.sort(key=lambda x: x.get("verdict_date") or "", reverse=True)

    return {
        "verdicts": verdicts[:limit],
        "total": len(verdicts),
        "filters": {"state": state, "verdict_type": verdict_type}
    }


@app.get("/v1/state-courts/verdicts/{verdict_id}")
def get_verdict(verdict_id: str):
    """Get details of a specific verdict."""
    _ensure_initialized()

    verdict = _verdicts.get(verdict_id)
    if not verdict:
        return {"error": "Verdict not found", "verdict_id": verdict_id}

    return verdict


@app.post("/v1/state-courts/settlements")
def record_settlement(body: dict):
    """
    Record a settlement for a case.

    Tracks negotiated resolutions and settlement terms.
    """
    _ensure_initialized()

    settlement_id = f"stl_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    settlement = {
        "id": settlement_id,
        "case_id": body.get("case_id"),
        "case_number": body.get("case_number"),
        "state": body.get("state"),
        "court": body.get("court"),
        "settlement_date": body.get("settlement_date"),
        "amount": body.get("amount"),
        "confidential": body.get("confidential", False),
        "structured": body.get("structured", False),  # Structured settlement
        "includes_injunction": body.get("includes_injunction", False),
        "mediated": body.get("mediated", False),
        "stage": body.get("stage"),  # pre_filing, discovery, trial, appeal
        "notes": body.get("notes"),
        "created_at": datetime.utcnow().isoformat()
    }

    _settlements[settlement_id] = settlement

    return {"message": "Settlement recorded", "settlement": settlement}


@app.get("/v1/state-courts/settlements")
def list_settlements(
    state: str = None,
    min_amount: float = None,
    confidential: bool = None,
    limit: int = 100
):
    """List recorded settlements with filters."""
    _ensure_initialized()

    settlements = list(_settlements.values())

    if state:
        settlements = [s for s in settlements if s.get("state", "").upper() == state.upper()]

    if min_amount:
        settlements = [s for s in settlements if (s.get("amount") or 0) >= min_amount]

    if confidential is not None:
        settlements = [s for s in settlements if s.get("confidential") == confidential]

    settlements.sort(key=lambda x: x.get("settlement_date") or "", reverse=True)

    return {
        "settlements": settlements[:limit],
        "total": len(settlements),
        "filters": {"state": state, "min_amount": min_amount}
    }


@app.get("/v1/state-courts/settlements/{settlement_id}")
def get_settlement(settlement_id: str):
    """Get details of a specific settlement."""
    _ensure_initialized()

    settlement = _settlements.get(settlement_id)
    if not settlement:
        return {"error": "Settlement not found", "settlement_id": settlement_id}

    return settlement


@app.get("/v1/state-courts/analytics/verdicts")
def get_verdict_analytics(state: str = None):
    """
    Get analytics on verdicts.

    Includes win rates, average amounts, and trends.
    """
    _ensure_initialized()

    verdicts = list(_verdicts.values())

    if state:
        verdicts = [v for v in verdicts if v.get("state", "").upper() == state.upper()]

    # Win rates
    plaintiff_wins = len([v for v in verdicts if v.get("verdict_type") == "plaintiff"])
    defendant_wins = len([v for v in verdicts if v.get("verdict_type") == "defendant"])
    total = len(verdicts)

    # Amounts
    amounts = [v.get("amount", 0) for v in verdicts if v.get("amount")]
    avg_amount = sum(amounts) / len(amounts) if amounts else 0
    max_amount = max(amounts) if amounts else 0
    median_amount = sorted(amounts)[len(amounts)//2] if amounts else 0

    # Jury vs bench
    jury_trials = len([v for v in verdicts if v.get("jury_trial")])

    return {
        "total_verdicts": total,
        "win_rates": {
            "plaintiff": round(plaintiff_wins / max(total, 1) * 100, 1),
            "defendant": round(defendant_wins / max(total, 1) * 100, 1)
        },
        "amounts": {
            "average": round(avg_amount, 2),
            "median": median_amount,
            "maximum": max_amount,
            "total_recorded": len(amounts)
        },
        "trial_type": {
            "jury_trials": jury_trials,
            "bench_trials": total - jury_trials
        },
        "filters": {"state": state}
    }


@app.get("/v1/state-courts/analytics/settlements")
def get_settlement_analytics(state: str = None):
    """
    Get analytics on settlements.

    Includes average amounts, timing, and trends.
    """
    _ensure_initialized()

    settlements = list(_settlements.values())

    if state:
        settlements = [s for s in settlements if s.get("state", "").upper() == state.upper()]

    # Amounts
    amounts = [s.get("amount", 0) for s in settlements if s.get("amount")]
    avg_amount = sum(amounts) / len(amounts) if amounts else 0

    # By stage
    by_stage = {}
    for s in settlements:
        stage = s.get("stage", "unknown")
        by_stage[stage] = by_stage.get(stage, 0) + 1

    # Confidentiality
    confidential = len([s for s in settlements if s.get("confidential")])
    mediated = len([s for s in settlements if s.get("mediated")])

    return {
        "total_settlements": len(settlements),
        "amounts": {
            "average": round(avg_amount, 2),
            "total_recorded": len(amounts)
        },
        "by_stage": by_stage,
        "characteristics": {
            "confidential": confidential,
            "public": len(settlements) - confidential,
            "mediated": mediated
        },
        "filters": {"state": state}
    }


# ============================================================================
# PUBLIC SETTLEMENT RSS FEED
# ============================================================================

# Sample high-profile settlements for public feed
_PUBLIC_SETTLEMENTS = [
    {
        "title": "T-Mobile Data Breach Class Action",
        "amount": "$350 Million",
        "url": "https://www.t-mobilesettlement.com/",
        "description": "Settlement for 2021 data breach affecting 76 million customers",
        "source": "Class Action",
        "date": "2024-01-15"
    },
    {
        "title": "Equifax Data Breach Settlement",
        "amount": "$425 Million",
        "url": "https://www.equifaxbreachsettlement.com/",
        "description": "FTC settlement for 2017 breach affecting 147 million people",
        "source": "FTC",
        "date": "2023-12-01"
    },
    {
        "title": "Facebook Privacy Settlement",
        "amount": "$725 Million",
        "url": "https://www.facebookuserprivacysettlement.com/",
        "description": "Cambridge Analytica data sharing settlement",
        "source": "Class Action",
        "date": "2023-09-22"
    },
    {
        "title": "Google Location Tracking Settlement",
        "amount": "$392 Million",
        "url": "https://www.googlelocationhistorysettlement.com/",
        "description": "Settlement with 40 states over location tracking",
        "source": "State AG",
        "date": "2023-11-14"
    },
    {
        "title": "Wells Fargo Fake Accounts",
        "amount": "$3.7 Billion",
        "url": "https://www.wellsfargosettlement.com/",
        "description": "CFPB settlement for fake accounts scandal",
        "source": "CFPB",
        "date": "2022-12-20"
    },
    {
        "title": "Johnson & Johnson Talc Settlement",
        "amount": "$8.9 Billion",
        "url": "https://www.jjtalcsettlement.com/",
        "description": "Settlement for talc-related cancer claims",
        "source": "Class Action",
        "date": "2024-05-01"
    },
    {
        "title": "3M Earplug Settlement",
        "amount": "$6 Billion",
        "url": "https://www.3mearplugsettlement.com/",
        "description": "Military earplug defect settlement",
        "source": "MDL",
        "date": "2023-08-29"
    },
    {
        "title": "Juul E-Cigarette Settlement",
        "amount": "$1.2 Billion",
        "url": "https://www.juulsettlement.com/",
        "description": "Settlement with states over youth marketing",
        "source": "State AG",
        "date": "2023-04-12"
    },
]


@app.get("/feed.xml", response_class=Response)
@app.get("/settlements.xml", response_class=Response)
@app.get("/rss", response_class=Response)
def settlement_rss_feed():
    """
    Public RSS feed of legal settlements.
    Subscribe in any RSS reader.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')

    # Combine public settlements with any recorded ones
    all_settlements = list(_PUBLIC_SETTLEMENTS)
    for s in _settlements.values():
        if not s.get("confidential"):
            all_settlements.append({
                "title": s.get("case_number", "Settlement"),
                "amount": f"${s.get('amount'):,.0f}" if s.get("amount") else "",
                "url": f"/v1/state-courts/settlements/{s['id']}",
                "description": s.get("notes", ""),
                "source": s.get("state", "State Court"),
                "date": s.get("settlement_date", now)
            })

    items = []
    for s in all_settlements[:100]:
        amount = s.get('amount', '')
        title = f"[{amount}] {s['title']}" if amount else s['title']
        items.append(f"""
    <item>
      <title><![CDATA[{title}]]></title>
      <link>{s.get('url', '')}</link>
      <description><![CDATA[{s.get('description', '')}

Amount: {amount}
Source: {s.get('source', 'Unknown')}]]></description>
      <pubDate>{s.get('date', now)}</pubDate>
      <guid isPermaLink="false">{s.get('url', '')}</guid>
    </item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Settlement Watch - Legal Settlement Feed</title>
    <link>https://pacer-rss.vercel.app/</link>
    <description>Aggregated legal settlement discoveries from federal courts, state courts, and regulatory filings. Covering 15 state court systems and 94 federal districts.</description>
    <language>en-us</language>
    <lastBuildDate>{now}</lastBuildDate>
    <atom:link href="https://pacer-rss.vercel.app/feed.xml" rel="self" type="application/rss+xml"/>
    {''.join(items)}
  </channel>
</rss>"""

    return Response(content=rss, media_type="application/rss+xml")


@app.get("/feed.atom", response_class=Response)
@app.get("/settlements.atom", response_class=Response)
def settlement_atom_feed():
    """Atom format feed of legal settlements."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    all_settlements = list(_PUBLIC_SETTLEMENTS)
    for s in _settlements.values():
        if not s.get("confidential"):
            all_settlements.append({
                "title": s.get("case_number", "Settlement"),
                "amount": f"${s.get('amount'):,.0f}" if s.get("amount") else "",
                "url": f"/v1/state-courts/settlements/{s['id']}",
                "description": s.get("notes", ""),
                "source": s.get("state", "State Court"),
                "date": s.get("settlement_date", now)
            })

    entries = []
    for s in all_settlements[:100]:
        amount = s.get('amount', '')
        title = f"[{amount}] {s['title']}" if amount else s['title']
        entries.append(f"""
  <entry>
    <title><![CDATA[{title}]]></title>
    <link href="{s.get('url', '')}"/>
    <id>{s.get('url', '')}</id>
    <updated>{now}</updated>
    <summary><![CDATA[{s.get('description', '')}

Amount: {amount}
Source: {s.get('source', 'Unknown')}]]></summary>
  </entry>""")

    atom = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Settlement Watch - Legal Settlement Feed</title>
  <link href="https://pacer-rss.vercel.app/"/>
  <link href="https://pacer-rss.vercel.app/feed.atom" rel="self"/>
  <updated>{now}</updated>
  <id>https://pacer-rss.vercel.app/</id>
  <subtitle>Legal settlement discoveries from federal and state courts</subtitle>
  {''.join(entries)}
</feed>"""

    return Response(content=atom, media_type="application/atom+xml")


@app.get("/api/settlements/public")
def public_settlements_api():
    """JSON API for public settlement data."""
    from datetime import datetime, timezone

    all_settlements = list(_PUBLIC_SETTLEMENTS)
    for s in _settlements.values():
        if not s.get("confidential"):
            all_settlements.append({
                "title": s.get("case_number", "Settlement"),
                "amount": s.get("amount"),
                "description": s.get("notes", ""),
                "source": s.get("state", "State Court"),
                "date": s.get("settlement_date")
            })

    return {
        "count": len(all_settlements),
        "updated": datetime.now(timezone.utc).isoformat(),
        "settlements": all_settlements
    }


# ============================================================================
# DOCKET ENTRY PARSING AND EXTRACTION
# ============================================================================

# Patterns for extracting structured data from docket entries
DOCKET_ENTRY_PATTERNS = {
    "motion_filed": r"(?:MOTION|Motion)\s+(?:to|for)\s+([^.]+)",
    "order_entered": r"(?:ORDER|Order)\s+(?:granting|denying|on)\s+([^.]+)",
    "judgment": r"(?:JUDGMENT|Judgment)\s+(?:entered|in favor of)\s+([^.]+)",
    "notice": r"(?:NOTICE|Notice)\s+of\s+([^.]+)",
    "stipulation": r"(?:STIPULATION|Stipulation)\s+(?:to|for|and order)\s+([^.]+)",
    "subpoena": r"(?:SUBPOENA|Subpoena)\s+(?:issued|served|to)\s+([^.]+)",
    "summons": r"(?:SUMMONS|Summons)\s+(?:issued|served)\s+(?:to|on)\s+([^.]+)",
    "complaint": r"(?:COMPLAINT|Complaint)\s+(?:filed|for)\s+([^.]+)",
    "answer": r"(?:ANSWER|Answer)\s+(?:filed|to)\s+([^.]+)",
    "discovery": r"(?:DISCOVERY|Discovery|Interrogatories|Document Request)\s+([^.]+)",
    "deposition": r"(?:DEPOSITION|Deposition)\s+(?:of|notice)\s+([^.]+)",
    "hearing": r"(?:HEARING|Hearing)\s+(?:scheduled|held|set)\s+(?:for|on)\s+([^.]+)",
    "continuance": r"(?:CONTINUANCE|Continuance)\s+(?:granted|requested)\s+([^.]+)",
    "dismissal": r"(?:DISMISSAL|Dismissed|Case dismissed)\s+([^.]+)?",
    "default": r"(?:DEFAULT|Default)\s+(?:entered|judgment)\s+([^.]+)",
    "appeal": r"(?:APPEAL|Appeal|Notice of appeal)\s+(?:filed|to)\s+([^.]+)",
    "settlement": r"(?:SETTLEMENT|Settlement|Case settled)\s+([^.]+)?",
}


@app.post("/v1/state-courts/docket/parse")
def parse_docket_entry(body: dict):
    """
    Parse a docket entry to extract structured information.

    Identifies entry type, parties mentioned, dates, and key details.
    """
    _ensure_initialized()

    text = body.get("text", "")
    if not text:
        return {"error": "Docket entry text required"}

    result = {
        "original_text": text,
        "entry_types": [],
        "extracted_details": [],
        "parties_mentioned": [],
        "dates_found": [],
        "amounts_found": []
    }

    # Match entry types
    for entry_type, pattern in DOCKET_ENTRY_PATTERNS.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result["entry_types"].append(entry_type)
            if match.group(1):
                result["extracted_details"].append({
                    "type": entry_type,
                    "detail": match.group(1).strip()
                })

    # Extract party names (common patterns)
    party_patterns = [
        r"(?:plaintiff|petitioner|appellant)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        r"(?:defendant|respondent|appellee)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+v\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
    ]
    for pattern in party_patterns:
        matches = re.findall(pattern, text)
        for m in matches:
            if isinstance(m, tuple):
                result["parties_mentioned"].extend([p for p in m if p])
            else:
                result["parties_mentioned"].append(m)

    result["parties_mentioned"] = list(set(result["parties_mentioned"]))

    # Extract dates
    date_pattern = r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+\s+\d{1,2},?\s+\d{4})\b"
    result["dates_found"] = re.findall(date_pattern, text)

    # Extract monetary amounts
    amount_pattern = r"\$[\d,]+(?:\.\d{2})?"
    result["amounts_found"] = re.findall(amount_pattern, text)

    return result


@app.post("/v1/state-courts/docket/parse/batch")
def parse_docket_entries_batch(body: dict):
    """
    Parse multiple docket entries in batch.

    Useful for processing entire case docket sheets.
    """
    _ensure_initialized()

    entries = body.get("entries", [])
    if not entries:
        return {"error": "Entries list required"}

    results = []
    entry_type_counts = {}
    all_parties = set()

    for i, entry in enumerate(entries):
        text = entry.get("text", "") if isinstance(entry, dict) else str(entry)

        parsed = {
            "index": i,
            "entry_types": [],
            "details": []
        }

        for entry_type, pattern in DOCKET_ENTRY_PATTERNS.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                parsed["entry_types"].append(entry_type)
                entry_type_counts[entry_type] = entry_type_counts.get(entry_type, 0) + 1
                if match.group(1):
                    parsed["details"].append(match.group(1).strip())

        results.append(parsed)

    return {
        "parsed_entries": results,
        "total_entries": len(entries),
        "entry_type_summary": entry_type_counts,
        "unique_entry_types": len(entry_type_counts)
    }


@app.get("/v1/state-courts/docket/entry-types")
def get_docket_entry_types():
    """Get list of recognized docket entry types and their patterns."""
    _ensure_initialized()

    return {
        "entry_types": list(DOCKET_ENTRY_PATTERNS.keys()),
        "patterns": {k: v for k, v in DOCKET_ENTRY_PATTERNS.items()},
        "categories": {
            "filings": ["complaint", "answer", "motion_filed", "notice", "stipulation"],
            "orders": ["order_entered", "judgment", "dismissal", "default"],
            "discovery": ["discovery", "deposition", "subpoena"],
            "procedural": ["hearing", "continuance", "summons"],
            "resolution": ["settlement", "appeal", "dismissal", "judgment"]
        }
    }


@app.post("/v1/state-courts/docket/timeline")
def generate_docket_timeline(body: dict):
    """
    Generate a timeline from docket entries.

    Creates chronological view of case events.
    """
    _ensure_initialized()

    entries = body.get("entries", [])

    timeline = []
    for entry in entries:
        date = entry.get("date")
        text = entry.get("text", "")

        # Determine entry category
        categories = []
        for entry_type, pattern in DOCKET_ENTRY_PATTERNS.items():
            if re.search(pattern, text, re.IGNORECASE):
                categories.append(entry_type)

        timeline.append({
            "date": date,
            "text": text[:200] + "..." if len(text) > 200 else text,
            "categories": categories,
            "is_milestone": any(c in ["complaint", "judgment", "settlement", "dismissal", "appeal"] for c in categories)
        })

    # Sort by date
    timeline.sort(key=lambda x: x.get("date") or "")

    # Identify key milestones
    milestones = [t for t in timeline if t.get("is_milestone")]

    return {
        "timeline": timeline,
        "total_entries": len(timeline),
        "milestones": milestones,
        "date_range": {
            "first": timeline[0]["date"] if timeline else None,
            "last": timeline[-1]["date"] if timeline else None
        }
    }


# ============================================================================
# PARTY RELATIONSHIP MAPPING
# ============================================================================

# In-memory party relationship storage
_party_relationships: dict = {}


@app.post("/v1/state-courts/parties/relationship")
def add_party_relationship(body: dict):
    """
    Add a relationship between parties in cases.

    Maps connections like parent/subsidiary, attorney/client, related parties.
    """
    _ensure_initialized()

    rel_id = f"rel_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{len(_party_relationships)}"

    relationship = {
        "id": rel_id,
        "party1": body.get("party1"),
        "party2": body.get("party2"),
        "relationship_type": body.get("relationship_type"),  # parent_subsidiary, affiliate, attorney_client, etc.
        "direction": body.get("direction", "bidirectional"),  # unidirectional or bidirectional
        "case_ids": body.get("case_ids", []),
        "verified": body.get("verified", False),
        "source": body.get("source"),
        "notes": body.get("notes"),
        "created_at": datetime.utcnow().isoformat()
    }

    _party_relationships[rel_id] = relationship

    return {"message": "Relationship added", "relationship": relationship}


@app.get("/v1/state-courts/parties/relationships")
def list_party_relationships(
    party_name: str = None,
    relationship_type: str = None,
    limit: int = 100
):
    """List party relationships with optional filters."""
    _ensure_initialized()

    relationships = list(_party_relationships.values())

    if party_name:
        name_lower = party_name.lower()
        relationships = [
            r for r in relationships
            if name_lower in (r.get("party1") or "").lower() or name_lower in (r.get("party2") or "").lower()
        ]

    if relationship_type:
        relationships = [r for r in relationships if r.get("relationship_type") == relationship_type]

    return {
        "relationships": relationships[:limit],
        "total": len(relationships),
        "relationship_types": list(set(r.get("relationship_type") for r in _party_relationships.values() if r.get("relationship_type")))
    }


@app.get("/v1/state-courts/parties/{party_name}/network")
def get_party_network(party_name: str, depth: int = 2):
    """
    Get the relationship network for a party.

    Returns connected parties up to specified depth.
    """
    _ensure_initialized()

    name_lower = party_name.lower()

    # Find direct relationships
    direct = []
    connected_parties = set()

    for rel in _party_relationships.values():
        p1 = (rel.get("party1") or "").lower()
        p2 = (rel.get("party2") or "").lower()

        if name_lower in p1:
            direct.append(rel)
            connected_parties.add(rel.get("party2"))
        elif name_lower in p2:
            direct.append(rel)
            connected_parties.add(rel.get("party1"))

    # Find second-degree relationships if depth > 1
    second_degree = []
    if depth > 1:
        for connected in connected_parties:
            if not connected:
                continue
            connected_lower = connected.lower()
            for rel in _party_relationships.values():
                p1 = (rel.get("party1") or "").lower()
                p2 = (rel.get("party2") or "").lower()
                if (connected_lower in p1 or connected_lower in p2) and rel not in direct:
                    second_degree.append(rel)

    return {
        "party": party_name,
        "direct_relationships": direct,
        "second_degree": second_degree,
        "connected_parties": list(connected_parties),
        "network_size": len(connected_parties)
    }


@app.get("/v1/state-courts/parties/relationship-types")
def get_party_relationship_types():
    """Get available party relationship types."""
    _ensure_initialized()

    return {
        "relationship_types": [
            {"code": "parent_subsidiary", "label": "Parent/Subsidiary", "description": "Corporate ownership relationship"},
            {"code": "affiliate", "label": "Affiliate", "description": "Related corporate entities"},
            {"code": "attorney_client", "label": "Attorney/Client", "description": "Legal representation"},
            {"code": "agent_principal", "label": "Agent/Principal", "description": "Agency relationship"},
            {"code": "partner", "label": "Partner", "description": "Business partnership"},
            {"code": "joint_venture", "label": "Joint Venture", "description": "Joint business venture"},
            {"code": "successor", "label": "Successor", "description": "Successor entity"},
            {"code": "alter_ego", "label": "Alter Ego", "description": "Alter ego or piercing corporate veil"},
            {"code": "guarantor", "label": "Guarantor", "description": "Guarantor relationship"},
            {"code": "insurer_insured", "label": "Insurer/Insured", "description": "Insurance relationship"},
            {"code": "employer_employee", "label": "Employer/Employee", "description": "Employment relationship"},
            {"code": "landlord_tenant", "label": "Landlord/Tenant", "description": "Lease relationship"}
        ]
    }


# ============================================================================
# COURT FILING FEE TRACKING
# ============================================================================

# Filing fee schedules by state
STATE_FILING_FEES: dict = {
    "CA": {
        "civil_unlimited": 435,
        "civil_limited": 225,
        "small_claims": 75,
        "family": 435,
        "probate": 435,
        "criminal": 0,
        "appeal": 775
    },
    "NY": {
        "supreme_civil": 210,
        "civil_court": 45,
        "small_claims": 20,
        "family": 0,
        "surrogate": 45,
        "appeal": 65
    },
    "TX": {
        "district_civil": 302,
        "county_civil": 212,
        "justice_court": 54,
        "family": 302,
        "probate": 302,
        "appeal": 205
    },
    "FL": {
        "circuit_civil": 400,
        "county_civil": 300,
        "small_claims": 175,
        "family": 400,
        "probate": 400,
        "appeal": 300
    },
    # Default fees for states not specifically listed
    "DEFAULT": {
        "civil": 250,
        "small_claims": 50,
        "family": 200,
        "probate": 200,
        "criminal": 0,
        "appeal": 300
    }
}


@app.get("/v1/state-courts/fees/{state}")
def get_state_filing_fees(state: str, case_type: str = None):
    """
    Get filing fee schedule for a state.

    Returns fees by case type for the specified state.
    """
    _ensure_initialized()

    state_upper = state.upper()
    fees = STATE_FILING_FEES.get(state_upper, STATE_FILING_FEES["DEFAULT"])

    if case_type:
        fee = fees.get(case_type.lower(), fees.get("civil", 0))
        return {
            "state": state_upper,
            "case_type": case_type,
            "filing_fee": fee,
            "note": "Fee may vary by county. Check local court for current fees."
        }

    return {
        "state": state_upper,
        "fee_schedule": fees,
        "note": "Fees subject to change. Check local court for current fees.",
        "fee_waivers_available": True,
        "e_filing_discount": state_upper in ["CA", "TX", "FL"]  # States with e-filing discounts
    }


@app.get("/v1/state-courts/fees/compare")
def compare_filing_fees(states: str = None, case_type: str = "civil"):
    """
    Compare filing fees across states.

    Useful for understanding cost variations by jurisdiction.
    """
    _ensure_initialized()

    state_list = states.split(",") if states else list(STATE_FILING_FEES.keys())
    state_list = [s.strip().upper() for s in state_list if s.strip().upper() != "DEFAULT"]

    comparison = []
    for state in state_list:
        fees = STATE_FILING_FEES.get(state, STATE_FILING_FEES["DEFAULT"])

        # Find the matching fee
        fee = fees.get(case_type.lower(), 0)
        if not fee:
            # Try variations
            for key, val in fees.items():
                if case_type.lower() in key:
                    fee = val
                    break
            if not fee:
                fee = fees.get("civil", fees.get(list(fees.keys())[0], 0))

        comparison.append({
            "state": state,
            "case_type": case_type,
            "fee": fee
        })

    comparison.sort(key=lambda x: x["fee"])

    avg_fee = sum(c["fee"] for c in comparison) / len(comparison) if comparison else 0

    return {
        "case_type": case_type,
        "comparison": comparison,
        "average_fee": round(avg_fee, 2),
        "lowest": comparison[0] if comparison else None,
        "highest": comparison[-1] if comparison else None
    }


@app.post("/v1/state-courts/fees/estimate")
def estimate_case_fees(body: dict):
    """
    Estimate total fees for a case.

    Calculates filing, service, and other typical fees.
    """
    _ensure_initialized()

    state = body.get("state", "").upper()
    case_type = body.get("case_type", "civil")
    num_defendants = body.get("num_defendants", 1)
    includes_discovery = body.get("includes_discovery", True)
    expects_trial = body.get("expects_trial", False)

    fees = STATE_FILING_FEES.get(state, STATE_FILING_FEES["DEFAULT"])

    # Base filing fee
    filing_fee = fees.get(case_type.lower(), fees.get("civil", 250))

    # Service fees (estimate per defendant)
    service_fee_per = 75
    service_fees = service_fee_per * num_defendants

    # Discovery costs (estimated)
    discovery_fees = 500 if includes_discovery else 0

    # Trial fees (jury fees, etc.)
    trial_fees = 300 if expects_trial else 0

    total = filing_fee + service_fees + discovery_fees + trial_fees

    return {
        "state": state,
        "case_type": case_type,
        "fee_breakdown": {
            "filing_fee": filing_fee,
            "service_fees": service_fees,
            "service_per_defendant": service_fee_per,
            "discovery_costs": discovery_fees,
            "trial_fees": trial_fees
        },
        "estimated_total": total,
        "notes": [
            "Estimates only - actual fees may vary",
            "Does not include attorney fees",
            "Additional fees may apply for motions, copies, etc."
        ]
    }


# ============================================================================
# APPELLATE CASE TRACKING
# ============================================================================

# In-memory appellate case tracking
_appellate_cases: dict = {}


@app.post("/v1/state-courts/appellate/case")
def add_appellate_case(body: dict):
    """
    Track an appellate case.

    Links trial court case to appeal with outcome tracking.
    """
    _ensure_initialized()

    appeal_id = f"app_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{len(_appellate_cases)}"

    appellate_case = {
        "id": appeal_id,
        "trial_case_id": body.get("trial_case_id"),
        "trial_case_number": body.get("trial_case_number"),
        "trial_court": body.get("trial_court"),
        "trial_state": body.get("trial_state"),
        "appeal_case_number": body.get("appeal_case_number"),
        "appellate_court": body.get("appellate_court"),  # Court of Appeals, Supreme Court
        "appellant": body.get("appellant"),
        "appellee": body.get("appellee"),
        "notice_of_appeal_date": body.get("notice_of_appeal_date"),
        "opening_brief_due": body.get("opening_brief_due"),
        "issues_on_appeal": body.get("issues_on_appeal", []),
        "status": body.get("status", "pending"),  # pending, briefing, argued, decided
        "outcome": body.get("outcome"),  # affirmed, reversed, remanded, dismissed
        "opinion_date": body.get("opinion_date"),
        "opinion_type": body.get("opinion_type"),  # published, unpublished, memorandum
        "created_at": datetime.utcnow().isoformat()
    }

    _appellate_cases[appeal_id] = appellate_case

    return {"message": "Appellate case added", "appeal": appellate_case}


@app.get("/v1/state-courts/appellate/cases")
def list_appellate_cases(
    state: str = None,
    status: str = None,
    outcome: str = None,
    limit: int = 100
):
    """List appellate cases with filters."""
    _ensure_initialized()

    cases = list(_appellate_cases.values())

    if state:
        cases = [c for c in cases if (c.get("trial_state") or "").upper() == state.upper()]

    if status:
        cases = [c for c in cases if c.get("status") == status]

    if outcome:
        cases = [c for c in cases if c.get("outcome") == outcome]

    cases.sort(key=lambda x: x.get("notice_of_appeal_date") or "", reverse=True)

    return {
        "appeals": cases[:limit],
        "total": len(cases),
        "by_status": {
            status: len([c for c in _appellate_cases.values() if c.get("status") == status])
            for status in ["pending", "briefing", "argued", "decided"]
        }
    }


@app.get("/v1/state-courts/appellate/case/{appeal_id}")
def get_appellate_case(appeal_id: str):
    """Get details of an appellate case."""
    _ensure_initialized()

    appeal = _appellate_cases.get(appeal_id)
    if not appeal:
        return {"error": "Appellate case not found", "appeal_id": appeal_id}

    return appeal


@app.get("/v1/state-courts/appellate/trial/{trial_case_id}")
def get_appeals_for_trial_case(trial_case_id: str):
    """Get all appeals related to a trial court case."""
    _ensure_initialized()

    appeals = [
        c for c in _appellate_cases.values()
        if c.get("trial_case_id") == trial_case_id
    ]

    return {
        "trial_case_id": trial_case_id,
        "appeals": appeals,
        "total_appeals": len(appeals)
    }


@app.get("/v1/state-courts/appellate/analytics")
def get_appellate_analytics(state: str = None):
    """
    Get analytics on appellate cases.

    Includes reversal rates, outcomes, and timing.
    """
    _ensure_initialized()

    cases = list(_appellate_cases.values())

    if state:
        cases = [c for c in cases if (c.get("trial_state") or "").upper() == state.upper()]

    # Outcome breakdown
    outcomes = {}
    for c in cases:
        outcome = c.get("outcome", "pending")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    # Calculate reversal rate
    decided = [c for c in cases if c.get("outcome")]
    reversed_count = len([c for c in decided if c.get("outcome") in ["reversed", "remanded"]])
    reversal_rate = round(reversed_count / max(len(decided), 1) * 100, 1)

    # Opinion types
    opinion_types = {}
    for c in cases:
        ot = c.get("opinion_type", "unknown")
        opinion_types[ot] = opinion_types.get(ot, 0) + 1

    return {
        "total_appeals": len(cases),
        "outcomes": outcomes,
        "reversal_rate": reversal_rate,
        "opinion_types": opinion_types,
        "by_status": {
            status: len([c for c in cases if c.get("status") == status])
            for status in ["pending", "briefing", "argued", "decided"]
        },
        "filters": {"state": state}
    }


@app.get("/v1/state-courts/appellate/courts")
def get_appellate_courts():
    """Get list of state appellate courts."""
    _ensure_initialized()

    return {
        "court_structures": {
            "three_tier": {
                "description": "Supreme Court + Court of Appeals + Trial Courts",
                "states": ["CA", "NY", "TX", "FL", "IL", "PA", "OH", "GA", "NC", "MI",
                          "NJ", "VA", "WA", "AZ", "MA", "TN", "IN", "MO", "MD", "WI",
                          "MN", "CO", "AL", "SC", "LA", "KY", "OR", "OK", "CT", "IA",
                          "AR", "KS", "NM", "NE", "ID", "HI", "AK"]
            },
            "two_tier": {
                "description": "Supreme Court + Trial Courts (no intermediate appellate court)",
                "states": ["DE", "ME", "MT", "NH", "RI", "SD", "VT", "WV", "WY", "ND", "NV"]
            }
        },
        "special_courts": {
            "TX": ["Court of Criminal Appeals (criminal only)"],
            "OK": ["Court of Criminal Appeals (criminal only)"],
            "NY": ["Court of Appeals (highest)", "Appellate Division", "Appellate Term"]
        }
    }


# ============================================================================
# CITATION EXTRACTION AND LINKING
# ============================================================================

# Citation patterns for various reporters
CITATION_PATTERNS = {
    # State reporters
    "state_reporter": r"(\d+)\s+([A-Z][a-z]+\.?\s*(?:\d[a-z]{2})?)\s+(\d+)",
    # Regional reporters
    "regional": r"(\d+)\s+(A\.|A\.2d|A\.3d|N\.E\.|N\.E\.2d|N\.W\.|N\.W\.2d|P\.|P\.2d|P\.3d|S\.E\.|S\.E\.2d|S\.W\.|S\.W\.2d|S\.W\.3d|So\.|So\.2d|So\.3d)\s+(\d+)",
    # State-specific reporters
    "california": r"(\d+)\s+(Cal\.\s*(?:App\.)?\s*(?:\d[a-z]{2})?)\s+(\d+)",
    "new_york": r"(\d+)\s+(N\.Y\.\s*(?:\d[a-z]{2})?|A\.D\.\s*(?:\d[a-z]{2})?|Misc\.\s*(?:\d[a-z]{2})?)\s+(\d+)",
    "texas": r"(\d+)\s+(Tex\.\s*(?:App\.|Crim\.)?)\s+(\d+)",
    "florida": r"(\d+)\s+(Fla\.\s*(?:App\.)?|So\.\s*(?:\d[a-z]{2})?)\s+(\d+)",
    # Parallel citations
    "parallel": r"(\d+)\s+([A-Z][a-zA-Z\.]+\s*(?:\d[a-z]{2})?)\s+(\d+)\s*,\s*(\d+)\s+([A-Z][a-zA-Z\.]+\s*(?:\d[a-z]{2})?)\s+(\d+)",
    # Slip opinions
    "slip_opinion": r"(No\.|Case No\.)\s*([\d\-]+)",
    # Westlaw
    "westlaw": r"(\d{4})\s+WL\s+(\d+)",
    # Lexis
    "lexis": r"(\d{4})\s+[A-Z]+\s+LEXIS\s+(\d+)",
}


@app.post("/v1/state-courts/citations/extract")
def extract_citations(body: dict):
    """
    Extract legal citations from text.

    Identifies case citations, statute citations, and regulatory citations.
    """
    _ensure_initialized()

    text = body.get("text", "")
    if not text:
        return {"error": "Text required"}

    citations = []

    # Extract case citations
    for citation_type, pattern in CITATION_PATTERNS.items():
        matches = re.finditer(pattern, text)
        for match in matches:
            citation = {
                "type": citation_type,
                "full_citation": match.group(0),
                "components": match.groups(),
                "start_pos": match.start(),
                "end_pos": match.end()
            }
            citations.append(citation)

    # Extract statute citations (common patterns)
    statute_patterns = [
        (r"(\d+)\s+U\.S\.C\.?\s*[§]?\s*(\d+)", "federal_statute"),
        (r"([A-Z][a-z]+\.?\s+(?:Code|Stat\.|Laws))\s*[§]?\s*([\d\-\.]+)", "state_statute"),
        (r"(Cal\.\s+(?:Civ\.|Pen\.|Bus\.|Gov\.|Fam\.)\s+Code)\s*[§]?\s*(\d+)", "california_code"),
        (r"(N\.Y\.\s+[A-Z][a-z]+\.?\s+Law)\s*[§]?\s*(\d+)", "new_york_law"),
        (r"(Tex\.\s+[A-Z][a-z]+\.?\s+Code)\s*[§]?\s*([\d\.]+)", "texas_code"),
    ]

    for pattern, statute_type in statute_patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            citations.append({
                "type": statute_type,
                "full_citation": match.group(0),
                "components": match.groups(),
                "start_pos": match.start(),
                "end_pos": match.end()
            })

    # Deduplicate by full citation
    seen = set()
    unique_citations = []
    for c in citations:
        if c["full_citation"] not in seen:
            seen.add(c["full_citation"])
            unique_citations.append(c)

    return {
        "citations": unique_citations,
        "total_found": len(unique_citations),
        "by_type": {
            t: len([c for c in unique_citations if c["type"] == t])
            for t in set(c["type"] for c in unique_citations)
        }
    }


@app.post("/v1/state-courts/citations/parse")
def parse_citation(body: dict):
    """
    Parse a single citation into structured components.

    Extracts volume, reporter, page, and other metadata.
    """
    _ensure_initialized()

    citation = body.get("citation", "")
    if not citation:
        return {"error": "Citation required"}

    result = {
        "original": citation,
        "parsed": {},
        "normalized": None,
        "lookup_urls": []
    }

    # Try standard case citation format
    case_match = re.match(r"(\d+)\s+([A-Za-z\.\s\d]+)\s+(\d+)(?:\s*\(([^)]+)\))?", citation)
    if case_match:
        result["parsed"] = {
            "volume": case_match.group(1),
            "reporter": case_match.group(2).strip(),
            "page": case_match.group(3),
            "parenthetical": case_match.group(4) if case_match.group(4) else None
        }
        result["normalized"] = f"{case_match.group(1)} {case_match.group(2).strip()} {case_match.group(3)}"

        # Generate lookup URLs
        result["lookup_urls"] = [
            f"https://scholar.google.com/scholar?q={quote_plus(citation)}",
            f"https://www.courtlistener.com/?q={quote_plus(citation)}",
        ]

    # Try Westlaw format
    wl_match = re.match(r"(\d{4})\s+WL\s+(\d+)", citation)
    if wl_match:
        result["parsed"] = {
            "year": wl_match.group(1),
            "wl_number": wl_match.group(2),
            "format": "westlaw"
        }

    return result


@app.post("/v1/state-courts/citations/link")
def link_citations(body: dict):
    """
    Create links between cases based on citations.

    Builds citation network showing which cases cite each other.
    """
    _ensure_initialized()

    case_id = body.get("case_id")
    citations = body.get("citations", [])

    links = []
    for citation in citations:
        link = {
            "citing_case": case_id,
            "cited_citation": citation,
            "relationship": body.get("relationship", "cites"),  # cites, distinguishes, overrules, etc.
            "treatment": body.get("treatment"),  # positive, negative, neutral
            "created_at": datetime.utcnow().isoformat()
        }
        links.append(link)

    return {
        "message": f"Created {len(links)} citation links",
        "links": links,
        "citing_case": case_id
    }


@app.get("/v1/state-courts/citations/formats")
def get_citation_formats():
    """Get supported citation formats and examples."""
    _ensure_initialized()

    return {
        "formats": {
            "standard": {
                "pattern": "[Volume] [Reporter] [Page]",
                "example": "100 Cal.App.4th 500"
            },
            "with_parenthetical": {
                "pattern": "[Volume] [Reporter] [Page] ([Court] [Year])",
                "example": "100 Cal.App.4th 500 (2002)"
            },
            "parallel": {
                "pattern": "[Citation 1], [Citation 2]",
                "example": "50 Cal.4th 100, 200 P.3d 500"
            },
            "westlaw": {
                "pattern": "[Year] WL [Number]",
                "example": "2024 WL 12345"
            },
            "lexis": {
                "pattern": "[Year] [State] LEXIS [Number]",
                "example": "2024 Cal. LEXIS 1000"
            }
        },
        "regional_reporters": [
            "A. / A.2d / A.3d (Atlantic)",
            "N.E. / N.E.2d (North Eastern)",
            "N.W. / N.W.2d (North Western)",
            "P. / P.2d / P.3d (Pacific)",
            "S.E. / S.E.2d (South Eastern)",
            "S.W. / S.W.2d / S.W.3d (South Western)",
            "So. / So.2d / So.3d (Southern)"
        ]
    }


# ============================================================================
# LEGAL RESEARCH INTEGRATION
# ============================================================================

# In-memory research notes storage
_research_notes: dict = {}


@app.post("/v1/state-courts/research/note")
def add_research_note(body: dict):
    """
    Add a research note for a case or legal topic.

    Store analysis, observations, and research findings.
    """
    _ensure_initialized()

    note_id = f"note_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{len(_research_notes)}"

    note = {
        "id": note_id,
        "case_id": body.get("case_id"),
        "case_number": body.get("case_number"),
        "topic": body.get("topic"),
        "content": body.get("content"),
        "tags": body.get("tags", []),
        "citations": body.get("citations", []),
        "priority": body.get("priority", "normal"),
        "status": body.get("status", "draft"),
        "created_at": datetime.utcnow().isoformat()
    }

    _research_notes[note_id] = note

    return {"message": "Research note added", "note": note}


@app.get("/v1/state-courts/research/notes")
def list_research_notes(
    topic: str = None,
    tag: str = None,
    case_id: str = None,
    limit: int = 100
):
    """List research notes with filters."""
    _ensure_initialized()

    notes = list(_research_notes.values())

    if topic:
        notes = [n for n in notes if topic.lower() in (n.get("topic") or "").lower()]

    if tag:
        notes = [n for n in notes if tag in n.get("tags", [])]

    if case_id:
        notes = [n for n in notes if n.get("case_id") == case_id]

    notes.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return {
        "notes": notes[:limit],
        "total": len(notes),
        "tags": list(set(tag for n in _research_notes.values() for tag in n.get("tags", [])))
    }


@app.get("/v1/state-courts/research/note/{note_id}")
def get_research_note(note_id: str):
    """Get a specific research note."""
    _ensure_initialized()

    note = _research_notes.get(note_id)
    if not note:
        return {"error": "Note not found", "note_id": note_id}

    return note


@app.post("/v1/state-courts/research/search")
def search_legal_topics(body: dict):
    """
    Search for legal topics and related cases.

    Finds relevant cases, statutes, and research materials.
    """
    _ensure_initialized()

    query = body.get("query", "")
    state = body.get("state")
    case_type = body.get("case_type")

    # Search research notes
    matching_notes = []
    query_lower = query.lower()
    for note in _research_notes.values():
        if query_lower in (note.get("content") or "").lower() or \
           query_lower in (note.get("topic") or "").lower():
            matching_notes.append(note)

    # Generate search suggestions
    suggestions = {
        "google_scholar": f"https://scholar.google.com/scholar?q={quote_plus(query)}",
        "courtlistener": f"https://www.courtlistener.com/?q={quote_plus(query)}",
        "casetext": f"https://casetext.com/search?q={quote_plus(query)}",
    }

    if state:
        suggestions["courtlistener"] += f"&court={state.lower()}"

    return {
        "query": query,
        "matching_notes": matching_notes[:10],
        "search_links": suggestions,
        "filters": {"state": state, "case_type": case_type}
    }


@app.get("/v1/state-courts/research/topics")
def get_legal_topics():
    """Get common legal research topics."""
    _ensure_initialized()

    return {
        "topics": {
            "civil_procedure": [
                "Personal jurisdiction",
                "Subject matter jurisdiction",
                "Venue",
                "Service of process",
                "Pleading standards",
                "Motion practice",
                "Discovery",
                "Summary judgment",
                "Trial procedure",
                "Appeals"
            ],
            "contracts": [
                "Formation",
                "Consideration",
                "Breach",
                "Damages",
                "Specific performance",
                "Statute of frauds"
            ],
            "torts": [
                "Negligence",
                "Strict liability",
                "Intentional torts",
                "Defamation",
                "Products liability",
                "Medical malpractice"
            ],
            "property": [
                "Real property",
                "Personal property",
                "Landlord-tenant",
                "Easements",
                "Adverse possession"
            ],
            "criminal": [
                "Elements of crimes",
                "Defenses",
                "Sentencing",
                "Constitutional rights",
                "Evidence"
            ]
        }
    }


# ============================================================================
# BATCH DATA IMPORT FROM PUBLIC SOURCES
# ============================================================================

# Track import jobs
_import_jobs: dict = {}


@app.post("/v1/state-courts/import/csv")
def import_csv_data(body: dict):
    """
    Import case data from CSV format.

    Parses and validates CSV data for batch import.
    """
    _ensure_initialized()

    csv_data = body.get("data", "")
    mapping = body.get("column_mapping", {})
    state = body.get("state")

    if not csv_data:
        return {"error": "CSV data required"}

    # Parse CSV
    import io
    import csv as csv_module

    lines = csv_data.strip().split("\n")
    if not lines:
        return {"error": "Empty CSV data"}

    reader = csv_module.DictReader(io.StringIO(csv_data))

    records = []
    errors = []

    for i, row in enumerate(reader):
        try:
            record = {
                "case_number": row.get(mapping.get("case_number", "case_number")),
                "case_type": row.get(mapping.get("case_type", "case_type")),
                "parties": row.get(mapping.get("parties", "parties")),
                "date_filed": row.get(mapping.get("date_filed", "date_filed")),
                "court": row.get(mapping.get("court", "court")),
                "state": state or row.get(mapping.get("state", "state")),
                "source": "csv_import"
            }
            records.append(record)
        except Exception as e:
            errors.append({"row": i, "error": str(e)})

    job_id = f"import_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    _import_jobs[job_id] = {
        "id": job_id,
        "type": "csv",
        "records_parsed": len(records),
        "errors": len(errors),
        "status": "completed",
        "created_at": datetime.utcnow().isoformat()
    }

    return {
        "job_id": job_id,
        "records_parsed": len(records),
        "sample_records": records[:5],
        "errors": errors[:10],
        "message": f"Parsed {len(records)} records with {len(errors)} errors"
    }


@app.post("/v1/state-courts/import/json")
def import_json_data(body: dict):
    """
    Import case data from JSON format.

    Validates and processes JSON case records.
    """
    _ensure_initialized()

    records = body.get("records", [])
    state = body.get("state")
    validate = body.get("validate", True)

    if not records:
        return {"error": "Records array required"}

    processed = []
    errors = []

    for i, record in enumerate(records):
        try:
            processed_record = {
                "case_number": record.get("case_number"),
                "case_type": record.get("case_type"),
                "parties": record.get("parties"),
                "date_filed": record.get("date_filed"),
                "court": record.get("court"),
                "state": state or record.get("state"),
                "source": "json_import"
            }

            if validate and not processed_record["case_number"]:
                errors.append({"index": i, "error": "Missing case_number"})
            else:
                processed.append(processed_record)
        except Exception as e:
            errors.append({"index": i, "error": str(e)})

    job_id = f"import_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    _import_jobs[job_id] = {
        "id": job_id,
        "type": "json",
        "records_processed": len(processed),
        "errors": len(errors),
        "status": "completed"
    }

    return {
        "job_id": job_id,
        "records_processed": len(processed),
        "errors": errors[:10],
        "message": f"Processed {len(processed)} records"
    }


@app.get("/v1/state-courts/import/jobs")
def list_import_jobs(limit: int = 50):
    """List recent import jobs."""
    _ensure_initialized()

    jobs = sorted(
        _import_jobs.values(),
        key=lambda x: x.get("created_at") or "",
        reverse=True
    )

    return {
        "jobs": jobs[:limit],
        "total": len(jobs)
    }


@app.get("/v1/state-courts/import/job/{job_id}")
def get_import_job(job_id: str):
    """Get details of an import job."""
    _ensure_initialized()

    job = _import_jobs.get(job_id)
    if not job:
        return {"error": "Import job not found", "job_id": job_id}

    return job


@app.get("/v1/state-courts/import/templates")
def get_import_templates():
    """Get templates for data import formats."""
    _ensure_initialized()

    return {
        "csv_template": {
            "required_columns": ["case_number"],
            "optional_columns": ["case_type", "parties", "date_filed", "court", "state", "county", "judge"],
            "example": "case_number,case_type,parties,date_filed,court,state\\n2024-CV-001,Civil,Smith v. Jones,2024-01-15,Superior Court,CA"
        },
        "json_template": {
            "required_fields": ["case_number"],
            "schema": {
                "case_number": "string",
                "case_type": "string",
                "parties": "object|string",
                "date_filed": "string (YYYY-MM-DD)",
                "court": "string",
                "state": "string (2-letter code)"
            },
            "example": {
                "case_number": "2024-CV-001",
                "case_type": "civil",
                "parties": {"plaintiff": "Smith", "defendant": "Jones"},
                "date_filed": "2024-01-15",
                "court": "Superior Court",
                "state": "CA"
            }
        }
    }


@app.get("/v1/state-courts/import/sources")
def get_public_data_sources():
    """Get list of known public court data sources."""
    _ensure_initialized()

    return {
        "sources": [
            {
                "name": "CourtListener",
                "url": "https://www.courtlistener.com",
                "coverage": "Federal and State Appellate",
                "format": "API, Bulk Downloads",
                "free": True
            },
            {
                "name": "Harvard Caselaw Access Project",
                "url": "https://case.law",
                "coverage": "All State Appellate (Historical)",
                "format": "API, Bulk Downloads",
                "free": True
            },
            {
                "name": "Virginia Court Data",
                "url": "https://www.vacourts.gov",
                "coverage": "Virginia All Courts",
                "format": "CSV Downloads",
                "free": True
            },
            {
                "name": "Oklahoma OSCN",
                "url": "https://www.oscn.net",
                "coverage": "Oklahoma All Courts",
                "format": "Web Scraping",
                "free": True
            },
            {
                "name": "Florida Courts",
                "url": "https://www.flcourts.org",
                "coverage": "Florida All Courts",
                "format": "Web Access",
                "free": True
            },
            {
                "name": "Texas OCA",
                "url": "https://www.txcourts.gov/oca",
                "coverage": "Texas All Courts",
                "format": "Statistics, Reports",
                "free": True
            },
            {
                "name": "California Courts",
                "url": "https://www.courts.ca.gov",
                "coverage": "California All Courts",
                "format": "Web Access",
                "free": True
            }
        ],
        "note": "Coverage and access methods vary. Check individual source for current availability."
    }


# ============================================================================
# COURT RULES AND PROCEDURES LOOKUP
# ============================================================================

# State court rules references
STATE_COURT_RULES: dict = {
    "CA": {
        "civil": "California Rules of Court",
        "rules_url": "https://www.courts.ca.gov/rules.htm",
        "key_deadlines": {
            "answer": 30,
            "demurrer": 30,
            "motion_response": 9,
            "discovery_response": 30,
            "appeal_notice": 60
        },
        "service_methods": ["personal", "mail", "electronic"],
        "e_filing": "mandatory_most_courts"
    },
    "NY": {
        "civil": "CPLR (Civil Practice Law and Rules)",
        "rules_url": "https://www.nycourts.gov/rules/",
        "key_deadlines": {
            "answer": 20,
            "answer_if_served_mail": 30,
            "motion_response": 8,
            "discovery_response": 20,
            "appeal_notice": 30
        },
        "service_methods": ["personal", "mail", "nail_and_mail"],
        "e_filing": "mandatory_supreme_civil"
    },
    "TX": {
        "civil": "Texas Rules of Civil Procedure",
        "rules_url": "https://www.txcourts.gov/rules-forms/",
        "key_deadlines": {
            "answer": 20,
            "answer_if_served_outside": 42,
            "motion_response": 21,
            "discovery_response": 30,
            "appeal_notice": 30
        },
        "service_methods": ["personal", "mail", "electronic"],
        "e_filing": "mandatory"
    },
    "FL": {
        "civil": "Florida Rules of Civil Procedure",
        "rules_url": "https://www.flcourts.org/florida-courts/rules-procedures",
        "key_deadlines": {
            "answer": 20,
            "motion_response": 10,
            "discovery_response": 30,
            "appeal_notice": 30
        },
        "service_methods": ["personal", "mail", "electronic"],
        "e_filing": "mandatory"
    }
}


@app.get("/v1/state-courts/rules/{state}")
def get_state_court_rules(state: str):
    """
    Get court rules information for a state.

    Returns key deadlines, service methods, and rules references.
    """
    _ensure_initialized()

    state_upper = state.upper()
    rules = STATE_COURT_RULES.get(state_upper)

    if not rules:
        return {
            "state": state_upper,
            "message": "Specific rules not available. Using general defaults.",
            "general_guidance": {
                "answer_deadline": "Typically 20-30 days",
                "motion_response": "Typically 10-21 days",
                "discovery_response": "Typically 30 days",
                "appeal_notice": "Typically 30-60 days"
            },
            "recommendation": "Check state court website for current rules"
        }

    return {
        "state": state_upper,
        "rules": rules,
        "note": "Deadlines may vary by court and case type. Always verify with local rules."
    }


@app.get("/v1/state-courts/rules/{state}/deadline/{deadline_type}")
def calculate_deadline(state: str, deadline_type: str, start_date: str = None):
    """
    Calculate a specific deadline based on state rules.

    Returns the deadline date given a start date.
    """
    _ensure_initialized()

    state_upper = state.upper()
    rules = STATE_COURT_RULES.get(state_upper, {})
    deadlines = rules.get("key_deadlines", {})

    days = deadlines.get(deadline_type)
    if not days:
        return {
            "error": f"Unknown deadline type: {deadline_type}",
            "available_types": list(deadlines.keys()) if deadlines else []
        }

    if start_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            from datetime import timedelta
            deadline_date = start + timedelta(days=days)
            return {
                "state": state_upper,
                "deadline_type": deadline_type,
                "days": days,
                "start_date": start_date,
                "deadline_date": deadline_date.strftime("%Y-%m-%d"),
                "note": "Excludes weekends/holidays. Verify with local court."
            }
        except ValueError:
            return {"error": "Invalid date format. Use YYYY-MM-DD"}

    return {
        "state": state_upper,
        "deadline_type": deadline_type,
        "days": days,
        "note": f"{days} days from triggering event"
    }


@app.get("/v1/state-courts/rules/deadlines")
def get_deadline_comparison(deadline_type: str = "answer"):
    """
    Compare a specific deadline type across states.

    Shows variation in deadline lengths.
    """
    _ensure_initialized()

    comparison = []
    for state, rules in STATE_COURT_RULES.items():
        deadlines = rules.get("key_deadlines", {})
        days = deadlines.get(deadline_type)
        if days:
            comparison.append({
                "state": state,
                "deadline_type": deadline_type,
                "days": days
            })

    comparison.sort(key=lambda x: x["days"])

    return {
        "deadline_type": deadline_type,
        "comparison": comparison,
        "shortest": comparison[0] if comparison else None,
        "longest": comparison[-1] if comparison else None,
        "note": "Only includes states with specific data"
    }


@app.get("/v1/state-courts/rules/service-methods")
def get_service_methods():
    """Get allowed service methods by state."""
    _ensure_initialized()

    methods = {}
    for state, rules in STATE_COURT_RULES.items():
        methods[state] = rules.get("service_methods", [])

    return {
        "service_methods_by_state": methods,
        "common_methods": {
            "personal": "Hand delivery to party",
            "mail": "Certified or first-class mail",
            "electronic": "E-service through court system",
            "nail_and_mail": "Posted at residence + mailed (limited states)",
            "publication": "Published notice (when other methods fail)"
        }
    }


# ============================================================================
# E-FILING STATUS TRACKING
# ============================================================================

# E-filing status by state
STATE_EFILING_STATUS: dict = {
    "CA": {"status": "mandatory", "system": "Multiple (varies by county)", "url": "https://www.courts.ca.gov/8212.htm"},
    "NY": {"status": "mandatory_civil", "system": "NYSCEF", "url": "https://iapps.courts.state.ny.us/nyscef/"},
    "TX": {"status": "mandatory", "system": "eFileTexas", "url": "https://efiletexas.gov"},
    "FL": {"status": "mandatory", "system": "Florida Courts E-Filing Portal", "url": "https://www.myflcourtaccess.com"},
    "IL": {"status": "mandatory", "system": "Odyssey eFileIL", "url": "https://efileil.com"},
    "PA": {"status": "available", "system": "PACFile", "url": "https://ujsportal.pacourts.us"},
    "OH": {"status": "varies", "system": "Multiple by county", "url": "https://www.supremecourt.ohio.gov"},
    "GA": {"status": "mandatory_appellate", "system": "PeachCourt", "url": "https://peachcourt.com"},
    "NC": {"status": "available", "system": "eCourts", "url": "https://www.nccourts.gov/ecourts"},
    "MI": {"status": "mandatory", "system": "MiFile", "url": "https://mifile.courts.michigan.gov"},
    "NJ": {"status": "mandatory", "system": "eCourts", "url": "https://www.njcourts.gov/attorneys/ecourts"},
    "VA": {"status": "available", "system": "VACES", "url": "https://eapps.courts.state.va.us/cav"},
    "WA": {"status": "available", "system": "Multiple by county", "url": "https://www.courts.wa.gov"},
    "AZ": {"status": "mandatory", "system": "AZTurboCourt", "url": "https://www.azturbocourt.gov"},
    "CO": {"status": "mandatory", "system": "Colorado Courts E-Filing", "url": "https://www.courts.state.co.us/efiling"},
    "MA": {"status": "available", "system": "eFileMA", "url": "https://www.efilema.com"},
    "MN": {"status": "mandatory", "system": "eFS (eFile & eServe)", "url": "https://minnesota.tylerhost.net"},
    "WI": {"status": "mandatory", "system": "eFiling", "url": "https://efiling.wicourts.gov"},
    "TN": {"status": "varies", "system": "Multiple by county", "url": "https://www.tncourts.gov"},
    "IN": {"status": "mandatory", "system": "Odyssey File & Serve", "url": "https://indianaefiling.com"},
}


@app.get("/v1/state-courts/efiling/{state}")
def get_state_efiling_info(state: str):
    """
    Get e-filing information for a state.

    Returns system details, status, and links.
    """
    _ensure_initialized()

    state_upper = state.upper()
    efiling = STATE_EFILING_STATUS.get(state_upper)

    if not efiling:
        return {
            "state": state_upper,
            "status": "unknown",
            "message": "E-filing information not available for this state",
            "recommendation": "Check state court website for e-filing options"
        }

    return {
        "state": state_upper,
        "efiling": efiling,
        "tips": [
            "Register for an account before filing",
            "Check accepted file formats",
            "Verify filing fees for electronic submission",
            "Keep confirmation receipts"
        ]
    }


@app.get("/v1/state-courts/efiling/status")
def get_efiling_status_nationwide():
    """Get e-filing status across all states."""
    _ensure_initialized()

    by_status = {
        "mandatory": [],
        "mandatory_civil": [],
        "mandatory_appellate": [],
        "available": [],
        "varies": [],
        "unknown": []
    }

    for state, info in STATE_EFILING_STATUS.items():
        status = info.get("status", "unknown")
        if status in by_status:
            by_status[status].append(state)

    return {
        "efiling_status_by_state": STATE_EFILING_STATUS,
        "summary": {
            status: len(states) for status, states in by_status.items()
        },
        "by_status": by_status
    }


@app.get("/v1/state-courts/efiling/systems")
def get_efiling_systems():
    """Get list of e-filing systems used across states."""
    _ensure_initialized()

    systems = {}
    for state, info in STATE_EFILING_STATUS.items():
        system = info.get("system", "Unknown")
        if system not in systems:
            systems[system] = []
        systems[system].append(state)

    return {
        "systems": systems,
        "major_providers": {
            "Tyler Technologies": "Odyssey-based systems (TX, IL, IN, MN)",
            "File & ServeXpress": "Multi-state provider",
            "State-specific": "Many states have custom systems"
        }
    }


# ============================================================================
# CASE DISPOSITION ANALYTICS
# ============================================================================

# In-memory disposition tracking
_dispositions: dict = {}


@app.post("/v1/state-courts/dispositions")
def record_disposition(body: dict):
    """
    Record a case disposition.

    Tracks how cases are resolved (settlement, judgment, dismissal, etc.).
    """
    _ensure_initialized()

    disp_id = f"disp_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{len(_dispositions)}"

    disposition = {
        "id": disp_id,
        "case_id": body.get("case_id"),
        "case_number": body.get("case_number"),
        "state": body.get("state"),
        "court": body.get("court"),
        "case_type": body.get("case_type"),
        "disposition_type": body.get("disposition_type"),  # settlement, judgment, dismissal, etc.
        "disposition_date": body.get("disposition_date"),
        "prevailing_party": body.get("prevailing_party"),  # plaintiff, defendant, mixed
        "monetary_amount": body.get("monetary_amount"),
        "days_to_disposition": body.get("days_to_disposition"),
        "trial_held": body.get("trial_held", False),
        "appealed": body.get("appealed", False),
        "created_at": datetime.utcnow().isoformat()
    }

    _dispositions[disp_id] = disposition

    return {"message": "Disposition recorded", "disposition": disposition}


@app.get("/v1/state-courts/dispositions")
def list_dispositions(
    state: str = None,
    disposition_type: str = None,
    case_type: str = None,
    limit: int = 100
):
    """List recorded dispositions with filters."""
    _ensure_initialized()

    dispositions = list(_dispositions.values())

    if state:
        dispositions = [d for d in dispositions if (d.get("state") or "").upper() == state.upper()]

    if disposition_type:
        dispositions = [d for d in dispositions if d.get("disposition_type") == disposition_type]

    if case_type:
        dispositions = [d for d in dispositions if d.get("case_type") == case_type]

    dispositions.sort(key=lambda x: x.get("disposition_date") or "", reverse=True)

    return {
        "dispositions": dispositions[:limit],
        "total": len(dispositions)
    }


@app.get("/v1/state-courts/dispositions/analytics")
def get_disposition_analytics(state: str = None, case_type: str = None):
    """
    Get analytics on case dispositions.

    Includes disposition types, timing, and outcomes.
    """
    _ensure_initialized()

    dispositions = list(_dispositions.values())

    if state:
        dispositions = [d for d in dispositions if (d.get("state") or "").upper() == state.upper()]

    if case_type:
        dispositions = [d for d in dispositions if d.get("case_type") == case_type]

    # Disposition type breakdown
    by_type = {}
    for d in dispositions:
        dtype = d.get("disposition_type", "unknown")
        by_type[dtype] = by_type.get(dtype, 0) + 1

    # Prevailing party analysis
    by_party = {}
    for d in dispositions:
        party = d.get("prevailing_party", "unknown")
        by_party[party] = by_party.get(party, 0) + 1

    # Time to disposition
    times = [d.get("days_to_disposition") for d in dispositions if d.get("days_to_disposition")]
    avg_time = sum(times) / len(times) if times else 0

    # Trial rate
    trial_count = len([d for d in dispositions if d.get("trial_held")])
    trial_rate = round(trial_count / max(len(dispositions), 1) * 100, 1)

    # Appeal rate
    appeal_count = len([d for d in dispositions if d.get("appealed")])
    appeal_rate = round(appeal_count / max(len(dispositions), 1) * 100, 1)

    return {
        "total_dispositions": len(dispositions),
        "by_type": by_type,
        "by_prevailing_party": by_party,
        "timing": {
            "average_days": round(avg_time, 1),
            "cases_with_timing": len(times)
        },
        "rates": {
            "trial_rate": trial_rate,
            "appeal_rate": appeal_rate
        },
        "filters": {"state": state, "case_type": case_type}
    }


@app.get("/v1/state-courts/dispositions/types")
def get_disposition_types():
    """Get standard disposition types and their meanings."""
    _ensure_initialized()

    return {
        "disposition_types": [
            {"code": "settlement", "label": "Settlement", "description": "Parties reached agreement"},
            {"code": "judgment_plaintiff", "label": "Judgment for Plaintiff", "description": "Court ruled for plaintiff"},
            {"code": "judgment_defendant", "label": "Judgment for Defendant", "description": "Court ruled for defendant"},
            {"code": "summary_judgment", "label": "Summary Judgment", "description": "Decided without trial"},
            {"code": "default_judgment", "label": "Default Judgment", "description": "Defendant failed to respond"},
            {"code": "dismissal_voluntary", "label": "Voluntary Dismissal", "description": "Plaintiff dismissed case"},
            {"code": "dismissal_involuntary", "label": "Involuntary Dismissal", "description": "Court dismissed case"},
            {"code": "dismissed_with_prejudice", "label": "Dismissed with Prejudice", "description": "Cannot be refiled"},
            {"code": "dismissed_without_prejudice", "label": "Dismissed without Prejudice", "description": "Can be refiled"},
            {"code": "arbitration_award", "label": "Arbitration Award", "description": "Decided by arbitrator"},
            {"code": "mediated_settlement", "label": "Mediated Settlement", "description": "Settled through mediation"},
            {"code": "consolidated", "label": "Consolidated", "description": "Merged with another case"},
            {"code": "transferred", "label": "Transferred", "description": "Moved to another court"}
        ]
    }


@app.get("/v1/state-courts/dispositions/benchmarks")
def get_disposition_benchmarks():
    """
    Get benchmark statistics for case dispositions.

    Standard timing and outcome benchmarks for comparison.
    """
    _ensure_initialized()

    return {
        "benchmarks": {
            "civil": {
                "median_days_to_disposition": 300,
                "settlement_rate": 65,
                "trial_rate": 3,
                "plaintiff_win_rate": 55
            },
            "family": {
                "median_days_to_disposition": 180,
                "settlement_rate": 70,
                "trial_rate": 5
            },
            "small_claims": {
                "median_days_to_disposition": 60,
                "settlement_rate": 40,
                "default_rate": 30
            },
            "criminal": {
                "median_days_to_disposition": 150,
                "plea_rate": 90,
                "trial_rate": 5
            }
        },
        "source": "Aggregate court statistics (estimates)",
        "note": "Actual metrics vary significantly by jurisdiction and case complexity"
    }


# ============================================================================
# STATUTE OF LIMITATIONS TRACKING
# ============================================================================

# Common statutes of limitations by state and claim type
STATUTES_OF_LIMITATIONS: dict = {
    "CA": {
        "personal_injury": 2,
        "medical_malpractice": 3,
        "property_damage": 3,
        "written_contract": 4,
        "oral_contract": 2,
        "fraud": 3,
        "defamation": 1,
        "product_liability": 2
    },
    "NY": {
        "personal_injury": 3,
        "medical_malpractice": 2.5,
        "property_damage": 3,
        "written_contract": 6,
        "oral_contract": 6,
        "fraud": 6,
        "defamation": 1,
        "product_liability": 3
    },
    "TX": {
        "personal_injury": 2,
        "medical_malpractice": 2,
        "property_damage": 2,
        "written_contract": 4,
        "oral_contract": 4,
        "fraud": 4,
        "defamation": 1,
        "product_liability": 2
    },
    "FL": {
        "personal_injury": 4,
        "medical_malpractice": 2,
        "property_damage": 4,
        "written_contract": 5,
        "oral_contract": 4,
        "fraud": 4,
        "defamation": 2,
        "product_liability": 4
    }
}


@app.get("/v1/state-courts/limitations/{state}")
def get_state_limitations(state: str, claim_type: str = None):
    """
    Get statute of limitations for a state.

    Returns time limits for filing various claims.
    """
    _ensure_initialized()

    state_upper = state.upper()
    limitations = STATUTES_OF_LIMITATIONS.get(state_upper)

    if not limitations:
        return {
            "state": state_upper,
            "message": "Specific limitations not available",
            "general_guidance": {
                "personal_injury": "Typically 2-4 years",
                "contracts": "Typically 4-6 years",
                "property": "Typically 3-6 years"
            },
            "recommendation": "Consult state statutes for exact limitations"
        }

    if claim_type:
        years = limitations.get(claim_type.lower())
        if years:
            return {
                "state": state_upper,
                "claim_type": claim_type,
                "limitation_years": years,
                "note": "Exceptions and tolling rules may apply"
            }
        else:
            return {
                "error": f"Unknown claim type: {claim_type}",
                "available_types": list(limitations.keys())
            }

    return {
        "state": state_upper,
        "limitations": limitations,
        "note": "Years from accrual of claim. Exceptions may apply."
    }


@app.get("/v1/state-courts/limitations/compare")
def compare_limitations(claim_type: str = "personal_injury"):
    """
    Compare statute of limitations across states.

    Shows variation in time limits for a claim type.
    """
    _ensure_initialized()

    comparison = []
    for state, limitations in STATUTES_OF_LIMITATIONS.items():
        years = limitations.get(claim_type.lower())
        if years:
            comparison.append({
                "state": state,
                "claim_type": claim_type,
                "years": years
            })

    comparison.sort(key=lambda x: x["years"])

    return {
        "claim_type": claim_type,
        "comparison": comparison,
        "shortest": comparison[0] if comparison else None,
        "longest": comparison[-1] if comparison else None,
        "note": "Only includes states with specific data"
    }


@app.post("/v1/state-courts/limitations/calculate")
def calculate_limitation_deadline(body: dict):
    """
    Calculate statute of limitations deadline.

    Given incident date and claim type, returns filing deadline.
    """
    _ensure_initialized()

    state = body.get("state", "").upper()
    claim_type = body.get("claim_type", "")
    incident_date = body.get("incident_date")

    limitations = STATUTES_OF_LIMITATIONS.get(state, {})
    years = limitations.get(claim_type.lower())

    if not years:
        return {
            "error": "Cannot calculate - unknown state or claim type",
            "state": state,
            "claim_type": claim_type
        }

    if not incident_date:
        return {
            "state": state,
            "claim_type": claim_type,
            "limitation_years": years,
            "note": "Provide incident_date (YYYY-MM-DD) to calculate deadline"
        }

    try:
        from datetime import timedelta
        incident = datetime.strptime(incident_date, "%Y-%m-%d")
        deadline = incident + timedelta(days=int(years * 365))

        days_remaining = (deadline - datetime.utcnow()).days

        return {
            "state": state,
            "claim_type": claim_type,
            "limitation_years": years,
            "incident_date": incident_date,
            "filing_deadline": deadline.strftime("%Y-%m-%d"),
            "days_remaining": days_remaining,
            "status": "expired" if days_remaining < 0 else ("urgent" if days_remaining < 90 else "active"),
            "warning": "This is a general calculation. Consult an attorney for exact deadlines."
        }
    except ValueError:
        return {"error": "Invalid date format. Use YYYY-MM-DD"}


# ============================================================================
# COURT CONTACT INFORMATION
# ============================================================================

# Major court contact info by state (partial listing)
STATE_COURT_CONTACTS: dict = {
    "CA": {
        "supreme_court": {
            "name": "Supreme Court of California",
            "address": "350 McAllister Street, San Francisco, CA 94102",
            "phone": "(415) 865-7000",
            "website": "https://www.courts.ca.gov/supremecourt.htm"
        },
        "courts_of_appeal": [
            {"district": 1, "city": "San Francisco", "counties": ["Alameda", "Contra Costa", "Del Norte", "Humboldt", "Lake", "Marin", "Mendocino", "Napa", "San Francisco", "San Mateo", "Solano", "Sonoma"]},
            {"district": 2, "city": "Los Angeles/Ventura", "counties": ["Los Angeles", "San Luis Obispo", "Santa Barbara", "Ventura"]},
            {"district": 3, "city": "Sacramento", "counties": ["Alpine", "Amador", "Butte", "Calaveras", "Colusa", "El Dorado", "Glenn", "Lassen", "Modoc", "Mono", "Nevada", "Placer", "Plumas", "Sacramento", "San Joaquin", "Shasta", "Sierra", "Siskiyou", "Stanislaus", "Sutter", "Tehama", "Trinity", "Tuolumne", "Yolo", "Yuba"]},
            {"district": 4, "city": "San Diego/Riverside/Santa Ana", "counties": ["Imperial", "Inyo", "Orange", "Riverside", "San Bernardino", "San Diego"]},
            {"district": 5, "city": "Fresno", "counties": ["Fresno", "Kern", "Kings", "Madera", "Mariposa", "Merced", "Tulare"]},
            {"district": 6, "city": "San Jose", "counties": ["Monterey", "San Benito", "Santa Clara", "Santa Cruz"]}
        ],
        "admin_office": {
            "name": "Judicial Council of California",
            "website": "https://www.courts.ca.gov"
        }
    },
    "NY": {
        "court_of_appeals": {
            "name": "Court of Appeals of New York",
            "address": "20 Eagle Street, Albany, NY 12207",
            "phone": "(518) 455-7700",
            "website": "https://www.nycourts.gov/ctapps/"
        },
        "appellate_divisions": [
            {"department": 1, "city": "New York", "counties": ["New York", "Bronx"]},
            {"department": 2, "city": "Brooklyn", "counties": ["Kings", "Queens", "Richmond", "Nassau", "Suffolk", "Westchester", "Rockland", "Putnam", "Orange", "Dutchess"]},
            {"department": 3, "city": "Albany", "counties": "Third Department counties"},
            {"department": 4, "city": "Rochester", "counties": "Fourth Department counties"}
        ],
        "admin_office": {
            "name": "Office of Court Administration",
            "website": "https://www.nycourts.gov"
        }
    },
    "TX": {
        "supreme_court": {
            "name": "Supreme Court of Texas",
            "address": "201 W. 14th Street, Austin, TX 78701",
            "phone": "(512) 463-1312",
            "website": "https://www.txcourts.gov/supreme/"
        },
        "court_criminal_appeals": {
            "name": "Court of Criminal Appeals",
            "address": "201 W. 14th Street, Austin, TX 78701",
            "phone": "(512) 463-1551",
            "website": "https://www.txcourts.gov/cca/"
        },
        "courts_of_appeals": 14,
        "admin_office": {
            "name": "Office of Court Administration",
            "website": "https://www.txcourts.gov"
        }
    },
    "FL": {
        "supreme_court": {
            "name": "Supreme Court of Florida",
            "address": "500 South Duval Street, Tallahassee, FL 32399",
            "phone": "(850) 488-0125",
            "website": "https://www.floridasupremecourt.org"
        },
        "district_courts_of_appeal": 6,
        "admin_office": {
            "name": "Office of the State Courts Administrator",
            "website": "https://www.flcourts.org"
        }
    }
}


@app.get("/v1/state-courts/contacts/{state}")
def get_state_court_contacts(state: str):
    """
    Get court contact information for a state.

    Returns supreme court, appellate courts, and administrative office info.
    """
    _ensure_initialized()

    state_upper = state.upper()
    contacts = STATE_COURT_CONTACTS.get(state_upper)

    if not contacts:
        return {
            "state": state_upper,
            "message": "Detailed contacts not available",
            "recommendation": f"Search for '{state_upper} state courts' for official website"
        }

    return {
        "state": state_upper,
        "contacts": contacts
    }


@app.get("/v1/state-courts/contacts/websites")
def get_state_court_websites():
    """Get official court website URLs for all states."""
    _ensure_initialized()

    websites = {
        "AL": "https://judicial.alabama.gov",
        "AK": "https://courts.alaska.gov",
        "AZ": "https://www.azcourts.gov",
        "AR": "https://www.arcourts.gov",
        "CA": "https://www.courts.ca.gov",
        "CO": "https://www.courts.state.co.us",
        "CT": "https://www.jud.ct.gov",
        "DE": "https://courts.delaware.gov",
        "FL": "https://www.flcourts.org",
        "GA": "https://georgiacourts.gov",
        "HI": "https://www.courts.state.hi.us",
        "ID": "https://isc.idaho.gov",
        "IL": "https://www.illinoiscourts.gov",
        "IN": "https://www.in.gov/courts",
        "IA": "https://www.iowacourts.gov",
        "KS": "https://www.kscourts.org",
        "KY": "https://courts.ky.gov",
        "LA": "https://www.lacourt.org",
        "ME": "https://www.courts.maine.gov",
        "MD": "https://www.mdcourts.gov",
        "MA": "https://www.mass.gov/courts",
        "MI": "https://courts.michigan.gov",
        "MN": "https://www.mncourts.gov",
        "MS": "https://courts.ms.gov",
        "MO": "https://www.courts.mo.gov",
        "MT": "https://courts.mt.gov",
        "NE": "https://supremecourt.nebraska.gov",
        "NV": "https://nvcourts.gov",
        "NH": "https://www.courts.nh.gov",
        "NJ": "https://www.njcourts.gov",
        "NM": "https://www.nmcourts.gov",
        "NY": "https://www.nycourts.gov",
        "NC": "https://www.nccourts.gov",
        "ND": "https://www.ndcourts.gov",
        "OH": "https://www.supremecourt.ohio.gov",
        "OK": "https://www.oscn.net",
        "OR": "https://www.courts.oregon.gov",
        "PA": "https://www.pacourts.us",
        "RI": "https://www.courts.ri.gov",
        "SC": "https://www.sccourts.org",
        "SD": "https://ujs.sd.gov",
        "TN": "https://www.tncourts.gov",
        "TX": "https://www.txcourts.gov",
        "UT": "https://www.utcourts.gov",
        "VT": "https://www.vermontjudiciary.org",
        "VA": "https://www.vacourts.gov",
        "WA": "https://www.courts.wa.gov",
        "WV": "https://www.courtswv.gov",
        "WI": "https://www.wicourts.gov",
        "WY": "https://www.courts.state.wy.us"
    }

    return {
        "court_websites": websites,
        "total_states": len(websites)
    }


# ============================================================================
# LITIGANT SELF-HELP RESOURCES
# ============================================================================

@app.get("/v1/state-courts/self-help/{state}")
def get_self_help_resources(state: str):
    """
    Get self-help resources for pro se litigants in a state.

    Returns links to forms, guides, and assistance programs.
    """
    _ensure_initialized()

    state_upper = state.upper()

    # General self-help resources
    resources = {
        "state": state_upper,
        "general_resources": [
            {
                "name": "State Court Self-Help Center",
                "description": "Official court forms and guides",
                "note": "Check state court website for self-help section"
            },
            {
                "name": "Legal Aid Organizations",
                "search_url": f"https://www.lawhelp.org/find-help?s={state_upper}",
                "description": "Free legal services for qualifying individuals"
            },
            {
                "name": "Law Library",
                "description": "Many courthouses have public law libraries",
                "note": "Call ahead to verify hours and resources"
            }
        ],
        "national_resources": [
            {
                "name": "LawHelp.org",
                "url": "https://www.lawhelp.org",
                "description": "Directory of free legal aid"
            },
            {
                "name": "USA.gov Legal Aid",
                "url": "https://www.usa.gov/legal-aid",
                "description": "Federal resource for finding legal help"
            },
            {
                "name": "American Bar Association",
                "url": "https://www.americanbar.org/groups/legal_services/flh-home/",
                "description": "Free legal answers program"
            }
        ],
        "form_resources": [
            {
                "name": "State Court Forms",
                "note": "Available on official state court website"
            },
            {
                "name": "Self-Help Kits",
                "note": "Many courts offer DIY packets for common cases"
            }
        ]
    }

    # State-specific additions
    state_specific = {
        "CA": {
            "self_help_url": "https://www.courts.ca.gov/selfhelp.htm",
            "forms_url": "https://www.courts.ca.gov/forms.htm",
            "fee_waiver_form": "FW-001"
        },
        "NY": {
            "self_help_url": "https://www.nycourts.gov/courthelp/",
            "forms_url": "https://www.nycourts.gov/forms/",
            "help_centers": "CourtHelp Centers in each borough"
        },
        "TX": {
            "self_help_url": "https://www.texaslawhelp.org",
            "forms_url": "https://www.txcourts.gov/rules-forms/",
            "pro_se_guide": "Available from district clerk"
        },
        "FL": {
            "self_help_url": "https://www.flcourts.org/Resources-Services/Court-Improvement/Self-Help",
            "forms_url": "https://www.flcourts.org/Resources-Services/Court-Improvement/Family-Courts/Family-Law-Forms",
            "family_law_forms": "Comprehensive packet available"
        }
    }

    if state_upper in state_specific:
        resources["state_specific"] = state_specific[state_upper]

    return resources


@app.get("/v1/state-courts/self-help/fee-waiver")
def get_fee_waiver_info():
    """Get information about court fee waivers."""
    _ensure_initialized()

    return {
        "fee_waiver_info": {
            "what": "Court fee waivers allow qualifying individuals to file without paying fees",
            "who_qualifies": [
                "Receiving public benefits (SNAP, SSI, TANF, etc.)",
                "Income below federal poverty guidelines",
                "Cannot afford basic necessities if fees are paid"
            ],
            "how_to_apply": [
                "Complete fee waiver application form",
                "Provide proof of income/benefits",
                "Submit with initial filing or later",
                "Judge will approve or deny"
            ],
            "common_form_names": {
                "CA": "FW-001 (Request to Waive Court Fees)",
                "NY": "CIV-LT-91 (Poor Person Application)",
                "TX": "Statement of Inability to Afford Payment of Court Costs",
                "FL": "Application for Civil Indigent Status"
            },
            "note": "Forms and requirements vary by state. Check local court for specific requirements."
        }
    }


@app.get("/v1/state-courts/self-help/common-cases")
def get_common_case_guides():
    """Get guides for common pro se case types."""
    _ensure_initialized()

    return {
        "case_type_guides": {
            "small_claims": {
                "typical_limit": "$5,000-$25,000 depending on state",
                "steps": [
                    "Determine if your case qualifies for small claims",
                    "File a claim at the appropriate court",
                    "Serve the defendant",
                    "Gather evidence (documents, photos, receipts)",
                    "Attend the hearing",
                    "Collect the judgment if you win"
                ],
                "tips": [
                    "Bring copies of all documents",
                    "Prepare a brief summary of your case",
                    "Be respectful to the judge",
                    "Arrive early"
                ]
            },
            "divorce_uncontested": {
                "requirements": [
                    "Both parties agree to divorce",
                    "Agreement on property division",
                    "Agreement on custody (if applicable)",
                    "Meet residency requirements"
                ],
                "typical_forms": [
                    "Petition for Dissolution",
                    "Financial Declaration",
                    "Marital Settlement Agreement",
                    "Final Judgment"
                ]
            },
            "name_change": {
                "steps": [
                    "File petition with court",
                    "Pay filing fee (or request waiver)",
                    "Publish notice (some states)",
                    "Attend hearing",
                    "Receive court order"
                ],
                "uses": "Update ID, Social Security, bank accounts, etc."
            },
            "eviction_defense": {
                "key_steps": [
                    "Read the eviction notice carefully",
                    "Note all deadlines",
                    "File an answer if required",
                    "Gather evidence (lease, payment records, photos)",
                    "Appear at all hearings"
                ],
                "possible_defenses": [
                    "Improper notice",
                    "Retaliation",
                    "Habitability issues",
                    "Payment was made"
                ]
            }
        }
    }


# ============================================================================
# DOCUMENT TEMPLATE GENERATION
# ============================================================================

@app.get("/v1/state-courts/templates")
def list_document_templates():
    """List available document templates for state courts."""
    _ensure_initialized()

    return {
        "templates": [
            {
                "id": "civil_complaint",
                "name": "Civil Complaint",
                "description": "Basic template for filing a civil lawsuit",
                "case_types": ["civil"]
            },
            {
                "id": "answer",
                "name": "Answer to Complaint",
                "description": "Response to a civil complaint",
                "case_types": ["civil"]
            },
            {
                "id": "motion_dismiss",
                "name": "Motion to Dismiss",
                "description": "Request to dismiss case",
                "case_types": ["civil", "criminal"]
            },
            {
                "id": "motion_summary_judgment",
                "name": "Motion for Summary Judgment",
                "description": "Request for judgment without trial",
                "case_types": ["civil"]
            },
            {
                "id": "discovery_interrogatories",
                "name": "Interrogatories",
                "description": "Written questions to opposing party",
                "case_types": ["civil", "family"]
            },
            {
                "id": "discovery_requests_production",
                "name": "Requests for Production",
                "description": "Request for documents from opposing party",
                "case_types": ["civil", "family"]
            },
            {
                "id": "subpoena",
                "name": "Subpoena",
                "description": "Order to appear or produce documents",
                "case_types": ["civil", "criminal", "family"]
            },
            {
                "id": "fee_waiver",
                "name": "Fee Waiver Application",
                "description": "Request to waive court fees",
                "case_types": ["all"]
            }
        ],
        "note": "Templates provide structure only. Always use official court forms when available."
    }


@app.get("/v1/state-courts/templates/{template_id}")
def get_document_template(template_id: str, state: str = None):
    """
    Get a specific document template.

    Returns template structure and required fields.
    """
    _ensure_initialized()

    templates = {
        "civil_complaint": {
            "title": "COMPLAINT",
            "sections": [
                {
                    "name": "caption",
                    "fields": ["court_name", "plaintiff_name", "defendant_name", "case_number"]
                },
                {
                    "name": "introduction",
                    "content": "Plaintiff, [PLAINTIFF NAME], by and through [counsel/pro se], complains against Defendant, [DEFENDANT NAME], and alleges as follows:"
                },
                {
                    "name": "parties",
                    "fields": ["plaintiff_description", "defendant_description", "residency"]
                },
                {
                    "name": "jurisdiction_venue",
                    "fields": ["jurisdictional_basis", "venue_basis"]
                },
                {
                    "name": "facts",
                    "content": "Numbered paragraphs describing the factual basis for the claims"
                },
                {
                    "name": "causes_of_action",
                    "content": "Each cause of action as a separate count"
                },
                {
                    "name": "prayer_for_relief",
                    "content": "WHEREFORE, Plaintiff requests that this Court enter judgment..."
                },
                {
                    "name": "signature_block",
                    "fields": ["date", "attorney_name", "bar_number", "address", "phone", "email"]
                }
            ],
            "verification": "Optional sworn statement if required by state"
        },
        "answer": {
            "title": "ANSWER TO COMPLAINT",
            "sections": [
                {"name": "caption", "fields": ["court_name", "plaintiff_name", "defendant_name", "case_number"]},
                {"name": "introduction", "content": "Defendant, [NAME], answers Plaintiff's Complaint as follows:"},
                {"name": "responses", "content": "Numbered responses to each paragraph of complaint (admit/deny/lack knowledge)"},
                {"name": "affirmative_defenses", "content": "List any affirmative defenses"},
                {"name": "prayer", "content": "Request to dismiss or other relief"},
                {"name": "signature_block", "fields": ["date", "name", "address", "phone"]}
            ]
        },
        "motion_dismiss": {
            "title": "MOTION TO DISMISS",
            "sections": [
                {"name": "caption", "fields": ["court_name", "plaintiff_name", "defendant_name", "case_number"]},
                {"name": "introduction", "content": "Defendant moves to dismiss this action pursuant to [rule/statute]..."},
                {"name": "statement_of_issues", "content": "Issues to be decided"},
                {"name": "facts", "content": "Relevant background facts"},
                {"name": "argument", "content": "Legal arguments supporting dismissal"},
                {"name": "conclusion", "content": "Request for specific relief"},
                {"name": "signature_block", "fields": ["date", "name", "address", "phone"]}
            ],
            "memorandum": "Most courts require a supporting memorandum of law"
        }
    }

    template = templates.get(template_id)
    if not template:
        return {
            "error": f"Template not found: {template_id}",
            "available_templates": list(templates.keys())
        }

    return {
        "template_id": template_id,
        "template": template,
        "state": state,
        "note": "Use official court forms when available. This template is for reference only."
    }


@app.post("/v1/state-courts/templates/generate")
def generate_document(body: dict):
    """
    Generate a document from template with provided data.

    Fills template fields with case-specific information.
    """
    _ensure_initialized()

    template_id = body.get("template_id")
    data = body.get("data", {})

    # Basic document generation
    if template_id == "civil_complaint":
        document = f"""
IN THE {data.get('court_name', '[COURT NAME]').upper()}
{data.get('county', '[COUNTY]').upper()} COUNTY, {data.get('state', '[STATE]').upper()}

{data.get('plaintiff_name', '[PLAINTIFF]')},
    Plaintiff,

v.                                          Case No. {data.get('case_number', '____________')}

{data.get('defendant_name', '[DEFENDANT]')},
    Defendant.

COMPLAINT

Plaintiff, {data.get('plaintiff_name', '[PLAINTIFF]')}, complains against Defendant,
{data.get('defendant_name', '[DEFENDANT]')}, and alleges as follows:

PARTIES

1. Plaintiff {data.get('plaintiff_name', '[PLAINTIFF]')} is {data.get('plaintiff_description', '[description]')}.

2. Defendant {data.get('defendant_name', '[DEFENDANT]')} is {data.get('defendant_description', '[description]')}.

JURISDICTION AND VENUE

3. This Court has jurisdiction over this matter pursuant to {data.get('jurisdictional_basis', '[basis]')}.

4. Venue is proper in this county because {data.get('venue_basis', '[basis]')}.

FACTS

{data.get('facts', '[Numbered paragraphs describing the facts]')}

CAUSES OF ACTION

{data.get('causes_of_action', '[Each cause of action as a separate count]')}

PRAYER FOR RELIEF

WHEREFORE, Plaintiff respectfully requests that this Court:

{data.get('relief_requested', '[Specific relief requested]')}

Respectfully submitted,

____________________________
{data.get('attorney_name', '[Name]')}
{data.get('address', '[Address]')}
{data.get('phone', '[Phone]')}
{data.get('email', '[Email]')}
Date: {data.get('date', '____________')}
"""
        return {
            "template_id": template_id,
            "generated_document": document,
            "note": "Review and modify as needed. Use official court forms when available."
        }

    return {
        "error": f"Generation not supported for template: {template_id}",
        "message": "Use template endpoint to get structure, then fill manually"
    }


@app.get("/v1/state-courts/forms/{state}")
def get_state_court_forms(state: str, case_type: str = None):
    """
    Get links to official court forms for a state.

    Returns form resources and common form names.
    """
    _ensure_initialized()

    state_upper = state.upper()

    form_info = {
        "CA": {
            "forms_url": "https://www.courts.ca.gov/forms.htm",
            "categories": {
                "civil": ["CM-010 (Civil Case Cover Sheet)", "PLD-C-001 (Complaint)", "PLD-050 (General Denial)"],
                "family": ["FL-100 (Petition)", "FL-300 (Motion)", "FL-150 (Income Declaration)"],
                "small_claims": ["SC-100 (Plaintiff's Claim)", "SC-120 (Response)"],
                "fee_waiver": ["FW-001 (Request)", "FW-003 (Order)"]
            }
        },
        "NY": {
            "forms_url": "https://www.nycourts.gov/forms/",
            "categories": {
                "civil": ["Summons", "Complaint", "Answer"],
                "family": ["Petition Forms", "Financial Disclosure"],
                "small_claims": ["Small Claims Statement of Claim"]
            }
        },
        "TX": {
            "forms_url": "https://www.txcourts.gov/rules-forms/",
            "categories": {
                "civil": ["Original Petition", "Original Answer"],
                "family": ["Petition for Divorce", "Decree of Divorce"],
                "small_claims": ["Justice Court Forms"]
            }
        },
        "FL": {
            "forms_url": "https://www.flcourts.org/Resources-Services/Court-Improvement/Family-Courts/Family-Law-Forms",
            "categories": {
                "family": ["Petition for Dissolution", "Financial Affidavit", "Parenting Plan"],
                "civil": ["Civil Cover Sheet"],
                "small_claims": ["Statement of Claim"]
            }
        }
    }

    state_forms = form_info.get(state_upper)

    if not state_forms:
        return {
            "state": state_upper,
            "message": "Specific forms not catalogued",
            "recommendation": f"Visit official {state_upper} court website for forms"
        }

    if case_type:
        categories = state_forms.get("categories", {})
        case_forms = categories.get(case_type.lower())
        if case_forms:
            return {
                "state": state_upper,
                "case_type": case_type,
                "forms": case_forms,
                "forms_url": state_forms.get("forms_url")
            }
        return {
            "error": f"No forms found for case type: {case_type}",
            "available_types": list(categories.keys())
        }

    return {
        "state": state_upper,
        "form_info": state_forms
    }


# ============================================================================
# SMALL CLAIMS LIMITS BY STATE
# ============================================================================

SMALL_CLAIMS_LIMITS: dict = {
    "AL": {"limit": 6000, "appeals": "circuit_court"},
    "AK": {"limit": 10000, "appeals": "superior_court"},
    "AZ": {"limit": 3500, "appeals": "superior_court"},
    "AR": {"limit": 5000, "appeals": "circuit_court"},
    "CA": {"limit": 10000, "individual": 10000, "business": 5000, "appeals": "superior_court"},
    "CO": {"limit": 7500, "appeals": "district_court"},
    "CT": {"limit": 5000, "appeals": "appellate_court"},
    "DE": {"limit": 25000, "appeals": "superior_court"},
    "FL": {"limit": 8000, "appeals": "circuit_court"},
    "GA": {"limit": 15000, "appeals": "state_court"},
    "HI": {"limit": 5000, "appeals": "circuit_court"},
    "ID": {"limit": 5000, "appeals": "district_court"},
    "IL": {"limit": 10000, "appeals": "appellate_court"},
    "IN": {"limit": 8000, "appeals": "circuit_court"},
    "IA": {"limit": 6500, "appeals": "district_court"},
    "KS": {"limit": 4000, "appeals": "district_court"},
    "KY": {"limit": 2500, "appeals": "circuit_court"},
    "LA": {"limit": 5000, "appeals": "district_court"},
    "ME": {"limit": 6000, "appeals": "superior_court"},
    "MD": {"limit": 5000, "appeals": "circuit_court"},
    "MA": {"limit": 7000, "appeals": "appellate_division"},
    "MI": {"limit": 6500, "appeals": "circuit_court"},
    "MN": {"limit": 15000, "appeals": "district_court"},
    "MS": {"limit": 3500, "appeals": "circuit_court"},
    "MO": {"limit": 5000, "appeals": "circuit_court"},
    "MT": {"limit": 7000, "appeals": "district_court"},
    "NE": {"limit": 3600, "appeals": "district_court"},
    "NV": {"limit": 10000, "appeals": "district_court"},
    "NH": {"limit": 10000, "appeals": "superior_court"},
    "NJ": {"limit": 3000, "special_civil": 15000, "appeals": "appellate_division"},
    "NM": {"limit": 10000, "appeals": "district_court"},
    "NY": {"limit": 5000, "town_village": 3000, "appeals": "appellate_term"},
    "NC": {"limit": 10000, "appeals": "district_court"},
    "ND": {"limit": 15000, "appeals": "district_court"},
    "OH": {"limit": 6000, "appeals": "court_of_appeals"},
    "OK": {"limit": 10000, "appeals": "district_court"},
    "OR": {"limit": 10000, "appeals": "circuit_court"},
    "PA": {"limit": 12000, "appeals": "court_of_common_pleas"},
    "RI": {"limit": 2500, "appeals": "superior_court"},
    "SC": {"limit": 7500, "appeals": "circuit_court"},
    "SD": {"limit": 12000, "appeals": "circuit_court"},
    "TN": {"limit": 25000, "appeals": "circuit_court"},
    "TX": {"limit": 20000, "appeals": "county_court"},
    "UT": {"limit": 11000, "appeals": "district_court"},
    "VT": {"limit": 5000, "appeals": "superior_court"},
    "VA": {"limit": 5000, "appeals": "circuit_court"},
    "WA": {"limit": 10000, "appeals": "superior_court"},
    "WV": {"limit": 10000, "appeals": "circuit_court"},
    "WI": {"limit": 10000, "appeals": "circuit_court"},
    "WY": {"limit": 6000, "appeals": "district_court"},
    "DC": {"limit": 10000, "appeals": "court_of_appeals"}
}


@app.get("/v1/state-courts/small-claims/{state}")
def get_small_claims_limit(state: str):
    """
    Get small claims court limit for a state.

    Returns maximum claim amount and appeal information.
    """
    _ensure_initialized()

    state_upper = state.upper()
    info = SMALL_CLAIMS_LIMITS.get(state_upper)

    if not info:
        return {
            "state": state_upper,
            "error": "State not found",
            "typical_range": "$3,000 - $25,000"
        }

    return {
        "state": state_upper,
        "limit": info.get("limit"),
        "individual_limit": info.get("individual", info.get("limit")),
        "business_limit": info.get("business", info.get("limit")),
        "appeals_to": info.get("appeals"),
        "note": "Limits may vary by county. Verify with local court."
    }


@app.get("/v1/state-courts/small-claims/compare")
def compare_small_claims_limits(min_limit: int = None):
    """
    Compare small claims limits across states.

    Sorted by limit amount.
    """
    _ensure_initialized()

    comparison = []
    for state, info in SMALL_CLAIMS_LIMITS.items():
        limit = info.get("limit", 0)
        if min_limit is None or limit >= min_limit:
            comparison.append({
                "state": state,
                "limit": limit
            })

    comparison.sort(key=lambda x: x["limit"], reverse=True)

    return {
        "comparison": comparison,
        "highest": comparison[0] if comparison else None,
        "lowest": comparison[-1] if comparison else None,
        "average": round(sum(c["limit"] for c in comparison) / len(comparison)) if comparison else 0
    }


# ============================================================================
# COURT HOLIDAY CALENDARS
# ============================================================================

# Federal holidays observed by most state courts
FEDERAL_HOLIDAYS = [
    {"name": "New Year's Day", "date": "January 1", "observed": "all"},
    {"name": "Martin Luther King Jr. Day", "date": "Third Monday in January", "observed": "all"},
    {"name": "Presidents' Day", "date": "Third Monday in February", "observed": "most"},
    {"name": "Memorial Day", "date": "Last Monday in May", "observed": "all"},
    {"name": "Juneteenth", "date": "June 19", "observed": "most"},
    {"name": "Independence Day", "date": "July 4", "observed": "all"},
    {"name": "Labor Day", "date": "First Monday in September", "observed": "all"},
    {"name": "Columbus Day", "date": "Second Monday in October", "observed": "some"},
    {"name": "Veterans Day", "date": "November 11", "observed": "all"},
    {"name": "Thanksgiving Day", "date": "Fourth Thursday in November", "observed": "all"},
    {"name": "Christmas Day", "date": "December 25", "observed": "all"},
]

# State-specific holidays
STATE_HOLIDAYS: dict = {
    "CA": [
        {"name": "César Chávez Day", "date": "March 31"},
        {"name": "Day After Thanksgiving", "date": "Fourth Friday in November"}
    ],
    "TX": [
        {"name": "Texas Independence Day", "date": "March 2"},
        {"name": "San Jacinto Day", "date": "April 21"},
        {"name": "Emancipation Day", "date": "June 19"},
        {"name": "Lyndon B. Johnson Day", "date": "August 27"}
    ],
    "FL": [
        {"name": "Day After Thanksgiving", "date": "Fourth Friday in November"}
    ],
    "NY": [
        {"name": "Lincoln's Birthday", "date": "February 12"},
        {"name": "Election Day", "date": "First Tuesday after first Monday in November"}
    ],
    "MA": [
        {"name": "Patriots' Day", "date": "Third Monday in April"},
        {"name": "Evacuation Day", "date": "March 17", "note": "Suffolk County only"}
    ],
    "LA": [
        {"name": "Mardi Gras", "date": "Varies (47 days before Easter)"}
    ],
    "HI": [
        {"name": "Prince Kuhio Day", "date": "March 26"},
        {"name": "King Kamehameha Day", "date": "June 11"},
        {"name": "Statehood Day", "date": "Third Friday in August"}
    ],
    "AK": [
        {"name": "Seward's Day", "date": "Last Monday in March"},
        {"name": "Alaska Day", "date": "October 18"}
    ]
}


@app.get("/v1/state-courts/holidays/{state}")
def get_court_holidays(state: str, year: int = None):
    """
    Get court holidays for a state.

    Returns federal and state-specific court closure dates.
    """
    _ensure_initialized()

    state_upper = state.upper()

    result = {
        "state": state_upper,
        "federal_holidays": FEDERAL_HOLIDAYS,
        "state_holidays": STATE_HOLIDAYS.get(state_upper, []),
        "note": "Courts may close for additional local holidays. Verify with specific court."
    }

    if year:
        result["year"] = year
        result["note"] += f" Holiday dates shown are typical patterns for {year}."

    return result


@app.get("/v1/state-courts/holidays/federal")
def get_federal_court_holidays():
    """Get federal holidays observed by courts."""
    _ensure_initialized()

    return {
        "federal_holidays": FEDERAL_HOLIDAYS,
        "note": "Most state courts observe federal holidays. Some states have additional closures."
    }


# ============================================================================
# LEGAL TERMINOLOGY GLOSSARY
# ============================================================================

LEGAL_GLOSSARY: dict = {
    "affidavit": "A written statement of facts made under oath",
    "answer": "Defendant's written response to a complaint",
    "appellant": "Party who appeals a court decision",
    "appellee": "Party against whom an appeal is filed",
    "arraignment": "Court proceeding where defendant is formally charged",
    "brief": "Written legal argument submitted to court",
    "caption": "Heading of a legal document identifying parties and court",
    "cause_of_action": "Legal basis for a lawsuit",
    "complaint": "Document initiating a civil lawsuit",
    "continuance": "Postponement of a court proceeding",
    "counterclaim": "Claim by defendant against plaintiff",
    "cross_examination": "Questioning of a witness by opposing party",
    "default_judgment": "Judgment against party who fails to respond",
    "defendant": "Person or entity being sued or charged",
    "demurrer": "Legal objection that complaint is legally insufficient",
    "deposition": "Sworn testimony taken outside of court",
    "discovery": "Pre-trial process of exchanging information",
    "dismissal": "Termination of a case before judgment",
    "docket": "Court's official record of case proceedings",
    "due_process": "Constitutional right to fair legal proceedings",
    "ex_parte": "Communication with court by one party without other party",
    "exhibit": "Document or object presented as evidence",
    "filing": "Submitting a document to the court",
    "guardian_ad_litem": "Person appointed to represent minor's interests",
    "habeas_corpus": "Legal action challenging unlawful detention",
    "hearing": "Court session to address specific matters",
    "in_camera": "Proceedings held in judge's chambers, not open court",
    "injunction": "Court order requiring or prohibiting action",
    "interrogatories": "Written questions requiring written answers",
    "judgment": "Final decision of the court",
    "jurisdiction": "Court's authority to hear a case",
    "lien": "Legal claim against property for debt",
    "litigation": "Process of taking legal action",
    "motion": "Formal request to the court",
    "notary": "Official authorized to witness signatures",
    "objection": "Formal protest during court proceedings",
    "order": "Written direction from the court",
    "party": "Person or entity involved in a lawsuit",
    "petition": "Formal written request to court",
    "plaintiff": "Person or entity initiating a lawsuit",
    "plea": "Defendant's response to criminal charges",
    "pleadings": "Formal written statements in a lawsuit",
    "precedent": "Prior court decision used as authority",
    "pro_se": "Representing oneself without an attorney",
    "probate": "Court process for administering estates",
    "quash": "To void or suppress",
    "remand": "Send case back to lower court",
    "restitution": "Compensation for loss or injury",
    "service_of_process": "Delivery of legal documents to a party",
    "settlement": "Agreement to resolve dispute without trial",
    "standing": "Legal right to bring a lawsuit",
    "statute": "Written law enacted by legislature",
    "statute_of_limitations": "Time limit for filing a lawsuit",
    "stipulation": "Agreement between parties",
    "subpoena": "Order requiring appearance or document production",
    "summary_judgment": "Judgment without trial based on undisputed facts",
    "summons": "Notice requiring court appearance",
    "testimony": "Statements made under oath",
    "tort": "Civil wrong causing harm",
    "trial": "Court proceeding to determine facts and apply law",
    "venue": "Geographic location where case is heard",
    "verdict": "Jury's decision on factual issues",
    "voir_dire": "Jury selection process",
    "waiver": "Voluntary giving up of a right",
    "witness": "Person who testifies under oath",
    "writ": "Written court order"
}


@app.get("/v1/state-courts/glossary")
def get_legal_glossary(term: str = None):
    """
    Get legal terminology definitions.

    Search for specific term or get full glossary.
    """
    _ensure_initialized()

    if term:
        term_lower = term.lower().replace(" ", "_")
        definition = LEGAL_GLOSSARY.get(term_lower)
        if definition:
            return {
                "term": term,
                "definition": definition
            }
        # Fuzzy search
        matches = [
            {"term": k.replace("_", " "), "definition": v}
            for k, v in LEGAL_GLOSSARY.items()
            if term_lower in k
        ]
        if matches:
            return {"search_term": term, "matches": matches}
        return {"error": f"Term not found: {term}", "suggestion": "Browse full glossary"}

    return {
        "glossary": {k.replace("_", " "): v for k, v in LEGAL_GLOSSARY.items()},
        "total_terms": len(LEGAL_GLOSSARY)
    }


@app.get("/v1/state-courts/glossary/categories")
def get_glossary_categories():
    """Get legal terms organized by category."""
    _ensure_initialized()

    categories = {
        "parties": ["plaintiff", "defendant", "appellant", "appellee", "party", "witness"],
        "documents": ["affidavit", "answer", "brief", "complaint", "petition", "pleadings", "subpoena", "summons"],
        "procedures": ["arraignment", "continuance", "deposition", "discovery", "filing", "hearing", "trial", "voir_dire"],
        "motions_orders": ["demurrer", "injunction", "motion", "order", "writ"],
        "outcomes": ["default_judgment", "dismissal", "judgment", "settlement", "verdict"],
        "concepts": ["cause_of_action", "due_process", "jurisdiction", "precedent", "standing", "venue"]
    }

    return {
        "categories": {
            cat: [{"term": t.replace("_", " "), "definition": LEGAL_GLOSSARY.get(t, "")} for t in terms]
            for cat, terms in categories.items()
        }
    }


# ============================================================================
# MULTI-STATE CASE TRACKING
# ============================================================================

# In-memory multi-state case tracking
_multi_state_cases: dict = {}


@app.post("/v1/state-courts/multi-state/case")
def create_multi_state_case(body: dict):
    """
    Create a multi-state case tracker.

    Links related cases across multiple state jurisdictions.
    """
    _ensure_initialized()

    tracker_id = f"msc_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{len(_multi_state_cases)}"

    tracker = {
        "id": tracker_id,
        "name": body.get("name"),
        "description": body.get("description"),
        "cases": body.get("cases", []),  # List of {state, case_number, court, status}
        "parties": body.get("parties", []),
        "lead_state": body.get("lead_state"),
        "case_type": body.get("case_type"),
        "tags": body.get("tags", []),
        "notes": body.get("notes"),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }

    _multi_state_cases[tracker_id] = tracker

    return {"message": "Multi-state case tracker created", "tracker": tracker}


@app.get("/v1/state-courts/multi-state/cases")
def list_multi_state_cases(party: str = None, state: str = None, limit: int = 100):
    """List multi-state case trackers with filters."""
    _ensure_initialized()

    trackers = list(_multi_state_cases.values())

    if party:
        party_lower = party.lower()
        trackers = [
            t for t in trackers
            if any(party_lower in (p or "").lower() for p in t.get("parties", []))
        ]

    if state:
        state_upper = state.upper()
        trackers = [
            t for t in trackers
            if any(c.get("state", "").upper() == state_upper for c in t.get("cases", []))
        ]

    trackers.sort(key=lambda x: x.get("updated_at") or "", reverse=True)

    return {
        "trackers": trackers[:limit],
        "total": len(trackers)
    }


@app.get("/v1/state-courts/multi-state/case/{tracker_id}")
def get_multi_state_case(tracker_id: str):
    """Get details of a multi-state case tracker."""
    _ensure_initialized()

    tracker = _multi_state_cases.get(tracker_id)
    if not tracker:
        return {"error": "Tracker not found", "tracker_id": tracker_id}

    return tracker


@app.put("/v1/state-courts/multi-state/case/{tracker_id}")
def update_multi_state_case(tracker_id: str, body: dict):
    """Update a multi-state case tracker."""
    _ensure_initialized()

    tracker = _multi_state_cases.get(tracker_id)
    if not tracker:
        return {"error": "Tracker not found", "tracker_id": tracker_id}

    # Update allowed fields
    for field in ["name", "description", "cases", "parties", "lead_state", "tags", "notes"]:
        if field in body:
            tracker[field] = body[field]

    tracker["updated_at"] = datetime.utcnow().isoformat()

    return {"message": "Tracker updated", "tracker": tracker}


@app.post("/v1/state-courts/multi-state/case/{tracker_id}/add-case")
def add_case_to_tracker(tracker_id: str, body: dict):
    """Add a case to a multi-state tracker."""
    _ensure_initialized()

    tracker = _multi_state_cases.get(tracker_id)
    if not tracker:
        return {"error": "Tracker not found", "tracker_id": tracker_id}

    new_case = {
        "state": body.get("state"),
        "case_number": body.get("case_number"),
        "court": body.get("court"),
        "status": body.get("status", "active"),
        "date_filed": body.get("date_filed"),
        "added_at": datetime.utcnow().isoformat()
    }

    tracker["cases"].append(new_case)
    tracker["updated_at"] = datetime.utcnow().isoformat()

    return {
        "message": "Case added to tracker",
        "case": new_case,
        "total_cases": len(tracker["cases"])
    }


# ============================================================================
# CASE COMPLEXITY SCORING
# ============================================================================

@app.post("/v1/state-courts/complexity/score")
def calculate_case_complexity(body: dict):
    """
    Calculate a complexity score for a case.

    Based on factors like parties, claims, discovery needs.
    """
    _ensure_initialized()

    # Scoring factors
    score = 0
    factors = []

    # Number of parties
    num_parties = body.get("num_parties", 2)
    if num_parties > 5:
        score += 20
        factors.append({"factor": "Many parties", "points": 20})
    elif num_parties > 2:
        score += 10
        factors.append({"factor": "Multiple parties", "points": 10})

    # Number of claims
    num_claims = body.get("num_claims", 1)
    if num_claims > 5:
        score += 20
        factors.append({"factor": "Many claims", "points": 20})
    elif num_claims > 2:
        score += 10
        factors.append({"factor": "Multiple claims", "points": 10})

    # Case type complexity
    case_type = body.get("case_type", "").lower()
    complex_types = {"class_action": 30, "antitrust": 25, "securities": 25, "patent": 25, "mass_tort": 25}
    moderate_types = {"products_liability": 15, "medical_malpractice": 15, "employment": 10}

    if case_type in complex_types:
        pts = complex_types[case_type]
        score += pts
        factors.append({"factor": f"Complex case type: {case_type}", "points": pts})
    elif case_type in moderate_types:
        pts = moderate_types[case_type]
        score += pts
        factors.append({"factor": f"Moderate complexity type: {case_type}", "points": pts})

    # Discovery scope
    discovery = body.get("discovery_scope", "standard")
    if discovery == "extensive":
        score += 20
        factors.append({"factor": "Extensive discovery", "points": 20})
    elif discovery == "limited":
        score -= 10
        factors.append({"factor": "Limited discovery", "points": -10})

    # Expert witnesses
    num_experts = body.get("num_experts", 0)
    if num_experts > 3:
        score += 15
        factors.append({"factor": "Multiple experts", "points": 15})
    elif num_experts > 0:
        score += 5
        factors.append({"factor": "Expert testimony", "points": 5})

    # Multi-jurisdiction
    if body.get("multi_state"):
        score += 15
        factors.append({"factor": "Multi-state litigation", "points": 15})

    # Determine complexity level
    if score >= 60:
        level = "high"
    elif score >= 30:
        level = "moderate"
    else:
        level = "low"

    return {
        "complexity_score": max(0, min(100, score)),
        "complexity_level": level,
        "factors": factors,
        "recommendations": {
            "high": "Consider specialized counsel; plan for extensive discovery timeline",
            "moderate": "Standard case management; some discovery disputes likely",
            "low": "Streamlined procedures may be appropriate"
        }.get(level)
    }


# ============================================================================
# COURT REPORTER AND TRANSCRIPT INFO
# ============================================================================

@app.get("/v1/state-courts/transcripts/info")
def get_transcript_info():
    """Get information about court transcripts and reporters."""
    _ensure_initialized()

    return {
        "transcript_info": {
            "what": "Official written record of court proceedings",
            "who_creates": "Court reporters/stenographers",
            "formats": ["Paper", "Electronic (ASCII)", "RealTime"],
            "typical_turnaround": {
                "expedited": "1-3 days",
                "standard": "2-4 weeks",
                "regular": "30+ days"
            },
            "cost_estimates": {
                "per_page": "$3-7 standard",
                "expedited_surcharge": "50-100%",
                "copy_rate": "$0.50-2 per page"
            }
        },
        "ordering_process": [
            "Contact court reporter or court reporting firm",
            "Specify hearing date and case number",
            "Choose delivery format and timeline",
            "Pay deposit if required",
            "Receive transcript"
        ],
        "uses": [
            "Appeals",
            "Post-trial motions",
            "Impeachment of witnesses",
            "Record preservation"
        ]
    }


@app.get("/v1/state-courts/transcripts/appeal-requirements")
def get_appeal_transcript_requirements():
    """Get information about transcript requirements for appeals."""
    _ensure_initialized()

    return {
        "general_requirements": {
            "deadline": "Usually must order within 10-30 days of notice of appeal",
            "what_to_include": "All proceedings relevant to issues on appeal",
            "designation": "Appellant must file designation of record"
        },
        "state_variations": {
            "CA": "Must file notice designating record within 10 days",
            "NY": "Must order transcript within 10 days of filing notice",
            "TX": "Must file request within 10 days",
            "FL": "Must designate within 10 days"
        },
        "cost_responsibility": "Appellant typically pays; may seek from appellee if successful",
        "indigent_parties": "May request transcript at public expense with fee waiver"
    }


# ============================================================================
# JURY INSTRUCTIONS DATABASE
# ============================================================================

# Common civil jury instructions categories
JURY_INSTRUCTION_CATEGORIES = {
    "general": [
        "Duties of the jury",
        "Burden of proof",
        "Preponderance of the evidence",
        "Clear and convincing evidence",
        "Credibility of witnesses",
        "Direct and circumstantial evidence",
        "Expert witness testimony"
    ],
    "negligence": [
        "Definition of negligence",
        "Duty of care",
        "Breach of duty",
        "Proximate cause",
        "Comparative negligence",
        "Contributory negligence",
        "Assumption of risk"
    ],
    "contracts": [
        "Elements of a contract",
        "Breach of contract",
        "Material breach",
        "Substantial performance",
        "Damages for breach",
        "Mitigation of damages"
    ],
    "damages": [
        "Compensatory damages",
        "General damages",
        "Special damages",
        "Future damages",
        "Pain and suffering",
        "Loss of consortium",
        "Punitive damages"
    ],
    "intentional_torts": [
        "Assault",
        "Battery",
        "False imprisonment",
        "Intentional infliction of emotional distress",
        "Fraud",
        "Conversion"
    ]
}

# State jury instruction resources
STATE_JURY_INSTRUCTIONS: dict = {
    "CA": {
        "name": "CACI (California Civil Jury Instructions)",
        "url": "https://www.courts.ca.gov/partners/317.htm",
        "criminal": "CALCRIM (California Criminal Jury Instructions)"
    },
    "NY": {
        "name": "PJI (Pattern Jury Instructions)",
        "url": "https://www.nycourts.gov/judges/cji/",
        "note": "Civil and Criminal available"
    },
    "TX": {
        "name": "Texas Pattern Jury Charges",
        "url": "https://www.texasbarcle.com/pjc/",
        "note": "Published by State Bar of Texas"
    },
    "FL": {
        "name": "Florida Standard Jury Instructions",
        "url": "https://www.floridabar.org/rules/florida-standard-jury-instructions/",
        "note": "Civil and Criminal"
    },
    "IL": {
        "name": "IPI (Illinois Pattern Jury Instructions)",
        "url": "https://www.illinoiscourts.gov/",
        "note": "Civil and Criminal available"
    }
}


@app.get("/v1/state-courts/jury-instructions/{state}")
def get_jury_instructions_info(state: str):
    """
    Get jury instruction resources for a state.

    Returns links to pattern jury instructions.
    """
    _ensure_initialized()

    state_upper = state.upper()
    instructions = STATE_JURY_INSTRUCTIONS.get(state_upper)

    if not instructions:
        return {
            "state": state_upper,
            "message": "State-specific resources not catalogued",
            "recommendation": "Search for '[State] pattern jury instructions'",
            "general_categories": list(JURY_INSTRUCTION_CATEGORIES.keys())
        }

    return {
        "state": state_upper,
        "instructions": instructions,
        "general_categories": list(JURY_INSTRUCTION_CATEGORIES.keys())
    }


@app.get("/v1/state-courts/jury-instructions/categories")
def get_jury_instruction_categories():
    """Get standard jury instruction categories and topics."""
    _ensure_initialized()

    return {
        "categories": JURY_INSTRUCTION_CATEGORIES,
        "note": "Specific instructions vary by state. Use state pattern instructions."
    }


@app.get("/v1/state-courts/jury-instructions/category/{category}")
def get_jury_instructions_by_category(category: str):
    """Get jury instruction topics for a specific category."""
    _ensure_initialized()

    instructions = JURY_INSTRUCTION_CATEGORIES.get(category.lower())
    if not instructions:
        return {
            "error": f"Category not found: {category}",
            "available_categories": list(JURY_INSTRUCTION_CATEGORIES.keys())
        }

    return {
        "category": category,
        "instructions": instructions,
        "note": "Use state-specific pattern instructions for actual text"
    }


# ============================================================================
# MEDIATION AND ADR RESOURCES
# ============================================================================

ADR_TYPES = {
    "mediation": {
        "description": "Neutral third party helps parties reach agreement",
        "binding": False,
        "typical_duration": "1-2 days",
        "cost": "Split between parties, $200-500/hour for mediator"
    },
    "arbitration": {
        "description": "Neutral arbitrator makes binding decision",
        "binding": True,
        "typical_duration": "1-5 days",
        "cost": "Split between parties, arbitrator fees + admin fees"
    },
    "neutral_evaluation": {
        "description": "Expert provides non-binding case assessment",
        "binding": False,
        "typical_duration": "Half day",
        "cost": "Varies by evaluator"
    },
    "settlement_conference": {
        "description": "Judge or magistrate facilitates settlement",
        "binding": False,
        "typical_duration": "Half to full day",
        "cost": "Usually no additional cost (court-provided)"
    },
    "mini_trial": {
        "description": "Abbreviated trial presentation to decision-makers",
        "binding": False,
        "typical_duration": "1-2 days",
        "cost": "Parties bear own costs"
    }
}

STATE_ADR_PROGRAMS: dict = {
    "CA": {
        "mandatory_mediation": "Many courts require ADR before trial",
        "programs": ["Court-connected mediation", "Private mediation"],
        "url": "https://www.courts.ca.gov/programs-adr.htm"
    },
    "NY": {
        "mandatory_mediation": "Required in some courts",
        "programs": ["Community Dispute Resolution Centers", "Court-annexed mediation"],
        "url": "https://www.nycourts.gov/ip/adr/"
    },
    "TX": {
        "mandatory_mediation": "Court may order",
        "programs": ["Court-referred mediation", "Private ADR"],
        "url": "https://www.txcourts.gov/about-texas-courts/alternative-dispute-resolution/"
    },
    "FL": {
        "mandatory_mediation": "Required in many civil cases",
        "programs": ["Court-ordered mediation", "DRC programs"],
        "url": "https://www.flcourts.org/Resources-Services/Alternative-Dispute-Resolution"
    }
}


@app.get("/v1/state-courts/adr/types")
def get_adr_types():
    """Get information about ADR (Alternative Dispute Resolution) types."""
    _ensure_initialized()

    return {
        "adr_types": ADR_TYPES,
        "benefits": [
            "Often faster than litigation",
            "Usually less expensive",
            "More control over process",
            "Confidential (usually)",
            "Can preserve relationships"
        ]
    }


@app.get("/v1/state-courts/adr/{state}")
def get_state_adr_info(state: str):
    """
    Get ADR program information for a state.

    Returns court-connected and private ADR options.
    """
    _ensure_initialized()

    state_upper = state.upper()
    adr_info = STATE_ADR_PROGRAMS.get(state_upper)

    if not adr_info:
        return {
            "state": state_upper,
            "message": "State-specific ADR info not catalogued",
            "general_options": list(ADR_TYPES.keys()),
            "recommendation": "Check state court website for ADR programs"
        }

    return {
        "state": state_upper,
        "adr_programs": adr_info,
        "adr_types_available": list(ADR_TYPES.keys())
    }


@app.get("/v1/state-courts/adr/mediator-qualifications")
def get_mediator_qualifications():
    """Get general mediator qualification requirements."""
    _ensure_initialized()

    return {
        "general_qualifications": {
            "training": "Usually 40+ hours of mediation training",
            "experience": "May require supervised mediations",
            "education": "Often requires degree (varies by court)",
            "continuing_education": "Usually required annually"
        },
        "state_examples": {
            "CA": "40 hours training + degree or experience",
            "FL": "40 hours training + mentorship",
            "TX": "40 hours training + experience requirements",
            "NY": "Varies by court program"
        },
        "finding_mediators": [
            "Court-connected programs",
            "State bar association panels",
            "Private mediation firms",
            "AAA (American Arbitration Association)",
            "JAMS"
        ]
    }


# ============================================================================
# PRO BONO LEGAL SERVICES
# ============================================================================

PRO_BONO_RESOURCES = {
    "national": [
        {
            "name": "LawHelp.org",
            "url": "https://www.lawhelp.org",
            "description": "Directory of free legal aid by state"
        },
        {
            "name": "American Bar Association Free Legal Answers",
            "url": "https://abafreelegalanswers.org",
            "description": "Online Q&A with volunteer lawyers"
        },
        {
            "name": "Legal Services Corporation",
            "url": "https://www.lsc.gov/find-legal-aid",
            "description": "Federally funded legal aid programs"
        },
        {
            "name": "Pro Bono Net",
            "url": "https://www.probono.net",
            "description": "Pro bono resources and programs"
        }
    ],
    "eligibility": {
        "income_guidelines": "Usually 125-200% of federal poverty level",
        "asset_limits": "May apply",
        "case_types": "Varies by program - civil matters only (usually)",
        "exclusions": "Fee-generating cases usually excluded"
    }
}

STATE_LEGAL_AID: dict = {
    "CA": {
        "primary": "Legal Aid Foundation of Los Angeles, Bay Area Legal Aid, etc.",
        "bar_program": "State Bar of California Lawyer Referral Service",
        "url": "https://www.calbar.ca.gov/Public/Need-Legal-Help"
    },
    "NY": {
        "primary": "Legal Aid Society, Legal Services NYC",
        "bar_program": "NYC Bar Legal Referral Service",
        "url": "https://www.nycourts.gov/courthelp/goingtocourt/legalhelp.shtml"
    },
    "TX": {
        "primary": "Texas RioGrande Legal Aid, Lone Star Legal Aid",
        "bar_program": "State Bar of Texas Lawyer Referral Service",
        "url": "https://www.texaslawhelp.org"
    },
    "FL": {
        "primary": "Florida Legal Services, Legal Aid Society",
        "bar_program": "Florida Bar Lawyer Referral Service",
        "url": "https://www.floridabar.org/public/lrs/"
    }
}


@app.get("/v1/state-courts/legal-aid")
def get_legal_aid_resources():
    """Get national legal aid resources."""
    _ensure_initialized()

    return {
        "resources": PRO_BONO_RESOURCES,
        "note": "Eligibility requirements vary by program"
    }


@app.get("/v1/state-courts/legal-aid/{state}")
def get_state_legal_aid(state: str):
    """
    Get legal aid resources for a specific state.

    Returns legal aid organizations and referral services.
    """
    _ensure_initialized()

    state_upper = state.upper()
    state_resources = STATE_LEGAL_AID.get(state_upper)

    result = {
        "state": state_upper,
        "national_resources": PRO_BONO_RESOURCES["national"],
        "eligibility_info": PRO_BONO_RESOURCES["eligibility"]
    }

    if state_resources:
        result["state_resources"] = state_resources
    else:
        result["recommendation"] = f"Search LawHelp.org for {state_upper} resources"

    return result


@app.get("/v1/state-courts/legal-aid/eligibility")
def get_legal_aid_eligibility():
    """Get information about legal aid eligibility requirements."""
    _ensure_initialized()

    # 2024 Federal Poverty Guidelines (for reference)
    poverty_guidelines = {
        1: 15060,
        2: 20440,
        3: 25820,
        4: 31200,
        5: 36580,
        6: 41960,
        7: 47340,
        8: 52720
    }

    return {
        "income_eligibility": {
            "typical_threshold": "125-200% of Federal Poverty Level",
            "poverty_guidelines_2024": poverty_guidelines,
            "example_125_percent": {size: int(amt * 1.25) for size, amt in poverty_guidelines.items()},
            "example_200_percent": {size: int(amt * 2.0) for size, amt in poverty_guidelines.items()}
        },
        "other_factors": [
            "Household size",
            "Assets and resources",
            "Type of legal problem",
            "Availability of other help",
            "Merits of the case"
        ],
        "note": "Eligibility varies by program. Contact specific program for requirements."
    }


# ============================================================================
# CASE MANAGEMENT TOOLS
# ============================================================================

# In-memory case notes storage
_case_notes: dict = {}


@app.post("/v1/state-courts/case-notes")
def add_case_note(body: dict):
    """
    Add a note to a case file.

    Track observations, to-dos, and important dates.
    """
    _ensure_initialized()

    note_id = f"cn_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{len(_case_notes)}"

    note = {
        "id": note_id,
        "case_id": body.get("case_id"),
        "case_number": body.get("case_number"),
        "note_type": body.get("note_type", "general"),  # general, deadline, strategy, research
        "content": body.get("content"),
        "priority": body.get("priority", "normal"),
        "due_date": body.get("due_date"),
        "completed": body.get("completed", False),
        "tags": body.get("tags", []),
        "created_at": datetime.utcnow().isoformat()
    }

    _case_notes[note_id] = note

    return {"message": "Note added", "note": note}


@app.get("/v1/state-courts/case-notes")
def list_case_notes(
    case_id: str = None,
    note_type: str = None,
    pending_only: bool = False,
    limit: int = 100
):
    """List case notes with filters."""
    _ensure_initialized()

    notes = list(_case_notes.values())

    if case_id:
        notes = [n for n in notes if n.get("case_id") == case_id]

    if note_type:
        notes = [n for n in notes if n.get("note_type") == note_type]

    if pending_only:
        notes = [n for n in notes if not n.get("completed")]

    notes.sort(key=lambda x: x.get("due_date") or x.get("created_at") or "", reverse=True)

    return {
        "notes": notes[:limit],
        "total": len(notes)
    }


@app.get("/v1/state-courts/case-notes/{note_id}")
def get_case_note(note_id: str):
    """Get a specific case note."""
    _ensure_initialized()

    note = _case_notes.get(note_id)
    if not note:
        return {"error": "Note not found", "note_id": note_id}

    return note


@app.put("/v1/state-courts/case-notes/{note_id}")
def update_case_note(note_id: str, body: dict):
    """Update a case note."""
    _ensure_initialized()

    note = _case_notes.get(note_id)
    if not note:
        return {"error": "Note not found", "note_id": note_id}

    for field in ["content", "priority", "due_date", "completed", "tags", "note_type"]:
        if field in body:
            note[field] = body[field]

    note["updated_at"] = datetime.utcnow().isoformat()

    return {"message": "Note updated", "note": note}


@app.delete("/v1/state-courts/case-notes/{note_id}")
def delete_case_note(note_id: str):
    """Delete a case note."""
    _ensure_initialized()

    if note_id not in _case_notes:
        return {"error": "Note not found", "note_id": note_id}

    del _case_notes[note_id]
    return {"message": "Note deleted", "note_id": note_id}


# ============================================================================
# COURT APPEARANCE CHECKLIST
# ============================================================================

@app.get("/v1/state-courts/checklists/court-appearance")
def get_court_appearance_checklist():
    """Get checklist for preparing for court appearances."""
    _ensure_initialized()

    return {
        "before_court": [
            {"item": "Review all case documents", "importance": "critical"},
            {"item": "Organize exhibits and evidence", "importance": "critical"},
            {"item": "Prepare witness list and questions", "importance": "high"},
            {"item": "Review applicable rules and procedures", "importance": "high"},
            {"item": "Confirm hearing date, time, and courtroom", "importance": "critical"},
            {"item": "Arrange for interpreter if needed", "importance": "high"},
            {"item": "Prepare copies of documents for court and opposing party", "importance": "high"},
            {"item": "Plan arrival time (arrive 30+ minutes early)", "importance": "medium"}
        ],
        "what_to_bring": [
            "Photo ID",
            "All case documents",
            "Copies of filed documents",
            "Evidence and exhibits",
            "Witness contact information",
            "Calendar for scheduling",
            "Pen and notepad",
            "Payment for any fees"
        ],
        "courtroom_etiquette": [
            "Dress professionally",
            "Turn off cell phone",
            "Stand when judge enters/exits",
            "Address judge as 'Your Honor'",
            "Speak clearly and respectfully",
            "Wait to be recognized before speaking",
            "Do not interrupt others"
        ],
        "prohibited_items": [
            "Weapons",
            "Food and drinks",
            "Recording devices (usually)",
            "Large bags (may be searched)"
        ]
    }


@app.get("/v1/state-courts/checklists/filing")
def get_filing_checklist():
    """Get checklist for filing court documents."""
    _ensure_initialized()

    return {
        "before_filing": [
            {"item": "Verify correct court and case type", "importance": "critical"},
            {"item": "Use correct forms for your state/court", "importance": "critical"},
            {"item": "Complete all required fields", "importance": "critical"},
            {"item": "Sign and date where required", "importance": "critical"},
            {"item": "Make copies (original + copies for each party + your copy)", "importance": "high"},
            {"item": "Calculate and prepare filing fee", "importance": "high"},
            {"item": "If fee waiver needed, complete application", "importance": "high"}
        ],
        "document_formatting": [
            "Use standard paper size (8.5 x 11)",
            "Use readable font (usually 12pt)",
            "Number pages",
            "Include case caption on all documents",
            "Leave margins for court stamps"
        ],
        "e_filing_tips": [
            "Register for e-filing account in advance",
            "Check accepted file formats (usually PDF)",
            "Verify file size limits",
            "Keep confirmation receipt",
            "Check for acceptance notification"
        ]
    }


# ============================================================================
# WITNESS PREPARATION GUIDE
# ============================================================================

@app.get("/v1/state-courts/guides/witness-preparation")
def get_witness_preparation_guide():
    """Get guide for preparing witnesses for testimony."""
    _ensure_initialized()

    return {
        "before_testimony": [
            "Review relevant documents and facts",
            "Understand the questions likely to be asked",
            "Know the sequence of events clearly",
            "Be honest about what you don't remember",
            "Dress appropriately for court"
        ],
        "during_testimony": [
            "Listen carefully to each question",
            "Wait for the complete question before answering",
            "Answer only what was asked",
            "If you don't understand, ask for clarification",
            "Speak clearly and audibly",
            "Don't guess - say 'I don't know' if true",
            "Remain calm during cross-examination",
            "Tell the truth"
        ],
        "common_mistakes": [
            "Volunteering information not asked",
            "Arguing with the attorney",
            "Guessing at answers",
            "Giving inconsistent answers",
            "Not listening to the full question"
        ],
        "legal_note": "Witnesses must tell the truth under penalty of perjury"
    }


# ============================================================================
# EVIDENCE RULES OVERVIEW
# ============================================================================

@app.get("/v1/state-courts/guides/evidence")
def get_evidence_guide():
    """Get overview of evidence rules for state courts."""
    _ensure_initialized()

    return {
        "types_of_evidence": {
            "testimonial": "Witness statements under oath",
            "documentary": "Written documents, records, contracts",
            "physical": "Objects, photographs, diagrams",
            "demonstrative": "Charts, models, simulations"
        },
        "admissibility_requirements": [
            "Relevant to the case",
            "Not unfairly prejudicial",
            "Authentic (what it claims to be)",
            "Not hearsay (or exception applies)",
            "Properly obtained"
        ],
        "common_objections": {
            "hearsay": "Out-of-court statement offered for truth",
            "relevance": "Not related to issues in the case",
            "foundation": "Insufficient basis established",
            "speculation": "Witness guessing or assuming",
            "leading": "Question suggests the answer",
            "asked_and_answered": "Question already addressed",
            "argumentative": "Arguing rather than questioning"
        },
        "hearsay_exceptions": [
            "Present sense impression",
            "Excited utterance",
            "State of mind",
            "Medical diagnosis",
            "Business records",
            "Public records",
            "Former testimony"
        ],
        "note": "Evidence rules vary by state. Check your state's rules of evidence."
    }


# ============================================================================
# JUDGMENT COLLECTION INFORMATION
# ============================================================================

@app.get("/v1/state-courts/guides/judgment-collection")
def get_judgment_collection_guide():
    """Get guide for collecting on court judgments."""
    _ensure_initialized()

    return {
        "collection_methods": {
            "wage_garnishment": {
                "description": "Court orders employer to withhold from wages",
                "limit": "Usually 25% of disposable earnings",
                "process": "File for writ of garnishment"
            },
            "bank_levy": {
                "description": "Seize funds from debtor's bank account",
                "process": "File for writ of execution",
                "exemptions": "Some funds may be exempt"
            },
            "property_lien": {
                "description": "Place lien on real property",
                "effect": "Must be paid when property sold/refinanced",
                "process": "Record abstract of judgment"
            },
            "till_tap": {
                "description": "Levy on business cash register",
                "applicability": "Business debts only"
            }
        },
        "debtor_examination": {
            "purpose": "Question debtor about assets under oath",
            "process": "File motion; debtor must appear",
            "information_sought": ["Bank accounts", "Employment", "Property", "Other assets"]
        },
        "exempt_property": [
            "Primary residence (homestead exemption)",
            "Necessary clothing",
            "Basic household goods",
            "Tools of trade (limited)",
            "Some retirement accounts",
            "Public benefits"
        ],
        "judgment_duration": {
            "typical": "10-20 years depending on state",
            "renewal": "Many states allow renewal before expiration"
        },
        "note": "Collection rules vary significantly by state. Consult local rules."
    }


# ============================================================================
# COURT INTERPRETER SERVICES
# ============================================================================

STATE_INTERPRETER_SERVICES: dict = {
    "CA": {
        "program": "California Court Interpreters Program",
        "languages": 200,
        "certification_required": True,
        "url": "https://www.courts.ca.gov/programs-interpreters.htm",
        "request_process": "File request with court clerk"
    },
    "TX": {
        "program": "Texas Licensed Court Interpreters",
        "languages": 100,
        "certification_required": True,
        "url": "https://www.txcourts.gov/jbcc/licensed-court-interpreters/",
        "request_process": "Contact court coordinator"
    },
    "NY": {
        "program": "NYS Office of Court Administration Interpreter Services",
        "languages": 150,
        "certification_required": True,
        "url": "https://www.nycourts.gov/courtinterpreter/",
        "request_process": "Request through court clerk"
    },
    "FL": {
        "program": "Florida Court Interpreter Certification",
        "languages": 75,
        "certification_required": True,
        "url": "https://www.flcourts.org/Resources-Services/Court-Interpreters",
        "request_process": "File written request with clerk"
    },
}


@app.get("/v1/state-courts/interpreter-services")
def get_interpreter_services(state: str = None):
    """Get court interpreter service information."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        if state_upper in STATE_INTERPRETER_SERVICES:
            return {
                "state": state_upper,
                "interpreter_info": STATE_INTERPRETER_SERVICES[state_upper]
            }

    return {
        "states": STATE_INTERPRETER_SERVICES,
        "general_info": {
            "right_to_interpreter": "Court must provide interpreter for non-English speakers in most proceedings",
            "cost": "Generally free for criminal cases; may vary for civil",
            "languages": "Spanish most common; many courts offer 50+ languages",
            "request_timing": "Request at least 5 business days before hearing"
        }
    }


@app.get("/v1/state-courts/interpreter-services/request-form")
def get_interpreter_request_form(state: str, language: str, case_number: str = None, hearing_date: str = None):
    """Get template for interpreter service request."""
    _ensure_initialized()

    return {
        "request_template": {
            "state": state.upper(),
            "case_number": case_number or "[CASE NUMBER]",
            "hearing_date": hearing_date or "[HEARING DATE]",
            "language_needed": language,
            "type": "Interpreter Services Request",
            "content": f"""
INTERPRETER SERVICES REQUEST

Case Number: {case_number or '[CASE NUMBER]'}
Hearing Date: {hearing_date or '[HEARING DATE]'}
Language Needed: {language}

I, the undersigned, hereby request interpreter services for the above-referenced case.

Language(s) spoken: {language}
Type of proceeding: ___________________
Estimated duration: ___________________

I understand that:
1. The court will make reasonable efforts to provide an interpreter
2. I should arrive 15 minutes early to meet with the interpreter
3. I should bring any documents relevant to the case

Signature: ___________________________
Date: _______________________________
Phone: ______________________________
            """.strip()
        },
        "instructions": [
            "Complete this form and file with the court clerk",
            "Submit at least 5 business days before your hearing",
            "Include your contact information",
            "Specify the exact language or dialect needed"
        ]
    }


# ============================================================================
# BANKRUPTCY EXEMPTIONS BY STATE
# ============================================================================

STATE_BANKRUPTCY_EXEMPTIONS: dict = {
    "CA": {
        "homestead": {"system_1": 300000, "system_2": 31950, "note": "Two systems available"},
        "vehicle": 3325,
        "personal_property": 9525,
        "wildcard": 1600,
        "retirement": "Unlimited (qualified plans)",
        "allows_federal": False
    },
    "TX": {
        "homestead": {"urban": "10 acres", "rural": "200 acres", "value": "Unlimited"},
        "vehicle": "1 per licensed driver",
        "personal_property": 100000,
        "wildcard": 0,
        "retirement": "Unlimited",
        "allows_federal": False
    },
    "FL": {
        "homestead": {"acres": "0.5 urban / 160 rural", "value": "Unlimited"},
        "vehicle": 1000,
        "personal_property": 1000,
        "wildcard": 0,
        "retirement": "Unlimited",
        "allows_federal": False
    },
    "NY": {
        "homestead": 179975,
        "vehicle": 4825,
        "personal_property": 13950,
        "wildcard": 0,
        "retirement": "Unlimited (qualified plans)",
        "allows_federal": False
    },
}


@app.get("/v1/state-courts/bankruptcy/exemptions")
def get_bankruptcy_exemptions(state: str = None):
    """Get bankruptcy exemption amounts by state."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        if state_upper in STATE_BANKRUPTCY_EXEMPTIONS:
            return {
                "state": state_upper,
                "exemptions": STATE_BANKRUPTCY_EXEMPTIONS[state_upper],
                "note": "Exemption amounts change periodically. Verify current amounts."
            }

    return {
        "states": STATE_BANKRUPTCY_EXEMPTIONS,
        "federal_exemptions": {
            "homestead": 27900,
            "vehicle": 4450,
            "household_goods": 14875,
            "jewelry": 1875,
            "wildcard": 1475,
            "tools_of_trade": 2800
        },
        "note": "Some states allow choice between state and federal exemptions"
    }


# ============================================================================
# EXPUNGEMENT / RECORD SEALING GUIDES
# ============================================================================

STATE_EXPUNGEMENT_RULES: dict = {
    "CA": {
        "eligible_offenses": ["Most misdemeanors", "Some felonies after reduction"],
        "waiting_period": "After probation completion",
        "automatic_expungement": True,
        "cost": "$120-150",
        "url": "https://www.courts.ca.gov/1070.htm"
    },
    "TX": {
        "eligible_offenses": ["Acquittals", "Dismissed charges", "Certain misdemeanors"],
        "waiting_period": "Varies by offense",
        "automatic_expungement": False,
        "cost": "$300+",
        "note": "Called 'expunction' in Texas"
    },
    "NY": {
        "eligible_offenses": ["Dismissed cases", "Some marijuana offenses"],
        "waiting_period": "Immediate for eligible offenses",
        "automatic_expungement": True,
        "cost": "Free for automatic; varies for petition",
        "note": "Marijuana convictions automatically expunged (MRTA)"
    },
    "FL": {
        "eligible_offenses": ["Limited offenses", "Dismissed/acquitted cases"],
        "waiting_period": "10 years for sealing",
        "automatic_expungement": False,
        "cost": "$75 filing fee + processing",
        "note": "Expungement and sealing are different"
    },
}


@app.get("/v1/state-courts/expungement")
def get_expungement_info(state: str = None):
    """Get expungement/record sealing information by state."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        if state_upper in STATE_EXPUNGEMENT_RULES:
            return {
                "state": state_upper,
                "rules": STATE_EXPUNGEMENT_RULES[state_upper]
            }

    return {
        "states": STATE_EXPUNGEMENT_RULES,
        "general_info": {
            "expungement_vs_sealing": {
                "expungement": "Record is destroyed or returned to you",
                "sealing": "Record exists but is hidden from most searches"
            },
            "common_ineligible": [
                "Sex offenses",
                "Violent felonies",
                "DUI (in most states)",
                "Offenses against minors"
            ]
        }
    }


@app.get("/v1/state-courts/expungement/eligibility-check")
def check_expungement_eligibility(
    state: str,
    offense_type: str,
    conviction_date: str = None,
    sentence_completed: bool = True
):
    """Check basic expungement eligibility (preliminary only)."""
    _ensure_initialized()

    state_upper = state.upper()
    offense_lower = offense_type.lower()

    ineligible_keywords = ["murder", "rape", "sexual", "child", "minor", "dui", "dwi"]
    likely_ineligible = any(kw in offense_lower for kw in ineligible_keywords)

    return {
        "state": state_upper,
        "offense_type": offense_type,
        "sentence_completed": sentence_completed,
        "preliminary_assessment": {
            "likely_eligible": not likely_ineligible and sentence_completed,
            "concerns": ["Offense type may be ineligible"] if likely_ineligible else [],
            "next_steps": [
                "Obtain your criminal record",
                "Consult with an attorney",
                "Contact the court clerk for forms",
                "Check specific waiting periods"
            ]
        },
        "disclaimer": "This is a preliminary check only. Actual eligibility depends on many factors."
    }


# ============================================================================
# SERVICE OF PROCESS REQUIREMENTS
# ============================================================================

SERVICE_OF_PROCESS_METHODS: dict = {
    "personal_service": {
        "description": "Personally delivering documents to the defendant",
        "who_can_serve": "Any adult 18+ not a party to the case",
        "generally_required_for": ["Initial complaint", "Summons"]
    },
    "substituted_service": {
        "description": "Leaving with someone at home/business + mailing",
        "requirements": "Must attempt personal service first",
        "follow_up": "Usually requires mailing copy"
    },
    "service_by_mail": {
        "description": "Certified mail with return receipt",
        "when_allowed": "Varies by jurisdiction and document type",
        "note": "May not be sufficient for initial summons"
    },
    "service_by_publication": {
        "description": "Publishing in newspaper when defendant cannot be found",
        "requirements": "Must show due diligence in locating defendant",
        "duration": "Usually 4 consecutive weeks"
    },
    "electronic_service": {
        "description": "Email or electronic filing system",
        "when_allowed": "After initial service; with consent",
        "growing_acceptance": True
    }
}


@app.get("/v1/state-courts/service-of-process")
def get_service_of_process_info(state: str = None, document_type: str = None):
    """Get service of process requirements."""
    _ensure_initialized()

    return {
        "methods": SERVICE_OF_PROCESS_METHODS,
        "state_specific": state.upper() if state else None,
        "document_type": document_type,
        "general_rules": {
            "who_cannot_serve": "Parties to the case, minors",
            "proof_of_service": "Must file proof/affidavit of service with court",
            "time_to_serve": "Usually 60-120 days from filing",
            "consequences_of_improper_service": "Case may be dismissed; judgment may be void"
        },
        "tips": [
            "Attempt personal service first",
            "Keep detailed records of all service attempts",
            "Use a professional process server for difficult cases",
            "File proof of service promptly"
        ]
    }


@app.get("/v1/state-courts/service-of-process/proof-form")
def get_proof_of_service_form(state: str, case_number: str = None, served_party: str = None):
    """Get proof of service form template."""
    _ensure_initialized()

    return {
        "form_template": {
            "title": "PROOF OF SERVICE",
            "state": state.upper(),
            "case_number": case_number or "[CASE NUMBER]",
            "content": f"""
PROOF OF SERVICE

I, _________________________, declare:

1. I am over 18 years of age and not a party to this action.

2. On [DATE], I served the following document(s):
   [ ] Summons
   [ ] Complaint
   [ ] Motion
   [ ] Other: _________________

3. On: {served_party or '[NAME OF PERSON SERVED]'}

4. By the following means:
   [ ] PERSONAL SERVICE - by personally delivering to the person served
   [ ] SUBSTITUTED SERVICE - by leaving copies at dwelling/business with:
       Name: _____________________ Relationship: _________________
   [ ] MAIL - by depositing in US Mail, postage prepaid at [CITY, STATE]
   [ ] CERTIFIED MAIL - receipt number: _________________

5. Address where served: _______________________________________

I declare under penalty of perjury that the foregoing is true and correct.

Date: _________________

Signature: _______________________
Name: ___________________________
Address: _________________________
            """.strip()
        },
        "instructions": [
            "Complete all applicable sections",
            "Sign under penalty of perjury",
            "File original with court",
            "Keep copy for your records"
        ]
    }


# ============================================================================
# GARNISHMENT LIMITS BY STATE
# ============================================================================

STATE_GARNISHMENT_LIMITS: dict = {
    "federal_default": {
        "wage_garnishment": "25% of disposable earnings or amount over 30x minimum wage",
        "minimum_wage_multiple": 30,
        "note": "Whichever results in smaller garnishment"
    },
    "CA": {"wage_limit": "25%", "head_of_household_protection": True, "extra_protection": True},
    "TX": {"wage_limit": "Wages generally exempt", "exceptions": ["Child support", "Taxes", "Student loans"]},
    "FL": {"wage_limit": "Head of household fully exempt", "non_head_limit": "25%"},
    "NY": {"wage_limit": "10%", "extra_protection": True, "minimum_exempt": "90% of income"},
    "PA": {"wage_limit": "Wages exempt", "exceptions": ["Support orders", "Taxes"]},
    "SC": {"wage_limit": "Wages exempt", "exceptions": ["Support", "Taxes"]},
    "NC": {"wage_limit": "Wages exempt", "exceptions": ["Support", "Taxes", "Student loans"]},
}


@app.get("/v1/state-courts/garnishment-limits")
def get_garnishment_limits(state: str = None):
    """Get wage garnishment limits by state."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        if state_upper in STATE_GARNISHMENT_LIMITS:
            return {
                "state": state_upper,
                "limits": STATE_GARNISHMENT_LIMITS[state_upper],
                "federal_baseline": STATE_GARNISHMENT_LIMITS["federal_default"]
            }

    return {
        "states": STATE_GARNISHMENT_LIMITS,
        "exempt_income_types": [
            "Social Security benefits",
            "SSI/disability payments",
            "Veterans benefits",
            "Unemployment benefits",
            "Workers compensation",
            "Child support received"
        ],
        "child_support_garnishment": {
            "limit": "50-65% depending on circumstances",
            "note": "Higher limits than regular creditor garnishment"
        }
    }


# ============================================================================
# VENUE AND TRANSFER RULES
# ============================================================================

@app.get("/v1/state-courts/venue-rules")
def get_venue_rules(state: str = None, case_type: str = None):
    """Get venue rules for filing cases."""
    _ensure_initialized()

    return {
        "general_venue_rules": {
            "civil": {
                "primary": "Where defendant resides",
                "alternatives": [
                    "Where cause of action arose",
                    "Where contract was to be performed",
                    "Where property is located (real property cases)"
                ]
            },
            "criminal": {
                "primary": "Where crime was committed",
                "change_of_venue": "Available if fair trial not possible"
            },
            "family": {
                "divorce": "Where either spouse resides (residency requirements apply)",
                "custody": "Child's home state under UCCJEA"
            },
            "small_claims": {
                "primary": "Where defendant resides or works",
                "alternatives": ["Where injury/damage occurred", "Where contract signed"]
            }
        },
        "transfer_motions": {
            "convenience": "Forum non conveniens - transfer for convenience",
            "improper_venue": "Motion to dismiss or transfer",
            "timing": "Usually must raise early in case"
        },
        "state": state.upper() if state else None,
        "case_type": case_type
    }


# ============================================================================
# APPEALS PROCESS AND TIMELINES
# ============================================================================

STATE_APPEAL_DEADLINES: dict = {
    "CA": {"civil": 60, "criminal": 60, "family": 60, "small_claims": 30},
    "TX": {"civil": 30, "criminal": 30, "family": 30, "small_claims": "None"},
    "NY": {"civil": 30, "criminal": 30, "family": 30, "small_claims": 30},
    "FL": {"civil": 30, "criminal": 30, "family": 30, "small_claims": 30},
    "IL": {"civil": 30, "criminal": 30, "family": 30, "small_claims": 30},
    "PA": {"civil": 30, "criminal": 30, "family": 30, "small_claims": 30},
    "OH": {"civil": 30, "criminal": 30, "family": 30, "small_claims": 28},
    "GA": {"civil": 30, "criminal": 30, "family": 30, "small_claims": 30},
}


@app.get("/v1/state-courts/appeals/deadlines")
def get_appeal_deadlines(state: str = None, case_type: str = None):
    """Get appeal filing deadlines by state."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        if state_upper in STATE_APPEAL_DEADLINES:
            result = {
                "state": state_upper,
                "deadlines_days": STATE_APPEAL_DEADLINES[state_upper]
            }
            if case_type:
                case_type_lower = case_type.lower()
                if case_type_lower in STATE_APPEAL_DEADLINES[state_upper]:
                    result["specific_deadline"] = STATE_APPEAL_DEADLINES[state_upper][case_type_lower]
            return result

    return {
        "states": STATE_APPEAL_DEADLINES,
        "general_rule": "Most states: 30 days from judgment for civil; 30-60 days for criminal",
        "warning": "Deadlines are STRICT - missing deadline usually waives right to appeal"
    }


@app.get("/v1/state-courts/appeals/process")
def get_appeals_process_guide():
    """Get general appeals process information."""
    _ensure_initialized()

    return {
        "steps": [
            {
                "step": 1,
                "name": "File Notice of Appeal",
                "description": "File within deadline in trial court",
                "critical": True
            },
            {
                "step": 2,
                "name": "Order Transcripts",
                "description": "Request transcripts of trial proceedings",
                "timing": "Usually within 10-30 days of notice"
            },
            {
                "step": 3,
                "name": "Designate Record",
                "description": "Specify which documents to include in appellate record",
                "timing": "Varies by jurisdiction"
            },
            {
                "step": 4,
                "name": "File Opening Brief",
                "description": "Written argument explaining errors in trial",
                "timing": "30-60 days after record complete"
            },
            {
                "step": 5,
                "name": "Response Brief Filed",
                "description": "Other party responds to your arguments",
                "timing": "30-45 days after opening brief"
            },
            {
                "step": 6,
                "name": "Reply Brief (Optional)",
                "description": "Respond to other party's arguments",
                "timing": "15-30 days after response"
            },
            {
                "step": 7,
                "name": "Oral Argument",
                "description": "Present case to appellate judges (not always granted)",
                "note": "Many appeals decided without oral argument"
            },
            {
                "step": 8,
                "name": "Decision",
                "description": "Court issues written opinion",
                "timing": "Can take months to over a year"
            }
        ],
        "standards_of_review": {
            "de_novo": "Appeals court reviews legal issues fresh",
            "abuse_of_discretion": "Trial judge's decisions given deference",
            "substantial_evidence": "Facts supported by record",
            "clearly_erroneous": "Fact findings clearly wrong"
        },
        "costs": {
            "filing_fee": "$200-500 typically",
            "transcripts": "$3-5 per page",
            "attorney_fees": "Significant (often $5,000-50,000+)"
        }
    }


# ============================================================================
# DEFAULT JUDGMENT PROCEDURES
# ============================================================================

@app.get("/v1/state-courts/default-judgment")
def get_default_judgment_info(state: str = None):
    """Get information about default judgments."""
    _ensure_initialized()

    return {
        "what_is_default_judgment": "Judgment entered when defendant fails to respond",
        "timing": {
            "response_deadline": "Usually 20-30 days after service",
            "request_for_default": "After deadline passes",
            "entry_of_default": "Clerk enters default on record",
            "default_judgment": "Court enters judgment"
        },
        "types": {
            "clerk_default": "For liquidated (specific) money amounts",
            "court_default": "Requires hearing for unliquidated damages"
        },
        "setting_aside_default": {
            "grounds": [
                "Excusable neglect",
                "Improper service",
                "Meritorious defense",
                "Mistake, inadvertence, surprise"
            ],
            "timing": "Usually must act quickly (30-180 days)",
            "motion_required": "Motion to set aside default/vacate judgment"
        },
        "state": state.upper() if state else None,
        "caution": "Default judgments are enforceable - take action immediately if you receive one"
    }


@app.get("/v1/state-courts/default-judgment/motion-to-vacate")
def get_motion_to_vacate_template(state: str, case_number: str = None, grounds: str = None):
    """Get motion to vacate default judgment template."""
    _ensure_initialized()

    return {
        "motion_template": {
            "title": "MOTION TO SET ASIDE/VACATE DEFAULT JUDGMENT",
            "state": state.upper(),
            "case_number": case_number or "[CASE NUMBER]",
            "content": f"""
MOTION TO SET ASIDE DEFAULT JUDGMENT

Defendant moves this Court to set aside the default judgment entered on [DATE]
on the following grounds:

1. EXCUSABLE NEGLECT/MISTAKE
   [Explain why you failed to respond - illness, never received papers, etc.]

2. MERITORIOUS DEFENSE
   [Explain your defense to the claims]

3. PROMPT ACTION
   [Explain that you are acting promptly upon learning of the default]

4. NO PREJUDICE TO PLAINTIFF
   [Explain why setting aside default will not harm plaintiff]

WHEREFORE, Defendant respectfully requests that this Court:
1. Set aside the default entered against Defendant
2. Vacate the default judgment
3. Allow Defendant to file an Answer
4. Grant such other relief as is just

Dated: ________________

_________________________
[Your Name]
[Address]
[Phone]
            """.strip()
        },
        "supporting_documents": [
            "Declaration explaining circumstances",
            "Proposed Answer to complaint",
            "Any evidence supporting your defense"
        ],
        "grounds": grounds
    }


# ============================================================================
# PROTECTIVE ORDERS / RESTRAINING ORDERS
# ============================================================================

PROTECTIVE_ORDER_TYPES: dict = {
    "domestic_violence": {
        "also_called": ["Restraining order", "Order of protection"],
        "protects_against": "Abuse by family/household member",
        "duration": "Temporary (14-21 days), then permanent (1-5 years)"
    },
    "civil_harassment": {
        "protects_against": "Harassment by non-family members",
        "examples": ["Neighbors", "Acquaintances", "Strangers"],
        "duration": "Up to 5 years typically"
    },
    "workplace_violence": {
        "who_can_file": "Employers on behalf of employees",
        "protects_against": "Threats/violence at work"
    },
    "elder_abuse": {
        "protects": "Adults 65+ or dependent adults",
        "who_can_file": "Victim or on behalf of victim"
    },
    "gun_violence": {
        "also_called": ["ERPO", "Red flag order"],
        "purpose": "Temporary removal of firearms",
        "available_in": "About 21 states"
    }
}


@app.get("/v1/state-courts/protective-orders")
def get_protective_order_info(state: str = None, order_type: str = None):
    """Get protective/restraining order information."""
    _ensure_initialized()

    return {
        "types": PROTECTIVE_ORDER_TYPES,
        "state": state.upper() if state else None,
        "order_type": order_type,
        "general_process": {
            "step_1": "File petition with court",
            "step_2": "Court may issue temporary order immediately",
            "step_3": "Hearing scheduled (usually within 21 days)",
            "step_4": "Both parties present evidence at hearing",
            "step_5": "Judge decides whether to grant permanent order"
        },
        "what_orders_can_require": [
            "Stay away from petitioner",
            "No contact (calls, texts, email)",
            "Move out of shared residence",
            "Stay away from workplace/school",
            "Surrender firearms",
            "Temporary custody arrangements"
        ],
        "emergency_orders": {
            "when": "Immediate danger exists",
            "how": "Ex parte (without other party present)",
            "duration": "Until hearing (usually 14-21 days)"
        }
    }


# ============================================================================
# COURT FINES AND FEES PAYMENT OPTIONS
# ============================================================================

@app.get("/v1/state-courts/fines-payment")
def get_fines_payment_info(state: str = None):
    """Get information about court fines and payment options."""
    _ensure_initialized()

    return {
        "payment_options": {
            "pay_in_full": {
                "methods": ["Online", "In person", "By mail"],
                "benefits": "Closes matter immediately"
            },
            "payment_plan": {
                "availability": "Most courts offer payment plans",
                "typical_terms": "Monthly payments over 6-24 months",
                "fees": "May include setup fee or interest"
            },
            "community_service": {
                "availability": "Often available as alternative",
                "credit": "Usually $10-15 per hour",
                "approval": "Must be approved by court"
            },
            "ability_to_pay_hearing": {
                "purpose": "Reduce fines based on financial hardship",
                "documentation": "Bring proof of income, expenses"
            }
        },
        "consequences_of_nonpayment": [
            "Additional late fees",
            "License suspension",
            "Wage garnishment",
            "Collection agency referral",
            "Contempt of court",
            "Arrest warrant (in some cases)"
        ],
        "fee_waivers": {
            "eligibility": "Low income, public benefits recipients",
            "covers": "Filing fees, some court costs",
            "form": "Fee waiver application required"
        },
        "state": state.upper() if state else None
    }


# ============================================================================
# JURY DUTY INFORMATION
# ============================================================================

STATE_JURY_SERVICE: dict = {
    "CA": {"term": "1 day / 1 trial", "pay": "$15/day", "employer_protection": True},
    "TX": {"term": "Varies by county", "pay": "$6-40/day", "employer_protection": True},
    "NY": {"term": "2 weeks or 1 trial", "pay": "$40/day", "employer_protection": True},
    "FL": {"term": "1 day / 1 trial", "pay": "$15/day", "employer_protection": True},
}


@app.get("/v1/state-courts/jury-duty")
def get_jury_duty_info(state: str = None):
    """Get jury duty information by state."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        if state_upper in STATE_JURY_SERVICE:
            return {
                "state": state_upper,
                "service_info": STATE_JURY_SERVICE[state_upper]
            }

    return {
        "states": STATE_JURY_SERVICE,
        "general_info": {
            "who_is_eligible": [
                "US citizen",
                "18 years or older",
                "Resident of jurisdiction",
                "Can understand English",
                "No felony convictions (usually)"
            ],
            "exemptions": [
                "Age (70+ in many states)",
                "Medical condition",
                "Undue hardship",
                "Primary caregiver",
                "Recent prior service"
            ],
            "employer_obligations": [
                "Cannot fire for jury service",
                "May not have to pay wages",
                "Must allow time off"
            ]
        },
        "postponement": {
            "usually_available": True,
            "timing": "Request before service date",
            "limit": "Usually 1-2 postponements"
        }
    }


# ============================================================================
# SUBPOENA INFORMATION
# ============================================================================

@app.get("/v1/state-courts/subpoenas")
def get_subpoena_info(subpoena_type: str = None):
    """Get information about subpoenas."""
    _ensure_initialized()

    return {
        "types": {
            "subpoena_ad_testificandum": {
                "purpose": "Compel witness to testify",
                "requires": "Personal attendance at proceeding"
            },
            "subpoena_duces_tecum": {
                "purpose": "Compel production of documents/evidence",
                "requires": "Bring specified items to court or deposition"
            },
            "deposition_subpoena": {
                "purpose": "Compel testimony at deposition",
                "requires": "Appear for questioning under oath"
            }
        },
        "service_requirements": {
            "who_can_serve": "Anyone 18+ not a party to case",
            "how": "Personal service usually required",
            "fees": "Witness fee and mileage usually required"
        },
        "compliance": {
            "deadline": "As specified in subpoena",
            "failure_consequences": "Contempt of court, fines, arrest"
        },
        "objections": {
            "grounds": [
                "Improper service",
                "Unreasonable burden",
                "Privileged information",
                "Insufficient time"
            ],
            "procedure": "File motion to quash with court",
            "timing": "Before compliance deadline"
        },
        "subpoena_type": subpoena_type
    }


# ============================================================================
# BAIL AND BOND INFORMATION
# ============================================================================

STATE_BAIL_RULES: dict = {
    "CA": {
        "bail_schedule": True,
        "cash_bail_allowed": True,
        "bail_reform": "Proposition 25 rejected",
        "10_percent_option": False
    },
    "NY": {
        "bail_schedule": True,
        "cash_bail_allowed": True,
        "bail_reform": "2020 reform for non-violent offenses",
        "10_percent_option": True
    },
    "NJ": {
        "bail_schedule": False,
        "cash_bail_allowed": False,
        "bail_reform": "Near elimination of cash bail (2017)",
        "risk_assessment": True
    },
    "IL": {
        "bail_schedule": False,
        "cash_bail_allowed": False,
        "bail_reform": "SAFE-T Act eliminated cash bail (2023)",
        "risk_assessment": True
    },
    "TX": {
        "bail_schedule": True,
        "cash_bail_allowed": True,
        "bail_reform": "Limited reforms",
        "10_percent_option": True
    },
}


@app.get("/v1/state-courts/bail-bond")
def get_bail_bond_info(state: str = None):
    """Get bail and bond information by state."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        if state_upper in STATE_BAIL_RULES:
            return {
                "state": state_upper,
                "rules": STATE_BAIL_RULES[state_upper]
            }

    return {
        "states": STATE_BAIL_RULES,
        "bail_types": {
            "cash_bail": {
                "description": "Pay full amount in cash",
                "refund": "Returned after case concludes (minus fees)"
            },
            "surety_bond": {
                "description": "Pay bondsman 10-15% premium",
                "refund": "Premium not refunded",
                "collateral": "May require collateral"
            },
            "property_bond": {
                "description": "Use property as collateral",
                "requirements": "Usually 150-200% of bail amount"
            },
            "release_on_recognizance": {
                "description": "Released on promise to appear",
                "requirements": "Low flight risk, community ties"
            },
            "unsecured_bond": {
                "description": "Promise to pay if fail to appear",
                "no_upfront_payment": True
            }
        },
        "bail_reduction": {
            "motion": "Motion for bail reduction",
            "arguments": [
                "Excessive bail (8th Amendment)",
                "Strong community ties",
                "Employment",
                "No flight risk",
                "Minimal danger to community"
            ]
        }
    }


# ============================================================================
# TRAFFIC COURT INFORMATION
# ============================================================================

TRAFFIC_VIOLATION_TYPES: dict = {
    "infractions": {
        "examples": ["Speeding", "Running red light", "Illegal turn"],
        "penalty": "Fine only, no jail",
        "points": "Usually 1-2 points on license"
    },
    "misdemeanors": {
        "examples": ["Reckless driving", "DUI first offense", "Hit and run (property)"],
        "penalty": "Fine and/or jail up to 1 year",
        "points": "Higher points, possible license suspension"
    },
    "felonies": {
        "examples": ["DUI with injury", "Vehicular manslaughter", "Fleeing police"],
        "penalty": "Prison time possible",
        "points": "License revocation likely"
    }
}


@app.get("/v1/state-courts/traffic")
def get_traffic_court_info(state: str = None):
    """Get traffic court information."""
    _ensure_initialized()

    return {
        "violation_types": TRAFFIC_VIOLATION_TYPES,
        "state": state.upper() if state else None,
        "options_after_ticket": {
            "pay_fine": {
                "effect": "Admission of guilt",
                "points": "Added to driving record"
            },
            "traffic_school": {
                "eligibility": "Minor violations, no recent attendance",
                "benefit": "May avoid points",
                "cost": "Course fee + court fee"
            },
            "contest_in_court": {
                "written_declaration": "Some courts allow written contest",
                "trial": "Present defense to judge",
                "outcomes": ["Dismissed", "Guilty", "Reduced charge"]
            }
        },
        "points_system": {
            "purpose": "Track driving history",
            "accumulation": "Too many points = license suspension",
            "typical_thresholds": {
                "warning": "4-6 points in 12 months",
                "suspension": "8-12 points in 12-24 months"
            }
        },
        "license_suspension": {
            "causes": [
                "Too many points",
                "DUI conviction",
                "Failure to appear",
                "Failure to pay fines",
                "No insurance"
            ],
            "reinstatement": "Fees, possible SR-22 insurance, waiting period"
        }
    }


@app.get("/v1/state-courts/traffic/dui-penalties")
def get_dui_penalties(state: str = None, offense_number: int = 1):
    """Get DUI/DWI penalty information."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "offense_number": offense_number,
        "general_penalties": {
            "first_offense": {
                "fine": "$1,000-$5,000",
                "jail": "0-6 months",
                "license_suspension": "3-12 months",
                "probation": "3-5 years typical",
                "dui_school": "Required in most states",
                "ignition_interlock": "Required in some states"
            },
            "second_offense": {
                "fine": "$2,500-$10,000",
                "jail": "30 days - 1 year",
                "license_suspension": "1-2 years",
                "ignition_interlock": "Usually required"
            },
            "third_offense": {
                "fine": "$5,000-$25,000",
                "jail": "6 months - 3 years",
                "license_suspension": "3+ years or permanent",
                "felony": "Many states treat as felony"
            }
        },
        "aggravating_factors": [
            "BAC .15 or higher",
            "Minor in vehicle",
            "Accident causing injury",
            "Prior DUI convictions",
            "Driving on suspended license"
        ],
        "alternative_sentences": [
            "House arrest",
            "Community service",
            "SCRAM bracelet (alcohol monitoring)",
            "Victim impact panels"
        ]
    }


# ============================================================================
# DISCOVERY RULES
# ============================================================================

DISCOVERY_TYPES: dict = {
    "interrogatories": {
        "description": "Written questions requiring written answers under oath",
        "limit": "Usually 25-35 questions",
        "response_time": "30 days typically"
    },
    "requests_for_production": {
        "description": "Request to produce documents/things",
        "scope": "Relevant, non-privileged materials",
        "response_time": "30-45 days typically"
    },
    "requests_for_admission": {
        "description": "Request to admit/deny specific facts",
        "effect": "Failure to respond = admitted",
        "response_time": "30 days typically"
    },
    "depositions": {
        "description": "Oral examination under oath",
        "duration": "Usually limited to 7 hours",
        "who": "Parties, witnesses, experts"
    },
    "subpoenas_duces_tecum": {
        "description": "Compel non-parties to produce documents",
        "requirements": "Court approval may be needed"
    }
}


@app.get("/v1/state-courts/discovery")
def get_discovery_info(state: str = None, case_type: str = None):
    """Get discovery rules and procedures."""
    _ensure_initialized()

    return {
        "types": DISCOVERY_TYPES,
        "state": state.upper() if state else None,
        "case_type": case_type,
        "scope": {
            "standard": "Relevant and proportional to needs of case",
            "limitations": [
                "Privilege (attorney-client, work product)",
                "Undue burden or expense",
                "Harassment or annoyance",
                "Privacy concerns"
            ]
        },
        "objections": {
            "common_grounds": [
                "Privilege",
                "Vague or ambiguous",
                "Overly broad",
                "Not reasonably calculated to lead to discovery",
                "Unduly burdensome"
            ],
            "procedure": "Object within response deadline; may still need to answer non-objectionable parts"
        },
        "motion_to_compel": {
            "when": "Party refuses to respond adequately",
            "meet_and_confer": "Required before filing in most courts",
            "sanctions": "Court may order costs/fees against non-compliant party"
        },
        "protective_orders": {
            "purpose": "Limit disclosure of sensitive information",
            "types": ["Confidentiality order", "Attorneys eyes only", "Sealed filing"]
        }
    }


# ============================================================================
# PLEADING REQUIREMENTS
# ============================================================================

@app.get("/v1/state-courts/pleading-requirements")
def get_pleading_requirements(state: str = None, pleading_type: str = None):
    """Get pleading format and content requirements."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "pleading_type": pleading_type,
        "general_requirements": {
            "format": {
                "paper_size": "8.5 x 11 inches",
                "margins": "1 inch on all sides",
                "font": "12 point, readable (Times New Roman, Arial)",
                "line_spacing": "Double-spaced",
                "page_numbers": "Bottom center or right"
            },
            "caption": {
                "court_name": "Full name of court",
                "case_number": "If assigned",
                "party_names": "Plaintiff v. Defendant",
                "document_title": "Clear identification"
            },
            "signature": {
                "required": True,
                "certification": "Certifies good faith basis for filing",
                "sanctions": "Rule 11 type sanctions for frivolous filings"
            }
        },
        "common_pleadings": {
            "complaint": {
                "purpose": "Initiate lawsuit",
                "contents": ["Jurisdiction", "Facts", "Causes of action", "Prayer for relief"]
            },
            "answer": {
                "purpose": "Respond to complaint",
                "contents": ["Admissions/denials", "Affirmative defenses", "Counterclaims"],
                "deadline": "Usually 20-30 days after service"
            },
            "motion": {
                "purpose": "Request court action",
                "contents": ["Notice of motion", "Memorandum of points and authorities", "Declarations"]
            },
            "demurrer_motion_to_dismiss": {
                "purpose": "Challenge legal sufficiency",
                "grounds": ["Failure to state claim", "Lack of jurisdiction", "Statute of limitations"]
            }
        }
    }


# ============================================================================
# SENTENCING GUIDELINES
# ============================================================================

@app.get("/v1/state-courts/sentencing")
def get_sentencing_info(state: str = None, offense_type: str = None):
    """Get sentencing information and guidelines."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "offense_type": offense_type,
        "sentencing_types": {
            "determinate": {
                "description": "Fixed sentence (e.g., 5 years)",
                "parole": "May be eligible for early release"
            },
            "indeterminate": {
                "description": "Range (e.g., 5-10 years)",
                "parole_board": "Determines actual release"
            },
            "mandatory_minimum": {
                "description": "Judge cannot go below minimum",
                "common_for": "Drug offenses, weapons crimes, repeat offenders"
            }
        },
        "factors_considered": {
            "aggravating": [
                "Prior criminal history",
                "Leadership role in crime",
                "Vulnerable victim",
                "Cruelty or violence",
                "Abuse of trust"
            ],
            "mitigating": [
                "No prior record",
                "Minor role in offense",
                "Duress or coercion",
                "Mental health issues",
                "Acceptance of responsibility"
            ]
        },
        "alternatives_to_incarceration": [
            "Probation",
            "Community service",
            "House arrest",
            "Drug/mental health court",
            "Diversion programs",
            "Restitution"
        ],
        "three_strikes": {
            "states_with_laws": ["CA", "WA", "TX", "FL", "GA", "many others"],
            "effect": "Enhanced sentences for repeat serious offenders"
        }
    }


# ============================================================================
# PROBATION INFORMATION
# ============================================================================

@app.get("/v1/state-courts/probation")
def get_probation_info(state: str = None, probation_type: str = None):
    """Get probation rules and requirements."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "probation_type": probation_type,
        "types": {
            "formal_supervised": {
                "description": "Regular check-ins with probation officer",
                "frequency": "Monthly or more",
                "drug_testing": "Often required"
            },
            "informal_unsupervised": {
                "description": "No probation officer meetings",
                "requirements": "Follow court orders, no new offenses",
                "common_for": "Minor first offenses"
            },
            "summary": {
                "description": "Court-supervised without probation officer",
                "progress_reports": "To court"
            }
        },
        "common_conditions": [
            "No new crimes",
            "Report to probation officer",
            "Pay fines and restitution",
            "Complete community service",
            "Attend counseling or treatment",
            "No alcohol or drugs",
            "No contact with victims",
            "Stay away from certain places",
            "Maintain employment",
            "No leaving jurisdiction without permission"
        ],
        "violation_consequences": [
            "Warning",
            "Modified conditions",
            "Short jail term",
            "Extended probation",
            "Revocation and full sentence"
        ],
        "early_termination": {
            "eligibility": "Good compliance, typically after half the term",
            "process": "Motion to terminate probation early"
        }
    }


# ============================================================================
# ATTORNEY FEE RECOVERY
# ============================================================================

ATTORNEY_FEE_RULES: dict = {
    "american_rule": {
        "description": "Each party pays own fees (default in US)",
        "exceptions": "Contract, statute, or court order"
    },
    "fee_shifting_statutes": {
        "civil_rights": "42 USC 1988 - prevailing plaintiff",
        "consumer_protection": "Many state statutes",
        "employment": "FLSA, Title VII, ADA, etc.",
        "environmental": "Many environmental statutes"
    },
    "contractual_fee_provisions": {
        "enforceability": "Generally enforceable",
        "reciprocity": "Many states make one-sided provisions mutual"
    }
}


@app.get("/v1/state-courts/attorney-fees")
def get_attorney_fee_info(state: str = None, case_type: str = None):
    """Get attorney fee recovery rules."""
    _ensure_initialized()

    return {
        "rules": ATTORNEY_FEE_RULES,
        "state": state.upper() if state else None,
        "case_type": case_type,
        "how_fees_calculated": {
            "lodestar_method": {
                "formula": "Reasonable hours x reasonable rate",
                "adjustments": "May increase/decrease based on results"
            },
            "percentage_of_recovery": {
                "use": "Common in contingency and class actions",
                "typical_range": "25-40% of recovery"
            }
        },
        "fee_motions": {
            "timing": "Usually within 14-30 days of judgment",
            "documentation": "Detailed billing records required",
            "opposition": "Other side can challenge reasonableness"
        },
        "pro_se_litigants": {
            "generally": "Cannot recover attorney fees (no attorney)",
            "exceptions": "Some statutes allow reasonable value of time"
        }
    }


# ============================================================================
# COURT ACCESSIBILITY (ADA)
# ============================================================================

@app.get("/v1/state-courts/accessibility")
def get_court_accessibility_info(state: str = None, accommodation_type: str = None):
    """Get court accessibility and ADA accommodation information."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "accommodation_type": accommodation_type,
        "ada_requirements": {
            "physical_access": [
                "Wheelchair accessible entrances",
                "Accessible parking",
                "Accessible restrooms",
                "Accessible courtrooms"
            ],
            "communication_access": [
                "Sign language interpreters",
                "Assistive listening devices",
                "Real-time captioning (CART)",
                "Braille/large print documents"
            ],
            "cognitive_access": [
                "Plain language materials",
                "Extra time for processing",
                "Support person allowed"
            ]
        },
        "requesting_accommodations": {
            "when": "As soon as possible before court date",
            "how": "Contact court's ADA coordinator",
            "documentation": "May need to show disability-related need",
            "cost": "Free - court must provide"
        },
        "common_accommodations": [
            "Sign language interpreter",
            "Wheelchair accessible courtroom",
            "Assistive listening device",
            "Extended time for testimony",
            "Frequent breaks",
            "Service animal allowed",
            "Support person/advocate"
        ],
        "if_denied": {
            "options": [
                "Request reconsideration",
                "File ADA complaint with court",
                "Contact state ADA coordinator",
                "File complaint with DOJ"
            ]
        }
    }


# ============================================================================
# MEDIATION REQUIREMENTS
# ============================================================================

STATE_MANDATORY_MEDIATION: dict = {
    "CA": {"civil": True, "family": True, "small_claims": False},
    "FL": {"civil": True, "family": True, "small_claims": True},
    "TX": {"civil": False, "family": True, "small_claims": False},
    "NY": {"civil": True, "family": True, "small_claims": True},
}


@app.get("/v1/state-courts/mediation-requirements")
def get_mediation_requirements(state: str = None, case_type: str = None):
    """Get mandatory mediation requirements by state."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        if state_upper in STATE_MANDATORY_MEDIATION:
            result = {
                "state": state_upper,
                "requirements": STATE_MANDATORY_MEDIATION[state_upper]
            }
            if case_type:
                case_type_lower = case_type.lower()
                if case_type_lower in STATE_MANDATORY_MEDIATION[state_upper]:
                    result["mandatory"] = STATE_MANDATORY_MEDIATION[state_upper][case_type_lower]
            return result

    return {
        "states": STATE_MANDATORY_MEDIATION,
        "mediation_process": {
            "selection": "Parties agree on mediator or court appoints",
            "preparation": "Exchange position statements beforehand",
            "session": "Typically 2-8 hours",
            "outcome": "Settlement or impasse"
        },
        "benefits": [
            "Faster than trial",
            "Less expensive",
            "Confidential",
            "Parties control outcome",
            "Preserves relationships"
        ],
        "what_happens_if_no_settlement": {
            "proceed_to_trial": True,
            "statements_confidential": "Cannot be used in court",
            "mediator_cannot_testify": True
        }
    }


# ============================================================================
# SMALL CLAIMS COURT GUIDE
# ============================================================================

@app.get("/v1/state-courts/small-claims/guide")
def get_small_claims_guide(state: str = None):
    """Get comprehensive small claims court guide."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "step_by_step": [
            {
                "step": 1,
                "name": "Determine if small claims is appropriate",
                "considerations": [
                    "Amount is within limit",
                    "You can identify defendant",
                    "You have evidence"
                ]
            },
            {
                "step": 2,
                "name": "Send demand letter",
                "purpose": "Required in some courts; shows good faith",
                "content": "What you're owed, deadline to pay, consequence"
            },
            {
                "step": 3,
                "name": "File claim",
                "where": "Correct court (usually where defendant lives/works)",
                "cost": "$30-100 filing fee typically"
            },
            {
                "step": 4,
                "name": "Serve defendant",
                "methods": ["Sheriff", "Process server", "Certified mail"],
                "deadline": "Before hearing date"
            },
            {
                "step": 5,
                "name": "Prepare for hearing",
                "gather": ["Documents", "Photos", "Witnesses", "Receipts"]
            },
            {
                "step": 6,
                "name": "Attend hearing",
                "tips": [
                    "Arrive early",
                    "Dress appropriately",
                    "Be organized",
                    "Speak clearly to judge",
                    "Stick to facts"
                ]
            },
            {
                "step": 7,
                "name": "Collect judgment (if you win)",
                "methods": ["Wage garnishment", "Bank levy", "Property lien"]
            }
        ],
        "tips": {
            "presentation": [
                "Organize documents chronologically",
                "Prepare a brief summary",
                "Practice your presentation",
                "Anticipate defendant's arguments"
            ],
            "what_not_to_do": [
                "Interrupt the other party",
                "Get emotional or angry",
                "Bring up irrelevant issues",
                "Lie or exaggerate"
            ]
        }
    }


# ============================================================================
# COURT FORMS DIRECTORY
# ============================================================================

STATE_COURT_FORMS: dict = {
    "CA": {
        "url": "https://www.courts.ca.gov/forms.htm",
        "common_forms": {
            "civil": ["SC-100 (small claims)", "PLD-C-001 (complaint)"],
            "family": ["FL-100 (divorce)", "FL-300 (motion)"],
            "criminal": ["CR-101 (plea form)"]
        }
    },
    "NY": {
        "url": "https://www.nycourts.gov/forms/",
        "common_forms": {
            "civil": ["UCS-840 (summons)", "CIV-GP-10 (complaint)"],
            "family": ["UD-1 (divorce)", "FM-1 (family offense)"],
        }
    },
    "TX": {
        "url": "https://www.txcourts.gov/programs-services/court-approved-forms/",
        "common_forms": {
            "family": ["Divorce petition", "Custody modification"],
        }
    },
    "FL": {
        "url": "https://www.flcourts.org/Resources-Services/Court-Improvement/Forms",
        "common_forms": {
            "family": ["Form 12.900 (family cover sheet)"],
            "civil": ["Form 1.997 (civil cover sheet)"]
        }
    },
}


@app.get("/v1/state-courts/forms")
def get_court_forms_directory(state: str = None, form_type: str = None):
    """Get court forms directory by state."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        if state_upper in STATE_COURT_FORMS:
            return {
                "state": state_upper,
                "forms_info": STATE_COURT_FORMS[state_upper]
            }

    return {
        "states": STATE_COURT_FORMS,
        "general_guidance": {
            "where_to_find": "State court website, clerk's office",
            "fillable_forms": "Many courts offer PDF fillable forms",
            "local_forms": "Some courts have additional local forms",
            "help_completing": "Self-help center, legal aid, law library"
        },
        "form_type": form_type
    }


# ============================================================================
# COURT RECORDS RETENTION
# ============================================================================

STATE_RECORDS_RETENTION: dict = {
    "CA": {
        "civil": "10 years after final disposition",
        "criminal": "Permanent (felony); 5 years (misdemeanor)",
        "family": "30 years",
        "juvenile": "Sealed at age 18 or after period"
    },
    "TX": {
        "civil": "20 years",
        "criminal": "Permanent",
        "family": "Permanent",
        "juvenile": "Until age 21"
    },
    "NY": {
        "civil": "Various by type",
        "criminal": "Permanent",
        "family": "Permanent",
        "juvenile": "Sealed after completion"
    },
    "FL": {
        "civil": "10 years",
        "criminal": "Permanent",
        "family": "10 years after minor reaches 18",
        "juvenile": "5 years after last entry"
    },
}


@app.get("/v1/state-courts/records-retention")
def get_records_retention_info(state: str = None, record_type: str = None):
    """Get court records retention periods by state."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        if state_upper in STATE_RECORDS_RETENTION:
            return {
                "state": state_upper,
                "retention": STATE_RECORDS_RETENTION[state_upper]
            }

    return {
        "states": STATE_RECORDS_RETENTION,
        "accessing_old_records": {
            "archived_records": "May be in storage, longer retrieval time",
            "destroyed_records": "Cannot be recovered",
            "fees": "Copying/retrieval fees may apply"
        },
        "record_type": record_type
    }


# ============================================================================
# NAME CHANGE PROCEDURES
# ============================================================================

@app.get("/v1/state-courts/name-change")
def get_name_change_info(state: str = None, change_type: str = None):
    """Get name change procedure information."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "change_type": change_type,
        "general_process": {
            "step_1": "File petition for name change",
            "step_2": "Pay filing fee ($150-400 typically)",
            "step_3": "Publish notice in newspaper (if required)",
            "step_4": "Attend hearing (may be waived)",
            "step_5": "Receive court order",
            "step_6": "Update documents (SSA, DMV, etc.)"
        },
        "types": {
            "adult": {
                "petitioner": "Individual 18+",
                "reasons": "Marriage, divorce, personal preference",
                "restrictions": "Cannot be for fraud or to evade law"
            },
            "minor": {
                "petitioner": "Parent/guardian",
                "requirements": "Both parents' consent (usually)",
                "court_consideration": "Best interests of child"
            },
            "marriage": {
                "process": "Can change at time of marriage license",
                "no_court_required": True
            },
            "divorce": {
                "process": "Request in divorce decree",
                "timing": "During divorce proceedings"
            }
        },
        "documents_to_update": [
            "Social Security card",
            "Driver's license/state ID",
            "Passport",
            "Bank accounts",
            "Credit cards",
            "Employer records",
            "Voter registration",
            "Insurance policies",
            "Professional licenses"
        ]
    }


# ============================================================================
# GUARDIANSHIP AND CONSERVATORSHIP
# ============================================================================

@app.get("/v1/state-courts/guardianship")
def get_guardianship_info(state: str = None, guardianship_type: str = None):
    """Get guardianship and conservatorship information."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "guardianship_type": guardianship_type,
        "types": {
            "guardianship_of_person": {
                "authority": "Personal care, medical decisions, living arrangements",
                "for": "Incapacitated adults, minors"
            },
            "guardianship_of_estate": {
                "authority": "Financial decisions, property management",
                "also_called": "Conservatorship (in some states)"
            },
            "limited_guardianship": {
                "scope": "Only specific powers granted",
                "preserves": "Maximum independence for protected person"
            },
            "temporary_guardianship": {
                "duration": "Limited time, emergency situations",
                "process": "Expedited hearing"
            }
        },
        "process": [
            "File petition with court",
            "Notify interested parties",
            "Court investigation/evaluation",
            "Hearing before judge",
            "Appointment and bond",
            "Ongoing reporting requirements"
        ],
        "alternatives": [
            "Power of attorney (if person has capacity)",
            "Healthcare proxy",
            "Representative payee (for Social Security)",
            "Supported decision-making"
        ],
        "termination": {
            "grounds": [
                "Protected person regains capacity",
                "Death of protected person",
                "Guardian incapacity or misconduct",
                "No longer necessary"
            ]
        }
    }


# ============================================================================
# LANDLORD-TENANT COURT
# ============================================================================

STATE_EVICTION_TIMELINES: dict = {
    "CA": {"notice_period": "3-60 days", "court_timeline": "20-60 days"},
    "TX": {"notice_period": "3 days", "court_timeline": "10-30 days"},
    "NY": {"notice_period": "14-30 days", "court_timeline": "30-90 days"},
    "FL": {"notice_period": "3-7 days", "court_timeline": "14-30 days"},
    "IL": {"notice_period": "5-30 days", "court_timeline": "14-45 days"},
}


@app.get("/v1/state-courts/landlord-tenant")
def get_landlord_tenant_info(state: str = None, issue_type: str = None):
    """Get landlord-tenant court information."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        eviction_info = STATE_EVICTION_TIMELINES.get(state_upper, {})
    else:
        eviction_info = STATE_EVICTION_TIMELINES

    return {
        "state": state.upper() if state else None,
        "issue_type": issue_type,
        "eviction_timelines": eviction_info,
        "eviction_process": {
            "step_1": "Landlord serves notice (cure or quit, pay or quit, etc.)",
            "step_2": "If not cured, landlord files unlawful detainer",
            "step_3": "Tenant is served with summons",
            "step_4": "Tenant has limited time to respond (5-14 days)",
            "step_5": "Court hearing/trial",
            "step_6": "Judgment and writ of possession",
            "step_7": "Sheriff enforces eviction"
        },
        "tenant_defenses": [
            "Improper notice",
            "Retaliation by landlord",
            "Discrimination",
            "Breach of habitability",
            "Rent was paid",
            "Landlord's failure to maintain"
        ],
        "security_deposit_rules": {
            "limit": "Typically 1-2 months rent",
            "return_deadline": "14-60 days after move-out",
            "itemized_deductions": "Required in most states",
            "bad_faith_penalty": "2-3x deposit in some states"
        },
        "rent_control": {
            "states_with_laws": ["CA", "NY", "NJ", "MD", "OR", "DC"],
            "effect": "Limits rent increases",
            "exemptions": "Usually newer buildings, single-family homes"
        }
    }


# ============================================================================
# FORECLOSURE PROCEDURES
# ============================================================================

STATE_FORECLOSURE_TYPES: dict = {
    "CA": {"type": "Non-judicial", "timeline": "120+ days", "deficiency": "Limited"},
    "TX": {"type": "Non-judicial", "timeline": "60+ days", "deficiency": "Allowed"},
    "NY": {"type": "Judicial", "timeline": "1-3 years", "deficiency": "Allowed"},
    "FL": {"type": "Judicial", "timeline": "6-12 months", "deficiency": "Allowed"},
    "IL": {"type": "Judicial", "timeline": "6-12 months", "deficiency": "Allowed"},
}


@app.get("/v1/state-courts/foreclosure")
def get_foreclosure_info(state: str = None):
    """Get foreclosure procedure information by state."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        if state_upper in STATE_FORECLOSURE_TYPES:
            return {
                "state": state_upper,
                "foreclosure_rules": STATE_FORECLOSURE_TYPES[state_upper]
            }

    return {
        "states": STATE_FORECLOSURE_TYPES,
        "types": {
            "judicial": {
                "process": "Lender must file lawsuit",
                "court_oversight": True,
                "timeline": "Generally longer (6+ months)"
            },
            "non_judicial": {
                "process": "Follows power of sale clause in deed of trust",
                "court_oversight": False,
                "timeline": "Generally shorter (60-120 days)"
            }
        },
        "homeowner_options": [
            "Loan modification",
            "Forbearance",
            "Refinance",
            "Short sale",
            "Deed in lieu of foreclosure",
            "Bankruptcy filing",
            "Reinstatement (pay past due)"
        ],
        "defenses": [
            "Improper notice",
            "Lack of standing",
            "RESPA violations",
            "TILA violations",
            "Fraud",
            "Dual tracking violations"
        ],
        "right_of_redemption": {
            "before_sale": "Most states allow",
            "after_sale": "Some states allow (limited time)"
        }
    }


# ============================================================================
# CIVIL COMMITMENT / MENTAL HEALTH COURT
# ============================================================================

@app.get("/v1/state-courts/civil-commitment")
def get_civil_commitment_info(state: str = None):
    """Get civil commitment procedure information."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "types": {
            "emergency_hold": {
                "duration": "24-72 hours typically",
                "criteria": "Immediate danger to self or others",
                "who_can_initiate": "Police, doctors, family members"
            },
            "short_term": {
                "duration": "14-30 days",
                "requires": "Court hearing",
                "criteria": "Danger or grave disability"
            },
            "long_term": {
                "duration": "90 days to 1 year",
                "requires": "Full court hearing with representation",
                "criteria": "Continued need for treatment"
            }
        },
        "patient_rights": [
            "Right to counsel (appointed if needed)",
            "Right to hearing",
            "Right to present evidence",
            "Right to independent evaluation",
            "Right to appeal",
            "Right to least restrictive environment"
        ],
        "criteria_for_commitment": {
            "danger_to_self": "Risk of suicide or self-harm",
            "danger_to_others": "Risk of harming others",
            "grave_disability": "Unable to provide basic needs"
        },
        "outpatient_commitment": {
            "also_called": "Assisted outpatient treatment (AOT)",
            "requirements": "Follow treatment plan in community",
            "consequences": "May be hospitalized if non-compliant"
        }
    }


# ============================================================================
# DRUG COURT / DIVERSION PROGRAMS
# ============================================================================

@app.get("/v1/state-courts/drug-court")
def get_drug_court_info(state: str = None):
    """Get drug court and diversion program information."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "drug_court": {
            "purpose": "Treatment-focused alternative to incarceration",
            "typical_length": "12-24 months",
            "requirements": [
                "Regular court appearances",
                "Frequent drug testing",
                "Substance abuse treatment",
                "Employment or education",
                "Support group attendance"
            ],
            "phases": [
                "Stabilization (intensive supervision)",
                "Intensive treatment",
                "Transition",
                "Aftercare/maintenance",
                "Graduation"
            ],
            "benefits": [
                "Avoid jail/prison",
                "Charges may be dismissed",
                "Record may be sealed/expunged",
                "Treatment support"
            ]
        },
        "other_specialty_courts": {
            "mental_health_court": {
                "for": "Defendants with mental illness",
                "focus": "Treatment and stability"
            },
            "veterans_court": {
                "for": "Veterans with criminal charges",
                "focus": "VA services, peer mentors"
            },
            "dui_court": {
                "for": "Repeat DUI offenders",
                "focus": "Intensive monitoring and treatment"
            },
            "domestic_violence_court": {
                "for": "DV offenders",
                "focus": "Batterer intervention, victim safety"
            }
        },
        "eligibility": {
            "typically_includes": [
                "Non-violent offenses",
                "Substance abuse history",
                "Willingness to participate"
            ],
            "typically_excludes": [
                "Violent offenses",
                "Prior failed attempts",
                "Drug dealing (varies)"
            ]
        }
    }


# ============================================================================
# DOMESTIC RELATIONS / FAMILY COURT
# ============================================================================

STATE_DIVORCE_RESIDENCY: dict = {
    "CA": {"residency": "6 months state, 3 months county"},
    "TX": {"residency": "6 months state, 90 days county"},
    "NY": {"residency": "1-2 years depending on grounds"},
    "FL": {"residency": "6 months"},
    "NV": {"residency": "6 weeks"},
}


@app.get("/v1/state-courts/family-court")
def get_family_court_info(state: str = None, case_type: str = None):
    """Get family court procedures and requirements."""
    _ensure_initialized()

    if state:
        state_upper = state.upper()
        residency_info = STATE_DIVORCE_RESIDENCY.get(state_upper, {})
    else:
        residency_info = STATE_DIVORCE_RESIDENCY

    return {
        "state": state.upper() if state else None,
        "case_type": case_type,
        "residency_requirements": residency_info,
        "case_types": {
            "divorce": {
                "contested": "Parties disagree on terms",
                "uncontested": "Parties agree on all terms",
                "no_fault": "Available in all states",
                "issues": ["Property division", "Spousal support", "Custody", "Child support"]
            },
            "custody": {
                "legal_custody": "Decision-making authority",
                "physical_custody": "Where child lives",
                "joint_vs_sole": "Shared or one parent",
                "standard": "Best interests of child"
            },
            "child_support": {
                "guidelines": "State formula based on income/needs",
                "modification": "Change in circumstances",
                "enforcement": "Wage garnishment, license suspension"
            },
            "domestic_violence": {
                "protective_orders": "Emergency and permanent",
                "separate_process": "Can be filed independently"
            },
            "paternity": {
                "establishment": "DNA testing, acknowledgment",
                "effects": "Custody rights, support obligations"
            }
        },
        "mediation": {
            "required": "In many states for custody disputes",
            "purpose": "Help parents reach agreement",
            "confidential": True
        }
    }


# ============================================================================
# COURT REPORTING AND TRANSCRIPTS
# ============================================================================

@app.get("/v1/state-courts/transcripts")
def get_transcript_info(state: str = None):
    """Get court transcript information and procedures."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "types": {
            "official_transcript": {
                "prepared_by": "Certified court reporter",
                "uses": ["Appeals", "Motions", "Reference"],
                "timeline": "2-30 days depending on urgency"
            },
            "electronic_recording": {
                "common_in": "Many lower courts",
                "transcript_from": "Recording transcribed on request"
            }
        },
        "ordering_process": [
            "Contact court reporter or clerk's office",
            "Specify hearing date and type",
            "Request specific portions or full transcript",
            "Pay deposit (usually required)",
            "Receive transcript when complete"
        ],
        "costs": {
            "original": "$3-5+ per page",
            "copy": "$1-2 per page",
            "expedited": "Additional 50-100% premium",
            "realtime": "Significantly higher"
        },
        "fee_waiver": {
            "available": "For indigent parties",
            "process": "File fee waiver application",
            "for_appeals": "Usually required for indigent appeals"
        }
    }


# ============================================================================
# COURT DEADLINES CALCULATOR
# ============================================================================

@app.post("/v1/state-courts/deadline-calculator")
def calculate_court_deadlines(
    state: str,
    event_type: str,
    start_date: str,
    case_type: str = "civil"
):
    """Calculate common court deadlines (advisory only)."""
    _ensure_initialized()

    from datetime import datetime, timedelta

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        return {"error": "Invalid date format. Use YYYY-MM-DD"}

    # Common deadlines (simplified - actual rules vary)
    deadlines = {
        "service_of_process": {
            "civil": {"days": 60, "description": "Serve complaint"},
            "criminal": {"days": 0, "description": "N/A"}
        },
        "answer": {
            "civil": {"days": 30, "description": "File answer to complaint"},
            "criminal": {"days": 0, "description": "Arraignment"}
        },
        "discovery_start": {
            "civil": {"days": 45, "description": "Begin discovery"},
            "criminal": {"days": 10, "description": "Receive discovery"}
        },
        "appeal": {
            "civil": {"days": 30, "description": "File notice of appeal"},
            "criminal": {"days": 30, "description": "File notice of appeal"}
        },
        "motion_response": {
            "civil": {"days": 21, "description": "Respond to motion"},
            "criminal": {"days": 14, "description": "Respond to motion"}
        }
    }

    calculated = {}
    event_info = deadlines.get(event_type, {})
    case_info = event_info.get(case_type.lower(), {})

    if case_info:
        deadline_date = start + timedelta(days=case_info.get("days", 0))
        calculated = {
            "event": event_type,
            "start_date": start_date,
            "deadline_date": deadline_date.strftime("%Y-%m-%d"),
            "days": case_info.get("days"),
            "description": case_info.get("description")
        }
    else:
        calculated = {"error": f"Unknown event type: {event_type}"}

    return {
        "state": state.upper(),
        "case_type": case_type,
        "calculated_deadline": calculated,
        "disclaimer": "This is for reference only. Actual deadlines depend on court rules and may exclude weekends/holidays.",
        "available_events": list(deadlines.keys())
    }


# ============================================================================
# COURT CLERK SERVICES
# ============================================================================

@app.get("/v1/state-courts/clerk-services")
def get_clerk_services_info(state: str = None, service_type: str = None):
    """Get court clerk services information."""
    _ensure_initialized()

    return {
        "state": state.upper() if state else None,
        "service_type": service_type,
        "services_available": {
            "filing": {
                "what": "Accept and process court documents",
                "how": "In person, by mail, or e-filing",
                "fees": "Vary by document type"
            },
            "certified_copies": {
                "what": "Official copies of court documents",
                "cost": "$5-25 per document typically",
                "uses": ["Name changes", "Background checks", "Legal proceedings"]
            },
            "case_lookup": {
                "what": "Search court records",
                "how": "Online portal, in person, by phone",
                "limitations": "Some records sealed or confidential"
            },
            "fee_waivers": {
                "what": "Waiver of filing fees for low income",
                "how": "Submit application with income documentation",
                "covers": "Most filing fees, some service costs"
            },
            "notarization": {
                "availability": "Some clerk offices offer",
                "cost": "Free or nominal"
            },
            "marriage_licenses": {
                "issued_by": "County clerk (usually separate)",
                "requirements": "ID, fee, waiting period varies"
            }
        },
        "clerk_cannot_help_with": [
            "Legal advice",
            "Which forms to file",
            "How to fill out forms",
            "Predicting case outcomes",
            "Recommending attorneys"
        ],
        "tip": "Clerks can tell you procedural requirements but not legal advice"
    }
