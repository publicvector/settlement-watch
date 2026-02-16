"""
Multi-channel Newsletter Delivery Service.

Handles newsletter distribution across:
- Email (SMTP)
- RSS feed generation
- Web archive pages
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Dict, Any, List, Optional
import hashlib

from ..models.db import (
    get_conn, get_newsletter, get_newsletter_issue,
    get_newsletter_items, get_newsletter_subscribers,
    update_newsletter_issue
)

# Email configuration
SMTP_HOST = os.getenv("NEWSLETTER_SMTP_HOST", os.getenv("SMTP_HOST", "smtp.gmail.com"))
SMTP_PORT = int(os.getenv("NEWSLETTER_SMTP_PORT", os.getenv("SMTP_PORT", "587")))
SMTP_USER = os.getenv("NEWSLETTER_SMTP_USER", os.getenv("SMTP_USER", ""))
SMTP_PASS = os.getenv("NEWSLETTER_SMTP_PASS", os.getenv("SMTP_PASS", ""))
FROM_EMAIL = os.getenv("NEWSLETTER_FROM_EMAIL", SMTP_USER)

# Base URL for links
BASE_URL = os.getenv("BASE_URL", "https://pacerapirssdemo.vercel.app")


class NewsletterDelivery:
    """Handle newsletter distribution across multiple channels."""

    def __init__(self):
        self.smtp_configured = bool(SMTP_USER and SMTP_PASS)

    def send_email(
        self,
        issue_id: str,
        recipients: List[str] = None,
        test_mode: bool = False
    ) -> Dict[str, Any]:
        """
        Send newsletter via email.

        Args:
            issue_id: Newsletter issue ID
            recipients: Optional specific recipients (overrides subscribers)
            test_mode: If True, only sends to first recipient

        Returns:
            Delivery status dict
        """
        if not self.smtp_configured:
            return {
                "success": False,
                "error": "SMTP not configured",
                "sent_count": 0
            }

        issue = get_newsletter_issue(issue_id)
        if not issue:
            return {"success": False, "error": "Issue not found", "sent_count": 0}

        newsletter = get_newsletter(issue["newsletter_id"])
        if not newsletter:
            return {"success": False, "error": "Newsletter not found", "sent_count": 0}

        # Get recipients
        if recipients is None:
            subscribers = get_newsletter_subscribers(issue["newsletter_id"])
            recipients = [s["email"] for s in subscribers if s.get("email")]

        if not recipients:
            return {"success": False, "error": "No recipients", "sent_count": 0}

        if test_mode:
            recipients = recipients[:1]

        # Get content
        html_content = issue.get("html_content", "")
        text_content = self._html_to_text(html_content)

        sent_count = 0
        errors = []

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)

                for recipient in recipients:
                    try:
                        msg = MIMEMultipart("alternative")
                        msg["From"] = FROM_EMAIL
                        msg["To"] = recipient
                        msg["Subject"] = issue.get("title", "Court Filings Newsletter")

                        # Add unsubscribe header
                        unsubscribe_url = f"{BASE_URL}/newsletter/unsubscribe?email={recipient}"
                        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"

                        # Attach text and HTML versions
                        msg.attach(MIMEText(text_content, "plain"))
                        msg.attach(MIMEText(html_content, "html"))

                        server.send_message(msg)
                        sent_count += 1

                    except Exception as e:
                        errors.append(f"{recipient}: {str(e)}")

        except Exception as e:
            return {
                "success": False,
                "error": f"SMTP error: {str(e)}",
                "sent_count": sent_count
            }

        # Update issue status
        if sent_count > 0:
            update_newsletter_issue(issue_id, status="sent", sent_at=datetime.utcnow().isoformat())

        return {
            "success": sent_count > 0,
            "sent_count": sent_count,
            "total_recipients": len(recipients),
            "errors": errors if errors else None
        }

    def _html_to_text(self, html: str) -> str:
        """Convert HTML to plain text."""
        import re
        # Remove style and script tags
        text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        # Convert links
        text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>', r"\2 (\1)", text)
        # Remove remaining tags
        text = re.sub(r"<[^>]+>", "", text)
        # Clean up whitespace
        text = re.sub(r"\n\s*\n", "\n\n", text)
        text = re.sub(r" +", " ", text)
        return text.strip()

    def generate_rss(self, newsletter_id: str, limit: int = 20) -> str:
        """
        Generate RSS feed XML for a newsletter.

        Args:
            newsletter_id: Newsletter ID
            limit: Maximum issues to include

        Returns:
            RSS XML string
        """
        newsletter = get_newsletter(newsletter_id)
        if not newsletter:
            return self._empty_rss("Newsletter not found")

        conn = get_conn()
        cur = conn.execute("""
            SELECT * FROM newsletter_issues
            WHERE newsletter_id = ? AND status = 'sent'
            ORDER BY sent_at DESC
            LIMIT ?
        """, (newsletter_id, limit))

        issues = [dict(row) for row in cur.fetchall()]

        items_xml = ""
        for issue in issues:
            slug = self._generate_slug(issue["id"])
            pub_date = self._format_rss_date(issue.get("sent_at") or issue.get("generated_at"))

            # Get items for description
            items = get_newsletter_items(issue["id"])[:5]
            description = issue.get("summary_text", "")
            if items:
                description += "\n\nHighlights:\n" + "\n".join(
                    f"- {item.get('court_code', '').upper()} {item.get('case_number', '')}: {item.get('title', '')}"
                    for item in items
                )

            items_xml += f"""
    <item>
      <title>{self._escape_xml(issue.get('title', 'Newsletter'))}</title>
      <link>{BASE_URL}/newsletter/{slug}</link>
      <guid isPermaLink="true">{BASE_URL}/newsletter/{slug}</guid>
      <pubDate>{pub_date}</pubDate>
      <description>{self._escape_xml(description)}</description>
    </item>"""

        rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{self._escape_xml(newsletter.get('name', 'Newsletter'))}</title>
    <link>{BASE_URL}/newsletter/feed/{newsletter_id}</link>
    <description>{self._escape_xml(newsletter.get('description', 'Court filings newsletter'))}</description>
    <language>en-us</language>
    <lastBuildDate>{self._format_rss_date(datetime.utcnow().isoformat())}</lastBuildDate>
    <atom:link href="{BASE_URL}/newsletter/feed/{newsletter_id}.xml" rel="self" type="application/rss+xml"/>{items_xml}
  </channel>
</rss>"""

        return rss

    def _empty_rss(self, message: str) -> str:
        """Generate empty RSS feed with message."""
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Newsletter</title>
    <description>{self._escape_xml(message)}</description>
  </channel>
</rss>"""

    def _escape_xml(self, text: str) -> str:
        """Escape XML special characters."""
        if not text:
            return ""
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;"))

    def _format_rss_date(self, iso_date: str) -> str:
        """Format ISO date to RSS date format."""
        try:
            dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
            return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
        except Exception:
            return datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")

    def _generate_slug(self, issue_id: str) -> str:
        """Generate URL-friendly slug from issue ID."""
        return hashlib.sha256(issue_id.encode()).hexdigest()[:12]

    def get_web_archive_html(self, issue_id: str) -> Optional[str]:
        """
        Get HTML for web archive page.

        Args:
            issue_id: Newsletter issue ID

        Returns:
            HTML string or None
        """
        issue = get_newsletter_issue(issue_id)
        if not issue:
            return None

        newsletter = get_newsletter(issue["newsletter_id"])
        items = get_newsletter_items(issue_id)

        # Use stored HTML content or regenerate
        html_content = issue.get("html_content", "")

        # Add web-specific wrapper
        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{issue.get('title', 'Newsletter')}</title>
    <style>
        body {{ margin: 0; padding: 20px; background: #f5f5f5; }}
        .archive-header {{
            max-width: 600px;
            margin: 0 auto 20px;
            padding: 15px;
            background: #fff;
            border-radius: 8px;
            text-align: center;
        }}
        .archive-header a {{ color: #3182ce; }}
    </style>
</head>
<body>
    <div class="archive-header">
        <p><a href="{BASE_URL}/newsletter">← Back to Newsletter Archive</a></p>
        <p style="font-size: 12px; color: #666;">
            Published: {issue.get('sent_at', issue.get('generated_at', ''))} |
            {len(items)} filings
        </p>
    </div>
    {html_content}
</body>
</html>"""

    def deliver_all(
        self,
        issue_id: str,
        test_mode: bool = False
    ) -> Dict[str, Any]:
        """
        Deliver newsletter via all configured channels.

        Args:
            issue_id: Newsletter issue ID
            test_mode: If True, limit delivery for testing

        Returns:
            Delivery results for each channel
        """
        issue = get_newsletter_issue(issue_id)
        if not issue:
            return {"success": False, "error": "Issue not found"}

        newsletter = get_newsletter(issue["newsletter_id"])
        if not newsletter:
            return {"success": False, "error": "Newsletter not found"}

        channels = newsletter.get("output_channels", ["email", "rss", "web"])
        results = {"issue_id": issue_id, "channels": {}}

        # Email delivery
        if "email" in channels:
            email_result = self.send_email(issue_id, test_mode=test_mode)
            results["channels"]["email"] = email_result

        # RSS is generated on-demand, just verify it works
        if "rss" in channels:
            try:
                rss = self.generate_rss(issue["newsletter_id"], limit=1)
                results["channels"]["rss"] = {
                    "success": True,
                    "feed_url": f"{BASE_URL}/newsletter/feed/{issue['newsletter_id']}.xml"
                }
            except Exception as e:
                results["channels"]["rss"] = {"success": False, "error": str(e)}

        # Web archive is generated on-demand
        if "web" in channels:
            slug = self._generate_slug(issue_id)
            results["channels"]["web"] = {
                "success": True,
                "archive_url": f"{BASE_URL}/newsletter/{slug}"
            }

        # Overall success
        results["success"] = any(
            ch.get("success") for ch in results["channels"].values()
        )

        return results


# Convenience functions
def send_newsletter_email(issue_id: str, recipients: List[str] = None) -> Dict[str, Any]:
    """Send newsletter issue via email."""
    delivery = NewsletterDelivery()
    return delivery.send_email(issue_id, recipients)


def get_newsletter_rss(newsletter_id: str) -> str:
    """Get RSS feed for a newsletter."""
    delivery = NewsletterDelivery()
    return delivery.generate_rss(newsletter_id)


def deliver_newsletter(issue_id: str) -> Dict[str, Any]:
    """Deliver newsletter via all channels."""
    delivery = NewsletterDelivery()
    return delivery.deliver_all(issue_id)
