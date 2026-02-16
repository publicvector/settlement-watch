"""Predictive Analytics Dashboard HTML Generator"""
from typing import Dict, List, Any


def generate_predictive_dashboard_html(
    summary: Dict[str, Any],
    nos_matrix: Dict[str, Any],
    motion_stats: Dict[str, Any],
    judge_stats: Dict[str, Any],
    pro_se_data: Dict[str, Any],
    court_benchmarks: Dict[str, Any]
) -> str:
    """Generate the predictive analytics dashboard HTML."""

    # Prepare data for charts
    matrix = nos_matrix.get("matrix", [])[:15]
    motion_data = motion_stats.get("motion_stats", [])
    judges = judge_stats.get("judges", [])[:30]
    benchmarks = court_benchmarks.get("court_benchmarks", [])[:30]
    national_avgs = court_benchmarks.get("national_averages", {})
    pro_se_comparison = pro_se_data.get("comparison", [])

    # Data source summary
    sources = summary.get("data_sources", {})
    fjc_records = sources.get("fjc_outcomes", {}).get("records", 0)
    motion_records = sources.get("motion_outcomes", {}).get("records", 0)
    judge_records = sources.get("judge_motion_stats", {}).get("records", 0)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Predictive Analytics Dashboard</title>
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
        .data-quality-banner {{
            background: linear-gradient(135deg, #1e40af 0%, #1e3a5f 100%);
            border-radius: 12px;
            padding: 20px 24px;
            margin-bottom: 30px;
            border: 1px solid #3b82f6;
        }}
        .data-quality-banner h3 {{
            color: #93c5fd;
            font-size: 1em;
            margin-bottom: 12px;
        }}
        .data-stats {{
            display: flex;
            gap: 30px;
            flex-wrap: wrap;
        }}
        .data-stat {{
            display: flex;
            flex-direction: column;
        }}
        .data-stat .value {{
            font-size: 1.5em;
            font-weight: 700;
            color: #f8fafc;
        }}
        .data-stat .label {{
            font-size: 0.8em;
            color: #94a3b8;
        }}
        .section {{
            margin-bottom: 40px;
        }}
        .section-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }}
        .section-header h2 {{
            font-size: 1.3em;
            color: #f8fafc;
        }}
        .section-header .badge {{
            font-size: 0.75em;
            padding: 4px 12px;
            border-radius: 9999px;
            background: #334155;
            color: #94a3b8;
        }}
        .section-header .badge.high {{ background: #065f46; color: #6ee7b7; }}
        .section-header .badge.medium {{ background: #92400e; color: #fcd34d; }}
        .section-header .badge.low {{ background: #991b1b; color: #fca5a5; }}
        .grid-2 {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
        }}
        .grid-3 {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
        }}
        .card {{
            background: #1e293b;
            border-radius: 12px;
            padding: 24px;
            border: 1px solid #334155;
        }}
        .card h3 {{
            color: #f8fafc;
            font-size: 1.1em;
            margin-bottom: 16px;
        }}
        .card.full-width {{
            grid-column: 1 / -1;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9em;
        }}
        th {{
            text-align: left;
            padding: 12px 8px;
            background: #0f172a;
            color: #94a3b8;
            font-weight: 600;
            font-size: 0.8em;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border-bottom: 1px solid #334155;
            cursor: pointer;
            user-select: none;
            white-space: nowrap;
        }}
        th:hover {{ color: #60a5fa; }}
        td {{
            padding: 10px 8px;
            border-bottom: 1px solid #1e293b;
            color: #e2e8f0;
        }}
        tr:hover td {{ background: rgba(59, 130, 246, 0.05); }}
        .pct-bar {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .pct-bar-bg {{
            flex: 1;
            height: 8px;
            background: #334155;
            border-radius: 4px;
            overflow: hidden;
            max-width: 100px;
        }}
        .pct-bar-fill {{
            height: 100%;
            border-radius: 4px;
        }}
        .pct-bar-fill.green {{ background: #10b981; }}
        .pct-bar-fill.red {{ background: #ef4444; }}
        .pct-bar-fill.yellow {{ background: #f59e0b; }}
        .pct-bar-fill.blue {{ background: #3b82f6; }}
        .confidence {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75em;
            font-weight: 600;
        }}
        .confidence.high {{ background: #065f46; color: #6ee7b7; }}
        .confidence.medium {{ background: #92400e; color: #fcd34d; }}
        .confidence.low {{ background: #991b1b; color: #fca5a5; }}
        .ci-range {{
            font-size: 0.8em;
            color: #64748b;
        }}
        .methodology {{
            background: #0f172a;
            border-radius: 8px;
            padding: 16px;
            margin-top: 16px;
            font-size: 0.85em;
            color: #94a3b8;
            border-left: 3px solid #3b82f6;
        }}
        .methodology strong {{ color: #60a5fa; }}
        .stat-highlight {{
            display: flex;
            justify-content: space-between;
            padding: 12px 0;
            border-bottom: 1px solid #334155;
        }}
        .stat-highlight:last-child {{ border-bottom: none; }}
        .stat-highlight .label {{ color: #94a3b8; }}
        .stat-highlight .value {{ font-weight: 600; color: #f8fafc; }}
        .stat-highlight .value.positive {{ color: #10b981; }}
        .stat-highlight .value.negative {{ color: #ef4444; }}
        .vs-national {{
            font-size: 0.8em;
            padding: 2px 6px;
            border-radius: 4px;
        }}
        .vs-national.positive {{ background: rgba(16, 185, 129, 0.2); color: #6ee7b7; }}
        .vs-national.negative {{ background: rgba(239, 68, 68, 0.2); color: #fca5a5; }}
        .finding {{
            padding: 10px 16px;
            background: rgba(59, 130, 246, 0.1);
            border-left: 3px solid #3b82f6;
            border-radius: 0 8px 8px 0;
            margin-bottom: 10px;
            font-size: 0.9em;
        }}
        .predictor-form {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        .form-group {{
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        .form-group label {{
            font-size: 0.85em;
            color: #94a3b8;
        }}
        .form-group select, .form-group input {{
            padding: 10px 12px;
            border-radius: 6px;
            border: 1px solid #334155;
            background: #0f172a;
            color: #e2e8f0;
            font-size: 0.9em;
        }}
        .form-group select:focus, .form-group input:focus {{
            outline: none;
            border-color: #3b82f6;
        }}
        .btn {{
            padding: 10px 20px;
            border-radius: 6px;
            border: none;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.9em;
            transition: all 0.2s;
        }}
        .btn-primary {{ background: #3b82f6; color: white; }}
        .btn-primary:hover {{ background: #2563eb; }}
        #predictionResult {{
            margin-top: 20px;
            padding: 20px;
            background: #0f172a;
            border-radius: 8px;
            display: none;
        }}
        @media (max-width: 1200px) {{
            .grid-2, .grid-3 {{ grid-template-columns: 1fr; }}
            .container {{ padding: 15px; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Predictive Analytics Dashboard</h1>
        <p class="subtitle">Objective, data-driven case outcome analysis based on {fjc_records:,} historical federal court cases</p>
        <nav class="nav">
            <a href="/dashboard">RSS Dashboard</a>
            <a href="/analytics" class="active">Federal Analytics</a>
            <a href="/state-courts">State Courts</a>
            <a href="/reader">Live Feed</a>
            <a href="/feeds">HTML View</a>
            <a href="/docs">API Docs</a>
        </nav>
    </div>

    <div class="container">
        <!-- Data Quality Banner -->
        <div class="data-quality-banner">
            <h3>Data Sources & Quality</h3>
            <div class="data-stats">
                <div class="data-stat">
                    <span class="value">{fjc_records:,}</span>
                    <span class="label">FJC Case Outcomes</span>
                </div>
                <div class="data-stat">
                    <span class="value">{motion_records:,}</span>
                    <span class="label">Motion Outcomes</span>
                </div>
                <div class="data-stat">
                    <span class="value">{judge_records}</span>
                    <span class="label">Judge Profiles</span>
                </div>
                <div class="data-stat">
                    <span class="value">{len(matrix)}</span>
                    <span class="label">NOS Categories</span>
                </div>
            </div>
        </div>

        <!-- Case Outcome Predictor -->
        <div class="section">
            <div class="section-header">
                <h2>Case Outcome Predictor</h2>
                <span class="badge high">Interactive Tool</span>
            </div>
            <div class="card">
                <div class="predictor-form">
                    <div class="form-group">
                        <label>Nature of Suit</label>
                        <select id="nosSelect">
                            <option value="">All Categories</option>
"""

    # Add NOS options from matrix
    for item in nos_matrix.get("matrix", []):
        nos_code = item.get("nos_code", "")
        nos_desc = item.get("nos_description", "")
        html += f'                            <option value="{nos_code}">{nos_code} - {nos_desc}</option>\n'

    html += """                        </select>
                    </div>
                    <div class="form-group">
                        <label>Court</label>
                        <select id="courtSelect">
                            <option value="">All Courts</option>
"""

    # Add court options from benchmarks
    for court in court_benchmarks.get("court_benchmarks", [])[:50]:
        code = court.get("court_code", "")
        html += f'                            <option value="{code.lower()}">{code}</option>\n'

    html += """                        </select>
                    </div>
                    <div class="form-group">
                        <label>Litigant Status</label>
                        <select id="proSeSelect">
                            <option value="false">Represented</option>
                            <option value="true">Pro Se</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>&nbsp;</label>
                        <button class="btn btn-primary" onclick="getPrediction()">Get Prediction</button>
                    </div>
                </div>
                <div id="predictionResult"></div>
            </div>
        </div>

        <!-- Motion Outcomes -->
        <div class="section">
            <div class="section-header">
                <h2>Motion Outcomes</h2>
                <span class="badge medium">n={motion_records:,}</span>
            </div>
            <div class="grid-2">
"""

    # Motion cards
    for ms in motion_data:
        mt = ms.get("motion_type_full", ms.get("motion_type", ""))
        total = ms.get("total", 0)
        grant_rate = ms.get("grant_rate", 0)
        deny_rate = ms.get("deny_rate", 0)
        partial_rate = ms.get("partial_rate", 0)
        ci = ms.get("confidence_interval_95", [0, 100])
        conf = ms.get("confidence", "low")

        html += f"""
                <div class="card">
                    <h3>{mt}</h3>
                    <div class="stat-highlight">
                        <span class="label">Grant Rate</span>
                        <span class="value">{grant_rate}%</span>
                    </div>
                    <div class="stat-highlight">
                        <span class="label">Deny Rate</span>
                        <span class="value">{deny_rate}%</span>
                    </div>
                    <div class="stat-highlight">
                        <span class="label">Partial (Granted in Part)</span>
                        <span class="value">{partial_rate}%</span>
                    </div>
                    <div class="stat-highlight">
                        <span class="label">95% Confidence Interval</span>
                        <span class="ci-range">{ci[0]}% - {ci[1]}%</span>
                    </div>
                    <div class="stat-highlight">
                        <span class="label">Sample Size</span>
                        <span class="value">n={total}</span>
                    </div>
                    <div class="stat-highlight">
                        <span class="label">Confidence</span>
                        <span class="confidence {conf}">{conf.upper()}</span>
                    </div>
                </div>
"""

    html += """
            </div>
            <div class="methodology">
                <strong>Methodology:</strong> Outcomes extracted from docket entry text analysis.
                "Partial" represents motions "granted in part and denied in part" - preserved as reported, not simplified.
                Confidence intervals calculated using Wilson score method.
            </div>
        </div>

        <!-- NOS Outcome Matrix -->
        <div class="section">
            <div class="section-header">
                <h2>Case Outcomes by Nature of Suit</h2>
                <span class="badge high">FJC Data</span>
            </div>
            <div class="card full-width">
                <table id="nosTable">
                    <thead>
                        <tr>
                            <th onclick="sortTable('nosTable', 0)">NOS Code</th>
                            <th onclick="sortTable('nosTable', 1)">Category</th>
                            <th onclick="sortTable('nosTable', 2)">Cases</th>
                            <th onclick="sortTable('nosTable', 3)">Settlement %</th>
                            <th onclick="sortTable('nosTable', 4)">Dismissal %</th>
                            <th onclick="sortTable('nosTable', 5)">Judgment %</th>
                            <th onclick="sortTable('nosTable', 6)">Trial %</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    for item in matrix:
        nos_code = item.get("nos_code", "")
        nos_desc = item.get("nos_description", "")[:40]
        total = item.get("total", 0)
        settlement = item.get("settlement_pct", 0)
        dismissal = item.get("dismissal_pct", 0)
        judgment = item.get("motion_judgment_pct", 0)
        trial = round(item.get("jury_verdict_pct", 0) + item.get("court_trial_pct", 0), 1)

        html += f"""
                        <tr>
                            <td><strong>{nos_code}</strong></td>
                            <td>{nos_desc}</td>
                            <td>{total:,}</td>
                            <td>
                                <div class="pct-bar">
                                    <div class="pct-bar-bg"><div class="pct-bar-fill green" style="width:{min(settlement, 100)}%"></div></div>
                                    <span>{settlement}%</span>
                                </div>
                            </td>
                            <td>
                                <div class="pct-bar">
                                    <div class="pct-bar-bg"><div class="pct-bar-fill red" style="width:{min(dismissal, 100)}%"></div></div>
                                    <span>{dismissal}%</span>
                                </div>
                            </td>
                            <td>
                                <div class="pct-bar">
                                    <div class="pct-bar-bg"><div class="pct-bar-fill blue" style="width:{min(judgment, 100)}%"></div></div>
                                    <span>{judgment}%</span>
                                </div>
                            </td>
                            <td>
                                <div class="pct-bar">
                                    <div class="pct-bar-bg"><div class="pct-bar-fill yellow" style="width:{min(trial * 5, 100)}%"></div></div>
                                    <span>{trial}%</span>
                                </div>
                            </td>
                        </tr>
"""

    html += """
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Pro Se Analysis -->
        <div class="section">
            <div class="section-header">
                <h2>Pro Se vs Represented Outcomes</h2>
                <span class="badge high">FJC Data</span>
            </div>
            <div class="grid-2">
                <div class="card">
                    <h3>Key Findings</h3>
"""

    for finding in pro_se_data.get("key_findings", []):
        html += f'                    <div class="finding">{finding}</div>\n'

    html += f"""
                    <div class="stat-highlight">
                        <span class="label">Pro Se Cases Analyzed</span>
                        <span class="value">{pro_se_data.get('pro_se_total_cases', 0):,}</span>
                    </div>
                    <div class="stat-highlight">
                        <span class="label">Represented Cases Analyzed</span>
                        <span class="value">{pro_se_data.get('represented_total_cases', 0):,}</span>
                    </div>
                </div>
                <div class="card">
                    <h3>Outcome Comparison</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Outcome</th>
                                <th>Pro Se</th>
                                <th>Represented</th>
                                <th>Diff</th>
                            </tr>
                        </thead>
                        <tbody>
"""

    for comp in pro_se_comparison[:8]:
        outcome = comp.get("outcome", "")
        ps_pct = comp.get("pro_se_pct", 0)
        rep_pct = comp.get("represented_pct", 0)
        diff = comp.get("difference", 0)
        diff_class = "positive" if diff > 0 else "negative" if diff < 0 else ""

        html += f"""
                            <tr>
                                <td>{outcome}</td>
                                <td>{ps_pct}%</td>
                                <td>{rep_pct}%</td>
                                <td><span class="vs-national {diff_class}">{'+' if diff > 0 else ''}{diff}%</span></td>
                            </tr>
"""

    html += """
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Judge Motion Rates -->
        <div class="section">
            <div class="section-header">
                <h2>Judge Motion Grant Rates</h2>
                <span class="badge medium">Minimum 5 motions required</span>
            </div>
            <div class="card full-width">
                <table id="judgeTable">
                    <thead>
                        <tr>
                            <th onclick="sortTable('judgeTable', 0)">Judge</th>
                            <th onclick="sortTable('judgeTable', 1)">Court</th>
                            <th onclick="sortTable('judgeTable', 2)">Motion Type</th>
                            <th onclick="sortTable('judgeTable', 3)">Total</th>
                            <th onclick="sortTable('judgeTable', 4)">Grant Rate</th>
                            <th>95% CI</th>
                            <th>Confidence</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    for judge in judges:
        name = judge.get("judge_name", "")
        court = judge.get("court_code", "")
        mt = judge.get("motion_type", "")
        total = judge.get("total_motions", 0)
        grant_rate = judge.get("grant_rate", 0)
        ci = judge.get("confidence_interval_95", [0, 100])
        conf = judge.get("confidence", "low")

        html += f"""
                        <tr>
                            <td>{name}</td>
                            <td>{court}</td>
                            <td>{mt}</td>
                            <td>{total}</td>
                            <td>
                                <div class="pct-bar">
                                    <div class="pct-bar-bg"><div class="pct-bar-fill blue" style="width:{min(grant_rate, 100)}%"></div></div>
                                    <span>{grant_rate}%</span>
                                </div>
                            </td>
                            <td class="ci-range">{ci[0]}% - {ci[1]}%</td>
                            <td><span class="confidence {conf}">{conf.upper()}</span></td>
                        </tr>
"""

    html += """
                    </tbody>
                </table>
            </div>
            <div class="methodology">
                <strong>Note:</strong> Judge-level statistics have smaller sample sizes and wider confidence intervals.
                Use as directional indicators only. Individual case outcomes depend on facts not captured in aggregate data.
            </div>
        </div>

        <!-- Court Benchmarks -->
        <div class="section">
            <div class="section-header">
                <h2>Court Benchmarks vs National Average</h2>
                <span class="badge high">FJC Data</span>
            </div>
            <div class="card full-width">
                <p style="margin-bottom:15px;color:#94a3b8;">
                    National Averages: Settlement {national_avgs.get('settlement', 0)}% |
                    Dismissal {national_avgs.get('dismissal', 0)}% |
                    Judgment {national_avgs.get('motion_judgment', 0)}% |
                    Trial {round(national_avgs.get('jury_verdict', 0) + national_avgs.get('court_trial', 0), 1)}%
                </p>
                <table id="courtTable">
                    <thead>
                        <tr>
                            <th onclick="sortTable('courtTable', 0)">Court</th>
                            <th onclick="sortTable('courtTable', 1)">Cases</th>
                            <th onclick="sortTable('courtTable', 2)">Settlement</th>
                            <th onclick="sortTable('courtTable', 3)">Dismissal</th>
                            <th onclick="sortTable('courtTable', 4)">Trial Rate</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    for court in benchmarks:
        code = court.get("court_code", "")
        total = court.get("total_cases", 0)
        settlement = court.get("settlement_rate", 0)
        dismissal = court.get("dismissal_rate", 0)
        trial = court.get("trial_rate", 0)
        vs_nat = court.get("vs_national", {})

        settle_diff = vs_nat.get("settlement", 0)
        dismiss_diff = vs_nat.get("dismissal", 0)

        html += f"""
                        <tr>
                            <td><strong>{code}</strong></td>
                            <td>{total:,}</td>
                            <td>
                                {settlement}%
                                <span class="vs-national {'positive' if settle_diff > 0 else 'negative' if settle_diff < 0 else ''}">{'+' if settle_diff > 0 else ''}{settle_diff}%</span>
                            </td>
                            <td>
                                {dismissal}%
                                <span class="vs-national {'positive' if dismiss_diff < 0 else 'negative' if dismiss_diff > 0 else ''}">{'+' if dismiss_diff > 0 else ''}{dismiss_diff}%</span>
                            </td>
                            <td>{trial}%</td>
                        </tr>
"""

    html += """
                    </tbody>
                </table>
            </div>
        </div>

    </div>

    <script>
        // Sortable tables
        let sortState = {};
        function sortTable(tableId, colIdx) {
            const table = document.getElementById(tableId);
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));

            const key = tableId + '-' + colIdx;
            sortState[key] = !sortState[key];
            const asc = sortState[key];

            rows.sort((a, b) => {
                let aVal = a.cells[colIdx].textContent.trim().replace(/[,%+]/g, '');
                let bVal = b.cells[colIdx].textContent.trim().replace(/[,%+]/g, '');
                const aNum = parseFloat(aVal);
                const bNum = parseFloat(bVal);
                if (!isNaN(aNum) && !isNaN(bNum)) {
                    return asc ? aNum - bNum : bNum - aNum;
                }
                return asc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
            });

            rows.forEach(row => tbody.appendChild(row));
        }

        // Case predictor
        async function getPrediction() {
            const nos = document.getElementById('nosSelect').value;
            const court = document.getElementById('courtSelect').value;
            const proSe = document.getElementById('proSeSelect').value;

            const resultDiv = document.getElementById('predictionResult');
            resultDiv.style.display = 'block';
            resultDiv.innerHTML = '<p style="color:#94a3b8;">Loading prediction...</p>';

            try {
                let url = `/v1/predict/case?pro_se=${proSe}`;
                if (nos) url += `&nos_code=${nos}`;
                if (court) url += `&court_code=${court}`;

                const res = await fetch(url);
                const data = await res.json();

                if (data.error) {
                    resultDiv.innerHTML = `<p style="color:#fca5a5;">${data.error}</p>`;
                    return;
                }

                let html = `
                    <h4 style="color:#f8fafc;margin-bottom:15px;">Predicted Outcomes</h4>
                    <p style="color:#94a3b8;font-size:0.9em;margin-bottom:15px;">
                        Based on ${data.sample_size?.toLocaleString() || 0} historical cases
                        <span class="confidence ${data.confidence}">${(data.confidence || 'low').toUpperCase()}</span>
                    </p>
                `;

                if (data.predicted_outcomes) {
                    html += '<table><thead><tr><th>Outcome</th><th>Probability</th><th>Historical Count</th></tr></thead><tbody>';
                    for (const o of data.predicted_outcomes) {
                        html += `<tr>
                            <td>${o.outcome}</td>
                            <td>
                                <div class="pct-bar">
                                    <div class="pct-bar-bg"><div class="pct-bar-fill blue" style="width:${Math.min(o.probability, 100)}%"></div></div>
                                    <span>${o.probability}%</span>
                                </div>
                            </td>
                            <td>${o.historical_count?.toLocaleString() || 0}</td>
                        </tr>`;
                    }
                    html += '</tbody></table>';
                }

                if (data.judgment_for_probabilities && data.judgment_for_probabilities.length > 0) {
                    html += '<h4 style="color:#f8fafc;margin:20px 0 10px;">Judgment For (when applicable)</h4>';
                    html += '<table><thead><tr><th>Party</th><th>Probability</th></tr></thead><tbody>';
                    for (const j of data.judgment_for_probabilities) {
                        html += `<tr><td>${j.party}</td><td>${j.probability}%</td></tr>`;
                    }
                    html += '</tbody></table>';
                }

                html += `<p style="color:#64748b;font-size:0.8em;margin-top:15px;font-style:italic;">${data.disclaimer || ''}</p>`;

                resultDiv.innerHTML = html;

            } catch (err) {
                resultDiv.innerHTML = `<p style="color:#fca5a5;">Error: ${err.message}</p>`;
            }
        }
    </script>
</body>
</html>
"""

    return html
