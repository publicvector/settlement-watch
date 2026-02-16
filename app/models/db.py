
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Dict, Any, List

# Check for Turso/libsql configuration (strip whitespace/newlines from env vars)
TURSO_URL = (os.getenv("TURSO_DATABASE_URL") or "").strip()
TURSO_TOKEN = (os.getenv("TURSO_AUTH_TOKEN") or "").strip()
_using_turso = bool(TURSO_URL and TURSO_TOKEN)

# Use /tmp in serverless environments (Vercel), otherwise local path
_is_serverless = os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME")
if _is_serverless:
    DB_PATH = Path("/tmp/demo.db")
else:
    DB_PATH = Path(__file__).resolve().parent.parent / "demo.db"

# Turso HTTP client using requests
if _using_turso:
    import requests as _requests

    class TursoConnection:
        """Simple Turso HTTP API wrapper that mimics sqlite3 connection."""

        def __init__(self, url: str, token: str):
            # Convert libsql:// to https://
            self.url = url.replace("libsql://", "https://")
            self.token = token
            self.row_factory = None

        def execute(self, sql: str, params: tuple = ()) -> 'TursoCursor':
            return TursoCursor(self, sql, params)

        def executemany(self, sql: str, params_list):
            for params in params_list:
                self.execute(sql, params)

        def _run_query(self, sql: str, params: tuple = ()):
            """Execute query via Turso HTTP API."""
            # Convert ? placeholders to positional args
            args = [{"type": "text", "value": str(p)} if p is not None else {"type": "null"} for p in params]

            payload = {
                "requests": [
                    {"type": "execute", "stmt": {"sql": sql, "args": args}},
                    {"type": "close"}
                ]
            }

            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }

            resp = _requests.post(f"{self.url}/v2/pipeline", json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()

        def execute_batch(self, statements: list):
            """Execute multiple statements in a single request."""
            requests_list = []
            for sql, params in statements:
                args = [{"type": "text", "value": str(p)} if p is not None else {"type": "null"} for p in params]
                requests_list.append({"type": "execute", "stmt": {"sql": sql, "args": args}})
            requests_list.append({"type": "close"})

            payload = {"requests": requests_list}
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }

            resp = _requests.post(f"{self.url}/v2/pipeline", json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            return resp.json()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class TursoCursor:
        """Cursor-like object for Turso results."""

        def __init__(self, conn: TursoConnection, sql: str, params: tuple):
            self.conn = conn
            self.sql = sql
            self.params = params
            # Execute immediately
            self._results = self.conn._run_query(self.sql, self.params)

        def fetchall(self):
            try:
                result = self._results["results"][0]["response"]["result"]
                cols = [c["name"] for c in result["cols"]]
                rows = []
                for row in result["rows"]:
                    # Convert values based on Turso type
                    values = []
                    for cell in row:
                        cell_type = cell.get("type")
                        cell_value = cell.get("value")
                        if cell_type == "null" or cell_value is None:
                            values.append(None)
                        elif cell_type == "integer":
                            values.append(int(cell_value))
                        elif cell_type == "float":
                            values.append(float(cell_value))
                        else:
                            values.append(cell_value)
                    if self.conn.row_factory:
                        rows.append(dict(zip(cols, values)))
                    else:
                        rows.append(tuple(values))
                return rows
            except (KeyError, IndexError, TypeError):
                return []

        def fetchone(self):
            rows = self.fetchall()
            return rows[0] if rows else None

    class TursoRow(dict):
        """Row class that supports both dict and index access."""
        def __getitem__(self, key):
            if isinstance(key, int):
                return list(self.values())[key]
            return super().__getitem__(key)

SCHEMA = """
create table if not exists courts (
  code text primary key,
  name text not null,
  cmecf_base_url text not null
);
create table if not exists cases (
  id text primary key,
  court_code text references courts(code),
  case_number text,
  caption text,
  case_type text,
  nature_of_suit text,
  filed_on text,
  terminated_on text,
  status text,
  source text,
  last_docket_pull text
);
create table if not exists docket_entries (
  id text primary key,
  case_id text references cases(id),
  entry_no text,
  filed_on text,
  text_raw text,
  text_clean text,
  entry_type text,
  has_document integer default 0,
  doc_number text,
  recap_document_id text,
  cmecf_doc_url text
);
create table if not exists pacer_charges (
  id text primary key,
  case_id text,
  court_code text,
  resource text,
  cmecf_url text,
  pages_billed integer,
  amount_usd real,
  api_key_id text,
  triggered_by text,
  created_at text
);
create table if not exists rss_sources (
  id text primary key,
  court_code text,
  url text not null,
  label text,
  last_polled text
);
create table if not exists rss_items (
  id text primary key,
  source_id text,
  court_code text,
  case_number text,
  case_type text,
  judge_name text,
  nature_of_suit text,
  title text,
  summary text,
  link text,
  published text,
  created_at text,
  metadata_json text
);
create table if not exists filing_stats_daily (
  id text primary key,
  date text not null,
  court_code text,
  case_type text,
  nature_of_suit text,
  filing_count integer default 0,
  new_case_count integer default 0,
  unique_cases integer default 0,
  updated_at text
);
create table if not exists case_patterns (
  id text primary key,
  court_code text,
  case_type text,
  nature_of_suit text,
  avg_filings_per_day real,
  total_filings integer,
  total_new_cases integer,
  first_seen text,
  last_seen text,
  trend text,
  updated_at text
);
create table if not exists party_activity (
  id text primary key,
  party_name text not null,
  party_type text,
  court_code text,
  case_count integer default 0,
  filing_count integer default 0,
  case_types text,
  first_seen text,
  last_seen text,
  updated_at text
);
create index if not exists idx_rss_items_court on rss_items(court_code);
create index if not exists idx_rss_items_case_type on rss_items(case_type);
create index if not exists idx_rss_items_published on rss_items(published);
create index if not exists idx_rss_items_nos on rss_items(nature_of_suit);
create index if not exists idx_filing_stats_date on filing_stats_daily(date);
create index if not exists idx_party_activity_name on party_activity(party_name);

-- RECAP/CourtListener integration tables
create table if not exists recap_dockets (
  id text primary key,
  cl_docket_id integer,
  court_code text,
  docket_number text,
  case_name text,
  date_filed text,
  date_terminated text,
  nature_of_suit text,
  cause text,
  jury_demand text,
  assigned_to text,
  referred_to text,
  party_count integer,
  attorney_count integer,
  entry_count integer,
  last_enriched text
);
create unique index if not exists idx_recap_dockets_court_num on recap_dockets(court_code, docket_number);
create index if not exists idx_recap_dockets_cl_id on recap_dockets(cl_docket_id);

create table if not exists recap_parties (
  id text primary key,
  docket_id text,
  cl_party_id integer,
  name text,
  party_type text,
  extra_info text,
  date_terminated text
);
create index if not exists idx_recap_parties_docket on recap_parties(docket_id);
create index if not exists idx_recap_parties_name on recap_parties(name);

create table if not exists recap_attorneys (
  id text primary key,
  docket_id text,
  party_id text,
  cl_attorney_id integer,
  name text,
  firm text,
  phone text,
  email text,
  roles text
);
create index if not exists idx_recap_attorneys_docket on recap_attorneys(docket_id);

create table if not exists recap_entries (
  id text primary key,
  docket_id text,
  cl_entry_id integer,
  entry_number integer,
  date_filed text,
  description text,
  document_count integer,
  pacer_doc_id text,
  recap_document_id integer
);
create index if not exists idx_recap_entries_docket on recap_entries(docket_id);
create index if not exists idx_recap_entries_date on recap_entries(date_filed);

create table if not exists recap_documents (
  id text primary key,
  entry_id text,
  cl_document_id integer,
  document_number text,
  attachment_number integer,
  description text,
  page_count integer,
  filepath_local text,
  is_available integer default 0,
  sha1 text
);
create index if not exists idx_recap_documents_entry on recap_documents(entry_id);

-- Analytics tables (FJC data, motion outcomes, judge stats)
create table if not exists fjc_outcomes (
  id text primary key,
  circuit text,
  district text,
  court_code text,
  docket_number text,
  date_filed text,
  date_terminated text,
  nature_of_suit text,
  disp_code integer,
  disp_outcome text,
  outcome_bucket text,
  proc_prog integer,
  judgment_for text,
  plaintiff text,
  defendant text,
  jury_demand text,
  class_action integer,
  pro_se integer,
  duration_days integer
);
create index if not exists idx_fjc_nos on fjc_outcomes(nature_of_suit);
create index if not exists idx_fjc_court on fjc_outcomes(court_code);
create index if not exists idx_fjc_outcome on fjc_outcomes(disp_outcome);

create table if not exists motion_outcomes (
  id text primary key,
  court_id text,
  docket_number text,
  case_name text,
  judge_name text,
  motion_type text,
  outcome text,
  description text,
  date_filed text,
  cl_docket_id text,
  created_at text
);
create index if not exists idx_motion_type on motion_outcomes(motion_type);
create index if not exists idx_motion_outcome on motion_outcomes(outcome);
create index if not exists idx_motion_judge on motion_outcomes(judge_name);

create table if not exists judge_motion_stats (
  id text primary key,
  judge_name text,
  court_id text,
  motion_type text,
  granted integer default 0,
  denied integer default 0,
  partial integer default 0,
  total integer default 0,
  grant_rate real,
  updated_at text
);
create index if not exists idx_judge_stats_name on judge_motion_stats(judge_name);
create index if not exists idx_judge_stats_court on judge_motion_stats(court_id);

create table if not exists opinion_outcomes (
  id text primary key,
  docket_id text,
  court_id text,
  case_name text,
  date_filed text,
  nature_of_suit text,
  disposition text,
  outcome_type text,
  precedential_status text,
  judges text,
  created_at text
);
create index if not exists idx_opinion_court on opinion_outcomes(court_id);

-- Newsletter system tables
create table if not exists newsletters (
  id text primary key,
  name text not null,
  description text,
  schedule text not null,
  is_active integer default 1,
  court_codes text,
  case_types text,
  keywords text,
  min_relevance_score real default 0.5,
  max_items integer default 20,
  output_channels text,
  created_at text,
  updated_at text
);

create table if not exists subscribers (
  id text primary key,
  email text unique not null,
  name text,
  is_verified integer default 0,
  verification_token text,
  preferences_json text,
  created_at text,
  unsubscribed_at text
);
create index if not exists idx_subscribers_email on subscribers(email);

create table if not exists newsletter_subscriptions (
  id text primary key,
  newsletter_id text,
  subscriber_id text,
  created_at text,
  unique(newsletter_id, subscriber_id)
);

create table if not exists newsletter_issues (
  id text primary key,
  newsletter_id text,
  issue_number integer,
  title text,
  summary_text text,
  html_content text,
  item_count integer,
  status text default 'draft',
  generated_at text,
  sent_at text,
  error_message text
);
create index if not exists idx_newsletter_issues_newsletter on newsletter_issues(newsletter_id);
create index if not exists idx_newsletter_issues_status on newsletter_issues(status);

create table if not exists newsletter_items (
  id text primary key,
  issue_id text,
  rss_item_id text,
  relevance_score real,
  ai_summary text,
  ai_reasoning text,
  document_source text,
  document_url text,
  display_order integer,
  created_at text
);
create index if not exists idx_newsletter_items_issue on newsletter_items(issue_id);

-- Motion events with firm/attorney tracking
create table if not exists motion_events (
  id text primary key,
  docket_id text,
  entry_id text,
  motion_type text,
  filed_by text,
  filing_attorney_id text,
  filing_firm text,
  filed_date text,
  outcome text,
  outcome_date text,
  outcome_entry_id text,
  court_code text,
  case_type text,
  case_name text,
  created_at text
);
create index if not exists idx_motion_events_firm on motion_events(filing_firm);
create index if not exists idx_motion_events_type on motion_events(motion_type);
create index if not exists idx_motion_events_outcome on motion_events(outcome);
create index if not exists idx_motion_events_court on motion_events(court_code);
create index if not exists idx_motion_events_docket on motion_events(docket_id);

-- State court integration tables
create table if not exists state_courts (
  code text primary key,
  state text not null,
  name text not null,
  court_type text,
  jurisdiction_level text,
  data_source text,
  last_sync text
);

create table if not exists state_court_cases (
  id text primary key,
  state text not null,
  court_code text,
  county text,
  case_number text,
  case_style text,
  case_type text,
  case_type_code text,
  date_filed text,
  date_closed text,
  judge text,
  parties_json text,
  charges_json text,
  disposition text,
  data_source text,
  source_url text,
  raw_data_json text,
  created_at text,
  updated_at text
);
create index if not exists idx_state_cases_state on state_court_cases(state);
create index if not exists idx_state_cases_county on state_court_cases(county);
create index if not exists idx_state_cases_case_num on state_court_cases(case_number);
create index if not exists idx_state_cases_type on state_court_cases(case_type);
create index if not exists idx_state_cases_filed on state_court_cases(date_filed);
create unique index if not exists idx_state_cases_unique on state_court_cases(state, county, case_number);

create table if not exists state_court_entries (
  id text primary key,
  case_id text,
  entry_date text,
  entry_number text,
  description text,
  entry_type text,
  document_url text,
  created_at text
);
create index if not exists idx_state_entries_case on state_court_entries(case_id);
create index if not exists idx_state_entries_date on state_court_entries(entry_date);

create table if not exists state_court_parties (
  id text primary key,
  case_id text,
  name text,
  party_type text,
  attorney_name text,
  attorney_firm text,
  created_at text
);
create index if not exists idx_state_parties_case on state_court_parties(case_id);
create index if not exists idx_state_parties_name on state_court_parties(name);
create index if not exists idx_state_parties_attorney on state_court_parties(attorney_name);

create table if not exists state_court_opinions (
  id text primary key,
  state text not null,
  court text,
  case_name text,
  citation text,
  date_decided text,
  docket_number text,
  judges text,
  opinion_type text,
  opinion_text text,
  headnotes text,
  data_source text,
  source_url text,
  cl_opinion_id integer,
  created_at text
);
create index if not exists idx_opinions_state on state_court_opinions(state);
create index if not exists idx_opinions_court on state_court_opinions(court);
create index if not exists idx_opinions_date on state_court_opinions(date_decided);
create index if not exists idx_opinions_citation on state_court_opinions(citation);

create table if not exists state_court_documents (
  id text primary key,
  case_id text,
  state text,
  case_number text,
  doc_type text,
  title text,
  description text,
  source text,
  file_url text,
  file_path text,
  file_size integer,
  mime_type text,
  extracted_text text,
  metadata_json text,
  created_at text
);
create index if not exists idx_docs_case on state_court_documents(case_id);
create index if not exists idx_docs_state on state_court_documents(state);
create index if not exists idx_docs_type on state_court_documents(doc_type);

-- State court analytics aggregates
create table if not exists state_court_stats (
  id text primary key,
  state text not null,
  county text,
  court_type text,
  case_type text,
  period text,
  total_cases integer default 0,
  new_filings integer default 0,
  dispositions integer default 0,
  pending integer default 0,
  avg_duration_days real,
  updated_at text
);
create index if not exists idx_state_stats_state on state_court_stats(state);
create index if not exists idx_state_stats_period on state_court_stats(period);

-- Scraper run tracking
create table if not exists scraper_runs (
  id text primary key,
  state text not null,
  county text,
  scraper_type text,
  started_at text,
  completed_at text,
  cases_found integer default 0,
  cases_stored integer default 0,
  entries_stored integer default 0,
  errors_count integer default 0,
  errors_json text,
  last_sync_marker text,
  status text default 'running'
);
create index if not exists idx_scraper_runs_state on scraper_runs(state);
create index if not exists idx_scraper_runs_started on scraper_runs(started_at);
create index if not exists idx_scraper_runs_status on scraper_runs(status);

-- CAPTCHA encounter tracking
create table if not exists captcha_encounters (
  id text primary key,
  state text,
  county text,
  url text,
  pattern_matched text,
  encountered_at text,
  resolved_at text,
  resolution_method text,
  resolved integer default 0
);
create index if not exists idx_captcha_state on captcha_encounters(state);
create index if not exists idx_captcha_resolved on captcha_encounters(resolved);

-- Document download queue for automatic PACER document fetching
create table if not exists doc_download_queue (
  id text primary key,
  court_code text,
  case_number text,
  doc_url text not null,
  priority integer default 5,
  estimated_cost real default 0.10,
  trigger_name text,
  rss_item_id text,
  status text default 'pending',
  error_message text,
  created_at text,
  completed_at text,
  actual_cost real
);
create index if not exists idx_doc_queue_status on doc_download_queue(status);
create index if not exists idx_doc_queue_priority on doc_download_queue(priority desc);
create index if not exists idx_doc_queue_court on doc_download_queue(court_code);

-- OCR columns for state_court_documents (added via ALTER TABLE if not exists)
-- Note: SQLite doesn't support IF NOT EXISTS for ALTER TABLE, so we handle this in init_db
"""


def get_conn():
    """Get database connection (Turso or local SQLite)."""
    if _using_turso:
        conn = TursoConnection(TURSO_URL, TURSO_TOKEN)
        conn.row_factory = dict
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    with conn:
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                try:
                    conn.execute(stmt)
                except Exception:
                    pass  # Table may already exist
        # Lightweight migration: add metadata_json to rss_items if missing (skip for Turso)
        if not _using_turso:
            cur = conn.execute("PRAGMA table_info(rss_items)")
            cols = {r[1] for r in cur.fetchall()}
            if "metadata_json" not in cols:
                conn.execute("ALTER TABLE rss_items ADD COLUMN metadata_json text")

            # Migration: add OCR columns to state_court_documents
            try:
                cur = conn.execute("PRAGMA table_info(state_court_documents)")
                cols = {r[1] for r in cur.fetchall()}
                if "ocr_used" not in cols:
                    conn.execute("ALTER TABLE state_court_documents ADD COLUMN ocr_used integer default 0")
                if "ocr_confidence" not in cols:
                    conn.execute("ALTER TABLE state_court_documents ADD COLUMN ocr_confidence real")
                if "ocr_method" not in cols:
                    conn.execute("ALTER TABLE state_court_documents ADD COLUMN ocr_method text")
            except Exception:
                pass  # Table may not exist yet
    return conn

def upsert_court(code: str, name: str, url: str):
    conn = get_conn()
    with conn:
        conn.execute(
            "insert into courts(code,name,cmecf_base_url) values(?,?,?) "
            "on conflict(code) do update set name=excluded.name, cmecf_base_url=excluded.cmecf_base_url",
            (code, name, url),
        )

def upsert_courts_batch(courts: list):
    """Batch upsert multiple courts in a single request (for Turso efficiency)."""
    conn = get_conn()
    if _using_turso and hasattr(conn, 'execute_batch'):
        sql = ("insert into courts(code,name,cmecf_base_url) values(?,?,?) "
               "on conflict(code) do update set name=excluded.name, cmecf_base_url=excluded.cmecf_base_url")
        statements = [(sql, (c["code"], c["name"], c["url"])) for c in courts]
        conn.execute_batch(statements)
    else:
        for c in courts:
            upsert_court(c["code"], c["name"], c["url"])

def upsert_case(case: Dict[str, Any]):
    conn = get_conn()
    fields = ["id","court_code","case_number","caption","case_type","nature_of_suit","filed_on","terminated_on","status","source","last_docket_pull"]
    values = [case.get(k) for k in fields]
    placeholders = ",".join(["?"]*len(fields))
    with conn:
        conn.execute(
            f"insert into cases({','.join(fields)}) values({placeholders}) "
            f"on conflict(id) do update set "
            f"court_code=excluded.court_code, case_number=excluded.case_number, caption=excluded.caption, "
            f"case_type=excluded.case_type, nature_of_suit=excluded.nature_of_suit, filed_on=excluded.filed_on, "
            f"terminated_on=excluded.terminated_on, status=excluded.status, source=excluded.source, "
            f"last_docket_pull=excluded.last_docket_pull",
            values
        )

def insert_entries(entries: Iterable[Dict[str, Any]]):
    conn = get_conn()
    with conn:
        for e in entries:
            conn.execute(
                "insert into docket_entries(id,case_id,entry_no,filed_on,text_raw,text_clean,entry_type,has_document,doc_number,recap_document_id,cmecf_doc_url) "
                "values(?,?,?,?,?,?,?,?,?,?,?)",
                (e["id"], e["case_id"], e.get("entry_no"), e.get("filed_on"), e.get("text_raw"),
                 e.get("text_clean"), e.get("entry_type"), int(e.get("has_document", False)),
                 e.get("doc_number"), e.get("recap_document_id"), e.get("cmecf_doc_url"))
            )

def insert_charge(charge: Dict[str, Any]):
    conn = get_conn()
    with conn:
        conn.execute(
            "insert into pacer_charges(id,case_id,court_code,resource,cmecf_url,pages_billed,amount_usd,api_key_id,triggered_by,created_at) "
            "values(?,?,?,?,?,?,?,?,?,?)",
            (charge["id"], charge.get("case_id"), charge.get("court_code"), charge.get("resource"),
             charge.get("cmecf_url"), charge.get("pages_billed"), charge.get("amount_usd"),
             charge.get("api_key_id"), charge.get("triggered_by"), charge.get("created_at"))
        )

def list_cases():
    conn = get_conn()
    cur = conn.execute("select * from cases order by filed_on desc")
    return [dict(r) for r in cur.fetchall()]

def get_case(case_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.execute("select * from cases where id=?", (case_id,))
    row = cur.fetchone()
    return dict(row) if row else None

def list_entries(case_id: str):
    conn = get_conn()
    cur = conn.execute(
        "select id, entry_no, filed_on, text_clean, entry_type, has_document, doc_number from docket_entries where case_id=? order by filed_on",
        (case_id,)
    )
    return [dict(r) for r in cur.fetchall()]

def list_charges():
    conn = get_conn()
    cur = conn.execute("select * from pacer_charges order by created_at desc")
    return [dict(r) for r in cur.fetchall()]

# RSS helpers
def upsert_rss_source(source: Dict[str, Any]):
    conn = get_conn()
    with conn:
        conn.execute(
            "insert into rss_sources(id,court_code,url,label,last_polled) values(?,?,?,?,?) "
            "on conflict(id) do update set court_code=excluded.court_code, url=excluded.url, "
            "label=excluded.label, last_polled=excluded.last_polled",
            (source["id"], source.get("court_code"), source["url"], source.get("label"), source.get("last_polled"))
        )

def upsert_rss_sources_batch(sources: list):
    """Batch upsert multiple RSS sources in a single request (for Turso efficiency)."""
    conn = get_conn()
    if _using_turso and hasattr(conn, 'execute_batch'):
        sql = ("insert into rss_sources(id,court_code,url,label,last_polled) values(?,?,?,?,?) "
               "on conflict(id) do update set court_code=excluded.court_code, url=excluded.url, "
               "label=excluded.label, last_polled=excluded.last_polled")
        statements = [(sql, (s["id"], s.get("court_code"), s["url"], s.get("label"), s.get("last_polled"))) for s in sources]
        conn.execute_batch(statements)
    else:
        for s in sources:
            upsert_rss_source(s)

def update_rss_source_poll_time(source_id: str, ts: str):
    conn = get_conn()
    with conn:
        conn.execute("update rss_sources set last_polled=? where id=?", (ts, source_id))

def list_rss_sources():
    conn = get_conn()
    cur = conn.execute("select * from rss_sources order by label")
    return [dict(r) for r in cur.fetchall()]

def insert_rss_items(items: Iterable[Dict[str, Any]]):
    conn = get_conn()
    with conn:
        for it in items:
            conn.execute(
                "insert into rss_items(id,source_id,court_code,case_number,case_type,judge_name,nature_of_suit,title,summary,link,published,created_at,metadata_json) "
                "values(?,?,?,?,?,?,?,?,?,?,?,?,?) on conflict(id) do nothing",
                (it["id"], it.get("source_id"), it.get("court_code"), it.get("case_number"), it.get("case_type"),
                 it.get("judge_name"), it.get("nature_of_suit"), it.get("title"), it.get("summary"), it.get("link"), it.get("published"), it.get("created_at"), it.get("metadata_json"))
            )

def list_rss_items(
    court_code: str | None = None,
    courts: list[str] | None = None,
    case_type: str | None = None,
    nature_of_suit: str | None = None,
    keyword: str | None = None,
    new_only: bool = False,
    limit: int = 50
):
    conn = get_conn()
    conditions = []
    params = []

    # Single court filter
    if court_code:
        conditions.append("court_code=?")
        params.append(court_code)
    # Multiple courts filter
    elif courts:
        placeholders = ",".join(["?"] * len(courts))
        conditions.append(f"court_code IN ({placeholders})")
        params.extend(courts)

    if case_type:
        conditions.append("case_type=?")
        params.append(case_type)

    if nature_of_suit:
        conditions.append("nature_of_suit LIKE ?")
        params.append(f"%{nature_of_suit}%")

    if keyword:
        conditions.append("(title LIKE ? OR summary LIKE ? OR case_number LIKE ?)")
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw])

    if new_only:
        conditions.append("json_extract(metadata_json, '$.is_new_case') = 1")

    where_clause = " where " + " and ".join(conditions) if conditions else ""
    params.append(limit)

    cur = conn.execute(
        f"select * from rss_items{where_clause} order by created_at desc limit ?",
        tuple(params)
    )
    return [dict(r) for r in cur.fetchall()]


# --- RECAP/CourtListener helpers ---

def upsert_recap_docket(docket: Dict[str, Any]):
    """Insert or update a RECAP docket record."""
    conn = get_conn()
    fields = ["id", "cl_docket_id", "court_code", "docket_number", "case_name",
              "date_filed", "date_terminated", "nature_of_suit", "cause",
              "jury_demand", "assigned_to", "referred_to", "party_count",
              "attorney_count", "entry_count", "last_enriched"]
    values = [docket.get(k) for k in fields]
    placeholders = ",".join(["?"] * len(fields))
    # Update all fields except the keys on conflict
    update_fields = ",".join([f"{f}=excluded.{f}" for f in fields if f not in ("id", "court_code", "docket_number")])
    with conn:
        conn.execute(
            f"insert into recap_dockets({','.join(fields)}) values({placeholders}) "
            f"on conflict(court_code, docket_number) do update set {update_fields}",
            values
        )


def get_recap_docket(court_code: str, docket_number: str) -> Optional[Dict[str, Any]]:
    """Get a RECAP docket by court code and docket number."""
    conn = get_conn()
    cur = conn.execute(
        "select * from recap_dockets where court_code=? and docket_number=?",
        (court_code, docket_number)
    )
    row = cur.fetchone()
    return dict(row) if row else None


def get_recap_docket_by_id(docket_id: str) -> Optional[Dict[str, Any]]:
    """Get a RECAP docket by internal ID."""
    conn = get_conn()
    cur = conn.execute("select * from recap_dockets where id=?", (docket_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def insert_recap_parties(parties: Iterable[Dict[str, Any]]):
    """Insert RECAP party records."""
    conn = get_conn()
    with conn:
        for p in parties:
            conn.execute(
                "insert into recap_parties(id,docket_id,cl_party_id,name,party_type,extra_info,date_terminated) "
                "values(?,?,?,?,?,?,?) on conflict(id) do update set "
                "name=excluded.name, party_type=excluded.party_type, extra_info=excluded.extra_info, date_terminated=excluded.date_terminated",
                (p["id"], p.get("docket_id"), p.get("cl_party_id"), p.get("name"),
                 p.get("party_type"), p.get("extra_info"), p.get("date_terminated"))
            )


def get_recap_parties(docket_id: str) -> list:
    """Get parties for a RECAP docket."""
    conn = get_conn()
    cur = conn.execute(
        "select * from recap_parties where docket_id=? order by party_type, name",
        (docket_id,)
    )
    return [dict(r) for r in cur.fetchall()]


def search_recap_parties(name: str, court_code: str = None, limit: int = 50) -> list:
    """Search parties by name."""
    conn = get_conn()
    params = [f"%{name}%"]
    sql = "select p.*, d.court_code, d.docket_number, d.case_name from recap_parties p join recap_dockets d on p.docket_id = d.id where p.name like ?"
    if court_code:
        sql += " and d.court_code = ?"
        params.append(court_code)
    sql += " order by p.name limit ?"
    params.append(limit)
    cur = conn.execute(sql, tuple(params))
    return [dict(r) for r in cur.fetchall()]


def insert_recap_attorneys(attorneys: Iterable[Dict[str, Any]]):
    """Insert RECAP attorney records."""
    conn = get_conn()
    with conn:
        for a in attorneys:
            conn.execute(
                "insert into recap_attorneys(id,docket_id,party_id,cl_attorney_id,name,firm,phone,email,roles) "
                "values(?,?,?,?,?,?,?,?,?) on conflict(id) do update set "
                "name=excluded.name, firm=excluded.firm, phone=excluded.phone, email=excluded.email, roles=excluded.roles",
                (a["id"], a.get("docket_id"), a.get("party_id"), a.get("cl_attorney_id"),
                 a.get("name"), a.get("firm"), a.get("phone"), a.get("email"), a.get("roles"))
            )


def get_recap_attorneys(docket_id: str) -> list:
    """Get attorneys for a RECAP docket."""
    conn = get_conn()
    cur = conn.execute(
        "select * from recap_attorneys where docket_id=? order by name",
        (docket_id,)
    )
    return [dict(r) for r in cur.fetchall()]


def insert_recap_entries(entries: Iterable[Dict[str, Any]]):
    """Insert RECAP docket entry records."""
    conn = get_conn()
    with conn:
        for e in entries:
            conn.execute(
                "insert into recap_entries(id,docket_id,cl_entry_id,entry_number,date_filed,description,document_count,pacer_doc_id,recap_document_id) "
                "values(?,?,?,?,?,?,?,?,?) on conflict(id) do update set "
                "description=excluded.description, document_count=excluded.document_count",
                (e["id"], e.get("docket_id"), e.get("cl_entry_id"), e.get("entry_number"),
                 e.get("date_filed"), e.get("description"), e.get("document_count"),
                 e.get("pacer_doc_id"), e.get("recap_document_id"))
            )


def get_recap_entries(docket_id: str, limit: int = 500) -> list:
    """Get docket entries for a RECAP docket."""
    conn = get_conn()
    cur = conn.execute(
        "select * from recap_entries where docket_id=? order by entry_number limit ?",
        (docket_id, limit)
    )
    return [dict(r) for r in cur.fetchall()]


def insert_recap_documents(documents: Iterable[Dict[str, Any]]):
    """Insert RECAP document records."""
    conn = get_conn()
    with conn:
        for d in documents:
            conn.execute(
                "insert into recap_documents(id,entry_id,cl_document_id,document_number,attachment_number,description,page_count,filepath_local,is_available,sha1) "
                "values(?,?,?,?,?,?,?,?,?,?) on conflict(id) do update set "
                "is_available=excluded.is_available, filepath_local=excluded.filepath_local",
                (d["id"], d.get("entry_id"), d.get("cl_document_id"), d.get("document_number"),
                 d.get("attachment_number"), d.get("description"), d.get("page_count"),
                 d.get("filepath_local"), int(d.get("is_available", False)), d.get("sha1"))
            )


def get_recap_documents(entry_id: str) -> list:
    """Get documents for a RECAP docket entry."""
    conn = get_conn()
    cur = conn.execute(
        "select * from recap_documents where entry_id=? order by document_number, attachment_number",
        (entry_id,)
    )
    return [dict(r) for r in cur.fetchall()]


def list_unenriched_cases(limit: int = 100) -> list:
    """Get RSS items that haven't been enriched from RECAP yet."""
    conn = get_conn()
    cur = conn.execute("""
        select distinct r.court_code, r.case_number
        from rss_items r
        left join recap_dockets d on r.court_code = d.court_code and r.case_number = d.docket_number
        where r.case_number is not null
          and d.id is null
        order by r.published desc
        limit ?
    """, (limit,))
    return [dict(r) for r in cur.fetchall()]


def get_recap_stats() -> Dict[str, Any]:
    """Get statistics about RECAP enrichment."""
    conn = get_conn()
    stats = {}

    cur = conn.execute("select count(*) as cnt from recap_dockets")
    row = cur.fetchone()
    stats["enriched_dockets"] = dict(row)["cnt"] if row else 0

    cur = conn.execute("select count(*) as cnt from recap_parties")
    row = cur.fetchone()
    stats["total_parties"] = dict(row)["cnt"] if row else 0

    cur = conn.execute("select count(*) as cnt from recap_attorneys")
    row = cur.fetchone()
    stats["total_attorneys"] = dict(row)["cnt"] if row else 0

    cur = conn.execute("select count(*) as cnt from recap_entries")
    row = cur.fetchone()
    stats["total_entries"] = dict(row)["cnt"] if row else 0

    cur = conn.execute("select count(*) as cnt from recap_documents where is_available = 1")
    row = cur.fetchone()
    stats["available_documents"] = dict(row)["cnt"] if row else 0

    # Count unenriched cases
    cur = conn.execute("""
        select count(distinct case_number) as cnt
        from rss_items r
        left join recap_dockets d on r.court_code = d.court_code and r.case_number = d.docket_number
        where r.case_number is not null and d.id is null
    """)
    row = cur.fetchone()
    stats["unenriched_cases"] = dict(row)["cnt"] if row else 0

    return stats


# --- Firm Analytics ---

def get_firm_stats(limit: int = 50, court_code: str = None) -> list:
    """
    Get statistics on law firms by case count.

    Returns firms ranked by number of cases they appear in.
    """
    conn = get_conn()

    if court_code:
        cur = conn.execute("""
            select
                a.firm,
                count(distinct a.docket_id) as case_count,
                count(distinct a.id) as attorney_count,
                group_concat(distinct d.court_code) as courts
            from recap_attorneys a
            join recap_dockets d on a.docket_id = d.id
            where a.firm is not null
              and a.firm != ''
              and d.court_code = ?
            group by a.firm
            order by case_count desc
            limit ?
        """, (court_code, limit))
    else:
        cur = conn.execute("""
            select
                a.firm,
                count(distinct a.docket_id) as case_count,
                count(distinct a.id) as attorney_count,
                group_concat(distinct d.court_code) as courts
            from recap_attorneys a
            join recap_dockets d on a.docket_id = d.id
            where a.firm is not null and a.firm != ''
            group by a.firm
            order by case_count desc
            limit ?
        """, (limit,))

    return [dict(r) for r in cur.fetchall()]


def get_firm_details(firm_name: str) -> Dict[str, Any]:
    """
    Get detailed information about a specific law firm.

    Returns firm stats, attorneys, and recent cases.
    """
    conn = get_conn()

    # Get attorneys at this firm
    cur = conn.execute("""
        select distinct a.name, a.email, a.phone
        from recap_attorneys a
        where a.firm = ?
        order by a.name
    """, (firm_name,))
    attorneys = [dict(r) for r in cur.fetchall()]

    # Get cases this firm appears in
    cur = conn.execute("""
        select distinct
            d.court_code,
            d.docket_number,
            d.case_name,
            d.date_filed,
            d.nature_of_suit,
            p.party_type
        from recap_attorneys a
        join recap_dockets d on a.docket_id = d.id
        left join recap_parties p on a.party_id = p.id
        where a.firm = ?
        order by d.date_filed desc
        limit 100
    """, (firm_name,))
    cases = [dict(r) for r in cur.fetchall()]

    # Get stats
    cur = conn.execute("""
        select
            count(distinct a.docket_id) as total_cases,
            count(distinct a.id) as total_attorneys,
            count(distinct d.court_code) as courts_active,
            min(d.date_filed) as earliest_case,
            max(d.date_filed) as latest_case
        from recap_attorneys a
        join recap_dockets d on a.docket_id = d.id
        where a.firm = ?
    """, (firm_name,))
    stats = dict(cur.fetchone()) if cur else {}

    # Get case type distribution
    cur = conn.execute("""
        select
            d.nature_of_suit,
            count(distinct d.id) as count
        from recap_attorneys a
        join recap_dockets d on a.docket_id = d.id
        where a.firm = ? and d.nature_of_suit is not null
        group by d.nature_of_suit
        order by count desc
    """, (firm_name,))
    case_types = [dict(r) for r in cur.fetchall()]

    # Get party side distribution (plaintiff vs defendant representation)
    cur = conn.execute("""
        select
            case
                when lower(p.party_type) like '%plaintiff%' then 'Plaintiff'
                when lower(p.party_type) like '%defendant%' then 'Defendant'
                when lower(p.party_type) like '%petitioner%' then 'Petitioner'
                when lower(p.party_type) like '%respondent%' then 'Respondent'
                else 'Other'
            end as side,
            count(distinct d.id) as count
        from recap_attorneys a
        join recap_dockets d on a.docket_id = d.id
        join recap_parties p on a.party_id = p.id
        where a.firm = ?
        group by side
        order by count desc
    """, (firm_name,))
    sides = [dict(r) for r in cur.fetchall()]

    return {
        "firm": firm_name,
        "stats": stats,
        "attorneys": attorneys,
        "case_types": case_types,
        "representation_sides": sides,
        "recent_cases": cases
    }


def search_firms(query: str, limit: int = 50) -> list:
    """Search for law firms by name."""
    conn = get_conn()
    cur = conn.execute("""
        select
            a.firm,
            count(distinct a.docket_id) as case_count,
            count(distinct a.id) as attorney_count
        from recap_attorneys a
        where a.firm like ?
        group by a.firm
        order by case_count desc
        limit ?
    """, (f"%{query}%", limit))
    return [dict(r) for r in cur.fetchall()]


def get_firm_comparison(firms: List[str]) -> list:
    """Compare multiple firms side by side."""
    conn = get_conn()
    results = []

    for firm in firms:
        cur = conn.execute("""
            select
                count(distinct a.docket_id) as case_count,
                count(distinct a.id) as attorney_count,
                count(distinct d.court_code) as courts_active
            from recap_attorneys a
            join recap_dockets d on a.docket_id = d.id
            where a.firm = ?
        """, (firm,))
        row = cur.fetchone()
        if row:
            results.append({
                "firm": firm,
                **dict(row)
            })

    return results


def get_attorney_stats(limit: int = 50, court_code: str = None) -> list:
    """
    Get statistics on individual attorneys by case count.
    """
    conn = get_conn()

    if court_code:
        cur = conn.execute("""
            select
                a.name,
                a.firm,
                a.email,
                count(distinct a.docket_id) as case_count
            from recap_attorneys a
            join recap_dockets d on a.docket_id = d.id
            where a.name is not null
              and a.name != ''
              and d.court_code = ?
            group by a.name, a.firm
            order by case_count desc
            limit ?
        """, (court_code, limit))
    else:
        cur = conn.execute("""
            select
                a.name,
                a.firm,
                a.email,
                count(distinct a.docket_id) as case_count
            from recap_attorneys a
            where a.name is not null and a.name != ''
            group by a.name, a.firm
            order by case_count desc
            limit ?
        """, (limit,))

    return [dict(r) for r in cur.fetchall()]


def get_court_firm_activity(court_code: str, limit: int = 25) -> Dict[str, Any]:
    """
    Get firm activity statistics for a specific court.
    """
    conn = get_conn()

    # Top firms
    cur = conn.execute("""
        select
            a.firm,
            count(distinct a.docket_id) as case_count,
            count(distinct a.id) as attorney_count
        from recap_attorneys a
        join recap_dockets d on a.docket_id = d.id
        where a.firm is not null
          and a.firm != ''
          and d.court_code = ?
        group by a.firm
        order by case_count desc
        limit ?
    """, (court_code, limit))
    top_firms = [dict(r) for r in cur.fetchall()]

    # Total stats
    cur = conn.execute("""
        select
            count(distinct a.firm) as unique_firms,
            count(distinct a.id) as total_attorneys,
            count(distinct d.id) as total_cases
        from recap_attorneys a
        join recap_dockets d on a.docket_id = d.id
        where d.court_code = ?
    """, (court_code,))
    stats = dict(cur.fetchone()) if cur else {}

    return {
        "court_code": court_code,
        "stats": stats,
        "top_firms": top_firms
    }


# --- Newsletter CRUD Operations ---

def create_newsletter(
    name: str,
    schedule: str,
    description: str = None,
    court_codes: List[str] = None,
    case_types: List[str] = None,
    keywords: List[str] = None,
    min_relevance_score: float = 0.5,
    max_items: int = 20,
    output_channels: List[str] = None
) -> Dict[str, Any]:
    """Create a new newsletter configuration."""
    import json
    import uuid
    from datetime import datetime

    conn = get_conn()
    newsletter_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    conn.execute("""
        INSERT INTO newsletters (id, name, description, schedule, court_codes, case_types,
                                 keywords, min_relevance_score, max_items, output_channels, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        newsletter_id, name, description, schedule,
        json.dumps(court_codes) if court_codes else None,
        json.dumps(case_types) if case_types else None,
        json.dumps(keywords) if keywords else None,
        min_relevance_score, max_items,
        json.dumps(output_channels or ["email", "rss", "web"]),
        now
    ))

    # For SQLite, commit and return data directly
    if hasattr(conn, 'commit'):
        conn.commit()

    return {
        "id": newsletter_id,
        "name": name,
        "description": description,
        "schedule": schedule,
        "court_codes": court_codes,
        "case_types": case_types,
        "keywords": keywords,
        "min_relevance_score": min_relevance_score,
        "max_items": max_items,
        "output_channels": output_channels or ["email", "rss", "web"],
        "created_at": now,
        "is_active": 1
    }


def get_newsletter(newsletter_id: str) -> Optional[Dict[str, Any]]:
    """Get a newsletter by ID."""
    import json
    conn = get_conn()
    cur = conn.execute("SELECT * FROM newsletters WHERE id = ?", (newsletter_id,))
    row = cur.fetchone()
    if not row:
        return None
    result = dict(row)
    # Parse JSON fields
    for field in ['court_codes', 'case_types', 'keywords', 'output_channels']:
        if result.get(field):
            try:
                result[field] = json.loads(result[field])
            except:
                pass
    return result


def list_newsletters(active_only: bool = True) -> List[Dict[str, Any]]:
    """List all newsletters."""
    import json
    conn = get_conn()
    sql = "SELECT * FROM newsletters"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY created_at DESC"
    cur = conn.execute(sql)
    results = []
    for row in cur.fetchall():
        result = dict(row)
        for field in ['court_codes', 'case_types', 'keywords', 'output_channels']:
            if result.get(field):
                try:
                    result[field] = json.loads(result[field])
                except:
                    pass
        results.append(result)
    return results


def update_newsletter(newsletter_id: str, **updates) -> Optional[Dict[str, Any]]:
    """Update a newsletter configuration."""
    import json
    from datetime import datetime

    conn = get_conn()
    # JSON encode list fields
    for field in ['court_codes', 'case_types', 'keywords', 'output_channels']:
        if field in updates and isinstance(updates[field], list):
            updates[field] = json.dumps(updates[field])

    updates['updated_at'] = datetime.utcnow().isoformat()

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [newsletter_id]

    conn.execute(f"UPDATE newsletters SET {set_clause} WHERE id = ?", tuple(values))
    return get_newsletter(newsletter_id)


def delete_newsletter(newsletter_id: str) -> bool:
    """Delete a newsletter (soft delete by setting inactive)."""
    conn = get_conn()
    conn.execute("UPDATE newsletters SET is_active = 0 WHERE id = ?", (newsletter_id,))
    return True


def create_newsletter_issue(
    newsletter_id: str,
    title: str,
    summary_text: str = None,
    html_content: str = None,
    item_count: int = 0
) -> Dict[str, Any]:
    """Create a new newsletter issue."""
    import uuid
    from datetime import datetime

    conn = get_conn()
    issue_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # Get next issue number
    cur = conn.execute(
        "SELECT COALESCE(MAX(issue_number), 0) + 1 FROM newsletter_issues WHERE newsletter_id = ?",
        (newsletter_id,)
    )
    issue_number = cur.fetchone()[0]

    conn.execute("""
        INSERT INTO newsletter_issues (id, newsletter_id, issue_number, title, summary_text,
                                       html_content, item_count, status, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?)
    """, (issue_id, newsletter_id, issue_number, title, summary_text, html_content, item_count, now))

    return get_newsletter_issue(issue_id)


def get_newsletter_issue(issue_id: str) -> Optional[Dict[str, Any]]:
    """Get a newsletter issue by ID."""
    conn = get_conn()
    cur = conn.execute("SELECT * FROM newsletter_issues WHERE id = ?", (issue_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def list_newsletter_issues(newsletter_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """List issues for a newsletter."""
    conn = get_conn()
    cur = conn.execute("""
        SELECT * FROM newsletter_issues
        WHERE newsletter_id = ?
        ORDER BY issue_number DESC
        LIMIT ?
    """, (newsletter_id, limit))
    return [dict(row) for row in cur.fetchall()]


def update_newsletter_issue(issue_id: str, **updates) -> Optional[Dict[str, Any]]:
    """Update a newsletter issue."""
    conn = get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [issue_id]
    conn.execute(f"UPDATE newsletter_issues SET {set_clause} WHERE id = ?", tuple(values))
    return get_newsletter_issue(issue_id)


def add_newsletter_item(
    issue_id: str,
    rss_item_id: str,
    relevance_score: float,
    ai_summary: str = None,
    ai_reasoning: str = None,
    document_source: str = None,
    display_order: int = 0
) -> str:
    """Add an item to a newsletter issue."""
    import uuid
    from datetime import datetime

    conn = get_conn()
    item_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    conn.execute("""
        INSERT INTO newsletter_items (id, issue_id, rss_item_id, relevance_score,
                                      ai_summary, ai_reasoning, document_source, display_order, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (item_id, issue_id, rss_item_id, relevance_score, ai_summary, ai_reasoning,
          document_source, display_order, now))

    return item_id


def get_newsletter_items(issue_id: str) -> List[Dict[str, Any]]:
    """Get all items for a newsletter issue with RSS item details."""
    conn = get_conn()
    cur = conn.execute("""
        SELECT ni.*, ri.court_code, ri.case_number, ri.title, ri.summary, ri.link,
               ri.published, ri.case_type, ri.judge_name, ri.nature_of_suit
        FROM newsletter_items ni
        JOIN rss_items ri ON ni.rss_item_id = ri.id
        WHERE ni.issue_id = ?
        ORDER BY ni.display_order ASC, ni.relevance_score DESC
    """, (issue_id,))
    return [dict(row) for row in cur.fetchall()]


def create_subscriber(email: str, name: str = None) -> Dict[str, Any]:
    """Create a new subscriber."""
    import uuid
    import secrets
    from datetime import datetime

    conn = get_conn()
    subscriber_id = str(uuid.uuid4())
    verification_token = secrets.token_urlsafe(32)
    now = datetime.utcnow().isoformat()

    conn.execute("""
        INSERT INTO subscribers (id, email, name, verification_token, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (subscriber_id, email, name, verification_token, now))

    return {"id": subscriber_id, "email": email, "name": name, "verification_token": verification_token}


def get_subscriber(subscriber_id: str = None, email: str = None) -> Optional[Dict[str, Any]]:
    """Get a subscriber by ID or email."""
    conn = get_conn()
    if subscriber_id:
        cur = conn.execute("SELECT * FROM subscribers WHERE id = ?", (subscriber_id,))
    elif email:
        cur = conn.execute("SELECT * FROM subscribers WHERE email = ?", (email,))
    else:
        return None
    row = cur.fetchone()
    return dict(row) if row else None


def verify_subscriber(token: str) -> Optional[Dict[str, Any]]:
    """Verify a subscriber by token."""
    conn = get_conn()
    conn.execute("UPDATE subscribers SET is_verified = 1 WHERE verification_token = ?", (token,))
    cur = conn.execute("SELECT * FROM subscribers WHERE verification_token = ?", (token,))
    row = cur.fetchone()
    return dict(row) if row else None


def subscribe_to_newsletter(subscriber_id: str, newsletter_id: str) -> bool:
    """Subscribe a user to a newsletter."""
    import uuid
    from datetime import datetime

    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO newsletter_subscriptions (id, newsletter_id, subscriber_id, created_at)
            VALUES (?, ?, ?, ?)
        """, (str(uuid.uuid4()), newsletter_id, subscriber_id, datetime.utcnow().isoformat()))
        return True
    except:
        return False  # Already subscribed


def get_newsletter_subscribers(newsletter_id: str, verified_only: bool = True) -> List[Dict[str, Any]]:
    """Get all subscribers for a newsletter."""
    conn = get_conn()
    sql = """
        SELECT s.* FROM subscribers s
        JOIN newsletter_subscriptions ns ON s.id = ns.subscriber_id
        WHERE ns.newsletter_id = ? AND s.unsubscribed_at IS NULL
    """
    if verified_only:
        sql += " AND s.is_verified = 1"
    cur = conn.execute(sql, (newsletter_id,))
    return [dict(row) for row in cur.fetchall()]


# --- Motion Event CRUD and Analytics ---

def insert_motion_event(event: Dict[str, Any]) -> str:
    """Insert a motion event record."""
    import uuid
    from datetime import datetime

    conn = get_conn()
    event_id = event.get("id") or str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    conn.execute("""
        INSERT INTO motion_events (id, docket_id, entry_id, motion_type, filed_by,
                                   filing_attorney_id, filing_firm, filed_date, outcome,
                                   outcome_date, outcome_entry_id, court_code, case_type,
                                   case_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            outcome = excluded.outcome,
            outcome_date = excluded.outcome_date,
            outcome_entry_id = excluded.outcome_entry_id
    """, (
        event_id, event.get("docket_id"), event.get("entry_id"), event.get("motion_type"),
        event.get("filed_by"), event.get("filing_attorney_id"), event.get("filing_firm"),
        event.get("filed_date"), event.get("outcome"), event.get("outcome_date"),
        event.get("outcome_entry_id"), event.get("court_code"), event.get("case_type"),
        event.get("case_name"), now
    ))
    return event_id


def insert_motion_events_batch(events: List[Dict[str, Any]]):
    """Insert multiple motion events."""
    for event in events:
        insert_motion_event(event)


def update_motion_outcome(motion_id: str, outcome: str, outcome_date: str, outcome_entry_id: str = None):
    """Update the outcome of a motion event."""
    conn = get_conn()
    conn.execute("""
        UPDATE motion_events
        SET outcome = ?, outcome_date = ?, outcome_entry_id = ?
        WHERE id = ?
    """, (outcome, outcome_date, outcome_entry_id, motion_id))


def get_motion_events(docket_id: str = None, firm: str = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Get motion events with optional filters."""
    conn = get_conn()
    conditions = []
    params = []

    if docket_id:
        conditions.append("docket_id = ?")
        params.append(docket_id)
    if firm:
        conditions.append("filing_firm = ?")
        params.append(firm)

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit)

    cur = conn.execute(f"""
        SELECT * FROM motion_events
        {where_clause}
        ORDER BY filed_date DESC
        LIMIT ?
    """, tuple(params))
    return [dict(row) for row in cur.fetchall()]


def get_pending_motions(docket_id: str = None) -> List[Dict[str, Any]]:
    """Get motions that don't have outcomes yet."""
    conn = get_conn()
    if docket_id:
        cur = conn.execute("""
            SELECT * FROM motion_events
            WHERE outcome IS NULL AND docket_id = ?
            ORDER BY filed_date DESC
        """, (docket_id,))
    else:
        cur = conn.execute("""
            SELECT * FROM motion_events
            WHERE outcome IS NULL
            ORDER BY filed_date DESC
            LIMIT 500
        """)
    return [dict(row) for row in cur.fetchall()]


# --- Firm Motion Analytics ---

def get_firm_motion_stats(firm_name: str = None, court_code: str = None, motion_type: str = None) -> Dict[str, Any]:
    """
    Get motion success rates by firm.

    Returns aggregated stats with grant rates and confidence intervals.
    """
    import math
    conn = get_conn()

    conditions = ["outcome IS NOT NULL"]
    params = []

    if firm_name:
        conditions.append("filing_firm = ?")
        params.append(firm_name)
    if court_code:
        conditions.append("court_code = ?")
        params.append(court_code)
    if motion_type:
        conditions.append("motion_type = ?")
        params.append(motion_type)

    where_clause = " AND ".join(conditions)

    # Overall stats
    cur = conn.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN outcome = 'granted' THEN 1 ELSE 0 END) as granted,
            SUM(CASE WHEN outcome = 'denied' THEN 1 ELSE 0 END) as denied,
            SUM(CASE WHEN outcome = 'partial' THEN 1 ELSE 0 END) as partial,
            SUM(CASE WHEN outcome = 'moot' THEN 1 ELSE 0 END) as moot
        FROM motion_events
        WHERE {where_clause}
    """, tuple(params))

    row = cur.fetchone()
    stats = dict(row) if row else {"total": 0, "granted": 0, "denied": 0, "partial": 0, "moot": 0}

    # Calculate grant rate with Wilson confidence interval
    total = stats["total"] or 0
    granted = stats["granted"] or 0

    if total > 0:
        grant_rate = round(granted / total * 100, 1)
        # Wilson score interval
        z = 1.96  # 95% confidence
        p = granted / total
        denominator = 1 + z*z/total
        center = (p + z*z/(2*total)) / denominator
        spread = z * math.sqrt((p*(1-p) + z*z/(4*total)) / total) / denominator
        ci_low = max(0, round((center - spread) * 100, 1))
        ci_high = min(100, round((center + spread) * 100, 1))
    else:
        grant_rate = 0
        ci_low = 0
        ci_high = 0

    stats["grant_rate"] = grant_rate
    stats["ci_95"] = [ci_low, ci_high]

    # Stats by motion type
    cur = conn.execute(f"""
        SELECT
            motion_type,
            COUNT(*) as total,
            SUM(CASE WHEN outcome = 'granted' THEN 1 ELSE 0 END) as granted,
            SUM(CASE WHEN outcome = 'denied' THEN 1 ELSE 0 END) as denied
        FROM motion_events
        WHERE {where_clause}
        GROUP BY motion_type
        ORDER BY total DESC
    """, tuple(params))

    by_type = []
    for r in cur.fetchall():
        row_dict = dict(r)
        t = row_dict["total"] or 0
        g = row_dict["granted"] or 0
        row_dict["grant_rate"] = round(g / t * 100, 1) if t > 0 else 0
        by_type.append(row_dict)

    stats["by_type"] = by_type

    return stats


def get_firm_practice_areas(firm_name: str) -> List[Dict[str, Any]]:
    """Get case type/nature of suit breakdown for a firm."""
    conn = get_conn()

    # From motion events
    cur = conn.execute("""
        SELECT
            COALESCE(case_type, 'unknown') as case_type,
            COUNT(DISTINCT docket_id) as case_count
        FROM motion_events
        WHERE filing_firm = ?
        GROUP BY case_type
        ORDER BY case_count DESC
    """, (firm_name,))

    results = [dict(r) for r in cur.fetchall()]
    total = sum(r["case_count"] for r in results)

    for r in results:
        r["pct"] = round(r["case_count"] / total * 100, 1) if total > 0 else 0

    return results


def get_firm_court_presence(firm_name: str) -> List[Dict[str, Any]]:
    """Get courts where firm has motion activity."""
    conn = get_conn()

    cur = conn.execute("""
        SELECT
            court_code,
            COUNT(*) as motion_count,
            COUNT(DISTINCT docket_id) as case_count,
            SUM(CASE WHEN outcome = 'granted' THEN 1 ELSE 0 END) as granted,
            SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) as decided
        FROM motion_events
        WHERE filing_firm = ?
        GROUP BY court_code
        ORDER BY motion_count DESC
    """, (firm_name,))

    results = []
    for r in cur.fetchall():
        row_dict = dict(r)
        decided = row_dict["decided"] or 0
        granted = row_dict["granted"] or 0
        row_dict["grant_rate"] = round(granted / decided * 100, 1) if decided > 0 else None
        results.append(row_dict)

    return results


def get_top_firms_by_success(motion_type: str = None, court_code: str = None,
                              min_motions: int = 10, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Rank firms by motion success rate.

    Uses Wilson score confidence interval lower bound for ranking
    to avoid small sample size bias.
    """
    import math
    conn = get_conn()

    conditions = ["filing_firm IS NOT NULL", "filing_firm != ''", "outcome IS NOT NULL"]
    params = []

    if motion_type:
        conditions.append("motion_type = ?")
        params.append(motion_type)
    if court_code:
        conditions.append("court_code = ?")
        params.append(court_code)

    where_clause = " AND ".join(conditions)
    params.extend([min_motions, limit])

    cur = conn.execute(f"""
        SELECT
            filing_firm,
            COUNT(*) as total,
            SUM(CASE WHEN outcome = 'granted' THEN 1 ELSE 0 END) as granted,
            SUM(CASE WHEN outcome = 'denied' THEN 1 ELSE 0 END) as denied
        FROM motion_events
        WHERE {where_clause}
        GROUP BY filing_firm
        HAVING COUNT(*) >= ?
        ORDER BY CAST(SUM(CASE WHEN outcome = 'granted' THEN 1 ELSE 0 END) AS REAL) / COUNT(*) DESC
        LIMIT ?
    """, tuple(params))

    results = []
    z = 1.96  # 95% confidence

    for r in cur.fetchall():
        row_dict = dict(r)
        total = row_dict["total"]
        granted = row_dict["granted"]
        p = granted / total if total > 0 else 0

        # Wilson score interval
        denominator = 1 + z*z/total
        center = (p + z*z/(2*total)) / denominator
        spread = z * math.sqrt((p*(1-p) + z*z/(4*total)) / total) / denominator

        row_dict["grant_rate"] = round(p * 100, 1)
        row_dict["ci_low"] = max(0, round((center - spread) * 100, 1))
        row_dict["ci_high"] = min(100, round((center + spread) * 100, 1))
        # Use lower bound of CI for ranking (more conservative)
        row_dict["wilson_score"] = row_dict["ci_low"]
        results.append(row_dict)

    # Re-sort by Wilson score (lower bound)
    results.sort(key=lambda x: x["wilson_score"], reverse=True)

    # Add rank
    for i, r in enumerate(results):
        r["rank"] = i + 1

    return results


def get_motion_analytics_summary(court_code: str = None) -> Dict[str, Any]:
    """Get overall motion analytics summary."""
    conn = get_conn()

    conditions = []
    params = []

    if court_code:
        conditions.append("court_code = ?")
        params.append(court_code)

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    # Overall counts
    cur = conn.execute(f"""
        SELECT
            COUNT(*) as total_motions,
            COUNT(DISTINCT filing_firm) as unique_firms,
            COUNT(DISTINCT docket_id) as unique_cases,
            SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) as decided,
            SUM(CASE WHEN outcome = 'granted' THEN 1 ELSE 0 END) as granted,
            SUM(CASE WHEN outcome = 'denied' THEN 1 ELSE 0 END) as denied
        FROM motion_events
        {where_clause}
    """, tuple(params))

    stats = dict(cur.fetchone()) if cur else {}

    # By motion type
    cur = conn.execute(f"""
        SELECT
            motion_type,
            COUNT(*) as total,
            SUM(CASE WHEN outcome = 'granted' THEN 1 ELSE 0 END) as granted
        FROM motion_events
        {where_clause}
        GROUP BY motion_type
        ORDER BY total DESC
    """, tuple(params))

    by_type = []
    for r in cur.fetchall():
        row_dict = dict(r)
        t = row_dict["total"] or 0
        g = row_dict["granted"] or 0
        row_dict["grant_rate"] = round(g / t * 100, 1) if t > 0 else 0
        by_type.append(row_dict)

    stats["by_type"] = by_type

    return stats


# --- State Court Functions ---

def upsert_state_court_case(case: Dict[str, Any]) -> str:
    """Insert or update a state court case."""
    import json
    from datetime import datetime
    import uuid

    conn = get_conn()

    # Generate ID if not provided
    if not case.get("id"):
        key = f"{case.get('state', '')}:{case.get('county', '')}:{case.get('case_number', '')}"
        case["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, key))

    now = datetime.utcnow().isoformat() + "Z"
    if not case.get("created_at"):
        case["created_at"] = now
    case["updated_at"] = now

    # Serialize JSON fields
    if case.get("parties_json") and isinstance(case["parties_json"], (list, dict)):
        case["parties_json"] = json.dumps(case["parties_json"])
    if case.get("charges_json") and isinstance(case["charges_json"], (list, dict)):
        case["charges_json"] = json.dumps(case["charges_json"])
    if case.get("raw_data_json") and isinstance(case["raw_data_json"], (list, dict)):
        case["raw_data_json"] = json.dumps(case["raw_data_json"])

    fields = [
        "id", "state", "court_code", "county", "case_number", "case_style",
        "case_type", "case_type_code", "date_filed", "date_closed", "judge",
        "parties_json", "charges_json", "disposition", "data_source",
        "source_url", "raw_data_json", "created_at", "updated_at"
    ]
    values = [case.get(f) for f in fields]
    placeholders = ",".join(["?"] * len(fields))

    with conn:
        try:
            conn.execute(f"""
                INSERT INTO state_court_cases ({','.join(fields)})
                VALUES ({placeholders})
                ON CONFLICT(state, county, case_number) DO UPDATE SET
                    case_style = excluded.case_style,
                    case_type = excluded.case_type,
                    case_type_code = excluded.case_type_code,
                    date_closed = excluded.date_closed,
                    judge = excluded.judge,
                    parties_json = excluded.parties_json,
                    charges_json = excluded.charges_json,
                    disposition = excluded.disposition,
                    updated_at = excluded.updated_at
            """, tuple(values))
        except Exception:
            # Fallback for unique constraint violation
            pass

    return case["id"]


def insert_state_court_cases_batch(cases: List[Dict[str, Any]]) -> int:
    """Batch insert state court cases."""
    count = 0
    for case in cases:
        try:
            upsert_state_court_case(case)
            count += 1
        except Exception:
            pass
    return count


def get_state_court_case(state: str, county: str, case_number: str) -> Optional[Dict]:
    """Get a state court case by identifiers."""
    import json
    conn = get_conn()
    cur = conn.execute("""
        SELECT * FROM state_court_cases
        WHERE state = ? AND county = ? AND case_number = ?
    """, (state, county, case_number))
    row = cur.fetchone()
    if not row:
        return None

    result = dict(row)
    # Parse JSON fields
    for field in ["parties_json", "charges_json", "raw_data_json"]:
        if result.get(field):
            try:
                result[field] = json.loads(result[field])
            except Exception:
                pass
    return result


def search_state_court_cases(
    state: str = None,
    county: str = None,
    case_type: str = None,
    date_from: str = None,
    date_to: str = None,
    party_name: str = None,
    limit: int = 100
) -> List[Dict]:
    """Search state court cases."""
    conn = get_conn()

    conditions = []
    params = []

    if state:
        conditions.append("state = ?")
        params.append(state)
    if county:
        conditions.append("county = ?")
        params.append(county)
    if case_type:
        conditions.append("(case_type LIKE ? OR case_type_code = ?)")
        params.extend([f"%{case_type}%", case_type])
    if date_from:
        conditions.append("date_filed >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("date_filed <= ?")
        params.append(date_to)
    if party_name:
        conditions.append("(case_style LIKE ? OR parties_json LIKE ?)")
        params.extend([f"%{party_name}%", f"%{party_name}%"])

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    cur = conn.execute(f"""
        SELECT * FROM state_court_cases
        {where_clause}
        ORDER BY date_filed DESC
        LIMIT ?
    """, tuple(params + [limit]))

    return [dict(r) for r in cur.fetchall()]


def upsert_state_appellate_opinion(opinion: Dict[str, Any]) -> str:
    """Insert or update a state appellate opinion."""
    import uuid
    from datetime import datetime

    conn = get_conn()

    # Generate ID if not provided
    if not opinion.get("id"):
        key = f"{opinion.get('state', '')}:{opinion.get('citation', '')}:{opinion.get('case_name', '')}"
        opinion["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, key))

    if not opinion.get("created_at"):
        opinion["created_at"] = datetime.utcnow().isoformat() + "Z"

    fields = [
        "id", "state", "court", "case_name", "citation", "date_decided",
        "docket_number", "judges", "opinion_type", "opinion_text",
        "headnotes", "data_source", "source_url", "cl_opinion_id", "created_at"
    ]
    values = [opinion.get(f) for f in fields]
    placeholders = ",".join(["?"] * len(fields))

    # Insert into both state_appellate_opinions and state_court_opinions tables
    with conn:
        for table in ["state_appellate_opinions", "state_court_opinions"]:
            try:
                conn.execute(f"""
                    INSERT INTO {table} ({','.join(fields)})
                    VALUES ({placeholders})
                    ON CONFLICT(id) DO UPDATE SET
                        opinion_text = excluded.opinion_text,
                        headnotes = excluded.headnotes
                """, tuple(values))
            except Exception as e:
                import logging
                logging.error(f"Error inserting into {table}: {e}")

    return opinion["id"]


def search_state_appellate_opinions(
    state: str = None,
    court: str = None,
    search: str = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 50,
    offset: int = 0
) -> List[Dict]:
    """Search state appellate opinions."""
    conn = get_conn()

    conditions = []
    params = []

    if state:
        conditions.append("state = ?")
        params.append(state)
    if court:
        conditions.append("court LIKE ?")
        params.append(f"%{court}%")
    if search:
        conditions.append("(case_name LIKE ? OR opinion_text LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if date_from:
        conditions.append("date_decided >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("date_decided <= ?")
        params.append(date_to)

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    cur = conn.execute(f"""
        SELECT id, state, court, case_name, citation, date_decided,
               docket_number, judges, opinion_type, data_source, source_url
        FROM state_appellate_opinions
        {where_clause}
        ORDER BY date_decided DESC
        LIMIT ? OFFSET ?
    """, tuple(params + [limit, offset]))

    return [dict(r) for r in cur.fetchall()]


def get_state_court_stats(state: str = None) -> Dict[str, Any]:
    """Get state court statistics."""
    conn = get_conn()

    # Total cases by state
    cur = conn.execute("""
        SELECT state, COUNT(*) as case_count,
               COUNT(DISTINCT county) as counties,
               MIN(date_filed) as earliest_case,
               MAX(date_filed) as latest_case
        FROM state_court_cases
        GROUP BY state
        ORDER BY case_count DESC
    """)
    by_state = [dict(r) for r in cur.fetchall()]

    # Case type breakdown
    conditions = []
    params = []
    if state:
        conditions.append("state = ?")
        params.append(state)

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    cur = conn.execute(f"""
        SELECT case_type, COUNT(*) as count
        FROM state_court_cases
        {where_clause}
        GROUP BY case_type
        ORDER BY count DESC
        LIMIT 20
    """, tuple(params))
    by_type = [dict(r) for r in cur.fetchall()]

    # Appellate opinions by state
    cur = conn.execute("""
        SELECT state, COUNT(*) as opinion_count
        FROM state_appellate_opinions
        GROUP BY state
        ORDER BY opinion_count DESC
    """)
    appellate_by_state = [dict(r) for r in cur.fetchall()]

    total_cases = sum(s["case_count"] for s in by_state) if by_state else 0
    total_opinions = sum(s["opinion_count"] for s in appellate_by_state) if appellate_by_state else 0

    # Transform to expected format for dashboard
    by_state_formatted = [{"state": s["state"], "count": s["case_count"], "counties": s.get("counties", 0)} for s in by_state]
    opinions_formatted = [{"state": s["state"], "count": s["opinion_count"]} for s in appellate_by_state]
    by_type_formatted = [{"case_type": t["case_type"], "count": t["count"]} for t in by_type]

    # Calculate percentages for case types
    if total_cases > 0:
        for t in by_type_formatted:
            t["percentage"] = (t["count"] / total_cases) * 100

    # Count unique states with data
    states_with_cases = set(s["state"] for s in by_state)
    states_with_opinions = set(s["state"] for s in appellate_by_state)
    states_covered = len(states_with_cases | states_with_opinions)

    return {
        "cases_by_state": by_state,
        "cases_by_type": by_type,
        "appellate_by_state": appellate_by_state,
        "total_cases": total_cases,
        "total_opinions": total_opinions,
        # Dashboard-expected keys
        "by_state": by_state_formatted,
        "opinions_by_state": opinions_formatted,
        "by_case_type": by_type_formatted,
        "states_covered": states_covered
    }


# =============================================================================
# Scraper Run Tracking
# =============================================================================

def create_scraper_run(
    state: str,
    scraper_type: str,
    county: str = None
) -> str:
    """Create a new scraper run record."""
    import uuid
    from datetime import datetime

    run_id = str(uuid.uuid4())
    started_at = datetime.utcnow().isoformat() + "Z"

    conn = get_conn()
    with conn:
        conn.execute("""
            INSERT INTO scraper_runs (id, state, county, scraper_type, started_at, status)
            VALUES (?, ?, ?, ?, ?, 'running')
        """, (run_id, state, county, scraper_type, started_at))

    return run_id


def update_scraper_run(
    run_id: str,
    cases_found: int = None,
    cases_stored: int = None,
    entries_stored: int = None,
    errors_count: int = None,
    errors_json: str = None,
    last_sync_marker: str = None,
    status: str = None
) -> None:
    """Update a scraper run record."""
    from datetime import datetime

    conn = get_conn()

    updates = []
    params = []

    if cases_found is not None:
        updates.append("cases_found = ?")
        params.append(cases_found)
    if cases_stored is not None:
        updates.append("cases_stored = ?")
        params.append(cases_stored)
    if entries_stored is not None:
        updates.append("entries_stored = ?")
        params.append(entries_stored)
    if errors_count is not None:
        updates.append("errors_count = ?")
        params.append(errors_count)
    if errors_json is not None:
        updates.append("errors_json = ?")
        params.append(errors_json)
    if last_sync_marker is not None:
        updates.append("last_sync_marker = ?")
        params.append(last_sync_marker)
    if status is not None:
        updates.append("status = ?")
        params.append(status)
        if status in ("completed", "failed"):
            updates.append("completed_at = ?")
            params.append(datetime.utcnow().isoformat() + "Z")

    if updates:
        params.append(run_id)
        with conn:
            conn.execute(f"""
                UPDATE scraper_runs
                SET {', '.join(updates)}
                WHERE id = ?
            """, tuple(params))


def complete_scraper_run(
    run_id: str,
    cases_found: int,
    cases_stored: int,
    entries_stored: int = 0,
    errors_count: int = 0,
    errors_json: str = None,
    last_sync_marker: str = None
) -> None:
    """Mark a scraper run as completed."""
    update_scraper_run(
        run_id,
        cases_found=cases_found,
        cases_stored=cases_stored,
        entries_stored=entries_stored,
        errors_count=errors_count,
        errors_json=errors_json,
        last_sync_marker=last_sync_marker,
        status="completed"
    )


def fail_scraper_run(run_id: str, error_message: str) -> None:
    """Mark a scraper run as failed."""
    import json
    update_scraper_run(
        run_id,
        errors_json=json.dumps({"error": error_message}),
        errors_count=1,
        status="failed"
    )


def get_scraper_run(run_id: str) -> Optional[Dict]:
    """Get a scraper run by ID."""
    conn = get_conn()
    cur = conn.execute("SELECT * FROM scraper_runs WHERE id = ?", (run_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_recent_scraper_runs(
    state: str = None,
    status: str = None,
    limit: int = 50
) -> List[Dict]:
    """Get recent scraper runs."""
    conn = get_conn()

    conditions = []
    params = []

    if state:
        conditions.append("state = ?")
        params.append(state)
    if status:
        conditions.append("status = ?")
        params.append(status)

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    cur = conn.execute(f"""
        SELECT * FROM scraper_runs
        {where_clause}
        ORDER BY started_at DESC
        LIMIT ?
    """, tuple(params + [limit]))

    return [dict(r) for r in cur.fetchall()]


def get_scraper_stats() -> Dict[str, Any]:
    """Get scraper statistics summary."""
    conn = get_conn()

    # Runs by state
    cur = conn.execute("""
        SELECT state,
               COUNT(*) as total_runs,
               SUM(cases_stored) as total_cases,
               MAX(completed_at) as last_run
        FROM scraper_runs
        WHERE status = 'completed'
        GROUP BY state
    """)
    by_state = [dict(r) for r in cur.fetchall()]

    # Overall stats
    cur = conn.execute("""
        SELECT
            COUNT(*) as total_runs,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
            SUM(cases_stored) as total_cases_stored
        FROM scraper_runs
    """)
    overall = dict(cur.fetchone()) if cur else {}

    return {
        "by_state": by_state,
        "overall": overall
    }


# =============================================================================
# CAPTCHA Tracking
# =============================================================================

def record_captcha_encounter(
    state: str,
    url: str,
    pattern_matched: str,
    county: str = None
) -> str:
    """Record a CAPTCHA encounter."""
    import uuid
    from datetime import datetime

    encounter_id = str(uuid.uuid4())
    encountered_at = datetime.utcnow().isoformat() + "Z"

    conn = get_conn()
    with conn:
        conn.execute("""
            INSERT INTO captcha_encounters
            (id, state, county, url, pattern_matched, encountered_at, resolved)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (encounter_id, state, county, url, pattern_matched, encountered_at))

    return encounter_id


def resolve_captcha(encounter_id: str, resolution_method: str = "manual") -> None:
    """Mark a CAPTCHA encounter as resolved."""
    from datetime import datetime

    conn = get_conn()
    with conn:
        conn.execute("""
            UPDATE captcha_encounters
            SET resolved = 1, resolved_at = ?, resolution_method = ?
            WHERE id = ?
        """, (datetime.utcnow().isoformat() + "Z", resolution_method, encounter_id))


def get_unresolved_captchas(state: str = None, limit: int = 50) -> List[Dict]:
    """Get unresolved CAPTCHA encounters."""
    conn = get_conn()

    if state:
        cur = conn.execute("""
            SELECT * FROM captcha_encounters
            WHERE resolved = 0 AND state = ?
            ORDER BY encountered_at DESC
            LIMIT ?
        """, (state, limit))
    else:
        cur = conn.execute("""
            SELECT * FROM captcha_encounters
            WHERE resolved = 0
            ORDER BY encountered_at DESC
            LIMIT ?
        """, (limit,))

    return [dict(r) for r in cur.fetchall()]


def get_captcha_stats() -> Dict[str, Any]:
    """Get CAPTCHA encounter statistics."""
    conn = get_conn()

    # By state
    cur = conn.execute("""
        SELECT state,
               COUNT(*) as total,
               SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
               SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) as pending
        FROM captcha_encounters
        GROUP BY state
    """)
    by_state = [dict(r) for r in cur.fetchall()]

    # Overall
    cur = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
            SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) as pending
        FROM captcha_encounters
    """)
    overall = dict(cur.fetchone()) if cur else {}

    return {
        "by_state": by_state,
        "overall": overall
    }
