"""State Courts Analytics Dashboard HTML Generator"""
from typing import Dict, List, Any
import json


# US State coordinates for SVG map (simplified polygon centroids)
US_STATE_COORDS = {
    "AL": {"x": 580, "y": 340, "name": "Alabama"},
    "AK": {"x": 150, "y": 450, "name": "Alaska"},
    "AZ": {"x": 230, "y": 320, "name": "Arizona"},
    "AR": {"x": 500, "y": 310, "name": "Arkansas"},
    "CA": {"x": 120, "y": 260, "name": "California"},
    "CO": {"x": 310, "y": 250, "name": "Colorado"},
    "CT": {"x": 730, "y": 170, "name": "Connecticut"},
    "DE": {"x": 710, "y": 220, "name": "Delaware"},
    "FL": {"x": 640, "y": 410, "name": "Florida"},
    "GA": {"x": 620, "y": 340, "name": "Georgia"},
    "HI": {"x": 250, "y": 450, "name": "Hawaii"},
    "ID": {"x": 200, "y": 150, "name": "Idaho"},
    "IL": {"x": 530, "y": 230, "name": "Illinois"},
    "IN": {"x": 560, "y": 230, "name": "Indiana"},
    "IA": {"x": 470, "y": 200, "name": "Iowa"},
    "KS": {"x": 400, "y": 260, "name": "Kansas"},
    "KY": {"x": 580, "y": 260, "name": "Kentucky"},
    "LA": {"x": 500, "y": 380, "name": "Louisiana"},
    "ME": {"x": 750, "y": 100, "name": "Maine"},
    "MD": {"x": 690, "y": 220, "name": "Maryland"},
    "MA": {"x": 740, "y": 155, "name": "Massachusetts"},
    "MI": {"x": 560, "y": 160, "name": "Michigan"},
    "MN": {"x": 460, "y": 130, "name": "Minnesota"},
    "MS": {"x": 540, "y": 350, "name": "Mississippi"},
    "MO": {"x": 490, "y": 260, "name": "Missouri"},
    "MT": {"x": 270, "y": 110, "name": "Montana"},
    "NE": {"x": 390, "y": 200, "name": "Nebraska"},
    "NV": {"x": 175, "y": 230, "name": "Nevada"},
    "NH": {"x": 735, "y": 130, "name": "New Hampshire"},
    "NJ": {"x": 715, "y": 200, "name": "New Jersey"},
    "NM": {"x": 290, "y": 320, "name": "New Mexico"},
    "NY": {"x": 700, "y": 155, "name": "New York"},
    "NC": {"x": 660, "y": 280, "name": "North Carolina"},
    "ND": {"x": 390, "y": 110, "name": "North Dakota"},
    "OH": {"x": 600, "y": 220, "name": "Ohio"},
    "OK": {"x": 410, "y": 310, "name": "Oklahoma"},
    "OR": {"x": 140, "y": 140, "name": "Oregon"},
    "PA": {"x": 670, "y": 195, "name": "Pennsylvania"},
    "RI": {"x": 745, "y": 165, "name": "Rhode Island"},
    "SC": {"x": 650, "y": 310, "name": "South Carolina"},
    "SD": {"x": 390, "y": 155, "name": "South Dakota"},
    "TN": {"x": 570, "y": 290, "name": "Tennessee"},
    "TX": {"x": 380, "y": 380, "name": "Texas"},
    "UT": {"x": 235, "y": 230, "name": "Utah"},
    "VT": {"x": 720, "y": 120, "name": "Vermont"},
    "VA": {"x": 670, "y": 250, "name": "Virginia"},
    "WA": {"x": 150, "y": 90, "name": "Washington"},
    "WV": {"x": 640, "y": 240, "name": "West Virginia"},
    "WI": {"x": 510, "y": 155, "name": "Wisconsin"},
    "WY": {"x": 290, "y": 180, "name": "Wyoming"},
}


def generate_state_courts_dashboard_html(
    stats: Dict[str, Any],
    recent_cases: List[Dict[str, Any]],
    recent_opinions: List[Dict[str, Any]],
    coverage_info: Dict[str, Any],
    scraper_status: Dict[str, Any] = None,
    captcha_queue: List[Dict[str, Any]] = None
) -> str:
    """Generate the state courts analytics dashboard HTML."""

    # Extract stats
    total_cases = stats.get("total_cases", 0)
    total_opinions = stats.get("total_opinions", 0)
    states_covered = stats.get("states_covered", 0)
    cases_by_state = stats.get("by_state", [])
    opinions_by_state = stats.get("opinions_by_state", [])
    cases_by_type = stats.get("by_case_type", [])

    # Initialize optional data
    scraper_status = scraper_status or {}
    captcha_queue = captcha_queue or []

    # Build state coverage data for map
    state_case_counts = {st.get('state', ''): st.get('count', 0) for st in cases_by_state}

    # Determine coverage level for each state
    state_coverage = {}
    for state_code in US_STATE_COORDS.keys():
        case_count = state_case_counts.get(state_code, 0)
        scraper_info = scraper_status.get(state_code, {})

        if case_count > 1000:
            level = "full"
        elif case_count > 0 or scraper_info.get("active"):
            level = "partial"
        elif scraper_info.get("configured"):
            level = "configured"
        else:
            level = "none"

        state_coverage[state_code] = {
            "level": level,
            "cases": case_count,
            "last_sync": scraper_info.get("last_sync", "Never"),
            "status": scraper_info.get("status", "idle"),
            "errors": scraper_info.get("errors", 0)
        }

    # Build state breakdown table rows
    state_rows = ""
    for st in cases_by_state[:15]:
        state_rows += f"""
        <tr>
            <td>{st.get('state', 'Unknown')}</td>
            <td class="numeric">{st.get('count', 0):,}</td>
            <td class="numeric">{st.get('counties', 0)}</td>
        </tr>"""

    # Build opinions by state rows
    opinion_rows = ""
    for op in opinions_by_state[:15]:
        opinion_rows += f"""
        <tr>
            <td>{op.get('state', 'Unknown')}</td>
            <td class="numeric">{op.get('count', 0):,}</td>
            <td>{op.get('courts', 'Various')}</td>
        </tr>"""

    # Build case type breakdown
    type_rows = ""
    for ct in cases_by_type[:10]:
        type_rows += f"""
        <tr>
            <td>{ct.get('case_type', 'Unknown')}</td>
            <td class="numeric">{ct.get('count', 0):,}</td>
            <td class="numeric">{ct.get('percentage', 0):.1f}%</td>
        </tr>"""

    # State chart data
    state_chart_labels = json.dumps([st.get('state', 'Unknown') for st in cases_by_state[:8]])
    state_chart_data = json.dumps([st.get('count', 0) for st in cases_by_state[:8]])

    # If no data, use placeholder
    if not cases_by_state:
        state_chart_labels = json.dumps(["OK", "VA", "AR", "IL", "NM", "NC"])
        state_chart_data = json.dumps([0, 0, 0, 0, 0, 0])

    # Type chart data
    type_chart_labels = json.dumps([ct.get('case_type', 'Unknown') for ct in cases_by_type[:6]])
    type_chart_data = json.dumps([ct.get('count', 0) for ct in cases_by_type[:6]])

    # If no data, use placeholder
    if not cases_by_type:
        type_chart_labels = json.dumps(["Criminal", "Civil", "Family", "Probate", "Traffic"])
        type_chart_data = json.dumps([0, 0, 0, 0, 0])

    # Build recent cases list
    case_items = ""
    for case in recent_cases[:10]:
        case_items += f"""
        <div class="case-item">
            <div class="case-header">
                <span class="case-number">{case.get('case_number', 'N/A')}</span>
                <span class="state-badge">{case.get('state', '??')}</span>
            </div>
            <div class="case-style">{case.get('case_style', 'Unknown')[:60]}</div>
            <div class="case-meta">
                <span>{case.get('county', 'Unknown County')}</span>
                <span>{case.get('case_type', 'Unknown')}</span>
                <span>{case.get('date_filed', 'No date')}</span>
            </div>
        </div>"""

    # Build recent opinions list
    opinion_items = ""
    for op in recent_opinions[:10]:
        opinion_items += f"""
        <div class="opinion-item">
            <div class="opinion-header">
                <span class="opinion-citation">{op.get('citation', 'No citation')}</span>
                <span class="state-badge">{op.get('state', '??')}</span>
            </div>
            <div class="opinion-name">{op.get('case_name', 'Unknown')[:60]}</div>
            <div class="opinion-meta">
                <span>{op.get('court', 'Unknown Court')}</span>
                <span>{op.get('date_decided', 'No date')}</span>
            </div>
        </div>"""

    # Coverage info
    full_access = coverage_info.get("full_data_access", [])
    appellate = coverage_info.get("appellate_opinions", [])
    partial = coverage_info.get("partial_access", [])

    coverage_full = ""
    for src in full_access:
        coverage_full += f"""
        <div class="coverage-item full">
            <span class="state-code">{src.get('state')}</span>
            <span class="state-name">{src.get('name')}</span>
            <span class="coverage-detail">{src.get('coverage')}</span>
        </div>"""

    coverage_appellate = ""
    for src in appellate:
        coverage_appellate += f"""
        <div class="coverage-item appellate">
            <span class="state-code">{src.get('state')}</span>
            <span class="state-name">{src.get('name')}</span>
            <span class="coverage-detail">Appellate opinions</span>
        </div>"""

    coverage_partial = ""
    for src in partial:
        coverage_partial += f"""
        <div class="coverage-item partial">
            <span class="state-code">{src.get('state')}</span>
            <span class="state-name">{src.get('name')}</span>
            <span class="coverage-detail">{src.get('note', 'Limited access')}</span>
        </div>"""

    # Build CAPTCHA queue items
    captcha_items = ""
    for cap in captcha_queue[:10]:
        resolved_class = "resolved" if cap.get("resolved") else ""
        captcha_items += f"""
        <div class="captcha-item {resolved_class}">
            <div class="captcha-header">
                <span class="state-badge">{cap.get('state', '??')}</span>
                <span class="captcha-time">{cap.get('encountered_at', 'Unknown')}</span>
            </div>
            <div class="captcha-url">{cap.get('url', 'Unknown URL')[:60]}</div>
            <div class="captcha-actions">
                <button class="captcha-btn resolve" onclick="resolveCaptcha('{cap.get('id', '')}')">
                    {'Resolved' if cap.get('resolved') else 'Mark Resolved'}
                </button>
                <a href="{cap.get('url', '#')}" target="_blank" class="captcha-btn view">Open URL</a>
            </div>
        </div>"""

    # Build scraper status rows
    scraper_rows = ""
    active_scrapers = 0
    total_errors = 0
    for state_code, info in sorted(scraper_status.items()):
        status = info.get("status", "idle")
        status_class = "running" if status == "running" else ("error" if info.get("errors", 0) > 0 else "idle")
        if status == "running":
            active_scrapers += 1
        total_errors += info.get("errors", 0)

        scraper_rows += f"""
        <tr class="scraper-row {status_class}">
            <td><span class="state-badge">{state_code}</span></td>
            <td>{US_STATE_COORDS.get(state_code, {}).get('name', state_code)}</td>
            <td><span class="status-indicator {status_class}">{status}</span></td>
            <td class="numeric">{info.get('cases_found', 0):,}</td>
            <td class="numeric error-count">{info.get('errors', 0)}</td>
            <td>{info.get('last_sync', 'Never')}</td>
        </tr>"""

    # Generate SVG map markers
    map_markers = ""
    for state_code, coords in US_STATE_COORDS.items():
        cov = state_coverage.get(state_code, {})
        level = cov.get("level", "none")
        cases = cov.get("cases", 0)

        if level == "full":
            color = "#22c55e"  # Green
        elif level == "partial":
            color = "#f59e0b"  # Yellow/Orange
        elif level == "configured":
            color = "#3b82f6"  # Blue
        else:
            color = "#64748b"  # Gray

        map_markers += f"""
        <circle cx="{coords['x']}" cy="{coords['y']}" r="12"
                fill="{color}" stroke="#1e293b" stroke-width="2"
                class="state-marker" data-state="{state_code}"
                onclick="showStateDetails('{state_code}')" />
        <text x="{coords['x']}" y="{coords['y'] + 4}"
              text-anchor="middle" fill="white" font-size="10" font-weight="600"
              class="state-label" pointer-events="none">{state_code}</text>
        """

    # State coverage data for JavaScript
    state_coverage_json = json.dumps(state_coverage)

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
            font-size: 0.95em;
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
            transition: all 0.2s;
            font-size: 0.9em;
        }}
        .nav a:hover {{ background: rgba(96, 165, 250, 0.2); }}
        .nav a.active {{ background: #3b82f6; color: white; }}
        .container {{
            max-width: 1800px;
            margin: 0 auto;
            padding: 30px;
        }}
        .summary-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .summary-card {{
            background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
            border-radius: 12px;
            padding: 24px;
            border: 1px solid #334155;
        }}
        .summary-card h3 {{
            color: #94a3b8;
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }}
        .summary-card .value {{
            font-size: 2.2em;
            font-weight: 700;
            color: #f8fafc;
        }}
        .summary-card .subtitle {{
            color: #64748b;
            font-size: 0.9em;
            margin-top: 5px;
        }}
        .summary-card.success .value {{ color: #22c55e; }}
        .summary-card.warning .value {{ color: #f59e0b; }}
        .summary-card.error .value {{ color: #ef4444; }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 25px;
            margin-bottom: 30px;
        }}
        .card {{
            background: #1e293b;
            border-radius: 12px;
            padding: 24px;
            border: 1px solid #334155;
        }}
        .card.wide {{
            grid-column: span 2;
        }}
        .card h2 {{
            color: #f8fafc;
            font-size: 1.1em;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 1px solid #334155;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .card h2 .badge {{
            background: #3b82f6;
            color: white;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.75em;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid #334155;
        }}
        th {{
            color: #94a3b8;
            font-weight: 500;
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        td {{ color: #e2e8f0; font-size: 0.9em; }}
        td.numeric {{ text-align: right; font-family: 'SF Mono', Monaco, monospace; }}
        tr:hover {{ background: rgba(59, 130, 246, 0.1); }}

        /* Map styles */
        .map-container {{
            background: #0f172a;
            border-radius: 8px;
            padding: 20px;
            position: relative;
        }}
        .map-svg {{
            width: 100%;
            height: 500px;
        }}
        .state-marker {{
            cursor: pointer;
            transition: all 0.2s;
        }}
        .state-marker:hover {{
            transform: scale(1.2);
            filter: brightness(1.2);
        }}
        .map-legend {{
            display: flex;
            gap: 20px;
            justify-content: center;
            margin-top: 15px;
            flex-wrap: wrap;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.85em;
            color: #94a3b8;
        }}
        .legend-dot {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }}
        .legend-dot.full {{ background: #22c55e; }}
        .legend-dot.partial {{ background: #f59e0b; }}
        .legend-dot.configured {{ background: #3b82f6; }}
        .legend-dot.none {{ background: #64748b; }}

        /* State details popup */
        .state-popup {{
            display: none;
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 24px;
            min-width: 350px;
            z-index: 1000;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
        }}
        .state-popup.active {{ display: block; }}
        .state-popup-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.5);
            z-index: 999;
        }}
        .state-popup-overlay.active {{ display: block; }}
        .state-popup h3 {{
            color: #f8fafc;
            margin-bottom: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .state-popup .close-btn {{
            background: none;
            border: none;
            color: #94a3b8;
            font-size: 1.5em;
            cursor: pointer;
        }}
        .state-popup .stat-row {{
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid #334155;
        }}
        .state-popup .stat-label {{ color: #94a3b8; }}
        .state-popup .stat-value {{ color: #f8fafc; font-weight: 600; }}
        .state-popup .action-btn {{
            display: inline-block;
            margin-top: 15px;
            padding: 10px 20px;
            background: #3b82f6;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.9em;
        }}
        .state-popup .action-btn:hover {{ background: #2563eb; }}

        /* CAPTCHA queue styles */
        .captcha-item {{
            padding: 12px;
            border: 1px solid #334155;
            border-radius: 8px;
            margin-bottom: 10px;
            background: rgba(239, 68, 68, 0.1);
        }}
        .captcha-item.resolved {{
            background: rgba(34, 197, 94, 0.1);
            opacity: 0.7;
        }}
        .captcha-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }}
        .captcha-time {{
            color: #64748b;
            font-size: 0.8em;
        }}
        .captcha-url {{
            font-family: 'SF Mono', Monaco, monospace;
            font-size: 0.85em;
            color: #94a3b8;
            margin-bottom: 10px;
            word-break: break-all;
        }}
        .captcha-actions {{
            display: flex;
            gap: 10px;
        }}
        .captcha-btn {{
            padding: 6px 12px;
            border: none;
            border-radius: 4px;
            font-size: 0.8em;
            cursor: pointer;
            text-decoration: none;
        }}
        .captcha-btn.resolve {{
            background: #22c55e;
            color: white;
        }}
        .captcha-btn.view {{
            background: #475569;
            color: white;
        }}

        /* Scraper status styles */
        .status-indicator {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: 500;
        }}
        .status-indicator.running {{
            background: rgba(34, 197, 94, 0.2);
            color: #22c55e;
        }}
        .status-indicator.idle {{
            background: rgba(100, 116, 139, 0.2);
            color: #94a3b8;
        }}
        .status-indicator.error {{
            background: rgba(239, 68, 68, 0.2);
            color: #ef4444;
        }}
        .scraper-row.running {{ background: rgba(34, 197, 94, 0.05); }}
        .scraper-row.error {{ background: rgba(239, 68, 68, 0.05); }}
        .error-count {{ color: #ef4444; }}

        .case-item, .opinion-item {{
            padding: 12px 0;
            border-bottom: 1px solid #334155;
        }}
        .case-header, .opinion-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
        }}
        .case-number, .opinion-citation {{
            font-family: 'SF Mono', Monaco, monospace;
            color: #60a5fa;
            font-size: 0.9em;
        }}
        .state-badge {{
            background: #3b82f6;
            color: white;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75em;
            font-weight: 600;
        }}
        .case-style, .opinion-name {{
            color: #f8fafc;
            font-size: 0.95em;
            margin-bottom: 6px;
        }}
        .case-meta, .opinion-meta {{
            display: flex;
            gap: 15px;
            color: #64748b;
            font-size: 0.85em;
        }}
        .coverage-section {{
            margin-bottom: 20px;
        }}
        .coverage-section h3 {{
            color: #94a3b8;
            font-size: 0.9em;
            margin-bottom: 10px;
        }}
        .coverage-item {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 8px 12px;
            border-radius: 6px;
            margin-bottom: 8px;
        }}
        .coverage-item.full {{ background: rgba(34, 197, 94, 0.15); border-left: 3px solid #22c55e; }}
        .coverage-item.appellate {{ background: rgba(59, 130, 246, 0.15); border-left: 3px solid #3b82f6; }}
        .coverage-item.partial {{ background: rgba(245, 158, 11, 0.15); border-left: 3px solid #f59e0b; }}
        .state-code {{
            font-family: 'SF Mono', Monaco, monospace;
            font-weight: 600;
            width: 30px;
        }}
        .state-name {{ color: #f8fafc; flex: 1; }}
        .coverage-detail {{ color: #94a3b8; font-size: 0.85em; }}
        .ingest-button {{
            display: inline-block;
            background: #3b82f6;
            color: white;
            padding: 12px 24px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 500;
            margin-right: 10px;
            margin-bottom: 10px;
            transition: background 0.2s;
            border: none;
            cursor: pointer;
            font-size: 0.95em;
        }}
        .ingest-button:hover {{ background: #2563eb; }}
        .ingest-button.secondary {{
            background: #475569;
        }}
        .ingest-button.secondary:hover {{ background: #64748b; }}
        .ingest-button.success {{
            background: #22c55e;
        }}
        .ingest-button.warning {{
            background: #f59e0b;
        }}
        .actions {{
            margin-top: 20px;
            padding-top: 20px;
            border-top: 1px solid #334155;
        }}
        .tabs {{
            display: flex;
            gap: 5px;
            margin-bottom: 20px;
        }}
        .tab {{
            padding: 10px 20px;
            background: transparent;
            border: none;
            color: #94a3b8;
            cursor: pointer;
            border-radius: 6px;
            font-size: 0.9em;
        }}
        .tab.active {{
            background: #334155;
            color: #f8fafc;
        }}
        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}
        .scrollable {{
            max-height: 400px;
            overflow-y: auto;
        }}
        .empty-state {{
            text-align: center;
            padding: 40px;
            color: #64748b;
        }}
        .pulse {{
            animation: pulse 2s infinite;
        }}
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>State Courts Analytics Dashboard</h1>
        <p class="subtitle">50-state trial court dockets and appellate opinions - {states_covered} jurisdictions active</p>
        <nav class="nav">
            <a href="/">Federal RSS Feed</a>
            <a href="/analytics">Federal Analytics</a>
            <a href="/state-courts" class="active">State Courts</a>
            <a href="/docs">API Docs</a>
        </nav>
    </div>

    <div class="container">
        <div class="summary-cards">
            <div class="summary-card">
                <h3>Total Cases</h3>
                <div class="value">{total_cases:,}</div>
                <div class="subtitle">State trial court cases</div>
            </div>
            <div class="summary-card">
                <h3>Appellate Opinions</h3>
                <div class="value">{total_opinions:,}</div>
                <div class="subtitle">State appellate decisions</div>
            </div>
            <div class="summary-card">
                <h3>States Active</h3>
                <div class="value">{states_covered}/50</div>
                <div class="subtitle">Jurisdictions with data</div>
            </div>
            <div class="summary-card {'success' if active_scrapers > 0 else ''}">
                <h3>Active Scrapers</h3>
                <div class="value">{active_scrapers}</div>
                <div class="subtitle">Currently running</div>
            </div>
            <div class="summary-card {'error' if len(captcha_queue) > 0 else ''}">
                <h3>CAPTCHA Queue</h3>
                <div class="value">{len([c for c in captcha_queue if not c.get('resolved')])}</div>
                <div class="subtitle">Pending resolution</div>
            </div>
            <div class="summary-card {'warning' if total_errors > 0 else ''}">
                <h3>Total Errors</h3>
                <div class="value">{total_errors}</div>
                <div class="subtitle">Across all scrapers</div>
            </div>
        </div>

        <!-- Coverage Map -->
        <div class="card wide">
            <h2>50-State Coverage Map <span class="badge">Interactive</span></h2>
            <div class="map-container">
                <svg class="map-svg" viewBox="0 0 800 500" preserveAspectRatio="xMidYMid meet">
                    <!-- Background -->
                    <rect x="0" y="0" width="800" height="500" fill="#0f172a"/>

                    <!-- State markers -->
                    {map_markers}
                </svg>
                <div class="map-legend">
                    <div class="legend-item">
                        <div class="legend-dot full"></div>
                        <span>Full Coverage (1000+ cases)</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-dot partial"></div>
                        <span>Partial Coverage</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-dot configured"></div>
                        <span>Configured</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-dot none"></div>
                        <span>Not Active</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Charts Section -->
        <div class="grid" style="grid-template-columns: repeat(2, 1fr);">
            <div class="card">
                <h2>Case Distribution by State</h2>
                <canvas id="stateChart" height="250"></canvas>
            </div>
            <div class="card">
                <h2>Case Types Distribution</h2>
                <canvas id="typeChart" height="250"></canvas>
            </div>
        </div>

        <div class="grid">
            <!-- CAPTCHA Queue -->
            <div class="card">
                <h2>CAPTCHA Intervention Queue <span class="badge">{len([c for c in captcha_queue if not c.get('resolved')])}</span></h2>
                <div class="scrollable">
                    {captcha_items if captcha_items else '<div class="empty-state">No CAPTCHAs pending. All scrapers running smoothly.</div>'}
                </div>
            </div>

            <!-- Scraper Status -->
            <div class="card">
                <h2>Scraper Status</h2>
                <div class="scrollable">
                    <table>
                        <thead>
                            <tr>
                                <th>State</th>
                                <th>Name</th>
                                <th>Status</th>
                                <th>Cases</th>
                                <th>Errors</th>
                                <th>Last Sync</th>
                            </tr>
                        </thead>
                        <tbody>
                            {scraper_rows if scraper_rows else '<tr><td colspan="6" class="empty-state">No scraper activity recorded</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="card">
                <h2>Cases by State</h2>
                <table>
                    <thead>
                        <tr>
                            <th>State</th>
                            <th>Cases</th>
                            <th>Counties</th>
                        </tr>
                    </thead>
                    <tbody>
                        {state_rows if state_rows else '<tr><td colspan="3" style="text-align:center;color:#64748b">No cases ingested yet. Run ingestion to populate.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h2>Opinions by State</h2>
                <table>
                    <thead>
                        <tr>
                            <th>State</th>
                            <th>Opinions</th>
                            <th>Courts</th>
                        </tr>
                    </thead>
                    <tbody>
                        {opinion_rows if opinion_rows else '<tr><td colspan="3" style="text-align:center;color:#64748b">No opinions ingested yet. Run ingestion to populate.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h2>Cases by Type</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Case Type</th>
                            <th>Count</th>
                            <th>Share</th>
                        </tr>
                    </thead>
                    <tbody>
                        {type_rows if type_rows else '<tr><td colspan="3" style="text-align:center;color:#64748b">No case type data available.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h2>Data Coverage</h2>
                <div class="coverage-section">
                    <h3>Full Trial Court Access</h3>
                    {coverage_full if coverage_full else '<div class="coverage-item partial"><span class="coverage-detail">No full access sources configured</span></div>'}
                </div>
                <div class="coverage-section">
                    <h3>Appellate Opinions (via CourtListener)</h3>
                    {coverage_appellate if coverage_appellate else '<div class="coverage-item partial"><span class="coverage-detail">No appellate sources configured</span></div>'}
                </div>
                <div class="coverage-section">
                    <h3>Partial Access (County Portals)</h3>
                    {coverage_partial if coverage_partial else '<div class="coverage-item partial"><span class="coverage-detail">No partial access sources</span></div>'}
                </div>
            </div>

            <div class="card">
                <h2>Recent Cases</h2>
                <div class="scrollable">
                    {case_items if case_items else '<div class="empty-state">No recent cases. Run ingestion to fetch data.</div>'}
                </div>
            </div>

            <div class="card">
                <h2>Recent Appellate Opinions</h2>
                <div class="scrollable">
                    {opinion_items if opinion_items else '<div class="empty-state">No recent opinions. Run ingestion to fetch data.</div>'}
                </div>
            </div>
        </div>

        <div class="card">
            <h2>Data Ingestion & Scraping Control</h2>
            <p style="color:#94a3b8;margin-bottom:20px">
                Trigger data ingestion from 50 state court systems. Select specific states or run full nationwide ingestion.
            </p>

            <div class="tabs">
                <button class="tab active" onclick="showTab('quick')">Quick Actions</button>
                <button class="tab" onclick="showTab('states')">By State</button>
                <button class="tab" onclick="showTab('advanced')">Advanced</button>
            </div>

            <div id="tab-quick" class="tab-content active">
                <div class="actions" style="border-top:none;padding-top:0;">
                    <button class="ingest-button success" onclick="runIngest('all')">Run Full 50-State Ingestion</button>
                    <button class="ingest-button" onclick="runIngest('opinions')">Appellate Opinions Only</button>
                    <button class="ingest-button secondary" onclick="runIngest('oklahoma')">Oklahoma (OSCN)</button>
                    <button class="ingest-button secondary" onclick="runIngest('virginia')">Virginia (Bulk)</button>
                </div>
            </div>

            <div id="tab-states" class="tab-content">
                <div class="actions" style="border-top:none;padding-top:0;display:flex;flex-wrap:wrap;gap:8px;">
                    {''.join([f"<button class='ingest-button secondary' style='padding:8px 12px;font-size:0.85em' onclick=\"runIngest('{code.lower()}')\">{code}</button>" for code in sorted(US_STATE_COORDS.keys())])}
                </div>
            </div>

            <div id="tab-advanced" class="tab-content">
                <div class="actions" style="border-top:none;padding-top:0;">
                    <button class="ingest-button warning" onclick="runIngest('retry-failed')">Retry Failed Scrapers</button>
                    <button class="ingest-button secondary" onclick="clearCaptchaQueue()">Clear Resolved CAPTCHAs</button>
                    <button class="ingest-button secondary" onclick="resetErrors()">Reset Error Counts</button>
                </div>
            </div>

            <div id="ingest-status" style="margin-top:15px;color:#94a3b8"></div>
        </div>
    </div>

    <!-- State Details Popup -->
    <div class="state-popup-overlay" onclick="closeStatePopup()"></div>
    <div class="state-popup">
        <h3>
            <span id="popup-state-name">State Name</span>
            <button class="close-btn" onclick="closeStatePopup()">&times;</button>
        </h3>
        <div class="stat-row">
            <span class="stat-label">Cases</span>
            <span class="stat-value" id="popup-cases">0</span>
        </div>
        <div class="stat-row">
            <span class="stat-label">Status</span>
            <span class="stat-value" id="popup-status">idle</span>
        </div>
        <div class="stat-row">
            <span class="stat-label">Last Sync</span>
            <span class="stat-value" id="popup-last-sync">Never</span>
        </div>
        <div class="stat-row">
            <span class="stat-label">Errors</span>
            <span class="stat-value" id="popup-errors">0</span>
        </div>
        <button class="action-btn" id="popup-scrape-btn" onclick="scrapeState()">Run Scraper</button>
    </div>

    <script>
        // State coverage data
        const stateCoverage = {state_coverage_json};
        const stateNames = {json.dumps({k: v['name'] for k, v in US_STATE_COORDS.items()})};
        let currentState = null;

        // Chart.js configuration
        Chart.defaults.color = '#94a3b8';
        Chart.defaults.borderColor = '#334155';

        // State distribution chart
        const stateCtx = document.getElementById('stateChart');
        if (stateCtx) {{
            new Chart(stateCtx, {{
                type: 'bar',
                data: {{
                    labels: {state_chart_labels},
                    datasets: [{{
                        label: 'Cases',
                        data: {state_chart_data},
                        backgroundColor: [
                            'rgba(59, 130, 246, 0.7)',
                            'rgba(34, 197, 94, 0.7)',
                            'rgba(245, 158, 11, 0.7)',
                            'rgba(239, 68, 68, 0.7)',
                            'rgba(168, 85, 247, 0.7)',
                            'rgba(20, 184, 166, 0.7)',
                            'rgba(249, 115, 22, 0.7)',
                            'rgba(236, 72, 153, 0.7)'
                        ],
                        borderColor: [
                            'rgb(59, 130, 246)',
                            'rgb(34, 197, 94)',
                            'rgb(245, 158, 11)',
                            'rgb(239, 68, 68)',
                            'rgb(168, 85, 247)',
                            'rgb(20, 184, 166)',
                            'rgb(249, 115, 22)',
                            'rgb(236, 72, 153)'
                        ],
                        borderWidth: 1
                    }}]
                }},
                options: {{
                    responsive: true,
                    plugins: {{
                        legend: {{ display: false }}
                    }},
                    scales: {{
                        y: {{
                            beginAtZero: true,
                            grid: {{ color: 'rgba(51, 65, 85, 0.5)' }}
                        }},
                        x: {{
                            grid: {{ display: false }}
                        }}
                    }}
                }}
            }});
        }}

        // Case type distribution chart
        const typeCtx = document.getElementById('typeChart');
        if (typeCtx) {{
            new Chart(typeCtx, {{
                type: 'doughnut',
                data: {{
                    labels: {type_chart_labels},
                    datasets: [{{
                        data: {type_chart_data},
                        backgroundColor: [
                            'rgba(59, 130, 246, 0.8)',
                            'rgba(34, 197, 94, 0.8)',
                            'rgba(245, 158, 11, 0.8)',
                            'rgba(239, 68, 68, 0.8)',
                            'rgba(168, 85, 247, 0.8)',
                            'rgba(20, 184, 166, 0.8)'
                        ],
                        borderColor: '#1e293b',
                        borderWidth: 2
                    }}]
                }},
                options: {{
                    responsive: true,
                    plugins: {{
                        legend: {{
                            position: 'right',
                            labels: {{ boxWidth: 12, padding: 15 }}
                        }}
                    }}
                }}
            }});
        }}

        // Tab switching
        function showTab(tabName) {{
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`[onclick="showTab('${{tabName}}')"]`).classList.add('active');
            document.getElementById(`tab-${{tabName}}`).classList.add('active');
        }}

        // State popup functions
        function showStateDetails(stateCode) {{
            currentState = stateCode;
            const data = stateCoverage[stateCode] || {{}};
            const name = stateNames[stateCode] || stateCode;

            document.getElementById('popup-state-name').textContent = `${{name}} (${{stateCode}})`;
            document.getElementById('popup-cases').textContent = (data.cases || 0).toLocaleString();
            document.getElementById('popup-status').textContent = data.status || 'idle';
            document.getElementById('popup-last-sync').textContent = data.last_sync || 'Never';
            document.getElementById('popup-errors').textContent = data.errors || 0;

            document.querySelector('.state-popup-overlay').classList.add('active');
            document.querySelector('.state-popup').classList.add('active');
        }}

        function closeStatePopup() {{
            document.querySelector('.state-popup-overlay').classList.remove('active');
            document.querySelector('.state-popup').classList.remove('active');
            currentState = null;
        }}

        function scrapeState() {{
            if (currentState) {{
                closeStatePopup();
                runIngest(currentState.toLowerCase());
            }}
        }}

        // Ingestion function
        async function runIngest(type) {{
            const statusEl = document.getElementById('ingest-status');
            statusEl.innerHTML = '<span class="pulse">Starting ingestion...</span>';

            let url = '/v1/state-courts/ingest/all';
            if (type === 'oklahoma') url = '/v1/state-courts/ingest/oklahoma';
            else if (type === 'opinions') url = '/v1/state-courts/ingest/opinions';
            else if (type === 'virginia') url = '/v1/state-courts/ingest/virginia';
            else if (type === 'retry-failed') url = '/v1/state-courts/scrape/retry';
            else if (type.length === 2) url = `/v1/state-courts/scrape/${{type.toUpperCase()}}`;

            try {{
                const resp = await fetch(url, {{ method: 'POST' }});
                const data = await resp.json();
                if (resp.ok) {{
                    statusEl.innerHTML = '<span style="color:#22c55e">Ingestion complete!</span> ' +
                        'Stored: ' + (data.total_stored || data.cases_stored || data.opinions_stored || data.cases_found || 0) + ' records. ' +
                        '<a href="/state-courts" style="color:#60a5fa">Refresh</a>';
                }} else {{
                    statusEl.innerHTML = '<span style="color:#ef4444">Error:</span> ' + (data.detail || 'Unknown error');
                }}
            }} catch (err) {{
                statusEl.innerHTML = '<span style="color:#ef4444">Error:</span> ' + err.message;
            }}
        }}

        // CAPTCHA functions
        async function resolveCaptcha(captchaId) {{
            try {{
                const resp = await fetch(`/v1/state-courts/captcha/${{captchaId}}/resolve`, {{ method: 'POST' }});
                if (resp.ok) {{
                    location.reload();
                }}
            }} catch (err) {{
                console.error('Failed to resolve CAPTCHA:', err);
            }}
        }}

        async function clearCaptchaQueue() {{
            try {{
                const resp = await fetch('/v1/state-courts/captcha/clear-resolved', {{ method: 'POST' }});
                if (resp.ok) {{
                    location.reload();
                }}
            }} catch (err) {{
                console.error('Failed to clear queue:', err);
            }}
        }}

        async function resetErrors() {{
            try {{
                const resp = await fetch('/v1/state-courts/scrape/reset-errors', {{ method: 'POST' }});
                if (resp.ok) {{
                    location.reload();
                }}
            }} catch (err) {{
                console.error('Failed to reset errors:', err);
            }}
        }}

        // Health check on load
        async function checkHealth() {{
            try {{
                const resp = await fetch('/v1/state-courts/quality/health');
                const data = await resp.json();
                console.log('State Courts Health:', data);
            }} catch (e) {{
                console.warn('Health check failed:', e);
            }}
        }}
        checkHealth();
    </script>
</body>
</html>"""

    return html
