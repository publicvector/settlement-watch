"""Predictive Analytics Module - Objective data-driven predictions"""
import math
from typing import Dict, List, Optional, Any
from .models.db import get_conn

# NOS Code descriptions
NOS_DESCRIPTIONS = {
    "110": "Insurance",
    "120": "Marine",
    "130": "Miller Act",
    "140": "Negotiable Instrument",
    "150": "Recovery of Overpayment",
    "151": "Medicare Act",
    "152": "Recovery of Student Loans",
    "153": "Recovery of Veteran Benefits",
    "160": "Stockholders Suits",
    "190": "Contract Other",
    "195": "Contract Product Liability",
    "196": "Franchise",
    "210": "Land Condemnation",
    "220": "Foreclosure",
    "230": "Rent Lease Ejectment",
    "240": "Torts to Land",
    "245": "Tort Product Liability",
    "290": "Real Property Other",
    "310": "Airplane",
    "315": "Airplane Product Liability",
    "320": "Assault Libel Slander",
    "330": "Federal Employers Liability",
    "340": "Marine",
    "345": "Marine Product Liability",
    "350": "Motor Vehicle",
    "355": "Motor Vehicle Product Liability",
    "360": "Personal Injury Other",
    "362": "Medical Malpractice",
    "365": "Personal Injury Product Liability",
    "367": "Health Care/Pharmaceutical PI",
    "368": "Asbestos Personal Injury",
    "370": "Fraud",
    "371": "Truth in Lending",
    "380": "Other Personal Property Damage",
    "385": "Property Damage Product Liability",
    "400": "State Reapportionment",
    "410": "Antitrust",
    "422": "Bankruptcy Appeal Rule 28 USC 158",
    "423": "Withdrawal 28 USC 157",
    "430": "Banks and Banking",
    "440": "Civil Rights Other",
    "441": "Civil Rights Voting",
    "442": "Civil Rights Employment",
    "443": "Civil Rights Housing",
    "444": "Civil Rights Welfare",
    "445": "Civil Rights ADA Employment",
    "446": "Civil Rights ADA Other",
    "448": "Education",
    "450": "Interstate Commerce",
    "460": "Deportation",
    "462": "Naturalization Application",
    "463": "Habeas Corpus Alien Detainee",
    "465": "Other Immigration Actions",
    "470": "RICO",
    "480": "Consumer Credit",
    "490": "Cable/Satellite TV",
    "510": "Motions to Vacate Sentence",
    "530": "Habeas Corpus General",
    "535": "Habeas Corpus Death Penalty",
    "540": "Mandamus Other",
    "550": "Civil Rights Prisoner",
    "555": "Prison Condition",
    "560": "Civil Detainee",
    "610": "Agriculture",
    "620": "Food Drug",
    "625": "Drug Related Seizure",
    "630": "Liquor Laws",
    "640": "RR and Truck",
    "650": "Airline Regulations",
    "660": "Occupational Safety/Health",
    "690": "Other",
    "710": "Fair Labor Standards Act",
    "720": "Labor/Management Relations",
    "730": "Labor/Management Reporting",
    "740": "Railway Labor Act",
    "751": "Family and Medical Leave Act",
    "790": "Other Labor Litigation",
    "791": "ERISA",
    "810": "Selective Service",
    "820": "Copyright",
    "830": "Patent",
    "835": "Patent Abbreviated New Drug",
    "840": "Trademark",
    "850": "Securities Exchange",
    "860": "Social Security HIA",
    "861": "Social Security Black Lung",
    "862": "Social Security DIWC",
    "863": "Social Security DIWW",
    "864": "Social Security SSID",
    "865": "Social Security RSI",
    "870": "Taxes US Plaintiff or Defendant",
    "871": "IRS Third Party 26 USC 7609",
    "875": "Customer Challenge 12 USC 3410",
    "890": "Other Statutory Actions",
    "891": "Agricultural Acts",
    "892": "Economic Stabilization Act",
    "893": "Environmental Matters",
    "894": "Energy Allocation Act",
    "895": "Freedom of Information Act",
    "896": "Arbitration",
    "899": "Admin Procedure Act/Review",
    "900": "Appeal of Fee - Equal Access",
    "910": "Domestic Relations",
    "920": "Insanity",
    "930": "Probate",
    "940": "Substitute Trustee",
    "950": "Constitutionality of State Statutes",
    "990": "Other",
    "992": "Local Jurisdictional Appeal",
    "999": "Unclassified"
}


def get_nos_description(code: str) -> str:
    """Get human-readable NOS description"""
    return NOS_DESCRIPTIONS.get(str(code), f"Code {code}")


def get_case_outcome_by_nos(nos_code: str = None, court_code: str = None) -> Dict[str, Any]:
    """
    Get case outcome distribution for a Nature of Suit category.
    Returns objective outcome percentages based on FJC historical data.
    """
    conn = get_conn()

    conditions = ["disp_outcome IS NOT NULL"]
    params = []

    if nos_code:
        conditions.append("nature_of_suit = ?")
        params.append(nos_code)
    if court_code:
        conditions.append("court_code = ?")
        params.append(court_code.lower())

    where_clause = " AND ".join(conditions)

    # Get outcome distribution
    cur = conn.execute(f"""
        SELECT
            disp_outcome,
            COUNT(*) as count
        FROM fjc_outcomes
        WHERE {where_clause}
        GROUP BY disp_outcome
        ORDER BY count DESC
    """, tuple(params))

    outcomes = {}
    total = 0
    for row in cur.fetchall():
        outcome = row["disp_outcome"]
        count = row["count"]
        outcomes[outcome] = count
        total += count

    # Calculate percentages
    distribution = []
    for outcome, count in outcomes.items():
        pct = round(count / total * 100, 1) if total > 0 else 0
        distribution.append({
            "outcome": outcome,
            "count": count,
            "percentage": pct
        })

    # Get judgment_for breakdown
    cur = conn.execute(f"""
        SELECT
            judgment_for,
            COUNT(*) as count
        FROM fjc_outcomes
        WHERE {where_clause} AND judgment_for IS NOT NULL AND length(judgment_for) > 0
        GROUP BY judgment_for
        ORDER BY count DESC
    """, tuple(params))

    judgment_for = {}
    jf_total = 0
    for row in cur.fetchall():
        jf = row["judgment_for"]
        count = row["count"]
        judgment_for[jf] = count
        jf_total += count

    judgment_breakdown = []
    for jf, count in judgment_for.items():
        pct = round(count / jf_total * 100, 1) if jf_total > 0 else 0
        judgment_breakdown.append({
            "judgment_for": jf,
            "count": count,
            "percentage": pct
        })

    return {
        "nos_code": nos_code,
        "nos_description": get_nos_description(nos_code) if nos_code else "All Categories",
        "court_code": court_code,
        "total_cases": total,
        "outcome_distribution": distribution,
        "judgment_breakdown": judgment_breakdown,
        "confidence": "high" if total >= 1000 else "medium" if total >= 100 else "low",
        "sample_size_note": f"Based on {total:,} historical cases"
    }


def get_case_outcome_by_court(court_code: str) -> Dict[str, Any]:
    """Get case outcome distribution for a specific court."""
    conn = get_conn()

    # Get outcome distribution
    cur = conn.execute("""
        SELECT
            disp_outcome,
            COUNT(*) as count
        FROM fjc_outcomes
        WHERE court_code = ? AND disp_outcome IS NOT NULL
        GROUP BY disp_outcome
        ORDER BY count DESC
    """, (court_code.lower(),))

    outcomes = {}
    total = 0
    for row in cur.fetchall():
        outcomes[row["disp_outcome"]] = row["count"]
        total += row["count"]

    distribution = []
    for outcome, count in outcomes.items():
        pct = round(count / total * 100, 1) if total > 0 else 0
        distribution.append({
            "outcome": outcome,
            "count": count,
            "percentage": pct
        })

    # Get national average for comparison
    cur = conn.execute("""
        SELECT disp_outcome, COUNT(*) as count
        FROM fjc_outcomes
        WHERE disp_outcome IS NOT NULL
        GROUP BY disp_outcome
    """)

    national = {}
    national_total = 0
    for row in cur.fetchall():
        national[row["disp_outcome"]] = row["count"]
        national_total += row["count"]

    national_pcts = {}
    for outcome, count in national.items():
        national_pcts[outcome] = round(count / national_total * 100, 1) if national_total > 0 else 0

    # Add comparison to national average
    for item in distribution:
        nat_pct = national_pcts.get(item["outcome"], 0)
        item["national_avg"] = nat_pct
        item["vs_national"] = round(item["percentage"] - nat_pct, 1)

    return {
        "court_code": court_code.upper(),
        "total_cases": total,
        "outcome_distribution": distribution,
        "confidence": "high" if total >= 1000 else "medium" if total >= 100 else "low",
        "sample_size_note": f"Based on {total:,} cases in {court_code.upper()}"
    }


def get_motion_outcome_stats(motion_type: str = None, court_code: str = None) -> Dict[str, Any]:
    """
    Get motion outcome statistics with granular breakdown.
    Preserves exact categories: granted, denied, partial (granted in part/denied in part).
    """
    conn = get_conn()

    conditions = ["outcome IS NOT NULL"]
    params = []

    if motion_type:
        conditions.append("motion_type = ?")
        params.append(motion_type.lower())
    if court_code:
        conditions.append("court_id = ?")
        params.append(court_code.lower())

    where_clause = " AND ".join(conditions)

    cur = conn.execute(f"""
        SELECT
            motion_type,
            outcome,
            COUNT(*) as count
        FROM motion_outcomes
        WHERE {where_clause}
        GROUP BY motion_type, outcome
        ORDER BY motion_type, count DESC
    """, tuple(params))

    results = {}
    for row in cur.fetchall():
        mt = row["motion_type"]
        if mt not in results:
            results[mt] = {"granted": 0, "denied": 0, "partial": 0, "total": 0}
        results[mt][row["outcome"]] = row["count"]
        results[mt]["total"] += row["count"]

    # Calculate rates with confidence intervals
    motion_stats = []
    for mt, data in results.items():
        total = data["total"]
        if total == 0:
            continue

        # Wilson score confidence interval for grant rate
        grant_rate = data["granted"] / total
        z = 1.96  # 95% confidence

        # Wilson score interval
        denominator = 1 + z**2 / total
        center = (grant_rate + z**2 / (2 * total)) / denominator
        spread = z * math.sqrt((grant_rate * (1 - grant_rate) + z**2 / (4 * total)) / total) / denominator

        ci_low = max(0, center - spread)
        ci_high = min(1, center + spread)

        motion_stats.append({
            "motion_type": mt.upper(),
            "motion_type_full": "Motion to Dismiss" if mt == "mtd" else "Motion for Summary Judgment" if mt == "msj" else mt.upper(),
            "total": total,
            "granted": data["granted"],
            "denied": data["denied"],
            "partial": data["partial"],
            "grant_rate": round(grant_rate * 100, 1),
            "deny_rate": round(data["denied"] / total * 100, 1),
            "partial_rate": round(data["partial"] / total * 100, 1),
            "confidence_interval_95": [round(ci_low * 100, 1), round(ci_high * 100, 1)],
            "confidence": "high" if total >= 100 else "medium" if total >= 30 else "low",
            "sample_note": f"n={total}"
        })

    return {
        "court_code": court_code,
        "motion_type_filter": motion_type,
        "motion_stats": motion_stats,
        "methodology": "Outcomes from docket text analysis. 'Partial' = granted in part and/or denied in part."
    }


def get_judge_motion_rates(court_code: str = None, min_cases: int = 5) -> Dict[str, Any]:
    """
    Get judge-level motion grant rates with confidence intervals.
    Only includes judges with minimum sample size for statistical validity.
    """
    conn = get_conn()

    if court_code:
        cur = conn.execute("""
            SELECT
                judge_name,
                court_id,
                motion_type,
                granted,
                denied,
                partial,
                total,
                grant_rate
            FROM judge_motion_stats
            WHERE court_id = ? AND total >= ?
            ORDER BY total DESC
        """, (court_code.lower(), min_cases))
    else:
        cur = conn.execute("""
            SELECT
                judge_name,
                court_id,
                motion_type,
                granted,
                denied,
                partial,
                total,
                grant_rate
            FROM judge_motion_stats
            WHERE total >= ?
            ORDER BY total DESC
        """, (min_cases,))

    judges = []
    for row in cur.fetchall():
        total = row["total"]
        grant_rate = row["grant_rate"]

        # Wilson score confidence interval
        z = 1.96
        denominator = 1 + z**2 / total
        center = (grant_rate + z**2 / (2 * total)) / denominator
        spread = z * math.sqrt((grant_rate * (1 - grant_rate) + z**2 / (4 * total)) / total) / denominator

        ci_low = max(0, center - spread)
        ci_high = min(1, center + spread)

        judges.append({
            "judge_name": row["judge_name"],
            "court_code": row["court_id"].upper() if row["court_id"] else None,
            "motion_type": row["motion_type"].upper(),
            "total_motions": total,
            "granted": row["granted"],
            "denied": row["denied"],
            "partial": row["partial"],
            "grant_rate": round(grant_rate * 100, 1),
            "confidence_interval_95": [round(ci_low * 100, 1), round(ci_high * 100, 1)],
            "confidence": "high" if total >= 20 else "medium" if total >= 10 else "low"
        })

    return {
        "court_code": court_code,
        "min_cases_threshold": min_cases,
        "judges": judges,
        "total_judges": len(judges),
        "methodology": "Grant rates from analyzed docket entries. Confidence intervals use Wilson score method."
    }


def get_pro_se_outcomes() -> Dict[str, Any]:
    """Compare outcomes for pro se vs represented litigants."""
    conn = get_conn()

    # Pro se outcomes
    cur = conn.execute("""
        SELECT
            disp_outcome,
            COUNT(*) as count
        FROM fjc_outcomes
        WHERE pro_se = 1 AND disp_outcome IS NOT NULL
        GROUP BY disp_outcome
        ORDER BY count DESC
    """)

    pro_se = {}
    pro_se_total = 0
    for row in cur.fetchall():
        pro_se[row["disp_outcome"]] = row["count"]
        pro_se_total += row["count"]

    # Represented outcomes
    cur = conn.execute("""
        SELECT
            disp_outcome,
            COUNT(*) as count
        FROM fjc_outcomes
        WHERE pro_se = 0 AND disp_outcome IS NOT NULL
        GROUP BY disp_outcome
        ORDER BY count DESC
    """)

    represented = {}
    rep_total = 0
    for row in cur.fetchall():
        represented[row["disp_outcome"]] = row["count"]
        rep_total += row["count"]

    # Build comparison
    all_outcomes = set(pro_se.keys()) | set(represented.keys())
    comparison = []

    for outcome in all_outcomes:
        ps_count = pro_se.get(outcome, 0)
        rep_count = represented.get(outcome, 0)
        ps_pct = round(ps_count / pro_se_total * 100, 1) if pro_se_total > 0 else 0
        rep_pct = round(rep_count / rep_total * 100, 1) if rep_total > 0 else 0

        comparison.append({
            "outcome": outcome,
            "pro_se_count": ps_count,
            "pro_se_pct": ps_pct,
            "represented_count": rep_count,
            "represented_pct": rep_pct,
            "difference": round(ps_pct - rep_pct, 1)
        })

    comparison.sort(key=lambda x: x["represented_count"], reverse=True)

    return {
        "pro_se_total_cases": pro_se_total,
        "represented_total_cases": rep_total,
        "comparison": comparison,
        "key_findings": _analyze_pro_se_findings(comparison, pro_se_total, rep_total)
    }


def _analyze_pro_se_findings(comparison: list, ps_total: int, rep_total: int) -> List[str]:
    """Generate key findings from pro se comparison."""
    findings = []

    ps_dismissal = next((c for c in comparison if c["outcome"] == "dismissal"), None)
    rep_dismissal = next((c for c in comparison if c["outcome"] == "dismissal"), None)

    if ps_dismissal and rep_dismissal:
        if ps_dismissal["pro_se_pct"] > rep_dismissal["represented_pct"]:
            diff = ps_dismissal["pro_se_pct"] - rep_dismissal["represented_pct"]
            findings.append(f"Pro se cases dismissed at {diff:.1f}% higher rate than represented cases")

    ps_settlement = next((c for c in comparison if c["outcome"] == "settlement"), None)
    if ps_settlement:
        if ps_settlement["pro_se_pct"] < ps_settlement["represented_pct"]:
            diff = ps_settlement["represented_pct"] - ps_settlement["pro_se_pct"]
            findings.append(f"Pro se cases settle at {diff:.1f}% lower rate than represented cases")

    ps_trial = next((c for c in comparison if c["outcome"] in ("jury_verdict", "court_trial")), None)
    if ps_trial:
        findings.append(f"Pro se cases reaching trial: {ps_trial['pro_se_pct']}%")

    return findings


def get_class_action_outcomes() -> Dict[str, Any]:
    """Analyze outcomes for class action cases."""
    conn = get_conn()

    cur = conn.execute("""
        SELECT
            disp_outcome,
            COUNT(*) as count
        FROM fjc_outcomes
        WHERE class_action = 1 AND disp_outcome IS NOT NULL
        GROUP BY disp_outcome
        ORDER BY count DESC
    """)

    outcomes = {}
    total = 0
    for row in cur.fetchall():
        outcomes[row["disp_outcome"]] = row["count"]
        total += row["count"]

    distribution = []
    for outcome, count in outcomes.items():
        pct = round(count / total * 100, 1) if total > 0 else 0
        distribution.append({
            "outcome": outcome,
            "count": count,
            "percentage": pct
        })

    # Top NOS categories for class actions
    cur = conn.execute("""
        SELECT
            nature_of_suit,
            COUNT(*) as count
        FROM fjc_outcomes
        WHERE class_action = 1 AND nature_of_suit IS NOT NULL
        GROUP BY nature_of_suit
        ORDER BY count DESC
        LIMIT 10
    """)

    top_nos = []
    for row in cur.fetchall():
        top_nos.append({
            "nos_code": row["nature_of_suit"],
            "nos_description": get_nos_description(row["nature_of_suit"]),
            "count": row["count"]
        })

    return {
        "total_class_actions": total,
        "outcome_distribution": distribution,
        "top_nos_categories": top_nos,
        "sample_size_note": f"Based on {total:,} class action cases"
    }


def get_nos_outcome_matrix(limit: int = 20) -> Dict[str, Any]:
    """
    Get outcome breakdown for top NOS categories.
    Returns a matrix showing outcome rates by case type.
    """
    conn = get_conn()

    # Get top NOS by volume
    cur = conn.execute("""
        SELECT nature_of_suit, COUNT(*) as count
        FROM fjc_outcomes
        WHERE nature_of_suit IS NOT NULL AND disp_outcome IS NOT NULL
        GROUP BY nature_of_suit
        ORDER BY count DESC
        LIMIT ?
    """, (limit,))

    top_nos = [row["nature_of_suit"] for row in cur.fetchall()]

    if not top_nos:
        return {"matrix": [], "outcomes": []}

    # Get outcome breakdown for each
    placeholders = ",".join(["?"] * len(top_nos))
    cur = conn.execute(f"""
        SELECT
            nature_of_suit,
            disp_outcome,
            COUNT(*) as count
        FROM fjc_outcomes
        WHERE nature_of_suit IN ({placeholders}) AND disp_outcome IS NOT NULL
        GROUP BY nature_of_suit, disp_outcome
    """, tuple(top_nos))

    # Build matrix
    data = {}
    all_outcomes = set()
    for row in cur.fetchall():
        nos = row["nature_of_suit"]
        outcome = row["disp_outcome"]
        count = row["count"]

        if nos not in data:
            data[nos] = {"total": 0, "outcomes": {}}
        data[nos]["outcomes"][outcome] = count
        data[nos]["total"] += count
        all_outcomes.add(outcome)

    # Convert to matrix format
    matrix = []
    for nos in top_nos:
        if nos not in data:
            continue

        row_data = {
            "nos_code": nos,
            "nos_description": get_nos_description(nos),
            "total": data[nos]["total"]
        }

        for outcome in all_outcomes:
            count = data[nos]["outcomes"].get(outcome, 0)
            pct = round(count / data[nos]["total"] * 100, 1) if data[nos]["total"] > 0 else 0
            row_data[f"{outcome}_pct"] = pct
            row_data[f"{outcome}_count"] = count

        matrix.append(row_data)

    return {
        "matrix": matrix,
        "outcomes": list(all_outcomes),
        "methodology": "Outcome percentages based on FJC terminated case data"
    }


def get_prediction_for_case(nos_code: str, court_code: str = None,
                           pro_se: bool = False, class_action: bool = False) -> Dict[str, Any]:
    """
    Generate outcome prediction for a hypothetical case based on objective historical data.
    """
    conn = get_conn()

    conditions = ["disp_outcome IS NOT NULL"]
    params = []

    if nos_code:
        conditions.append("nature_of_suit = ?")
        params.append(nos_code)
    if court_code:
        conditions.append("court_code = ?")
        params.append(court_code.lower())
    if pro_se:
        conditions.append("pro_se = 1")
    if class_action:
        conditions.append("class_action = 1")

    where_clause = " AND ".join(conditions)

    # Get outcome distribution
    cur = conn.execute(f"""
        SELECT disp_outcome, COUNT(*) as count
        FROM fjc_outcomes
        WHERE {where_clause}
        GROUP BY disp_outcome
        ORDER BY count DESC
    """, tuple(params))

    outcomes = []
    total = 0
    for row in cur.fetchall():
        outcomes.append({"outcome": row["disp_outcome"], "count": row["count"]})
        total += row["count"]

    if total == 0:
        return {
            "error": "No historical data matching these criteria",
            "nos_code": nos_code,
            "court_code": court_code
        }

    # Calculate probabilities
    predictions = []
    for o in outcomes:
        prob = o["count"] / total
        predictions.append({
            "outcome": o["outcome"],
            "probability": round(prob * 100, 1),
            "historical_count": o["count"]
        })

    # Judgment for (if data exists)
    cur = conn.execute(f"""
        SELECT judgment_for, COUNT(*) as count
        FROM fjc_outcomes
        WHERE {where_clause} AND judgment_for IS NOT NULL AND length(judgment_for) > 0
        GROUP BY judgment_for
        ORDER BY count DESC
    """, tuple(params))

    judgment_probs = []
    jf_total = 0
    for row in cur.fetchall():
        judgment_probs.append({"party": row["judgment_for"], "count": row["count"]})
        jf_total += row["count"]

    for jp in judgment_probs:
        jp["probability"] = round(jp["count"] / jf_total * 100, 1) if jf_total > 0 else 0

    return {
        "input_parameters": {
            "nos_code": nos_code,
            "nos_description": get_nos_description(nos_code) if nos_code else None,
            "court_code": court_code.upper() if court_code else "All Courts",
            "pro_se": pro_se,
            "class_action": class_action
        },
        "predicted_outcomes": predictions,
        "judgment_for_probabilities": judgment_probs,
        "sample_size": total,
        "confidence": "high" if total >= 500 else "medium" if total >= 50 else "low",
        "disclaimer": "Predictions based on historical averages. Individual case outcomes depend on many factors not captured in this data."
    }


def get_court_benchmarks() -> Dict[str, Any]:
    """
    Get court-level benchmarks for comparison.
    Shows how each court compares to national averages.
    """
    conn = get_conn()

    # National averages
    cur = conn.execute("""
        SELECT disp_outcome, COUNT(*) as count
        FROM fjc_outcomes
        WHERE disp_outcome IS NOT NULL
        GROUP BY disp_outcome
    """)

    national = {}
    national_total = 0
    for row in cur.fetchall():
        national[row["disp_outcome"]] = row["count"]
        national_total += row["count"]

    # Handle empty fjc_outcomes table
    if national_total == 0:
        return {
            "national_averages": {},
            "court_benchmarks": [],
            "note": "No FJC outcome data available for benchmarks"
        }

    national_pcts = {k: round(v / national_total * 100, 1) for k, v in national.items()}

    # Per-court stats
    cur = conn.execute("""
        SELECT
            court_code,
            disp_outcome,
            COUNT(*) as count
        FROM fjc_outcomes
        WHERE court_code IS NOT NULL AND disp_outcome IS NOT NULL
        GROUP BY court_code, disp_outcome
    """)

    courts = {}
    for row in cur.fetchall():
        court = row["court_code"]
        if court not in courts:
            courts[court] = {"outcomes": {}, "total": 0}
        courts[court]["outcomes"][row["disp_outcome"]] = row["count"]
        courts[court]["total"] += row["count"]

    # Build benchmark data
    benchmarks = []
    for court, data in courts.items():
        if data["total"] < 100:  # Skip courts with too few cases
            continue

        court_pcts = {k: round(v / data["total"] * 100, 1) for k, v in data["outcomes"].items()}

        # Calculate deviation from national average
        deviations = {}
        for outcome in national_pcts:
            court_val = court_pcts.get(outcome, 0)
            nat_val = national_pcts.get(outcome, 0)
            deviations[outcome] = round(court_val - nat_val, 1)

        benchmarks.append({
            "court_code": court.upper(),
            "total_cases": data["total"],
            "settlement_rate": court_pcts.get("settlement", 0),
            "dismissal_rate": court_pcts.get("dismissal", 0),
            "trial_rate": round(court_pcts.get("jury_verdict", 0) + court_pcts.get("court_trial", 0), 1),
            "vs_national": deviations
        })

    benchmarks.sort(key=lambda x: x["total_cases"], reverse=True)

    return {
        "national_averages": national_pcts,
        "national_total_cases": national_total,
        "court_benchmarks": benchmarks[:50],
        "methodology": "Comparison of court outcomes to national averages from FJC data"
    }


def get_analytics_summary() -> Dict[str, Any]:
    """Get summary of all available analytics data and quality."""
    conn = get_conn()

    # FJC data
    cur = conn.execute("SELECT COUNT(*) as cnt FROM fjc_outcomes")
    fjc_count = cur.fetchone()["cnt"]

    # Motion outcomes
    cur = conn.execute("SELECT COUNT(*) as cnt FROM motion_outcomes")
    motion_count = cur.fetchone()["cnt"]

    # Judge stats
    cur = conn.execute("SELECT COUNT(*) as cnt FROM judge_motion_stats")
    judge_count = cur.fetchone()["cnt"]

    # Motion breakdown
    cur = conn.execute("""
        SELECT motion_type, outcome, COUNT(*) as cnt
        FROM motion_outcomes
        GROUP BY motion_type, outcome
    """)
    motion_breakdown = {}
    for row in cur.fetchall():
        mt = row["motion_type"]
        if mt not in motion_breakdown:
            motion_breakdown[mt] = {}
        motion_breakdown[mt][row["outcome"]] = row["cnt"]

    return {
        "data_sources": {
            "fjc_outcomes": {
                "records": fjc_count,
                "description": "Federal Judicial Center case disposition records",
                "quality": "high",
                "coverage": "Terminated civil cases 1970-present"
            },
            "motion_outcomes": {
                "records": motion_count,
                "description": "Motion to Dismiss and Summary Judgment outcomes",
                "quality": "medium",
                "breakdown": motion_breakdown,
                "note": "Limited sample - use with caution for individual predictions"
            },
            "judge_motion_stats": {
                "records": judge_count,
                "description": "Per-judge motion grant rates",
                "quality": "medium",
                "note": "Only judges with 5+ motions included"
            }
        },
        "available_predictions": [
            "Case outcome by Nature of Suit",
            "Case outcome by Court",
            "Motion to Dismiss grant rates",
            "Motion for Summary Judgment grant rates",
            "Pro se vs represented outcomes",
            "Class action outcomes",
            "Judge-specific motion rates (limited)"
        ],
        "methodology_notes": [
            "All predictions based on objective historical data",
            "Confidence intervals provided where sample size permits",
            "Outcomes preserved at reported granularity (no over-simplification)",
            "'Partial' outcomes = 'granted in part and/or denied in part'"
        ]
    }
