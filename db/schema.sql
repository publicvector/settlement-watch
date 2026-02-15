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
