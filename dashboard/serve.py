#!/usr/bin/env python3
"""
Simple HTTP server for the Settlement Watch dashboard.

Usage:
    python dashboard/serve.py
    # Then open http://localhost:8080/app.html
"""
import http.server
import socketserver
import os
import sys

PORT = 8080

# Change to dashboard directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

Handler = http.server.SimpleHTTPRequestHandler

# Add CORS headers for local development
class CORSHandler(Handler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

print(f"""
╔══════════════════════════════════════════════════════════════╗
║           Settlement Watch Analytics Dashboard               ║
╠══════════════════════════════════════════════════════════════╣
║  Server running at: http://localhost:{PORT}                   ║
║                                                              ║
║  Pages:                                                      ║
║    - http://localhost:{PORT}/app.html   (Full Dashboard)      ║
║    - http://localhost:{PORT}/index.html (Static Dashboard)    ║
║                                                              ║
║  Press Ctrl+C to stop                                        ║
╚══════════════════════════════════════════════════════════════╝
""")

with socketserver.TCPServer(("", PORT), CORSHandler) as httpd:
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        sys.exit(0)
