#!/usr/bin/env python3
"""
DocumentCloud Scraper

Scrapes recently uploaded legal documents from DocumentCloud.
https://www.documentcloud.org/

DocumentCloud is a platform where journalists and researchers upload
primary source documents including:
- Court filings and orders
- Settlement agreements
- Government reports
- Legal complaints
- Investigative documents

Uses the public DocumentCloud API.
"""
import argparse
import json
import requests
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path


# DocumentCloud API endpoint
API_BASE = "https://api.www.documentcloud.org/api"
SEARCH_URL = f"{API_BASE}/documents/search/"


@dataclass
class Document:
    """Represents a DocumentCloud document."""
    id: str
    title: str
    description: str
    source: str
    created_at: str
    updated_at: str
    page_count: int
    language: str
    organization: str
    document_url: str
    pdf_url: str
    thumbnail_url: str
    category: str = ""
    is_court_doc: bool = False
    is_settlement: bool = False
    is_order: bool = False

    def __post_init__(self):
        """Detect document type from title/description."""
        text = f"{self.title} {self.description}".lower()

        court_patterns = [
            'court', 'filing', 'docket', 'case no', 'complaint',
            'motion', 'brief', 'ruling', 'judgment', 'indictment',
            'subpoena', 'deposition', 'affidavit', 'petition'
        ]
        settlement_patterns = [
            'settlement', 'consent decree', 'plea agreement',
            'resolution', 'agreement to settle'
        ]
        order_patterns = [
            'order', 'ruling', 'decision', 'opinion', 'judgment',
            'decree', 'injunction', 'mandate'
        ]

        self.is_court_doc = any(p in text for p in court_patterns)
        self.is_settlement = any(p in text for p in settlement_patterns)
        self.is_order = any(p in text for p in order_patterns)

        if self.is_settlement:
            self.category = 'settlement'
        elif self.is_order:
            self.category = 'order'
        elif self.is_court_doc:
            self.category = 'court_filing'
        else:
            self.category = 'document'


class DocumentCloudScraper:
    """Scraper for DocumentCloud public documents."""

    LEGAL_QUERIES = [
        'settlement agreement',
        'consent decree',
        'court order',
        'class action',
        'lawsuit complaint',
        'plea agreement',
        'indictment',
        'court filing',
        'motion to dismiss',
        'summary judgment',
        'injunction',
        'subpoena',
    ]

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; SettlementWatch/1.0)',
            'Accept': 'application/json',
        })

    def search(self, query: str, per_page: int = 25, page: int = 1,
               sort: str = "created_at", order: str = "desc") -> List[Document]:
        """Search DocumentCloud for documents."""
        params = {
            'q': query,
            'per_page': min(per_page, 100),
            'page': page,
            'sort': sort,
            'order': order,
        }

        try:
            response = self.session.get(SEARCH_URL, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            documents = []
            for doc in data.get('results', []):
                try:
                    document = self._parse_document(doc)
                    documents.append(document)
                except Exception as e:
                    continue

            return documents

        except requests.RequestException as e:
            print(f"Error searching DocumentCloud: {e}")
            return []

    def _parse_document(self, doc: dict) -> Document:
        """Parse API response into Document object."""
        doc_id = str(doc.get('id', ''))
        slug = doc.get('slug', doc_id)
        document_url = f"https://www.documentcloud.org/documents/{doc_id}-{slug}"
        pdf_url = doc.get('pdf_url', '') or f"https://assets.documentcloud.org/documents/{doc_id}/{slug}.pdf"

        pages = doc.get('pages', [])
        thumbnail_url = pages[0].get('image', '') if pages else ''

        org = doc.get('organization', {})
        org_name = org.get('name', '') if isinstance(org, dict) else str(org)

        return Document(
            id=doc_id,
            title=doc.get('title', 'Untitled'),
            description=doc.get('description', '') or doc.get('source', '') or '',
            source=doc.get('source', ''),
            created_at=doc.get('created_at', ''),
            updated_at=doc.get('updated_at', ''),
            page_count=doc.get('page_count', 0),
            language=doc.get('language', 'en'),
            organization=org_name,
            document_url=document_url,
            pdf_url=pdf_url,
            thumbnail_url=thumbnail_url,
        )

    def get_recent_legal_documents(self, days: int = 7, limit: int = 100) -> List[Document]:
        """Get recently uploaded legal documents."""
        all_docs = []
        seen_ids = set()

        print(f"Searching DocumentCloud for legal documents (last {days} days)...")

        for query in self.LEGAL_QUERIES:
            if len(all_docs) >= limit:
                break

            print(f"  Query: '{query}'")
            docs = self.search(query, per_page=25)

            for doc in docs:
                if doc.id not in seen_ids:
                    try:
                        created = datetime.fromisoformat(doc.created_at.replace('Z', '+00:00'))
                        cutoff = datetime.now(created.tzinfo) - timedelta(days=days)
                        if created >= cutoff:
                            seen_ids.add(doc.id)
                            all_docs.append(doc)
                    except (ValueError, TypeError):
                        seen_ids.add(doc.id)
                        all_docs.append(doc)

                if len(all_docs) >= limit:
                    break

            print(f"    Found {len(docs)} documents, {len(all_docs)} total unique")

        all_docs.sort(key=lambda d: d.created_at, reverse=True)
        return all_docs[:limit]


def main():
    parser = argparse.ArgumentParser(description='DocumentCloud Legal Document Scraper')
    parser.add_argument('--days', '-d', type=int, default=7)
    parser.add_argument('--limit', '-l', type=int, default=50)
    parser.add_argument('--query', '-q', help='Custom search query')
    parser.add_argument('--output', '-o', help='Output JSON file')

    args = parser.parse_args()

    print("=" * 70)
    print("DOCUMENTCLOUD LEGAL DOCUMENT SCRAPER")
    print("=" * 70)

    scraper = DocumentCloudScraper()

    if args.query:
        docs = scraper.search(args.query, per_page=args.limit)
    else:
        docs = scraper.get_recent_legal_documents(days=args.days, limit=args.limit)

    print(f"\nRESULTS: {len(docs)} documents")

    by_category = {}
    for doc in docs:
        by_category.setdefault(doc.category, []).append(doc)

    for category, cat_docs in sorted(by_category.items()):
        print(f"\n{category.upper()} ({len(cat_docs)}):")
        for doc in cat_docs[:5]:
            print(f"  [{doc.created_at[:10]}] {doc.title[:60]}")

    output_file = args.output or '/tmp/documentcloud_results.json'
    output_data = {
        'scraped_at': datetime.now().isoformat(),
        'query': args.query or 'legal_documents',
        'days': args.days,
        'count': len(docs),
        'documents': [asdict(d) for d in docs]
    }

    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)
    print(f"\nSaved to {output_file}")

    return docs


if __name__ == "__main__":
    main()
