"""RECAP enrichment service - fetches case data from CourtListener"""
import os
import uuid
import json
from datetime import datetime
from typing import Optional, Dict, Any, List

from .courtlistener import get_client, CourtListenerClient
from .motion_tracker import extract_and_track_motions
from ..models.db import (
    upsert_recap_docket, get_recap_docket,
    insert_recap_parties, insert_recap_attorneys,
    insert_recap_entries, insert_recap_documents,
    insert_motion_events_batch,
    list_unenriched_cases, get_recap_stats
)

# Configuration
RECAP_AUTO_ENRICH = os.getenv("RECAP_AUTO_ENRICH", "false").lower() == "true"
RECAP_RATE_LIMIT = int(os.getenv("RECAP_RATE_LIMIT", "1000"))


def enrich_case(court_code: str, case_number: str, force: bool = False) -> Dict[str, Any]:
    """
    Fetch full case data from RECAP/CourtListener and store locally.

    Args:
        court_code: Court code (e.g., 'nysd')
        case_number: Case number (e.g., '1:25-cv-00001')
        force: If True, re-enrich even if already exists

    Returns:
        Dict with enrichment results
    """
    client = get_client()

    if not client.is_configured():
        return {
            "status": "error",
            "message": "CourtListener API token not configured",
            "court_code": court_code,
            "case_number": case_number
        }

    # Check if already enriched
    existing = get_recap_docket(court_code, case_number)
    if existing and not force:
        return {
            "status": "already_enriched",
            "court_code": court_code,
            "case_number": case_number,
            "docket_id": existing["id"],
            "last_enriched": existing.get("last_enriched")
        }

    # Search for docket in CourtListener
    try:
        results = client.search_dockets(court=court_code, docket_number=case_number, limit=5)
    except Exception as e:
        return {
            "status": "error",
            "message": f"Search failed: {str(e)}",
            "court_code": court_code,
            "case_number": case_number
        }

    if not results:
        return {
            "status": "not_found",
            "message": "Case not found in RECAP archive",
            "court_code": court_code,
            "case_number": case_number
        }

    # Find best match (exact docket number match)
    cl_docket = None
    for r in results:
        if r.get("docket_number", "").strip() == case_number.strip():
            cl_docket = r
            break

    if not cl_docket:
        # Use first result if no exact match
        cl_docket = results[0]

    cl_docket_id = cl_docket.get("docket_id") or cl_docket.get("id")
    if not cl_docket_id:
        return {
            "status": "error",
            "message": "Could not extract docket ID from search results",
            "court_code": court_code,
            "case_number": case_number
        }

    # Fetch full docket details
    try:
        docket_detail = client.get_docket(cl_docket_id)
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to fetch docket details: {str(e)}",
            "court_code": court_code,
            "case_number": case_number
        }

    # Create local docket record
    docket_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"recap:{court_code}:{case_number}"))
    now = datetime.utcnow().isoformat() + "Z"

    docket_record = {
        "id": docket_id,
        "cl_docket_id": cl_docket_id,
        "court_code": court_code,
        "docket_number": docket_detail.get("docket_number", case_number),
        "case_name": docket_detail.get("case_name"),
        "date_filed": docket_detail.get("date_filed"),
        "date_terminated": docket_detail.get("date_terminated"),
        "nature_of_suit": docket_detail.get("nature_of_suit"),
        "cause": docket_detail.get("cause"),
        "jury_demand": docket_detail.get("jury_demand"),
        "assigned_to": _extract_judge_name(docket_detail.get("assigned_to_str")),
        "referred_to": _extract_judge_name(docket_detail.get("referred_to_str")),
        "party_count": 0,
        "attorney_count": 0,
        "entry_count": 0,
        "last_enriched": now
    }

    # Fetch and store parties
    parties_stored = 0
    attorneys_stored = 0
    try:
        parties = client.get_parties(cl_docket_id, limit=200)
        party_records = []
        attorney_records = []

        for p in parties:
            party_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"party:{docket_id}:{p.get('id')}"))
            party_records.append({
                "id": party_id,
                "docket_id": docket_id,
                "cl_party_id": p.get("id"),
                "name": p.get("name"),
                "party_type": _extract_party_type(p),
                "extra_info": p.get("extra_info"),
                "date_terminated": p.get("date_terminated")
            })

            # Extract attorneys from party
            for atty in p.get("attorneys", []):
                atty_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"atty:{docket_id}:{atty.get('id')}"))
                attorney_records.append({
                    "id": atty_id,
                    "docket_id": docket_id,
                    "party_id": party_id,
                    "cl_attorney_id": atty.get("id"),
                    "name": atty.get("name"),
                    "firm": atty.get("firm"),
                    "phone": atty.get("phone"),
                    "email": atty.get("email"),
                    "roles": json.dumps(atty.get("roles", []))
                })

        if party_records:
            insert_recap_parties(party_records)
            parties_stored = len(party_records)

        if attorney_records:
            insert_recap_attorneys(attorney_records)
            attorneys_stored = len(attorney_records)

    except Exception as e:
        # Non-fatal: continue without parties
        pass

    # Fetch and store docket entries
    entries_stored = 0
    documents_stored = 0
    try:
        entries = client.get_docket_entries(cl_docket_id, limit=500)
        entry_records = []
        document_records = []

        for e in entries:
            entry_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"entry:{docket_id}:{e.get('id')}"))
            entry_records.append({
                "id": entry_id,
                "docket_id": docket_id,
                "cl_entry_id": e.get("id"),
                "entry_number": e.get("entry_number"),
                "date_filed": e.get("date_filed"),
                "description": e.get("description"),
                "document_count": len(e.get("recap_documents", [])),
                "pacer_doc_id": e.get("pacer_doc_id"),
                "recap_document_id": None
            })

            # Extract documents from entry
            for doc in e.get("recap_documents", []):
                doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"doc:{entry_id}:{doc.get('id')}"))
                document_records.append({
                    "id": doc_id,
                    "entry_id": entry_id,
                    "cl_document_id": doc.get("id"),
                    "document_number": doc.get("document_number"),
                    "attachment_number": doc.get("attachment_number"),
                    "description": doc.get("description"),
                    "page_count": doc.get("page_count"),
                    "filepath_local": doc.get("filepath_local"),
                    "is_available": doc.get("is_available", False),
                    "sha1": doc.get("sha1")
                })

        if entry_records:
            insert_recap_entries(entry_records)
            entries_stored = len(entry_records)

        if document_records:
            insert_recap_documents(document_records)
            documents_stored = len(document_records)

    except Exception as e:
        # Non-fatal: continue without entries
        pass

    # Track motions from docket entries
    motions_stored = 0
    try:
        if entries_stored > 0 and entry_records:
            motions = extract_and_track_motions(
                docket_id=docket_id,
                entries=entry_records,
                docket_info=docket_record,
                attorneys=attorney_records if attorneys_stored > 0 else None,
                parties=party_records if parties_stored > 0 else None
            )
            if motions:
                insert_motion_events_batch(motions)
                motions_stored = len(motions)
    except Exception:
        # Non-fatal: continue without motion tracking
        pass

    # Update counts and save docket
    docket_record["party_count"] = parties_stored
    docket_record["attorney_count"] = attorneys_stored
    docket_record["entry_count"] = entries_stored

    upsert_recap_docket(docket_record)

    return {
        "status": "enriched",
        "court_code": court_code,
        "case_number": case_number,
        "docket_id": docket_id,
        "cl_docket_id": cl_docket_id,
        "case_name": docket_record["case_name"],
        "parties_stored": parties_stored,
        "attorneys_stored": attorneys_stored,
        "entries_stored": entries_stored,
        "documents_stored": documents_stored,
        "motions_tracked": motions_stored,
        "last_enriched": now
    }


def enrich_batch(limit: int = 50, max_requests: int = None) -> Dict[str, Any]:
    """
    Batch enrich unenriched cases from RSS data.

    Args:
        limit: Max cases to attempt
        max_requests: Override for rate limit

    Returns:
        Dict with batch results
    """
    if max_requests is None:
        max_requests = RECAP_RATE_LIMIT

    # Get unenriched cases
    unenriched = list_unenriched_cases(limit=limit)

    results = {
        "attempted": 0,
        "enriched": 0,
        "not_found": 0,
        "errors": 0,
        "already_enriched": 0,
        "cases": []
    }

    for case in unenriched:
        if results["attempted"] >= limit:
            break

        # Rough estimate: each case takes ~3 requests
        if results["attempted"] * 3 >= max_requests:
            break

        court_code = case.get("court_code")
        case_number = case.get("case_number")

        if not court_code or not case_number:
            continue

        result = enrich_case(court_code, case_number)
        results["attempted"] += 1

        status = result.get("status")
        if status == "enriched":
            results["enriched"] += 1
        elif status == "not_found":
            results["not_found"] += 1
        elif status == "already_enriched":
            results["already_enriched"] += 1
        else:
            results["errors"] += 1

        results["cases"].append({
            "court_code": court_code,
            "case_number": case_number,
            "status": status
        })

    return results


def get_enrichment_status() -> Dict[str, Any]:
    """Get overall RECAP enrichment status."""
    client = get_client()

    status = {
        "api_configured": client.is_configured(),
        "auto_enrich_enabled": RECAP_AUTO_ENRICH,
        "rate_limit": RECAP_RATE_LIMIT
    }

    # Test connection if configured
    if client.is_configured():
        status["api_status"] = client.test_connection()

    # Get database stats
    status.update(get_recap_stats())

    return status


def _extract_judge_name(judge_str: Optional[str]) -> Optional[str]:
    """Extract judge name from CourtListener format."""
    if not judge_str:
        return None
    # Remove common prefixes
    name = judge_str.strip()
    for prefix in ["Judge ", "Hon. ", "Honorable ", "Magistrate Judge ", "Chief Judge "]:
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.strip() if name else None


def _extract_party_type(party: dict) -> Optional[str]:
    """Extract party type from CourtListener party object."""
    # Check party_types array
    party_types = party.get("party_types", [])
    if party_types:
        # Get first non-terminated type
        for pt in party_types:
            if not pt.get("date_terminated"):
                return pt.get("name")
        # Fall back to first type
        return party_types[0].get("name") if party_types else None

    return party.get("party_type")
