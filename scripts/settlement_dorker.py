#!/usr/bin/env python3
"""
Settlement Document Dorker
Finds settlement documents via search engine queries using DuckDuckGo API.
"""
import asyncio
import re
import json
from datetime import datetime
from ddgs import DDGS
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    amount: Optional[str] = None
    is_pdf: bool = False
    query: str = ""


# Settlement administration company domains
SETTLEMENT_ADMIN_SITES = [
    'epiqglobal.com',           # Epiq - one of the largest
    'jndla.com',                # JND Legal Administration
    'angeiongroup.com',         # Angeion Group
    'kccllc.com',               # KCC Class Action Services
    'atticus.admin.com',        # Atticus Administration
    'gcgsettlements.com',       # Garden City Group
    'simpluris.com',            # Simpluris
    'rustconsulting.com',       # Rust Consulting
    'browngreer.com',           # BrownGreer
    'cptgroup.com',             # CPT Group
    'abdataclassaction.com',    # A.B. Data
    'hefflerclaims.com',        # Heffler Claims Group
    'gilardi.com',              # Gilardi & Co
    'settlementatty.com',       # Settlement attorneys
    'strategicclaims.net',      # Strategic Claims
    'noticeadministrator.com',  # Notice Administrator
]

# Class action aggregator sites
CLASS_ACTION_AGGREGATORS = [
    'topclassactions.com',
    'classaction.org',
    'classactionrebates.com',
    'consumer-action.org',
    'lawyersandsettlements.com',
    'settlementwatch.com',
    'openclassactions.com',
]

# State Attorney General domains (for press releases)
STATE_AG_DOMAINS = [
    'oag.ca.gov',           # California
    'ag.ny.gov',            # New York
    'texasattorneygeneral.gov',  # Texas
    'illinoisattorneygeneral.gov',  # Illinois
    'myfloridalegal.com',   # Florida
    'mass.gov/ago',         # Massachusetts
    'nj.gov/oag',           # New Jersey
    'ohioattorneygeneral.gov',  # Ohio
    'atg.wa.gov',           # Washington
    'doj.state.or.us',      # Oregon
]

# Pre-built dork queries organized by category
DORK_TEMPLATES = {
    # === TIER 1: Settlement Administration Sites (Best Source) ===
    'settlement_admin': [
        'site:epiqglobal.com "settlement" "claim" 2026',
        'site:epiqglobal.com "settlement" "claim" 2025',
        'site:jndla.com "settlement" "$"',
        'site:angeiongroup.com "settlement" "claim"',
        'site:kccllc.com "settlement" "$" million',
        'site:simpluris.com "settlement" "claim"',
        'site:gcgsettlements.com "settlement"',
        'site:rustconsulting.com "class action"',
        'site:gilardi.com "settlement"',
        'site:browngreer.com "settlement"',
        'site:strategicclaims.net "settlement"',
        # URL pattern searches
        'inurl:settlement inurl:claim "file" "$"',
        'inurl:classaction inurl:settlement "$" million',
    ],

    # === TIER 1: Class Action Aggregators ===
    'aggregators': [
        'site:topclassactions.com "settlement" 2026',
        'site:topclassactions.com "settlement" 2025',
        'site:topclassactions.com "open" "settlement"',
        'site:topclassactions.com "deadline"',
        'site:classaction.org "settlement" "$" million',
        'site:classaction.org "news" "settlement"',
        'site:classactionrebates.com "claim"',
        'site:lawyersandsettlements.com "settlement" 2026',
        'site:openclassactions.com "settlement"',
        'site:consumer-action.org "settlement"',
    ],

    # === TIER 2: Federal Government Sources ===
    'federal_gov': [
        # DOJ
        'site:justice.gov "settlement" "$" million 2026',
        'site:justice.gov "settlement" "$" million 2025',
        'site:justice.gov "agrees to pay" "$"',
        'site:justice.gov "consent decree" "$"',
        'site:justice.gov "False Claims Act" settlement',
        # FTC
        'site:ftc.gov "settlement" "$"',
        'site:ftc.gov "refund" "settlement"',
        'site:ftc.gov "order" "$" million',
        # SEC
        'site:sec.gov "settlement" "$" million',
        'site:sec.gov "agrees to pay" "$"',
        'site:sec.gov/litigation "settled"',
        # CFPB
        'site:consumerfinance.gov "settlement" "$"',
        'site:consumerfinance.gov "consent order" "$"',
        # EPA
        'site:epa.gov "settlement" "$" million',
        'site:epa.gov "consent decree" "$"',
        'site:epa.gov "penalty" "$" million',
        # HHS/OIG
        'site:hhs.gov "settlement" "$" million',
        'site:oig.hhs.gov "settlement" "$"',
        'site:oig.hhs.gov "agrees to pay"',
        # EEOC
        'site:eeoc.gov "settlement" "$"',
        'site:eeoc.gov "consent decree"',
        # OSHA
        'site:osha.gov "settlement" "$"',
        'site:osha.gov "penalty" "$"',
        # NLRB
        'site:nlrb.gov "settlement" "$"',
        # CFTC
        'site:cftc.gov "settlement" "$" million',
        'site:cftc.gov "order" "$" million',
    ],

    # === TIER 2: State Attorney General Press Releases ===
    'state_ag': [
        # Major states
        'site:oag.ca.gov "settlement" "$" million',
        'site:ag.ny.gov "settlement" "$" million',
        'site:texasattorneygeneral.gov "settlement"',
        'site:illinoisattorneygeneral.gov "settlement"',
        'site:myfloridalegal.com "settlement"',
        'site:mass.gov "attorney general" "settlement" "$"',
        'site:nj.gov "attorney general" "settlement"',
        # Multi-state
        '"multistate" "settlement" "$" million 2026',
        '"multistate" "settlement" "$" million 2025',
        '"attorneys general" "settlement" "$" million',
        '"state attorneys general" "$" million settlement',
    ],

    # === TIER 2: Legal News Sources ===
    'legal_news': [
        'site:reuters.com/legal "settlement" "$" million 2026',
        'site:reuters.com/legal "settlement" "$" million 2025',
        'site:law360.com "settlement" "$" million',
        'site:law.com "settlement" "$" million',
        'site:bloomberglaw.com "settlement" "$"',
        'site:lexology.com "settlement" "$" million',
        'site:jdsupra.com "settlement" "$" million',
        'site:natlawreview.com "settlement" "$"',
    ],

    # === TIER 3: MDL and Complex Litigation ===
    'mdl_complex': [
        '"MDL" "settlement" "$" million 2026',
        '"MDL" "settlement" "$" million 2025',
        '"multidistrict litigation" "settlement" "$"',
        '"mass tort" "settlement" "$" million',
        '"bellwether" "settlement" "$"',
        'site:jpml.uscourts.gov "settlement"',
        '"In re:" "settlement" "$" million',
        '"consolidated" "class action" "settlement"',
    ],

    # === TIER 3: Press Releases ===
    'press_releases': [
        'site:prnewswire.com "settlement" "$" million 2026',
        'site:prnewswire.com "class action" "settlement"',
        'site:businesswire.com "settlement" "$" million',
        'site:globenewswire.com "settlement" "$"',
        '"announces settlement" "$" million',
        '"reached settlement" "$" million',
        '"agreed to settle" "$" million',
    ],

    # === TIER 3: SEC Filings (10-K, 8-K disclosures) ===
    'sec_filings': [
        'site:sec.gov/cgi-bin "settlement" "10-K"',
        'site:sec.gov "8-K" "settlement" "$" million',
        '"litigation settlement" "10-K" "$" million',
        '"legal proceedings" "settlement" "$" million filetype:htm',
        '"accrued" "settlement" "$" million "Form 10"',
    ],

    # === Settlement Types ===
    'high_value': [
        '"settlement" "$" "billion" 2026',
        '"settlement" "$" "billion" 2025',
        '"settlement" "$500 million"',
        '"settlement" "$100 million"',
        '"agrees to pay" "$" "billion"',
        '"historic settlement" "$"',
        '"record settlement" "$" million',
    ],

    'class_action': [
        '"class action settlement" "$" "million" 2026',
        '"class action settlement" "$" "million" 2025',
        '"settlement fund" "class members"',
        '"settlement agreement" "class action" filetype:pdf',
        '"preliminary approval" "class action settlement" 2026',
        '"final approval" "class action settlement" 2026',
        '"file a claim" "settlement" "deadline"',
        '"claims deadline" "settlement" "$"',
        '"settlement administrator" "$" million',
    ],

    'data_breach': [
        '"data breach" "settlement" "$" "million" 2026',
        '"data breach" "settlement" "$" "million" 2025',
        '"security incident" "settlement" "$"',
        '"data breach settlement" "claim" "deadline"',
        '"cybersecurity" "settlement" "$"',
        '"personal information" "settlement" "$" million',
        '"CCPA" "settlement" "$"',
        '"GDPR" "settlement" "$"',
    ],

    'securities': [
        '"securities" "settlement" "$" "million" 2026',
        '"securities" "settlement" "$" "million" 2025',
        '"investor" "settlement" "$" "million"',
        '"shareholder" "settlement" "$" million',
        '"securities fraud" "settlement"',
        '"10b-5" "settlement" "$"',
        'PSLRA "settlement" "$" million',
    ],

    'antitrust': [
        '"antitrust" "settlement" "$" "million" 2026',
        '"antitrust" "settlement" "$" "million" 2025',
        '"price fixing" "settlement" "$"',
        '"monopoly" "settlement" "$" million',
        '"Sherman Act" "settlement"',
        '"anticompetitive" "settlement" "$"',
    ],

    'employment': [
        '"wage" "settlement" "$" "million" 2026',
        '"wage theft" "settlement" "$"',
        '"overtime" "settlement" "$" million',
        '"discrimination" "settlement" "$" million',
        '"wrongful termination" "settlement"',
        '"EEOC" "settlement" "$"',
        '"FLSA" "settlement" "$" million',
        '"sexual harassment" "settlement" "$"',
        '"Title VII" "settlement"',
    ],

    'pharma_healthcare': [
        '"pharmaceutical" "settlement" "$" million 2026',
        '"drug" "settlement" "$" million',
        '"healthcare" "fraud" "settlement"',
        '"Medicare" "fraud" "settlement" "$"',
        '"Medicaid" "settlement" "$"',
        '"False Claims Act" "healthcare" "$"',
        '"off-label" "settlement" "$"',
        '"kickback" "settlement" "$" million',
        '"opioid" "settlement" "$"',
    ],

    'environmental': [
        '"environmental" "settlement" "$" million 2026',
        '"pollution" "settlement" "$"',
        '"clean water" "settlement"',
        '"Superfund" "settlement" "$"',
        '"Clean Air Act" "settlement"',
        '"CERCLA" "settlement" "$"',
        '"contamination" "settlement" "$" million',
        '"toxic" "settlement" "$" million',
    ],

    'consumer_product': [
        '"product recall" "settlement" "$"',
        '"defective" "class action" "settlement"',
        '"consumer" "refund" "settlement"',
        '"false advertising" "settlement" "$"',
        '"CPSC" "settlement" "$"',
        '"product liability" "settlement" "$" million',
        '"design defect" "settlement"',
        '"manufacturing defect" "settlement"',
    ],

    'insurance': [
        '"insurance" "bad faith" "settlement" "$"',
        '"insurance" "class action" "settlement"',
        '"denied claims" "settlement" "$" million',
        '"insurance fraud" "settlement"',
        '"policy" "settlement" "$" million',
    ],

    'real_estate': [
        '"real estate" "settlement" "$" million',
        '"mortgage" "settlement" "$"',
        '"foreclosure" "settlement" "$" million',
        '"NAR" "settlement" "$"',
        '"broker" "commission" "settlement"',
        '"HOA" "settlement" "$"',
    ],

    'auto': [
        '"auto" "defect" "settlement" "$"',
        '"vehicle" "recall" "settlement"',
        '"emissions" "settlement" "$" million',
        '"airbag" "settlement" "$"',
        '"Takata" "settlement"',
        '"automotive" "class action" "$"',
    ],

    # === Document Types ===
    'recent_pdf': [
        '"settlement agreement" filetype:pdf 2026',
        '"settlement agreement" filetype:pdf 2025',
        '"consent decree" filetype:pdf 2026',
        '"stipulation of settlement" filetype:pdf',
        '"class action settlement" filetype:pdf 2026',
        '"final judgment" "settlement" filetype:pdf',
    ],

    'court_docs': [
        'site:courtlistener.com "settlement" "$" million',
        'site:pacermonitor.com "settlement"',
        'site:dockets.justia.com "settlement"',
        '"case no" "settlement" "$" million filetype:pdf',
        '"docket" "settlement agreement" "$"',
    ],
}


class SettlementDorker:
    """Search engine dorking for settlement documents using DuckDuckGo API."""

    def __init__(self):
        self.results: List[SearchResult] = []
        self.ddgs = DDGS()

    def _extract_amount(self, text: str) -> Optional[str]:
        """Extract settlement amount from text."""
        if not text:
            return None

        patterns = [
            r'\$[\d,]+(?:\.\d+)?\s*(?:billion|B)\b',
            r'\$[\d,]+(?:\.\d+)?\s*(?:million|M)\b',
            r'\$[\d,]+(?:\.\d+)?(?:\s*(?:billion|million|M|B))?',
            r'[\d,]+(?:\.\d+)?\s*(?:billion|million)\s*(?:dollars?)?',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(0)
        return None

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """Search DuckDuckGo using API."""
        results = []

        try:
            # Use text search
            ddg_results = self.ddgs.text(query, max_results=max_results)

            for r in ddg_results:
                url = r.get('href', '') or r.get('link', '')
                title = r.get('title', '')
                snippet = r.get('body', '') or r.get('snippet', '')

                if url:
                    amount = self._extract_amount(title + ' ' + snippet)
                    results.append(SearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        amount=amount,
                        is_pdf=url.lower().endswith('.pdf'),
                        query=query
                    ))

        except Exception as e:
            print(f"      Search error: {e}")

        return results

    def run_category(self, category: str, max_per_query: int = 5) -> List[SearchResult]:
        """Run all queries in a category."""
        if category not in DORK_TEMPLATES:
            print(f"Unknown category: {category}")
            return []

        queries = DORK_TEMPLATES[category]
        all_results = []

        for query in queries:
            print(f"    [{category}] {query[:50]}...")
            results = self.search(query, max_per_query)
            all_results.extend(results)
            # Small delay to avoid rate limiting
            import time
            time.sleep(1)

        return all_results

    def run_custom_query(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """Run a custom dork query."""
        print(f"    Custom: {query[:50]}...")
        return self.search(query, max_results)

    def run_all_categories(self, categories: List[str] = None, max_per_query: int = 3) -> List[SearchResult]:
        """Run queries across multiple categories."""
        categories = categories or list(DORK_TEMPLATES.keys())
        all_results = []

        for category in categories:
            print(f"\n  Category: {category}")
            results = self.run_category(category, max_per_query)
            all_results.extend(results)

        # Deduplicate
        seen = set()
        unique = []
        for r in all_results:
            if r.url not in seen:
                seen.add(r.url)
                unique.append(r)

        self.results = unique
        return unique

    def filter_pdfs(self) -> List[SearchResult]:
        """Get only PDF results."""
        return [r for r in self.results if r.is_pdf]

    def filter_with_amounts(self, min_amount: float = 0) -> List[SearchResult]:
        """Filter results that have settlement amounts."""
        results = []
        for r in self.results:
            if r.amount:
                # Parse and filter by minimum
                amount_str = r.amount.lower().replace(',', '').replace('$', '')
                try:
                    if 'billion' in amount_str or 'b' in amount_str:
                        num = float(re.search(r'[\d.]+', amount_str).group()) * 1e9
                    elif 'million' in amount_str or 'm' in amount_str:
                        num = float(re.search(r'[\d.]+', amount_str).group()) * 1e6
                    else:
                        num = float(re.search(r'[\d.]+', amount_str).group())

                    if num >= min_amount:
                        results.append(r)
                except:
                    results.append(r)  # Include if can't parse
        return results

    def to_json(self) -> str:
        """Export to JSON."""
        return json.dumps([asdict(r) for r in self.results], indent=2)

    def print_summary(self):
        """Print summary of results."""
        print(f"\n{'=' * 60}")
        print(f"TOTAL: {len(self.results)} results")
        print(f"PDFs: {len(self.filter_pdfs())}")
        print(f"With amounts: {len(self.filter_with_amounts())}")
        print(f"{'=' * 60}")

        # Group by query
        by_query = {}
        for r in self.results:
            by_query.setdefault(r.query[:40], []).append(r)

        for query, items in list(by_query.items())[:10]:
            print(f"\n[{query}...] - {len(items)} results")
            for r in items[:3]:
                print(f"  * {r.title[:55]}...")
                if r.amount:
                    print(f"    Amount: {r.amount}")
                print(f"    {'PDF' if r.is_pdf else 'Web'}: {r.url[:60]}...")


def main():
    """Advanced settlement dorking with expanded sources."""
    import argparse

    parser = argparse.ArgumentParser(description='Settlement Document Dorker')
    parser.add_argument('--quick', action='store_true', help='Quick scan (admin sites only)')
    parser.add_argument('--full', action='store_true', help='Full scan (all categories)')
    parser.add_argument('--category', type=str, help='Run specific category')
    parser.add_argument('--max-results', type=int, default=5, help='Max results per query')
    args = parser.parse_args()

    print("=" * 70)
    print("SETTLEMENT DOCUMENT DORKER - ADVANCED")
    print("=" * 70)

    dorker = SettlementDorker()

    if args.category:
        # Run single category
        print(f"\n[*] Running category: {args.category}")
        results = dorker.run_category(args.category, args.max_results)
        dorker.results = results

    elif args.quick:
        # Quick scan - just admin sites and aggregators
        print("\n[QUICK SCAN] Settlement admin sites and aggregators only...")
        results = dorker.run_all_categories(
            categories=['settlement_admin', 'aggregators'],
            max_per_query=args.max_results
        )

    elif args.full:
        # Full scan - all categories
        print("\n[FULL SCAN] Running all categories...")

        # Tier 1: Best sources
        print("\n" + "=" * 50)
        print("TIER 1: Settlement Admin & Aggregators")
        print("=" * 50)
        dorker.run_all_categories(
            categories=['settlement_admin', 'aggregators'],
            max_per_query=args.max_results
        )

        # Tier 2: Government sources
        print("\n" + "=" * 50)
        print("TIER 2: Federal & State Government")
        print("=" * 50)
        dorker.run_all_categories(
            categories=['federal_gov', 'state_ag'],
            max_per_query=args.max_results
        )

        # Tier 2: Legal news
        print("\n" + "=" * 50)
        print("TIER 2: Legal News Sources")
        print("=" * 50)
        dorker.run_all_categories(
            categories=['legal_news', 'press_releases'],
            max_per_query=args.max_results
        )

        # Tier 3: Settlement types
        print("\n" + "=" * 50)
        print("TIER 3: Settlement Categories")
        print("=" * 50)
        dorker.run_all_categories(
            categories=[
                'high_value', 'class_action', 'data_breach', 'securities',
                'antitrust', 'employment', 'pharma_healthcare', 'environmental',
                'consumer_product', 'insurance', 'real_estate', 'auto'
            ],
            max_per_query=3
        )

        # Tier 3: Complex litigation & docs
        print("\n" + "=" * 50)
        print("TIER 3: MDL, SEC Filings, Court Docs")
        print("=" * 50)
        dorker.run_all_categories(
            categories=['mdl_complex', 'sec_filings', 'court_docs', 'recent_pdf'],
            max_per_query=3
        )

    else:
        # Default: balanced scan
        print("\n[BALANCED SCAN] Primary sources...")

        print("\n[1/4] Settlement administration sites...")
        dorker.run_all_categories(
            categories=['settlement_admin', 'aggregators'],
            max_per_query=args.max_results
        )

        print("\n[2/4] Government enforcement...")
        dorker.run_all_categories(
            categories=['federal_gov', 'state_ag'],
            max_per_query=4
        )

        print("\n[3/4] Legal news & press releases...")
        dorker.run_all_categories(
            categories=['legal_news', 'press_releases'],
            max_per_query=4
        )

        print("\n[4/4] Settlement categories...")
        dorker.run_all_categories(
            categories=['class_action', 'data_breach', 'high_value', 'pharma_healthcare'],
            max_per_query=4
        )

    # Summary
    dorker.print_summary()

    # Categorize results by source
    print(f"\n{'=' * 70}")
    print("RESULTS BY SOURCE TYPE")
    print("=" * 70)

    gov_results = [r for r in dorker.results if any(
        domain in r.url for domain in ['.gov', 'justice.gov', 'ftc.gov', 'sec.gov', 'epa.gov']
    )]
    admin_results = [r for r in dorker.results if any(
        site in r.url for site in SETTLEMENT_ADMIN_SITES + CLASS_ACTION_AGGREGATORS
    )]
    news_results = [r for r in dorker.results if any(
        site in r.url for site in ['reuters.com', 'law360.com', 'bloomberg', 'prnewswire', 'businesswire']
    )]

    print(f"\n  Government sources: {len(gov_results)}")
    print(f"  Settlement admin/aggregators: {len(admin_results)}")
    print(f"  Legal news/press: {len(news_results)}")
    print(f"  Other: {len(dorker.results) - len(gov_results) - len(admin_results) - len(news_results)}")

    # Show high-value settlements
    print(f"\n{'=' * 70}")
    print("HIGH-VALUE SETTLEMENTS (>$10M)")
    print("=" * 70)
    high_value = dorker.filter_with_amounts(10_000_000)
    # Sort by amount (rough)
    def sort_key(r):
        amt = r.amount.lower().replace(',', '').replace('$', '')
        try:
            if 'billion' in amt:
                return float(re.search(r'[\d.]+', amt).group()) * 1e9
            elif 'million' in amt:
                return float(re.search(r'[\d.]+', amt).group()) * 1e6
            else:
                return float(re.search(r'[\d.]+', amt).group())
        except:
            return 0

    high_value.sort(key=sort_key, reverse=True)
    for r in high_value[:25]:
        print(f"\n  {r.title[:65]}")
        print(f"  Amount: {r.amount}")
        print(f"  URL: {r.url[:80]}")

    # Show government enforcement actions
    print(f"\n{'=' * 70}")
    print("GOVERNMENT ENFORCEMENT ACTIONS")
    print("=" * 70)
    for r in gov_results[:15]:
        print(f"\n  {r.title[:65]}")
        if r.amount:
            print(f"  Amount: {r.amount}")
        print(f"  URL: {r.url[:80]}")

    # Show PDFs (court documents)
    print(f"\n{'=' * 70}")
    print("PDF COURT DOCUMENTS")
    print("=" * 70)
    for r in dorker.filter_pdfs()[:10]:
        print(f"\n  PDF: {r.title[:60]}")
        if r.amount:
            print(f"  Amount: {r.amount}")
        print(f"  {r.url[:80]}")

    # Save results
    with open("/tmp/settlement_dork_results.json", "w") as f:
        f.write(dorker.to_json())
    print(f"\n\n{'=' * 70}")
    print(f"SAVED: {len(dorker.results)} results to /tmp/settlement_dork_results.json")
    print(f"WITH AMOUNTS: {len(dorker.filter_with_amounts())}")
    print(f"PDFs: {len(dorker.filter_pdfs())}")
    print("=" * 70)


if __name__ == "__main__":
    main()
