#!/usr/bin/env python3
"""
Vermont Judiciary Portal Scraper

This scraper accesses the Vermont Judiciary Public Portal.
URL: https://portal.vtcourts.gov/Portal

Features:
- No CAPTCHA required
- Anonymous access for Civil Division and Judicial Bureau cases
- Smart Search functionality
- Registration required for elevated access

Notes:
- Public users can only view Civil Division and Judicial Bureau cases
- Case parties and attorneys can sign up for elevated access

Usage:
    python scraper_vermont.py --name "Smith"
    python scraper_vermont.py --case-number "123-4-56"
"""

import asyncio
import argparse
import json
from playwright.async_api import async_playwright
from typing import List, Dict, Any, Optional

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False


class VermontCourtScraper:
    """Scraper for Vermont Judiciary Public Portal."""

    BASE_URL = "https://portal.vtcourts.gov/Portal"

    def __init__(self, headless: bool = True, timeout: int = 60000):
        self.headless = headless
        self.timeout = timeout
        self.browser = None
        self.context = None
        self.page = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        """Initialize browser and page."""
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='en-US',
        )
        self.page = await self.context.new_page()
        self.page.set_default_timeout(self.timeout)

        if HAS_STEALTH:
            stealth = Stealth()
            await stealth.apply_stealth_async(self.page)

    async def close(self):
        """Close browser."""
        if self.browser:
            await self.browser.close()
        if hasattr(self, '_playwright'):
            await self._playwright.stop()

    async def search(
        self,
        query: Optional[str] = None,
        party_name: Optional[str] = None,
        case_number: Optional[str] = None,
        attorney_name: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search Vermont court records.

        Note: Anonymous users can only view Civil Division and Judicial Bureau cases.

        Args:
            query: General search term (Smart Search)
            party_name: Party name
            case_number: Case number
            attorney_name: Attorney name
            limit: Maximum results

        Returns:
            List of case dictionaries
        """
        search_term = query or party_name or case_number or attorney_name or "Smith"

        try:
            print(f"[1] Loading Vermont Judiciary Portal...")
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(5000)  # JS-heavy site

            title = await self.page.title()
            print(f"    Page loaded: {title}")

            # Check for CAPTCHA
            content = await self.page.content()
            if 'captcha' in content.lower() or 'recaptcha' in content.lower():
                print("    WARNING: CAPTCHA detected!")
                return []

            await self.page.screenshot(path="/tmp/vermont_main.png")
            print("    Screenshot: /tmp/vermont_main.png")

            # Check page text
            page_text = await self.page.evaluate('() => document.body.innerText')
            print(f"\n    Page preview: {page_text[:400]}...")

            # Check if we need to enable JavaScript notice
            if 'javascript must be enabled' in page_text.lower():
                print("    WARNING: JavaScript requirement notice detected")

            # Look for Smart Search or search options
            print("[2] Looking for search options...")

            # Try to find Smart Search button/link
            smart_search = await self.page.query_selector(
                'button:has-text("Smart Search"), a:has-text("Smart Search"), '
                '[class*="smart-search"], [id*="smart-search"], '
                'button:has-text("Search"), a:has-text("Search Cases")'
            )

            if smart_search:
                print("    Found Smart Search - clicking...")
                await smart_search.click()
                await self.page.wait_for_timeout(3000)
                await self.page.screenshot(path="/tmp/vermont_search.png")

            # Analyze available elements
            elements = await self.page.evaluate('''() => {
                const info = { inputs: [], buttons: [], links: [], divs: [] };

                document.querySelectorAll('input').forEach(i => {
                    if (i.offsetParent !== null || i.type === 'hidden') {
                        info.inputs.push({
                            type: i.type,
                            name: i.name || i.id,
                            placeholder: i.placeholder,
                            visible: i.offsetParent !== null
                        });
                    }
                });

                document.querySelectorAll('button').forEach(b => {
                    if (b.offsetParent !== null) {
                        info.buttons.push(b.textContent?.trim().substring(0, 50));
                    }
                });

                document.querySelectorAll('a').forEach(a => {
                    const text = a.textContent?.trim() || '';
                    if (text.length > 2 && text.length < 50) {
                        info.links.push(text);
                    }
                });

                // Look for Angular/React components
                document.querySelectorAll('[ng-click], [click], [onclick]').forEach(el => {
                    info.divs.push(el.textContent?.trim().substring(0, 50));
                });

                return info;
            }''')

            print(f"    Inputs: {[(i['name'], i['type']) for i in elements['inputs'] if i['visible']]}")
            print(f"    Buttons: {elements['buttons'][:5]}")
            print(f"    Links: {elements['links'][:10]}")

            # Find search input
            search_input = await self.page.query_selector(
                'input[type="text"]:visible, input[type="search"]:visible, '
                'input[placeholder*="search" i], input[name*="search" i]'
            )

            if search_input:
                print(f"[3] Filling search: {search_term}...")
                await search_input.fill(search_term)
                await self.page.wait_for_timeout(500)
                await self.page.screenshot(path="/tmp/vermont_filled.png")

                # Submit search
                print("[4] Submitting search...")
                search_btn = await self.page.query_selector(
                    'button:has-text("Search"), button[type="submit"], '
                    'input[type="submit"], button.search-btn'
                )
                if search_btn:
                    await search_btn.click()
                else:
                    await self.page.keyboard.press('Enter')

                await self.page.wait_for_timeout(5000)
                await self.page.screenshot(path="/tmp/vermont_results.png")
                print("    Screenshot: /tmp/vermont_results.png")

                # Save HTML
                html = await self.page.content()
                with open("/tmp/vermont_results.html", "w") as f:
                    f.write(html)

                # Check results
                results_text = await self.page.evaluate('() => document.body.innerText')
                print(f"\n    Results preview: {results_text[:500]}...")

                # Extract results
                print("[5] Extracting results...")
                results = await self._extract_results()
                print(f"    Found {len(results)} results")
                return results[:limit]

            else:
                print("    No search input found - portal may require authentication")
                print("    Note: Anonymous access is limited to Civil/Judicial Bureau cases")

                # Save page for debugging
                html = await self.page.content()
                with open("/tmp/vermont_page.html", "w") as f:
                    f.write(html)

                return []

        except Exception as e:
            print(f"    Error: {e}")
            import traceback
            traceback.print_exc()
            await self.page.screenshot(path="/tmp/vermont_error.png")
            return []

    async def _extract_results(self) -> List[Dict[str, Any]]:
        """Extract case results from the page."""
        results = await self.page.evaluate('''() => {
            const items = [];

            // Try table rows
            document.querySelectorAll('table tr').forEach((row, idx) => {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 2) {
                    const rowText = row.innerText?.trim() || '';
                    if (rowText.length > 20 && !row.querySelector('th')) {
                        items.push({
                            case_number: cells[0]?.innerText?.trim() || '',
                            case_title: cells[1]?.innerText?.trim()?.substring(0, 150) || '',
                            court: cells[2]?.innerText?.trim() || '',
                            case_type: cells[3]?.innerText?.trim() || '',
                            status: cells[4]?.innerText?.trim() || '',
                            raw: rowText.substring(0, 300),
                            source: 'Vermont Judiciary'
                        });
                    }
                }
            });

            // Try card/div results
            if (items.length === 0) {
                document.querySelectorAll('.case-card, .search-result, .case-item, [class*="result"]').forEach(el => {
                    const text = el.innerText?.trim() || '';
                    const link = el.querySelector('a')?.href || '';
                    if (text.length > 20) {
                        // Try to parse structured content
                        const caseNum = el.querySelector('[class*="case-number"], .case-id')?.innerText?.trim();
                        const title = el.querySelector('[class*="title"], .caption')?.innerText?.trim();

                        items.push({
                            case_number: caseNum || '',
                            case_title: title || '',
                            link: link,
                            raw: text.substring(0, 300),
                            source: 'Vermont Judiciary'
                        });
                    }
                });
            }

            // Try any clickable case links
            if (items.length === 0) {
                document.querySelectorAll('a').forEach(a => {
                    const text = a.innerText?.trim() || '';
                    const href = a.href || '';
                    // Vermont case numbers often have format like "123-4-56"
                    if (text.match(/\\d{2,4}-\\d+-\\d+/) ||
                        href.includes('case') || href.includes('docket')) {
                        const parent = a.closest('tr, div, li');
                        const context = parent?.innerText?.trim() || text;
                        items.push({
                            case_number: text,
                            link: href,
                            raw: context.substring(0, 300),
                            source: 'Vermont Judiciary'
                        });
                    }
                });
            }

            return items;
        }''')

        # Deduplicate
        seen = set()
        unique = []
        for r in results:
            key = r.get('case_number', '') or r.get('raw', '')[:50]
            if key and key not in seen:
                seen.add(key)
                unique.append(r)

        return unique

    async def check_accessibility(self) -> Dict[str, Any]:
        """Check what features are available without login."""
        try:
            await self.page.goto(self.BASE_URL, timeout=self.timeout)
            await self.page.wait_for_timeout(5000)

            info = await self.page.evaluate('''() => {
                const result = {
                    hasSmartSearch: false,
                    hasPublicSearch: false,
                    requiresLogin: false,
                    availableFeatures: []
                };

                const text = document.body.innerText.toLowerCase();

                result.hasSmartSearch = text.includes('smart search');
                result.hasPublicSearch = text.includes('public') && text.includes('search');
                result.requiresLogin = text.includes('sign in') || text.includes('login required');

                // Find available features
                document.querySelectorAll('button, a').forEach(el => {
                    const t = el.textContent?.trim();
                    if (t && t.length > 2 && t.length < 30) {
                        result.availableFeatures.push(t);
                    }
                });

                return result;
            }''')

            return info

        except Exception as e:
            return {"error": str(e)}


async def main():
    parser = argparse.ArgumentParser(description='Vermont Judiciary Portal Scraper')
    parser.add_argument('--name', '-n', help='Party name to search')
    parser.add_argument('--case-number', '-c', help='Case number')
    parser.add_argument('--attorney', '-a', help='Attorney name')
    parser.add_argument('--query', '-q', help='General search query')
    parser.add_argument('--check', action='store_true',
                        help='Check portal accessibility')
    parser.add_argument('--limit', '-l', type=int, default=25,
                        help='Maximum results')
    parser.add_argument('--headless', action='store_true', default=True)
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    print("=" * 70)
    print("VERMONT JUDICIARY PORTAL SCRAPER")
    print("=" * 70)

    async with VermontCourtScraper(headless=args.headless) as scraper:
        if args.check:
            print("\nChecking portal accessibility...")
            info = await scraper.check_accessibility()
            print(f"\nAccessibility info:")
            for k, v in info.items():
                print(f"  {k}: {v}")
            return

        # Default search
        if not args.name and not args.case_number and not args.query:
            args.name = "Smith"

        print(f"Search parameters:")
        print(f"  Party name: {args.name or 'N/A'}")
        print(f"  Case number: {args.case_number or 'N/A'}")
        print(f"  Attorney: {args.attorney or 'N/A'}")
        print(f"  Query: {args.query or 'N/A'}")
        print("=" * 70)
        print("\nNote: Anonymous access limited to Civil Division & Judicial Bureau cases")

        results = await scraper.search(
            query=args.query,
            party_name=args.name,
            case_number=args.case_number,
            attorney_name=args.attorney,
            limit=args.limit
        )

        if results:
            print(f"\nFound {len(results)} results:")
            for i, r in enumerate(results[:15], 1):
                print(f"\n[{i}]")
                for k, v in r.items():
                    if v and k != 'raw':
                        print(f"    {k}: {str(v)[:60]}")

            if args.output:
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                print(f"\nResults saved to {args.output}")
        else:
            print("\nNo results found.")
            print("Note: Vermont portal requires JavaScript and may need registration")
            print("Check /tmp/vermont_*.png for screenshots.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
