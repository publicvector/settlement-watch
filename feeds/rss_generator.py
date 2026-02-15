#!/usr/bin/env python3
"""
RSS Feed Generator for Settlement Watch
Generates RSS and Atom feeds for settlements and court cases.
"""
import os
import sys
from datetime import datetime
from typing import List, Dict, Optional
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import Database, get_db


class RSSGenerator:
    """Generate RSS 2.0 feeds."""

    def __init__(self, db: Database = None):
        self.db = db or get_db()
        self.output_dir = Path(__file__).parent.parent / "docs" / "feeds"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _format_date(self, date_str: str) -> str:
        """Format date for RSS (RFC 822)."""
        if not date_str:
            return datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")
        try:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
        except:
            return date_str

    def _prettify(self, elem: Element) -> str:
        """Pretty print XML."""
        rough_string = tostring(elem, encoding='unicode')
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ")

    def generate_rss(
        self,
        title: str,
        link: str,
        description: str,
        items: List[Dict],
        feed_url: str = None
    ) -> str:
        """Generate RSS 2.0 feed XML."""
        rss = Element('rss', version='2.0')
        rss.set('xmlns:atom', 'http://www.w3.org/2005/Atom')

        channel = SubElement(rss, 'channel')
        SubElement(channel, 'title').text = title
        SubElement(channel, 'link').text = link
        SubElement(channel, 'description').text = description
        SubElement(channel, 'language').text = 'en-us'
        SubElement(channel, 'lastBuildDate').text = self._format_date(datetime.now().isoformat())

        if feed_url:
            atom_link = SubElement(channel, 'atom:link')
            atom_link.set('href', feed_url)
            atom_link.set('rel', 'self')
            atom_link.set('type', 'application/rss+xml')

        for item_data in items:
            item = SubElement(channel, 'item')
            SubElement(item, 'title').text = item_data.get('title', 'Untitled')
            SubElement(item, 'link').text = item_data.get('url') or item_data.get('link', '')
            SubElement(item, 'description').text = item_data.get('description', '')
            SubElement(item, 'pubDate').text = self._format_date(
                item_data.get('pub_date') or item_data.get('filing_date', '')
            )
            SubElement(item, 'guid').text = item_data.get('guid', item_data.get('url', ''))

            if item_data.get('category'):
                SubElement(item, 'category').text = item_data['category']

        return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(rss, encoding='unicode')

    # === Settlement Feeds ===

    def generate_settlements_feed(self, category: str = None, limit: int = 50) -> str:
        """Generate RSS feed for settlements."""
        settlements = self.db.get_settlements(limit=limit, category=category)

        # Format items
        items = []
        for s in settlements:
            title = s['title']
            if s.get('amount_formatted'):
                title = f"[{s['amount_formatted']}] {title}"

            items.append({
                'title': title,
                'url': s.get('url', ''),
                'description': s.get('description', ''),
                'pub_date': s.get('pub_date', ''),
                'guid': s.get('guid', ''),
                'category': s.get('category', '')
            })

        cat_suffix = f" - {category}" if category else ""
        return self.generate_rss(
            title=f"Settlement Watch{cat_suffix}",
            link="https://publicvector.github.io/settlement-watch/",
            description=f"Legal settlements from courts and regulators{cat_suffix}",
            items=items,
            feed_url=f"https://publicvector.github.io/settlement-watch/feeds/settlements{'-' + category.lower() if category else ''}.xml"
        )

    def save_settlements_feeds(self):
        """Generate and save settlement feeds."""
        # Main feed
        xml = self.generate_settlements_feed()
        (self.output_dir / "settlements.xml").write_text(xml)
        print(f"Generated: feeds/settlements.xml")

        # Category feeds
        categories = ['Data Breach', 'Privacy', 'Healthcare', 'Antitrust',
                      'Securities', 'Employment', 'Environmental', 'Consumer']
        for cat in categories:
            settlements = self.db.get_settlements(category=cat, limit=50)
            if settlements:
                xml = self.generate_settlements_feed(category=cat)
                filename = f"settlements-{cat.lower().replace(' ', '-')}.xml"
                (self.output_dir / filename).write_text(xml)
                print(f"Generated: feeds/{filename}")

    # === State Court Feeds ===

    def generate_state_feed(self, state: str, limit: int = 50) -> str:
        """Generate RSS feed for a state's court cases."""
        cases = self.db.get_state_cases(state=state, limit=limit)

        items = []
        for c in cases:
            title = c.get('case_title') or c.get('case_number', 'Unknown Case')
            if c.get('case_number') and c.get('case_title'):
                title = f"{c['case_number']}: {c['case_title']}"

            description_parts = []
            if c.get('court'):
                description_parts.append(f"Court: {c['court']}")
            if c.get('case_type'):
                description_parts.append(f"Type: {c['case_type']}")
            if c.get('charges'):
                description_parts.append(f"Charges: {c['charges']}")
            if c.get('status'):
                description_parts.append(f"Status: {c['status']}")

            items.append({
                'title': title,
                'url': c.get('url', ''),
                'description': ' | '.join(description_parts),
                'pub_date': c.get('filing_date', ''),
                'guid': c.get('guid', ''),
                'category': c.get('case_type', '')
            })

        state_name = STATE_NAMES.get(state.upper(), state)
        return self.generate_rss(
            title=f"{state_name} Court Cases",
            link=f"https://publicvector.github.io/settlement-watch/states/{state.lower()}/",
            description=f"Recent court filings from {state_name}",
            items=items,
            feed_url=f"https://publicvector.github.io/settlement-watch/feeds/state-{state.lower()}.xml"
        )

    def generate_case_type_feed(self, case_type: str, limit: int = 50) -> str:
        """Generate RSS feed for a specific case type."""
        cases = self.db.get_state_cases_by_type(case_type=case_type, limit=limit)

        items = []
        for c in cases:
            state = c.get('state', '')
            title = c.get('case_title') or c.get('case_number', 'Unknown Case')
            if c.get('case_number') and c.get('case_title'):
                title = f"[{state}] {c['case_number']}: {c['case_title']}"
            elif state:
                title = f"[{state}] {title}"

            description_parts = []
            if c.get('court'):
                description_parts.append(f"Court: {c['court']}")
            if c.get('charges'):
                description_parts.append(f"Charges: {c['charges']}")
            if c.get('status'):
                description_parts.append(f"Status: {c['status']}")

            items.append({
                'title': title,
                'url': c.get('url', ''),
                'description': ' | '.join(description_parts),
                'pub_date': c.get('filing_date', ''),
                'guid': c.get('guid', ''),
                'category': case_type
            })

        slug = case_type.lower().replace(' ', '-').replace('/', '-')
        return self.generate_rss(
            title=f"{case_type} Cases",
            link=f"https://publicvector.github.io/settlement-watch/types/{slug}/",
            description=f"Court cases of type: {case_type}",
            items=items,
            feed_url=f"https://publicvector.github.io/settlement-watch/feeds/type-{slug}.xml"
        )

    def save_state_feeds(self):
        """Generate and save state court feeds."""
        states = self.db.get_states()

        # Per-state feeds
        for state in states:
            xml = self.generate_state_feed(state)
            filename = f"state-{state.lower()}.xml"
            (self.output_dir / filename).write_text(xml)
            print(f"Generated: feeds/{filename}")

        # Combined all-states feed
        all_cases = self.db.get_state_cases(limit=100)
        if all_cases:
            items = []
            for c in all_cases:
                state = c.get('state', 'Unknown')
                title = f"[{state}] {c.get('case_title') or c.get('case_number', 'Case')}"
                items.append({
                    'title': title,
                    'url': c.get('url', ''),
                    'description': f"{c.get('court', '')} - {c.get('case_type', '')}",
                    'pub_date': c.get('filing_date', ''),
                    'guid': c.get('guid', ''),
                    'category': c.get('state', '')
                })

            xml = self.generate_rss(
                title="All State Courts",
                link="https://publicvector.github.io/settlement-watch/states/",
                description="Court filings from all monitored state courts",
                items=items,
                feed_url="https://publicvector.github.io/settlement-watch/feeds/states-all.xml"
            )
            (self.output_dir / "states-all.xml").write_text(xml)
            print("Generated: feeds/states-all.xml")

    def save_case_type_feeds(self):
        """Generate and save feeds organized by case type."""
        case_types = self.db.get_case_types()

        if not case_types:
            print("No case types found in database")
            return

        # Create types subdirectory
        types_dir = self.output_dir / "types"
        types_dir.mkdir(exist_ok=True)

        for case_type in case_types:
            try:
                xml = self.generate_case_type_feed(case_type)
                slug = case_type.lower().replace(' ', '-').replace('/', '-')
                filename = f"type-{slug}.xml"
                (types_dir / filename).write_text(xml)
                print(f"Generated: feeds/types/{filename}")
            except Exception as e:
                print(f"Error generating feed for {case_type}: {e}")

    # === Federal Court Feeds ===

    def generate_federal_feed(self, court: str = None, limit: int = 50) -> str:
        """Generate RSS feed for federal court cases."""
        cases = self.db.get_federal_cases(court=court, limit=limit)

        items = []
        for c in cases:
            title = c.get('case_title') or c.get('case_number', 'Unknown Case')
            if c.get('case_number'):
                title = f"{c['case_number']}: {title}"

            description_parts = []
            if c.get('court'):
                description_parts.append(f"Court: {c['court']}")
            if c.get('nature_of_suit'):
                description_parts.append(f"Nature: {c['nature_of_suit']}")
            if c.get('jurisdiction'):
                description_parts.append(f"Jurisdiction: {c['jurisdiction']}")

            items.append({
                'title': title,
                'url': c.get('url', ''),
                'description': ' | '.join(description_parts),
                'pub_date': c.get('filing_date', ''),
                'guid': c.get('guid', ''),
                'category': c.get('nature_of_suit', '')
            })

        court_suffix = f" - {court}" if court else ""
        return self.generate_rss(
            title=f"Federal Court Cases{court_suffix}",
            link="https://publicvector.github.io/settlement-watch/federal/",
            description=f"Federal court filings{court_suffix}",
            items=items,
            feed_url=f"https://publicvector.github.io/settlement-watch/feeds/federal{'-' + court.lower() if court else ''}.xml"
        )

    def save_federal_feeds(self):
        """Generate and save federal court feeds."""
        xml = self.generate_federal_feed()
        (self.output_dir / "federal.xml").write_text(xml)
        print("Generated: feeds/federal.xml")

    # === Docket Entry Feeds ===

    def generate_recent_filings_feed(self, days: int = 7, state: str = None) -> str:
        """Generate RSS feed for recent docket filings."""
        entries = self.db.get_recent_filings(days=days, state=state, limit=50)

        items = []
        for e in entries:
            case_num = e.get('case_number', 'Unknown')
            state_code = e.get('state', '')
            title = f"[{state_code}] {case_num}: {e.get('entry_text', '')[:80]}"

            description_parts = []
            if e.get('entry_type'):
                description_parts.append(f"Type: {e['entry_type']}")
            if e.get('filed_by'):
                description_parts.append(f"Filed by: {e['filed_by']}")
            if e.get('is_opinion'):
                description_parts.append("OPINION/DECISION")
            if e.get('is_order'):
                description_parts.append("COURT ORDER")

            items.append({
                'title': title,
                'url': e.get('document_url', ''),
                'description': e.get('entry_text', '') + '\n\n' + ' | '.join(description_parts),
                'pub_date': e.get('entry_date', ''),
                'guid': e.get('guid', ''),
                'category': e.get('entry_type', 'filing')
            })

        state_suffix = f" - {state}" if state else ""
        return self.generate_rss(
            title=f"Recent Court Filings ({days}d){state_suffix}",
            link="https://publicvector.github.io/settlement-watch/filings/",
            description=f"Court filings from the last {days} days{state_suffix}",
            items=items,
            feed_url=f"https://publicvector.github.io/settlement-watch/feeds/filings-recent{'-' + state.lower() if state else ''}.xml"
        )

    def generate_opinions_feed(self, days: int = 30, state: str = None) -> str:
        """Generate RSS feed for judicial opinions and decisions."""
        entries = self.db.get_opinions(days=days, state=state, limit=50)

        items = []
        for e in entries:
            case_num = e.get('case_number', 'Unknown')
            state_code = e.get('state', '')
            title = f"[{state_code}] {case_num}: {e.get('entry_text', '')[:80]}"

            items.append({
                'title': title,
                'url': e.get('document_url', ''),
                'description': e.get('entry_text', ''),
                'pub_date': e.get('entry_date', ''),
                'guid': e.get('guid', ''),
                'category': 'opinion'
            })

        state_suffix = f" - {state}" if state else ""
        return self.generate_rss(
            title=f"Court Opinions & Decisions{state_suffix}",
            link="https://publicvector.github.io/settlement-watch/opinions/",
            description=f"Judicial opinions, rulings, and decisions{state_suffix}",
            items=items,
            feed_url=f"https://publicvector.github.io/settlement-watch/feeds/opinions{'-' + state.lower() if state else ''}.xml"
        )

    def generate_orders_feed(self, days: int = 30, state: str = None) -> str:
        """Generate RSS feed for court orders."""
        entries = self.db.get_orders(days=days, state=state, limit=50)

        items = []
        for e in entries:
            case_num = e.get('case_number', 'Unknown')
            state_code = e.get('state', '')
            title = f"[{state_code}] {case_num}: {e.get('entry_text', '')[:80]}"

            items.append({
                'title': title,
                'url': e.get('document_url', ''),
                'description': e.get('entry_text', ''),
                'pub_date': e.get('entry_date', ''),
                'guid': e.get('guid', ''),
                'category': 'order'
            })

        state_suffix = f" - {state}" if state else ""
        return self.generate_rss(
            title=f"Court Orders{state_suffix}",
            link="https://publicvector.github.io/settlement-watch/orders/",
            description=f"Court orders and directives{state_suffix}",
            items=items,
            feed_url=f"https://publicvector.github.io/settlement-watch/feeds/orders{'-' + state.lower() if state else ''}.xml"
        )

    def save_docket_feeds(self):
        """Generate and save docket entry feeds."""
        # Create docket subdirectory
        docket_dir = self.output_dir / "docket"
        docket_dir.mkdir(exist_ok=True)

        # Recent filings (all states)
        try:
            xml = self.generate_recent_filings_feed(days=7)
            (docket_dir / "filings-recent.xml").write_text(xml)
            print("Generated: feeds/docket/filings-recent.xml")
        except Exception as e:
            print(f"  Error generating recent filings feed: {e}")

        # Opinions (all states)
        try:
            xml = self.generate_opinions_feed(days=30)
            (docket_dir / "opinions.xml").write_text(xml)
            print("Generated: feeds/docket/opinions.xml")
        except Exception as e:
            print(f"  Error generating opinions feed: {e}")

        # Orders (all states)
        try:
            xml = self.generate_orders_feed(days=30)
            (docket_dir / "orders.xml").write_text(xml)
            print("Generated: feeds/docket/orders.xml")
        except Exception as e:
            print(f"  Error generating orders feed: {e}")

        # Per-state feeds for states with data
        for state in self.db.get_states():
            try:
                # Recent filings per state
                xml = self.generate_recent_filings_feed(days=7, state=state)
                (docket_dir / f"filings-{state.lower()}.xml").write_text(xml)
                print(f"Generated: feeds/docket/filings-{state.lower()}.xml")
            except Exception as e:
                pass

    # === Master Index ===

    def generate_feed_index(self) -> str:
        """Generate OPML index of all feeds."""
        opml = Element('opml', version='2.0')

        head = SubElement(opml, 'head')
        SubElement(head, 'title').text = 'Settlement Watch Feeds'
        SubElement(head, 'dateCreated').text = datetime.now().isoformat()

        body = SubElement(opml, 'body')

        # Settlements
        settlements_outline = SubElement(body, 'outline', text='Settlements', title='Settlements')
        SubElement(settlements_outline, 'outline',
                   text='All Settlements',
                   type='rss',
                   xmlUrl='https://publicvector.github.io/settlement-watch/feeds/settlements.xml')

        # State Courts - by State
        states_outline = SubElement(body, 'outline', text='State Courts (by State)', title='State Courts (by State)')
        for state in self.db.get_states():
            state_name = STATE_NAMES.get(state.upper(), state)
            SubElement(states_outline, 'outline',
                       text=state_name,
                       type='rss',
                       xmlUrl=f'https://publicvector.github.io/settlement-watch/feeds/state-{state.lower()}.xml')

        # State Courts - by Case Type
        types_outline = SubElement(body, 'outline', text='State Courts (by Case Type)', title='State Courts (by Case Type)')
        for case_type in self.db.get_case_types():
            slug = case_type.lower().replace(' ', '-').replace('/', '-')
            SubElement(types_outline, 'outline',
                       text=case_type,
                       type='rss',
                       xmlUrl=f'https://publicvector.github.io/settlement-watch/feeds/types/type-{slug}.xml')

        # Docket Entries (Recent Filings, Opinions, Orders)
        docket_outline = SubElement(body, 'outline', text='Docket Entries', title='Docket Entries')
        SubElement(docket_outline, 'outline',
                   text='Recent Filings (7 days)',
                   type='rss',
                   xmlUrl='https://publicvector.github.io/settlement-watch/feeds/docket/filings-recent.xml')
        SubElement(docket_outline, 'outline',
                   text='Opinions & Decisions',
                   type='rss',
                   xmlUrl='https://publicvector.github.io/settlement-watch/feeds/docket/opinions.xml')
        SubElement(docket_outline, 'outline',
                   text='Court Orders',
                   type='rss',
                   xmlUrl='https://publicvector.github.io/settlement-watch/feeds/docket/orders.xml')

        # Federal
        federal_outline = SubElement(body, 'outline', text='Federal Courts', title='Federal Courts')
        SubElement(federal_outline, 'outline',
                   text='All Federal',
                   type='rss',
                   xmlUrl='https://publicvector.github.io/settlement-watch/feeds/federal.xml')

        return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(opml, encoding='unicode')

    def save_all_feeds(self):
        """Generate and save all feeds."""
        print("Generating RSS feeds...")
        print("=" * 50)

        self.save_settlements_feeds()
        self.save_state_feeds()
        self.save_case_type_feeds()
        self.save_docket_feeds()
        self.save_federal_feeds()

        # OPML index
        opml = self.generate_feed_index()
        (self.output_dir / "feeds.opml").write_text(opml)
        print("Generated: feeds/feeds.opml")

        print("=" * 50)
        stats = self.db.get_stats()
        print(f"Database: {stats['settlements']} settlements, {stats['state_cases']} state cases, {stats['federal_cases']} federal cases")
        print(f"Docket: {stats.get('docket_entries', 0)} entries, {stats.get('opinions', 0)} opinions, {stats.get('orders', 0)} orders")


# State name mapping
STATE_NAMES = {
    'AK': 'Alaska',
    'AR': 'Arkansas',
    'CO': 'Colorado',
    'CT': 'Connecticut',
    'DE': 'Delaware',
    'IN': 'Indiana',
    'LA': 'Louisiana',
    'MT': 'Montana',
    'NV': 'Nevada',
    'ND': 'North Dakota',
    'OH': 'Ohio',
    'OK': 'Oklahoma',
    'PA': 'Pennsylvania',
    'VT': 'Vermont',
    'WI': 'Wisconsin',
}


if __name__ == "__main__":
    generator = RSSGenerator()
    generator.save_all_feeds()
