#!/usr/bin/env python3
"""
Settlement Watch Management CLI
Unified interface for managing scrapers, database, and feeds.
"""
import argparse
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))


def cmd_import(args):
    """Import data into database."""
    from db.importers import import_all, SettlementImporter, StateCaseImporter
    from db.database import get_db

    if args.source == 'all':
        import_all()
    elif args.source == 'settlements':
        db = get_db()
        importer = SettlementImporter(db)
        if args.file:
            if args.file.endswith('.xml'):
                count = importer.import_from_feed_xml(args.file)
            else:
                count = importer.import_from_json(args.file)
            print(f"Imported {count} settlements")
    elif args.source == 'state':
        if not args.state or not args.file:
            print("Error: --state and --file required for state import")
            return
        db = get_db()
        importer = StateCaseImporter(db)
        count = importer.import_from_json(args.file, args.state)
        print(f"Imported {count} {args.state} cases")


def cmd_generate(args):
    """Generate RSS feeds."""
    from feeds.rss_generator import RSSGenerator

    generator = RSSGenerator()

    if args.type == 'all':
        generator.save_all_feeds()
    elif args.type == 'settlements':
        generator.save_settlements_feeds()
    elif args.type == 'states':
        generator.save_state_feeds()
    elif args.type == 'federal':
        generator.save_federal_feeds()


def cmd_dork(args):
    """Run settlement dorker."""
    from scripts.settlement_dorker import SettlementDorker, DORK_TEMPLATES

    dorker = SettlementDorker()

    if args.category:
        if args.category not in DORK_TEMPLATES:
            print(f"Unknown category: {args.category}")
            print(f"Available: {', '.join(DORK_TEMPLATES.keys())}")
            return
        results = dorker.run_category(args.category, args.max_results)
    elif args.quick:
        results = dorker.run_all_categories(
            categories=['settlement_admin', 'aggregators'],
            max_per_query=args.max_results
        )
    else:
        # Default balanced scan
        dorker.run_all_categories(
            categories=['settlement_admin', 'aggregators', 'federal_gov', 'class_action'],
            max_per_query=args.max_results
        )

    dorker.print_summary()

    # Auto-import if requested
    if args.import_results:
        import json
        with open('/tmp/settlement_dork_results.json', 'w') as f:
            f.write(dorker.to_json())

        from db.importers import SettlementImporter
        from db.database import get_db
        importer = SettlementImporter(get_db())
        count = importer.import_from_json('/tmp/settlement_dork_results.json')
        print(f"\nImported {count} settlements into database")


def cmd_scrape(args):
    """Run state court scrapers."""
    import asyncio
    import importlib

    scrapers = {
        'alaska': 'scrapers.scraper_alaska',
        'wisconsin': 'scrapers.test_wisconsin',
        'oklahoma': 'scrapers.scraper_oklahoma',
        'ohio': 'scrapers.scraper_ohio_franklin',
        'pennsylvania': 'scrapers.scraper_pennsylvania',
        'delaware': 'scrapers.scraper_delaware',
        # Add more as needed
    }

    if args.state == 'list':
        print("Available scrapers:")
        for state in scrapers:
            print(f"  - {state}")
        return

    if args.state not in scrapers:
        print(f"Unknown state: {args.state}")
        print(f"Use --state list to see available scrapers")
        return

    try:
        module = importlib.import_module(scrapers[args.state])
        if hasattr(module, 'main'):
            if asyncio.iscoroutinefunction(module.main):
                asyncio.run(module.main())
            else:
                module.main()
        else:
            print(f"Scraper {args.state} has no main() function")
    except Exception as e:
        print(f"Error running scraper: {e}")


def cmd_stats(args):
    """Show database statistics."""
    from db.database import get_db

    db = get_db()
    stats = db.get_stats()

    print("=" * 50)
    print("SETTLEMENT WATCH DATABASE STATS")
    print("=" * 50)
    print(f"  Settlements:    {stats['settlements']}")
    print(f"  State Cases:    {stats['state_cases']}")
    print(f"  States:         {stats['states']}")
    print(f"  Federal Cases:  {stats['federal_cases']}")
    print("=" * 50)

    if args.verbose:
        # Show states breakdown
        states = db.get_states()
        if states:
            print("\nState Cases by State:")
            for state in states:
                cases = db.get_state_cases(state=state, limit=1000)
                print(f"  {state}: {len(cases)}")

        # Show settlement categories
        settlements = db.get_settlements(limit=1000)
        if settlements:
            categories = {}
            for s in settlements:
                cat = s.get('category', 'Other')
                categories[cat] = categories.get(cat, 0) + 1
            print("\nSettlements by Category:")
            for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
                print(f"  {cat}: {count}")


def cmd_serve(args):
    """Start the API server."""
    import uvicorn
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


def main():
    parser = argparse.ArgumentParser(
        description='Settlement Watch Management CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python manage.py import --source all
  python manage.py generate --type all
  python manage.py dork --quick --import
  python manage.py scrape --state alaska
  python manage.py stats -v
  python manage.py serve --port 8000
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Import command
    import_parser = subparsers.add_parser('import', help='Import data into database')
    import_parser.add_argument('--source', choices=['all', 'settlements', 'state', 'federal'],
                               default='all', help='Data source to import')
    import_parser.add_argument('--file', help='Input file path')
    import_parser.add_argument('--state', help='State code for state import')
    import_parser.set_defaults(func=cmd_import)

    # Generate command
    gen_parser = subparsers.add_parser('generate', help='Generate RSS feeds')
    gen_parser.add_argument('--type', choices=['all', 'settlements', 'states', 'federal'],
                            default='all', help='Feed type to generate')
    gen_parser.set_defaults(func=cmd_generate)

    # Dork command
    dork_parser = subparsers.add_parser('dork', help='Run settlement dorker')
    dork_parser.add_argument('--category', help='Specific category to search')
    dork_parser.add_argument('--quick', action='store_true', help='Quick scan (admin sites only)')
    dork_parser.add_argument('--max-results', type=int, default=5, help='Max results per query')
    dork_parser.add_argument('--import', dest='import_results', action='store_true',
                             help='Import results into database')
    dork_parser.set_defaults(func=cmd_dork)

    # Scrape command
    scrape_parser = subparsers.add_parser('scrape', help='Run state court scrapers')
    scrape_parser.add_argument('--state', required=True, help='State to scrape (or "list")')
    scrape_parser.set_defaults(func=cmd_scrape)

    # Stats command
    stats_parser = subparsers.add_parser('stats', help='Show database statistics')
    stats_parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    stats_parser.set_defaults(func=cmd_stats)

    # Serve command
    serve_parser = subparsers.add_parser('serve', help='Start API server')
    serve_parser.add_argument('--host', default='0.0.0.0', help='Host to bind')
    serve_parser.add_argument('--port', type=int, default=8000, help='Port to bind')
    serve_parser.add_argument('--reload', action='store_true', help='Auto-reload on changes')
    serve_parser.set_defaults(func=cmd_serve)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
