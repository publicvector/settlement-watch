#!/usr/bin/env python3
"""
Comprehensive Case Analysis CLI Tool

Analyze cases across all dimensions: cause, court, defendant, and source.
Provides value estimates and intelligence reports.

Usage:
    python analyze_case.py score "securities fraud" "S.D. New York" "Goldman Sachs" "SEC"
    python analyze_case.py courts           # Court performance report
    python analyze_case.py defendants       # Repeat payer report
    python analyze_case.py sources          # Source analysis
    python analyze_case.py causes           # Cause of action benchmarks
    python analyze_case.py full-report      # All reports
    python analyze_case.py export           # Export to JSON
"""
import argparse
import sys
from analytics.case_valuation import CaseValuator
from analytics.case_analytics import CaseAnalytics


def score_case(args):
    """Score a specific case."""
    analytics = CaseAnalytics()

    scorecard = analytics.score_case(
        cause_of_action=args.cause,
        court=args.court or "",
        defendant=args.defendant or "",
        source=args.source or ""
    )

    print(scorecard.summary())

    # Also show valuation benchmarks
    valuator = CaseValuator()
    result = valuator.valuate(
        args.cause,
        jurisdiction=args.jurisdiction,
        defendant_type=args.defendant_type,
        class_size_category=args.class_size
    )

    if result:
        print("\n" + "=" * 66)
        print("HISTORICAL BENCHMARKS FOR", result.cause_of_action.upper())
        print("=" * 66)
        print(f"Sample Size: {result.sample_size} comparable cases")
        print(f"Confidence: {result.confidence_score:.0%}")
        print(f"\nHistorical Range:")
        print(f"  P25 (Conservative): {result.format_currency(result.p25)}")
        print(f"  Median:             {result.format_currency(result.median)}")
        print(f"  P75 (Aggressive):   {result.format_currency(result.p75)}")
        print(f"  Mean:               {result.format_currency(result.mean)}")


def show_courts(args):
    """Show court analysis."""
    analytics = CaseAnalytics()
    print(analytics.generate_court_report())


def show_defendants(args):
    """Show defendant analysis."""
    analytics = CaseAnalytics()
    print(analytics.generate_defendant_report())


def show_sources(args):
    """Show source analysis."""
    analytics = CaseAnalytics()
    print(analytics.generate_source_report())


def show_causes(args):
    """Show cause of action benchmarks."""
    valuator = CaseValuator()
    print(valuator.generate_benchmark_report())


def full_report(args):
    """Generate all reports."""
    analytics = CaseAnalytics()
    valuator = CaseValuator()

    print("\n" + "=" * 80)
    print("SETTLEMENT WATCH - FULL ANALYTICS REPORT")
    print("=" * 80)

    print("\n\n")
    print(valuator.generate_benchmark_report())

    print("\n\n")
    print(analytics.generate_court_report())

    print("\n\n")
    print(analytics.generate_defendant_report())

    print("\n\n")
    print(analytics.generate_source_report())


def export_data(args):
    """Export all analytics to JSON."""
    analytics = CaseAnalytics()
    valuator = CaseValuator()

    path1 = analytics.export_analytics_json()
    path2 = valuator.export_benchmarks_json()

    print(f"Exported analytics to: {path1}")
    print(f"Exported benchmarks to: {path2}")


def interactive_mode():
    """Interactive case scoring."""
    print("\n" + "=" * 60)
    print("INTERACTIVE CASE ANALYSIS")
    print("=" * 60)

    cause = input("\nCause of Action (e.g., 'securities fraud'): ").strip()
    if not cause:
        print("Cause of action is required.")
        return

    court = input("Court (e.g., 'S.D. New York') [optional]: ").strip()
    defendant = input("Defendant (e.g., 'Goldman Sachs') [optional]: ").strip()
    source = input("Source (e.g., 'SEC', 'Class Action') [optional]: ").strip()

    analytics = CaseAnalytics()
    scorecard = analytics.score_case(cause, court, defendant, source)
    print(scorecard.summary())


def main():
    parser = argparse.ArgumentParser(
        description='Comprehensive case analysis and valuation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s score "product liability" "N.D. California" "Apple"
  %(prog)s score "data breach" --jurisdiction california --defendant-type fortune_500
  %(prog)s courts
  %(prog)s defendants
  %(prog)s full-report
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # Score subcommand
    score_parser = subparsers.add_parser('score', help='Score a specific case')
    score_parser.add_argument('cause', help='Cause of action')
    score_parser.add_argument('court', nargs='?', help='Court/venue')
    score_parser.add_argument('defendant', nargs='?', help='Defendant name')
    score_parser.add_argument('source', nargs='?', help='Source type')
    score_parser.add_argument('--jurisdiction', '-j', help='Jurisdiction for multiplier')
    score_parser.add_argument('--defendant-type', '-d', help='Defendant type for multiplier')
    score_parser.add_argument('--class-size', '-c', help='Class size category')
    score_parser.set_defaults(func=score_case)

    # Courts subcommand
    courts_parser = subparsers.add_parser('courts', help='Court performance analysis')
    courts_parser.set_defaults(func=show_courts)

    # Defendants subcommand
    defendants_parser = subparsers.add_parser('defendants', help='Defendant payment analysis')
    defendants_parser.set_defaults(func=show_defendants)

    # Sources subcommand
    sources_parser = subparsers.add_parser('sources', help='Source type analysis')
    sources_parser.set_defaults(func=show_sources)

    # Causes subcommand
    causes_parser = subparsers.add_parser('causes', help='Cause of action benchmarks')
    causes_parser.set_defaults(func=show_causes)

    # Full report subcommand
    full_parser = subparsers.add_parser('full-report', help='Generate all reports')
    full_parser.set_defaults(func=full_report)

    # Export subcommand
    export_parser = subparsers.add_parser('export', help='Export analytics to JSON')
    export_parser.set_defaults(func=export_data)

    # Interactive subcommand
    interactive_parser = subparsers.add_parser('interactive', help='Interactive mode')
    interactive_parser.set_defaults(func=lambda x: interactive_mode())

    args = parser.parse_args()

    if args.command:
        args.func(args)
    else:
        # Default: show help
        parser.print_help()


if __name__ == "__main__":
    main()
