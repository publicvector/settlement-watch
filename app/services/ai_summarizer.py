"""
AI-powered document summarization and relevance scoring using Claude API.

This module provides AI capabilities for the newsletter system:
- Relevance scoring: Rate filings 0-1 based on newsworthiness
- Summarization: Generate concise summaries of court filings
- Newsletter intros: Generate overview text for newsletter issues
"""

import os
import json
from typing import Dict, Any, List, Optional

# Claude API configuration
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS_PER_SUMMARY", "300"))


class AISummarizer:
    """Claude-based summarization and relevance scoring for court filings."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or ANTHROPIC_API_KEY
        self.client = None
        self.model = AI_MODEL

        if self.api_key:
            try:
                from anthropic import Anthropic
                self.client = Anthropic(api_key=self.api_key)
            except ImportError:
                pass  # anthropic package not installed

    def is_configured(self) -> bool:
        """Check if AI is properly configured."""
        return bool(self.client)

    def score_relevance(
        self,
        filing: Dict[str, Any],
        criteria: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Score a filing's relevance (0-1) for newsletter inclusion.

        Args:
            filing: RSS item or docket entry data
            criteria: Newsletter filter criteria (keywords, case types, etc.)

        Returns:
            Dict with score (0-1), category, and reasoning
        """
        if not self.is_configured():
            return self._rule_based_score(filing, criteria)

        criteria = criteria or {}
        keywords = criteria.get("keywords", [])

        prompt = f"""Analyze this federal court filing and rate its newsworthiness on a scale of 0 to 1.

Filing Details:
- Court: {filing.get('court_code', 'Unknown').upper()}
- Case Number: {filing.get('case_number', 'Unknown')}
- Case Type: {filing.get('case_type', 'Unknown')}
- Title: {filing.get('title', 'Unknown')}
- Summary: {filing.get('summary', 'No summary')}
- Nature of Suit: {filing.get('nature_of_suit', 'Unknown')}
- Judge: {filing.get('judge_name', 'Unknown')}

{"Keywords to watch for: " + ", ".join(keywords) if keywords else ""}

Rating Criteria:
- 0.9-1.0: Major ruling, significant precedent, high-profile case
- 0.7-0.8: Notable motion outcome, important procedural development
- 0.5-0.6: Routine but potentially interesting filing
- 0.3-0.4: Standard procedural matter
- 0.0-0.2: Administrative or clerical entry

Respond in JSON format:
{{"score": 0.XX, "category": "significant|notable|routine|administrative", "reasoning": "Brief explanation"}}"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )

            result_text = response.content[0].text.strip()
            # Extract JSON from response
            if "{" in result_text:
                json_str = result_text[result_text.index("{"):result_text.rindex("}") + 1]
                result = json.loads(json_str)
                return {
                    "score": float(result.get("score", 0.5)),
                    "category": result.get("category", "routine"),
                    "reasoning": result.get("reasoning", ""),
                    "source": "ai"
                }
        except Exception as e:
            pass  # Fall back to rule-based

        return self._rule_based_score(filing, criteria)

    def _rule_based_score(
        self,
        filing: Dict[str, Any],
        criteria: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Fallback rule-based scoring when AI is unavailable."""
        score = 0.3  # Base score
        reasons = []

        title = (filing.get("title") or "").lower()
        summary = (filing.get("summary") or "").lower()
        text = title + " " + summary

        # High-value patterns
        high_value = [
            "order granting", "order denying", "summary judgment",
            "motion to dismiss", "class action", "settlement",
            "injunction", "jury verdict", "judgment"
        ]
        for pattern in high_value:
            if pattern in text:
                score += 0.2
                reasons.append(f"Contains '{pattern}'")
                break

        # Medium-value patterns
        medium_value = ["opinion", "ruling", "granted", "denied", "hearing"]
        for pattern in medium_value:
            if pattern in text:
                score += 0.1
                reasons.append(f"Contains '{pattern}'")
                break

        # Keyword matches
        criteria = criteria or {}
        keywords = criteria.get("keywords") or []
        for kw in keywords:
            if kw.lower() in text:
                score += 0.15
                reasons.append(f"Keyword match: {kw}")

        # New case filings
        if filing.get("is_new_case") or "complaint" in text or "petition" in text:
            score += 0.1
            reasons.append("New case filing")

        score = min(score, 1.0)
        category = "significant" if score >= 0.7 else "notable" if score >= 0.5 else "routine"

        return {
            "score": round(score, 2),
            "category": category,
            "reasoning": "; ".join(reasons) if reasons else "Standard filing",
            "source": "rules"
        }

    def summarize_filing(
        self,
        filing: Dict[str, Any],
        document_text: Optional[str] = None,
        max_length: int = 200
    ) -> str:
        """
        Generate a concise summary of a court filing.

        Args:
            filing: RSS item or docket entry data
            document_text: Optional full document text for deeper summarization
            max_length: Maximum summary length in characters

        Returns:
            Summary string
        """
        if not self.is_configured():
            return self._simple_summary(filing)

        context = f"""Court: {filing.get('court_code', '').upper()}
Case: {filing.get('case_number', '')} - {filing.get('title', '')}
Type: {filing.get('case_type', '')}
Filing: {filing.get('summary', '')}"""

        if document_text:
            # Truncate document to avoid token limits
            doc_preview = document_text[:3000] + "..." if len(document_text) > 3000 else document_text
            context += f"\n\nDocument excerpt:\n{doc_preview}"

        prompt = f"""Summarize this federal court filing in 1-2 sentences. Focus on what happened and why it matters.

{context}

Write a clear, professional summary suitable for a legal newsletter. Do not start with "This filing" or similar - get straight to the substance."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=AI_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()
        except Exception:
            return self._simple_summary(filing)

    def _simple_summary(self, filing: Dict[str, Any]) -> str:
        """Fallback simple summary when AI is unavailable."""
        parts = []
        if filing.get("court_code"):
            parts.append(filing["court_code"].upper())
        if filing.get("case_number"):
            parts.append(filing["case_number"])
        if filing.get("title"):
            parts.append(f"- {filing['title']}")

        summary = filing.get("summary", "")
        if summary:
            # Clean up HTML and truncate
            import re
            summary = re.sub(r"<[^>]+>", "", summary)
            if len(summary) > 150:
                summary = summary[:147] + "..."
            parts.append(summary)

        return " ".join(parts) if parts else "Court filing"

    def generate_newsletter_intro(
        self,
        items: List[Dict[str, Any]],
        newsletter_name: str = "Court Filings Newsletter"
    ) -> str:
        """
        Generate an introduction/overview for a newsletter issue.

        Args:
            items: List of newsletter items with their summaries
            newsletter_name: Name of the newsletter

        Returns:
            Introduction text for the newsletter
        """
        if not self.is_configured() or not items:
            return self._simple_intro(items, newsletter_name)

        # Prepare highlights
        highlights = []
        for item in items[:10]:  # Top 10 items
            highlights.append(f"- {item.get('court_code', '').upper()} {item.get('case_number', '')}: {item.get('ai_summary') or item.get('title', '')}")

        prompt = f"""Write a brief 2-3 sentence introduction for today's {newsletter_name}.

Today's highlights ({len(items)} total filings):
{chr(10).join(highlights)}

Write in a professional, informative tone suitable for legal professionals. Mention any particularly significant developments."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()
        except Exception:
            return self._simple_intro(items, newsletter_name)

    def _simple_intro(self, items: List[Dict[str, Any]], newsletter_name: str) -> str:
        """Fallback simple intro when AI is unavailable."""
        from datetime import datetime
        date_str = datetime.utcnow().strftime("%B %d, %Y")
        count = len(items) if items else 0

        if count == 0:
            return f"{newsletter_name} - {date_str}\n\nNo significant filings to report today."

        courts = set(item.get("court_code", "").upper() for item in items if item.get("court_code"))
        court_str = ", ".join(sorted(courts)[:5])
        if len(courts) > 5:
            court_str += f" and {len(courts) - 5} more"

        return f"{newsletter_name} - {date_str}\n\nToday's digest includes {count} notable filings from {court_str}."

    def batch_score(
        self,
        filings: List[Dict[str, Any]],
        criteria: Dict[str, Any] = None,
        min_score: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Score multiple filings and return sorted by relevance.

        Args:
            filings: List of RSS items or docket entries
            criteria: Newsletter filter criteria
            min_score: Minimum score threshold (0-1)

        Returns:
            List of filings with scores, sorted by score descending
        """
        scored = []
        for filing in filings:
            score_result = self.score_relevance(filing, criteria)
            if score_result["score"] >= min_score:
                scored.append({
                    **filing,
                    "relevance_score": score_result["score"],
                    "relevance_category": score_result["category"],
                    "relevance_reasoning": score_result["reasoning"]
                })

        # Sort by score descending
        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        return scored


# Singleton instance
_summarizer = None


def get_summarizer() -> AISummarizer:
    """Get the singleton AI summarizer instance."""
    global _summarizer
    if _summarizer is None:
        _summarizer = AISummarizer()
    return _summarizer
