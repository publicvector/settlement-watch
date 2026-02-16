"""Enhanced Dashboard and Feed Reader views"""
from typing import Optional

def generate_dashboard_html(stats: dict, court_stats: list, nos_stats: list, trends: list) -> str:
    """Generate the main analytics dashboard with charts and sortable tables."""

    # Prepare chart data
    case_types = stats.get('case_types', {})
    case_type_labels = list(case_types.keys())
    case_type_values = list(case_types.values())

    # Trend data for chart
    trend_dates = [t.get('date', '') for t in reversed(trends[:14])]
    trend_values = [int(t.get('total_filings', 0)) for t in reversed(trends[:14])]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Federal Court Filings Dashboard</title>
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
        .header p {{ color: #94a3b8; margin-top: 5px; }}
        .nav {{
            display: flex;
            gap: 20px;
            margin-top: 15px;
        }}
        .nav a {{
            color: #60a5fa;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 6px;
            background: rgba(96, 165, 250, 0.1);
            transition: all 0.2s;
        }}
        .nav a:hover {{ background: rgba(96, 165, 250, 0.2); }}
        .nav a.active {{ background: #3b82f6; color: white; }}
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
            background: #1e293b;
            border-radius: 12px;
            padding: 24px;
            border: 1px solid #334155;
        }}
        .stat-card h3 {{
            color: #94a3b8;
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 8px;
        }}
        .stat-card .value {{
            font-size: 2.2em;
            font-weight: 700;
            color: #f8fafc;
        }}
        .stat-card .sub {{ color: #64748b; font-size: 0.85em; margin-top: 5px; }}
        .charts-row {{
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 20px;
            margin-bottom: 30px;
        }}
        .chart-card {{
            background: #1e293b;
            border-radius: 12px;
            padding: 24px;
            border: 1px solid #334155;
        }}
        .chart-card h3 {{
            color: #f8fafc;
            font-size: 1.1em;
            margin-bottom: 20px;
        }}
        .table-card {{
            background: #1e293b;
            border-radius: 12px;
            padding: 24px;
            border: 1px solid #334155;
            margin-bottom: 30px;
        }}
        .table-card h3 {{
            color: #f8fafc;
            font-size: 1.1em;
            margin-bottom: 20px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th {{
            text-align: left;
            padding: 12px;
            background: #0f172a;
            color: #94a3b8;
            font-weight: 600;
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            cursor: pointer;
            user-select: none;
            border-bottom: 1px solid #334155;
        }}
        th:hover {{ color: #60a5fa; }}
        th.sorted {{ color: #60a5fa; }}
        th.sorted::after {{ content: ' ▼'; }}
        th.sorted.asc::after {{ content: ' ▲'; }}
        td {{
            padding: 12px;
            border-bottom: 1px solid #1e293b;
            color: #e2e8f0;
        }}
        tr:hover td {{ background: rgba(59, 130, 246, 0.1); }}
        .badge {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 9999px;
            font-size: 0.75em;
            font-weight: 600;
        }}
        .badge-cv {{ background: #1e40af; color: #93c5fd; }}
        .badge-cr {{ background: #991b1b; color: #fca5a5; }}
        .badge-bk {{ background: #92400e; color: #fcd34d; }}
        .badge-md {{ background: #6b21a8; color: #d8b4fe; }}
        .search-box {{
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }}
        .search-box input {{
            flex: 1;
            padding: 12px 16px;
            border-radius: 8px;
            border: 1px solid #334155;
            background: #0f172a;
            color: #e2e8f0;
            font-size: 1em;
        }}
        .search-box input:focus {{
            outline: none;
            border-color: #3b82f6;
        }}
        .search-box button {{
            padding: 12px 24px;
            background: #3b82f6;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
        }}
        .search-box button:hover {{ background: #2563eb; }}
        @media (max-width: 900px) {{
            .charts-row {{ grid-template-columns: 1fr; }}
            .container {{ padding: 15px; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Federal Court Filings Dashboard</h1>
        <p>Real-time monitoring of all 94 federal district courts</p>
        <nav class="nav">
            <a href="/dashboard" class="active">Dashboard</a>
            <a href="/reader">Live Feed</a>
            <a href="/feeds">HTML View</a>
            <a href="/feeds/all.xml">RSS Feed</a>
            <a href="/v1/recap/status">RECAP Status</a>
        </nav>
    </div>

    <div class="container">
        <div class="stats-grid">
            <div class="stat-card">
                <h3>Total Filings</h3>
                <div class="value">{int(stats.get('total_filings', 0)):,}</div>
                <div class="sub">All captured docket entries</div>
            </div>
            <div class="stat-card">
                <h3>Unique Cases</h3>
                <div class="value">{int(stats.get('unique_cases', 0)):,}</div>
                <div class="sub">Distinct case numbers</div>
            </div>
            <div class="stat-card">
                <h3>Active Courts</h3>
                <div class="value">{int(stats.get('active_courts', 0))}</div>
                <div class="sub">Of 94 federal districts</div>
            </div>
            <div class="stat-card">
                <h3>Civil Cases</h3>
                <div class="value">{int(case_types.get('cv', 0)):,}</div>
                <div class="sub">{round(int(case_types.get('cv', 0)) / max(int(stats.get('total_filings', 1)), 1) * 100, 1)}% of total</div>
            </div>
            <div class="stat-card">
                <h3>Criminal Cases</h3>
                <div class="value">{int(case_types.get('cr', 0)):,}</div>
                <div class="sub">{round(int(case_types.get('cr', 0)) / max(int(stats.get('total_filings', 1)), 1) * 100, 1)}% of total</div>
            </div>
        </div>

        <div class="charts-row">
            <div class="chart-card">
                <h3>Filing Trends (Last 14 Days)</h3>
                <canvas id="trendChart" height="100"></canvas>
            </div>
            <div class="chart-card">
                <h3>Case Types</h3>
                <canvas id="typeChart" height="200"></canvas>
            </div>
        </div>

        <div class="table-card">
            <h3>Court Activity</h3>
            <table id="courtTable">
                <thead>
                    <tr>
                        <th onclick="sortTable('courtTable', 0)">Court</th>
                        <th onclick="sortTable('courtTable', 1)">Total Filings</th>
                        <th onclick="sortTable('courtTable', 2)">Unique Cases</th>
                        <th onclick="sortTable('courtTable', 3)">Civil</th>
                        <th onclick="sortTable('courtTable', 4)">Criminal</th>
                        <th onclick="sortTable('courtTable', 5)">Last Filing</th>
                    </tr>
                </thead>
                <tbody>
"""

    for court in court_stats[:30]:
        html += f"""
                    <tr>
                        <td><strong>{(court.get('court_code') or '').upper()}</strong></td>
                        <td>{int(court.get('total_filings', 0)):,}</td>
                        <td>{int(court.get('unique_cases', 0)):,}</td>
                        <td>{int(court.get('civil', 0)):,}</td>
                        <td>{int(court.get('criminal', 0)):,}</td>
                        <td style="color:#64748b;font-size:0.9em">{(court.get('most_recent') or '')[:16]}</td>
                    </tr>
"""

    html += """
                </tbody>
            </table>
        </div>

        <div class="table-card">
            <h3>Nature of Suit Breakdown</h3>
            <table id="nosTable">
                <thead>
                    <tr>
                        <th onclick="sortTable('nosTable', 0)">Category</th>
                        <th onclick="sortTable('nosTable', 1)">Filings</th>
                        <th onclick="sortTable('nosTable', 2)">Cases</th>
                        <th onclick="sortTable('nosTable', 3)">Courts</th>
                    </tr>
                </thead>
                <tbody>
"""

    for nos in nos_stats[:20]:
        html += f"""
                    <tr>
                        <td>{nos.get('nature_of_suit', 'Unknown')}</td>
                        <td>{int(nos.get('total_filings', 0)):,}</td>
                        <td>{int(nos.get('unique_cases', 0)):,}</td>
                        <td>{int(nos.get('courts', 0))}</td>
                    </tr>
"""

    html += f"""
                </tbody>
            </table>
        </div>
    </div>

    <script>
        // Trend chart
        new Chart(document.getElementById('trendChart'), {{
            type: 'line',
            data: {{
                labels: {trend_dates},
                datasets: [{{
                    label: 'Daily Filings',
                    data: {trend_values},
                    borderColor: '#3b82f6',
                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                    fill: true,
                    tension: 0.4
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

        // Case type pie chart
        new Chart(document.getElementById('typeChart'), {{
            type: 'doughnut',
            data: {{
                labels: {[t.upper() for t in case_type_labels]},
                datasets: [{{
                    data: {[int(v) for v in case_type_values]},
                    backgroundColor: ['#3b82f6', '#ef4444', '#f59e0b', '#8b5cf6', '#10b981']
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ position: 'bottom', labels: {{ color: '#94a3b8' }} }}
                }}
            }}
        }});

        // Sortable tables
        let sortState = {{}};
        function sortTable(tableId, colIdx) {{
            const table = document.getElementById(tableId);
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            const headers = table.querySelectorAll('th');

            const key = tableId + '-' + colIdx;
            sortState[key] = !sortState[key];
            const asc = sortState[key];

            headers.forEach((h, i) => {{
                h.classList.remove('sorted', 'asc');
                if (i === colIdx) {{
                    h.classList.add('sorted');
                    if (asc) h.classList.add('asc');
                }}
            }});

            rows.sort((a, b) => {{
                let aVal = a.cells[colIdx].textContent.trim().replace(/,/g, '');
                let bVal = b.cells[colIdx].textContent.trim().replace(/,/g, '');
                const aNum = parseFloat(aVal);
                const bNum = parseFloat(bVal);
                if (!isNaN(aNum) && !isNaN(bNum)) {{
                    return asc ? aNum - bNum : bNum - aNum;
                }}
                return asc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
            }});

            rows.forEach(row => tbody.appendChild(row));
        }}
    </script>
</body>
</html>
"""
    return html


def generate_feed_reader_html() -> str:
    """Generate the live feed reader with auto-refresh and filtering."""

    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Federal Court Filings - Live Feed</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
            padding: 20px 40px;
            border-bottom: 1px solid #334155;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        .header h1 { font-size: 1.5em; font-weight: 600; color: #f8fafc; }
        .nav {
            display: flex;
            gap: 20px;
            margin-top: 15px;
        }
        .nav a {
            color: #60a5fa;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 6px;
            background: rgba(96, 165, 250, 0.1);
        }
        .nav a:hover { background: rgba(96, 165, 250, 0.2); }
        .nav a.active { background: #3b82f6; color: white; }
        .controls {
            background: #1e293b;
            padding: 20px 40px;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            align-items: center;
            border-bottom: 1px solid #334155;
        }
        .control-group { display: flex; align-items: center; gap: 8px; }
        .control-group label { color: #94a3b8; font-size: 0.9em; }
        select, input[type="text"] {
            padding: 8px 12px;
            border-radius: 6px;
            border: 1px solid #334155;
            background: #0f172a;
            color: #e2e8f0;
            font-size: 0.9em;
        }
        select:focus, input:focus { outline: none; border-color: #3b82f6; }
        .btn {
            padding: 8px 16px;
            border-radius: 6px;
            border: none;
            cursor: pointer;
            font-size: 0.9em;
            font-weight: 500;
            transition: all 0.2s;
        }
        .btn-primary { background: #3b82f6; color: white; }
        .btn-primary:hover { background: #2563eb; }
        .btn-secondary { background: #334155; color: #e2e8f0; }
        .btn-secondary:hover { background: #475569; }
        .btn-success { background: #10b981; color: white; }
        .status {
            margin-left: auto;
            display: flex;
            align-items: center;
            gap: 8px;
            color: #94a3b8;
            font-size: 0.9em;
        }
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #10b981;
            animation: pulse 2s infinite;
        }
        .status-dot.paused { background: #f59e0b; animation: none; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .feed-container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .feed-item {
            background: #1e293b;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 15px;
            border: 1px solid #334155;
            transition: all 0.3s;
            animation: slideIn 0.3s ease-out;
        }
        @keyframes slideIn {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .feed-item:hover { border-color: #3b82f6; }
        .feed-item.new-case { border-left: 4px solid #10b981; }
        .item-header {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 10px;
            flex-wrap: wrap;
        }
        .case-number {
            font-weight: 700;
            color: #60a5fa;
            font-size: 1.1em;
        }
        .badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 9999px;
            font-size: 0.75em;
            font-weight: 600;
        }
        .badge-court { background: #334155; color: #94a3b8; }
        .badge-cv { background: #1e40af; color: #93c5fd; }
        .badge-cr { background: #991b1b; color: #fca5a5; }
        .badge-bk { background: #92400e; color: #fcd34d; }
        .badge-new { background: #065f46; color: #6ee7b7; }
        .badge-event { background: #1e3a5f; color: #7dd3fc; }
        .item-title {
            font-size: 1.05em;
            color: #f8fafc;
            margin-bottom: 8px;
        }
        .item-summary {
            color: #94a3b8;
            font-size: 0.9em;
            margin-bottom: 10px;
        }
        .item-meta {
            display: flex;
            gap: 20px;
            font-size: 0.85em;
            color: #64748b;
        }
        .item-meta a { color: #60a5fa; text-decoration: none; }
        .item-meta a:hover { text-decoration: underline; }
        .loading {
            text-align: center;
            padding: 40px;
            color: #64748b;
        }
        .empty {
            text-align: center;
            padding: 60px;
            color: #64748b;
        }
        .empty h3 { color: #94a3b8; margin-bottom: 10px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Live Federal Court Filings</h1>
        <nav class="nav">
            <a href="/dashboard">Dashboard</a>
            <a href="/reader" class="active">Live Feed</a>
            <a href="/feeds">HTML View</a>
            <a href="/feeds/all.xml">RSS Feed</a>
        </nav>
    </div>

    <div class="controls">
        <div class="control-group">
            <label>Court:</label>
            <select id="courtFilter">
                <option value="">All Courts</option>
            </select>
        </div>
        <div class="control-group">
            <label>Type:</label>
            <select id="typeFilter">
                <option value="">All Types</option>
                <option value="cv">Civil</option>
                <option value="cr">Criminal</option>
                <option value="bk">Bankruptcy</option>
                <option value="mc">Misc</option>
            </select>
        </div>
        <div class="control-group">
            <label>Search:</label>
            <input type="text" id="searchInput" placeholder="Case name, number...">
        </div>
        <div class="control-group">
            <input type="checkbox" id="newOnly">
            <label for="newOnly">New cases only</label>
        </div>
        <button class="btn btn-primary" onclick="loadItems()">Apply Filters</button>
        <button class="btn btn-secondary" id="pauseBtn" onclick="togglePause()">Pause</button>

        <div class="status">
            <span class="status-dot" id="statusDot"></span>
            <span id="statusText">Auto-refreshing every 30s</span>
        </div>
    </div>

    <div class="feed-container" id="feedContainer">
        <div class="loading">Loading filings...</div>
    </div>

    <script>
        let paused = false;
        let refreshInterval;
        const courts = ['nysd','cacd','flsd','txsd','ilnd','paed','njd','mad','gasd','wawd','azd','cod','dcd','mied','ohnd','ncmd','nced','vaed','mowd','laed'];

        // Populate court filter
        const courtSelect = document.getElementById('courtFilter');
        courts.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c;
            opt.textContent = c.toUpperCase();
            courtSelect.appendChild(opt);
        });

        function buildUrl() {
            const court = document.getElementById('courtFilter').value;
            const type = document.getElementById('typeFilter').value;
            const search = document.getElementById('searchInput').value;
            const newOnly = document.getElementById('newOnly').checked;

            let url = '/v1/rss/items?limit=50';
            if (court) url += `&court_code=${court}`;
            if (type) url += `&case_type=${type}`;
            if (newOnly) url += '&new=1';
            return { apiUrl: url, search };
        }

        async function loadItems() {
            const container = document.getElementById('feedContainer');
            const { apiUrl, search } = buildUrl();

            try {
                const res = await fetch(apiUrl);
                const data = await res.json();
                let items = data.items || [];

                // Client-side search filter
                if (search) {
                    const q = search.toLowerCase();
                    items = items.filter(it =>
                        (it.title || '').toLowerCase().includes(q) ||
                        (it.case_number || '').toLowerCase().includes(q) ||
                        (it.summary || '').toLowerCase().includes(q)
                    );
                }

                if (items.length === 0) {
                    container.innerHTML = '<div class="empty"><h3>No filings found</h3><p>Try adjusting your filters</p></div>';
                    return;
                }

                container.innerHTML = items.map(item => {
                    const meta = item.metadata_json ? JSON.parse(item.metadata_json) : {};
                    const isNew = meta.is_new_case;
                    const eventType = meta.event_type ? meta.event_type.replace(/_/g, ' ') : '';

                    // Extract clean caption
                    let caption = item.title || '';
                    caption = caption.replace(/^\\d+:\\d{2}-(cv|cr|bk|ap|mc|md)-\\d+(-\\d+)?\\s*/i, '');

                    return `
                        <div class="feed-item ${isNew ? 'new-case' : ''}">
                            <div class="item-header">
                                <span class="case-number">${item.case_number || 'N/A'}</span>
                                <span class="badge badge-court">${(item.court_code || '').toUpperCase()}</span>
                                ${item.case_type ? `<span class="badge badge-${item.case_type}">${item.case_type.toUpperCase()}</span>` : ''}
                                ${isNew ? '<span class="badge badge-new">NEW CASE</span>' : ''}
                                ${eventType ? `<span class="badge badge-event">${eventType}</span>` : ''}
                            </div>
                            <div class="item-title">${caption}</div>
                            <div class="item-summary">${(item.summary || '').replace(/<[^>]*>/g, '').substring(0, 200)}</div>
                            <div class="item-meta">
                                <span>${item.published || ''}</span>
                                <a href="${item.link}" target="_blank">View on PACER →</a>
                                ${item.case_number ? `<a href="/v1/cases/${item.court_code}/${item.case_number}">Case Details →</a>` : ''}
                            </div>
                        </div>
                    `;
                }).join('');

            } catch (err) {
                container.innerHTML = '<div class="empty"><h3>Error loading filings</h3><p>' + err.message + '</p></div>';
            }
        }

        function togglePause() {
            paused = !paused;
            const btn = document.getElementById('pauseBtn');
            const dot = document.getElementById('statusDot');
            const text = document.getElementById('statusText');

            if (paused) {
                clearInterval(refreshInterval);
                btn.textContent = 'Resume';
                btn.classList.remove('btn-secondary');
                btn.classList.add('btn-success');
                dot.classList.add('paused');
                text.textContent = 'Paused';
            } else {
                startRefresh();
                btn.textContent = 'Pause';
                btn.classList.remove('btn-success');
                btn.classList.add('btn-secondary');
                dot.classList.remove('paused');
                text.textContent = 'Auto-refreshing every 30s';
            }
        }

        function startRefresh() {
            refreshInterval = setInterval(loadItems, 30000);
        }

        // Initial load
        loadItems();
        startRefresh();

        // Enter key triggers search
        document.getElementById('searchInput').addEventListener('keypress', e => {
            if (e.key === 'Enter') loadItems();
        });
    </script>
</body>
</html>
"""
