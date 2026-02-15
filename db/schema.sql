-- Settlement Watch Database Schema
-- Unified storage for all court and settlement data

-- Settlements from dorker
CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    amount REAL,
    amount_formatted TEXT,
    url TEXT,
    description TEXT,
    category TEXT,
    source TEXT,
    pub_date TEXT,
    guid TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- State court cases
CREATE TABLE IF NOT EXISTS state_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    case_number TEXT,
    case_title TEXT,
    case_type TEXT,
    filing_date TEXT,
    court TEXT,
    county TEXT,
    parties TEXT,
    charges TEXT,
    status TEXT,
    url TEXT,
    raw_data TEXT,  -- JSON blob for extra fields
    guid TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Federal court cases (PACER)
CREATE TABLE IF NOT EXISTS federal_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    court TEXT,
    case_number TEXT,
    case_title TEXT,
    case_type TEXT,
    filing_date TEXT,
    jurisdiction TEXT,
    nature_of_suit TEXT,
    parties TEXT,
    docket_entries TEXT,  -- JSON blob
    url TEXT,
    pacer_case_id TEXT,
    guid TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Docket entries (individual filings/events within cases)
CREATE TABLE IF NOT EXISTS docket_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER,  -- FK to state_cases or federal_cases
    case_source TEXT NOT NULL,  -- 'state' or 'federal'
    state TEXT,  -- For state cases
    case_number TEXT NOT NULL,
    entry_number INTEGER,  -- Docket entry number
    entry_date TEXT NOT NULL,
    entry_text TEXT,
    entry_type TEXT,  -- 'filing', 'order', 'opinion', 'hearing', 'minute', etc.
    is_opinion INTEGER DEFAULT 0,  -- Flag for judicial decisions
    is_order INTEGER DEFAULT 0,  -- Flag for court orders
    document_url TEXT,
    filed_by TEXT,  -- Party who filed (if applicable)
    judge TEXT,
    guid TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Feed metadata
CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    title TEXT,
    description TEXT,
    link TEXT,
    feed_url TEXT,
    last_build_date TIMESTAMP,
    item_count INTEGER DEFAULT 0
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_settlements_category ON settlements(category);
CREATE INDEX IF NOT EXISTS idx_settlements_pub_date ON settlements(pub_date);
CREATE INDEX IF NOT EXISTS idx_state_cases_state ON state_cases(state);
CREATE INDEX IF NOT EXISTS idx_state_cases_filing_date ON state_cases(filing_date);
CREATE INDEX IF NOT EXISTS idx_federal_cases_court ON federal_cases(court);
CREATE INDEX IF NOT EXISTS idx_federal_cases_filing_date ON federal_cases(filing_date);
CREATE INDEX IF NOT EXISTS idx_docket_entries_date ON docket_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_docket_entries_case ON docket_entries(case_number);
CREATE INDEX IF NOT EXISTS idx_docket_entries_type ON docket_entries(entry_type);
CREATE INDEX IF NOT EXISTS idx_docket_entries_opinion ON docket_entries(is_opinion);
CREATE INDEX IF NOT EXISTS idx_docket_entries_state ON docket_entries(state);
