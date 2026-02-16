"""HTML view templates for PACER RSS feeds"""
from typing import List, Optional, Dict, Any
import json
import re
from urllib.parse import quote_plus

def _parse_meta(meta_json: Optional[str]) -> Optional[dict]:
    try:
        return json.loads(meta_json) if meta_json else None
    except Exception:
        return None

def _group_items_by_case(items: List[Dict[str, Any]]):
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for it in items:
        key = (it.get('court_code'), it.get('case_number')) if it.get('case_number') else (it.get('id'), None)
        groups.setdefault(key, []).append(it)
    # Build combined records ordered by recency (items already generally sorted by published desc)
    combined = []
    for (court_code, case_number), grp in groups.items():
        # Most recent first
        grp_sorted = grp  # assume already sorted
        head = grp_sorted[0]
        # Build events list (max 5)
        events = []
        for g in grp_sorted[:5]:
            m = _parse_meta(g.get('metadata_json')) or {}
            events.append({
                'title': g.get('title'),
                'published': g.get('published'),
                'event_type': m.get('event_type'),
                'doc_number': m.get('doc_number'),
                'entry_no': m.get('docket_entry_number'),
                'is_new_case': m.get('is_new_case'),
            })
        # Combine badges/meta from head + union
        head_meta = _parse_meta(head.get('metadata_json')) or {}
        combined.append({
            'id': head.get('id'),
            'court_code': court_code or head.get('court_code'),
            'case_number': case_number or head.get('case_number') or 'N/A',
            'case_type': head.get('case_type'),
            'judge_name': head.get('judge_name'),
            'nature_of_suit': head.get('nature_of_suit'),
            'title': head.get('title'),
            'summary': head.get('summary'),
            'link': head.get('link'),
            'published': head.get('published'),
            'metadata_json': head.get('metadata_json'),
            '_events': events,
        })
    return combined

def generate_html_template(title: str, items: List, current_filter: Optional[str] = None, court_code: Optional[str] = None, group_cases: bool = False, new_only: bool = False):
    """Generate HTML template for displaying court filings"""

    filter_url_base = f"/feeds/court/{court_code}" if court_code else "/feeds"
    # Apply newly-filed filter if requested
    if new_only:
        filtered = []
        for it in items:
            try:
                meta = json.loads(it.get('metadata_json') or 'null')
            except Exception:
                meta = None
            if isinstance(meta, dict) and meta.get('is_new_case'):
                filtered.append(it)
        items = filtered
    items_to_render = _group_items_by_case(items) if group_cases else items

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                line-height: 1.6;
                color: #333;
                background: #f5f5f5;
                padding: 20px;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
                background: white;
                padding: 30px;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            h1 {{
                color: #1a1a1a;
                margin-bottom: 10px;
                font-size: 2em;
            }}
            .stats {{
                color: #666;
                margin-bottom: 20px;
                font-size: 0.9em;
            }}
            .filters {{
                margin: 20px 0;
                padding: 15px;
                background: #f8f9fa;
                border-radius: 6px;
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
                align-items: center;
            }}
            .filter-btn {{
                padding: 8px 16px;
                border: 1px solid #ddd;
                background: white;
                border-radius: 4px;
                cursor: pointer;
                text-decoration: none;
                color: #333;
                font-size: 0.9em;
                transition: all 0.2s;
            }}
            .filter-btn:hover {{
                background: #e9ecef;
                border-color: #999;
            }}
            .filter-btn.active {{
                background: #007bff;
                color: white;
                border-color: #007bff;
            }}
            .filing {{
                border-bottom: 1px solid #e0e0e0;
                padding: 20px 0;
            }}
            .filing:last-child {{ border-bottom: none; }}
            .filing-header {{
                display: flex;
                align-items: baseline;
                gap: 10px;
                margin-bottom: 8px;
                flex-wrap: wrap;
            }}
            .case-number {{
                font-weight: 600;
                color: #007bff;
                font-size: 1.1em;
                display: inline-block;
                min-width: 160px;
            }}
            .case-type {{
                display: inline-block;
                padding: 2px 8px;
                background: #e7f3ff;
                color: #0066cc;
                border-radius: 3px;
                font-size: 0.75em;
                font-weight: 600;
                text-transform: uppercase;
            }}
            .case-type.cr {{ background: #ffe7e7; color: #cc0000; }}
            .case-type.bk {{ background: #fff3e7; color: #cc6600; }}
            .case-type.ap {{ background: #f0e7ff; color: #6600cc; }}
            .court-badge {{
                display: inline-block;
                padding: 2px 8px;
                background: #e0e0e0;
                color: #555;
                border-radius: 3px;
                font-size: 0.75em;
                font-weight: 600;
            }}
            .division-badge {{
                display: inline-block;
                padding: 2px 8px;
                background: #f1f8e9;
                color: #558b2f;
                border-radius: 3px;
                font-size: 0.75em;
                font-weight: 600;
            }}
            .judge-badge {{
                display: inline-block;
                padding: 2px 8px;
                background: #e8f5e9;
                color: #2e7d32;
                border-radius: 3px;
                font-size: 0.75em;
                font-weight: 500;
            }}
            .nature-badge {{
                display: inline-block;
                padding: 2px 8px;
                background: #fff3e0;
                color: #e65100;
                border-radius: 3px;
                font-size: 0.75em;
                font-weight: 500;
            }}
            .filing-title {{
                font-size: 1.05em;
                margin-bottom: 5px;
                color: #1a1a1a;
            }}
            .filing-summary {{
                color: #666;
                font-size: 0.9em;
                margin-bottom: 8px;
            }}
            .filing-meta {{
                display: flex;
                gap: 15px;
                font-size: 0.85em;
                color: #888;
            }}
            .filing-link {{
                color: #007bff;
                text-decoration: none;
            }}
            .filing-link:hover {{
                text-decoration: underline;
            }}
            .nav-links {{
                margin-top: 20px;
                padding-top: 20px;
                border-top: 1px solid #e0e0e0;
            }}
            .nav-links a {{
                color: #007bff;
                text-decoration: none;
                margin-right: 20px;
            }}
            .muted {{ color: #999; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>{title}</h1>
            <div class="stats">Showing {len(items_to_render)} most recent {('newly filed ' if new_only else '')}{('cases' if group_cases else 'filings')}</div>

            <div class="filters">
                <strong>Filter by case type:</strong>
                <a href="{filter_url_base}" class="filter-btn {'active' if not current_filter else ''}">All</a>
                <a href="{filter_url_base}?case_type=cv" class="filter-btn {'active' if current_filter == 'cv' else ''}">Civil (CV)</a>
                <a href="{filter_url_base}?case_type=cr" class="filter-btn {'active' if current_filter == 'cr' else ''}">Criminal (CR)</a>
                <a href="{filter_url_base}?case_type=bk" class="filter-btn {'active' if current_filter == 'bk' else ''}">Bankruptcy (BK)</a>
                <a href="{filter_url_base}?case_type=ap" class="filter-btn {'active' if current_filter == 'ap' else ''}">Adversary (AP)</a>
                <span style="margin-left: 10px; color:#999">|</span>
                <a href="{filter_url_base}?{'case_type='+current_filter+'&' if current_filter else ''}group={'0' if group_cases else '1'}{'&new=1' if new_only else ''}" class="filter-btn {'active' if group_cases else ''}">{'Grouped by case' if group_cases else 'Ungrouped'}</a>
                <a href="{filter_url_base}?{'case_type='+current_filter+'&' if current_filter else ''}{'group=1&' if group_cases else ''}new={'0' if new_only else '1'}" class="filter-btn {'active' if new_only else ''}">Newly filed</a>
            </div>

            <div class="filings">
    """

    for item in items_to_render:
        case_number = item.get('case_number') or 'N/A'
        case_type = item.get('case_type') or ''
        court = item.get('court_code') or 'unknown'
        judge_name = item.get('judge_name') or ''
        nature_of_suit = item.get('nature_of_suit') or ''
        title_text = item.get('title') or 'Untitled'
        summary = item.get('summary') or ''
        link = item.get('link') or '#'
        published = item.get('published') or 'Unknown date'
        # Find a direct doc1 PDF link if present (often inside summary)
        doc1_url = link if ('/doc1/' in link) else None
        if not doc1_url and summary:
            m = re.search(r"https?://[^\s\"']+/doc1/[^\s\"'<>]+", summary)
            if m:
                doc1_url = m.group(0)
        meta = None
        try:
            meta = json.loads(item.get('metadata_json') or 'null')
        except Exception:
            meta = None

        new_badge = ''
        parties_line = ''
        nos_badge = ''
        cause_badge = ''
        doc_badge = ''
        event_badge = ''
        entry_badge = ''
        division_badge = ''
        caption_text = ''

        if isinstance(meta, dict):
            if meta.get('is_new_case'):
                new_badge = '<span class="judge-badge" style="background:#e6ffed;color:#1a7f37">NEW CASE</span>'
            if isinstance(meta.get('parties'), dict):
                pls = ', '.join(meta['parties'].get('plaintiffs') or [])
                defs = ', '.join(meta['parties'].get('defendants') or [])
                if pls or defs:
                    caption_text = f"{pls} v. {defs}"
            if isinstance(meta.get('nos'), dict) and meta['nos'].get('code'):
                label = meta['nos'].get('label') or ''
                nos_badge = f"<span class=\"nature-badge\">NOS {meta['nos']['code']} {label}</span>"
            if meta.get('cause_of_action'):
                cause_badge = f"<span class=\"nature-badge\" style=\"background:#e3f2fd;color:#1565c0\">Cause: {meta['cause_of_action']}</span>"
            if meta.get('doc_number'):
                doc_badge = f"<span class=\"judge-badge\" style=\"background:#fff8e1;color:#8d6e63\">Doc #{meta['doc_number']}</span>"
            if meta.get('event_type'):
                et = meta['event_type'].replace('_',' ').title()
                event_badge = f"<span class=\"judge-badge\" style=\"background:#e0f7fa;color:#006064\">{et}</span>"
            if meta.get('docket_entry_number'):
                entry_badge = f"<span class=\"judge-badge\" style=\"background:#ede7f6;color:#4527a0\">Entry #{meta['docket_entry_number']}</span>"
            # Division/office badge from case_parts
            cp = meta.get('case_parts') or {}
            divn = cp.get('division_number') or (int(cp['office']) if isinstance(cp.get('office'), str) and cp.get('office').isdigit() else None)
            if divn:
                division_badge = f"<span class=\"division-badge\">DIV {divn}</span>"

        case_type_badge = f'<span class="case-type {case_type}">{case_type.upper()}</span>' if case_type else ''
        judge_badge = f'<span class="judge-badge">⚖️ Judge {judge_name}</span>' if judge_name else ''
        nature_badge = f'<span class="nature-badge">{nature_of_suit}</span>' if nature_of_suit else ''
        case_number_span = f'<span class="case-number {"muted" if (not case_number or case_number=="N/A") else ""}">{case_number if (case_number and case_number!="N/A") else "—"}</span>'

        # Derive a clean caption if not present from metadata
        if not caption_text:
            import re as _re
            stripped = _re.sub(r"^\s*\d+:\d{2}-(cv|cr|bk|ap|mc|md)-\d{3,6}(?:-\d+)?\s*", "", title_text, flags=_re.IGNORECASE)
            caption_text = stripped.split(" - ")[0].strip() or title_text

        # Optional PACER download endpoint link (if doc1 URL detected)
        pacer_dl_btn = ''
        if doc1_url:
            # Derive court code from doc1 host if possible
            mhost = re.search(r"ecf\.([a-z0-9]+)\.uscourts\.gov", doc1_url, re.IGNORECASE)
            court_for_doc = (mhost.group(1) if mhost else court)
            # Prefer the browser-based fetch endpoint for higher reliability
            pacer_href = f"/v1/pacer/document_browser?court_code={court_for_doc}&doc_url={quote_plus(doc1_url)}&download=1"
            pacer_dl_btn = f"<a href=\"{pacer_href}\" target=\"_blank\" class=\"filter-btn\">Download via PACER (PDF)</a>"

        html += f"""
                <div class="filing">
                    <div class="filing-header">
                        {case_number_span}
                        {case_type_badge}
                        <span class="court-badge">{court.upper()}</span>
                        {judge_badge}
                        {nature_badge}
                        {nos_badge}
                        {cause_badge}
                        {doc_badge}
                        {event_badge}
                        {entry_badge}
                        {division_badge}
                        {new_badge}
                    </div>
                    <div class="filing-title">{caption_text}</div>
                    {(f'<div class="filing-summary">{summary[:200]}</div>' if summary else '')}
                    {'' if not item.get('_events') else '<div class="filing-summary" style="margin-top:6px">' + ''.join([f"<div style=\"color:#555;\">• {ev.get('published') or ''} — {((ev.get('event_type') or 'Docket event').replace('_',' ').title())}{(' (Doc #'+str(ev.get('doc_number'))+')') if ev.get('doc_number') else ''}{(' (Entry #'+str(ev.get('entry_no'))+')') if ev.get('entry_no') else ''}</div>" for ev in item.get('_events')]) + '</div>'}
                    <div class="filing-meta">
                        <span>{published}</span>
                        <a href="{link}" target="_blank" class="filing-link">View on PACER →</a>
                        {pacer_dl_btn}
                    </div>
                </div>
        """

    html += """
            </div>

            <div class="nav-links">
                <a href="/feeds">← All Courts</a>
                <a href="/feeds/all.xml">RSS Feed</a>
                <a href="/docs">API Documentation</a>
            </div>
        </div>
    </body>
    </html>
    """

    return html
