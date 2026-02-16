# Case Valuation Model

## Overview

The Settlement Watch Case Valuation Model estimates potential settlement values based on historical data from 700+ case outcomes totaling over $750 billion in settlements and verdicts.

## Formula

```
Estimated Value = Base Value × Jurisdiction Multiplier × Defendant Multiplier × Class Size Multiplier
```

Where **Base Value** is derived from historical percentiles for the cause of action.

## Benchmark Data (by Cause of Action)

| Cause of Action | Cases | Median | P25 (Low) | P75 (High) | Confidence |
|-----------------|-------|--------|-----------|------------|------------|
| Pharmaceutical | 10 | $1.5B | $270M | $5.0B | Medium |
| Environmental | 16 | $1.2B | $79M | $10.0B | Medium |
| Wrongful Death | 6 | $640M | $100M | $738M | Low |
| Antitrust | 37 | $275M | $84M | $630M | High |
| Sexual Abuse | 9 | $230M | $31M | $500M | Medium |
| Premises Liability | 8 | $159M | $81M | $330M | Medium |
| Healthcare Fraud | 11 | $150M | $60M | $949M | Medium |
| Product Liability | 48 | $120M | $50M | $651M | High |
| Insurance Bad Faith | 8 | $114M | $40M | $145M | Medium |
| BIPA (Biometric) | 12 | $100M | $50M | $650M | Medium |
| Motor Vehicle | 11 | $100M | $83M | $159M | Medium |
| Civil Rights | 26 | $99M | $20M | $160M | High |
| Securities | 91 | $80M | $20M | $200M | High |
| Employment | 26 | $80M | $12M | $197M | High |
| TCPA | 34 | $33M | $24M | $47M | High |
| Data Breach | 42 | $14M | $6M | $60M | High |
| Consumer Protection | 26 | $13M | $3M | $23M | High |

## Multipliers

### Jurisdiction Multipliers

| Jurisdiction | Multiplier |
|--------------|------------|
| California | 1.25x |
| New York | 1.20x |
| 9th Circuit | 1.20x |
| 2nd Circuit | 1.15x |
| New Jersey | 1.10x |
| Texas | 1.10x |
| Illinois | 1.05x |
| Federal (other) | 1.05x |
| State (default) | 0.95x |

### Defendant Type Multipliers

| Defendant Type | Multiplier |
|----------------|------------|
| Fortune 100 | 1.50x |
| Fortune 500 | 1.25x |
| Large Corporation | 1.10x |
| Mid-Size Company | 1.00x |
| Government | 0.90x |
| Small Company | 0.75x |
| Individual | 0.50x |

### Class Size Multipliers

| Class Size | Members | Multiplier |
|------------|---------|------------|
| Mega | > 1M | 1.30x |
| Large | 100K - 1M | 1.15x |
| Medium | 10K - 100K | 1.00x |
| Small | 1K - 10K | 0.85x |
| Individual | 1 | 0.60x |

## Confidence Scores

- **High (≥80%)**: 20+ cases, reliable estimate
- **Medium (60-79%)**: 10-19 cases, reasonable estimate
- **Low (<60%)**: <10 cases, directional only

## Usage Examples

### CLI

```bash
# Simple valuation
python valuate_case.py "data breach"

# With multipliers
python valuate_case.py "securities fraud" \
  --jurisdiction california \
  --defendant fortune_500 \
  --class-size large

# Compare causes
python valuate_case.py --compare "antitrust" "securities" "product liability"

# List all causes
python valuate_case.py --list

# Full benchmark report
python valuate_case.py --benchmark
```

### Python API

```python
from analytics.case_valuation import CaseValuator

valuator = CaseValuator()

# Basic valuation
result = valuator.valuate("data breach")
print(result.summary())

# With adjustments
result = valuator.valuate(
    "product liability",
    jurisdiction="california",
    defendant_type="fortune_500",
    class_size_category="large"
)

print(f"Low estimate: ${result.low_estimate/1e6:.1f}M")
print(f"Mid estimate: ${result.mid_estimate/1e6:.1f}M")
print(f"High estimate: ${result.high_estimate/1e6:.1f}M")
```

### SQL Query

```sql
-- Get benchmark for a specific cause
SELECT * FROM v_valuation_summary
WHERE cause_of_action LIKE '%breach%';

-- Top 10 by median value
SELECT * FROM v_valuation_summary
LIMIT 10;
```

## Data Sources

Historical data compiled from:
- Federal court settlements (SEC, DOJ, FTC, CFPB, EPA, EEOC)
- State Attorney General settlements
- Class action settlements (securities, consumer, BIPA, data breach)
- Mass tort resolutions
- Jury verdicts (nuclear verdicts database)
- Settlement administrator case lists

## Limitations

1. **Historical data may not predict future outcomes** - Market conditions, legal precedents, and defendant resources vary
2. **Settlement amounts may not reflect case merit** - Many factors affect negotiated outcomes
3. **Data skews toward larger cases** - Smaller settlements often unreported
4. **Jurisdiction-specific variations** - Local factors not fully captured
5. **Sample size affects reliability** - Check confidence scores

## Updates

Benchmarks are recalculated when new settlements are added to the database. Run:

```bash
python -c "from analytics.case_valuation import CaseValuator; CaseValuator().export_benchmarks_json()"
```
