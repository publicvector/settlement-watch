#!/usr/bin/env python3
"""
Case Valuation CLI Tool

Estimate potential settlement value based on cause of action and case factors.

Usage:
    python valuate_case.py "product liability"
    python valuate_case.py "data breach" --jurisdiction california --defendant fortune_500
    python valuate_case.py --list                    # List all causes
    python valuate_case.py --benchmark               # Show benchmark report
"""
import argparse
from analytics.case_valuation import CaseValuator


def main():
    parser = argparse.ArgumentParser(
        description='Estimate case settlement value based on cause of action'
    )
    parser.add_argument('cause', nargs='?', help='Cause of action (e.g., "product liability")')
    parser.add_argument('--jurisdiction', '-j', help='Jurisdiction (e.g., california, "9th circuit")')
    parser.add_argument('--defendant', '-d', help='Defendant type (fortune_100, fortune_500, large_corporation, etc.)')
    parser.add_argument('--class-size', '-c', help='Class size (mega, large, medium, small, individual)')
    parser.add_argument('--multiplier', '-m', type=float, default=1.0, help='Custom multiplier')
    parser.add_argument('--list', '-l', action='store_true', help='List available causes')
    parser.add_argument('--benchmark', '-b', action='store_true', help='Show benchmark report')
    parser.add_argument('--export-json', action='store_true', help='Export benchmarks to JSON')
    parser.add_argument('--compare', nargs='+', help='Compare multiple causes')

    args = parser.parse_args()

    valuator = CaseValuator()

    if args.list:
        print("\nAvailable Causes of Action (by median value):")
        print("-" * 60)
        for cause, n, median in valuator.get_available_causes():
            print(f"  {cause:<35} {n:>3} cases  ${median/1e6:>8.1f}M median")
        print()
        return

    if args.benchmark:
        print(valuator.generate_benchmark_report())
        return

    if args.export_json:
        path = valuator.export_benchmarks_json()
        print(f"Exported benchmarks to: {path}")
        return

    if args.compare:
        results = valuator.compare_causes(args.compare)
        print("\nComparison of Causes:")
        print("=" * 80)
        for r in results:
            print(f"\n{r.cause_of_action}")
            print(f"  Median: {r.format_currency(r.median):<12} "
                  f"Range: {r.format_currency(r.p25)} - {r.format_currency(r.p75)}")
            print(f"  Sample: {r.sample_size} cases, Confidence: {r.confidence_score:.0%}")
        return

    if not args.cause:
        parser.print_help()
        return

    result = valuator.valuate(
        args.cause,
        jurisdiction=args.jurisdiction,
        defendant_type=args.defendant,
        class_size_category=args.class_size,
        custom_multiplier=args.multiplier,
    )

    if result:
        print(result.summary())
        if len(result.multipliers) > 1:
            print("Multipliers Applied:")
            total = 1.0
            for name, mult in result.multipliers.items():
                if mult != 1.0:
                    print(f"  {name}: {mult:.2f}x")
                    total *= mult
            if total != 1.0:
                print(f"  Combined: {total:.2f}x")
    else:
        print(f"No data found for: {args.cause}")
        print("\nDid you mean one of these?")
        causes = valuator.get_available_causes()
        search_lower = args.cause.lower()
        matches = [c for c, _, _ in causes if search_lower in c.lower()][:5]
        for cause in matches:
            print(f"  - {cause}")
        if not matches:
            print("  (try --list to see all available causes)")


if __name__ == "__main__":
    main()
