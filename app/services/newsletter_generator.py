"""
Newsletter Generation Pipeline.

Orchestrates the complete newsletter generation process:
1. Collect candidate filings from RSS items
2. Enrich with RECAP data where available
3. Score relevance with AI
4. Filter and rank by score
5. Fetch documents (RECAP first, PACER fallback)
6. Generate AI summaries
7. Render to HTML and text
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from ..models.db import (
    get_conn, get_newsletter, create_newsletter_issue,
    add_newsletter_item, update_newsletter_issue, get_newsletter_items,
    list_rss_items
)
from .ai_summarizer import get_summarizer
from .courtlistener import get_client as get_cl_client

# Configuration
MAX_PACER_SPEND_PER_NEWSLETTER = float(os.getenv("NEWSLETTER_MAX_PACER_SPEND", "5.00"))
MAX_DOCUMENT_PAGES = int(os.getenv("NEWSLETTER_MAX_DOC_PAGES", "30"))


class NewsletterGenerator:
    """
    Main pipeline for generating newsletter issues.

    Usage:
        generator = NewsletterGenerator()
        issue = generator.generate_issue(newsletter_id)
    """

    def __init__(self):
        self.summarizer = get_summarizer()
        self.cl_client = None
        try:
            self.cl_client = get_cl_client()
        except Exception:
            pass  # CourtListener not configured

    def collect_candidates(
        self,
        newsletter: Dict[str, Any],
        since: datetime = None,
        limit: int = 200
    ) -> List[Dict[str, Any]]:
        """
        Collect RSS items matching newsletter criteria.

        Args:
            newsletter: Newsletter configuration dict
            since: Start datetime (defaults to 24h ago)
            limit: Maximum candidates to collect

        Returns:
            List of RSS items matching criteria
        """
        if since is None:
            if newsletter.get("schedule") == "weekly":
                since = datetime.utcnow() - timedelta(days=7)
            else:
                since = datetime.utcnow() - timedelta(hours=24)

        conn = get_conn()

        # Build query with filters
        conditions = ["created_at >= ?"]
        params = [since.isoformat()]

        # Court filter
        court_codes = newsletter.get("court_codes")
        if court_codes:
            placeholders = ",".join(["?"] * len(court_codes))
            conditions.append(f"court_code IN ({placeholders})")
            params.extend([c.lower() for c in court_codes])

        # Case type filter
        case_types = newsletter.get("case_types")
        if case_types:
            placeholders = ",".join(["?"] * len(case_types))
            conditions.append(f"case_type IN ({placeholders})")
            params.extend([t.lower() for t in case_types])

        where_clause = " AND ".join(conditions)

        cur = conn.execute(f"""
            SELECT * FROM rss_items
            WHERE {where_clause}
            ORDER BY published DESC
            LIMIT ?
        """, tuple(params) + (limit,))

        candidates = [dict(row) for row in cur.fetchall()]

        # Keyword filtering (post-query for flexibility)
        keywords = newsletter.get("keywords")
        if keywords:
            keywords_lower = [k.lower() for k in keywords]
            candidates = [
                c for c in candidates
                if any(kw in (c.get("title", "") + " " + c.get("summary", "")).lower()
                       for kw in keywords_lower)
            ]

        return candidates

    def enrich_with_recap(
        self,
        candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Add RECAP enrichment data to candidates where available.

        Args:
            candidates: List of RSS items

        Returns:
            Enriched candidates with RECAP data attached
        """
        if not self.cl_client:
            return candidates

        conn = get_conn()

        for candidate in candidates:
            case_number = candidate.get("case_number")
            court_code = candidate.get("court_code")

            if not case_number or not court_code:
                continue

            # Check if we have RECAP data
            cur = conn.execute("""
                SELECT * FROM recap_dockets
                WHERE court_code = ? AND docket_number LIKE ?
                LIMIT 1
            """, (court_code.lower(), f"%{case_number}%"))

            recap = cur.fetchone()
            if recap:
                candidate["recap_data"] = dict(recap)
                candidate["has_recap"] = True
            else:
                candidate["has_recap"] = False

        return candidates

    def score_and_filter(
        self,
        candidates: List[Dict[str, Any]],
        newsletter: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Score candidates by relevance and filter below threshold.

        Args:
            candidates: List of RSS items (potentially enriched)
            newsletter: Newsletter configuration

        Returns:
            Scored and filtered candidates, sorted by score
        """
        min_score = newsletter.get("min_relevance_score", 0.5)
        criteria = {
            "keywords": newsletter.get("keywords", []),
            "case_types": newsletter.get("case_types", [])
        }

        scored = self.summarizer.batch_score(candidates, criteria, min_score=min_score)
        return scored

    def fetch_documents(
        self,
        items: List[Dict[str, Any]],
        max_pacer_spend: float = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch document content for AI summarization.

        Strategy: RECAP first (free), PACER fallback with spend limit.

        Args:
            items: Scored items to fetch documents for
            max_pacer_spend: Maximum PACER spending limit

        Returns:
            Items with document content and source info
        """
        if max_pacer_spend is None:
            max_pacer_spend = MAX_PACER_SPEND_PER_NEWSLETTER

        pacer_spent = 0.0

        for item in items:
            item["document_source"] = "none"
            item["document_text"] = None

            # Try RECAP first
            if item.get("has_recap") and self.cl_client:
                try:
                    recap_data = item.get("recap_data", {})
                    cl_docket_id = recap_data.get("cl_docket_id")
                    if cl_docket_id:
                        # Check for available documents
                        docs = self.cl_client.get_docket_documents(cl_docket_id, limit=1)
                        if docs and docs[0].get("is_available"):
                            item["document_source"] = "recap"
                            item["document_url"] = docs[0].get("filepath_local")
                            # Note: actual content fetching would require additional implementation
                except Exception:
                    pass

            # PACER fallback (not implemented in this version - would require PACER auth)
            # if item["document_source"] == "none" and pacer_spent < max_pacer_spend:
            #     # Fetch from PACER...
            #     pass

        return items

    def generate_summaries(
        self,
        items: List[Dict[str, Any]],
        max_items: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Generate AI summaries for top items.

        Args:
            items: Scored items
            max_items: Maximum items to summarize

        Returns:
            Items with AI summaries
        """
        for i, item in enumerate(items[:max_items]):
            try:
                summary = self.summarizer.summarize_filing(
                    item,
                    document_text=item.get("document_text")
                )
                item["ai_summary"] = summary
            except Exception:
                item["ai_summary"] = item.get("summary", "")

            item["display_order"] = i + 1

        return items[:max_items]

    def generate_issue(
        self,
        newsletter_id: str,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Main entry point: generate a complete newsletter issue.

        Args:
            newsletter_id: ID of the newsletter to generate
            dry_run: If True, don't save to database

        Returns:
            Generated newsletter issue with items
        """
        newsletter = get_newsletter(newsletter_id)
        if not newsletter:
            raise ValueError(f"Newsletter not found: {newsletter_id}")

        # Step 1: Collect candidates
        candidates = self.collect_candidates(newsletter)
        if not candidates:
            return {"error": "No candidates found", "item_count": 0}

        # Step 2: Enrich with RECAP
        candidates = self.enrich_with_recap(candidates)

        # Step 3: Score and filter
        scored = self.score_and_filter(candidates, newsletter)
        if not scored:
            return {"error": "No items passed relevance threshold", "item_count": 0}

        # Step 4: Fetch documents (for top items only)
        max_items = newsletter.get("max_items", 20)
        top_items = self.fetch_documents(scored[:max_items * 2])

        # Step 5: Generate summaries
        items_with_summaries = self.generate_summaries(top_items, max_items)

        # Step 6: Generate newsletter intro
        intro = self.summarizer.generate_newsletter_intro(
            items_with_summaries,
            newsletter.get("name", "Court Filings Newsletter")
        )

        # Step 7: Render HTML
        html_content = self.render_html(newsletter, items_with_summaries, intro)

        if dry_run:
            return {
                "newsletter_id": newsletter_id,
                "item_count": len(items_with_summaries),
                "items": items_with_summaries,
                "summary_text": intro,
                "html_preview": html_content[:500] + "..."
            }

        # Step 8: Save to database
        title = f"{newsletter['name']} - {datetime.utcnow().strftime('%B %d, %Y')}"
        issue = create_newsletter_issue(
            newsletter_id=newsletter_id,
            title=title,
            summary_text=intro,
            html_content=html_content,
            item_count=len(items_with_summaries)
        )

        # Save items
        for item in items_with_summaries:
            add_newsletter_item(
                issue_id=issue["id"],
                rss_item_id=item["id"],
                relevance_score=item.get("relevance_score", 0.5),
                ai_summary=item.get("ai_summary"),
                ai_reasoning=item.get("relevance_reasoning"),
                document_source=item.get("document_source", "none"),
                display_order=item.get("display_order", 0)
            )

        return {
            "issue_id": issue["id"],
            "newsletter_id": newsletter_id,
            "title": title,
            "item_count": len(items_with_summaries),
            "status": "draft"
        }

    def render_html(
        self,
        newsletter: Dict[str, Any],
        items: List[Dict[str, Any]],
        intro: str
    ) -> str:
        """Render newsletter to HTML."""
        date_str = datetime.utcnow().strftime("%B %d, %Y")

        items_html = ""
        for item in items:
            score = item.get("relevance_score", 0.5)
            if score >= 0.7:
                score_class = "high"
                score_color = "#48bb78"
            elif score >= 0.5:
                score_class = "medium"
                score_color = "#ecc94b"
            else:
                score_class = "low"
                score_color = "#a0aec0"

            items_html += f"""
            <div class="filing-card" style="border-left: 4px solid {score_color}; margin: 15px 0; padding: 15px; background: #f7fafc; border-radius: 0 8px 8px 0;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <span style="font-weight: bold; color: #2b6cb0;">{(item.get('court_code') or '').upper()} {item.get('case_number') or ''}</span>
                    <span style="font-size: 12px; color: #718096;">{(item.get('case_type') or '').upper()}</span>
                </div>
                <div style="font-weight: 600; color: #1a202c; margin-bottom: 8px;">{item.get('title', '')}</div>
                <div style="color: #4a5568; font-size: 14px; line-height: 1.5;">{item.get('ai_summary', item.get('summary', ''))}</div>
                <div style="margin-top: 10px;">
                    <a href="{item.get('link', '#')}" style="color: #3182ce; font-size: 13px; text-decoration: none;">View on PACER →</a>
                </div>
            </div>
            """

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{newsletter.get('name', 'Newsletter')} - {date_str}</title>
</head>
<body style="margin: 0; padding: 0; background: #edf2f7; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
    <div style="max-width: 600px; margin: 0 auto; background: white;">
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #1a365d 0%, #2c5282 100%); color: white; padding: 30px; text-align: center;">
            <h1 style="margin: 0; font-size: 24px; font-weight: 600;">{newsletter.get('name', 'Court Filings Newsletter')}</h1>
            <p style="margin: 10px 0 0; opacity: 0.9; font-size: 14px;">{date_str} · {len(items)} filings</p>
        </div>

        <!-- Intro -->
        <div style="padding: 20px 30px; background: #ebf8ff; border-bottom: 1px solid #bee3f8;">
            <p style="margin: 0; color: #2c5282; line-height: 1.6;">{intro}</p>
        </div>

        <!-- Filings -->
        <div style="padding: 20px 30px;">
            <h2 style="font-size: 18px; color: #1a202c; margin: 0 0 15px; padding-bottom: 10px; border-bottom: 2px solid #e2e8f0;">Today's Filings</h2>
            {items_html}
        </div>

        <!-- Footer -->
        <div style="background: #f7fafc; padding: 20px 30px; text-align: center; border-top: 1px solid #e2e8f0;">
            <p style="margin: 0; font-size: 12px; color: #718096;">
                Generated by PACER RSS Newsletter System<br>
                <a href="#" style="color: #3182ce;">Unsubscribe</a> · <a href="#" style="color: #3182ce;">Manage Preferences</a>
            </p>
        </div>
    </div>
</body>
</html>"""

        return html

    def render_text(
        self,
        newsletter: Dict[str, Any],
        items: List[Dict[str, Any]],
        intro: str
    ) -> str:
        """Render newsletter to plain text."""
        date_str = datetime.utcnow().strftime("%B %d, %Y")

        lines = [
            f"{newsletter.get('name', 'Court Filings Newsletter')}",
            f"{date_str} - {len(items)} filings",
            "=" * 60,
            "",
            intro,
            "",
            "-" * 60,
            ""
        ]

        for item in items:
            lines.extend([
                f"[{(item.get('court_code') or '').upper()}] {item.get('case_number') or ''}",
                item.get('title') or '',
                item.get('ai_summary') or item.get('summary') or '',
                f"Link: {item.get('link') or ''}",
                ""
            ])

        lines.extend([
            "-" * 60,
            "Generated by PACER RSS Newsletter System"
        ])

        return "\n".join(lines)


# Convenience function
def generate_newsletter(newsletter_id: str, dry_run: bool = False) -> Dict[str, Any]:
    """Generate a newsletter issue."""
    generator = NewsletterGenerator()
    return generator.generate_issue(newsletter_id, dry_run=dry_run)
